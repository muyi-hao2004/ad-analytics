# ============================================
# Spark 基础练习 08：数据读写（Data I/O）
# ============================================
#
# 【练习目标】
# 1. 学会读取不同格式的数据（CSV/Parquet/JSON）
# 2. 学会写出不同格式的数据（CSV/Parquet/JSON）
# 3. 学会读写 MySQL 数据库
# 4. 理解保存模式（mode）的区别
# 5. 学会分区存储（partitionBy）
#
# 【知识点】
# - spark.read.csv/parquet/json：读取数据
# - df.write.csv/parquet/json：写出数据
# - jdbc：读写 MySQL
# - mode：overwrite/append/ignore/error
# - partitionBy：分区存储
# ============================================


from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("Practice08_DataIO")
    .master("local[*]")
    .getOrCreate()
)

# 读取广告数据
df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

print("=" * 60)
print("数据读取成功，共", df.count(), "行")
print("=" * 60)


# ---------- 一、写出数据 ----------

print("\n" + "-" * 60)
print("一、写出数据")
print("-" * 60)

# 1.1 写出 CSV
print("\n--- 1.1 写出 CSV ---")
df.write.csv("../data/output/ad_result_csv", header=True, mode="overwrite")
print("已写出 CSV 到 ../data/output/ad_result_csv")

# 1.2 写出 Parquet（大数据最常用！）
print("\n--- 1.2 写出 Parquet ---")
df.write.parquet("../data/output/ad_result_parquet", mode="overwrite")
print("已写出 Parquet 到 ../data/output/ad_result_parquet")

# 1.3 写出 JSON
print("\n--- 1.3 写出 JSON ---")
df.write.json("../data/output/ad_result_json", mode="overwrite")
print("已写出 JSON 到 ../data/output/ad_result_json")


# ---------- 二、读取不同格式的数据 ----------

print("\n" + "-" * 60)
print("二、读取不同格式的数据")
print("-" * 60)

# 2.1 读取 Parquet
print("\n--- 2.1 读取 Parquet ---")
df_parquet = spark.read.parquet("../data/output/ad_result_parquet")
print("Parquet 数据：", df_parquet.count(), "行")
df_parquet.printSchema()
df_parquet.show(5, truncate=False)

# 2.2 读取 JSON
print("\n--- 2.2 读取 JSON ---")
df_json = spark.read.json("../data/output/ad_result_json")
print("JSON 数据：", df_json.count(), "行")
df_json.printSchema()
df_json.show(5, truncate=False)


# ---------- 三、保存模式（mode） ----------

print("\n" + "-" * 60)
print("三、保存模式（mode）")
print("-" * 60)

print("""
四种保存模式：
- overwrite：覆盖（删掉旧的，重新写）—— 全量任务用
- append：追加（在已有数据后面加）—— 增量任务用
- ignore：忽略（如果已经存在就不写）—— 只写一次用
- error：报错（如果已存在就抛异常）—— 生产环境默认，防止误覆盖
""")

# 3.1 overwrite 模式
print("\n--- 3.1 overwrite 模式：覆盖写 ---")
df.write.parquet("../data/output/test_mode", mode="overwrite")
print("第一次写入（overwrite）")
df.write.parquet("../data/output/test_mode", mode="overwrite")
print("第二次写入（overwrite）：覆盖了第一次的结果")

# 3.2 append 模式
print("\n--- 3.2 append 模式：追加写 ---")
df.limit(10).write.parquet("../data/output/test_append", mode="overwrite")
print("第一次写入10条")
df.limit(10).write.parquet("../data/output/test_append", mode="append")
print("第二次追加10条")
df_test = spark.read.parquet("../data/output/test_append")
print("现在共有：", df_test.count(), "条")


# ---------- 四、分区存储（partitionBy） ----------

print("\n" + "-" * 60)
print("四、分区存储（partitionBy）")
print("-" * 60)

# 4.1 按渠道分区存储
print("\n--- 4.1 按渠道分区存储 ---")
df.write.partitionBy("channel").parquet("../data/output/ad_partitioned", mode="overwrite")
print("已按 channel 分区存储到 ../data/output/ad_partitioned")
print("目录结构：")
print("  channel=Facebook/")
print("  channel=Google/")
print("  channel=TikTok/")
print("  channel=YouTube/")
print("  channel=Instagram/")

# 4.2 读取分区数据（分区裁剪）
print("\n--- 4.2 读取分区数据（只查 Facebook）---")
df_fb = spark.read.parquet("../data/output/ad_partitioned").filter("channel = 'Facebook'")
print("Facebook 数据：", df_fb.count(), "条")
df_fb.show(5, truncate=False)

# 4.3 按日期+渠道多级分区
print("\n--- 4.3 按日期+渠道多级分区 ---")
df.write.partitionBy("date", "channel").parquet("../data/output/ad_partitioned_multi", mode="overwrite")
print("已按 date + channel 多级分区存储")
print("目录结构：")
print("  date=2026-08-01/")
print("    channel=Facebook/")
print("    channel=Google/")
print("  date=2026-08-02/")
print("    channel=Facebook/")
print("    channel=Google/")


# ---------- 五、读写 MySQL 数据库 ----------

print("\n" + "-" * 60)
print("五、读写 MySQL 数据库")
print("-" * 60)

# MySQL 连接参数
mysql_url = "jdbc:mysql://localhost:3306/ad_analytics"
mysql_props = {
    "user": "root",
    "password": "123456",
    "driver": "com.mysql.cj.jdbc.Driver"
}

# 5.1 从 MySQL 读取数据
print("\n--- 5.1 从 MySQL 读取数据 ---")
try:
    df_mysql = (
        spark.read
        .jdbc(url=mysql_url, table="ad_data", properties=mysql_props)
    )
    print("从 MySQL ad_data 表读取到：", df_mysql.count(), "行")
    df_mysql.show(5, truncate=False)
except Exception as e:
    print("读取 MySQL 失败：", str(e))
    print("（如果 MySQL 没启动或表不存在，这是正常的）")

# 5.2 把 DataFrame 写入 MySQL
print("\n--- 5.2 把 DataFrame 写入 MySQL ---")
try:
    # 先做一些聚合计算
    df_channel_agg = (
        df.groupBy("channel")
        .agg(
            F.sum("cost").alias("total_cost"),
            F.sum("clicks").alias("total_clicks"),
            F.sum("conversions").alias("total_conversions")
        )
        .withColumn("ctr", F.round(F.col("total_clicks") / F.col("total_impressions"), 4))
    )

    # 写入 MySQL
    (
        df_channel_agg.write
        .jdbc(url=mysql_url, table="channel_summary", mode="overwrite", properties=mysql_props)
    )
    print("已把渠道汇总数据写入 MySQL channel_summary 表")
except Exception as e:
    print("写入 MySQL 失败：", str(e))
    print("（如果 MySQL 没启动或驱动没装，这是正常的）")


# ---------- 六、综合实战：完整的 ETL 流程 ----------

print("\n" + "-" * 60)
print("六、综合实战：完整的 ETL 流程")
print("-" * 60)

print("""
实际工作中的完整流程：
1. 从 CSV 读取原始数据
2. 数据清洗（去重、缺失值处理、过滤异常值）
3. 指标计算（CTR、CVR、CPA、ROI）
4. 写出结果到 Parquet（分区存储）
5. 写出汇总报表到 MySQL
6. 读取结果数据生成日报
""")

# 6.1 模拟完整流程
print("\n--- 6.1 模拟完整 ETL 流程 ---")

# Step 1: 读取原始数据
print("Step 1: 读取原始 CSV 数据")
raw_df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)
print(f"  读取 {raw_df.count()} 条原始数据")

# Step 2: 数据清洗
print("Step 2: 数据清洗")
clean_df = (
    raw_df
    .dropDuplicates(["date", "channel", "campaign"])  # 去重
    .dropna(subset=["cost", "clicks", "conversions"])  # 去除关键列缺失值
    .filter(F.col("cost") > 0)  # 过滤花费为0的异常数据
)
print(f"  清洗后 {clean_df.count()} 条数据")

# Step 3: 指标计算
print("Step 3: 计算指标")
metrics_df = (
    clean_df
    .withColumn("ctr", F.round(F.col("clicks") / F.col("impressions"), 4))
    .withColumn("cvr", F.round(F.col("conversions") / F.col("clicks"), 4))
    .withColumn("cpa", F.round(F.col("cost") / F.col("conversions"), 2))
)
print("  计算完成：CTR、CVR、CPA")

# Step 4: 写出明细数据到 Parquet（分区存储）
print("Step 4: 写出明细数据到 Parquet（按渠道分区）")
metrics_df.write.partitionBy("channel").parquet(
    "../data/output/etl_detail",
    mode="overwrite"
)
print("  已写出到 ../data/output/etl_detail")

# Step 5: 汇总渠道报表，写出到 Parquet
print("Step 5: 生成渠道汇总报表")
channel_summary = (
    metrics_df
    .groupBy("channel")
    .agg(
        F.count("*").alias("campaign_count"),
        F.sum("cost").alias("total_cost"),
        F.sum("impressions").alias("total_impressions"),
        F.sum("clicks").alias("total_clicks"),
        F.sum("conversions").alias("total_conversions")
    )
    .withColumn("ctr", F.round(F.col("total_clicks") / F.col("total_impressions"), 4))
    .withColumn("cvr", F.round(F.col("total_conversions") / F.col("total_clicks"), 4))
    .withColumn("cpa", F.round(F.col("total_cost") / F.col("total_conversions"), 2))
    .orderBy(F.desc("total_cost"))
)
channel_summary.write.parquet("../data/output/channel_summary", mode="overwrite")
print("  已写出渠道汇总报表")

# Step 6: 读取结果并展示
print("Step 6: 读取结果并展示")
result = spark.read.parquet("../data/output/channel_summary")
print("\n渠道汇总报表：")
result.show(truncate=False)


spark.stop()
print("\n>>> 示例代码运行完成！")


# ============================================
# 【练习题】
# ============================================

print("\n" + "=" * 60)
print("【练习题区域】")
print("=" * 60)

# 练习题1：读取 CSV 数据，然后写出为 Parquet 格式
# 你的代码：



# 练习题2：读取 Parquet 数据，查看行数和列名
# 你的代码：



# 练习题3：把 DataFrame 写出为 JSON 格式
# 你的代码：



# 练习题4：用 overwrite 模式写出数据，然后用 append 模式追加 10 条数据，
#           最后读取出来看总共有多少条
# 你的代码：



# 练习题5：把数据按 channel 列分区存储到 Parquet
# 你的代码：



# 练习题6：读取分区存储的数据，只筛选 channel = 'Facebook' 的数据
# 你的代码：



# 练习题7：把数据按 date + channel 两级分区存储
# 你的代码：



# 练习题8：模拟完整 ETL 流程
#           1. 读取 CSV 原始数据
#           2. 去重 + 过滤异常值
#           3. 计算 CTR、CVR、CPA
#           4. 按渠道分区写出明细数据到 Parquet
#           5. 生成渠道汇总报表并写出
# 你的代码：



# 练习题9：从 MySQL 读取 ad_data 表的数据，然后按渠道聚合总花费，
#           把结果写出到 MySQL 的 channel_summary 表
# 提示：jdbc 连接参数 url=jdbc:mysql://localhost:3306/ad_analytics, user=root, password=123456
# 你的代码：



# 练习题10：综合题：
#           1. 读取 CSV 原始数据
#           2. 数据清洗（去重、缺失值处理）
#           3. 按日期+渠道聚合，计算每天每个渠道的总花费、总点击、总转化
#           4. 计算每天每个渠道的环比增长率（用 lag）
#           5. 按 date+channel 分区写出结果到 Parquet
#           6. 读取结果，展示前 20 行
# 你的代码：



print("\n>>> 练习题完成！")
