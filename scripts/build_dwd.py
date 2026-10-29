"""第 2 步：ODS → DWD（清洗 + 去重 + 派生 + 建维表）。

这一步和 ODS 那步的本质区别：
    ODS 是「搬」，不改内容，只换格式；
    DWD 是「判」，每一行都要被判一次「留还是不留」，而且必须能说清为什么。

所以 DWD 真正的产出不是那张表，是**三个数字**：
    ① 每条规则各拦掉多少行（没有这个数字，规则等于没写）
    ② 去重去掉多少行（同理）
    ③ ODS 行数 - 拦掉行数 = DWD 行数（对不上就是有 bug）

跑法：
    # 只看审计、不写盘（先看清楚规则会拦掉什么，再决定要不要改规则）
    ./.venv-spark/Scripts/python.exe -u scripts/build_dwd.py --audit-only

    # 正式构建
    ./.venv-spark/Scripts/python.exe -u scripts/build_dwd.py

    # 构建完顺便把 SQL 打出来方便粘贴
    ./.venv-spark/Scripts/python.exe -u scripts/build_dwd.py --print-sql

    # 改了表结构（增删派生列）后必须重建，否则 CREATE TABLE IF NOT EXISTS
    # 会静默跳过、旧结构的表继续用，INSERT 按位置写就会列错位（不报错）
    ./.venv-spark/Scripts/python.exe -u scripts/build_dwd.py --recreate
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

import dwd_rules  # noqa: E402  同目录下，scripts/ 在 sys.path 里

# ============================================================
# 配置
# ============================================================
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SQL_DWD_EVENT = os.path.join(ROOT, "sql", "dwd", "dwd_ad_event.sql")
SQL_DIM_CAMPAIGN = os.path.join(ROOT, "sql", "dwd", "dim_campaign.sql")

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
WAREHOUSE_DIR = os.environ.get("IAA_WAREHOUSE_DIR", r"D:/warehouse")

SRC_DB, SRC_TABLE = "ods", "ods_ad_log"
DB = "dwd"
T_EVENT = "dwd_ad_event"
T_DIM = "dim_campaign"

# 31 个分区 = 31 天，一天一个文件。理由和 ODS 一样：
# 不为文件大小，为了「按天重跑」这个能力。
SHUFFLE_PARTITIONS = 31


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("build-dwd")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        # 和 ODS 不同：这里必须开 AQE。
        # 因为这一步有窗口函数（按主键重分区）+ 写盘重分区，中间会产生
        # 大量不均匀的小分区，AQE 会按实际数据量合并，省内存也省时间。
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.shuffle.partitions", str(SHUFFLE_PARTITIONS))
        # 维表很小（675 行），广播阈值默认 10 MB 已经够，不用改。
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


# ============================================================
# DDL
# ============================================================
EVENT_COLUMNS: list[tuple[str, str]] = [
    ("event_ts", "BIGINT"),
    ("event_time", "TIMESTAMP"),
    ("event_hour", "INT"),
    ("uid", "BIGINT"),
    ("campaign_id", "BIGINT"),
    ("cat1", "BIGINT"), ("cat2", "BIGINT"), ("cat3", "BIGINT"),
    ("cat4", "BIGINT"), ("cat5", "BIGINT"), ("cat6", "BIGINT"),
    ("cat7", "BIGINT"), ("cat8", "BIGINT"), ("cat9", "BIGINT"),
    ("is_click", "INT"),
    ("is_conversion", "INT"),
    ("is_attributed", "INT"),
    ("prev_click_gap_sec", "DOUBLE"),
    ("is_first_click", "INT"),
    ("conversion_ts", "BIGINT"),
    ("conversion_id", "BIGINT"),
    ("conv_delay_sec", "BIGINT"),
    ("conv_click_nb", "INT"),
    ("conv_click_pos", "INT"),
    ("cost", "DOUBLE"),
    ("cost_per_click", "DOUBLE"),
    ("cpo", "DOUBLE"),
]

DIM_COLUMNS: list[tuple[str, str]] = [
    ("campaign_id", "BIGINT"),
    ("first_dt", "STRING"),
    ("last_dt", "STRING"),
    ("active_days", "BIGINT"),
    ("impressions", "BIGINT"),
    ("clicks", "BIGINT"),
    ("conversions", "BIGINT"),
    ("attributions", "BIGINT"),
    ("total_cost", "DOUBLE"),
    ("reach_users", "BIGINT"),
    ("cat3_cardinality", "BIGINT"),
    ("primary_cat5", "BIGINT"),
    ("cat5_variants", "BIGINT"),
    ("ctr", "DOUBLE"),
    ("cvr", "DOUBLE"),
    ("cpc", "DOUBLE"),
    ("cpa", "DOUBLE"),
]


def read_sql(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到 {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def table_columns(spark, table: str, partition_cols: tuple[str, ...] = ("dt",)) -> list[tuple[str, str]]:
    """从 Metastore 读回表的**真实**列定义（去掉分区列）。"""
    schema = spark.table(f"{DB}.{table}").schema
    return [
        (f.name, f.dataType.simpleString().lower())
        for f in schema.fields
        if f.name not in partition_cols
    ]


def reconcile_table(spark, table: str, columns: list[tuple[str, str]],
                    partition_cols: tuple[str, ...], recreate: bool) -> None:
    """建表；如果表已存在但结构和代码不一致，就报错（或重建）。

    为什么要专门写这个函数（这是本次真实踩到的坑）：
        `CREATE TABLE IF NOT EXISTS` 对已存在的表是**静默空操作**。
        我先跑了一次 --audit-only，那一步就把表建出来了（当时是旧结构）；
        之后我改了派生列（click_seq → conv_click_nb 等），再跑构建时，
        建表语句一声不响地跳过了，表还是旧结构。
        而 INSERT 是**按位置**写列的 —— 于是新顺序的数据被灌进了旧列名的槽位，
        从第 18 列开始全部错位：`sum(is_first_click)` 算出来 13,073,723,398,784
        （实际上它读到的是 conversion_id），而整个作业**没有任何报错**。

        教训：改表结构时，比代码更容易忘记更新的是**已经存在的表**。
        这类错误只能靠「写盘前把 Metastore 里的列和代码里的列比对一遍」拦住。
    """
    want = [(n, t.lower()) for n, t in columns]
    exists = spark.catalog.tableExists(f"{DB}.{table}")

    if exists:
        actual = table_columns(spark, table, partition_cols)
        if actual != want:
            print(f"\n      ⚠️  {DB}.{table} 的表结构与代码不一致")
            print(f"          表里: {[n for n, _ in actual]}")
            print(f"          代码: {[n for n, _ in want]}")
            if not recreate:
                raise RuntimeError(
                    f"{DB}.{table} 结构已过期。重新建表请加 --recreate：\n"
                    f"    ./.venv-spark/Scripts/python.exe -u scripts/build_dwd.py --recreate\n"
                    f"（不加 --recreate 就继续写，会导致数据按位置灌进错列，"
                    f"而且不会报错 —— 所以这里直接中止。）"
                )
            print(f"          --recreate：DROP 表并删除目录后重建")
            spark.sql(f"DROP TABLE IF EXISTS {DB}.{table}")
            # 外部表 DROP 只删 Metastore 登记、不删文件，必须自己清目录，
            # 否则重建后旧 schema 的文件还在，读出来又是错位的数据。
            table_dir = os.path.join(WAREHOUSE_DIR, f"{DB}.db", table)
            if os.path.isdir(table_dir):
                import shutil
                shutil.rmtree(table_dir)
                print(f"          已删除 {table_dir}")

    part_sql = f"PARTITIONED BY ({', '.join(f'{c} STRING' for c in partition_cols)})" if partition_cols else ""
    cols_sql = ",\n    ".join(f"`{n}` {t}" for n, t in columns)
    spark.sql(
        f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.{table} (
            {cols_sql}
        )
        {part_sql}
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/{table}'
        """
    )


def create_tables(spark, recreate: bool = False) -> None:
    spark.sql(f"CREATE DATABASE IF NOT EXISTS {DB}")
    reconcile_table(spark, T_EVENT, EVENT_COLUMNS, ("dt",), recreate)
    reconcile_table(spark, T_DIM, DIM_COLUMNS, (), recreate)


# ============================================================
# 审计：规则到底拦住了什么
# ============================================================
def audit_rules(spark) -> dict:
    """逐条统计每个规则命中多少行，外加「累计去掉」和「去重会去掉」。"""
    from pyspark.sql import functions as F

    print("\n[审计] 逐条统计 9 条规则各拦掉多少行（作用在 ODS 原始列上）")
    src = spark.table(f"{SRC_DB}.{SRC_TABLE}")
    total = src.count()
    print(f"      源表行数: {total:,}")

    aggs = [F.count(F.lit(1)).alias("__n")]
    for r in dwd_rules.RULES:
        # COALESCE(expr, TRUE)：表达式算出 NULL 时按「保留」算。
        # 不这么写的话，NULL 会被 WHEN 当 false，把「不知道该不该留」的行
        # 静默算成「该丢」——这正是数据丢失最常见的隐藏方式。
        keep = F.coalesce(F.expr(r.expr), F.lit(True))
        aggs.append(F.sum(F.when(~keep, 1).otherwise(0)).alias(r.code))

    row = src.agg(*aggs).collect()[0]
    print(f"\n      {'编号':<6}{'命中行数':>12}{'占比':>10}  说明")
    hit_any = 0
    for r in dwd_rules.RULES:
        n = row[r.code] or 0
        if n:
            hit_any += 1
        print(f"      {r.code:<6}{n:>12,}{n / total:>9.4%}  {r.title}")

    # 累计：9 条规则 AND 下来，一共剩多少行（不是各条相加，因为一行可能违规多条）
    where = dwd_rules.cleaning_where_sql(indent="              ")
    after_all = spark.sql(
        f"SELECT count(*) AS c FROM {SRC_DB}.{SRC_TABLE} WHERE\n{where}"
    ).collect()[0]["c"]
    dropped = total - after_all

    print(f"\n      单条命中合计无法直接相加（一行可能违规多条规则）")
    print(f"      9 条规则 AND 之后剩余 {after_all:,} 行，共拦掉 {dropped:,} 行"
          f"（{dropped / total:.4%}）")

    # 去重审计
    key = dwd_rules.DEDUP_KEY_SRC
    print(f"\n[审计] 去重：主键 ({', '.join(key)})")
    g = spark.table(f"{SRC_DB}.{SRC_TABLE}").groupBy(*key).agg(
        F.count(F.lit(1)).alias("n"),
        F.countDistinct("cost", "click", "conversion").alias("variants"),
    )
    dup = g.where("n > 1").agg(
        F.count(F.lit(1)).alias("groups"),
        F.sum("n").alias("rows"),
        F.sum(F.when(F.col("variants") > 1, 1).otherwise(0)).alias("conflict"),
    ).collect()[0]
    groups = dup["groups"] or 0
    rows_in_dup = dup["rows"] or 0
    conflict = dup["conflict"] or 0
    print(f"      重复键组数: {groups:,}")
    print(f"      涉及行数  : {rows_in_dup:,}（去重后剩 {after_all - (rows_in_dup - groups):,}）")
    print(f"      同键但内容不同（真的不是重复）: {conflict:,}")
    if conflict:
        print("      [警告] 存在同键不同内容的行 —— 说明主键定错了，"
              "不是重复而是同一秒发生了两个事件。不要用 row_number 静默丢一条。")

    if hit_any == 0 and groups == 0:
        print("\n      [重要] 所有规则命中 0 行。这**不代表**规则没用，")
        print("             代表这份公开数据集是已经清洗过的，规则一次都没被执行过。")
        print("             规则是否有效必须由 test_dwd_rules.py 的注入测试来证明。")

    # ---------------------------------------------------------------
    # 观察项：这些是「可能不成立、但还没确认为规则」的约束。
    # 先量出来看清楚，再决定要不要升级成规则 —— 顺序不能反，
    # 否则就会出现 R8 那种「凭想象定规则，一次拦掉 33% 数据」的事故。
    # ---------------------------------------------------------------
    obs = spark.sql(f"""
        SELECT
            SUM(CASE WHEN conversion = 1 AND click = 0 THEN 1 ELSE 0 END) AS conv_without_click,
            SUM(CASE WHEN attribution = 1 AND conversion = 0 THEN 1 ELSE 0 END) AS attr_without_conv,
            SUM(CASE WHEN click = 1 AND conversion = 0 THEN 1 ELSE 0 END) AS click_only,
            SUM(CASE WHEN conversion = 1 AND conversion_timestamp < timestamp
                     THEN 1 ELSE 0 END) AS neg_delay
        FROM {SRC_DB}.{SRC_TABLE}
    """).collect()[0]
    print(f"\n[观察] 还没升级成规则的约束（看清楚再决定，别急着写进 WHERE）")
    print(f"      有转化但没点击        : {obs['conv_without_click']:,}")
    print(f"      有归因但没转化        : {obs['attr_without_conv']:,}")
    print(f"      只有点击没有转化      : {obs['click_only']:,}")
    print(f"      转化延迟为负          : {obs['neg_delay']:,}")

    return {
        "total": total,
        "after_cleaning": after_all,
        "dropped": dropped,
        "dup_groups": groups,
        "dup_conflict": conflict,
    }


# ============================================================
# 构建
# ============================================================
def build_event(spark) -> None:
    from pyspark.sql import functions as F

    sql = read_sql(SQL_DWD_EVENT)
    dwd_rules.assert_sql_in_sync(sql)   # SQL 少写一条规则就当场报错

    print("\n[构建] dwd_ad_event（清洗 + 去重 + 派生）")
    df = spark.sql(sql)
    print(f"      逻辑列数: {len(df.columns)}")

    expect = [n for n, _ in EVENT_COLUMNS] + ["dt"]
    if df.columns != expect:
        raise RuntimeError(
            f"SQL 输出列与表结构不一致。\n  表定义: {expect}\n  实际:   {df.columns}"
        )

    # 写盘前重分区：原因和 ODS 完全一样 —— 不重分区时 Spark 按行数均分，
    # 切点落在某天中间就把那天拆成两个文件。
    # 这一步是**第二次 shuffle**，代价不小，但不做就控制不住文件数。
    staging = df.repartition(SHUFFLE_PARTITIONS, F.col("dt"))
    staging.createOrReplaceTempView("stg_dwd_event")

    cols = ", ".join(f"`{n}`" for n, _ in EVENT_COLUMNS)
    spark.sql(
        f"""
        INSERT OVERWRITE TABLE {DB}.{T_EVENT} PARTITION (dt)
        SELECT {cols}, dt FROM stg_dwd_event
        """
    )


def build_dim(spark) -> None:
    sql = read_sql(SQL_DIM_CAMPAIGN)
    print("\n[构建] dim_campaign（campaign 维表，全量覆盖）")
    df = spark.sql(sql)
    if df.columns != [n for n, _ in DIM_COLUMNS]:
        raise RuntimeError(
            f"SQL 输出列与表结构不一致。\n  表定义: {[n for n, _ in DIM_COLUMNS]}\n"
            f"  实际:   {df.columns}"
        )
    df.createOrReplaceTempView("stg_dim_campaign")
    cols = ", ".join(f"`{n}`" for n, _ in DIM_COLUMNS)
    # 维表不分区，直接覆盖整表。为什么不用 INSERT OVERWRITE PARTITION：
    # 它没有分区列，任何一次全量重算都应该整体替换，而不是按天叠加。
    spark.sql(f"INSERT OVERWRITE TABLE {DB}.{T_DIM} SELECT {cols} FROM stg_dim_campaign")


# ============================================================
# 验收
# ============================================================
def verify(spark, audit: dict) -> None:
    from pyspark.sql import functions as F

    print("\n[验收] dwd_ad_event")
    parts = [r[0].split("=", 1)[1]
             for r in spark.sql(f"SHOW PARTITIONS {DB}.{T_EVENT}").collect()]
    total = spark.sql(f"SELECT count(*) AS c FROM {DB}.{T_EVENT}").collect()[0]["c"]
    print(f"      分区数: {len(parts)}（{parts[0]} .. {parts[-1]}）" if parts else "      分区数: 0")
    print(f"      行数  : {total:,}")

    # 对账：这是整条链路唯一的硬约束
    expect = audit["after_cleaning"]
    ok = "✅" if total == expect else "❌"
    print(f"      对账  : ODS {audit['total']:,} - 拦掉 {audit['dropped']:,}"
          f" = {expect:,}，实际写入 {total:,}  {ok}")
    if total != expect:
        raise RuntimeError(f"对账失败：期望 {expect:,} 行，实际 {total:,} 行")

    # 自检：dt 必须等于 event_time 的日期
    # 这条能抓住「时间换算时区错了」这类问题 —— 错了不会报错，
    # 只会让数据整体偏移 8 小时，然后「按小时看流量」的结论全错。
    bad_dt = spark.sql(
        f"SELECT count(*) AS c FROM {DB}.{T_EVENT} "
        f"WHERE dt <> date_format(event_time, 'yyyy-MM-dd')"
    ).collect()[0]["c"]
    print(f"      dt 与 event_time 一致性: {bad_dt} 行不一致 "
          f"{'✅' if bad_dt == 0 else '❌ 时区换算有问题'}")

    # 派生字段抽查
    r = spark.sql(f"""
        SELECT
            count(*) AS rows_,
            sum(is_click) AS clicks,
            sum(is_conversion) AS convs,
            sum(is_attributed) AS attr,
            sum(is_first_click) AS first_clicks,
            avg(prev_click_gap_sec) AS avg_gap,
            avg(conv_delay_sec) AS avg_delay,
            max(conv_delay_sec) AS max_delay,
            sum(cost) AS cost
        FROM {DB}.{T_EVENT}
    """).collect()[0]
    print(f"\n      派生字段抽查:")
    print(f"        有点击 {r['clicks']:,}（{r['clicks'] / r['rows_']:.2%}）"
          f" / 有转化 {r['convs']:,}（{r['convs'] / r['rows_']:.2%}）"
          f" / 有归因 {r['attr']:,}")
    print(f"        首次点击 {r['first_clicks']:,}"
          f"（占点击的 {r['first_clicks'] / max(r['clicks'], 1):.1%}）")
    if r["avg_delay"] is not None:
        print(f"        平均上次点击间隔 {r['avg_gap']:,.1f} 秒"
              f" / 平均转化延迟 {r['avg_delay']:,.1f} 秒 / 最大 {r['max_delay']:,} 秒")
    print(f"        sum(cost) = {r['cost']:,.2f}")

    # ---------------------------------------------------------------
    # 语义自检：派生字段的取值范围本身就是一个「数据有没有错位」的探针。
    # 这次就是靠 sum(is_first_click) 算出 13 万亿才发现列错位的 ——
    # 但当时只是「打印出来看着不对」，脚本仍然跑完并宣布成功。
    # 所以这里把它变成硬断言：只要是 0/1 标志，取值就不能超过 1；
    # 首次点击不可能比总点击还多。这类检查比「列数对不对」有效得多。
    # ---------------------------------------------------------------
    viol = spark.sql(f"""
        SELECT
            MAX(is_first_click) AS max_first,
            SUM(is_first_click) AS sum_first,
            MIN(conv_delay_sec) AS min_delay,
            MAX(conv_delay_sec) AS max_delay2
        FROM {DB}.{T_EVENT}
    """).collect()[0]
    problems = []
    if viol["max_first"] is not None and viol["max_first"] > 1:
        problems.append(f"is_first_click 出现 {viol['max_first']}（应当只有 0/1）")
    if viol["sum_first"] is not None and viol["sum_first"] > r["clicks"]:
        problems.append(
            f"首次点击 {viol['sum_first']:,} 大于总点击 {r['clicks']:,}（不可能）"
        )
    if viol["min_delay"] is not None and viol["min_delay"] < 0:
        problems.append(f"conv_delay_sec 最小值 {viol['min_delay']}（不应为负）")
    if problems:
        raise RuntimeError(
            "语义自检失败，最可能的原因是「表结构和代码不一致导致列错位」：\n  - "
            + "\n  - ".join(problems)
            + "\n  试试加 --recreate 重建表。"
        )
    print(f"        语义自检: 通过（首次点击 {viol['sum_first']:,} ≤ 总点击 {r['clicks']:,}，"
          f"转化延迟 >= 0）")

    print(f"\n[验收] {DB}.{T_DIM}")
    d = spark.sql(f"""
        SELECT count(*) AS camps,
               sum(case when cat5_variants > 1 then 1 else 0 end) AS drift,
               max(cat5_variants) AS max_variants,
               sum(impressions) AS imp,
               sum(clicks) AS clk,
               sum(conversions) AS cnv
        FROM {DB}.{T_DIM}
    """).collect()[0]
    print(f"      campaign 数: {d['camps']:,}")
    print(f"      cat5 有漂移的: {d['drift']:,} 个（最多 {d['max_variants']} 种取值）")
    print(f"      累计曝光 {d['imp']:,} / 点击 {d['clk']:,} / 转化 {d['cnv']:,}")
    if d["imp"] != total:
        print(f"      ❌ 维表曝光合计 {d['imp']:,} 与明细行数 {total:,} 不一致")
    else:
        print(f"      ✅ 维表曝光合计与明细行数一致")

    # 物理文件
    table_dir = os.path.join(WAREHOUSE_DIR, f"{DB}.db", T_EVENT)
    if os.path.isdir(table_dir):
        dirs = sorted(x for x in os.listdir(table_dir) if x.startswith("dt="))
        files = sum(len([f for f in os.listdir(os.path.join(table_dir, d))
                         if f.endswith(".parquet")]) for d in dirs)
        size = sum(os.path.getsize(os.path.join(table_dir, d, f))
                   for d in dirs
                   for f in os.listdir(os.path.join(table_dir, d)) if f.endswith(".parquet"))
        print(f"\n      物理: {len(dirs)} 个分区目录 / {files} 个 parquet 文件"
              f" / {size / 1024 / 1024:.1f} MB"
              f"（平均 {size / 1024 / 1024 / max(files, 1):.1f} MB/文件）")

    print("\n      分区裁剪自检:")
    spark.sql(f"SELECT count(*) FROM {DB}.{T_EVENT} WHERE dt = '{parts[0]}'").explain()


def main() -> int:
    audit_only = "--audit-only" in sys.argv
    print_sql = "--print-sql" in sys.argv
    recreate = "--recreate" in sys.argv

    t0 = time.time()
    spark = build_session()
    print(f"pyspark {spark.version} / master=local[2] / driver=2g", flush=True)

    dbs = sorted(r[0] for r in spark.sql("SHOW DATABASES").collect())
    print(f"现有库: {dbs}")
    if "ods" not in dbs:
        print("[中止] 看不到 ods 库，先跑 scripts/build_ods.py")
        return 1

    create_tables(spark, recreate=recreate)
    audit = audit_rules(spark)

    if print_sql:
        print("\n" + "=" * 70)
        print(read_sql(SQL_DWD_EVENT))
        print("=" * 70)

    if audit_only:
        print("\n--audit-only：未写盘。确认规则命中数合理后去掉该参数重跑。")
        spark.stop()
        return 0

    t1 = time.time()
    build_event(spark)
    print(f"      用时 {time.time() - t1:.1f}s")
    t1 = time.time()
    build_dim(spark)
    print(f"      用时 {time.time() - t1:.1f}s")

    verify(spark, audit)

    print(f"\nDWD 层构建完成，总用时 {(time.time() - t0) / 60:.1f} 分钟。")
    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
