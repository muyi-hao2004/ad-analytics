# ============================================
# Spark 基础练习 02：select 选择列 + filter 筛选行
# ============================================
#
# 【练习目标】
# 1. 熟练使用 select() 选择列
# 2. 熟练使用 filter() / where() 筛选行
# 3. 学会多条件筛选
# 4. 学会 F.col() 引用列
#
# 【知识点】
# - df.select()：选择指定列，相当于 SQL 的 SELECT
# - df.filter() / df.where()：筛选行，相当于 SQL 的 WHERE
# - F.col()：引用列（推荐写法）
# - 多条件：&（与）、|（或）、~（非），每个条件加括号
# ============================================


from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("Practice02_SelectFilter")
    .master("local[*]")
    .getOrCreate()
)

df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

print("=" * 60)
print("数据读取成功，共", df.count(), "行")
print("=" * 60)


# ---------- 一、select() 选择列 ----------

print("\n" + "-" * 60)
print("一、select() 选择列")
print("-" * 60)

# 1.1 选择单列
print("\n--- 1.1 选择单列：只看 channel 列，前5行 ---")
df.select("channel").show(n=5)

# 1.2 选择多列
print("\n--- 1.2 选择多列：date, channel, cost，前5行 ---")
df.select("date", "channel", "cost").show(n=5)

# 1.3 用 F.col() 选择列（推荐写法）
print("\n--- 1.3 用 F.col() 选择列 ---")
df.select(F.col("date"), F.col("channel"), F.col("cost")).show(n=5)

# 1.4 选择列时做简单计算
print("\n--- 1.4 选择列时做计算：cost 翻倍，前5行 ---")
df.select("channel", F.col("cost"), (F.col("cost") * 2).alias("cost_double")).show(n=5)

# 1.5 选择所有列
print("\n--- 1.5 选择所有列（和直接 show 一样）---")
df.select("*").show(n=3)


# ---------- 二、filter() / where() 筛选行 ----------

print("\n" + "-" * 60)
print("二、filter() / where() 筛选行")
print("-" * 60)

# 2.1 单条件筛选：等于
print("\n--- 2.1 筛选 channel = 'Facebook' 的数据，前5行 ---")
df.filter(F.col("channel") == "Facebook").show(n=5)

# 2.2 单条件筛选：大于 / 小于
print("\n--- 2.2 筛选 cost > 5000 的数据，前5行 ---")
df.filter(F.col("cost") > 5000).show(n=5)

# 2.3 单条件筛选：大于等于 / 小于等于
print("\n--- 2.3 筛选 impressions >= 100000 的数据，前5行 ---")
df.filter(F.col("impressions") >= 100000).show(n=5)

# 2.4 单条件筛选：不等于
print("\n--- 2.4 筛选 channel != 'Google' 的数据，前5行 ---")
df.filter(F.col("channel") != "Google").show(n=5)

# 2.5 where() 和 filter() 完全一样
print("\n--- 2.5 用 where() 筛选（和 filter 一样）---")
df.where(F.col("channel") == "TikTok").show(n=3)

# 2.6 用 SQL 表达式字符串筛选（接近 SQL 写法）
print("\n--- 2.6 用 SQL 表达式字符串筛选 ---")
df.filter("channel = 'YouTube' AND cost > 1000").show(n=3)


# ---------- 三、多条件筛选 ----------

print("\n" + "-" * 60)
print("三、多条件筛选")
print("-" * 60)

# 重要！！！
# 多条件筛选注意事项：
# 1. 用 & 表示"与"，用 | 表示"或"，用 ~ 表示"非"
# 2. 每个条件必须用括号括起来！
# 3. 不能用 Python 的 and / or / not

# 3.1 与（&）：两个条件都满足
print("\n--- 3.1 与：channel='Facebook' 且 cost>3000，前5行 ---")
df.filter(
    (F.col("channel") == "Facebook") &
    (F.col("cost") > 3000)
).show(n=5)

# 3.2 或（|）：满足任意一个条件
print("\n--- 3.2 或：channel='Facebook' 或 channel='Google'，前5行 ---")
df.filter(
    (F.col("channel") == "Facebook") |
    (F.col("channel") == "Google")
).show(n=5)

# 3.3 非（~）：不满足条件
print("\n--- 3.3 非：不是 Mobile 设备，前5行 ---")
df.filter(~(F.col("device") == "Mobile")).show(n=5)

# 3.4 三个条件组合
print("\n--- 3.4 三条件：channel='Google' 且 region='华南' 且 device='Mobile'，前5行 ---")
df.filter(
    (F.col("channel") == "Google") &
    (F.col("region") == "华南") &
    (F.col("device") == "Mobile")
).show(n=5)


# ---------- 四、其他常用筛选方式 ----------

print("\n" + "-" * 60)
print("四、其他常用筛选方式")
print("-" * 60)

# 4.1 isin()：在某个列表中
print("\n--- 4.1 isin：channel 在 ['Facebook', 'Google', 'TikTok'] 中，前5行 ---")
df.filter(F.col("channel").isin(["Facebook", "Google", "TikTok"])).show(n=5)

# 4.2 like()：模糊匹配（SQL 的 LIKE）
# % 表示任意字符，_ 表示单个字符
print("\n--- 4.2 like：campaign 包含'大促'，前5行 ---")
df.filter(F.col("campaign").like("%大促%")).show(n=5)

# 4.3 startswith() / endswith()：以...开头/结尾
print("\n--- 4.3 startswith：campaign 以'双11'开头，前5行 ---")
df.filter(F.col("campaign").startswith("双11")).show(n=5)

# 4.4 isnull() / isnotnull()：是否为空
print("\n--- 4.4 isnull：cost 为空的数据（如果有的话）---")
df.filter(F.col("cost").isNull()).show(n=5)
print("空值数量:", df.filter(F.col("cost").isNull()).count())

# 4.5 between()：在某个范围内
print("\n--- 4.5 between：cost 在 1000 到 2000 之间，前5行 ---")
df.filter(F.col("cost").between(1000, 2000)).show(n=5)


# ---------- 五、select + filter 组合使用 ----------

print("\n" + "-" * 60)
print("五、select + filter 组合使用")
print("-" * 60)

# 先筛选，再选择列（推荐：先筛选减少数据量，再选择列）
print("\n--- 5.1 先筛选 Facebook，再选择 date, campaign, cost，前5行 ---")
df.filter(F.col("channel") == "Facebook").select("date", "campaign", "cost").show(n=5)

# 先选择列，再筛选（也可以，但先筛选更好）
print("\n--- 5.2 先选择列，再筛选 ---")
df.select("date", "channel", "campaign", "cost").filter(F.col("channel") == "Facebook").show(n=5)

# 统计筛选后的数据量
print("\n--- 5.3 统计筛选后的数据量 ---")
facebook_count = df.filter(F.col("channel") == "Facebook").count()
print(f"Facebook 渠道共有 {facebook_count} 条数据")

high_cost_count = df.filter(F.col("cost") > 5000).count()
print(f"花费大于5000的共有 {high_cost_count} 条数据")


spark.stop()
print("\n>>> 示例代码运行完成！")


# ============================================
# 【练习题】
# 请新建一个 SparkSession，读取数据，完成以下练习
# ============================================

print("\n" + "=" * 60)
print("【练习题区域】")
print("=" * 60)

# 练习题1：创建 SparkSession，读取 CSV 数据
# 你的代码：



# 练习题2：选择 date, channel, region, device, impressions, clicks, cost 这7列，显示前10行
# 你的代码：



# 练习题3：筛选 channel = 'Google' 的所有数据，显示前5行，并统计有多少条
# 你的代码：



# 练习题4：筛选 cost > 8000 的数据，只显示 campaign, cost 两列，前10行
# 你的代码：



# 练习题5：筛选 region = '华南' 且 device = 'Mobile' 的数据，显示前5行
# 你的代码：



# 练习题6：筛选 channel 是 Facebook 或 TikTok 的数据，显示前5行（用 | 或 isin 都可以）
# 你的代码：



# 练习题7：筛选 campaign 包含'新品'的数据，显示前5行（用 like）
# 你的代码：



# 练习题8：筛选 impressions 在 50000 到 100000 之间的数据，显示前5行（用 between）
# 你的代码：



# 练习题9：筛选 channel != 'YouTube' 且 conversions > 100 的数据，统计有多少条
# 你的代码：



# 练习题10：先筛选 cost > 3000，再选择 channel, campaign, cost，按 cost 降序显示前10行
# 提示：排序用 .orderBy(F.col("cost").desc())，后面会详细讲
# 你的代码：



print("\n>>> 练习题完成！")
