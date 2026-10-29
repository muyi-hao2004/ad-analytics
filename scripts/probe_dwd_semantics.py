"""第二个探针：验证第一轮体检暴露出的三个疑点。

第一轮体检的结果很反常（脏数据规则几乎全部 0 命中、而 time_since_last_click
有 53% 命中），说明**我凭想象写的规则有问题**，要么规则定义错了，要么字段含义理解错了。
这个脚本专门去验证：

    疑点 A：time_since_last_click 的 53% 负数，是真脏数据，还是 -1 这种哨兵值？
             （哨兵值 = 用一个特殊值表示「没有这个值」，比如 -1 表示「没有上一次点击」）

    疑点 B：cpo 均值 0.11 而 cost 均值 0.000995，差 110 倍。
             cpo 会不会是「campaign 级别的属性」被复制到了每一行？
             如果是，那把它 sum 起来就是错的 —— 这是事实表和维度表放错字段的经典症状。

    疑点 C：cat1..cat9 属于 campaign（维度属性），还是属于每次曝光（事件属性）？
             第一轮里 675 个 campaign 全都出现多个 cat 组合，最多 12 万种，
             这不像「漂移」，更像「压根不是 campaign 的属性」。

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/probe_dwd_semantics.py
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
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
SRC = "ods.ods_ad_log"


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("probe-dwd-semantics")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "16")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def section(t):
    print(f"\n{'=' * 62}\n{t}\n{'=' * 62}", flush=True)


def probe_a_time_since(spark):
    """A：time_since_last_click 的负值到底是什么。"""
    from pyspark.sql import functions as F

    section("A. time_since_last_click：负数是不是 -1 哨兵值")
    df = spark.table(SRC)

    r = df.agg(
        F.count("*").alias("n"),
        F.sum(F.when(F.col("time_since_last_click") < 0, 1).otherwise(0)).alias("neg"),
        F.sum(F.when(F.col("time_since_last_click") == -1, 1).otherwise(0)).alias("minus1"),
        F.min("time_since_last_click").alias("mn"),
        F.max("time_since_last_click").alias("mx"),
    ).collect()[0]
    print(f"  总行 {r['n']:,} / 负数 {r['neg']:,} / 其中等于 -1 的 {r['minus1']:,}")
    print(f"  范围 [{r['mn']}, {r['mx']}]")

    print("\n  负数区间的取值分布（如果是哨兵值，应该只有一个 -1）:")
    for row in (df.where(F.col("time_since_last_click") < 0)
                .groupBy("time_since_last_click").count()
                .orderBy("time_since_last_click").limit(10).collect()):
        print(f"    {row[0]:>6}  {row['count']:>12,}")

    # 交叉验证：-1 是否集中在「第一次点击」上？
    print("\n  按 click_nb（累计点击次数）看 -1 的分布:")
    rows = (df.withColumn("is_neg", F.col("time_since_last_click") == -1)
            .groupBy("click_nb")
            .agg(F.count("*").alias("n"),
                 F.sum(F.when(F.col("is_neg"), 1).otherwise(0)).alias("neg"))
            .orderBy("click_nb").limit(6).collect())
    for row in rows:
        print(f"    click_nb={row['click_nb']:<4} 行 {row['n']:>10,}  "
              f"其中 -1: {row['neg']:>10,} ({row['neg'] / row['n']:.1%})")

    # 再看 click_pos 与 click_nb 的关系
    print("\n  click_pos 与 click_nb 的关系（判断是否为「第几次点击」）:")
    corr = df.agg(
        F.sum(F.when(F.col("click_pos") <= F.col("click_nb"), 1).otherwise(0)).alias("ok"),
        F.sum(F.when(F.col("click_pos") == 0, 1).otherwise(0)).alias("zero_pos"),
        F.countDistinct("click_nb").alias("nb_vals"),
        F.max("click_nb").alias("nb_max"),
    ).collect()[0]
    print(f"    click_pos <= click_nb 的行: {corr['ok']:,}")
    print(f"    click_pos == 0 的行: {corr['zero_pos']:,}")
    print(f"    click_nb 去重值 {corr['nb_vals']} 个，最大 {corr['nb_max']}")


def probe_b_cpo(spark):
    """B：cpo 是 campaign 级属性还是行级事实。"""
    from pyspark.sql import functions as F

    section("B. cpo：是每行一个值，还是每个 campaign 一个值")
    df = spark.table(SRC)

    g = df.groupBy("campaign").agg(
        F.count("*").alias("rows"),
        F.countDistinct("cpo").alias("cpo_vals"),
        F.countDistinct("cost").alias("cost_vals"),
        F.first("cpo").alias("cpo0"),
        F.sum("cost").alias("cost_sum"),
        F.sum("conversion").alias("conv_sum"),
    )
    r = g.agg(
        F.count("*").alias("camps"),
        F.sum(F.when(F.col("cpo_vals") == 1, 1).otherwise(0)).alias("cpo_const"),
        F.avg("cpo_vals").alias("avg_cpo_vals"),
        F.avg("cost_vals").alias("avg_cost_vals"),
    ).collect()[0]
    print(f"  campaign 数 {r['camps']:,}")
    print(f"  其中 cpo 只有一个取值的: {r['cpo_const']:,}"
          f" ({r['cpo_const'] / r['camps']:.1%})")
    print(f"  平均每个 campaign 的 cpo 去重值: {r['avg_cpo_vals']:.1f}")
    print(f"  平均每个 campaign 的 cost 去重值: {r['avg_cost_vals']:.1f}")

    # cpo 是不是约等于 cost_sum / conv_sum？
    print("\n  验证 cpo 是否等于 campaign 级 cost/conversion（用前 5 个 campaign 对比）:")
    rows = (g.where(F.col("conv_sum") > 0)
            .withColumn("calc", F.col("cost_sum") / F.col("conv_sum"))
            .orderBy(F.desc("rows")).limit(5).collect())
    print(f"    {'campaign':>12}{'cpo(表里)':>14}{'cost/conv':>14}{'比值':>10}")
    for row in rows:
        ratio = row["cpo0"] / row["calc"] if row["calc"] else float("nan")
        print(f"    {row['campaign']:>12}{row['cpo0']:>14.6f}{row['calc']:>14.8f}{ratio:>10.1f}")


def probe_c_cat(spark):
    """C：cat 字段归属。看 cat 与 campaign 的耦合度、与 uid 的耦合度。"""
    from pyspark.sql import functions as F

    section("C. cat1..cat9：属于 campaign 还是属于每次曝光")
    df = spark.table(SRC)
    total = df.count()

    print(f"  {'字段':<8}{'去重值':>10}{'每 campaign 平均组合数':>22}{'最大':>10}")
    for i in range(1, 10):
        c = f"cat{i}"
        g = df.groupBy("campaign").agg(F.countDistinct(c).alias("k"))
        a = g.agg(F.avg("k").alias("avg_k"), F.max("k").alias("max_k")).collect()[0]
        distinct = df.select(c).distinct().count()
        print(f"  {c:<8}{distinct:>10,}{a['avg_k']:>22.1f}{a['max_k']:>10,}")

    # 关键判据：如果 cat 是 campaign 属性，那么「给定 campaign，cat 应该唯一」。
    # 上面已经否掉了。再看「给定 uid + campaign，cat 是否唯一」——如果也不唯一，
    # 就说明 cat 确实随每次曝光变化（广告位 / 设备 / 时段等上下文）。
    print("\n  给定 uid+campaign 时，cat3 是否唯一（抽 20 万行样本看）:")
    sample = df.select("uid", "campaign", "cat3", "cat1").limit(200000)
    g = sample.groupBy("uid", "campaign").agg(F.countDistinct("cat3").alias("k"))
    a = g.agg(
        F.count("*").alias("groups"),
        F.sum(F.when(F.col("k") > 1, 1).otherwise(0)).alias("multi"),
    ).collect()[0]
    print(f"    组数 {a['groups']:,}，其中 cat3 不唯一的 {a['multi']:,}"
          f" ({a['multi'] / max(a['groups'], 1):.1%})")

    # cat3 与「天」的关系：cat3 的分布在 31 天里稳定吗（这是 DWS 按渠道汇总的前提）
    print("\n  cat3 分布跨天是否稳定（取第 1 天与最后一天对比 top3 覆盖率）:")
    for d in ("2026-09-21", "2026-10-21"):
        sub = df.where(F.col("dt") == d)
        n = sub.count()
        top = sub.groupBy("cat3").count().orderBy(F.desc("count")).limit(3).collect()
        conv = sum(x["count"] for x in top) / n
        print(f"    {d}: 行 {n:,}，top3 cat3 覆盖率 {conv:.1%}")


def probe_d_click_fields(spark):
    """D：click_nb / click_pos / time_since_last_click 三者的真实语义。

    为什么专门补这一轮：第一版清洗规则 R8 假设「click_nb = 累计点击次数，
    所以 click=1 时必然 >= 1」，结果一跑拦掉了 33.65% 的行（5,541,519 行，
    而总点击只有 5,947,563 行 —— 等于 93% 的点击行都被判成脏）。
    这不是数据脏，是**规则把哨兵值当成了脏值**。

    这轮要确认的就是：-1 到底代表什么，它出现在哪些行上。
    """
    from pyspark.sql import functions as F

    section("D. click 相关三个字段的真实语义")
    df = spark.table(SRC)

    print("  [1] click_nb 的取值分布（按是否有转化分组）")
    rows = (df.groupBy("conversion", "click_nb")
            .count().orderBy("conversion", "click_nb").limit(10).collect())
    print(f"      {'conversion':>12}{'click_nb':>10}{'行数':>14}")
    for r in rows:
        print(f"      {r['conversion']:>12}{r['click_nb']:>10}{r['count']:>14,}")

    print("\n  [2] 关键交叉：conversion / click / click_nb=-1 的关系")
    r = df.agg(
        F.count(F.lit(1)).alias("n"),
        F.sum(F.when((F.col("click_nb") == -1) & (F.col("conversion") == 0), 1).otherwise(0)).alias("nb_neg_conv0"),
        F.sum(F.when((F.col("click_nb") == -1) & (F.col("conversion") == 1), 1).otherwise(0)).alias("nb_neg_conv1"),
        F.sum(F.when((F.col("click_nb") >= 1) & (F.col("conversion") == 0), 1).otherwise(0)).alias("nb_pos_conv0"),
        F.sum(F.when((F.col("click_nb") >= 1) & (F.col("conversion") == 1), 1).otherwise(0)).alias("nb_pos_conv1"),
    ).collect()[0]
    print(f"      总行 {r['n']:,}")
    print(f"      click_nb=-1 且 conversion=0 : {r['nb_neg_conv0']:>12,}")
    print(f"      click_nb=-1 且 conversion=1 : {r['nb_neg_conv1']:>12,}")
    print(f"      click_nb>=1 且 conversion=0 : {r['nb_pos_conv0']:>12,}")
    print(f"      click_nb>=1 且 conversion=1 : {r['nb_pos_conv1']:>12,}")
    concl = ("click_nb 是「转化前的累计点击数」，无转化时给 -1 哨兵"
             if r["nb_neg_conv0"] and not r["nb_neg_conv1"]
             else "语义仍不明确，需要再查")
    print(f"      → 结论: {concl}")

    print("\n  [3] click_pos 的取值分布")
    r = df.agg(
        F.min("click_pos").alias("mn"),
        F.max("click_pos").alias("mx"),
        F.sum(F.when(F.col("click_pos") < 0, 1).otherwise(0)).alias("neg"),
        F.sum(F.when(F.col("click_pos") == 0, 1).otherwise(0)).alias("zero"),
        F.sum(F.when((F.col("click_pos") == 0) & (F.col("click") == 1), 1).otherwise(0)).alias("zero_click1"),
    ).collect()[0]
    print(f"      范围 [{r['mn']}, {r['mx']}] / 负数 {r['neg']:,} / 等于 0 的 {r['zero']:,}"
          f" / 其中 click=1 的 {r['zero_click1']:,}")

    print("\n  [4] time_since_last_click=-1 与 click 的关系")
    r = df.agg(
        F.sum(F.when((F.col("time_since_last_click") == -1) & (F.col("click") == 0), 1).otherwise(0)).alias("neg_click0"),
        F.sum(F.when((F.col("time_since_last_click") == -1) & (F.col("click") == 1), 1).otherwise(0)).alias("neg_click1"),
    ).collect()[0]
    print(f"      =-1 且 click=0 : {r['neg_click0']:>12,}")
    print(f"      =-1 且 click=1 : {r['neg_click1']:>12,}")

    print("\n  [5] click_nb>=1 时，click_pos 与 click_nb 的大小关系")
    sub = df.where((F.col("click_nb") >= 1))
    r = sub.agg(
        F.count(F.lit(1)).alias("n"),
        F.sum(F.when(F.col("click_pos") <= F.col("click_nb"), 1).otherwise(0)).alias("le"),
        F.sum(F.when(F.col("click_pos") < 1, 1).otherwise(0)).alias("pos_lt1"),
    ).collect()[0]
    print(f"      行数 {r['n']:,} / click_pos<=click_nb 的 {r['le']:,}"
          f" / click_pos<1 的 {r['pos_lt1']:,}")


def main():
    t0 = time.time()
    spark = build_session()
    print(f"pyspark {spark.version}", flush=True)

    # 支持只跑某一段：python probe_dwd_semantics.py d
    only = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    probes = {
        "a": probe_a_time_since,
        "b": probe_b_cpo,
        "c": probe_c_cat,
        "d": probe_d_click_fields,
    }
    for k, fn in probes.items():
        if only and k != only:
            continue
        fn(spark)

    print(f"\n探针完成，用时 {time.time() - t0:.1f}s")
    spark.stop()


if __name__ == "__main__":
    raise SystemExit(main())
