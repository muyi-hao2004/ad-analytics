"""抽 5 行 DWD 数据看一眼，并实测分区裁剪。

只读一个分区（dt=2026-09-21），不扫全表，几秒钟就有结果。
作用：确认「建出来的表是人能看懂的样子」，而不是只有 count(*) 对得上。
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

from pyspark.sql import SparkSession  # noqa: E402


def main() -> int:
    spark = (
        SparkSession.builder.appName("peek-dwd")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse"))
        .config("spark.hadoop.hive.metastore.uris", "thrift://127.0.0.1:9083")
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    print("\n=== 表结构 ===")
    for f in spark.table("dwd.dwd_ad_event").schema.fields:
        print(f"  {f.name:<20} {f.dataType.simpleString():<10}")

    print("\n=== dt=2026-09-21 抽 5 行（有转化的优先，方便看转化列）===")
    # 不用 toPandas()：.venv-spark 是**只装 Spark 的干净环境**，没有 pandas。
    # 为了打印 5 行去装 pandas 不划算，collect() + 手工格式化就够了。
    rows = spark.sql("""
        SELECT event_time, dt, uid, campaign_id, cat3,
               is_click, is_conversion, is_attributed,
               conv_delay_sec, conv_click_nb, is_first_click, cost
        FROM dwd.dwd_ad_event
        WHERE dt = '2026-09-21' AND is_conversion = 1
        LIMIT 5
    """).collect()
    hdr = ("event_time", "dt", "uid", "campaign", "cat3", "clk", "conv", "attr",
           "延迟秒", "转化前点击", "首点", "cost")
    print("  " + " | ".join(f"{h}" for h in hdr))
    print("  " + "-" * 110)
    for x in rows:
        print(f"  {str(x['event_time'])[:19]} | {x['dt']} | {x['uid']} | {x['campaign_id']} "
              f"| {x['cat3']} | {x['is_click']} | {x['is_conversion']} | {x['is_attributed']} "
              f"| {x['conv_delay_sec']} | {x['conv_click_nb']} | {x['is_first_click']} "
              f"| {x['cost']:.6f}")

    print("\n=== 分区裁剪（只应出现 1 个 paths）===")
    plan = spark.sql(
        "SELECT count(*) FROM dwd.dwd_ad_event WHERE dt = '2026-09-21'"
    )._jdf.queryExecution().executedPlan().toString()
    for line in plan.splitlines():
        if "InMemoryFileIndex" in line or "PartitionFilters" in line:
            print("  " + line.strip())

    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
