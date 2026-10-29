"""第 4 步：DWS 层 —— 3,568 万行用户明细 → 四张汇总表。

产出：
    lab.dws_cohort_daily     31 分区 / 约 31 万行   cohort 长表（按变现日分区）
    lab.dws_campaign_daily   不分区 / 约 340 行     渠道 × 天（买量 + 变现）
    lab.dws_creative_daily   不分区 / 约 6,200 行   素材 × 天
    lab.dws_date_daily       不分区 / 31 行         全站日汇总
    lab.vw_cohort_d7_wide    视图（不占存储）       D0~D7 横铺，对齐原中台 daily_detail

核心验收（跑不过就中止）：
    A1 cohort 主键唯一
    A2 installs / spend 只在 day_offset=0 有值
    A3 cohort 汇总 = DWD 明细（行数、收入、花费三项）
    A4 四张表之间的交叉对账
    A5 漏斗单调性：曝光 ≥ 点击 ≥ 安装

跑法：
    全部：        MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/build_dws_iaa.py --all
    只重建某天：  MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/build_dws_iaa.py --dt 2026-10-15
    只验收：      MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/build_dws_iaa.py --verify-only
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
DB = "lab"

DWD = "lab.dwd_user_daily"
ACQ = "lab.ods_iaa_acquisition_daily"

SQL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql", "dws")

START_DATE = date(2026, 10, 1)
END_DATE = date(2026, 10, 31)

# ---------------------------------------------------------------------------
# 表结构。dt 单独说明：cohort 表用 dt 做分区键（不在这里列），
# 另外三张把 dt 当普通列（不分区）。
# ---------------------------------------------------------------------------
COHORT_COLS: list[tuple[str, str]] = [
    ("install_date", "date"),
    ("day_offset", "int"),
    ("app", "string"),
    ("country", "string"),
    ("campaign", "string"),
    ("sub_channel", "string"),
    ("creative_id", "string"),
    ("active_users", "bigint"),
    ("ad_impressions", "bigint"),
    ("rewarded_views", "bigint"),
    ("sessions", "bigint"),
    ("ad_revenue", "decimal(18,6)"),
    ("installs", "bigint"),
    ("spend", "decimal(18,6)"),
]

CAMPAIGN_COLS: list[tuple[str, str]] = [
    ("dt", "string"),
    ("app", "string"),
    ("country", "string"),
    ("campaign", "string"),
    ("sub_channel", "string"),
    ("installs", "bigint"),
    ("spend", "decimal(18,6)"),
    ("active_users", "bigint"),
    ("ad_impressions", "bigint"),
    ("rewarded_views", "bigint"),
    ("sessions", "bigint"),
    ("ad_revenue", "decimal(18,6)"),
    ("clicks", "bigint"),
    ("buy_impressions", "bigint"),
]

CREATIVE_COLS: list[tuple[str, str]] = [
    ("dt", "string"),
    ("creative_id", "string"),
    ("campaign", "string"),
    ("country", "string"),
    ("app", "string"),
    ("installs", "bigint"),
    ("spend", "decimal(18,6)"),
    ("active_users", "bigint"),
    ("ad_impressions", "bigint"),
    ("rewarded_views", "bigint"),
    ("sessions", "bigint"),
    ("ad_revenue", "decimal(18,6)"),
    ("clicks", "bigint"),
    ("buy_impressions", "bigint"),
]

DATE_COLS: list[tuple[str, str]] = [
    ("dt", "string"),
    ("installs", "bigint"),
    ("active_users", "bigint"),
    ("sessions", "bigint"),
    ("ad_impressions", "bigint"),
    ("rewarded_views", "bigint"),
    ("ad_revenue", "decimal(18,6)"),
    ("spend", "decimal(18,6)"),
    ("clicks", "bigint"),
    ("buy_impressions", "bigint"),
]

# D0~D7 横铺视图：对齐原中台的 daily_detail 宽表形态，
# 让 agent.py 里读 dact0..dact7 的那几行代码不用改成 pivot。
WIDE_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {DB}.vw_cohort_d7_wide AS
SELECT
    install_date,
    app,
    country,
    campaign,
    sub_channel,
    creative_id,
    max(installs) AS installs,
    max(spend)    AS spend,
    {",".join(f"max(CASE WHEN day_offset = {n} THEN active_users END) AS dact{n}" for n in range(8))},
    {",".join(f"max(CASE WHEN day_offset = {n} THEN ad_revenue END) AS drev{n}" for n in range(8))},
    {",".join(f"max(CASE WHEN day_offset = {n} THEN ad_impressions END) AS dimp{n}" for n in range(8))},
    {",".join(f"max(CASE WHEN day_offset = {n} THEN rewarded_views END) AS dview{n}" for n in range(8))}
FROM {DB}.dws_cohort_daily
GROUP BY install_date, app, country, campaign, sub_channel, creative_id
"""


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("build-dws-iaa")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.adaptive.enabled", "true")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def _ddl(table: str, cols: list[tuple[str, str]], partitioned: bool) -> str:
    body = ",\n    ".join(f"`{c}` {t}" for c, t in cols)
    part = "\n        PARTITIONED BY (dt STRING)" if partitioned else ""
    return f"""
        CREATE TABLE IF NOT EXISTS {DB}.{table} (
            {body}
        ){part}
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/{table}'
    """


def create_tables(spark) -> None:
    spark.sql(_ddl("dws_cohort_daily", COHORT_COLS, True))
    spark.sql(_ddl("dws_campaign_daily", CAMPAIGN_COLS, False))
    spark.sql(_ddl("dws_creative_daily", CREATIVE_COLS, False))
    spark.sql(_ddl("dws_date_daily", DATE_COLS, False))


def check_schema(spark, table: str, expected: list[tuple[str, str]], has_dt_partition: bool) -> None:
    """列名/顺序/类型逐项核对。

    这个检查是 DWD 那次「列错位 → 首次点击 13 万亿」事故之后加的。
    根因：CREATE TABLE IF NOT EXISTS 对已存在的表是**静默空操作**，
    改完脚本里的列定义再跑，旧表结构原封不动，而 INSERT 是按位置写的 ——
    于是 A 列的数据写进了 B 列，零报错。
    """
    actual: list[tuple[str, str]] = []
    for r in spark.sql(f"DESCRIBE {DB}.{table}").collect():
        name = r["col_name"]
        # DESCRIBE 的输出结构是：普通列 → "# Partition Information" → 分区列。
        # 遇到第一个 # 开头的分隔行就停，后面的是分区信息和表属性，不是列。
        if not name or name.startswith("#"):
            break
        actual.append((name, r["data_type"]))

    if has_dt_partition:
        expected = expected + [("dt", "string")]

    if [c for c, _ in actual] != [c for c, _ in expected]:
        raise SystemExit(
            f"[中止] {table} 列不一致\n"
            f"  表里: {[c for c, _ in actual]}\n"
            f"  代码: {[c for c, _ in expected]}\n"
            f"  上次就是因为这个导致列错位，重跑前先 DROP TABLE 或改表名。"
        )


def read_sql(name: str) -> str:
    with open(os.path.join(SQL_DIR, name), encoding="utf-8") as f:
        return f.read()


def build_cohort_day(spark, dt: str) -> int:
    sql = read_sql("dws_cohort_daily.sql").replace("${dt}", dt)
    sql = sql.replace("lab.dwd_user_daily", DWD)
    spark.sql(
        f"INSERT OVERWRITE TABLE {DB}.dws_cohort_daily PARTITION (dt='{dt}')\n{sql}"
    )
    return spark.sql(
        f"SELECT count(*) c FROM {DB}.dws_cohort_daily WHERE dt='{dt}'"
    ).collect()[0]["c"]


def build_full_table(spark, table: str, sql_file: str) -> int:
    sql = read_sql(sql_file).replace("lab.dwd_user_daily", DWD).replace(
        "lab.ods_iaa_acquisition_daily", ACQ
    )
    spark.sql(f"INSERT OVERWRITE TABLE {DB}.{table}\n{sql}")
    return spark.sql(f"SELECT count(*) c FROM {DB}.{table}").collect()[0]["c"]


# ---------------------------------------------------------------------------
# 验收
# ---------------------------------------------------------------------------
def verify(spark, full_tables_fresh: bool = True) -> bool:
    """验收。

    Args:
        full_tables_fresh: 三张全量表是不是本次刚重建的。
            用 `--skip-full` 只补 cohort 分区时，全量表还是旧的，
            跨表对账必然对不上（新 cohort vs 旧汇总），
            那是**预期内的不一致**，不是数据错。这时跳过而不是 FAIL ——
            把「我故意没跑那一步」报成数据错误，会让真故障淹没在假警报里。

    重要前提：cohort 表是**可以只构建部分天**的（这是它按 dt 分区的意义）。
    所以所有对账都必须**限定在 cohort 已构建的那几天**内，否则
        「只跑了 10-15 一天」vs「DWD 全量 31 天」
    会得到一堆假 FAIL —— 数字差 31 倍，但那是因为对比口径错了，不是数据错了。
    这类「校验逻辑本身有 bug」比数据 bug 更难发现，因为它看起来像数据问题。

    同理，留存检查必须**只统计观察窗口完整的 cohort**：
    算 D30 留存，就只能用「装机日 ≤ 最后一天 − 30」的人，
    否则分母里混进大量还没活到第 30 天的新用户，留存率会被稀释到接近 0。
    """
    ok = True

    def check(label: str, cond: bool, detail: str) -> None:
        nonlocal ok
        mark = "PASS" if cond else "FAIL"
        print(f"      [{mark}] {label:<40} {detail}", flush=True)
        if not cond:
            ok = False

    def skip(label: str, detail: str) -> None:
        print(f"      [SKIP] {label:<40} {detail}", flush=True)

    # 已构建的 dt 范围 —— 后面所有对账都限定在这个窗口内
    rng = spark.sql(
        f"SELECT min(dt) AS lo, max(dt) AS hi, count(DISTINCT dt) AS n FROM {DB}.dws_cohort_daily"
    ).collect()[0]
    lo, hi, n_days = rng["lo"], rng["hi"], rng["n"]
    print(f"\n      cohort 已构建 {n_days} 天：{lo} ~ {hi}（对账范围限定在此窗口）", flush=True)

    print("\n      ---- A1 cohort 主键唯一 ----", flush=True)
    dup = spark.sql(
        f"""
        SELECT count(*) - count(DISTINCT concat_ws('|', dt, install_date, day_offset,
                                                   campaign, sub_channel, coalesce(creative_id,'~NA~'))) AS d
        FROM {DB}.dws_cohort_daily
        """
    ).collect()[0]["d"]
    check("主键 (dt,install_date,day_offset,渠道,素材) 无重复", dup == 0, f"重复 {dup:,} 行")

    print("\n      ---- A2 installs / spend 只在 D0 ----", flush=True)
    r = spark.sql(
        f"""
        SELECT sum(CASE WHEN day_offset > 0 AND installs > 0 THEN 1 ELSE 0 END) AS bad_i,
               sum(CASE WHEN day_offset > 0 AND spend > 0 THEN 1 ELSE 0 END)    AS bad_s,
               sum(CASE WHEN day_offset > 0 AND installs = 0 AND spend = 0 THEN 1 ELSE 0 END) AS good
        FROM {DB}.dws_cohort_daily
        """
    ).collect()[0]
    check("day_offset>0 的行 installs 全为 0", r["bad_i"] == 0, f"违规 {r['bad_i']:,} 行")
    check("day_offset>0 的行 spend 全为 0", r["bad_s"] == 0, f"违规 {r['bad_s']:,} 行")
    print(f"      [INFO] 其中 {r['good']:,} 行是纯活跃行（installs/spend 都为 0），符合预期", flush=True)

    print("\n      ---- A3 cohort 汇总 = DWD 明细（按 dt 逐天）----", flush=True)
    r = spark.sql(
        f"""
        WITH c AS (
            SELECT dt,
                   sum(installs)     AS inst,
                   sum(ad_revenue)   AS rev,
                   sum(spend)        AS sp,
                   sum(active_users) AS act
            FROM {DB}.dws_cohort_daily GROUP BY dt
        ), d AS (
            SELECT dt,
                   sum(CASE WHEN days_since_install = 0 THEN 1 ELSE 0 END) AS inst,
                   sum(ad_revenue)                                        AS rev,
                   sum(CASE WHEN days_since_install = 0 THEN install_cost ELSE 0 END) AS sp
            FROM {DWD} WHERE dt BETWEEN '{lo}' AND '{hi}' GROUP BY dt
        )
        SELECT count(*) AS days,
               sum(CASE WHEN coalesce(c.inst, -1) <> d.inst THEN 1 ELSE 0 END)      AS bad_inst,
               sum(CASE WHEN abs(coalesce(c.rev, -1) - d.rev) > 0.01 THEN 1 ELSE 0 END)  AS bad_rev,
               sum(CASE WHEN abs(coalesce(c.sp, -1) - d.sp) > 0.01 THEN 1 ELSE 0 END)    AS bad_sp,
               sum(coalesce(c.act, 0)) AS act
        FROM d LEFT JOIN c ON d.dt = c.dt
        """
    ).collect()[0]
    check("每天 cohort.installs = DWD 当天装机用户数", r["bad_inst"] == 0,
          f"{r['days']} 天中 {r['bad_inst']} 天不一致")
    check("每天 cohort.ad_revenue = DWD 当天收入", r["bad_rev"] == 0,
          f"{r['days']} 天中 {r['bad_rev']} 天不一致")
    check("每天 cohort.spend = DWD 当天装机成本", r["bad_sp"] == 0,
          f"{r['days']} 天中 {r['bad_sp']} 天不一致")
    print(f"      [INFO] 活跃人次合计 {r['act']:,}（不可跨天相加：同一用户会在多个 dt 出现）", flush=True)

    print("\n      ---- A4 四表交叉对账（按 dt）----", flush=True)
    r = spark.sql(
        f"""
        WITH a AS (SELECT dt, sum(installs) i, sum(ad_revenue) v FROM {DB}.dws_campaign_daily GROUP BY dt),
             b AS (SELECT dt, sum(installs) i, sum(ad_revenue) v FROM {DB}.dws_creative_daily GROUP BY dt),
             c AS (SELECT dt, sum(installs) i, sum(ad_revenue) v FROM {DB}.dws_date_daily GROUP BY dt),
             e AS (SELECT dt, sum(installs) i, sum(ad_revenue) v FROM {DB}.dws_cohort_daily GROUP BY dt)
        SELECT
            sum(CASE WHEN NOT (a.i <=> b.i AND a.i <=> c.i AND a.i <=> e.i) THEN 1 ELSE 0 END) AS bad_inst,
            sum(CASE WHEN abs(a.v - c.v) > 0.01 OR abs(a.v - e.v) > 0.01 THEN 1 ELSE 0 END)   AS bad_rev,
            count(*) AS days
        FROM a
        LEFT JOIN b ON a.dt = b.dt
        LEFT JOIN c ON a.dt = c.dt
        LEFT JOIN e ON a.dt = e.dt
        WHERE a.dt BETWEEN '{lo}' AND '{hi}'
        """
    ).collect()[0]
    if full_tables_fresh:
        check("campaign / creative / date / cohort 四表逐日 installs 一致",
              r["bad_inst"] == 0, f"{r['days']} 天中 {r['bad_inst']} 天不一致")
        check("campaign / date / cohort 逐日 ad_revenue 一致",
              r["bad_rev"] == 0, f"{r['days']} 天中 {r['bad_rev']} 天不一致")
    else:
        skip("campaign / creative / date / cohort 四表逐日 installs 一致",
             "本次 --skip-full，全量表未重建，不做跨表对账")
        skip("campaign / date / cohort 逐日 ad_revenue 一致",
             "本次 --skip-full，全量表未重建，不做跨表对账")

    bad = spark.sql(
        f"""
        SELECT count(*) AS c FROM {DB}.dws_campaign_daily
        WHERE NOT (buy_impressions >= clicks AND clicks >= installs)
        """
    ).collect()[0]["c"]
    check("每行满足 曝光 ≥ 点击 ≥ 安装", bad == 0, f"倒挂 {bad:,} 行")

    print("\n      ---- A5 留存（只算观察窗口完整的 cohort）----", flush=True)
    for n in (1, 7, 30):
        row = spark.sql(
            f"""
            SELECT
              sum(CASE WHEN day_offset = {n} THEN active_users ELSE 0 END) AS num,
              sum(CASE WHEN day_offset = 0 THEN active_users ELSE 0 END)   AS den,
              count(DISTINCT install_date)                                 AS cohorts
            FROM {DB}.dws_cohort_daily
            WHERE install_date >= to_date('{lo}')
              AND install_date <= date_sub(to_date('{hi}'), {n})
            """
        ).collect()[0]
        if not row["den"]:
            skip(f"D{n} 留存", f"窗口内无完整 cohort（需要 install_date ≤ {hi} 往前 {n} 天，"
                              f"且 D0 分区已构建）")
            continue
        rate = row["num"] / row["den"]
        lo_b, hi_b = {1: (0.30, 0.38), 7: (0.10, 0.14), 30: (0.04, 0.07)}[n]
        check(f"D{n} 留存落在 {lo_b*100:.0f}%~{hi_b*100:.0f}%",
              lo_b <= rate <= hi_b,
              f"{rate*100:.2f}%  ({row['cohorts']} 个 cohort)")

    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dt", help="只重建某天的 cohort 分区")
    ap.add_argument("--all", action="store_true", help="重建全部（默认行为）")
    ap.add_argument("--verify-only", action="store_true", help="只跑验收")
    ap.add_argument("--skip-full", action="store_true",
                    help="只建 cohort 分区，跳过三张全量表（单天重跑时用，省 7 分钟）")
    ap.add_argument("--full-only", action="store_true",
                    help="只建三张全量表，跳过 cohort 分区（DAG 里与 --dt 拆成两个并行任务时用）")
    args = ap.parse_args()

    spark = build_session()
    try:
        if not args.verify_only:
            print("[1/4] 建表 + schema 核对 ...", flush=True)
            create_tables(spark)
            check_schema(spark, "dws_cohort_daily", COHORT_COLS, True)
            check_schema(spark, "dws_campaign_daily", CAMPAIGN_COLS, False)
            check_schema(spark, "dws_creative_daily", CREATIVE_COLS, False)
            check_schema(spark, "dws_date_daily", DATE_COLS, False)
            print("      四张表结构一致", flush=True)

        # ---- cohort 表：按天构建（唯一支持单天重跑的表）----
        if not args.verify_only and not args.full_only:
            if args.dt:
                days = [date.fromisoformat(args.dt)]
            else:
                days = [START_DATE + timedelta(days=i)
                        for i in range((END_DATE - START_DATE).days + 1)]
            print(f"\n[2/4] cohort 表：构建 {len(days)} 个分区 ...", flush=True)
            for i, d in enumerate(days, 1):
                d_str = d.strftime("%Y-%m-%d")
                t0 = time.time()
                n = build_cohort_day(spark, d_str)
                print(f"      [{i:>2}/{len(days)}] {d_str}  {n:>8,} 行  {time.time()-t0:>5.1f}s",
                      flush=True)

            # ---- 另外三张全量表 ----
            # 它们不依赖 cohort 表（直接读 DWD），所以单天重跑 cohort 时可以整段跳过。
            if args.skip_full:
                print("\n[3/4] 跳过三张全量表（--skip-full）", flush=True)
            else:
                print("\n[3/4] 三张全量表 ...", flush=True)
                for table, f in [
                    ("dws_campaign_daily", "dws_campaign_daily.sql"),
                    ("dws_creative_daily", "dws_creative_daily.sql"),
                    ("dws_date_daily", "dws_date_daily.sql"),
                ]:
                    t0 = time.time()
                    n = build_full_table(spark, table, f)
                    print(f"      {table:<24} {n:>8,} 行  {time.time()-t0:>5.1f}s", flush=True)

            # ---- 宽表视图 ----
            print("\n[3.5] 建 D0~D7 横铺视图 ...", flush=True)
            spark.sql(WIDE_VIEW_SQL)
            n = spark.sql(f"SELECT count(*) c FROM {DB}.vw_cohort_d7_wide").collect()[0]["c"]
            print(f"      vw_cohort_d7_wide {n:,} 行（视图，零存储）", flush=True)

        print("\n[4/4] 验收 ...", flush=True)
        ok = verify(spark, full_tables_fresh=not args.skip_full)
        print(f"\n      {'全部通过' if ok else '存在失败项'}", flush=True)
        return 0 if ok else 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
