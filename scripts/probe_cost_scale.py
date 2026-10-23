"""探针：cost 和 cpo 的量纲。只做单遍聚合，不做 groupBy 重活。

为什么要单独写一个脚本：
    probe_dwd_dist.py 里的第 5 节被前面几节的耗时拖累，最后超时被杀。
    这节只需要**一次扫描**，独立跑 10~20 秒就够。

要验证的三条旧结论（写在 docs/data_contract.md 里）：
    ① cost 均值 0.000995
    ② cpo 均值 0.110283
    ③ 按 campaign 算 cpo/cost 的比值，在 19~55 倍之间浮动
    这三条都是早期探针算的，本轮 DWD 重建后 sum(cost)=4,829.34，
    反推全表均值 = 0.000293，和 ① 差了 3 倍多 —— 必须先确认哪个对。

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/probe_cost_scale.py
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


def build_session() -> SparkSession:
    spark = (
        SparkSession.builder.appName("probe-cost-scale")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", "file:///D:/warehouse")
        .config("spark.hadoop.hive.metastore.uris", "thrift://127.0.0.1:9083")
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def main() -> int:
    spark = build_session()
    t = "dwd.dwd_ad_event"

    print("\n=== 1. 全局量纲（单次扫描）===")
    r = spark.sql(f"""
        SELECT count(*) AS rows_,
               sum(cost) AS total_cost,
               avg(cost) AS avg_cost,
               avg(CASE WHEN is_click = 1 THEN cost END) AS avg_cost_click,
               percentile_approx(cost, 0.5) AS med_cost,
               max(cost) AS max_cost,
               count(DISTINCT cost) AS distinct_cost,
               avg(cpo) AS avg_cpo,
               sum(cpo) AS total_cpo,
               count(DISTINCT cpo) AS distinct_cpo
        FROM {t}
    """).collect()[0]

    rows = r["rows_"]
    print(f"  行数            {rows:,}")
    print(f"  sum(cost)       {r['total_cost']:,.4f}")
    print(f"  avg(cost)       {r['avg_cost']:.6f}   ← 文档旧值 0.000995")
    print(f"  日均花费        {r['total_cost'] / 31:,.4f}（31 天）")
    print(f"  cost 中位数     {r['med_cost']:.6f} / 最大 {r['max_cost']:,.2f}"
          f" / 不同取值 {r['distinct_cost']:,}")
    print(f"  点击行均价      {r['avg_cost_click']:.6f}   ← 这就是 CPC")
    print(f"  avg(cpo)        {r['avg_cpo']:.6f}   ← 文档旧值 0.110283")
    print(f"  sum(cpo)        {r['total_cpo']:,.2f} / 不同取值 {r['distinct_cpo']:,}")
    print(f"  比值 avg_cpo/avg_cost = {r['avg_cpo'] / r['avg_cost']:.1f} 倍"
          f"   ← 文档旧值 110 倍")

    print("\n=== 2. 按 campaign 看 cpo/cost 的比值是否稳定 ===")
    # 一次 groupBy（675 个组，很便宜），组内不做集合运算
    g = spark.sql(f"""
        SELECT campaign_id,
               sum(cost) AS c,
               sum(cpo)  AS p,
               count(*)  AS n
        FROM {t} GROUP BY campaign_id
    """)
    g.createOrReplaceTempView("per_camp")
    s = spark.sql("""
        SELECT count(*) AS camps,
               min(p / c) AS min_ratio,
               max(p / c) AS max_ratio,
               percentile_approx(p / c, 0.5) AS med_ratio,
               stddev(p / c) AS sd_ratio
        FROM per_camp WHERE c > 0
    """).collect()[0]
    print(f"  campaign 数 {s['camps']}")
    print(f"  cpo/cost 比值: 最小 {s['min_ratio']:.1f} / 中位 {s['med_ratio']:.1f}"
          f" / 最大 {s['max_ratio']:.1f} / 标准差 {s['sd_ratio']:.2f}")
    print(f"  → {'比值稳定，可能是单位换算' if s['sd_ratio'] < 1 else '比值不稳定，不是单位换算'}")

    print("\n=== 3. cpo 是不是「转化成本」(cost / conversion)？===")
    # 只有转化行才有 conversion，所以按 campaign 比 sum(cost)/sum(conversion)
    q = spark.sql(f"""
        SELECT campaign_id,
               sum(cost) AS c,
               sum(is_conversion) AS cv,
               avg(cpo) AS p
        FROM {t} GROUP BY campaign_id HAVING sum(is_conversion) > 0
    """)
    q.createOrReplaceTempView("cpa_camp")
    z = spark.sql("""
        SELECT count(*) AS camps,
               max(c / cv) AS max_cpa,
               avg(c / cv) AS avg_cpa,
               avg(p) AS avg_p
        FROM cpa_camp
    """).collect()[0]
    print(f"  有转化的 campaign 数 {z['camps']}")
    print(f"  实际 CPA（cost/转化）: 平均 {z['avg_cpa']:.6f} / 最大 {z['max_cpa']:.6f}")
    print(f"  cpo 平均            : {z['avg_p']:.6f}")
    print(f"  → {'cpo ≈ CPA，可以当转化成本用' if abs(z['avg_p'] - z['avg_cpa']) < z['avg_cpa'] else 'cpo 与 CPA 差一个数量级以上，仍然语义不明'}")

    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
