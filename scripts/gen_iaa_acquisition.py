"""补一张「买量侧」事实表：广告投放的花费 / 点击 / 曝光。

为什么必须单独一张表，不能塞进用户明细：
    IAA 是「买量 + 变现」双轨业务，两条轨的时间轴不同：
        买量侧  只有 report_date（投放日）——投广告是当天动作，没有 cohort 概念
        变现侧  双轴（install_date + report_date）——用户哪天装的、哪天赚的钱

    更关键的是**粒度不同**：
        买量数据是「我买了 10 万次曝光，点了 4000 次，进来 500 个安装」
        用户明细是「这几个具体的用户，各自看了几次广告」

    从用户明细**算不出**前者：你不知道那 10 万次曝光对应的是这 500 个安装，
    还是同样 500 个安装背后其实花了 30 万次曝光。归因是单向的
    （曝光 → 点击 → 安装），逆向不可推导。

    metrics.yaml 里 buy_ctr / buy_cvr / buy_ecpm 三个指标都依赖 buy_impressions，
    没有这张表就直接算不出来。

为什么从 DWD 反算而不是独立随机生成：
    两轨如果各造各的，会出现「买量表说这天进来 500 人，用户表说有 480 人」，
    对账永远差 4%，而且查不出是谁的错。
    真实系统里两边也会有差异（叫归因差，来自跨设备、延迟回传），
    但那个差异是**业务事实**，不是我造数据时的 bug。
    所以这里让 installs / spend 直接取自 DWD，只把 clicks / impressions
    按「稳定转化率」反推出来 —— 保证 100% 对得上。

跑法：
    全部 31 天：   MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/gen_iaa_acquisition.py
    只重跑一天：   MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/gen_iaa_acquisition.py --dt 2026-10-15
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

SRC_DB, SRC_TABLE = "lab", "dwd_user_daily"
DB, TABLE = "lab", "ods_iaa_acquisition_daily"

START_DATE = date(2026, 10, 1)
END_DATE = date(2026, 10, 31)

COLUMNS: list[tuple[str, str]] = [
    ("app", "string"),
    ("country", "string"),
    ("campaign", "string"),
    ("sub_channel", "string"),
    ("creative_id", "string"),
    ("installs", "bigint"),
    ("spend", "decimal(12,6)"),
    ("clicks", "bigint"),
    ("buy_impressions", "bigint"),
]

# 买量侧的漏斗参数：曝光 → 点击 → 安装。
# 这些值不是拍脑袋，是从 CPI 反推出来的：
#     CPC = CPI × CVR        （每次点击花多少钱）
#     CPM = CPC × CTR × 1000 （千次曝光花多少钱）
# 代入 US_A（CPI 0.32 / CVR 0.11 / CTR 0.045）：
#     CPC = 0.0352，CPM = 1.58 美元
# 网赚类低价值流量在 Unity/Mintegral 上的真实 CPM 就是 1~3 美元这个量级，
# 所以这组参数能自然长出合理的买量成本结构，而不是硬编一个 CPM。
FUNNEL: dict[str, tuple[float, float]] = {
    # campaign: (ctr, cvr)
    "US_A_01": (0.045, 0.110),
    "US_B_01": (0.038, 0.095),
    "JP_A_01": (0.052, 0.120),
    "CA_A_01": (0.048, 0.130),
    "GB_A_01": (0.035, 0.090),
}


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("gen-iaa-acquisition")
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


def create_table(spark) -> None:
    """建表。LOCATION 写死，避免落到默认 warehouse 目录里找不到。

    注意：这里用 CREATE TABLE IF NOT EXISTS 是**安全的**，因为本表结构一旦定了
    就不会再改（不像 DWD 那次改结构后旧表静默保留导致列错位）。
    但为了防同样的坑，写入前仍然核对一次列。
    """
    cols = ",\n    ".join(f"`{c}` {t}" for c, t in COLUMNS)
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {DB}.{TABLE} (
            {cols}
        )
        PARTITIONED BY (dt STRING)
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/{TABLE}'
        """
    )


def funnel_expr(campaign_col, sub_col, creative_col, dt_col):
    """按 campaign 取漏斗参数，再叠一个确定性的 ±12% 扰动。

    为什么用 hash 而不是 rand()：
        rand() 每次执行结果不同 → 同一天重跑两次，点击率会变 →
        这就违反了幂等（同样输入必须同样输出）。
        用 hash(campaign, sub, creative, dt) 当伪随机源，结果永远一致。
    """
    from pyspark.sql import functions as F

    ctr = F.when(campaign_col == "US_A_01", 0.045) \
        .when(campaign_col == "US_B_01", 0.038) \
        .when(campaign_col == "JP_A_01", 0.052) \
        .when(campaign_col == "CA_A_01", 0.048) \
        .otherwise(0.035)
    cvr = F.when(campaign_col == "US_A_01", 0.110) \
        .when(campaign_col == "US_B_01", 0.095) \
        .when(campaign_col == "JP_A_01", 0.120) \
        .when(campaign_col == "CA_A_01", 0.130) \
        .otherwise(0.090)
    # hash 给 32 位整数，取模到 [-1, 1] 再缩到 ±12%
    noise = (
        (F.abs(F.hash(campaign_col, sub_col, creative_col, dt_col)) % F.lit(1000))
        / F.lit(1000.0) * F.lit(2.0) - F.lit(1.0)
    ) * F.lit(0.12)
    return ctr * (F.lit(1.0) + noise), cvr * (F.lit(1.0) + noise)


def gen_one(spark, dt: str) -> None:
    from pyspark.sql import functions as F

    # ---- 1. 从 DWD 取当天的装机口径（day_offset = 0 才是新装）----
    # 为什么必须限定 days_since_install = 0：
    #   买量表的一天 = 当天花了多少钱买量，只对应「当天装机」的人。
    #   如果把 day_offset > 0 的活跃用户也算进来，installs 会被放大 4 倍，
    #   CPI 直接算错（分母虚高 → CPI 偏低 → 看起来买量很便宜，这是致命的假指标）。
    base = spark.sql(
        f"""
        SELECT
            dt,
            app,
            country,
            campaign,
            sub_channel,
            creative_id,
            count(DISTINCT uid)          AS installs,
            sum(install_cost)            AS spend
        FROM {SRC_DB}.{SRC_TABLE}
        WHERE dt = '{dt}' AND days_since_install = 0
        GROUP BY dt, app, country, campaign, sub_channel, creative_id
        """
    )

    ctr, cvr = funnel_expr(F.col("campaign"), F.col("sub_channel"), F.col("creative_id"), F.col("dt"))

    # ---- 2. 反推点击与曝光 ----
    # 顺序不能反：先算 clicks（依赖 installs），再算 impressions（依赖 clicks）。
    # 一次 withColumn 引用另一个刚算出来的列是合法的，但同一表达式里互相引用不行。
    df = base.withColumn(
        "clicks",
        F.greatest(F.col("installs"), F.round(F.col("installs") / cvr).cast("bigint")),
    )
    df = df.withColumn(
        "buy_impressions",
        F.greatest(F.col("clicks"), F.round(F.col("clicks") / ctr).cast("bigint")),
    )
    df = df.withColumn("spend", F.col("spend").cast("decimal(12,6)"))

    cols = ", ".join(f"`{c}`" for c, _ in COLUMNS)
    df.repartition(1).createOrReplaceTempView("_stg_acq")
    spark.sql(
        f"""
        INSERT OVERWRITE TABLE {DB}.{TABLE} PARTITION (dt='{dt}')
        SELECT {cols} FROM _stg_acq
        """
    )


def verify_day(spark, dt: str) -> None:
    """单天对账：买量表的 installs / spend 必须和 DWD 的 D0 完全相等。"""
    row = spark.sql(
        f"""
        SELECT
            (SELECT sum(installs) FROM {DB}.{TABLE} WHERE dt='{dt}') AS acq_installs,
            (SELECT count(DISTINCT uid) FROM {SRC_DB}.{SRC_TABLE}
              WHERE dt='{dt}' AND days_since_install = 0)      AS dwd_installs,
            (SELECT sum(spend) FROM {DB}.{TABLE} WHERE dt='{dt}') AS acq_spend,
            (SELECT sum(install_cost) FROM {SRC_DB}.{SRC_TABLE}
              WHERE dt='{dt}' AND days_since_install = 0)      AS dwd_spend,
            (SELECT sum(buy_impressions) FROM {DB}.{TABLE} WHERE dt='{dt}') AS impr,
            (SELECT sum(clicks) FROM {DB}.{TABLE} WHERE dt='{dt}')         AS clk,
            (SELECT count(*) FROM {DB}.{TABLE} WHERE dt='{dt}')            AS grp
        """
    ).collect()[0]

    if row["acq_installs"] != row["dwd_installs"]:
        raise SystemExit(
            f"[中止] {dt} installs 对不上：买量表 {row['acq_installs']:,} vs DWD {row['dwd_installs']:,}"
        )
    if abs(float(row["acq_spend"]) - float(row["dwd_spend"])) > 0.01:
        raise SystemExit(
            f"[中止] {dt} spend 对不上：买量表 {row['acq_spend']} vs DWD {row['dwd_spend']}"
        )
    # 漏斗单调性：impressions >= clicks >= installs，违反说明除法算反了
    if not (row["impr"] >= row["clk"] >= row["acq_installs"]):
        raise SystemExit(f"[中止] {dt} 漏斗倒挂：impr={row['impr']} clk={row['clk']} inst={row['acq_installs']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dt", help="只处理某一天，格式 2026-10-15")
    ap.add_argument("--audit-only", action="store_true", help="只对账不写入")
    args = ap.parse_args()

    if args.dt:
        days = [date.fromisoformat(args.dt)]
    else:
        days = [START_DATE + timedelta(days=i) for i in range((END_DATE - START_DATE).days + 1)]

    spark = build_session()
    try:
        if not args.audit_only:
            create_table(spark)
            print(f"[1/3] 建表完成 {DB}.{TABLE}", flush=True)

        if args.audit_only:
            print(f"[对账] 逐天核对买量表 vs DWD ...", flush=True)
            for d in days:
                d_str = d.strftime("%Y-%m-%d")
                try:
                    verify_day(spark, d_str)
                    r = spark.sql(
                        f"SELECT sum(installs) i, sum(clicks) c, sum(buy_impressions) m, "
                        f"sum(spend) s FROM {DB}.{TABLE} WHERE dt='{d_str}'"
                    ).collect()[0]
                    ctr = r["c"] / r["m"] * 100 if r["m"] else 0
                    cvr = r["i"] / r["c"] * 100 if r["c"] else 0
                    cpm = float(r["s"]) / r["m"] * 1000 if r["m"] else 0
                    print(
                        f"      {d_str}  installs {r['i']:>7,}  ctr {ctr:5.2f}%  "
                        f"cvr {cvr:5.2f}%  cpm ${cpm:5.2f}  OK",
                        flush=True,
                    )
                except SystemExit as e:
                    print(f"      {d_str}  {e}", flush=True)
            return 0

        print(f"[2/3] 逐天生成 {len(days)} 个分区 ...", flush=True)
        for i, d in enumerate(days, 1):
            d_str = d.strftime("%Y-%m-%d")
            t0 = time.time()
            gen_one(spark, d_str)
            n = spark.sql(f"SELECT count(*) c FROM {DB}.{TABLE} WHERE dt='{d_str}'").collect()[0]["c"]
            print(f"      [{i:>2}/{len(days)}] {d_str}  {n:>7,} 组  {time.time() - t0:>5.1f}s", flush=True)

        print(f"\n[3/3] 全量对账 ...", flush=True)
        for d in days:
            verify_day(spark, d.strftime("%Y-%m-%d"))
        print("      installs / spend 与 DWD 逐天完全一致，漏斗单调性通过", flush=True)

        r = spark.sql(
            f"""
            SELECT count(*) rows,
                   sum(installs) inst,
                   sum(clicks) clk,
                   sum(buy_impressions) impr,
                   sum(spend) spend,
                   sum(clicks) / sum(buy_impressions) * 100 AS ctr,
                   sum(installs) / sum(clicks) * 100 AS cvr,
                   sum(spend) / sum(buy_impressions) * 1000 AS cpm,
                   sum(spend) / sum(installs) AS cpi
            FROM {DB}.{TABLE}
            """
        ).collect()[0]
        print(f"\n      总行数 {r['rows']:,}")
        print(f"      installs {r['inst']:,}   clicks {r['clk']:,}   曝光 {r['impr']:,}")
        print(
            f"      CTR {r['ctr']:.2f}%   CVR {r['cvr']:.2f}%   "
            f"买量CPM ${r['cpm']:.2f}   CPI ${r['cpi']:.4f}"
        )
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
