"""DWD 设计前的体检：这份数据到底脏在哪、主键怎么定、倾斜是真的还是得造。

为什么要先体检再写清洗规则：
    清洗规则如果凭想象写（「广告数据一般都有负数消费吧」「去重按 uid 就行吧」），
    结果是写出一堆永远不触发条件的分支 —— 看起来很专业，实际一行数据都没过滤掉。
    面试官问「你这三条规则各过滤了多少行」，答不出来就露了。
    所以顺序必须是：先量，再定规则，规则后面挂上「过滤了多少行」的数字。

这一步只做聚合（count / sum / max），不落盘，不改任何数据。

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/profile_dwd_input.py
"""

from __future__ import annotations

import os
import sys
import time

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = "file:///D:/warehouse"
SRC = "ods.ods_ad_log"


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("profile-dwd")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        # 聚合类查询可以交给 AQE 自己合并小分区，这里开着更省内存
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "16")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}", flush=True)


def q1_shape(spark):
    """行数 / 列数 / 日期跨度。"""
    from pyspark.sql import functions as F

    section("1. 规模")
    df = spark.table(SRC)
    n = df.count()
    print(f"  总行数: {n:,}")
    print(f"  列数  : {len(df.columns)}")
    r = df.agg(
        F.countDistinct("dt").alias("days"),
        F.countDistinct("uid").alias("uids"),
        F.countDistinct("campaign").alias("camps"),
        F.min("dt").alias("d0"),
        F.max("dt").alias("d1"),
    ).collect()[0]
    print(f"  天数 {r['days']} / 用户 {r['uids']:,} / campaign {r['camps']:,}")
    print(f"  日期 {r['d0']} .. {r['d1']}")


def q2_dedup(spark):
    """主键候选：到底哪种组合能唯一标识一行。

    这是 DWD 最关键的一个决定。判断方法不是「我觉得」，是把候选键的
    重复行数量出来：重复数 = 0 才能当主键。
    """
    from pyspark.sql import functions as F

    section("2. 去重：候选主键的重复情况")
    df = spark.table(SRC)

    candidates = {
        "uid + campaign + timestamp": ["uid", "campaign", "timestamp"],
        "uid + campaign + timestamp + click": ["uid", "campaign", "timestamp", "click"],
        "uid + campaign + timestamp + cat1..cat9": [
            "uid", "campaign", "timestamp", "cat1", "cat2", "cat3",
            "cat4", "cat5", "cat6", "cat7", "cat8", "cat9",
        ],
    }
    total = df.count()
    for label, keys in candidates.items():
        dup_groups = df.groupBy(*keys).count().where("count > 1")
        agg = dup_groups.agg(
            F.count(F.lit(1)).alias("g"),
            F.sum("count").alias("rows"),
        ).collect()[0]
        g = agg["g"] or 0
        rows_in_dups = agg["rows"] or 0
        print(f"  {label}")
        print(f"      重复组 {g:,} / 涉及行 {rows_in_dups:,}"
              f" （占 {rows_in_dups / total:.4%}），去重后剩 {total - (rows_in_dups - g):,}")

    # 全列完全重复
    all_cols = [c for c in df.columns if c != "dt"]
    full_dup = df.groupBy(*all_cols).count().where("count > 1").count()
    print(f"  全 22 列完全相同的组数: {full_dup:,}")


def q3_skew(spark):
    """真实倾斜到底存在不存在。三个维度都量一遍。

    「倾斜」= 某个键的行数远多于其他键。判断标准常用「最大键占总行数的比例」：
    超过 10% 就会被单个 task 拖住，超过 30% 基本必然出现长尾 task。
    """
    from pyspark.sql import functions as F

    section("3. 倾斜：三个候选键的分布（真实值，不是估计）")
    df = spark.table(SRC)
    total = df.count()

    for col in ("campaign", "cat3", "uid"):
        agg = (
            df.groupBy(col).count()
            .agg(
                F.count("*").alias("keys"),
                F.max("count").alias("mx"),
                F.sum("count").alias("tot"),
            )
            .collect()[0]
        )
        top5 = df.groupBy(col).count().orderBy(F.desc("count")).limit(5).collect()
        print(f"\n  [{col}] 去重值 {agg['keys']:,} 个，最大键 {agg['mx']:,} 行"
              f" = {agg['mx'] / total:.2%}")
        for r in top5:
            print(f"        {str(r[0]):>12}  {r['count']:>10,}  {r['count'] / total:>7.2%}")


def q4_dirty(spark):
    """脏数据体检：每一条都量出具体行数，为了后面写规则时能报出「拦掉多少」。

    这些规则不是随便列的，是从字段语义推出来的「不可能发生的情况」：
        - 点击只可能是 0/1，出现 2 就是上游错了
        - 消费不可能是负数
        - 转化时间不可能早于曝光时间
        - 点击位置不可能超过总点击次数
    """
    from pyspark.sql import functions as F

    section("4. 脏数据：每条规则各命中多少行")
    df = spark.table(SRC)
    total = df.count()

    rules = {
        "click 不在 {0,1}": ~F.col("click").isin(0, 1),
        "conversion 不在 {0,1}": ~F.col("conversion").isin(0, 1),
        "attribution 不在 {0,1}": ~F.col("attribution").isin(0, 1),
        "cost 为空或 <= 0": F.col("cost").isNull() | (F.col("cost") <= 0),
        "uid 为空": F.col("uid").isNull(),
        "campaign 为空": F.col("campaign").isNull(),
        "timestamp 为负": F.col("timestamp") < 0,
        "有转化但转化时间早于曝光": (F.col("conversion") == 1)
                                    & (F.col("conversion_timestamp") < F.col("timestamp")),
        "有转化但转化时间为 0": (F.col("conversion") == 1) & (F.col("conversion_timestamp") == 0),
        "点击位置 > 总点击次数": F.col("click_pos") > F.col("click_nb"),
        "上次点击间隔为负": F.col("time_since_last_click") < 0,
        "attribution=1 但 click=0": (F.col("attribution") == 1) & (F.col("click") == 0),
    }
    aggs = [F.sum(F.when(cond, 1).otherwise(0)).alias(name) for name, cond in rules.items()]
    row = df.agg(*aggs).collect()[0]
    for name in rules:
        n = row[name] or 0
        flag = "  <<< 命中" if n else ""
        print(f"  {name:<28}{n:>12,}{n / total:>9.4%}{flag}")

    # 字段分布，确认 0/1 语义
    print("\n  字段取值分布（确认语义）:")
    for col in ("click", "conversion", "attribution"):
        rows = df.groupBy(col).count().orderBy(col).collect()
        s = "  ".join(f"{r[0]}={r['count']:,}" for r in rows)
        print(f"    {col:<12}{s}")

    # cpo 到底是什么：和 cost/conversion、cost/click 比一比
    print("\n  cpo 与 cost 的关系（判断 cpo 是不是 unit cost）:")
    r = df.where((F.col("conversion") == 1) & F.col("cpo").isNotNull()).select(
        F.count("*").alias("n"),
        F.sum(F.when(F.abs(F.col("cpo") - F.col("cost")) < 1e-9, 1).otherwise(0)).alias("eq_cost"),
        F.avg("cpo").alias("avg_cpo"),
        F.avg("cost").alias("avg_cost"),
    ).collect()[0]
    print(f"    有转化行 {r['n']:,}，其中 cpo == cost 的 {r['eq_cost']:,}"
          f"（{r['eq_cost'] / max(r['n'], 1):.2%}）")
    print(f"    cpo 均值 {r['avg_cpo']:.6f} / cost 均值 {r['avg_cost']:.6f}")


def q5_dim_drift(spark):
    """维度漂移：同一个 campaign 的属性会不会变。

    如果会变，说明维度表不能简单「每个 campaign 一行」，
    而要决定「取最新」还是「保留历史」—— 这是 SCD（缓慢变化维）问题的入口。
    """
    from pyspark.sql import functions as F

    section("5. 维度漂移：一个 campaign 的属性是否唯一")
    df = spark.table(SRC)
    cats = [f"cat{i}" for i in range(1, 10)]

    g = df.groupBy("campaign").agg(
        F.countDistinct(*cats).alias("combos"),
        F.countDistinct("dt").alias("days"),
        F.count("*").alias("rows"),
    )
    r = g.agg(
        F.count("*").alias("camps"),
        F.sum(F.when(F.col("combos") > 1, 1).otherwise(0)).alias("drift"),
        F.max("combos").alias("max_combos"),
        F.avg("rows").alias("avg_rows"),
        F.min("rows").alias("min_rows"),
        F.max("rows").alias("max_rows"),
    ).collect()[0]
    print(f"  campaign 总数 {r['camps']:,}")
    print(f"  属性组合数 > 1 的 campaign: {r['drift']:,} 个"
          f"（最多有 {r['max_combos']} 种组合）")
    print(f"  每 campaign 行数: 平均 {r['avg_rows']:,.0f} / 最少 {r['min_rows']:,}"
          f" / 最多 {r['max_rows']:,}")

    if r["drift"]:
        print("\n  漂移样例（前 5 个）:")
        for row in g.where(F.col("combos") > 1).orderBy(F.desc("combos")).limit(5).collect():
            print(f"    campaign={row['campaign']}  {row['combos']} 种属性组合"
                  f" / {row['days']} 天 / {row['rows']:,} 行")


def q6_cost_semantics(spark):
    """cost 的粒度：一行是一次曝光还是一次计费事件。

    这决定了 DWS 里能不能直接 sum(cost)。
    如果同一个 (uid, campaign, timestamp) 有多行，且每行都有 cost，
    那 sum(cost) 会重复计费 —— 这是数仓里最常见的「指标算大一倍」的来源。
    """
    from pyspark.sql import functions as F

    section("6. cost 粒度：能不能直接 sum(cost)")
    df = spark.table(SRC)
    r = df.agg(
        F.count("*").alias("rows"),
        F.sum(F.when(F.col("cost") > 0, 1).otherwise(0)).alias("cost_pos"),
        F.sum(F.when(F.col("click") == 1, 1).otherwise(0)).alias("clicks"),
        F.sum(F.when(F.col("conversion") == 1, 1).otherwise(0)).alias("convs"),
        F.sum("cost").alias("total_cost"),
    ).collect()[0]
    print(f"  行数 {r['rows']:,} / cost>0 {r['cost_pos']:,} ({r['cost_pos'] / r['rows']:.2%})")
    print(f"  click=1 {r['clicks']:,} ({r['clicks'] / r['rows']:.2%})")
    print(f"  conversion=1 {r['convs']:,} ({r['convs'] / r['rows']:.2%})")
    print(f"  sum(cost) = {r['total_cost']:,.2f}  → 平均每行 {r['total_cost'] / r['rows']:.6f}")

    # 同 (uid, campaign, timestamp) 多行时 cost 是否相同（相同=同一次曝光被拆成多行，不能重复加）
    g = df.groupBy("uid", "campaign", "timestamp").agg(
        F.count("*").alias("n"),
        F.countDistinct("cost").alias("cost_vals"),
    )
    multi = g.where("n > 1").agg(
        F.count("*").alias("groups"),
        F.sum(F.when(F.col("cost_vals") > 1, 1).otherwise(0)).alias("diff_cost"),
    ).collect()[0]
    print(f"\n  同 (uid, campaign, timestamp) 的行组数（n>1）: {multi['groups'] or 0:,}")
    print(f"  其中 cost 值不同的: {multi['diff_cost'] or 0:,}"
          f"  → {'不能直接 sum，会重复计费' if multi['diff_cost'] else '没有重复组，属正常'}")


def main():
    t0 = time.time()
    spark = build_session()
    print(f"pyspark {spark.version}", flush=True)
    q1_shape(spark)
    q2_dedup(spark)
    q3_skew(spark)
    q4_dirty(spark)
    q5_dim_drift(spark)
    q6_cost_semantics(spark)
    print(f"\n体检完成，用时 {time.time() - t0:.1f}s")
    spark.stop()


if __name__ == "__main__":
    raise SystemExit(main())
