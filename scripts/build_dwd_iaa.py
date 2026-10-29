"""第 3 步：DWD 层清洗 —— ODS 脏数据 → 干净的用户日活明细。

和 ODS 的区别只有两件事：
    ① 丢掉 5 类脏数据（R1~R5）
    ② 去掉 install_date 列（和 dt、days_since_install 信息等价）

这一层的核心不是「洗数据」，是**证明自己洗干净了**：
    - 每条规则拦掉多少行，逐条打印出来
    - 输入行数 − 拦截数 − 重复数 = 输出行数，必须严格相等
    - 做不到对账，就等于没洗

跑法：
    全部 31 天：   MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/build_dwd_iaa.py --all
    只重跑一天：   MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/build_dwd_iaa.py --dt 2026-10-15
    只看审计不写： MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/build_dwd_iaa.py --dt 2026-10-15 --audit-only
"""

from __future__ import annotations

import argparse
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
WAREHOUSE_DIR = os.environ.get("IAA_WAREHOUSE_DIR", r"D:/warehouse")

SRC_DB, SRC_TABLE = "lab", "ods_iaa_user_daily"
DB, TABLE = "lab", "dwd_user_daily"
TABLE_LOCATION = f"{WAREHOUSE}/{DB}.db/{TABLE}"
SQL_FILE = os.path.join(os.path.dirname(__file__), "..", "sql", "dwd", "dwd_iaa_user_daily.sql")

COLUMNS: list[tuple[str, str]] = [
    ("uid", "string"),
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

# 规则唯一定义处。SQL 文件和这里必须完全一致，否则启动即中止。
# 顺序有意义：规则是级联的（后面的规则只在前面都通过的集合上统计）。
RULES: list[tuple[str, str, str]] = [
    ("R1", "主键为空", "uid IS NULL"),
    ("R2", "活跃日早于装机日", "days_since_install < 0"),
    ("R3", "收入为负", "ad_revenue < 0"),
    ("R4", "展示为负或零次打开", "ad_impr < 0 OR session_cnt < 1"),
    ("R5", "激励视频数超过总展示", "rewarded_views < 0 OR rewarded_views > ad_impr"),
]


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("build-dwd-iaa")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.hadoop.hive.exec.dynamic.partition", "true")
        .config("spark.hadoop.hive.exec.dynamic.partition.mode", "nonstrict")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.adaptive.enabled", "true")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def assert_sql_in_sync() -> None:
    """确认 SQL 文件里确实写了这 5 条规则。

    为什么要在启动时检查：
        规则变了但 SQL 忘了改，是这类项目最常见的「悄悄不一致」——
        代码里说拦了 5 条，实际只拦了 4 条，审计对账永远差一截，
        而且报错信息指向的是「数据有问题」而不是「代码有问题」。
    """
    with open(SQL_FILE, encoding="utf-8") as f:
        sql = f.read()
    missing = [f"{rid}" for rid, _, _ in RULES if f"-- {rid} " not in sql]
    if missing:
        raise SystemExit(
            f"[中止] {SQL_FILE} 里找不到这些规则的编号: {missing}\n"
            f"       SQL 和 build_dwd_iaa.py 的 RULES 已经不同步了。"
        )


def create_table(spark) -> None:
    spark.sql(f"CREATE DATABASE IF NOT EXISTS {DB}")
    cols_sql = ",\n    ".join(f"`{n}` {t}" for n, t in COLUMNS)
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


def check_schema_drift(spark) -> None:
    """比对 Metastore 里的真实列 vs 代码里的列。

    为什么要这一步：
        CREATE TABLE IF NOT EXISTS 对已存在的表是**静默空操作**。
        改了代码里的列顺序，表结构却还是旧的，INSERT 按位置写 →
        数据整体错位一列，而且零报错。这个坑原来在 Criteo 项目上踩过一次
        （sum(is_first_click) 算出了 13 万亿）。
    """
    actual = [
        (f.name.lower(), f.dataType.simpleString())
        for f in spark.table(f"{DB}.{TABLE}").schema.fields
        if f.name.lower() != "dt"
    ]
    expected = [(n, t) for n, t in COLUMNS]
    if actual != expected:
        print("      [中止] 表结构漂移！Metastore 里的列和代码不一致：")
        for i, (a, e) in enumerate(zip(actual, expected)):
            mark = "" if a == e else "   <-- 不一致"
            print(f"        [{i}] 表={a}  代码={e}{mark}")
        raise SystemExit(2)


def audit(spark, dt: str) -> dict:
    """逐条统计每类脏数据拦掉多少行（级联口径）。

    每条规则前面都加上「前面所有规则都不命中」的前缀，模拟 SQL 里 AND 的短路语义：
    一条行只可能被**第一条命中它的规则**拦掉，不会被重复计数。
    这样「各规则之和」才等于「总拦截数」，可加性保住了才能对账。
    """
    exprs = []
    for i, (rid, _, cond) in enumerate(RULES):
        prefix = " AND ".join(f"NOT ({RULES[j][2]})" for j in range(i))
        expr = f"{prefix} AND ({cond})" if prefix else f"({cond})"
        exprs.append(f"sum(CASE WHEN {expr} THEN 1 ELSE 0 END) AS {rid}")

    row = spark.sql(
        f"""
        SELECT count(*) AS total,
               {', '.join(exprs)}
        FROM {SRC_DB}.{SRC_TABLE} WHERE dt = '{dt}'
        """
    ).collect()[0]
    a = {k: (row[k] or 0) for k in row.asDict()}
    a["bad"] = sum(a[rid] for rid, _, _ in RULES)
    return a


def build_one(spark, dt: str, audit_only: bool = False) -> None:
    a = audit(spark, dt)
    total = a["total"]
    kept_raw = total - a["bad"]

    # 去重前后各数一次，才能把「被哪条规则拦」和「被去重拦」分开。
    # 注意这个 WHERE 必须和 SQL 文件里的 5 条规则**逐字对应** ——
    # 少写一条，去重数就会把这部分行也算进来，对账立刻差一截。
    dup = spark.sql(
        f"""
        SELECT count(*) - count(DISTINCT uid) AS d
        FROM {SRC_DB}.{SRC_TABLE}
        WHERE dt = '{dt}' AND uid IS NOT NULL
          AND days_since_install >= 0 AND ad_revenue >= 0
          AND ad_impr >= 0 AND session_cnt >= 1
          AND rewarded_views >= 0 AND rewarded_views <= ad_impr
        """
    ).collect()[0]["d"] or 0
    expected = kept_raw - dup

    rule_desc = "  ".join(f"{rid}:{a[rid]:,}" for rid, _, _ in RULES)
    print(f"      {dt}  输入 {total:>9,}  {rule_desc}  去重-{dup:,}  →  {expected:>9,} 行")

    if audit_only:
        return expected

    # 写入：SQL 文件只负责「选什么」，这里负责「怎么落」
    with open(SQL_FILE, encoding="utf-8") as f:
        body = f.read()
    # 关键：把 SQL 里写死的表名替换成 **按当前 dt 过滤过的临时视图**。
    # 不替换的话，下面那条 SELECT 会扫全部 31 个分区（3600 万行），
    # 而我们要的只是其中一天（116 万行）—— 这是分区裁剪在调度层的体现。
    # SQL 文件本身仍是通用模板（可以单独贴进 spark-sql 跑全量），dt 由这里注入。
    sql = body.replace(f"{SRC_DB}.{SRC_TABLE}", "_src")
    spark.sql(
        f"CREATE OR REPLACE TEMPORARY VIEW _src AS "
        f"SELECT * FROM {SRC_DB}.{SRC_TABLE} WHERE dt = '{dt}'"
    )

    cleaned = spark.sql(sql)
    cleaned.createOrReplaceTempView("_dwd_day")
    cols = ", ".join(f"`{n}`" for n, _ in COLUMNS)
    spark.sql(
        f"""
        INSERT OVERWRITE TABLE {DB}.{TABLE} PARTITION (dt='{dt}')
        SELECT {cols} FROM _dwd_day WHERE dt = '{dt}'
        """
    )
    n = spark.sql(f"SELECT count(*) c FROM {DB}.{TABLE} WHERE dt='{dt}'").collect()[0]["c"]
    if n != expected:
        raise SystemExit(
            f"      [中止] {dt} 对账失败：审计算出应写入 {expected:,} 行，实际写入 {n:,} 行，"
            f"差 {n - expected:+,} 行"
        )
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dt", help="只处理某一天，如 2026-10-15")
    ap.add_argument("--all", action="store_true", help="处理 ODS 里所有分区")
    ap.add_argument("--audit-only", action="store_true", help="只统计不写盘")
    args = ap.parse_args()
    if not args.dt and not args.all:
        ap.error("必须指定 --dt 或 --all")

    t_start = time.time()
    print("[1/5] 启动 Spark ...", flush=True)
    spark = build_session()
    print(f"      pyspark {spark.version} / local[2] / driver=2g")

    print("[2/5] 检查 SQL 与规则定义是否同步 ...", flush=True)
    assert_sql_in_sync()
    print(f"      {len(RULES)} 条规则在 SQL 里全部找到")

    print("[3/5] 建表 + 检查 schema 漂移 ...", flush=True)
    create_table(spark)
    check_schema_drift(spark)
    print(f"      {DB}.{TABLE} 就绪，{len(COLUMNS)} 列 + dt 分区")

    # 确定要处理的分区，按日期升序（保证日志可读）
    ods_parts = sorted(
        r[0].split("=", 1)[1]
        for r in spark.sql(f"SHOW PARTITIONS {SRC_DB}.{SRC_TABLE}").collect()
    )
    targets = [args.dt] if args.dt else ods_parts
    if args.dt and args.dt not in ods_parts:
        print(f"      [中止] ODS 里没有 dt={args.dt} 这个分区")
        return 2

    print(f"[4/5] 清洗 {len(targets)} 个分区 ...", flush=True)
    print(f"      规则: " + " | ".join(f"{rid}={desc}" for rid, desc, _ in RULES))
    total_in = total_out = 0
    t0 = time.time()
    for dt in targets:
        total_in += build_one(spark, dt, args.audit_only)
    print(f"      用时 {(time.time() - t0) / 60:.2f} 分钟", flush=True)

    print("[5/5] 全表验证 ...", flush=True)
    if not args.audit_only:
        verify(spark)

    print(f"\nDWD 层构建完成，总用时 {(time.time() - t_start) / 60:.1f} 分钟。")
    spark.stop()
    return 0


def verify(spark) -> None:
    t = f"{DB}.{TABLE}"
    ods_n = spark.sql(f"SELECT count(*) c FROM {SRC_DB}.{SRC_TABLE}").collect()[0]["c"]
    dwd_n = spark.sql(f"SELECT count(*) c FROM {t}").collect()[0]["c"]
    print(f"      ODS {ods_n:,} 行  →  DWD {dwd_n:,} 行  （拦掉 {ods_n - dwd_n:,} 行，"
          f"占 {(ods_n - dwd_n) / ods_n:.2%}）")

    parts = sorted(r[0].split("=", 1)[1] for r in spark.sql(f"SHOW PARTITIONS {t}").collect())
    print(f"      分区数: {len(parts)}   首尾: {parts[0]} ... {parts[-1]}")

    # A1 主键唯一
    dup = spark.sql(
        f"SELECT count(*) c FROM (SELECT uid, dt FROM {t} GROUP BY uid, dt HAVING count(*) > 1) x"
    ).collect()[0]["c"]
    print(f"      [A1] 主键 (uid,dt) 重复组数: {dup}  {'OK' if dup == 0 else '<- 失败'}")

    # A2 dt 与 days_since_install 自洽
    bad = spark.sql(
        f"SELECT count(*) c FROM {t} WHERE date_sub(dt, days_since_install) > dt"
    ).collect()[0]["c"]
    print(f"      [A2] install_date 晚于 dt 的行: {bad}  {'OK' if bad == 0 else '<- 失败'}")

    # A3 每个 uid 的 D0 行必须有且只有一行
    bad = spark.sql(
        f"""
        SELECT count(*) c FROM (
          SELECT uid FROM {t} WHERE days_since_install = 0 GROUP BY uid HAVING count(*) <> 1
        ) x
        """
    ).collect()[0]["c"]
    print(f"      [A3] D0 行不唯一的用户数: {bad}  {'OK' if bad == 0 else '<- 失败'}")

    # A4 install_cost 只在 D0 有值
    bad = spark.sql(
        f"SELECT count(*) c FROM {t} WHERE days_since_install > 0 AND install_cost IS NOT NULL"
    ).collect()[0]["c"]
    print(f"      [A4] D>0 却有 install_cost 的行: {bad}  {'OK' if bad == 0 else '<- 失败'}")

    # A5 脏数据应已全部清零
    bad = spark.sql(
        f"""
        SELECT count(*) c FROM {t}
        WHERE uid IS NULL OR days_since_install < 0 OR ad_revenue < 0
           OR ad_impr < 0 OR session_cnt < 1 OR rewarded_views > ad_impr
        """
    ).collect()[0]["c"]
    print(f"      [A5] 残留脏数据行: {bad}  {'OK' if bad == 0 else '<- 失败'}")

    # A6 留存曲线抽查（验证寿命抽样是否符合设计）
    if len(parts) < 10:
        print(f"\n      留存曲线抽查：分区数 {len(parts)} 不足 10，跳过"
              f"（单天分区里没有 2026-10-01 的 cohort，分母会是 0）")
        return
    print(f"\n      留存曲线抽查（分母 = 2026-10-01 装机用户，观察窗口完整）:")
    row = spark.sql(
        f"""
        SELECT
          count(DISTINCT CASE WHEN days_since_install = 0 THEN uid END) AS d0,
          count(DISTINCT CASE WHEN days_since_install = 1 THEN uid END) AS d1,
          count(DISTINCT CASE WHEN days_since_install = 3 THEN uid END) AS d3,
          count(DISTINCT CASE WHEN days_since_install = 7 THEN uid END) AS d7,
          count(DISTINCT CASE WHEN days_since_install = 14 THEN uid END) AS d14,
          count(DISTINCT CASE WHEN days_since_install = 30 THEN uid END) AS d30
        FROM {t} WHERE date_sub(dt, days_since_install) <= date'2026-10-01'
        """
    ).collect()[0]
    d0 = row["d0"] or 1
    for d, design in [("d1", 0.350), ("d3", 0.192), ("d7", 0.120), ("d14", 0.081), ("d30", 0.053)]:
        print(f"        D{d[1:]:<3} 实测 {(row[d] or 0) / d0:>7.2%}   设计 {design:>7.2%}")


if __name__ == "__main__":
    raise SystemExit(main())
