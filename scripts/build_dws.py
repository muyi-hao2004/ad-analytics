"""第 3 步：DWD → DWS（汇总层）。

和上两步的区别：
    ODS 是「搬」，DWD 是「判」，DWS 是「合」。
    合的意思是：把 16,468,027 行压成 20,925 行（786 : 1），
    让报表不用每次摸明细。

本层只建两张表，而且**两张都不分区** —— 这和 ODS / DWD 的做法相反，
理由不是偷懒，是文件大小：

    dws_ad_campaign_daily  675 campaign × 31 天 ≈ 20,925 行 ≈ 1.6 MB
    dws_ad_date_daily       31 行           ≈ 几 KB

    如果按 dt 分区，会得到 31 个 50 KB 的小文件。
    Parquet 的甜点区是 128~512 MB，50 KB 的文件光是打开就要几十毫秒，
    扫 31 个文件的开销比扫数据本身还大。
    **分区是给「大表按时间切」用的，套在小表上只会制造小文件问题。**

    真正的分区判断标准不是「有没有时间字段」，而是：
        单分区大小是否落在 128 MB ~ 1 GB 之间。
    ODS / DWD 一天 15~19 MB，已经偏小但仍值得分（为了增量+补数）；
    DWS 一天 50 KB，分区就纯属自残。

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/build_dws.py

    # 只验算、不重写
    ./.venv-spark/Scripts/python.exe -u scripts/build_dws.py --verify-only
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

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SQL_CAMPAIGN = os.path.join(ROOT, "sql", "dws", "dws_ad_campaign_daily.sql")
SQL_DATE = os.path.join(ROOT, "sql", "dws", "dws_ad_date_daily.sql")

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
WAREHOUSE_DIR = os.environ.get("IAA_WAREHOUSE_DIR", r"D:/warehouse")

DB = "dws"
T_CAMPAIGN = "dws_ad_campaign_daily"
T_DATE = "dws_ad_date_daily"
SRC = "dwd.dwd_ad_event"

CAMPAIGN_COLUMNS: list[tuple[str, str]] = [
    ("dt", "STRING"),
    ("campaign_id", "BIGINT"),
    ("impressions", "BIGINT"),
    ("clicks", "BIGINT"),
    ("conversions", "BIGINT"),
    ("attributions", "BIGINT"),
    ("first_clicks", "BIGINT"),
    ("reach_users", "BIGINT"),
    ("cost", "DOUBLE"),
    ("cost_on_clicks", "DOUBLE"),
    ("ctr", "DOUBLE"),
    ("cvr", "DOUBLE"),
    ("cpa", "DOUBLE"),
    ("cpc", "DOUBLE"),
    ("attribution_rate", "DOUBLE"),
    ("conv_delay_p50_sec", "DOUBLE"),
]

DATE_COLUMNS: list[tuple[str, str]] = [
    ("dt", "STRING"),
    ("impressions", "BIGINT"),
    ("clicks", "BIGINT"),
    ("conversions", "BIGINT"),
    ("attributions", "BIGINT"),
    ("first_clicks", "BIGINT"),
    ("reach_users", "BIGINT"),
    ("active_campaigns", "BIGINT"),
    ("cost", "DOUBLE"),
    ("ctr", "DOUBLE"),
    ("cvr", "DOUBLE"),
    ("cpa", "DOUBLE"),
    ("cpc", "DOUBLE"),
    ("conv_delay_p50_sec", "DOUBLE"),
    ("cost_p50", "DOUBLE"),
]


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("build-dws")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        # 汇总层天然要 shuffle（16.5M 行 → 675 组），AQE 会按实际数据量合并分区，
        # 不开的话 200 个分区里有一大半是空的，白起 200 个 task。
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.shuffle.partitions", "64")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def read_sql(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def table_columns(spark, table: str) -> list[tuple[str, str]]:
    return [
        (f.name, f.dataType.simpleString().lower())
        for f in spark.table(f"{DB}.{table}").schema.fields
    ]


def reconcile(spark, table: str, columns: list[tuple[str, str]], recreate: bool) -> None:
    """建表 / 检查表结构与代码是否一致。

    为什么这段要从 build_dwd.py 抄一遍而不是 import：
        那个脚本已经在跑通的状态，动它有风险；这份逻辑只有 30 行。
        属于「已知的重复债」，等第三处需要它时再抽公共模块。
        （真实工程里也是这个判断：重复两次可以忍，第三次一定要抽。）
    """
    want = [(n, t.lower()) for n, t in columns]
    if spark.catalog.tableExists(f"{DB}.{table}"):
        actual = table_columns(spark, table)
        names_ok = [n for n, _ in actual] == [n for n, _ in want]
        types_ok = [t for _, t in actual] == [t for _, t in want]
        if names_ok and types_ok:
            print(f"      {DB}.{table} 结构与代码一致（{len(want)} 列）")
            return
        print(f"\n      ⚠️  {DB}.{table} 结构与代码不一致")
        if not names_ok:
            print(f"          表里: {[n for n, _ in actual]}")
            print(f"          代码: {[n for n, _ in want]}")
        else:
            print(f"          列名一致但类型不同：")
            for (an, at), (wn, wt) in zip(actual, want):
                if at != wt:
                    print(f"            {an}: 表里 {at} / 代码 {wt}")
        if not recreate:
            raise RuntimeError(
                f"{DB}.{table} 结构已过期，加 --recreate 重建。\n"
                f"（不加就继续写，会按位置灌进错列且不报错）"
            )
        spark.sql(f"DROP TABLE IF EXISTS {DB}.{table}")
        table_dir = os.path.join(WAREHOUSE_DIR, f"{DB}.db", table)
        if os.path.isdir(table_dir):
            import shutil
            shutil.rmtree(table_dir)
            print(f"          已删除 {table_dir}")

    cols_sql = ",\n    ".join(f"`{n}` {t}" for n, t in columns)
    spark.sql(
        f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.{table} (
            {cols_sql}
        )
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/{table}'
        """
    )
    print(f"      {DB}.{table} 已建（{len(want)} 列，不分区）")


def build_one(spark, name: str, sql_path: str, columns: list[tuple[str, str]]) -> float:
    t0 = time.time()
    sql = read_sql(sql_path)
    df = spark.sql(sql)

    expect = [n for n, _ in columns]
    if df.columns != expect:
        raise RuntimeError(
            f"{name} 的 SQL 输出列与表结构不一致。\n  表定义: {expect}\n  实际:   {df.columns}"
        )

    df.createOrReplaceTempView("stg_tmp")
    cols = ", ".join(f"`{n}`" for n in expect)
    spark.sql(f"INSERT OVERWRITE TABLE {DB}.{name} SELECT {cols} FROM stg_tmp")
    return time.time() - t0


def verify(spark) -> None:
    from pyspark.sql import functions as F

    print("\n[验收 1] 行数")
    c = spark.table(f"{DB}.{T_CAMPAIGN}").count()
    d = spark.table(f"{DB}.{T_DATE}").count()
    print(f"      {T_CAMPAIGN}: {c:,} 行")
    print(f"      {T_DATE}      : {d:,} 行（期望 31，一天一行）")
    if d != 31:
        raise RuntimeError(f"按天表应当是 31 行，实际 {d} 行")

    # ---------------------------------------------------------------
    # 行数不能硬写成「675 × 31」—— 这是我第一版犯的错。
    # 那个算式假设「每个 campaign 天天都在跑」，实测只有 20,665 组合，
    # 少了 260 个：平均每个计划有 0.39 天没投放（预算用完、主动暂停、审核不过都会造成）。
    # 正确的对账基准只能是 **源表里真实的 (dt, campaign_id) 组合数**。
    # 这类错误在报表里很常见：分母被想当然地写成一个整数，然后永远对不上。
    # ---------------------------------------------------------------
    pairs = spark.sql(f"SELECT count(*) AS n FROM (SELECT DISTINCT dt, campaign_id FROM {SRC})").collect()[0]["n"]
    dup = spark.sql(f"""
        SELECT count(*) AS n FROM (
            SELECT dt, campaign_id FROM {DB}.{T_CAMPAIGN}
            GROUP BY dt, campaign_id HAVING count(*) > 1
        )
    """).collect()[0]["n"]
    print(f"      源表 (dt, campaign_id) 组合数: {pairs:,}")
    print(f"      理论上限 675 × 31 = 20,925，实际少 {20925 - pairs} 个组合"
          f"（计划有停投的日子，正常）")
    print(f"      重复分组（同一天同一计划出现两行）: {dup} {'✅' if dup == 0 else '❌'}")
    if c != pairs:
        raise RuntimeError(f"行数不符：源表组合 {pairs:,}，DWS {c:,}")
    if dup:
        raise RuntimeError("分组键重复：聚合结果被拆成了多行")

    # ---------------------------------------------------------------
    print("\n[验收 2] 上卷对账：按天把 campaign 明细加起来，必须等于全站当天")
    # 这是第二道硬约束（第一道是 ODS→DWD 的行数对账）。
    # 只要有一个 dt 对不上，就说明汇总过程中丢行或重复计数了。
    # ---------------------------------------------------------------
    bad = spark.sql(f"""
        SELECT a.dt,
               a.impressions AS camp_imp, b.impressions AS date_imp,
               a.clicks AS camp_clk, b.clicks AS date_clk,
               a.cost AS camp_cost, b.cost AS date_cost
        FROM (SELECT dt, sum(impressions) AS impressions, sum(clicks) AS clicks,
                     sum(cost) AS cost
              FROM {DB}.{T_CAMPAIGN} GROUP BY dt) a
        JOIN {DB}.{T_DATE} b ON a.dt = b.dt
        WHERE a.impressions <> b.impressions
           OR a.clicks <> b.clicks
           OR abs(a.cost - b.cost) > 0.0001
    """).collect()
    total = spark.table(SRC).count()
    if bad:
        for r in bad:
            print(f"      ❌ {r['dt']}: 明细 {r['camp_imp']:,}/{r['date_imp']:,}")
        raise RuntimeError(f"{len(bad)} 个 dt 上卷对不上")
    print(f"      31 个 dt 全部一致 ✅")

    print("\n[验收 3] 全表对账：DWS 合计 = DWD 明细")
    r = spark.sql(f"""
        SELECT sum(impressions) AS imp, sum(clicks) AS clk,
               sum(conversions) AS cnv, sum(cost) AS cost
        FROM {DB}.{T_DATE}
    """).collect()[0]
    s = spark.sql(f"""
        SELECT count(*) AS imp, sum(is_click) AS clk,
               sum(is_conversion) AS cnv, sum(cost) AS cost
        FROM {SRC}
    """).collect()[0]
    ok = (r["imp"] == s["imp"] and r["clk"] == s["clk"]
          and r["cnv"] == s["cnv"] and abs(r["cost"] - s["cost"]) < 0.001)
    print(f"      DWS: {r['imp']:,} 曝光 / {r['clk']:,} 点击 / {r['cnv']:,} 转化 / cost {r['cost']:,.2f}")
    print(f"      DWD: {s['imp']:,} 曝光 / {s['clk']:,} 点击 / {s['cnv']:,} 转化 / cost {s['cost']:,.2f}")
    print(f"      结果: {'✅ 完全一致' if ok else '❌ 不一致'}")
    if not ok:
        raise RuntimeError("DWS 与 DWD 合计不一致")
    if s["imp"] != total:
        raise RuntimeError("明细行数在构建期间被改动了")

    # ---------------------------------------------------------------
    print("\n[验收 4] 比率字段自洽：存下来的 ctr 必须等于「用基础指标重算」的结果")
    # 这一条防的是「公式改了一处忘了另一处」：
    # 如果有人把 ctr 的定义改成 clicks/impressions*100，这里会立刻炸。
    # ---------------------------------------------------------------
    drift = spark.sql(f"""
        SELECT count(*) AS n
        FROM {DB}.{T_CAMPAIGN}
        WHERE abs(ctr - clicks * 1.0 / impressions) > 1e-12
    """).collect()[0]["n"]
    print(f"      ctr 与 clicks/impressions 不一致的行: {drift} "
          f"{'✅' if drift == 0 else '❌'}")

    # ---------------------------------------------------------------
    print("\n[验收 5] 一个必须能讲出来的数字：把 ctr 平均是错的")
    # 这是报表里最常见的错误：31 天平均 CTR，或者 675 个计划平均 CTR。
    # 正确做法永远是用「总点击 / 总曝光」重算。
    # ---------------------------------------------------------------
    x = spark.sql(f"""
        SELECT avg(ctr) AS naive, sum(clicks) * 1.0 / sum(impressions) AS correct,
               count(*) AS n
        FROM {DB}.{T_CAMPAIGN}
    """).collect()[0]
    # Spark 4 里 bigint * 1.0 / bigint 返回 Decimal，不是 float，
    # 直接和 float 相减会 TypeError。显式转 float。
    naive, correct = float(x["naive"]), float(x["correct"])
    print(f"      对 {x['n']:,} 行 ctr 直接 avg()  = {naive:.4%}   ← 错的")
    print(f"      用 sum(clicks)/sum(impressions) = {correct:.4%}   ← 对的")
    print(f"      偏差 {abs(naive - correct) / correct:.1%}"
          f"（小计划权重被放大，所以偏高）")

    print("\n[验收 6] 物理大小")
    tdir = os.path.join(WAREHOUSE_DIR, f"{DB}.db", T_CAMPAIGN)
    files = [f for f in os.listdir(tdir) if f.endswith(".parquet")]
    size = sum(os.path.getsize(os.path.join(tdir, f)) for f in files)
    print(f"      {T_CAMPAIGN}: {len(files)} 个文件 / {size / 1024:.0f} KB"
          f" → 不分区是对的（ partitioned 会变成 31 个 50KB 小文件）")


def main() -> int:
    verify_only = "--verify-only" in sys.argv
    recreate = "--recreate" in sys.argv

    t0 = time.time()
    spark = build_session()
    print(f"pyspark {spark.version} / master=local[2] / driver=2g", flush=True)

    dbs = sorted(r[0] for r in spark.sql("SHOW DATABASES").collect())
    if "dwd" not in dbs:
        print("[中止] 看不到 dwd 库，先跑 scripts/build_dwd.py")
        return 1

    spark.sql(f"CREATE DATABASE IF NOT EXISTS {DB}")

    if not verify_only:
        print("\n[建表]")
        reconcile(spark, T_CAMPAIGN, CAMPAIGN_COLUMNS, recreate)
        reconcile(spark, T_DATE, DATE_COLUMNS, recreate)

        print(f"\n[构建] {T_CAMPAIGN}")
        el = build_one(spark, T_CAMPAIGN, SQL_CAMPAIGN, CAMPAIGN_COLUMNS)
        print(f"      用时 {el:.1f}s")

        print(f"\n[构建] {T_DATE}")
        el = build_one(spark, T_DATE, SQL_DATE, DATE_COLUMNS)
        print(f"      用时 {el:.1f}s")

    verify(spark)

    print(f"\nDWS 层完成，总用时 {(time.time() - t0) / 60:.1f} 分钟。")
    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
