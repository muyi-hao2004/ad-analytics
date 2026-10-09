# ============================================
# Spark 基础练习 06：窗口函数（Window Functions）
# ============================================
#
# 【练习目标】
# 1. 理解窗口函数的概念，和 groupBy 的区别
# 2. 熟练使用排名函数：row_number、rank、dense_rank
# 3. 学会用窗口函数取 Top N
# 4. 学会累计求和、移动平均（聚合函数 over）
# 5. 学会用 lag/lead 计算环比
#
# 【知识点】
# - Window.partitionBy("列")：分区（类似 groupBy）
# - Window.orderBy("列")：分区内排序
# - Window.rowsBetween(开始, 结束)：窗口范围
# - 排名函数：row_number()、rank()、dense_rank()
# - 聚合函数 over：sum()、avg()、count() 等
# - 偏移函数：lag()、lead()
# ============================================


from pyspark.sql import SparkSession
from pyspark.sql import Window
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("Practice06_WindowFunctions")
    .master("local[*]")
    .getOrCreate()
)

# 读取广告数据
df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

print("=" * 60)
print("数据读取成功，共", df.count(), "行")
print("列名：", df.columns)
print("=" * 60)


# ---------- 一、排名函数 ----------

print("\n" + "-" * 60)
print("一、排名函数：row_number、rank、dense_rank")
print("-" * 60)

# 1.1 三个排名函数的区别
print("\n--- 1.1 三个排名函数对比（按渠道分区，按花费降序）---")

# 先按渠道+广告计划聚合，得到每个广告计划的总花费
df_campaign_cost = (
    df.groupBy("channel", "campaign")
    .agg(F.sum("cost").alias("total_cost"))
)

# 定义窗口：按渠道分区，按总花费降序排序
window_rank = Window.partitionBy("channel").orderBy(F.desc("total_cost"))

# 应用三个排名函数
df_ranked = (
    df_campaign_cost
    .withColumn("row_number", F.row_number().over(window_rank))
    .withColumn("rank", F.rank().over(window_rank))
    .withColumn("dense_rank", F.dense_rank().over(window_rank))
)

print("每个渠道的前5个广告计划排名：")
df_ranked.filter(F.col("row_number") <= 5).orderBy("channel", "row_number").show(30, truncate=False)

print("""
三个排名函数的区别：
- row_number：连续编号，即使值相同排名也不同（1,2,3,4）
- rank：值相同排名相同，后面跳号（1,1,3,4）
- dense_rank：值相同排名相同，后面不跳号（1,1,2,3）
实际工作中最常用 row_number()，因为排名唯一，方便取 Top N
""")

# 1.2 取每个渠道花费 Top 3 的广告计划（最常用！）
print("\n--- 1.2 取每个渠道花费 Top 3 的广告计划 ---")
window_topn = Window.partitionBy("channel").orderBy(F.desc("total_cost"))

top3_by_channel = (
    df_campaign_cost
    .withColumn("rn", F.row_number().over(window_topn))
    .filter(F.col("rn") <= 3)
    .drop("rn")
    .orderBy("channel", F.desc("total_cost"))
)
top3_by_channel.show(20, truncate=False)

print("这就是经典的 Top N 问题，用窗口函数 + filter 一行搞定！")


# ---------- 二、聚合窗口函数：累计求和 ----------

print("\n" + "-" * 60)
print("二、聚合窗口函数：累计求和")
print("-" * 60)

# 2.1 按日期累计总花费
print("\n--- 2.1 按日期累计总花费 ---")

# 先按日期聚合，得到每天的总花费
df_daily_cost = (
    df.groupBy("date")
    .agg(F.sum("cost").alias("daily_cost"))
    .orderBy("date")
)

# 定义窗口：按日期排序，从第一行到当前行（累计）
window_cumulative = (
    Window.orderBy("date")
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)

# 累计求和
df_cumulative = (
    df_daily_cost
    .withColumn("cumulative_cost", F.sum("daily_cost").over(window_cumulative))
    .orderBy("date")
)

print("前10天的累计花费：")
df_cumulative.show(10, truncate=False)

print("""
累计求和的固定写法：
rowsBetween(Window.unboundedPreceding, Window.currentRow)
意思是：从分区的第一行，加到当前行
""")

# 2.2 按渠道分别累计
print("\n--- 2.2 按渠道分别累计花费（前15行）---")

# 先按渠道+日期聚合
df_channel_daily = (
    df.groupBy("channel", "date")
    .agg(F.sum("cost").alias("daily_cost"))
)

# 定义窗口：按渠道分区，按日期排序，累计
window_channel_cumulative = (
    Window.partitionBy("channel")
    .orderBy("date")
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)

df_channel_cumulative = (
    df_channel_daily
    .withColumn("channel_cumulative_cost", F.sum("daily_cost").over(window_channel_cumulative))
    .orderBy("channel", "date")
)
df_channel_cumulative.show(15, truncate=False)


# ---------- 三、聚合窗口函数：移动平均 ----------

print("\n" + "-" * 60)
print("三、聚合窗口函数：移动平均")
print("-" * 60)

# 3.1 3日移动平均
print("\n--- 3.1 3日移动平均（前10天）---")

# 定义窗口：按日期排序，当前行往前2行 + 当前行 = 最近3行
window_moving_3d = Window.orderBy("date").rowsBetween(-2, Window.currentRow)

df_moving_avg = (
    df_daily_cost
    .withColumn("moving_avg_3d", F.round(F.avg("daily_cost").over(window_moving_3d), 2))
    .orderBy("date")
)
df_moving_avg.show(10, truncate=False)

print("""
移动平均的写法：
rowsBetween(-(N-1), Window.currentRow)
比如3日移动平均就是 rowsBetween(-2, Window.currentRow)
意思是：当前行往前2行 + 当前行，共3行求平均
""")

# 3.2 7日移动平均
print("\n--- 3.2 7日移动平均（前14天）---")
window_moving_7d = Window.orderBy("date").rowsBetween(-6, Window.currentRow)

df_moving_7d = (
    df_daily_cost
    .withColumn("moving_avg_7d", F.round(F.avg("daily_cost").over(window_moving_7d), 2))
    .orderBy("date")
)
df_moving_7d.show(14, truncate=False)


# ---------- 四、偏移函数：lag、lead（环比必备！） ----------

print("\n" + "-" * 60)
print("四、偏移函数：lag、lead（计算环比）")
print("-" * 60)

# 4.1 lag()：获取前一行的值，计算日环比
print("\n--- 4.1 用 lag() 计算日环比（前10天）---")

# 定义窗口：按日期排序（不需要 rowsBetween）
window_lag = Window.orderBy("date")

df_growth = (
    df_daily_cost
    # 获取昨天的花费（往前1行）
    .withColumn("yesterday_cost", F.lag("daily_cost", 1).over(window_lag))
    # 计算环比增长量 = 今天 - 昨天
    .withColumn("diff", F.col("daily_cost") - F.col("yesterday_cost"))
    # 计算环比增长率 = (今天 - 昨天) / 昨天
    .withColumn("growth_rate", F.round(
        (F.col("daily_cost") - F.col("yesterday_cost")) / F.col("yesterday_cost"), 4
    ))
    .orderBy("date")
)
df_growth.show(10, truncate=False)

print("""
lag() 函数：
- F.lag("列名", 偏移量)：获取往前第N行的值
- 偏移量=1 就是前一行（昨天）
- 第一天没有前一行，所以是 null
这是计算环比的标准方法！
""")

# 4.2 按渠道分别计算环比
print("\n--- 4.2 按渠道分别计算日环比（前15行）---")

window_channel_lag = Window.partitionBy("channel").orderBy("date")

df_channel_growth = (
    df_channel_daily
    .withColumn("yesterday_cost", F.lag("daily_cost", 1).over(window_channel_lag))
    .withColumn("growth_rate", F.round(
        (F.col("daily_cost") - F.col("yesterday_cost")) / F.col("yesterday_cost"), 4
    ))
    .orderBy("channel", "date")
)
df_channel_growth.show(15, truncate=False)

# 4.3 lead()：获取后一行的值
print("\n--- 4.3 用 lead() 获取后一行的值（前10天）---")
df_lead = (
    df_daily_cost
    .withColumn("tomorrow_cost", F.lead("daily_cost", 1).over(window_lag))
    .orderBy("date")
)
df_lead.show(10, truncate=False)
print("lead() 和 lag() 相反，获取的是往后第N行的值")


# ---------- 五、综合实战：渠道排名 + 累计 + 环比 ----------

print("\n" + "-" * 60)
print("五、综合实战：渠道排名 + 累计 + 环比")
print("-" * 60)

print("\n--- 5.1 每个渠道的广告计划花费排名 + 渠道内累计 ---")

# 按渠道+广告计划聚合
df_campaign = (
    df.groupBy("channel", "campaign")
    .agg(
        F.sum("impressions").alias("total_impressions"),
        F.sum("clicks").alias("total_clicks"),
        F.sum("conversions").alias("total_conversions"),
        F.sum("cost").alias("total_cost")
    )
)

# 定义排名窗口
window_campaign_rank = Window.partitionBy("channel").orderBy(F.desc("total_cost"))

# 定义累计窗口（按排名累计）
window_campaign_cumulative = (
    Window.partitionBy("channel")
    .orderBy(F.desc("total_cost"))
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)

df_campaign_analysis = (
    df_campaign
    .withColumn("rank", F.row_number().over(window_campaign_rank))
    .withColumn("cumulative_cost", F.sum("total_cost").over(window_campaign_cumulative))
    .withColumn("ctr", F.round(F.col("total_clicks") / F.col("total_impressions"), 4))
    .withColumn("cvr", F.round(F.col("total_conversions") / F.col("total_clicks"), 4))
    .withColumn("cpa", F.round(F.col("total_cost") / F.col("total_conversions"), 2))
    .orderBy("channel", "rank")
)

print("每个渠道前5个广告计划的分析（排名+累计+指标）：")
df_campaign_analysis.filter(F.col("rank") <= 5).show(30, truncate=False)

print("\n--- 5.2 找出每个渠道花费最高的广告计划 ---")
top1_by_channel = (
    df_campaign_analysis
    .filter(F.col("rank") == 1)
    .select("channel", "campaign", "total_cost", "ctr", "cvr", "cpa")
    .orderBy(F.desc("total_cost"))
)
top1_by_channel.show(truncate=False)


spark.stop()
print("\n>>> 示例代码运行完成！")


# ============================================
# 【练习题】
# ============================================

print("\n" + "=" * 60)
print("【练习题区域】")
print("=" * 60)

# 练习题1：创建 SparkSession，读取广告数据
# 你的代码：



# 练习题2：按渠道+广告计划聚合总花费，然后用 row_number() 给每个渠道内的广告计划按花费降序排名
# 提示：Window.partitionBy("channel").orderBy(F.desc("total_cost"))
# 你的代码：
df_total = df.groupBy("channel", "campaign").agg(F.sum("cost").alias("total_cost"))
window_spec = (Window.partitionBy("channel").orderBy(F.desc("total_cost")))
df_total.withColumn("rank", F.row_number().over(window_spec))
# 练习题3：在练习题2的基础上，筛选出每个渠道花费 Top 3 的广告计划
# 提示：filter(F.col("rn") <= 3)
# 你的代码：



# 练习题4：按日期聚合每天的总花费，然后计算累计花费（从第一天到当天的累计）
# 提示：rowsBetween(Window.unboundedPreceding, Window.currentRow)
# 你的代码：



# 练习题5：按日期聚合每天的总花费，计算3日移动平均
# 提示：rowsBetween(-2, Window.currentRow)
# 你的代码：



# 练习题6：按日期聚合每天的总花费，用 lag() 获取昨天的花费，然后计算日环比增长率
# 提示：F.lag("daily_cost", 1).over(Window.orderBy("date"))
# 你的代码：



# 练习题7：按渠道+日期聚合每天的花费，然后按渠道分别计算日环比
# 提示：Window.partitionBy("channel").orderBy("date")
# 你的代码：



# 练习题8：按渠道聚合总花费，然后用 dense_rank() 给所有渠道按总花费排名
# 你的代码：



# 练习题9：按地区聚合总花费，计算累计花费（按总花费降序排列后累计），
#           然后筛选出累计花费占总花费80%的地区（帕累托分析）
# 提示：先算总花费，再算累计占比 = 累计花费 / 总花费
# 你的代码：



# 练习题10：综合题：按渠道+广告计划聚合，计算总花费、CTR、CVR、CPA，
#            然后给每个渠道内的广告计划按花费排名，取每个渠道 Top 5，
#            同时计算渠道内累计花费和累计花费占比，最后按渠道+排名排序显示
# 你的代码：



print("\n>>> 练习题完成！")
