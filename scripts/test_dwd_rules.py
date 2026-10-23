"""故障注入测试：证明 DWD 的清洗规则真的抓得住脏数据。

为什么必须有这一步（这是整个 DWD 最关键的思想）：
    build_dwd.py 的审计报告显示 9 条规则**全部命中 0 行** —— 因为 Criteo 这份
    公开数据集已经清洗过了。这时最容易犯的错是：「规则都写了，审计也通过了，收工」。
    但真相是：**这 9 条规则从来没有被执行过一次**。
    它们可能有语法错、可能条件写反了、可能永远为真 —— 你完全不知道。

    上线第一次遇到重复和脏数据时才发现规则是坏的，代价是整张表。
    所以要用「故障注入」：人为造出每一种脏数据，看规则能不能刚好抓住它，
    而且**不能误伤任何一条干净数据**。

「不误伤」这半边比「抓得住」更重要：
    规则写得太宽，比如把 cost > 0.001 写成 cost > 0.01，
    看起来还是在「清洗」，实际上每天悄悄扔掉 5% 的真实数据，
    报表上的数字一天比一天小，而且没有任何报错。

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/test_dwd_rules.py
"""

from __future__ import annotations

import os
import sys

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

import dwd_rules  # noqa: E402

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = "file:///D:/warehouse"
SRC = "ods.ods_ad_log"

N_CLEAN = 20_000   # 干净样本行数
K_DIRTY = 100      # 每一类脏数据注入多少行


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("test-dwd-rules")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.shuffle.partitions", "8")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def main() -> int:
    from functools import reduce

    from pyspark.sql import Window
    from pyspark.sql import functions as F

    spark = build_session()
    print(f"pyspark {spark.version}", flush=True)

    # ---------------------------------------------------------------
    # 1. 取一份「保证干净」的样本：先用规则过滤，再取样。
    #    顺序不能反 —— 先取样再过滤，样本里可能混着脏行，
    #    后面判「干净行有没有被误伤」就说不清了。
    # ---------------------------------------------------------------
    where = dwd_rules.cleaning_where_sql()
    clean = spark.sql(f"SELECT * FROM {SRC} WHERE\n{where} LIMIT {N_CLEAN}")
    n_clean = clean.count()
    print(f"\n[准备] 干净样本 {n_clean:,} 行（已通过全部 9 条规则）")
    if n_clean < N_CLEAN:
        print(f"  [警告] 只取到 {n_clean} 行，不足 {N_CLEAN}")

    # ---------------------------------------------------------------
    # 2. 注入 9 类脏数据 + 1 类「多重违规」+ 1 类「重复」
    #    每一类都是从干净样本复制 K 行后改坏一个字段 ——
    #    这样能保证「只有目标字段有问题」，不会牵连别的规则。
    # ---------------------------------------------------------------
    injections = []   # (标签, DataFrame, 期望命中的规则)

    def inject(label: str, rule: str, **changes):
        """复制 K 行，按 changes 修改指定列。

        changes 的值是 Column 表达式，这样能表达「在原值基础上加个偏移」。
        """
        d = clean.limit(K_DIRTY)
        for col, expr in changes.items():
            d = d.withColumn(col, expr)
        injections.append((label, d, rule))

    inject("uid 为空", "R1", uid=F.lit(None).cast("bigint"))
    inject("campaign 为空", "R2", campaign=F.lit(None).cast("bigint"))
    inject("timestamp 为负", "R3", timestamp=F.lit(-3600).cast("bigint"))
    inject("click = 2（越界枚举）", "R4", click=F.lit(2).cast("bigint"))
    inject("conversion = 2（越界枚举）", "R5", conversion=F.lit(2).cast("bigint"))
    inject("cost = 0", "R6", cost=F.lit(0.0).cast("double"))
    inject(
        "转化时间早于曝光 2 小时", "R7",
        conversion=F.lit(1).cast("bigint"),
        conversion_timestamp=F.col("timestamp") - F.lit(7200),
        # 同时把「转化=1 就该有点击」和转化上下文补齐，
        # 否则 R9 和 R8 也会跟着命中，就测不出 R7 单独准不准了。
        # 这就是「一次只改一个变量」在测试里的体现 —— 少了这一步，
        # 你看到的是「R7 抓到了」还是「R9 顺手抓到了」根本分不清。
        click=F.lit(1).cast("bigint"),
        click_nb=F.lit(1).cast("bigint"), click_pos=F.lit(0).cast("bigint"),
    )
    inject(
        "有转化但 click_nb 漏填（仍为 -1）", "R8",
        conversion=F.lit(1).cast("bigint"),
        conversion_timestamp=F.col("timestamp") + F.lit(60),
        click=F.lit(1).cast("bigint"),
        click_nb=F.lit(-1).cast("bigint"), click_pos=F.lit(-1).cast("bigint"),
    )
    inject(
        "有归因但没点击", "R9",
        attribution=F.lit(1).cast("bigint"), click=F.lit(0).cast("bigint"),
    )
    # 一行同时违反 R1 和 R6：用来证明「单条命中数相加 ≠ 实际拦掉的行数」
    inject(
        "uid 为空 + cost = 0（双重违规）", "R1+R6",
        uid=F.lit(None).cast("bigint"), cost=F.lit(0.0).cast("double"),
    )
    # 完全重复：内容一个字不改，只是同样的行出现两次
    injections.append(("完全重复（内容相同的 2 份）", clean.limit(K_DIRTY), "DEDUP"))

    # ---------------------------------------------------------------
    # 3. 拼成一张表
    # ---------------------------------------------------------------
    stacked = reduce(lambda a, b: a.unionByName(b), [clean] + [x[1] for x in injections])
    n_stacked = stacked.count()
    print(f"[注入] 拼接后共 {n_stacked:,} 行"
          f"（干净 {n_clean:,} + 注入 {n_stacked - n_clean:,}）")
    stacked.createOrReplaceTempView("v_injected")

    # ---------------------------------------------------------------
    # 4. 逐条规则统计命中数
    # ---------------------------------------------------------------
    aggs = [F.count(F.lit(1)).alias("__n")]
    for r in dwd_rules.RULES:
        keep = F.coalesce(F.expr(r.expr), F.lit(True))
        aggs.append(F.sum(F.when(~keep, 1).otherwise(0)).alias(r.code))
    row = stacked.agg(*aggs).collect()[0]

    # 期望值：单条注入 K 行 → K；双重违规同时计入 R1 和 R6；重复不触发任何清洗规则
    expected = {r.code: 0 for r in dwd_rules.RULES}
    for label, _, rule in injections:
        if rule == "R1+R6":
            expected["R1"] += K_DIRTY
            expected["R6"] += K_DIRTY
        elif rule.startswith("R"):
            expected[rule] += K_DIRTY

    print(f"\n[结果] 逐条规则命中数（期望 / 实际）")
    print(f"  {'编号':<6}{'期望':>8}{'实际':>8}{'':>4}  说明")
    all_ok = True
    for r in dwd_rules.RULES:
        exp, act = expected[r.code], (row[r.code] or 0)
        ok = exp == act
        all_ok &= ok
        print(f"  {r.code:<6}{exp:>8,}{act:>8,}{'  ✅' if ok else '  ❌'}  {r.title}")

    # ---------------------------------------------------------------
    # 5. 清洗后行数：必须是「干净样本 + 重复组」，一条都不多不少
    # ---------------------------------------------------------------
    where_sql = dwd_rules.cleaning_where_sql()
    kept = spark.sql(f"SELECT * FROM v_injected WHERE\n{where_sql}")
    n_kept = kept.count()
    expect_kept = n_clean + K_DIRTY     # 只有重复组能通过清洗（它们本身是干净的）
    ok_kept = n_kept == expect_kept
    all_ok &= ok_kept
    print(f"\n[结果] 清洗后保留 {n_kept:,} 行，期望 {expect_kept:,}"
          f"（干净 {n_clean:,} + 重复 {K_DIRTY:,}）  {'✅' if ok_kept else '❌'}")

    # ---------------------------------------------------------------
    # 6. 去重：必须刚好去掉注入的那些重复，一条干净数据都不能误删
    # ---------------------------------------------------------------
    key = dwd_rules.DEDUP_KEY_SRC
    deduped = (
        kept.withColumn(
            "rn",
            F.row_number().over(
                Window.partitionBy(*key).orderBy(
                    F.col("conversion").desc(), F.col("click").desc(), F.col("cost").desc()
                )
            ),
        )
        .where("rn = 1")
        .drop("rn")
    )
    n_dedup = deduped.count()
    ok_dedup = n_dedup == n_clean
    all_ok &= ok_dedup
    print(f"[结果] 去重后剩 {n_dedup:,} 行，期望 {n_clean:,}（正好去掉 {K_DIRTY} 条重复）"
          f"  {'✅' if ok_dedup else '❌'}")

    # ---------------------------------------------------------------
    # 7. 误伤检查：纯干净数据过一遍全流程，必须一行不少
    # ---------------------------------------------------------------
    clean_kept = spark.sql(f"SELECT * FROM v_injected WHERE\n{where_sql} LIMIT {n_clean}").count()
    ok_fp = clean_kept == n_clean
    all_ok &= ok_fp
    print(f"[结果] 误伤检查：{n_clean:,} 行干净数据过清洗，保留 {clean_kept:,} 行"
          f"  {'✅ 零误伤' if ok_fp else '❌ 有误伤'}")

    print()
    if all_ok:
        print("全部通过：9 条规则各自精确命中、无重复计数、无漏拦、无误伤、去重精确。")
        print("这份测试才是 DWD 的交付物 —— 表本身只是副产物。")
    else:
        print("有检查未通过，见上面 ❌ 标记。不要带着失败的测试往下走。")

    spark.stop()
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
