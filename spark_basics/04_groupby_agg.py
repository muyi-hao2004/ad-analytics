# ============================================
# Spark 基础练习 04：groupBy 分组聚合 + orderBy 排序
# ============================================
#
# 【练习目标】
# 1. 熟练使用 groupBy() 分组
# 2. 熟练使用 agg() 做多个聚合计算
# 3. 掌握常用聚合函数
# 4. 学会多维度分组
# 5. 学会分组后筛选（HAVING）
# 6. 熟练使用 orderBy() 排序
#
# 【知识点】
# - df.groupBy("列1", "列2")：按一列或多列分组
# - df.agg(...)：聚合，可同时计算多个指标
# - 聚合函数：count、sum、avg、max、min、countDistinct、first、last
# - 分组后筛选：agg() 后再用 filter()，相当于 SQL 的 HAVING
# - df.orderBy()：排序，F.desc() 降序，F.asc() 升序
# - df.limit(n)：取前 n 行
# ============================================


from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("Practice04_GroupByAgg")
    .master("local[*]")
    .getOrCreate()
)

df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

print("=" * 60)
print("数据读取成功，共", df.count(), "行")
print("=" * 60)


# ---------- 一、基本分组计数 ----------

print("\n" + "-" * 60)
print("一、基本分组计数")
print("-" * 60)

# 1.1 按渠道分组计数
print("\n--- 1.1 按渠道分组计数 ---")
df.groupBy("channel").count().show()

# 1.2 按地区分组计数
print("\n--- 1.2 按地区分组计数 ---")
df.groupBy("region").count().show()

# 1.3 按设备分组计数
print("\n--- 1.3 按设备分组计数 ---")
df.groupBy("device").count().show()


# ---------- 二、分组 + 单个聚合 ----------

print("\n" + "-" * 60)
print("二、分组 + 单个聚合")
print("-" * 60)

# 2.1 按渠道分组，计算总花费（不推荐的写法，列名不好看）
print("\n--- 2.1 按渠道分组计算总花费（列名是 sum(cost)）---")
df.groupBy("channel").sum("cost").show()

# 2.2 按渠道分组，计算总花费（推荐写法，用 alias 起名字）
print("\n--- 2.2 按渠道分组计算总花费（列名是 total_cost）---")
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")).show()

# 2.3 按渠道分组，计算平均花费
print("\n--- 2.3 按渠道分组计算平均花费 ---")
df.groupBy("channel").agg(F.avg("cost").alias("avg_cost")).show()

# 2.4 按渠道分组，计算最大/最小花费
print("\n--- 2.4 按渠道分组计算最大/最小花费 ---")
df.groupBy("channel").agg(
    F.max("cost").alias("max_cost"),
    F.min("cost").alias("min_cost")
).show()


# ---------- 三、分组 + 多个聚合（最常用！） ----------

print("\n" + "-" * 60)
print("三、分组 + 多个聚合（最常用！）")
print("-" * 60)

# 3.1 按渠道分组，同时计算多个指标
print("\n--- 3.1 按渠道分组计算综合指标 ---")
channel_stats = (
    df.groupBy("channel")
    .agg(
        F.count("*").alias("record_count"),              # 记录数
        F.sum("impressions").alias("total_impressions"), # 总曝光
        F.sum("clicks").alias("total_clicks"),            # 总点击
        F.sum("conversions").alias("total_conversions"),  # 总转化
        F.sum("cost").alias("total_cost"),                # 总花费
        F.avg("cost").alias("avg_cost"),                  # 平均花费
        F.max("cost").alias("max_cost"),                  # 最大花费
        F.min("cost").alias("min_cost")                   # 最小花费
    )
)
channel_stats.show(truncate=False)

# 3.2 countDistinct：去重计数
print("\n--- 3.2 按渠道分组，统计有多少个不同的广告计划 ---")
df.groupBy("channel").agg(
    F.count("*").alias("record_count"),
    F.countDistinct("campaign").alias("campaign_count")
).show()


# ---------- 四、多维度分组 ----------

print("\n" + "-" * 60)
print("四、多维度分组")
print("-" * 60)

# 4.1 按渠道 + 地区两个维度分组
print("\n--- 4.1 按渠道+地区分组（前20行）---")
df.groupBy("channel", "region").agg(
    F.count("*").alias("record_count"),
    F.sum("cost").alias("total_cost")
).orderBy("channel", "region").show(20)

# 4.2 按渠道 + 地区 + 设备三个维度分组
print("\n--- 4.2 按渠道+地区+设备分组（前30行）---")
df.groupBy("channel", "region", "device").agg(
    F.count("*").alias("record_count"),
    F.sum("cost").alias("total_cost")
).orderBy("channel", "region", "device").show(30)


# ---------- 五、分组后筛选（HAVING） ----------

print("\n" + "-" * 60)
print("五、分组后筛选（相当于 SQL 的 HAVING）")
print("-" * 60)

# 5.1 分组后筛选：只保留总花费 > 100000 的渠道
print("\n--- 5.1 总花费 > 100000 的渠道 ---")
(
    df.groupBy("channel")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("cost").alias("total_cost")
    )
    .filter(F.col("total_cost") > 100000)
    .show()
)

# 5.2 分组后筛选：只保留记录数 > 200 的渠道+地区组合
print("\n--- 5.2 记录数 > 40 的渠道+地区组合 ---")
(
    df.groupBy("channel", "region")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("cost").alias("total_cost")
    )
    .filter(F.col("record_count") > 40)
    .orderBy(F.desc("record_count"))
    .show()
)


# ---------- 六、排序 orderBy ----------

print("\n" + "-" * 60)
print("六、排序 orderBy")
print("-" * 60)

# 6.1 升序排列（默认）
print("\n--- 6.1 按总花费升序排列 ---")
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")).orderBy("total_cost").show()

# 6.2 降序排列
print("\n--- 6.2 按总花费降序排列 ---")
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")).orderBy(F.desc("total_cost")).show()

# 6.3 另一种降序写法
print("\n--- 6.3 另一种降序写法：F.col().desc() ---")
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")).orderBy(F.col("total_cost").desc()).show()

# 6.4 多列排序：先按渠道升序，再按总花费降序
print("\n--- 6.4 多列排序：渠道升序 + 总花费降序（前20行）---")
df.groupBy("channel", "region").agg(
    F.sum("cost").alias("total_cost")
).orderBy(F.asc("channel"), F.desc("total_cost")).show(20)

# 6.5 排序后取 Top N
print("\n--- 6.5 花费最高的 Top 3 渠道 ---")
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost")
).orderBy(F.desc("total_cost")).limit(3).show()


# ---------- 七、综合实战：完整的渠道分析 ----------

print("\n" + "-" * 60)
print("七、综合实战：完整的渠道分析")
print("-" * 60)

# 先计算每行的 CTR、CVR、CPA
df_with_metrics = (
    df
    .withColumn(
        "CTR",
        F.when(F.col("impressions") > 0, F.col("clicks") / F.col("impressions")).otherwise(0)
    )
    .withColumn(
        "CVR",
        F.when(F.col("clicks") > 0, F.col("conversions") / F.col("clicks")).otherwise(0)
    )
    .withColumn(
        "CPA",
        F.when(F.col("conversions") > 0, F.col("cost") / F.col("conversions")).otherwise(0)
    )
)

# 按渠道分组，计算综合指标
print("\n--- 7.1 渠道综合分析 ---")
channel_analysis = (
    df_with_metrics
    .groupBy("channel")
    .agg(
        F.count("*").alias("record_count"),
        F.countDistinct("campaign").alias("campaign_count"),
        F.sum("impressions").alias("total_impressions"),
        F.sum("clicks").alias("total_clicks"),
        F.sum("conversions").alias("total_conversions"),
        F.sum("cost").alias("total_cost"),
        F.round(F.avg("CTR"), 4).alias("avg_ctr"),
        F.round(F.avg("CVR"), 4).alias("avg_cvr"),
        F.round(F.avg("CPA"), 2).alias("avg_cpa")
    )
    # 计算整体 CTR、CVR、CPA（用总量算，不是平均值的平均）
    .withColumn("overall_ctr", F.round(F.col("total_clicks") / F.col("total_impressions"), 4))
    .withColumn("overall_cvr", F.round(F.col("total_conversions") / F.col("total_clicks"), 4))
    .withColumn("overall_cpa", F.round(F.col("total_cost") / F.col("total_conversions"), 2))
    .orderBy(F.desc("total_cost"))
)
channel_analysis.show(truncate=False)

# 花费最高的 Top 5 广告计划
print("\n--- 7.2 花费最高的 Top 5 广告计划 ---")
(
    df_with_metrics
    .groupBy("channel", "campaign")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("cost").alias("total_cost"),
        F.sum("conversions").alias("total_conversions")
    )
    .orderBy(F.desc("total_cost"))
    .limit(5)
    .show(truncate=False)
)


spark.stop()
print("\n>>> 示例代码运行完成！")


# ============================================
# 【练习题】
# ============================================

print("\n" + "=" * 60)
print("【练习题区域】")
print("=" * 60)

# 练习题1：创建 SparkSession，读取数据
# 你的代码：



# 练习题2：按 campaign（广告计划）分组，统计每个广告计划有多少条记录，按记录数降序排列，显示前10
# 你的代码：
df.groupBy("campaign").agg(F.count("*").alias("count_record")).orderBy(F.desc("count_record")).limit(10).show()




# 练习题3：按渠道分组，计算每个渠道的总曝光、总点击、总转化、总花费，给每列起个有意义的名字
# 你的代码：
df.groupBy("channel").agg(
    F.sum("impressions").alias("total_im"),
    F.sun("clicks").alias("total_ck"),
    F.sum("cost").alias("total.cost"),
    F.sum("conversions").alias("total_cv")
)


# 练习题4：按渠道分组，计算每个渠道的平均花费、最大花费、最小花费，按平均花费降序排列
# 你的代码：



# 练习题5：按渠道分组，统计每个渠道有多少个不同的地区（countDistinct）
# 你的代码：



# 练习题6：按渠道 + 设备两个维度分组，计算总花费，按渠道升序、总花费降序排列
# 你的代码：



# 练习题7：按地区分组，计算总花费，然后只保留总花费 > 300000 的地区，按总花费降序排列
# 提示：分组聚合后用 filter 筛选
# 你的代码：
df.groupBy("region").agg(
    F.sum("cost").alias("total_cost").filter(F.col("total_cost")).orderBy(F.desc("cost")))


# 练习题8：先计算每行的 CTR（点击/曝光，注意处理曝光=0的情况），然后按渠道分组，计算平均 CTR 和整体 CTR（总点击/总曝光）
# 提示：整体 CTR 用 withColumn 在聚合后计算
# 你的代码：



# 练习题9：按渠道 + 广告计划分组，计算总花费，然后取花费最高的前10个广告计划
# 提示：用 orderBy + limit
# 你的代码：



# 练习题10：按渠道分组，计算记录数和总花费，然后筛选出记录数 > 200 且 总花费 > 500000 的渠道
# 提示：filter 里用 & 组合两个条件
# 你的代码：
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.count("*").alias("total_record")
).filter((F.col("total_record") > 200) & (F.col("total_cost") > 50000)).show()


# 练习题11：按日期分组，计算每天的总花费，按日期升序排列，显示前10天
# 你的代码：



# 练习题12：综合题：按渠道分组，计算记录数、总曝光、总点击、总转化、总花费、整体CTR、整体CVR、整体CPA，
#            按总花费降序排列，显示所有结果
# 你的代码：



print("\n>>> 练习题完成！")
