"""探针：DWD 派生字段的**分布**，不只看平均值。

为什么必须做这一步：
    build_dwd.py 的验收只打印了 avg(conv_delay_sec) = 616,728 秒（7.1 天）。
    但「平均值」是所有统计量里最会骗人的一个 ——
    一堆 0 和一堆 30 天混在一起，平均值同样是 15 天，
    而这两种分布对业务的含义完全相反：
        · 如果大部分转化发生在几小时内（真实的广告行为），要按小时调预算；
        · 如果转化均匀铺满 30 天（多半是生成出来的假数据），
          「归因窗口」这个分析根本不该做。
    所以这里看分位数和按天分布，不看平均。

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/probe_dwd_dist.py
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

DAY = 86400


def build_session() -> SparkSession:
    spark = (
        SparkSession.builder.appName("probe-dwd-dist")
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


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)


def main() -> int:
    spark = build_session()
    print(f"pyspark {spark.version}", flush=True)
    t = "dwd.dwd_ad_event"

    # ---------------------------------------------------------------
    # 1. 转化延迟的分位数
    # ---------------------------------------------------------------
    section("1. 转化延迟 conv_delay_sec 的分位数（只统计有转化的行）")
    r = spark.sql(f"""
        SELECT
            count(*) AS n,
            sum(CASE WHEN conv_delay_sec = 0 THEN 1 ELSE 0 END) AS zero,
            percentile_approx(conv_delay_sec, 0.10) AS p10,
            percentile_approx(conv_delay_sec, 0.25) AS p25,
            percentile_approx(conv_delay_sec, 0.50) AS p50,
            percentile_approx(conv_delay_sec, 0.75) AS p75,
            percentile_approx(conv_delay_sec, 0.90) AS p90,
            percentile_approx(conv_delay_sec, 0.99) AS p99,
            max(conv_delay_sec) AS mx
        FROM {t} WHERE conv_delay_sec IS NOT NULL
    """).collect()[0]
    n = r["n"]
    print(f"  样本 {n:,} 行（占全表 {n / 16468027:.2%}）")
    for k, label in [("p10", "10%"), ("p25", "25%"), ("p50", "中位数"),
                     ("p75", "75%"), ("p90", "90%"), ("p99", "99%"), ("mx", "最大")]:
        v = r[k]
        print(f"    {label:>6}: {v:>10,} 秒 = {v / 3600:>9.2f} 小时 = {v / DAY:>6.2f} 天")
    print(f"    延迟恰好为 0 的: {r['zero']:,}（{r['zero'] / n:.2%}）")

    # 按天分桶看形状：如果每个桶差不多高，就是均匀分布（假数据特征）
    section("2. 转化延迟按天分桶（看形状，不看均值）")
    rows = spark.sql(f"""
        SELECT
            CAST(conv_delay_sec / {DAY} AS INT) AS day_bucket,
            count(*) AS n
        FROM {t} WHERE conv_delay_sec IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).collect()
    total = sum(x["n"] for x in rows)
    top = max(x["n"] for x in rows)
    for x in rows:
        bar = "#" * int(40 * x["n"] / top)
        print(f"    {x['day_bucket']:>3} 天 | {x['n']:>8,} {x['n'] / total:>6.2%} {bar}")

    # ---------------------------------------------------------------
    # 3. 上次点击间隔
    # ---------------------------------------------------------------
    section("3. 上次点击间隔 prev_click_gap_sec 的分位数（只统计有值的行）")
    g = spark.sql(f"""
        SELECT
            count(*) AS n,
            percentile_approx(prev_click_gap_sec, 0.25) AS p25,
            percentile_approx(prev_click_gap_sec, 0.50) AS p50,
            percentile_approx(prev_click_gap_sec, 0.75) AS p75,
            percentile_approx(prev_click_gap_sec, 0.90) AS p90,
            max(prev_click_gap_sec) AS mx
        FROM {t} WHERE prev_click_gap_sec IS NOT NULL
    """).collect()[0]
    print(f"  样本 {g['n']:,} 行（首次点击没有该值）")
    for k, label in [("p25", "25%"), ("p50", "中位数"), ("p75", "75%"),
                     ("p90", "90%"), ("mx", "最大")]:
        v = g[k]
        print(f"    {label:>6}: {v:>10,.0f} 秒 = {v / DAY:>7.2f} 天")

    # ---------------------------------------------------------------
    # 4. 一个用户最多看到几个广告？（判断 uid 的粒度）
    # ---------------------------------------------------------------
    # 这一节原本写的是 groupBy(uid).agg(count(*), count(DISTINCT campaign_id))，
    # 在 2 核 / 2G 堆上跑了 14 分钟还在溢写磁盘 —— 因为它要做**两层** shuffle：
    # 先按 uid 分组，组内再维护一个 campaign 集合。6,142,256 个组，
    # 每组一个集合，内存根本放不下，只能反复落盘。
    #
    # 换成三个便宜的写法，信息量几乎一样：
    #   ① approx_count_distinct：HyperLogLog 估算，误差 <1%，只扫一遍
    #   ② TOP 10 最活跃 uid：一次 shuffle + 排序，不做组内集合
    #   ③ 用一个「有上限」的近似判断代替「精确最大 campaign 数」
    # 教训：探查阶段的目标是**排除假说**，不是出精确报表。
    # 为了一个「最多接触几个 campaign」的数字等 14 分钟，是拿错了性价比。
    section("4. uid 的粒度（低成本近似）")
    u = spark.sql(f"""
        SELECT approx_count_distinct(uid, 0.01) AS uids,
               count(*) AS rows_
        FROM {t}
    """).collect()[0]
    print(f"  不同 uid（HLL 估算，误差<1%）: {u['uids']:,} / 全表 {u['rows_']:,} 行")
    print(f"  平均每 uid {u['rows_'] / u['uids']:.1f} 行")

    top = spark.sql(f"""
        SELECT uid, count(*) AS c
        FROM {t} GROUP BY uid ORDER BY c DESC LIMIT 5
    """).collect()
    print(f"  最活跃的 5 个 uid（行数 = 看到这个广告的次数）:")
    for x in top:
        print(f"    uid={x['uid']:<12} {x['c']:>8,} 行")
    print(f"  → 第一版这里我写的是「按 uid 分区一定会倾斜」，实测**反了**：")
    print(f"     最活跃 uid 也只占全表 {top[0]['c'] / u['rows_']:.4%}，绝对量不到 900 行。")
    print(f"     结论：campaign 和 uid **两个维度都不倾斜**，这份数据太均匀。")
    print(f"     想练调优必须自己造倾斜（用 cat3），否则等于没调。")

    # ---------------------------------------------------------------
    # 5. cost 的量纲：是整笔还是单次？
    # ---------------------------------------------------------------
    section("5. cost 的量纲（决定 CPA 能不能和现实对照）")
    c = spark.sql(f"""
        SELECT sum(cost) AS total,
               avg(cost) AS avg_all,
               avg(CASE WHEN is_click = 1 THEN cost END) AS avg_click,
               percentile_approx(cost, 0.5) AS med,
               max(cost) AS mx,
               count(DISTINCT cost) AS distinct_cost
        FROM {t}
    """).collect()[0]
    print(f"  合计 {c['total']:,.2f} / 曝光均价 {c['avg_all']:.6f}"
          f" / 点击均价 {c['avg_click']:.6f}")
    print(f"  中位数 {c['med']:.6f} / 最大 {c['mx']:,.2f} / 不同取值 {c['distinct_cost']:,} 种")
    print(f"  每日花费约 {c['total'] / 31:,.2f}")

    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
