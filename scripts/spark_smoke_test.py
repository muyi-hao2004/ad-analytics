"""Spark + Hive Metastore 冒烟测试。

跑通这个脚本，说明整条链路是活的：
    Spark  →  Metastore（9083）  →  PostgreSQL 里的 TBLS / PARTITIONS
           →  本地 warehouse 目录（分区就是目录）

用法（必须用装了 pyspark 的那个 Python）：
    ./.venv-spark/Scripts/python.exe scripts/spark_smoke_test.py

它只写一个测试库 lab.smoke_part，不碰你的正式数据。
"""

from __future__ import annotations

import os
import sys

# ---- Windows 上跑 Spark 必须先解决的两件事（都在 import pyspark 之前）----
# 1. HADOOP_HOME：Windows 版 Hadoop 的 Shell 类会去找 winutils.exe，找不到直接抛
#    FileNotFoundException: HADOOP_HOME and hadoop.home.dir are unset
_HADOOP_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop")
)
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")

# 2. PYSPARK_PYTHON：driver 和 worker 必须是同一个解释器。
#    不设的话 worker 会用 PATH 里的 python（可能是另一个版本），版本不一致会静默崩。
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
WAREHOUSE_DIR = os.environ.get("IAA_WAREHOUSE_DIR", r"D:/warehouse")


def build_session():
    """建一个连外部 Metastore 的 SparkSession。"""
    from pyspark.sql import SparkSession

    return (
        SparkSession.builder.appName("lab-smoke-test")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .enableHiveSupport()
        .getOrCreate()
    )


def show_tree(root: str, max_entries: int = 20) -> None:
    """打印目录树，让人亲眼看到「分区就是子目录」。"""
    print(f"\n仓库目录: {root}")
    if not os.path.exists(root):
        print("  （目录还不存在）")
        return
    shown = 0
    for current, dirs, files in os.walk(root):
        depth = current[len(root) :].count(os.sep)
        if depth > 4:
            dirs[:] = []
            continue
        indent = "  " * (depth + 1)
        print(f"{indent}{os.path.basename(current) or current}/")
        for f in files[:3]:
            size = os.path.getsize(os.path.join(current, f))
            print(f"{indent}  {f}  ({size} B)")
            shown += 1
            if shown > max_entries:
                print(f"{indent}  ...")
                return


def main() -> int:
    os.makedirs(WAREHOUSE_DIR, exist_ok=True)

    print("[1/6] 启动 SparkSession 并连接 Metastore ...")
    try:
        spark = build_session()
    except Exception as exc:  # noqa: BLE001
        print(f"      失败: {exc}")
        print("      检查：docker compose ps 里 metastore 是不是 healthy")
        return 1
    print(f"      驱动版本: {spark.version}")

    print("[2/6] SHOW DATABASES ...")
    dbs = [r.namespace for r in spark.sql("SHOW DATABASES").collect()]
    print(f"      现有库: {dbs}")

    print("[3/6] 建测试库和分区表 ...")
    spark.sql("CREATE DATABASE IF NOT EXISTS lab")
    spark.sql("DROP TABLE IF EXISTS lab.smoke_part")
    spark.sql(
        """
        CREATE TABLE lab.smoke_part (
            channel  STRING,
            clicks   INT
        )
        PARTITIONED BY (dt STRING)
        STORED AS PARQUET
        """
    )
    print("      已建 lab.smoke_part（按 dt 分区）")

    print("[4/6] 写入两个分区 ...")
    spark.sql(
        """
        INSERT INTO lab.smoke_part PARTITION (dt='2026-10-20')
        VALUES ('fb', 120), ('tt', 80)
        """
    )
    spark.sql(
        """
        INSERT INTO lab.smoke_part PARTITION (dt='2026-10-21')
        VALUES ('fb', 150), ('tt', 95)
        """
    )

    print("[5/6] SHOW PARTITIONS + 查询 ...")
    parts = [r.partition for r in spark.sql("SHOW PARTITIONS lab.smoke_part").collect()]
    print(f"      分区: {parts}")
    rows = spark.sql(
        "SELECT dt, channel, clicks FROM lab.smoke_part ORDER BY dt, channel"
    ).collect()
    for r in rows:
        print(f"      {r.dt}  {r.channel}  {r.clicks}")

    print("[6/6] 检查元数据与物理路径 ...")
    tbl = spark.sql("DESCRIBE EXTENDED lab.smoke_part").collect()
    for r in tbl:
        if r.col_name in ("Location", "Table Type", "Provider"):
            print(f"      {r.col_name}: {r.data_type}")
    show_tree(os.path.join(WAREHOUSE_DIR, "lab.db"))

    spark.stop()
    print("\n冒烟测试通过。这条链路现在是活的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
