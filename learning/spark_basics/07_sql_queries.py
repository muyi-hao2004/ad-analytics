# ============================================
# Spark 基础练习 07：用 SQL 写 Spark 查询
# ============================================
#
# 【练习目标】
# 1. 学会把 DataFrame 注册成临时视图
# 2. 学会用 spark.sql() 执行 SQL 查询
# 3. 用 SQL 实现 select、filter、groupBy、join、窗口函数
# 4. 理解 SQL 和 DataFrame API 的区别和选择
#
# 【知识点】
# - createOrReplaceTempView：注册临时视图
# - spark.sql()：执行 SQL 语句
# - SQL 实现各种查询操作
# - Spark SQL 和 MySQL SQL 的区别
# ============================================


from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("Practice07_SQLQueries")
    .master("local[*]")
    .getOrCreate()
)

# 读取广告数据
df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

print("=" * 60)
print("数据读取成功，共", df.count(), "行")
print("=" * 60)


# ---------- 一、基础：注册临时视图 + 简单 SQL 查询 ----------

print("\n" + "-" * 60)
print("一、基础：注册临时视图 + 简单 SQL 查询")
print("-" * 60)

# 1.1 把 DataFrame 注册成临时视图
print("\n--- 1.1 注册临时视图 ---")
df.createOrReplaceTempView("ad_data")
print("已把 df 注册成临时视图 ad_data")
print("现在可以用 SQL 查询它了！")

# 1.2 最简单的 SQL 查询
print("\n--- 1.2 最简单的 SQL 查询（前10行）---")
result = spark.sql("SELECT * FROM ad_data LIMIT 10")
result.show(10, truncate=False)

# 1.3 选择指定列
print("\n--- 1.3 选择指定列 ---")
result = spark.sql("""
    SELECT date, channel, campaign, cost, clicks, conversions
    FROM ad_data
    LIMIT 10
""")
result.show(10, truncate=False)


# ---------- 二、筛选（WHERE） ----------

print("\n" + "-" * 60)
print("二、筛选（WHERE）")
print("-" * 60)

# 2.1 单条件筛选
print("\n--- 2.1 单条件筛选：花费大于1000 ---")
result = spark.sql("""
    SELECT date, channel, campaign, cost
    FROM ad_data
    WHERE cost > 1000
    ORDER BY cost DESC
    LIMIT 10
""")
result.show(10, truncate=False)

# 2.2 多条件筛选
print("\n--- 2.2 多条件筛选：Facebook 且花费大于1000 ---")
result = spark.sql("""
    SELECT date, channel, campaign, cost, clicks
    FROM ad_data
    WHERE channel = 'Facebook'
      AND cost > 1000
      AND clicks > 100
    ORDER BY cost DESC
    LIMIT 10
""")
result.show(10, truncate=False)

# 2.3 IN / NOT IN
print("\n--- 2.3 IN 筛选：Facebook 或 Google ---")
result = spark.sql("""
    SELECT channel, campaign, cost
    FROM ad_data
    WHERE channel IN ('Facebook', 'Google')
    ORDER BY cost DESC
    LIMIT 10
""")
result.show(10, truncate=False)

# 2.4 LIKE 模糊匹配
print("\n--- 2.4 LIKE 模糊匹配：campaign 包含 'summer' ---")
result = spark.sql("""
    SELECT channel, campaign, cost
    FROM ad_data
    WHERE campaign LIKE '%summer%'
    ORDER BY cost DESC
    LIMIT 10
""")
result.show(10, truncate=False)


# ---------- 三、分组聚合（GROUP BY） ----------

print("\n" + "-" * 60)
print("三、分组聚合（GROUP BY）")
print("-" * 60)

# 3.1 按渠道分组聚合
print("\n--- 3.1 按渠道分组：总花费、总点击、平均CPA ---")
result = spark.sql("""
    SELECT
        channel,
        COUNT(*) as campaign_count,
        SUM(cost) as total_cost,
        SUM(clicks) as total_clicks,
        SUM(conversions) as total_conversions,
        ROUND(SUM(clicks) / SUM(impressions), 4) as ctr,
        ROUND(SUM(conversions) / SUM(clicks), 4) as cvr,
        ROUND(SUM(cost) / SUM(conversions), 2) as cpa
    FROM ad_data
    GROUP BY channel
    ORDER BY total_cost DESC
""")
result.show(truncate=False)

# 3.2 按渠道+地区分组
print("\n--- 3.2 按渠道+地区分组 ---")
result = spark.sql("""
    SELECT
        channel,
        region,
        COUNT(*) as count,
        SUM(cost) as total_cost,
        ROUND(AVG(cost), 2) as avg_cost
    FROM ad_data
    GROUP BY channel, region
    ORDER BY channel, total_cost DESC
    LIMIT 20
""")
result.show(20, truncate=False)

# 3.3 分组后筛选（HAVING）
print("\n--- 3.3 分组后筛选（HAVING）：总花费大于10000的渠道 ---")
result = spark.sql("""
    SELECT
        channel,
        SUM(cost) as total_cost,
        COUNT(*) as count
    FROM ad_data
    GROUP BY channel
    HAVING SUM(cost) > 10000
    ORDER BY total_cost DESC
""")
result.show(truncate=False)


# ---------- 四、多表关联（JOIN） ----------

print("\n" + "-" * 60)
print("四、多表关联（JOIN）")
print("-" * 60)

# 4.1 创建一张渠道维度表
print("\n--- 4.1 创建渠道维度表 ---")
channel_dim_data = [
    ("Facebook", "社交平台", "张三"),
    ("Google", "搜索平台", "李四"),
    ("TikTok", "短视频平台", "王五"),
    ("YouTube", "视频平台", "赵六"),
    ("Instagram", "图片社交平台", "钱七")
]
df_channel_dim = spark.createDataFrame(channel_dim_data, ["channel", "channel_type", "owner"])
df_channel_dim.createOrReplaceTempView("channel_dim")
df_channel_dim.show(truncate=False)

# 4.2 左连接（LEFT JOIN）
print("\n--- 4.2 左连接：广告数据 + 渠道维度 ---")
result = spark.sql("""
    SELECT
        a.date,
        a.channel,
        a.campaign,
        a.cost,
        c.channel_type,
        c.owner
    FROM ad_data a
    LEFT JOIN channel_dim c
        ON a.channel = c.channel
    WHERE a.cost > 2000
    ORDER BY a.cost DESC
    LIMIT 15
""")
result.show(15, truncate=False)

# 4.3 内连接（INNER JOIN）
print("\n--- 4.3 内连接：只保留有维度信息的渠道 ---")
result = spark.sql("""
    SELECT
        a.channel,
        c.channel_type,
        SUM(a.cost) as total_cost
    FROM ad_data a
    INNER JOIN channel_dim c
        ON a.channel = c.channel
    GROUP BY a.channel, c.channel_type
    ORDER BY total_cost DESC
""")
result.show(truncate=False)


# ---------- 五、窗口函数（SQL 写法） ----------

print("\n" + "-" * 60)
print("五、窗口函数（SQL 写法）")
print("-" * 60)

# 先按渠道+广告计划聚合
df_campaign = spark.sql("""
    SELECT
        channel,
        campaign,
        SUM(cost) as total_cost,
        SUM(clicks) as total_clicks,
        SUM(conversions) as total_conversions
    FROM ad_data
    GROUP BY channel, campaign
""")
df_campaign.createOrReplaceTempView("campaign_cost")

# 5.1 排名（ROW_NUMBER）
print("\n--- 5.1 每个渠道内按花费排名 ---")
result = spark.sql("""
    SELECT
        channel,
        campaign,
        total_cost,
        ROW_NUMBER() OVER (PARTITION BY channel ORDER BY total_cost DESC) as rn,
        RANK() OVER (PARTITION BY channel ORDER BY total_cost DESC) as rank,
        DENSE_RANK() OVER (PARTITION BY channel ORDER BY total_cost DESC) as dense_rank
    FROM campaign_cost
    ORDER BY channel, rn
    LIMIT 20
""")
result.show(20, truncate=False)

# 5.2 取每个渠道 Top 3
print("\n--- 5.2 取每个渠道花费 Top 3 ---")
result = spark.sql("""
    SELECT *
    FROM (
        SELECT
            channel,
            campaign,
            total_cost,
            ROW_NUMBER() OVER (PARTITION BY channel ORDER BY total_cost DESC) as rn
        FROM campaign_cost
    ) t
    WHERE rn <= 3
    ORDER BY channel, rn
""")
result.show(20, truncate=False)

# 5.3 累计求和
print("\n--- 5.3 按日期累计总花费 ---")
df_daily = spark.sql("""
    SELECT date, SUM(cost) as daily_cost
    FROM ad_data
    GROUP BY date
    ORDER BY date
""")
df_daily.createOrReplaceTempView("daily_cost")

result = spark.sql("""
    SELECT
        date,
        daily_cost,
        SUM(daily_cost) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as cumulative_cost
    FROM daily_cost
    ORDER BY date
    LIMIT 15
""")
result.show(15, truncate=False)

# 5.4 环比（LAG）
print("\n--- 5.4 日环比增长率 ---")
result = spark.sql("""
    SELECT
        date,
        daily_cost,
        LAG(daily_cost, 1) OVER (ORDER BY date) as yesterday_cost,
        ROUND((daily_cost - LAG(daily_cost, 1) OVER (ORDER BY date)) / LAG(daily_cost, 1) OVER (ORDER BY date), 4) as growth_rate
    FROM daily_cost
    ORDER BY date
    LIMIT 15
""")
result.show(15, truncate=False)


# ---------- 六、子查询和常用函数 ----------

print("\n" + "-" * 60)
print("六、子查询和常用函数")
print("-" * 60)

# 6.1 常用字符串函数
print("\n--- 6.1 常用字符串函数 ---")
result = spark.sql("""
    SELECT
        campaign,
        UPPER(campaign) as upper_campaign,
        LOWER(campaign) as lower_campaign,
        LENGTH(campaign) as length,
        SUBSTRING(campaign, 1, 10) as first_10_chars,
        CONCAT(channel, '_', campaign) as channel_campaign
    FROM ad_data
    LIMIT 5
""")
result.show(5, truncate=False)

# 6.2 常用日期函数
print("\n--- 6.2 常用日期函数 ---")
result = spark.sql("""
    SELECT
        date,
        YEAR(date) as year,
        MONTH(date) as month,
        DAY(date) as day,
        DATE_FORMAT(date, 'yyyy-MM') as year_month,
        DATE_ADD(date, 7) as date_plus_7,
        DATEDIFF('2026-09-30', date) as days_to_end
    FROM ad_data
    LIMIT 5
""")
result.show(5, truncate=False)

# 6.3 条件函数（CASE WHEN）
print("\n--- 6.3 条件函数（CASE WHEN）：花费分级 ---")
result = spark.sql("""
    SELECT
        channel,
        campaign,
        cost,
        CASE
            WHEN cost > 3000 THEN '高花费'
            WHEN cost > 1000 THEN '中花费'
            ELSE '低花费'
        END as cost_level
    FROM ad_data
    ORDER BY cost DESC
    LIMIT 10
""")
result.show(10, truncate=False)

# 6.4 去重
print("\n--- 6.4 去重（DISTINCT）---")
result = spark.sql("""
    SELECT DISTINCT channel
    FROM ad_data
    ORDER BY channel
""")
result.show(truncate=False)


spark.stop()
print("\n>>> 示例代码运行完成！")


# ============================================
# 【练习题】
# ============================================

print("\n" + "=" * 60)
print("【练习题区域】")
print("=" * 60)

# 练习题1：创建 SparkSession，读取广告数据，注册成临时视图 ad_data
# 你的代码：



# 练习题2：用 SQL 查询：花费大于1000的 Facebook 广告，按花费降序，取前10条
# 你的代码：



# 练习题3：用 SQL 按渠道分组，计算每个渠道的总花费、总点击、CTR、CVR、CPA，按总花费降序
# 你的代码：



# 练习题4：用 SQL 按渠道+地区分组，计算总花费，然后用 HAVING 筛选总花费大于5000的组
# 你的代码：



# 练习题5：创建一张渠道维度表（channel, channel_type, owner），注册成临时视图，
#           然后用 LEFT JOIN 把广告数据和渠道维度表关联起来
# 你的代码：



# 练习题6：用 SQL 窗口函数 ROW_NUMBER()，给每个渠道内的广告计划按花费降序排名，
#           然后筛选出每个渠道 Top 3 的广告计划
# 你的代码：



# 练习题7：用 SQL 按日期聚合每天的总花费，然后用窗口函数计算累计花费（从第一天到当天）
# 提示：SUM(daily_cost) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
# 你的代码：



# 练习题8：用 SQL 按日期聚合每天的总花费，然后用 LAG() 计算日环比增长率
# 你的代码：



# 练习题9：用 SQL 的 CASE WHEN，把花费分成高（>3000）、中（1000-3000）、低（<1000）三档，
#           然后按花费分级统计数量和总花费
# 你的代码：



# 练习题10：综合题：用 SQL 实现完整的广告分析报表
#           - 按渠道分组，计算总花费、总点击、总转化、CTR、CVR、CPA
#           - 用 RANK() 给渠道按总花费排名
#           - 计算每个渠道的花费占比（渠道总花费 / 全部总花费）
#           - 按总花费降序排列
# 你的代码：



print("\n>>> 练习题完成！")
