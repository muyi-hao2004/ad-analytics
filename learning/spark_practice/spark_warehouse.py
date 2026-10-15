"""Spark 版数仓加工脚本（在 WSL 服务器上运行）

与本地 Pandas 版 main.py 形成「混合架构」：
    - 本地 Windows + Pandas：数据探索、校验、出 HTML 报表
    - WSL 服务器 + Spark：大数据的分层加工（DWD/DWS/ADS）

本脚本用 PySpark 实现数仓的明细层、汇总层、应用层，
中间产物全部落盘为 Parquet（列式存储，Spark 原生支持）。

运行方式（在 WSL 里）：
    cd ~/test
    source .venv/bin/activate
    python spark_warehouse.py
"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window


def create_spark():
    """创建 SparkSession —— Spark 程序入口"""
    spark = (
        SparkSession.builder
        .appName("ad_warehouse_spark")   # 任务名，显示在 Spark UI
        .master("local[*]")              # 本地模式，用满所有核
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")  # 减少日志噪音，只看 WARN 以上
    return spark


# ============================================================
# 第 1 层：ODS（贴源层）—— 原样读入，落盘 Parquet
# ============================================================
def build_ods(spark, input_path, output_path):
    """读原始 CSV，原样落盘为 Parquet（不做任何加工）"""
    print("\n[ODS] 贴源层：读 CSV → 落盘 Parquet")

    df = spark.read.csv(
        input_path,
        header=True,        # 第一行是表头
        inferSchema=True,   # 自动推断列类型（date 会变 date，数字变 int/double）
    )

    print(f"[ODS] 原始数据 schema：")
    df.printSchema()
    print(f"[ODS] 行数：{df.count()}")

    # 原样落盘 Parquet（列式存储，后面各层都从这读）
    df.write.mode("overwrite").parquet(output_path)
    print(f"[ODS] 已落盘：{output_path}")

    return df


# ============================================================
# 第 2 层：DWD（明细层）—— 清洗 + 维度建模
# ============================================================
def build_dwd(df, fact_path, dim_path):
    """清洗明细数据 + 抽取维度表"""
    print("\n[DWD] 明细层：清洗 + 维度建模")

    # 清洗逻辑（对应 Pandas 版 cleaning.py）：
    # 1. 过滤关键字段为 null 的行（date/channel/campaign 必须有值）
    df_clean = df.dropna(subset=["date", "channel", "campaign"])

    # 2. 数值列负值、曝光为 0、点击>曝光 的记录都删掉
    df_clean = (
        df_clean
        .filter(F.col("impressions") > 0)          # 零曝光删除
        .filter(F.col("cost") >= 0)                 # 花费不为负
        .filter(F.col("clicks") >= 0)               # 点击不为负
        .filter(F.col("conversions") >= 0)          # 转化不为负
        .filter(F.col("clicks") <= F.col("impressions"))  # 点击不能超过曝光
    )

    # 3. cost 缺失用 0 填充
    df_clean = df_clean.fillna({"cost": 0, "impressions": 0, "clicks": 0, "conversions": 0})

    # 4. 去重
    df_clean = df_clean.dropDuplicates()

    print(f"[DWD] 清洗后行数：{df_clean.count()}")

    # 事实表落盘
    df_clean.write.mode("overwrite").parquet(fact_path)
    print(f"[DWD] 事实表已落盘：{fact_path}")

    # 维度表：从事实表抽取 campaign 维度（去重）
    dim_df = df_clean.select("campaign", "channel", "region", "device").dropDuplicates()
    dim_df.write.mode("overwrite").parquet(dim_path)
    print(f"[DWD] 维度表已落盘：{dim_path}（{dim_df.count()} 个计划）")

    return df_clean


# ============================================================
# 第 3 层：DWS（汇总层）—— 按维度聚合 + 计算指标
# ============================================================
def build_dws(df_clean, channel_path, date_path):
    """按渠道、日期两个维度聚合，计算 CTR/CVR/CPC/CPA"""
    print("\n[DWS] 汇总层：按维度聚合 + 指标计算")

    # 按渠道聚合（对应 Pandas 版 group_by_dimension("channel")）
    channel_df = (
        df_clean.groupBy("channel")
        .agg(
            F.sum("impressions").alias("impressions"),
            F.sum("clicks").alias("clicks"),
            F.sum("conversions").alias("conversions"),
            F.sum("cost").alias("cost"),
        )
        .withColumn("ctr", F.when(F.col("impressions") > 0,
                                  F.col("clicks") / F.col("impressions")).otherwise(0))
        .withColumn("cvr", F.when(F.col("clicks") > 0,
                                  F.col("conversions") / F.col("clicks")).otherwise(0))
        .withColumn("cpc", F.when(F.col("clicks") > 0,
                                  F.col("cost") / F.col("clicks")).otherwise(0))
        .withColumn("cpa", F.when(F.col("conversions") > 0,
                                  F.col("cost") / F.col("conversions")).otherwise(0))
        .orderBy(F.col("cost").desc())
    )
    channel_df.write.mode("overwrite").parquet(channel_path)
    print(f"[DWS] 渠道汇总已落盘：{channel_path}")
    channel_df.show(truncate=False)

    # 按日期聚合（对应 Pandas 版 group_by_date）
    date_df = (
        df_clean.groupBy("date")
        .agg(
            F.sum("impressions").alias("impressions"),
            F.sum("clicks").alias("clicks"),
            F.sum("conversions").alias("conversions"),
            F.sum("cost").alias("cost"),
        )
        .withColumn("ctr", F.when(F.col("impressions") > 0,
                                  F.col("clicks") / F.col("impressions")).otherwise(0))
        .withColumn("cvr", F.when(F.col("clicks") > 0,
                                  F.col("conversions") / F.col("clicks")).otherwise(0))
        .withColumn("cpc", F.when(F.col("clicks") > 0,
                                  F.col("cost") / F.col("clicks")).otherwise(0))
        .withColumn("cpa", F.when(F.col("conversions") > 0,
                                  F.col("cost") / F.col("conversions")).otherwise(0))
        .orderBy("date")
    )
    date_df.write.mode("overwrite").parquet(date_path)
    print(f"[DWS] 日期汇总已落盘：{date_path}")

    return channel_df, date_df


# ============================================================
# 第 4 层：ADS（应用层）—— 异常检测（窗口函数版花费暴涨）
# ============================================================
def build_ads(df_clean, anomaly_path):
    """异常检测：花费暴涨（用窗口函数，展示 Spark 高级用法）"""
    print("\n[ADS] 应用层：异常检测")

    # 按 渠道+日期 汇总每天花费
    daily_spend = (
        df_clean.groupBy("channel", "date")
        .agg(F.sum("cost").alias("daily_cost"))
    )

    # 用窗口函数算「前 7 天平均花费」—— Spark 比 Pandas 优雅得多
    window_spec = (
        Window.partitionBy("channel")
        .orderBy("date")
        .rowsBetween(-7, -1)   # 前 7 天（不含当天）
    )
    daily_spend = daily_spend.withColumn(
        "prev_7_avg",
        F.avg("daily_cost").over(window_spec),
    )

    # 当天花费 > 前7天平均 × 1.5 判定为暴涨
    surge = daily_spend.filter(
        (F.col("prev_7_avg").isNotNull())
        & (F.col("prev_7_avg") > 0)
        & (F.col("daily_cost") > F.col("prev_7_avg") * 1.5)
    )

    surge_count = surge.count()
    print(f"[ADS] 检测到花费暴涨异常：{surge_count} 条")
    surge.write.mode("overwrite").parquet(anomaly_path)
    print(f"[ADS] 异常结果已落盘：{anomaly_path}")

    if surge_count > 0:
        surge.orderBy(F.col("daily_cost").desc()).show(10, truncate=False)

    return surge


def main():
    spark = create_spark()

    try:
        # 输入输出路径（相对 WSL 的 ~/test 目录）
        raw_csv = "data/raw/sample_ad_data.csv"
        wh = "data/warehouse"

        # 四层依次构建
        df_raw = build_ods(spark, raw_csv, f"{wh}/ods/ods_ad_log")
        df_clean = build_dwd(df_raw, f"{wh}/dwd/dwd_ad_fact", f"{wh}/dwd/dim_campaign")
        build_dws(df_clean, f"{wh}/dws/dws_ad_channel_daily", f"{wh}/dws/dws_ad_date_daily")
        build_ads(df_clean, f"{wh}/ads/ads_anomaly")

        print("\n" + "=" * 60)
        print("Spark 数仓四层构建完成！")
        print("=" * 60)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
