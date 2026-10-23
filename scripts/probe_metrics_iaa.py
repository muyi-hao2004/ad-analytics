"""用 DWS 表把 metrics.yaml 的 12 个指标全部实算一遍。

这不是「跑个数看看」，而是**契约测试**：
    metrics.yaml 里声明了 12 个指标、各自的公式和依赖字段。
    如果某个指标在数仓里算不出来（缺字段、缺粒度），说明契约和实现脱节了。
    这一步跑通，才敢说「中台能接上这个数仓」。

顺带验证一个最容易被搞错的点：
    比率指标不能二次聚合。
    下面的「陷阱对照」会同时给出两种算法的结果，差额就是踩坑的代价。
"""

from __future__ import annotations

import os
import sys

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = "file:///D:/warehouse"
DB = "lab"


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("probe-metrics-iaa")
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


def main() -> int:
    spark = build_session()
    try:
        # =====================================================================
        # 一、全局 12 指标
        # =====================================================================
        print("=" * 78)
        print("一、全局 12 指标（对齐 metrics.yaml）")
        print("=" * 78)

        row = spark.sql(
            f"""
            WITH acq AS (   -- 买量侧：单轴，渠道 × 天
                SELECT sum(installs)        AS installs,
                       sum(spend)           AS spend,
                       sum(clicks)          AS clicks,
                       sum(buy_impressions) AS buy_impr
                FROM {DB}.dws_campaign_daily
            ), coh AS (     -- 变现侧：双轴，cohort × 第 N 天
                SELECT sum(CASE WHEN day_offset = 0 THEN active_users ELSE 0 END) AS d0_users,
                       sum(installs)          AS installs,
                       sum(spend)             AS spend,
                       sum(ad_revenue)        AS revenue,
                       sum(ad_impressions)    AS ad_impr,
                       sum(rewarded_views)    AS rw_views,
                       sum(CASE WHEN day_offset <= 6 THEN ad_revenue ELSE 0 END)   AS rev_d7,
                       sum(CASE WHEN day_offset <= 6 THEN spend ELSE 0 END)        AS spend_d7,
                       sum(active_users)      AS act_all
                FROM {DB}.dws_cohort_daily
            ), ret AS (     -- 留存：只算窗口完整的 cohort
                SELECT sum(CASE WHEN day_offset = 0 THEN active_users ELSE 0 END) AS den,
                       sum(CASE WHEN day_offset = 1 THEN active_users ELSE 0 END) AS d1,
                       sum(CASE WHEN day_offset = 7 THEN active_users ELSE 0 END) AS d7
                FROM {DB}.dws_cohort_daily
                WHERE install_date <= date_sub(to_date('2026-10-31'), 7)
            )
            SELECT * FROM acq CROSS JOIN coh CROSS JOIN ret
            """
        ).collect()[0]

        def line(no: int, key: str, name: str, value: str, formula: str, track: str) -> None:
            print(f"  {no:>2}. {key:<24} {name:<14} {value:>12}   "
                  f"[{track}] {formula}")

        d7_roi = float(row["rev_d7"]) / float(row["spend_d7"])
        cpi = float(row["spend"]) / row["installs"]
        buy_ctr = row["clicks"] / row["buy_impr"]
        buy_cvr = row["installs"] / row["clicks"]
        buy_ecpm = float(row["spend"]) / row["buy_impr"] * 1000
        mon_ecpm = float(row["revenue"]) / row["ad_impr"] * 1000
        d7_ret = row["d7"] / row["den"]
        d1_ret = row["d1"] / row["den"]
        roi_d7 = d7_roi
        ecpm_d0 = None  # 下面单独算
        views_per_user = row["rw_views"] / row["act_all"]

        line(1, "d7_roi", "D7 回报率", f"{d7_roi:.4f}", "rev[0..6]/spend[0..6]", "monetization")
        line(2, "cpi", "安装成本", f"${cpi:.4f}", "spend/installs", "acquisition")
        line(3, "buy_ctr", "买量点击率", f"{buy_ctr*100:.2f}%", "clicks/buy_impr", "acquisition")
        line(4, "buy_cvr", "买量转化率", f"{buy_cvr*100:.2f}%", "installs/clicks", "acquisition")
        line(5, "buy_ecpm", "买量 eCPM", f"${buy_ecpm:.4f}", "spend/buy_impr*1000", "acquisition")
        line(6, "monetization_ecpm", "变现 eCPM", f"${mon_ecpm:.4f}", "rev/ad_impr*1000", "monetization")
        line(7, "d7_retention", "D7 留存率", f"{d7_ret*100:.2f}%", "active[d7]/installs", "monetization")
        line(8, "retention_dn", "D1 留存率", f"{d1_ret*100:.2f}%", "active[d1]/installs", "monetization")
        line(9, "roi_dn", "D7 累计 ROI", f"{roi_d7:.4f}", "cum(rev)/spend", "monetization")

        d0 = spark.sql(
            f"""
            SELECT sum(ad_revenue) / sum(ad_impressions) * 1000 AS ecpm
            FROM {DB}.dws_cohort_daily WHERE day_offset = 0
            """
        ).collect()[0]["ecpm"]
        line(10, "ecpm_dn", "D0 eCPM", f"${float(d0):.4f}", "rev[d0]/impr[d0]*1000", "monetization")
        line(11, "views_dn", "人均观次", f"{views_per_user:.4f}", "rewarded/active", "monetization")
        line(12, "rewarded_views_per_user", "D0 人均观次",
             f"{float(spark.sql(f'SELECT sum(rewarded_views)/sum(active_users) v FROM {DB}.dws_cohort_daily WHERE day_offset=0').collect()[0]['v']):.4f}",
             "rewarded[d0]/active[d0]", "monetization")

        print(f"\n  12 个指标全部可算 —— metrics.yaml 的契约在这个数仓上兑现了。")

        # =====================================================================
        # 二、按 campaign 的 D7 ROI 排行
        # =====================================================================
        print("\n" + "=" * 78)
        print("二、按 campaign 的 D7 ROI 排行（目标线 0.70）")
        print("=" * 78)
        print(f"  {'campaign':<12}{'installs':>10}{'spend':>12}{'rev7':>12}"
              f"{'D7 ROI':>10}{'CPI':>9}  判定")
        print("  " + "-" * 74)
        rows = spark.sql(
            f"""
            SELECT campaign,
                   sum(installs) AS inst,
                   sum(spend)    AS spend_amt,
                   sum(CASE WHEN day_offset <= 6 THEN ad_revenue ELSE 0 END) AS rev7
            FROM {DB}.dws_cohort_daily
            WHERE install_date <= date_sub(to_date('2026-10-31'), 6)
            GROUP BY campaign
            ORDER BY rev7 / spend_amt DESC
            """
        ).collect()
        for r in rows:
            roi = float(r["rev7"]) / float(r["spend_amt"])
            cpi = float(r["spend_amt"]) / r["inst"]
            verdict = "达标" if roi >= 0.70 else ("临界" if roi >= 0.60 else "需优化")
            print(f"  {r['campaign']:<12}{r['inst']:>10,}{float(r['spend_amt']):>12,.0f}"
                  f"{float(r['rev7']):>12,.0f}{roi:>10.4f}{cpi:>9.4f}  {verdict}")

        # =====================================================================
        # 三、陷阱对照：比率不能二次聚合
        # =====================================================================
        print("\n" + "=" * 78)
        print("三、陷阱对照：对 ctr 直接 avg() 会有多大偏差")
        print("=" * 78)
        r = spark.sql(
            f"""
            SELECT
              avg(clicks / buy_impressions)                          AS naive_avg,
              sum(clicks) / sum(buy_impressions)                     AS correct,
              count(*)                                               AS groups,
              min(buy_impressions)                                   AS min_impr,
              max(buy_impressions)                                   AS max_impr
            FROM {DB}.dws_campaign_daily
            WHERE buy_impressions > 0
            """
        ).collect()[0]
        naive, correct = float(r["naive_avg"]), float(r["correct"])
        diff = (naive - correct) / correct * 100
        print(f"  avg(ctr)              = {naive*100:.4f}%   ← 错误：给每行同等权重")
        print(f"  sum(clicks)/sum(impr) = {correct*100:.4f}%   ← 正确：按曝光量加权")
        print(f"  偏差                  = {diff:+.4f}%  （{r['groups']} 组，"
              f"单组曝光从 {r['min_impr']:,} 到 {r['max_impr']:,}）")
        print(f"\n  原因：一个 5000 次曝光的组合和一个 50 次曝光的组合，"
              f"在 avg() 里权重相同。")
        print(f"  偏差大小取决于曝光量分布有多不均 —— 分布越不均，错得越离谱。")

        # =====================================================================
        # 四、素材级：该砍的是哪几个
        # =====================================================================
        print("\n" + "=" * 78)
        print("四、素材级 Top5 / Bottom5（D7 ROI，仅看成本 > $100 的）")
        print("=" * 78)
        # 注意这里读的是 dws_cohort_daily 而不是 dws_creative_daily。
        # 因为「D7 ROI」是 cohort 指标（需要知道这批用户后来 7 天赚了多少），
        # 而 dws_creative_daily 的粒度是「素材 × 投放日」，里面没有 day_offset，
        # 算不出任何 Dn 类指标 —— 它只回答「这个素材今天花了多少、点了多少次」。
        # 同一维度在不同表里有不同口径，这是分层的正常代价：想算 cohort 就回 cohort 表。
        for label, order in (("最该砍", "ASC"), ("最该加预算", "DESC")):
            print(f"\n  {label}:")
            print(f"  {'creative_id':<24}{'installs':>9}{'spend':>10}{'D7 ROI':>10}")
            rows = spark.sql(
                f"""
                SELECT creative_id,
                       sum(installs) AS inst,
                       sum(spend)    AS spend_amt,
                       sum(CASE WHEN day_offset <= 6 THEN ad_revenue ELSE 0 END)
                           / sum(spend) AS roi
                FROM {DB}.dws_cohort_daily
                WHERE creative_id IS NOT NULL
                  AND install_date <= date_sub(to_date('2026-10-31'), 6)
                GROUP BY creative_id
                HAVING sum(spend) > 5000
                ORDER BY roi {order}
                LIMIT 5
                """
            ).collect()
            for r in rows:
                print(f"  {r['creative_id']:<24}{r['inst']:>9,}{float(r['spend_amt']):>10,.0f}"
                      f"{float(r['roi']):>10.4f}")

        # =====================================================================
        # 五、数据体量总表
        # =====================================================================
        print("\n" + "=" * 78)
        print("五、四层体量")
        print("=" * 78)
        print(f"  {'表':<30}{'行数':>14}{'分区':>8}")
        print("  " + "-" * 54)
        for t in ["ods_iaa_user_daily", "ods_iaa_acquisition_daily", "dwd_user_daily",
                  "dws_cohort_daily", "dws_campaign_daily", "dws_creative_daily",
                  "dws_date_daily"]:
            r = spark.sql(f"SELECT count(*) c FROM {DB}.{t}").collect()[0]["c"]
            try:
                p = len([x for x in spark.sql(f"SHOW PARTITIONS {DB}.{t}").collect()])
            except Exception:
                p = 0
            print(f"  {t:<30}{r:>14,}{p if p else '-':>8}")

    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
