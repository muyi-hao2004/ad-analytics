import pandas as pd

import pandas as pd

# 直接构造数据
data = [
    {"date": "2026-09-01", "channel": "Facebook", "cost": 1000, "impressions": 50000, "clicks": 1000, "conversions": 50},
    {"date": "2026-09-02", "channel": "Facebook", "cost": 1200, "impressions": 60000, "clicks": 1200, "conversions": 55},
    {"date": "2026-09-03", "channel": "Facebook", "cost": 1100, "impressions": 55000, "clicks": 1150, "conversions": 45},
    {"date": "2026-09-01", "channel": "Google", "cost": 1500, "impressions": 75000, "clicks": 1500, "conversions": 80},
    {"date": "2026-09-02", "channel": "Google", "cost": 1800, "impressions": 90000, "clicks": 1800, "conversions": 95},
    {"date": "2026-09-03", "channel": "Google", "cost": 1600, "impressions": 80000, "clicks": 1600, "conversions": 85},
    {"date": "2026-09-01", "channel": "TikTok", "cost": 800, "impressions": 80000, "clicks": 2400, "conversions": 30},
    {"date": "2026-09-02", "channel": "TikTok", "cost": 900, "impressions": 90000, "clicks": 2700, "conversions": 35},
    {"date": "2026-09-03", "channel": "TikTok", "cost": 850, "impressions": 85000, "clicks": 2550, "conversions": 32},
    {"date": "2026-09-01", "channel": "YouTube", "cost": 1200, "impressions": 40000, "clicks": 800, "conversions": 60},
    {"date": "2026-09-02", "channel": "YouTube", "cost": 1300, "impressions": 43000, "clicks": 860, "conversions": 65},
    {"date": "2026-09-03", "channel": "YouTube", "cost": 1250, "impressions": 42000, "clicks": 840, "conversions": 62},
    {"date": "2026-09-01", "channel": "Instagram", "cost": 600, "impressions": 30000, "clicks": 600, "conversions": 20},
    {"date": "2026-09-02", "channel": "Instagram", "cost": 700, "impressions": 35000, "clicks": 700, "conversions": 22},
    {"date": "2026-09-03", "channel": "Instagram", "cost": 650, "impressions": 32500, "clicks": 650, "conversions": 18},
]

df = pd.DataFrame(data)

df1 = df[(df["date"] == "2026-09-02") & (df["cost"] > "1000")]
print(df1[["channel", "cost", "conversions"]])

print(df.groupby("channel").agg({
    "cost": "sum",
    "conversions": "sum",
    "clicks": "mean"
}))

df2 = df.groupby("channel").agg({
    "cost": "sum",
    "conversions": "sum",
    "clicks": "mean"
})
print(df2[df2["avg_cpa"] > 50].sort_values("avg_cpa", ascending=False))

df["cpa"] = df["cost"]/df["conversions"]
df["rank"] = df.groupby("date")["cpa"].rank(ascending=False)
print(df[["date", "channel", "cpa", "rank"]])

df["y_cpa"] = df.groupby("channel")["cpa"].shift(1)
df["cpa_change_rate"] = df["cpa"]/df["y_cpa"]
print(df[["date", "channel", "cpa", "cpa_change_rate"]])

def rank_1(cpa):
    if cpa < 30:
        return "优"
    elif cpa < 60:
        return "中"
    elif cpa >= 60:
        return "差"
df5 = df.groupby("channel").agg(
    total_cost = ("cost", "sum"),
    total_conversions = ("conversions", "sum"),
    avg_cpa = ("cost/conversions", "avg")
)
df5["rank"] = df["cpa"].apply(rank_1)

df6 = df.groupby(["channel", "date"]).agg(
    total_cost = ("cost", "sum"),
    total_conversions = ("conversions", "sum")
)
df6["cpa"] = df6["total_cost"]/df6["total_conversions"]
df6["yesterday_cpa"] = df6.sort.values("date",ascending=False).groupby("channel")["cpa"].shift(1)
print(df6["cpa_change_rate"] = (df6["cpa"] - df6["yesterday_cpa"])/df6["yesterday_cpa"])

df6["avg2_cpa"] = df6.sort.values("date", ascending=False).groupby("channel")["cpa"].rolling(2).mean()

df6["rank"] = df6.groupby("date")["cpa"].rank(ascending=True)
df6[["date", "channel", "cpa", "rank"]]


from pyspark.sql import SparkSession
from pyspark.sql import Row

spark = SparkSession.builder \
    .appName("Practice") \
    .getOrCreate()

# 直接构造数据
data = [
    Row(date="2026-09-01", channel="Facebook", cost=1000, impressions=50000, clicks=1000, conversions=50),
    Row(date="2026-09-02", channel="Facebook", cost=1200, impressions=60000, clicks=1200, conversions=55),
    Row(date="2026-09-03", channel="Facebook", cost=1100, impressions=55000, clicks=1150, conversions=45),
    Row(date="2026-09-01", channel="Google", cost=1500, impressions=75000, clicks=1500, conversions=80),
    Row(date="2026-09-02", channel="Google", cost=1800, impressions=90000, clicks=1800, conversions=95),
    Row(date="2026-09-03", channel="Google", cost=1600, impressions=80000, clicks=1600, conversions=85),
    Row(date="2026-09-01", channel="TikTok", cost=800, impressions=80000, clicks=2400, conversions=30),
    Row(date="2026-09-02", channel="TikTok", cost=900, impressions=90000, clicks=2700, conversions=35),
    Row(date="2026-09-03", channel="TikTok", cost=850, impressions=85000, clicks=2550, conversions=32),
    Row(date="2026-09-01", channel="YouTube", cost=1200, impressions=40000, clicks=800, conversions=60),
    Row(date="2026-09-02", channel="YouTube", cost=1300, impressions=43000, clicks=860, conversions=65),
    Row(date="2026-09-03", channel="YouTube", cost=1250, impressions=42000, clicks=840, conversions=62),
    Row(date="2026-09-01", channel="Instagram", cost=600, impressions=30000, clicks=600, conversions=20),
    Row(date="2026-09-02", channel="Instagram", cost=700, impressions=35000, clicks=700, conversions=22),
    Row(date="2026-09-03", channel="Instagram", cost=650, impressions=32500, clicks=650, conversions=18),
]

df = spark.createDataFrame(data)
df.select("channel", "cost", "conversions").filter((F.col("date") == "2026-09-02") & (F.col("cost") > 1000)).show()

df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.sum("conversions").alias("total_conversions"),
    F.avg(F.col("total_cost")/F.col("total_conversions")).alias("avg_cpa")
).show()

df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.sum("conversions").alias("total_conversions"),
    F.avg(F.col("total_cost")/F.col("total_conversions")).alias("avg_cpa")
).filter(F.col("avg_cpa") > 50).orderBy("cpa_cpa").show()

df21 = df.withColumn("cpa", F.col("cost")/F.col("conversions"))
window = (Window.partitionBy("date").orderBy(F.desc("cpa"))
df21.withColumn("rank", F.number_rank().over(window)).select("date", "channel", "cpa", "rank").show()

window_1 = (Window.partitionBy("channel").orderBy("date"))
df21.withColumn("y_cpa", F.lag("cpa").over(window_1)).withColumn(
    "cpa_change_rate", (F.col("cpa")-F.col("y_cpa"))/F.col("y_cpa")
).show()

df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.sum("conversions").alias("total_conversions"),
    F.avg(F.col("total_cost")/F.col("conversions")).alias("avg_cpa")
).withColumn("level", F.when(
     F.col("cpa") < 30, "优"
).when(
     F.col("cpa") <60, "中"
).otherwise("差")).select("channel", "total_cost", "total_conversions", "avg_cpa", "level").show()

window_2 = (Window.partitionBy("channel").orderBy("date"))
df.groupBy("channel", "date").agg(
    F.sum("cost").alias("total_cost"),
    F.sum("conversions").alias("total_conversions")
).withColumn("cpa",F.col("total_cost")/F.col("total_conversions"))
.withColumn("yesterday_cpa", F.lag("cpa").over(window_2))
.withColumn("cpa_change_rate", (F.col("cpa") - F.col("yesterday_cpa"))/F.col("yesterday_cpa")).show()

window_3 = (Window.partitionBy("channel").groupBy("date").rowsBetween(-1,0))
df.withColumn("avg2_cpa", F.avg("cpa").over(window_3)).show()

window_4 = (Window.partitionBy("date").orderBy("cpa"))
df.withColumn("rank", F.number_rank().over(window_4)).select(
    "date", "channel", "cpa", "rank"
)
print("数据准备好了：")
df.show()





from pyspark.sql import SparkSession
from pyspark.sql import Row

# 1. 创建Spark会话
spark = SparkSession.builder \
    .appName("Practice") \
    .getOrCreate()

# 2. 构造练习数据
data = [
    Row(date="2026-09-01", channel="Facebook", cost=1000, impressions=50000, clicks=1000, conversions=50),
    Row(date="2026-09-02", channel="Facebook", cost=1200, impressions=60000, clicks=1200, conversions=55),
    Row(date="2026-09-03", channel="Facebook", cost=1100, impressions=55000, clicks=1150, conversions=45),
    Row(date="2026-09-01", channel="Google", cost=1500, impressions=75000, clicks=1500, conversions=80),
    Row(date="2026-09-02", channel="Google", cost=1800, impressions=90000, clicks=1800, conversions=95),
    Row(date="2026-09-03", channel="Google", cost=1600, impressions=80000, clicks=1600, conversions=85),
    Row(date="2026-09-01", channel="TikTok", cost=800, impressions=80000, clicks=2400, conversions=30),
    Row(date="2026-09-02", channel="TikTok", cost=900, impressions=90000, clicks=2700, conversions=35),
    Row(date="2026-09-03", channel="TikTok", cost=850, impressions=85000, clicks=2550, conversions=32),
    Row(date="2026-09-01", channel="YouTube", cost=1200, impressions=40000, clicks=800, conversions=60),
    Row(date="2026-09-02", channel="YouTube", cost=1300, impressions=43000, clicks=860, conversions=65),
    Row(date="2026-09-03", channel="YouTube", cost=1250, impressions=42000, clicks=840, conversions=62),
    Row(date="2026-09-01", channel="Instagram", cost=600, impressions=30000, clicks=600, conversions=20),
    Row(date="2026-09-02", channel="Instagram", cost=700, impressions=35000, clicks=700, conversions=22),
    Row(date="2026-09-03", channel="Instagram", cost=650, impressions=32500, clicks=650, conversions=18),
]

df = spark.createDataFrame(data)

# 3. 注册成临时表，名字叫ad_practice
df.createOrReplaceTempView("ad_practice")

print("数据准备好了！表名：ad_practice")
df.show()

# ============================================================
# 在这里写SQL！
# ============================================================

# 第1题：基础筛选
# 找出所有2026年9月2日，且花费大于1000的记录，只显示channel、cost、conversions三列。

result1 = spark.sql("""
select channel, cost, conversions from ad_practice where date = "2026-09-02" and cost > 1000
""")

print("\n第1题结果：")
result1.show()


# 第2题：分组+指标计算
# 按渠道分组，计算每个渠道的总花费、总转化、平均CPA

result2 = spark.sql("""
select sum(cost) as total_cost, sum(conversions) as total_conversion,
avg(total_cost/total_conversions) as avg_cpa from ad_practice
""")

print("\n第2题结果：")
result2.show()

result3 = spark.sql("""
select sum(cost) as total_cost, sum(conversions) as total_conversion,
avg(total_cost/total_conversions) as avg_cpa from ad_practice having avg_cpa > 50 order by avg_cpa desc 
""")

result3 = spark.sql("""
select date, channel, avg(cost/conversions) as cpa,
rank() over(partition by date order by cpa desc) from ad_practice
""")

result4 = spark.sql("""
select date, channel, avg(cost/conversions) as cpa, 
lag(cpa) over (partition by channel order by cpa) as y_cpa,
(cpa-y_cpa)/y_cpa as cpa_change_rate from ad_practice
""")

result5 = spark.sql("""
select avg(total_cost/total_conversions) as avg_cpa,
sum(cost) as total_cost, sum(conversions) as total_conversions,
case when avg_cpa < 30 then '优' when avg_cpa < 60 then '中' else '低'
end as level, channel from ad_practice group by channel
""")

result6 = spark.sql("""
select sum(cost) as total_cost,
sum(conversions) as total_conversions,
total_cost/total_conversions as cpa,
lag(cpa) over (partition by channel order by date) as yesterday_cpa,
(cpa - yesterday_cpa)/yesterday_cpa as cpa_change_rate
from ad_practice group by channel and date
""")

result7 = spark.sql("""
select sum(cost) as total_cost,
sum(conversions) as total_conversions,
total_cost/total_conversions as cpa,
AVG(cpa) OVER (
    PARTITION BY channel
    ORDER BY date
    ROWS BETWEEN 1 PRECEDING AND CURRENT ROW
) as avg2_cpa from ad_practice group by channel and date
""")

result8 = spark.sql("""
select sum(cost) as total_cost,
sum(conversions) as total_conversions,
total_cost/total_conversions as cpa,
rank() over (partition by date order by cpa),
channel, date
from ad_practice group by channel, date
""")