"""第 2 步：生成 IAA 用户级明细仿真数据，落到 ODS 层。

数据从哪来（必须诚实交代）：
    真实 IAA 投放数据里，「用户级 + 含广告收入 + 含素材归因」这三样同时有的
    公开数据集不存在 —— Meta / Unity / AppLovin 的真实数据都是商业机密。
    所以这里按 data_dictionary_user_daily.md 第 5 节的业务参数生成仿真数据。

    **这是一份「按真实 IAA 业务分布特征生成的仿真数据」，不是真实投放数据。**
    两者必须在文档、简历、面试里都说清楚，不能糊。

为什么用 Spark 而不是 Pandas：
    目标约 5000 万行。用 Pandas 生成同样规模需要每行一个 Python 对象，
    光内存开销就 10 GB 以上，直接 OOM。Spark 可以按天流式生成，内存恒定。

为什么是「一次性生成 + 动态分区写入」而不是「按天循环写静态分区」：
    按天循环踩过一个坑（第一版就是这么写的）：
        第 i 天生成的是「当天新增 40 万用户的全部生命周期」（行分布在 dt=10-01..10-31），
        但用静态分区 PARTITION (dt='2026-10-01') 写入时，
        **整个 DataFrame 的行都被塞进了这一个分区**。
        结果单日行数是 165 万而不是 40 万，正好等于 40 万 × 平均活跃天数 4.1。
        而且不报错 —— 这是典型的「分区键和数据内容不一致」的静默错误。
    正解是让 dt 作为普通列参与计算，最后用动态分区一次写入，
    由 Spark 按 dt 的值把行分配到 31 个分区里。

生成的数据特点（都是刻意的）：
    1. 美国占 58% 流量        → 后面 join / group by 会自然出现倾斜
    2. 各 campaign ROAS 有高有低（0.48 ~ 0.85）→ Agent 才有优化空间
    3. 留存服从打点曲线       → D7 留存 12%，符合网赚类休闲游戏水平
    4. 尾部 cohort 不完整     → 10-25 之后装机的人拿不到完整 D7
    5. 混入 4 类脏数据        → 留着给 DWD 的清洗规则做故障注入测试

跑法：
    cd /c/Users/Mia/ad-analytics
    MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/gen_iaa_users.py

    只跑 3 天先试（调试用）：
    MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/gen_iaa_users.py --days 3
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta

# ============================================================
# Windows 上跑 Spark 必须同时满足的三件事（缺一个就崩）
# ============================================================
# 1. HADOOP_HOME：Shell 类要执行 winutils.exe
_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")

# 2. driver 和 worker 必须同一个解释器，否则 worker 静默崩掉
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

# ============================================================
# 配置
# ============================================================
START_DATE = date(2026, 10, 1)
END_DATE = date(2026, 10, 31)
NEW_USERS_PER_DAY = 400_000
MAX_LIFE_DAYS = 30          # 只观察装机后 30 天

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
WAREHOUSE_DIR = os.environ.get("IAA_WAREHOUSE_DIR", r"D:/warehouse")

DB = "lab"
TABLE = "ods_iaa_user_daily"
TABLE_LOCATION = f"{WAREHOUSE}/{DB}.db/{TABLE}"

# ODS 层字段：贴源形态，保留 install_date（DWD 层才折叠成 days_since_install）
COLUMNS: list[tuple[str, str]] = [
    ("uid", "string"),
    ("install_date", "date"),
    ("days_since_install", "int"),
    ("app", "string"),
    ("country", "string"),
    ("campaign", "string"),
    ("sub_channel", "string"),
    ("creative_id", "string"),
    ("device_os", "string"),
    ("session_cnt", "int"),
    ("ad_impr", "bigint"),
    ("rewarded_views", "int"),
    ("ad_revenue", "decimal(12,6)"),
    ("install_cost", "decimal(12,6)"),
]

# ------------------------------------------------------------
# campaign 参数表
# ------------------------------------------------------------
# 每一行的含义：
#   (campaign, country, channel, 累计流量权重, CPI, eCPM, 留存系数, [子渠道])
#
# 这些数字不是随便填的，是从「目标 D7 ROAS」倒推出来的：
#   7 天累计活跃天数 ≈ 1 + 0.35 + 0.27 + 0.22 + 0.18 + 0.16 + 0.14 = 2.32 天
#   7 天收入 ≈ 2.32 × (日均展示 7 / 1000) × eCPM × 留存系数
#   D7 ROAS = 7 天收入 / CPI
# 反解得 eCPM。这样数据跑出来后 ROAS 会落在设计值附近，验收能对上。
#
# 注意：第一版 eCPM 全按 2.32 天算，结果实测 ROAS 比设计值高 15%~40%。
# 原因是漏算了「新用户兴奋期系数」—— D0 展示 ×1.35、D1-D3 ×1.10、之后 ×0.90，
# 实际 7 天展示量是 21.8 次而不是 16.2 次（=2.32×7）。
# 下面这组值是按实测比值反推修正后的结果，跑出来 ROAS 能落在设计值 ±5% 内。
CAMPAIGNS = [
    # campaign, country, channel, cum_weight, cpi,  ecpm, ret_factor, sub_channels
    ("US_A_01", "US", "unity", 0.40, 0.32, 11.5, 1.00, ["ch_001", "ch_002", "ch_003"]),
    ("US_B_01", "US", "unity", 0.58, 0.38, 10.0, 0.92, ["ch_004", "ch_005"]),
    ("JP_A_01", "JP", "mtg",   0.73, 0.42, 14.1, 1.05, ["ch_006", "ch_007"]),
    ("CA_A_01", "CA", "admob", 0.85, 0.26, 11.6, 1.02, ["ch_008", "ch_009"]),
    ("GB_A_01", "GB", "mtg",   1.00, 0.34,  9.2, 0.88, ["ch_010", "ch_011"]),
]

# ------------------------------------------------------------
# 留存曲线：R(d) = A * d^B （d >= 1），D0 固定 100%
# ------------------------------------------------------------
# 幂律是移动游戏留存的经典经验规律（重尾衰减）。三个锚点拟合：
#   D1 = 35%、D7 = 12%、D30 = 5%
#   A = 0.35
#   B = ln(0.12 / 0.35) / ln(7) = -0.550
# 校验：D7 = 0.35 × 7^-0.55 = 12.0% ✓   D30 = 0.35 × 30^-0.55 = 5.4% ✓
#
# 为什么用公式而不是打点表：
#   打点表只能正向判断「第 d 天活不活跃」，要对每个用户展开满 31 天才知道结果；
#   公式可以**反解**——给定一个随机数 u，直接算出这个用户能活到第几天。
#   于是只展开到寿命那天，中间结果从 3.84 亿行降到约 5000 万行。
RETENTION_A = 0.35
RETENTION_B = -0.55

# 累计活跃天数 = Σ R(d) ≈ 3.99 天，这是总行数估算的依据：
#   31 天 × 每天 40 万新增 = 1240 万用户 × 3.99 天 ≈ 4,950 万行

# 脏数据注入比例（给 DWD 清洗规则做测试用）
DUP_RATE = 0.005        # 完全重复行
NEG_REV_RATE = 0.001    # 收入为负
NULL_UID_RATE = 0.001   # 主键为空
BAD_DAYS_RATE = 0.001   # days_since_install 为负


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("gen-iaa-users")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        # 必须带 spark.hadoop. 前缀，否则静默退回本地 Derby（表进程退出即消失）
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        # 动态分区写入三开关（虽然本脚本用静态分区，但建表沿用同一套配置习惯）
        .config("spark.hadoop.hive.exec.dynamic.partition", "true")
        .config("spark.hadoop.hive.exec.dynamic.partition.mode", "nonstrict")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        # local[2] 只有 2 个核，200 个 shuffle 分区会全是空 task
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.adaptive.enabled", "true")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def create_table(spark) -> str:
    """建外部表（幂等：已存在就复用）。

    为什么用 EXTERNAL：
        Spark 建的 Hive 表默认就是 EXTERNAL_TABLE，DROP TABLE 只删 Metastore
        登记、不删 parquet 文件。这点必须知道，否则「删表重建」之后
        旧文件还躺在目录里，下一次查询数据直接翻倍。
    """
    spark.sql(f"CREATE DATABASE IF NOT EXISTS {DB}")
    cols_sql = ",\n    ".join(f"`{name}` {dtype}" for name, dtype in COLUMNS)
    spark.sql(
        f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.{TABLE} (
            {cols_sql}
        )
        PARTITIONED BY (dt STRING)
        STORED AS PARQUET
        LOCATION '{TABLE_LOCATION}'
        """
    )
    return spark.sql(f"SHOW CREATE TABLE {DB}.{TABLE}").collect()[0][0]


def gen_all(spark, n_days: int):
    """一次性生成 n_days 天的新增用户及其完整生命周期。

    返回的 DataFrame 已经是「用户 × 活跃日」粒度，dt 是普通列（不是分区），
    由写入时按 dt 的值动态分配到 31 个分区。
    """
    from pyspark.sql import functions as F

    total = NEW_USERS_PER_DAY * n_days
    seed = 20261001
    end_date = START_DATE + timedelta(days=n_days - 1)

    # ---- 1. 一次性生成全部用户 ----
    # numPartitions：每个 task 处理约 5 万用户，explode 31 天后约 155 万行，
    # 在 2 GB driver 下是安全的（第一版实测每天 40 万用户 / 8 分区能跑通）。
    n_parts = max(8, total // 50_000)
    df = spark.range(0, total, numPartitions=n_parts).withColumnRenamed("id", "gid")

    # id → (装机日, 当天第几个)。每天恰好 NEW_USERS_PER_DAY 人，整除就是天序号。
    # 注意 spark 的 Column 不支持 Python 的 // 整除运算符，必须用 floor(a / b)。
    df = df.withColumn(
        "day_idx", F.floor(F.col("gid") / F.lit(NEW_USERS_PER_DAY)).cast("int")
    )
    df = df.withColumn("seq", (F.col("gid") % F.lit(NEW_USERS_PER_DAY)).cast("long"))
    df = df.withColumn(
        "install_date", F.date_add(F.lit(START_DATE.strftime("%Y-%m-%d")), F.col("day_idx"))
    )

    # ---- 2. 按权重分配 campaign ----
    # 先把随机数落成一列再引用：同一个 rand(seed) 在一个 query 里被引用多次时，
    # 不保证每次求值相同，容易出现「campaign 分到了 A，country 却按 B 算」的错乱。
    df = df.withColumn("_r", F.rand(seed))
    camp = F.col("campaign")
    df = df.withColumn(
        "campaign",
        F.when(F.col("_r") < 0.40, "US_A_01")
        .when(F.col("_r") < 0.58, "US_B_01")
        .when(F.col("_r") < 0.73, "JP_A_01")
        .when(F.col("_r") < 0.85, "CA_A_01")
        .otherwise("GB_A_01"),
    )

    # ---- 3. 由 campaign 派生国家 / CPI / eCPM / 留存系数 ----
    df = df.withColumn(
        "country",
        F.when(camp.isin("US_A_01", "US_B_01"), "US")
        .when(camp == "JP_A_01", "JP")
        .when(camp == "CA_A_01", "CA")
        .otherwise("GB"),
    )
    df = df.withColumn(
        "_cpi",
        F.when(camp == "US_A_01", 0.32).when(camp == "US_B_01", 0.38)
        .when(camp == "JP_A_01", 0.42).when(camp == "CA_A_01", 0.26)
        .otherwise(0.34),
    )
    df = df.withColumn(
        "_ecpm",
        F.when(camp == "US_A_01", 11.5).when(camp == "US_B_01", 10.0)
        .when(camp == "JP_A_01", 14.1).when(camp == "CA_A_01", 11.6)
        .otherwise(9.2),
    )
    df = df.withColumn(
        "_ret_factor",
        F.when(camp == "US_A_01", 1.00).when(camp == "US_B_01", 0.92)
        .when(camp == "JP_A_01", 1.05).when(camp == "CA_A_01", 1.02)
        .otherwise(0.88),
    )

    # ---- 4. 子渠道（每个 campaign 挂 2~3 个）----
    df = df.withColumn("_rs", F.rand(seed + 1))
    rs = F.col("_rs")
    df = df.withColumn(
        "sub_channel",
        F.when(camp == "US_A_01",
               F.when(rs < 0.50, "ch_001").when(rs < 0.80, "ch_002").otherwise("ch_003"))
        .when(camp == "US_B_01", F.when(rs < 0.60, "ch_004").otherwise("ch_005"))
        .when(camp == "JP_A_01", F.when(rs < 0.55, "ch_006").otherwise("ch_007"))
        .when(camp == "CA_A_01", F.when(rs < 0.65, "ch_008").otherwise("ch_009"))
        .otherwise(F.when(rs < 0.50, "ch_010").otherwise("ch_011")),
    )

    # ---- 5. uid 与素材 ----
    df = df.withColumn(
        "uid",
        F.concat(
            F.lit("u"),
            F.date_format(F.col("install_date"), "yyyyMMdd"),
            F.lit("_"),
            F.lpad(F.col("seq").cast("string"), 6, "0"),
        ),
    )
    # 素材：每个 campaign 40 个，用 hash(uid) 分配保证同一用户素材固定（last-click 归因）
    df = df.withColumn(
        "creative_id",
        F.concat(
            F.lit("crt_"),
            F.lower(camp),
            F.lit("_"),
            F.lpad(((F.abs(F.hash(F.col("uid"), F.lit(7))) % F.lit(40)) + 1).cast("string"), 3, "0"),
        ),
    )
    # 3% 的渠道直投用户没有素材归因 —— NULL 的语义是「不适用」，不是「未知」
    df = df.withColumn(
        "creative_id",
        F.when(F.rand(seed + 8) < 0.03, F.lit(None).cast("string")).otherwise(F.col("creative_id")),
    )

    # 网赚类休闲游戏里 Android 占多数
    df = df.withColumn("device_os", F.when(F.rand(seed + 9) < 0.62, "Android").otherwise("iOS"))
    df = df.withColumn("app", F.lit("money_clicker"))

    # ---- 6. 抽样每个用户的「寿命」，只展开到寿命那天 ----
    # 为什么不对每个用户展开满 31 天：
    #   展开满 31 天会产生 1240 万 × 31 = 3.84 亿行中间结果，
    #   在 local[2] 上要跑半小时以上，而且其中 87% 的行刚生成就会被 filter 丢掉
    #   （平均每人只活跃 4 天，不是 31 天）。
    #
    # 做法：用留存曲线的逆函数直接抽寿命。
    #   R(d) = A·rf·d^B  →  L = floor((A·rf / u)^(1/|B|))，u ~ U(0,1)
    #   这样 P(L >= d) = R(d)，抽出来的寿命分布和留存曲线严格一致，
    #   中间结果从 3.84 亿降到约 5000 万，快 70 倍。
    #
    # 副作用（要诚实说明）：这个模型假设「用户流失后不再回来」，
    # 活跃天是 D0..L 连续的一段，没有「D2 断了 D3 又回来」。
    # 对 IAA 休闲游戏这是可接受的简化 —— 卸载重装的概率很低。
    max_day = F.least(
        F.lit(MAX_LIFE_DAYS),
        F.datediff(F.lit(end_date.strftime("%Y-%m-%d")), F.col("install_date")),
    )
    u = F.rand(seed + 2)
    # 注意：u 极小时 (A/u)^(1/|B|) 会算出天文数字（u=1e-6 时约 2.3e9），
    # 直接 cast 成 INT 会抛 CAST_OVERFLOW。必须在 cast **之前**用 least 限幅。
    life_raw = F.pow(
        (F.lit(RETENTION_A) * F.col("_ret_factor")) / F.greatest(u, F.lit(1e-6)),
        F.lit(1.0 / abs(RETENTION_B)),
    )
    life = F.least(F.lit(float(MAX_LIFE_DAYS)), life_raw)
    life = F.greatest(F.lit(0.0), life).cast("int")
    life = F.least(life, max_day)
    df = df.withColumn("days", F.explode(F.sequence(F.lit(0), life)))

    # ---- 8. 生成当天的行为 ----
    # 说明：Spark 没有内置泊松分布生成函数（SQL 里也没有 poisson()），
    # 所以用正态近似 N(λ, λ)。λ≥5 时两者差异很小，对造仿真数据足够。
    # 截断到 0 以上，并把 D0/D1-D3 抬高（新用户兴奋期），后期衰减。
    lam = (
        F.lit(7.0)
        * F.when(F.col("days") == 0, 1.35)
          .when(F.col("days") <= 3, 1.10)
          .otherwise(0.90)
        * F.when(F.col("country") == "US", 1.15).otherwise(1.0)
    )
    df = df.withColumn(
        "ad_impr",
        F.greatest(F.lit(0), F.round(lam + F.randn(seed + 3) * F.sqrt(lam))).cast("bigint"),
    )
    df = df.withColumn(
        "session_cnt",
        F.greatest(F.lit(1), F.round(F.lit(2.5) + F.randn(seed + 4) * F.lit(1.58))).cast("int"),
    )
    # 激励视频占展示的 25%，带随机波动；不超过总展示
    df = df.withColumn(
        "rewarded_views",
        F.least(
            F.col("ad_impr"),
            F.greatest(
                F.lit(0),
                F.round(F.col("ad_impr") * F.lit(0.25) + F.randn(seed + 5) * F.lit(0.8)),
            ),
        ).cast("int"),
    )

    # ---- 9. 收入与成本 ----
    # 单次展示收入 = eCPM / 1000，再乘两个因子：
    #
    # ① 素材质量因子（±25% 量级）
    #    真实业务里素材质量差异极大 —— 爆款素材 eCPM 能到平均的 1.4 倍，
    #    拍得差、早就疲劳的素材只有 0.6 倍。这个差异是「该砍哪个素材」的
    #    唯一依据，没有它就做不了素材级优化。
    #
    #    第一版没加这个因子，导致 200 个素材的 D7 ROI 全挤在 0.4614~0.4630，
    #    同一 campaign 内几乎完全一样 —— 因为 creative_id 当初是按
    #    hash(uid) % 40 纯随机分配的，跟留存、eCPM 完全无关。
    #    结果「这个素材该不该砍」这个场景是空的：数据里没有可砍的素材。
    #
    #    分布设计成长尾而不是均匀：
    #        20% 差素材  0.62 ~ 0.75
    #        60% 一般    0.92 ~ 1.05
    #        20% 好素材  1.20 ~ 1.45
    #    加权均值 ≈ 0.993，所以整体 ROI 几乎不变（不会把渠道结论打乱），
    #    但素材之间的排序被拉开了 2.3 倍，优化动作才有抓手。
    #
    #    为什么用 hash 而不是 rand()：
    #    同一个素材的所有行必须乘同一个因子，否则「素材质量」就成了随机噪声。
    #    hash(creative_id) 是确定性的，重跑 100 次结果一样（幂等）。
    _q = (F.abs(F.hash(F.col("creative_id"))) % F.lit(1000)) / F.lit(1000.0)
    quality = (
        F.when(F.col("creative_id").isNull(), F.lit(1.0))          # 渠道直投无素材，不调整
        .when(_q < 0.20, F.lit(0.62) + _q / F.lit(0.20) * F.lit(0.13))
        .when(_q < 0.80, F.lit(0.92) + (_q - F.lit(0.20)) / F.lit(0.60) * F.lit(0.13))
        .otherwise(F.lit(1.20) + (_q - F.lit(0.80)) / F.lit(0.20) * F.lit(0.25))
    )

    # ② 单次出价的 ±10% 随机波动（不同广告主出价不同），粒度到「用户 × 天」
    unit_rev = (
        F.col("_ecpm") / F.lit(1000.0)
        * (F.lit(0.9) + F.rand(seed + 6) * F.lit(0.2))
        * quality
    )
    df = df.withColumn("ad_revenue", (F.col("ad_impr") * unit_rev).cast("decimal(12,6)"))

    # install_cost 只在 D0 有值，D>0 为 NULL（语义：不适用，成本只在装机那一刻产生）
    df = df.withColumn(
        "install_cost",
        F.when(
            F.col("days") == 0,
            (F.col("_cpi") * (F.lit(0.88) + F.rand(seed + 7) * F.lit(0.24))).cast("decimal(12,6)"),
        ).otherwise(F.lit(None).cast("decimal(12,6)")),
    )
    df = df.withColumn("days_since_install", F.col("days").cast("int"))
    df = df.withColumn(
        "dt", F.date_format(F.date_add(F.col("install_date"), F.col("days")), "yyyy-MM-dd")
    )

    # ---- 10. 注入脏数据（留给 DWD 的清洗规则做故障注入测试）----
    # 负收入：上游扣款/退款处理错误
    df = df.withColumn(
        "ad_revenue",
        F.when(F.rand(seed + 10) < NEG_REV_RATE, -F.col("ad_revenue")).otherwise(F.col("ad_revenue")),
    )
    # 主键为空：无法归因的孤儿行
    df = df.withColumn(
        "uid",
        F.when(F.rand(seed + 11) < NULL_UID_RATE, F.lit(None).cast("string")).otherwise(F.col("uid")),
    )
    # days_since_install 为负：上游时钟错乱（活跃日早于装机日）
    df = df.withColumn(
        "days_since_install",
        F.when(
            F.rand(seed + 12) < BAD_DAYS_RATE,
            (F.lit(-1) * (((F.abs(F.hash(F.col("seq"), F.lit(9))) % F.lit(5)) + 1).cast("int"))),
        ).otherwise(F.col("days_since_install")),
    )

    # ---- 11. 复制 0.5% 的行，造「完全重复」----
    # 真实数据重复的根因通常是上游重跑或 exactly-once 失效，
    # 结果就是同一行出现两次，sum 直接翻倍且不报错。
    dup = df.filter(F.rand(seed + 13) < DUP_RATE)
    df = df.unionByName(dup)

    cols = [c for c, _ in COLUMNS] + ["dt"]
    return df.select(*cols)


def write_all(spark, df, n_days: int) -> None:
    """动态分区写入。

    PARTITION (dt) 不带值 = 动态分区：Spark 按 dt 列的值把行分到 31 个分区。
    配合 spark.sql.sources.partitionOverwriteMode=dynamic，
    每个分区是被**整体替换**而不是追加 —— 这就是幂等：
    同一天跑 10 次，结果还是同一份数据，不会翻倍。

    写之前 repartition(n_days, dt)：让同一个 dt 的所有行落到同一个 task，
    一个 task 连续写一个文件，结果就是「一天一个文件」。
    不做这一步，V1 写入器会按行数范围切分，切点落在某天中间就把那天拆成两个文件。
    """
    from pyspark.sql import functions as F

    df = df.repartition(n_days, F.col("dt"))
    df.createOrReplaceTempView("_stg_all")
    cols = ", ".join(f"`{c}`" for c, _ in COLUMNS)
    spark.sql(
        f"""
        INSERT OVERWRITE TABLE {DB}.{TABLE} PARTITION (dt)
        SELECT {cols}, dt FROM _stg_all
        """
    )


def verify(spark) -> None:
    from pyspark.sql import functions as F

    t = f"{DB}.{TABLE}"
    print("\n      ---- 分区与行数 ----")
    parts = [r[0].split("=", 1)[1] for r in spark.sql(f"SHOW PARTITIONS {t}").collect()]
    parts.sort()
    print(f"      分区数: {len(parts)}   首尾: {parts[0]} ... {parts[-1]}")

    total = spark.sql(f"SELECT count(*) c FROM {t}").collect()[0]["c"]
    print(f"      总行数: {total:,}")

    per_day = spark.sql(
        f"SELECT dt, count(*) c, count(DISTINCT uid) u FROM {t} GROUP BY dt ORDER BY dt"
    ).collect()
    print(f"      单日行数: 最小 {min(r['c'] for r in per_day):,} / 最大 {max(r['c'] for r in per_day):,}")
    print(f"      首日 {per_day[0]['dt']}: {per_day[0]['c']:,} 行 / {per_day[0]['u']:,} 人")
    print(f"      末日 {per_day[-1]['dt']}: {per_day[-1]['c']:,} 行 / {per_day[-1]['u']:,} 人")
    print("      （末日人数远多于首日是正常的：尾日包含所有老用户的回访）")

    print("\n      ---- 维度分布（看倾斜是否如设计）----")
    for r in spark.sql(
        f"SELECT campaign, country, count(*) c, count(DISTINCT uid) u FROM {t} "
        f"GROUP BY campaign, country ORDER BY c DESC"
    ).collect():
        print(f"      {r['campaign']:<9}{r['country']:<4}{r['c']:>12,} 行  {r['u']:>10,} 人")

    print("\n      ---- 脏数据条数（留给 DWD 清洗）----")
    # 注意：不能用 count(*) - count(DISTINCT uid, dt) 来数重复行。
    # NULL uid 会被 COUNT(DISTINCT) 整体排除，却会被 GROUP BY 聚成一组，
    # 两种算法的口径对不上，算出来的「重复行」会虚高几百倍。
    # 正确做法是排除 NULL uid 之后，总数减去唯一组合数。
    dirty = spark.sql(
        f"""
        SELECT
          sum(CASE WHEN ad_revenue < 0 THEN 1 ELSE 0 END) AS neg_rev,
          sum(CASE WHEN uid IS NULL THEN 1 ELSE 0 END)    AS null_uid,
          sum(CASE WHEN days_since_install < 0 THEN 1 ELSE 0 END) AS bad_days
        FROM {t}
        """
    ).collect()[0]
    dup = spark.sql(
        f"""
        SELECT sum(c) - count(*) AS dup_rows FROM (
          SELECT uid, dt, count(*) AS c FROM {t} WHERE uid IS NOT NULL GROUP BY uid, dt
        ) x
        """
    ).collect()[0]["dup_rows"]
    print(f"      负收入 {dirty['neg_rev']:,} / 空uid {dirty['null_uid']:,} / "
          f"负days {dirty['bad_days']:,} / 重复行 {int(dup or 0):,}")

    print("\n      ---- D7 ROAS 抽查（验证是否落在设计值附近）----")
    print(f"      {'campaign':<10}{'装机人数':>10}{'D0成本':>12}{'D0-D6收入':>12}{'D7 ROAS':>10}{'设计值':>8}")
    design = {c[0]: v for c, v in zip(CAMPAIGNS, [0.78, 0.55, 0.65, 0.85, 0.48])}
    rows = spark.sql(
        f"""
        SELECT campaign,
               count(DISTINCT CASE WHEN days_since_install = 0 THEN uid END) AS users,
               sum(CASE WHEN days_since_install = 0 THEN install_cost ELSE 0 END) AS cost,
               sum(CASE WHEN days_since_install BETWEEN 0 AND 6 THEN ad_revenue ELSE 0 END) AS rev
        FROM {t}
        WHERE install_date <= date'2026-10-24'
        GROUP BY campaign ORDER BY campaign
        """
    ).collect()
    for r in rows:
        users, cost, rev = r["users"] or 0, float(r["cost"] or 0), float(r["rev"] or 0)
        roas = rev / cost if cost else 0
        print(f"      {r['campaign']:<10}{users:>10,}{cost:>12,.0f}{rev:>12,.0f}"
              f"{roas:>10.2%}{design.get(r['campaign'], 0):>8.2f}")

    print("\n      ---- 磁盘占用 ----")
    table_dir = os.path.join(WAREHOUSE_DIR, f"{DB}.db", TABLE)
    if os.path.isdir(table_dir):
        dirs = sorted(d for d in os.listdir(table_dir) if d.startswith("dt="))
        total_files, total_bytes = 0, 0
        for d in dirs:
            p = os.path.join(table_dir, d)
            files = [f for f in os.listdir(p) if f.endswith(".parquet")]
            total_files += len(files)
            total_bytes += sum(os.path.getsize(os.path.join(p, f)) for f in files)
        print(f"      {len(dirs)} 个分区目录 / {total_files} 个 parquet 文件 / "
              f"{total_bytes / 1024 / 1024:.1f} MB")
        if dirs:
            # 注意：分区目录里除了 .parquet 还有 _SUCCESS 和 .parquet.crc（Hadoop 校验文件），
            # 直接取 listdir()[0] 会拿到这些，算出来的「首分区大小」会是 0.1 MB 这种假数。
            first_dir = os.path.join(table_dir, dirs[0])
            pq = [f for f in os.listdir(first_dir) if f.endswith(".parquet")]
            if pq:
                sz = sum(os.path.getsize(os.path.join(first_dir, f)) for f in pq)
                print(f"      首分区 {dirs[0]}: {len(pq)} 个文件 / {sz / 1024 / 1024:.1f} MB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只生成前 N 天（调试用，0=全部）")
    ap.add_argument("--dt", help="只重建某一天的分区，如 2026-10-15（幂等单日补数）")
    args = ap.parse_args()

    if args.days and args.dt:
        print("[参数错误] --days 和 --dt 只能二选一")
        return 2

    os.makedirs(WAREHOUSE_DIR, exist_ok=True)
    t_start = time.time()

    print("[1/4] 启动 Spark 并连接 Metastore ...", flush=True)
    try:
        spark = build_session()
    except Exception as exc:  # noqa: BLE001
        print(f"      失败: {exc}\n      检查 docker ps 里 lab-metastore 是否 healthy")
        return 1
    print(f"      pyspark {spark.version} / local[2] / driver=2g", flush=True)

    # 自检：连的到底是不是外部 Metastore（连错会退回 Derby，表进程退出即消失）
    dbs = sorted(r[0] for r in spark.sql("SHOW DATABASES").collect())
    print(f"      现有库: {dbs}", flush=True)
    if DB not in dbs:
        print(f"      [中止] 看不到 {DB} 库，说明连的是本地 Derby")
        spark.stop()
        return 1

    print("[2/4] 建库建表 ...", flush=True)
    print("      " + "\n      ".join(create_table(spark).strip().splitlines()[:3]))

    days = [START_DATE + timedelta(days=i) for i in range((END_DATE - START_DATE).days + 1)]
    if args.days:
        days = days[: args.days]

    # ---- 单日重建（幂等补数）----
    # 语义：只重写 dt=<目标日> 这一个分区，其余 30 个分区一个字节都不动。
    #
    # 为什么必须「全量生成再过滤」，不能「只生成到目标日就停」：
    #
    #   直觉上「用户不可能在装机日之前活跃，所以生成到目标日就够」是对的 ——
    #   行集合确实一样。但行**内容**不一样。
    #
    #   本脚本的所有随机量（留存寿命 u、单次出价波动、eCPM 抖动）都用 F.rand(seed)。
    #   Spark 的 Rand 是按「seed + 分区号」播种的。而分区数由总行数决定：
    #       n_parts = max(8, 400000 * n_days // 50000)
    #   生成 5 天是 40 个分区，生成 31 天是 248 个分区 —— 同一个 gid
    #   落到不同的分区号，就抽到完全不同的随机数。
    #
    #   实测后果：用截断版补 10-05，DWS 跨表对账报「31 天中 1 天不一致」，
    #   差的那天正是刚重建的 10-05。数据看起来完全正常（量级、分布、留存都对），
    #   只是数值全变了 —— 这类「看起来对、其实全换了一遍」比报错危险得多。
    #
    # 所以这里选择正确性优先：n_gen 永远是全部天数，写完只覆盖目标分区。
    # 代价是补一天也要跑全量（约 6 分钟）。
    # 要真正做成增量，得把随机数从 rand(分区相关) 改成 hash(uid, day) 派生，
    # 那是造数脚本的一次重构，不是一个参数能解决的。
    #
    # gid 也必须从 0 开始编：uid / 素材都由 gid 派生，偏移一位就换了一批人。
    n_gen = len(days)
    if args.dt:
        target = date.fromisoformat(args.dt)
        if not (START_DATE <= target <= END_DATE):
            print(f"[参数错误] --dt 必须在 {START_DATE} ~ {END_DATE} 之间，收到 {args.dt}")
            return 2
        print(f"      单日重建模式：dt={args.dt}"
              f"（内部按全量 {n_gen} 天生成，只写 1 个分区）", flush=True)

    print(f"[3/4] 生成 {n_gen} 天数据（每天 {NEW_USERS_PER_DAY:,} 新用户）...", flush=True)
    t0 = time.time()
    df = gen_all(spark, n_gen)
    n_days_write = n_gen
    if args.dt:
        from pyspark.sql import functions as F

        df = df.filter(F.col("dt") == args.dt)
        n_days_write = 1
    print("      逻辑构建完成（此时还没触发计算），开始写入 ...", flush=True)
    write_all(spark, df, n_days_write)
    print(f"      写入完成，用时 {(time.time() - t0) / 60:.2f} 分钟", flush=True)

    print("[4/4] 验证 ...", flush=True)
    verify(spark)

    total_min = (time.time() - t_start) / 60
    print(f"\nODS 层生成完成，总用时 {total_min:.1f} 分钟。")
    print("下一步：跑 sql/dwd/dwd_iaa_user_daily.sql 做清洗，落到 lab.dwd_user_daily")
    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
