"""分区裁剪（partition pruning）的实测演示。

回答一个问题：WHERE dt='2026-10-20' 到底是「读完再筛」还是「压根不读另一个目录」？

判断方法：看 Spark 的物理计划里 partitionFilters 有没有被下推到文件扫描层，
以及实际扫描了多少文件。这也是「分区为什么能省钱」的物理证据。

用法：
    ./.venv-spark/Scripts/python.exe scripts/demo_partition_pruning.py
"""

from __future__ import annotations

import os
import sys

# ---- 与 spark_smoke_test.py 相同的前置修复，缺一个都会崩 ----
_HADOOP_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop")
)
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")


def main() -> int:
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("demo-pruning")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    print("=" * 70)
    print("场景 A：带分区过滤  WHERE dt = '2026-10-20'")
    print("=" * 70)
    spark.sql("SELECT * FROM lab.smoke_part WHERE dt = '2026-10-20'").explain(True)

    print()
    print("=" * 70)
    print("场景 B：不带分区过滤  全表")
    print("=" * 70)
    spark.sql("SELECT * FROM lab.smoke_part").explain(True)

    print()
    print("=" * 70)
    print("看什么：FileScan 那一行里的 PartitionFilters")
    print("  A 场景 → PartitionFilters: [isnotnull(dt), (dt = 2026-10-20)]")
    print("           说明过滤条件被下推到「选目录」这一层，另一个目录压根不打开")
    print("  B 场景 → PartitionFilters: []")
    print("           两个目录全读")
    print("=" * 70)

    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
