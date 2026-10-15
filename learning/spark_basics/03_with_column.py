# ============================================
# Spark 基础练习 03：withColumn 新增/修改列 + 常用函数
# ============================================
#
# 【练习目标】
# 1. 熟练使用 withColumn() 新增/修改列
# 2. 掌握常用字符串函数、日期函数、数值函数
# 3. 熟练使用 when/otherwise 条件函数
# 4. 学会处理除以0、空值等边界情况
#
# 【知识点】
# - df.withColumn("列名", 表达式)：新增/修改列
# - df.drop("列名")：删除列
# - df.withColumnRenamed("旧名", "新名")：重命名列
# - F.lit(值)：创建常量列
# - 字符串函数：concat、substring、split、regexp_replace、lower、upper、length
# - 日期函数：to_date、date_format、year、month、datediff、date_add、current_date
# - 数值函数：round、abs、ceil、floor
# - 条件函数：when/otherwise、coalesce、isnull、isnotnull
# ============================================
from pymysql.converters import conversions
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("Practice03_WithColumn")
    .master("local[*]")
    .getOrCreate()
)

df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

print("=" * 60)
print("数据读取成功，共", df.count(), "行")
print("=" * 60)


# ---------- 一、withColumn 基本用法 ----------

print("\n" + "-" * 60)
print("一、withColumn 基本用法")
print("-" * 60)

# 1.1 新增一列：简单计算
print("\n--- 1.1 新增列：花费翻倍，前5行 ---")
df.withColumn("cost_double", F.col("cost") * 2).select("channel", "cost", "cost_double").show(n=5)

# 1.2 新增一列：计算 CTR
print("\n--- 1.2 新增列：CTR = 点击/曝光，前5行 ---")
df.withColumn("CTR", F.col("clicks") / F.col("impressions")).select("channel", "clicks", "impressions", "CTR").show(n=5)

# 1.3 修改已有列：把 cost 保留2位小数
print("\n--- 1.3 修改列：cost 保留2位小数，前5行 ---")
df.withColumn("cost", F.round(F.col("cost"), 2)).select("channel", "cost").show(n=5)

# 1.4 新增常量列
print("\n--- 1.4 新增常量列，前5行 ---")
df.withColumn("year", F.lit("2026")).withColumn("constant", F.lit(100)).select("channel", "year", "constant").show(n=5)

# 1.5 链式调用：一次新增多列
print("\n--- 1.5 链式调用：一次新增 CTR、CVR、CPA，前5行 ---")
df_multi = (
    df
    .withColumn("CTR", F.round(F.col("clicks") / F.col("impressions"), 4))
    .withColumn("CVR", F.round(F.col("conversions") / F.col("clicks"), 4))
    .withColumn("CPA", F.round(F.col("cost") / F.col("conversions"), 2))
)
df_multi.select("channel", "CTR", "CVR", "CPA").show(n=5)


# ---------- 二、字符串函数 ----------

print("\n" + "-" * 60)
print("二、字符串函数")
print("-" * 60)

# 2.1 concat：拼接字符串
print("\n--- 2.1 concat：拼接 channel 和 campaign，前5行 ---")
df.withColumn(
    "channel_campaign",
    F.concat(F.col("channel"), F.lit("_"), F.col("campaign"))
).select("channel", "campaign", "channel_campaign").show(n=5, truncate=False)

# 2.2 substring：截取字符串（起始位置是1！）
print("\n--- 2.2 substring：截取年份和月份，前5行 ---")
df.withColumn("year", F.substring(F.col("date"), 1, 4)) \
  .withColumn("month", F.substring(F.col("date"), 6, 2)) \
  .select("date", "year", "month").show(n=5)

# 2.3 split：分割字符串
print("\n--- 2.3 split：把 campaign 按下划线分割，前5行 ---")
df.withColumn("campaign_parts", F.split(F.col("campaign"), "_")) \
  .withColumn("campaign_type", F.split(F.col("campaign"), "_")[0]) \
  .select("campaign", "campaign_parts", "campaign_type").show(n=5, truncate=False)

# 2.4 regexp_replace：正则替换
print("\n--- 2.4 regexp_replace：把 _ 替换成 -，前5行 ---")
df.withColumn("campaign_new", F.regexp_replace(F.col("campaign"), "_", "-")) \
  .select("campaign", "campaign_new").show(n=5, truncate=False)

# 2.5 lower / upper：转小写/大写
print("\n--- 2.5 lower / upper，前5行 ---")
df.withColumn("channel_lower", F.lower(F.col("channel"))) \
  .withColumn("channel_upper", F.upper(F.col("channel"))) \
  .select("channel", "channel_lower", "channel_upper").show(n=5)

# 2.6 length：字符串长度
print("\n--- 2.6 length：campaign 长度，前5行 ---")
df.withColumn("campaign_length", F.length(F.col("campaign"))) \
  .select("campaign", "campaign_length").show(n=5, truncate=False)


# ---------- 三、日期函数 ----------

print("\n" + "-" * 60)
print("三、日期函数")
print("-" * 60)

# 3.1 date_format：日期格式化
print("\n--- 3.1 date_format：格式化成 年-月，前5行 ---")
df.withColumn("year_month", F.date_format(F.col("date"), "yyyy-MM")) \
  .select("date", "year_month").show(n=5)

# 3.2 year / month / dayofmonth：取年/月/日
print("\n--- 3.2 year / month / day，前5行 ---")
df.withColumn("year", F.year(F.col("date"))) \
  .withColumn("month", F.month(F.col("date"))) \
  .withColumn("day", F.dayofmonth(F.col("date"))) \
  .select("date", "year", "month", "day").show(n=5)

# 3.3 datediff：两个日期相差多少天
print("\n--- 3.3 datediff：距离今天多少天，前5行 ---")
df.withColumn("days_ago", F.datediff(F.current_date(), F.col("date"))) \
  .select("date", "days_ago").show(n=5)

# 3.4 date_add / date_sub：日期加/减几天
print("\n--- 3.4 date_add：日期加7天，前5行 ---")
df.withColumn("date_plus_7", F.date_add(F.col("date"), 7)) \
  .select("date", "date_plus_7").show(n=5)

# 3.5 current_date：当前日期
print("\n--- 3.5 current_date：当前日期 ---")
df.withColumn("today", F.current_date()).select("today").show(n=1)


# ---------- 四、数值函数 ----------

print("\n" + "-" * 60)
print("四、数值函数")
print("-" * 60)

# 4.1 round：四舍五入
print("\n--- 4.1 round：保留2位小数，前5行 ---")
df.withColumn("cost_2dec", F.round(F.col("cost"), 2)) \
  .select("cost", "cost_2dec").show(n=5)

# 4.2 abs：绝对值
print("\n--- 4.2 abs：绝对值，前5行 ---")
df.withColumn("cost_abs", F.abs(F.col("cost"))).select("cost", "cost_abs").show(n=5)

# 4.3 ceil / floor：向上/向下取整
print("\n--- 4.3 ceil / floor，前5行 ---")
df.withColumn("cost_ceil", F.ceil(F.col("cost"))) \
  .withColumn("cost_floor", F.floor(F.col("cost"))) \
  .select("cost", "cost_ceil", "cost_floor").show(n=5)

# 4.4 直接用运算符
print("\n--- 4.4 直接用运算符：+ - * /，前5行 ---")
df.withColumn("cost_double", F.col("cost") * 2) \
  .withColumn("ctr", F.col("clicks") / F.col("impressions")) \
  .select("cost", "cost_double", "clicks", "impressions", "ctr").show(n=5)


# ---------- 五、条件函数（重点！） ----------

print("\n" + "-" * 60)
print("五、条件函数（重点！）")
print("-" * 60)

# 5.1 when / otherwise：简单二分类
print("\n--- 5.1 when/otherwise：花费>500 标'高花费'，否则'低花费'，前5行 ---")
df.withColumn(
    "cost_level",
    F.when(F.col("cost") > 500, "高花费")
     .otherwise("低花费")
).select("channel", "cost", "cost_level").show(n=5)

# 5.2 多条件分支
print("\n--- 5.2 多条件：花费>5000'很高'，>1000'高'，>500'中'，否则'低'，前10行 ---")
df.withColumn(
    "cost_level",
    F.when(F.col("cost") > 5000, "很高")
     .when(F.col("cost") > 1000, "高")
     .when(F.col("cost") > 500, "中")
     .otherwise("低")
).select("channel", "cost", "cost_level").show(n=10)

# 5.3 条件里用 & / | 组合
print("\n--- 5.3 多条件组合：花费>3000 且 转化>100 标'高价值'，前10行 ---")
df.withColumn(
    "is_high_value",
    F.when(
        (F.col("cost") > 3000) & (F.col("conversions") > 100),
        "高价值"
    ).otherwise("普通")
).select("channel", "cost", "conversions", "is_high_value").show(n=10)

# 5.4 处理除以0：先判断再除
print("\n--- 5.4 处理除以0：曝光>0才计算CTR，否则为0，前5行 ---")
df.withColumn(
    "CTR_safe",
    F.when(F.col("impressions") > 0, F.round(F.col("clicks") / F.col("impressions"), 4))
     .otherwise(0)
).select("channel", "impressions", "clicks", "CTR_safe").show(n=5)

# 5.5 coalesce：取第一个非空值
print("\n--- 5.5 coalesce：如果cost为空用0代替（演示），前5行 ---")
df.withColumn("cost_filled", F.coalesce(F.col("cost"), F.lit(0))) \
  .select("cost", "cost_filled").show(n=5)

# 5.6 isnull / isnotnull：判断是否为空
print("\n--- 5.6 isnull：标记cost是否为空 ---")
df.withColumn("is_cost_null", F.isnull(F.col("cost"))) \
  .groupBy("is_cost_null").count().show()

# 5.7 用 F.expr() 写 SQL 表达式
print("\n--- 5.7 F.expr()：直接写SQL表达式，前5行 ---")
df.withColumn("ctr_sql", F.expr("clicks / impressions")) \
  .withColumn(
      "cost_level_sql",
      F.expr("CASE WHEN cost > 500 THEN '高' ELSE '低' END")
  ).select("channel", "cost", "ctr_sql", "cost_level_sql").show(n=5)


# ---------- 六、删除列、重命名列 ----------

print("\n" + "-" * 60)
print("六、删除列、重命名列")
print("-" * 60)

# 6.1 drop：删除列
print("\n--- 6.1 drop：删除列后查看列名 ---")
df_dropped = df.drop("region", "device")
print("原列名:", df.columns)
print("删除后列名:", df_dropped.columns)

# 6.2 withColumnRenamed：重命名列
print("\n--- 6.2 withColumnRenamed：重命名列 ---")
df_renamed = df.withColumnRenamed("cost", "spend").withColumnRenamed("impressions", "imps")
print("原列名:", df.columns)
print("重命名后列名:", df_renamed.columns)


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



# 练习题2：新增一列 CTR_safe，要求：曝光>0时计算 clicks/impressions 并保留4位小数，否则为0
# 你的代码：
df.withColumn("CTR_safe", F.when(
    F.col("impressions") > 0, F.round(F.col("clicks")/F.col("impressions"), 4)
).otherwise(0))


# 练习题3：新增一列 CVR = conversions/clicks，保留4位小数，注意处理 clicks=0 的情况
# 你的代码：
df.withColumn("CVR", F.when(F.col("clicks") > 0, F.round(F.col("conversions")/F.col("clicks"), 4)).otherwise(0))


# 练习题4：新增一列 CPA = cost/conversions，保留2位小数，注意处理 conversions=0 的情况
# 你的代码：



# 练习题5：新增一列 year_month，把 date 格式化成 "yyyy-MM" 格式
# 你的代码：



# 练习题6：新增一列 channel_campaign，把 channel 和 campaign 用 "_" 拼接起来
# 你的代码：



# 练习题7：新增一列 cost_level，按花费分级：>5000为"很高"，>1000为"高"，>500为"中"，否则为"低"
# 你的代码：
df.withColumn("cost_level", F.when(F.col("cost") > 5000, "很高").when(F.col("cost") > 1000, "高")
              .when(F.col("cost") > 500, "中").otherwise("低"))


# 练习题8：新增一列 is_abnormal，判断：花费>0 且 曝光=0 时标"异常"，否则标"正常"
# 你的代码：



# 练习题9：新增一列 campaign_type，把 campaign 按下划线分割，取第1个元素
# 提示：F.split(F.col("campaign"), "_")[0]
# 你的代码：



# 练习题10：把上面所有新增的列都加上，然后选择 date, year_month, channel, campaign, channel_campaign,
#           impressions, clicks, conversions, cost, CTR_safe, CVR, CPA, cost_level, is_abnormal, campaign_type
#           显示前10行
# 你的代码：



# 练习题11：把 cost 列重命名为 spend，然后删除 region 和 device 列
# 你的代码：



# 练习题12：统计不同 cost_level 的数量，按数量降序排列
# 提示：用 groupBy + count + orderBy
# 你的代码：



print("\n>>> 练习题完成！")
