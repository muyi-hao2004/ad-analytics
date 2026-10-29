"""第 1 步：把 Criteo 的 TSV 原始文件搬进 ODS 层，按 dt 分区。

这一步的定位是「只换格式，不改数据」：
    TSV（2.47 GB，一个巨文件）
        ↓ 解析 + 类型转换 + 加分区列
    Parquet（按天切成的 31 个目录，Hive 外部表）

为什么 ODS 要做类型转换（这算不算「清洗」）：
    TSV 是纯文本，没有类型这个概念 —— 它只有字节。Parquet 是列式二进制格式，
    必须声明每一列是 BIGINT 还是 DOUBLE，否则写不进去。
    所以「定类型」不是清洗，是格式的一部分。真正的清洗在 DWD 层做。

为什么 dt 要做日期平移（重要，别在面试里被问倒）：
    原始 `timestamp` 是从 0 开始的**相对秒**（数据集做了脱敏），
    直接换算会得到 1970-01-xx，既难看又容易和真实业务日期混淆。
    所以这里映射到 2026-09-21 开始的 31 天：
        day_offset = floor(timestamp / 86400)      # 0 .. 30
        dt         = 2026-09-21 + day_offset
    原始列 `timestamp` 原样保留，所以这个平移是**无损可逆**的。

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/build_ods.py
"""

from __future__ import annotations

import os
import sys
import time

# ============================================================
# Windows 上跑 Spark 必须同时满足的三件事（缺一个就崩，报错完全不一样）
# ============================================================
# 1. HADOOP_HOME：Windows 版 Spark 的 Shell 类要执行 winutils.exe
_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")

# 2. driver 和 worker 必须是同一个解释器，否则 Python 的版本在两边不一致，
#    worker 会静默崩掉（报 EOFException / WinError 10038，看起来像环境坏了）
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

# ============================================================
# 配置
# ============================================================
TSV = "D:/data/criteo/criteo_attribution_dataset.tsv"
BASE_DATE = "2026-09-21"  # day_offset=0 映射到哪天
METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
WAREHOUSE_DIR = os.environ.get("IAA_WAREHOUSE_DIR", r"D:/warehouse")

DB = "ods"
TABLE = "ods_ad_log"
TABLE_LOCATION = f"{WAREHOUSE}/{DB}.db/{TABLE}"

# 22 列的真实类型。来源是 scripts/profile_criteo.py 的实测推断，
# 不是凭记忆写的 —— 「不要凭想象定 schema」是数据工程第一条纪律。
COLUMNS: list[tuple[str, str]] = [
    ("timestamp", "bigint"),
    ("uid", "bigint"),
    ("campaign", "bigint"),
    ("conversion", "bigint"),
    ("conversion_timestamp", "bigint"),
    ("conversion_id", "bigint"),
    ("attribution", "bigint"),
    ("click", "bigint"),
    ("click_pos", "bigint"),
    ("click_nb", "bigint"),
    ("cost", "double"),
    ("cpo", "double"),
    ("time_since_last_click", "double"),
    ("cat1", "bigint"),
    ("cat2", "bigint"),
    ("cat3", "bigint"),
    ("cat4", "bigint"),
    ("cat5", "bigint"),
    ("cat6", "bigint"),
    ("cat7", "bigint"),
    ("cat8", "bigint"),
    ("cat9", "bigint"),
]

# 我们期望每个分区产出多少个文件。
# 依据：16.5M 行 / 31 天 ≈ 53 万行/天，Parquet 压缩后约 20~30 MB/天。
# Parquet 的甜点区是 128~512 MB/文件，所以这里「一天一个文件」偏小但合理；
# 拆成更多反而制造小文件。生产环境按大小反推，通常 1~3 个。
FILES_PER_DAY = 1

# 分区数 = 天数 × 每天文件数。用 repartition("dt") 时它决定 shuffle 后的桶数。
SHUFFLE_PARTITIONS = 31 * FILES_PER_DAY


def build_session():
    """建一个连外部 Metastore 的 SparkSession。"""
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("build-ods")
        .master("local[2]")  # 16 GB 机器 + Docker 已占 5 GB，只能给 2 核 2 GB
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        # 必须带 spark.hadoop. 前缀，否则 SparkConf 会静默忽略：
        #   Warning: Ignoring non-Spark config property: hive.metastore.uris
        # 不带前缀时 session 会退回本地 Derby，建的表只存在临时目录里，
        # 进程一退就没了 —— 而且不报错，是典型的静默失败。
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        # 别每 200ms 刷一行 Stage 进度条：日志会被 \r 塞满，
        # 存到文件里再 grep 会变成一坨乱码，排错时反而看不清
        .config("spark.ui.showConsoleProgress", "false")
        # ---- 动态分区写入必需的三个开关 ----
        .config("spark.hadoop.hive.exec.dynamic.partition", "true")
        .config("spark.hadoop.hive.exec.dynamic.partition.mode", "nonstrict")
        # dynamic 模式：INSERT OVERWRITE 只覆盖本次涉及的分区，
        # static 模式会先把整张表清空 —— 这是「重跑变空表」最经典的坑
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        # ---- 小文件控制 ----
        # 这里是纯写入，没有 join / 聚合，AQE 帮不上忙，反而会偷偷合并分区，
        # 让「一天到底几个文件」变得不可预测。关掉之后结果才是可解释的。
        .config("spark.sql.shuffle.partitions", str(SHUFFLE_PARTITIONS))
        .config("spark.sql.adaptive.enabled", "false")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def create_table(spark) -> str:
    """建库 + 建外部表（幂等：已存在就复用，不 DROP）。

    为什么不 DROP 后重建：Spark 建的 Hive 表默认是 EXTERNAL_TABLE，
    DROP TABLE 只删 Metastore 里那一行登记、**不删 parquet 文件**。
    删登记再重建，旧文件还躺在目录里，下一次查询数据直接翻倍。
    这是「外部表」最容易踩的坑，很多人的数仓就是这么被搞脏的。
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
    # 读回真实落地的 schema，而不是相信我们写的那份（P1：以系统为准）
    ddl = spark.sql(f"SHOW CREATE TABLE {DB}.{TABLE}").collect()[0][0]
    return ddl


def load_and_transform(spark):
    """读 TSV → 定类型 → 算 dt。返回 DataFrame（未触发计算）。"""
    from pyspark.sql import functions as F

    raw = (
        spark.read.option("sep", "\t")
        .option("header", "true")
        .option("inferSchema", "false")  # 不让 Spark 自己猜：猜类型要全表扫一遍，白烧一轮
        .option("encoding", "UTF-8")
        .csv(TSV)
    )

    df = raw
    for name, dtype in COLUMNS:
        # 注意：DataFrame API 里**不要**给列名加反引号。
        # 反引号是 SQL 解析器的东西，只在 SQL 文本里需要。
        # 在 withColumn 里写 "`timestamp`" 会被当成一个全新列名（带反引号字符），
        # 于是原列不会被覆盖，列数从 22 变成 44 —— 而且不报错。
        df = df.withColumn(name, F.col(name).cast(dtype))

    day_offset = F.floor(F.col("timestamp") / F.lit(86400)).cast("int")
    df = df.withColumn(
        "dt",
        F.date_format(F.date_add(F.lit(BASE_DATE), day_offset), "yyyy-MM-dd"),
    )
    return df


def check_quality(spark, df) -> bool:
    """写盘之前先体检：类型转换有没有丢数。

    这是数据质量的第一道闸，代价是多扫一遍 2.47 GB。
    为什么值得：如果某列本该是数字却大面积转成 NULL，写进 ODS 之后
    DWD 算指标会得到 NULL/0，报表上看起来「只是数字变小了」，
    没有任何报错 —— 这就是「静默的数据错误」，比崩溃危险得多。
    """
    from pyspark.sql import functions as F

    aggs = [F.count("*").alias("__rows")]
    for name, _ in COLUMNS:
        aggs.append(F.sum(F.when(F.col(name).isNull(), 1).otherwise(0)).alias(name))

    t0 = time.time()
    row = df.agg(*aggs).collect()[0]
    total = row["__rows"]

    print(f"      总行数: {total:,}（用时 {time.time() - t0:.1f}s）")
    print(f"      {'列名':<24}{'空值数':>12}{'占比':>10}")
    bad = []
    for name, _ in COLUMNS:
        n = row[name] or 0
        pct = n / total if total else 0
        mark = ""
        if pct > 0.5:
            mark = "  <- 超过一半是空，检查类型或字段含义"
            bad.append(name)
        elif pct > 0:
            mark = "  <- 有空洞（原始数据允许为空，先记录不阻断）"
        print(f"      {name:<24}{n:>12,}{pct:>9.2%}{mark}")

    if bad:
        print(f"\n      [中止] 以下列空值比例异常，可能是类型定错了: {bad}")
        return False
    return True


def write_partitions(spark, df) -> None:
    """动态分区写入。

    INSERT OVERWRITE + dynamic 模式 = **幂等**：
        同一天跑 1 次和跑 10 次，结果是同一份数据，不会翻倍。
    实现上就是「先删掉目标分区目录，再写新的」，而且是在一个事务性的动作里完成。
    """
    from pyspark.sql import functions as F

    staging = df.select(
        *[F.col(name) for name, _ in COLUMNS],
        F.col("dt"),
    )
    # 关键：先按 dt 做 hash 重分区。
    # 不做这一步时，Spark 的 V1 写入器会按「行数均分」切分数据（range 切分），
    # 切分点一旦落在某一天中间，那一天就被拆成两个文件 —— 实测 31 天出了 50 个文件。
    # 按 dt hash 分区后，同一个 dt 的所有行必然落在同一个 task 里，
    # 一个 task 打开一个分区文件连续写，结果就是「一天一个文件」。
    staging = staging.repartition(SHUFFLE_PARTITIONS, F.col("dt"))
    staging.createOrReplaceTempView("stg_ad_log")

    cols = ", ".join(f"`{name}`" for name, _ in COLUMNS)
    spark.sql(
        f"""
        INSERT OVERWRITE TABLE {DB}.{TABLE} PARTITION (dt)
        SELECT {cols}, dt FROM stg_ad_log
        """
    )


def verify(spark) -> None:
    """三件事必须亲眼看到：分区数、每天文件数、分区裁剪是否生效。"""
    from pyspark.sql import functions as F

    # SHOW PARTITIONS 返回的是 "dt=2026-09-21" 这种完整字符串，不是纯日期。
    # 直接拿去当 WHERE 条件会查不到任何数据（而且不报错，返回 0 行）。
    parts = [r[0].split("=", 1)[1] for r in spark.sql(f"SHOW PARTITIONS {DB}.{TABLE}").collect()]
    print(f"\n      分区数: {len(parts)}")
    print(f"      首尾: {parts[0] if parts else '-'}  ...  {parts[-1] if parts else '-'}")

    total = spark.sql(f"SELECT count(*) AS c FROM {DB}.{TABLE}").collect()[0]["c"]
    print(f"      全表行数: {total:,}")

    one_day = spark.sql(
        f"SELECT count(*) AS c FROM {DB}.{TABLE} WHERE dt = '{parts[0]}'"
    ).collect()[0]["c"]
    print(f"      {parts[0]} 单日行数: {one_day:,}")

    # 物理文件：日记账之外，仓库里到底躺了几个文件
    table_dir = os.path.join(WAREHOUSE_DIR, f"{DB}.db", TABLE)
    print(f"\n      物理目录: {table_dir}")
    if os.path.isdir(table_dir):
        dirs = sorted(d for d in os.listdir(table_dir) if d.startswith("dt="))
        sizes = []
        for d in dirs:
            files = [f for f in os.listdir(os.path.join(table_dir, d)) if f.endswith(".parquet")]
            sz = sum(os.path.getsize(os.path.join(table_dir, d, f)) for f in files)
            sizes.append((d, len(files), sz))
        for d, nf, sz in sizes[:5]:
            print(f"        {d}/  {nf} 个文件  {sz / 1024 / 1024:.1f} MB")
        if len(sizes) > 5:
            print(f"        ... 共 {len(sizes)} 个分区目录")
        total_files = sum(x[1] for x in sizes)
        total_mb = sum(x[2] for x in sizes) / 1024 / 1024
        print(f"      合计 {total_files} 个 parquet 文件 / {total_mb:.1f} MB"
              f"（原 TSV 2,589,891,984 B ≈ 2470 MB）")
        print(f"      平均每个文件 {total_mb / max(total_files, 1):.1f} MB")

    print("\n      分区裁剪自检（看 Location 是否只剩一个目录）:")
    spark.sql(f"SELECT count(*) FROM {DB}.{TABLE} WHERE dt = '{parts[0]}'").explain()
    spark.stop()


def main() -> int:
    if not os.path.exists(TSV):
        print(f"找不到 {TSV}，先跑 scripts/download_criteo.py")
        return 1
    os.makedirs(WAREHOUSE_DIR, exist_ok=True)

    t_start = time.time()
    print("[1/6] 启动 Spark 并连接 Metastore ...", flush=True)
    try:
        spark = build_session()
    except Exception as exc:  # noqa: BLE001
        print(f"      失败: {exc}\n      检查 docker compose ps 里 metastore 是否 healthy")
        return 1
    print(f"      pyspark {spark.version} / master=local[2] / driver=2g", flush=True)

    # 自检：连的到底是不是外部 Metastore？
    # 连错了不会报错，只会退回本地 Derby（临时目录），进程退出表就没了。
    # 之前冒烟测试建的 lab 库只存在于外部 Metastore 里 —— 看得见它就说明连对了。
    dbs = sorted(r[0] for r in spark.sql("SHOW DATABASES").collect())
    print(f"      现有库: {dbs}", flush=True)
    if "lab" not in dbs:
        print("      [中止] 看不到 lab 库，说明连的是本地 Derby 而不是外部 Metastore。"
              "检查 docker compose ps 和 9083 端口。")
        spark.stop()
        return 1

    print("[2/6] 建库建表 ...", flush=True)
    ddl = create_table(spark)
    print(f"      库 {DB} / 表 {TABLE} 就绪")
    print("      " + "\n      ".join(ddl.strip().splitlines()[:4]))

    print("[3/6] 读 TSV + 定类型 + 算 dt（不触发计算）...", flush=True)
    df = load_and_transform(spark)
    print(f"      逻辑列数: {len(df.columns)}（22 业务列 + 1 分区列 dt）")

    # 断言而不是相信：列名写错（比如多加了反引号）不会抛异常，
    # 只会在下一步报一个看起来完全无关的 "Cannot safely cast STRING to BIGINT"
    expected = len(COLUMNS) + 1
    if len(df.columns) != expected:
        print(f"      [中止] 期望 {expected} 列，实际 {len(df.columns)} 列")
        print(f"      实际列名: {df.columns}")
        spark.stop()
        return 2

    print("\n[4/6] 写盘前体检：类型转换有没有丢数 ...", flush=True)
    if not check_quality(spark, df):
        spark.stop()
        return 2

    print("\n[5/6] INSERT OVERWRITE 动态分区写盘（这一步最慢，要 shuffle 全量）...", flush=True)
    t0 = time.time()
    write_partitions(spark, df)
    print(f"      写盘完成，用时 {time.time() - t0:.1f}s", flush=True)

    print("\n[6/6] 验证 ...", flush=True)
    verify(spark)

    print(f"\nODS 层构建完成，总用时 {(time.time() - t_start) / 60:.1f} 分钟。")
    print("下一步：查 Metastore 的 PARTITIONS 表，看这 31 行分区登记")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
