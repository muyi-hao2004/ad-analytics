# ============================================
# Spark 基础练习 05：join 多表关联
# ============================================
#
# 【练习目标】
# 1. 理解六种 join 类型的区别
# 2. 熟练使用 inner、left、right、outer join
# 3. 学会 left_semi 和 left_anti 的使用场景
# 4. 学会多列 join、关联列名不同时的 join
# 5. 了解 join 的性能优化（广播 join、先过滤再 join）
#
# 【知识点】
# - df1.join(df2, on="关联列", how="类型")
# - 六种类型：inner、left、right、outer、left_semi、left_anti
# - 多列 join：on=["列1", "列2"]
# - 列名不同：on=F.col("df1.col") == F.col("df2.col")
# - 广播 join：F.broadcast(小表)
# ============================================


from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("Practice05_Join")
    .master("local[*]")
    .getOrCreate()
)

# 读取广告数据
df_ad = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

# 创建一张渠道维度表（模拟数据）
df_channel_dim = spark.createDataFrame([
    ("Facebook", "社交平台", "美国", "张三"),
    ("Google", "搜索平台", "美国", "李四"),
    ("TikTok", "短视频平台", "中国", "王五"),
    ("YouTube", "视频平台", "美国", "赵六"),
    ("Instagram", "社交平台", "美国", "钱七")
], ["channel", "channel_type", "country", "owner"])

# 创建一张地区维度表
df_region_dim = spark.createDataFrame([
    ("华东", "东部沿海", "上海"),
    ("华南", "南部沿海", "广州"),
    ("华北", "北部沿海", "北京"),
    ("华中", "中部地区", "武汉"),
    ("西南", "西南地区", "成都"),
    ("西北", "西北地区", "西安"),
    ("东北", "东北地区", "沈阳")
], ["region", "region_area", "province_capital"])

print("=" * 60)
print("数据准备完成")
print("广告数据表：", df_ad.count(), "行")
print("渠道维度表：", df_channel_dim.count(), "行")
print("地区维度表：", df_region_dim.count(), "行")
print("=" * 60)


# ---------- 一、inner join（内连接） ----------

print("\n" + "-" * 60)
print("一、inner join（内连接）：只保留两张表都有的记录")
print("-" * 60)

print("\n--- 1.1 广告数据 join 渠道维度表，按渠道分组看总花费 ---")
(
    df_ad.join(df_channel_dim, on="channel", how="inner")
    .groupBy("channel", "channel_type", "owner")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("cost").alias("total_cost")
    )
    .orderBy(F.desc("total_cost"))
    .show(truncate=False)
)

print("\n--- 1.2 inner join 后，广告数据还剩多少行？---")
inner_count = df_ad.join(df_channel_dim, on="channel", how="inner").count()
print(f"inner join 后：{inner_count} 行")
print(f"原始广告数据：{df_ad.count()} 行")
print(f"说明：广告数据的5个渠道都在维度表里，所以行数不变")


# ---------- 二、left join（左连接） ----------

print("\n" + "-" * 60)
print("二、left join（左连接）：保留左表所有记录，右表匹配不上填null")
print("-" * 60)

print("\n--- 2.1 left join：广告数据 left join 渠道维度表 ---")
(
    df_ad.join(df_channel_dim, on="channel", how="left")
    .groupBy("channel", "channel_type", "owner")
    .agg(F.sum("cost").alias("total_cost"))
    .orderBy(F.desc("total_cost"))
    .show(truncate=False)
)

# 演示：如果维度表少一个渠道，left join 会怎么样
print("\n--- 2.2 演示：维度表去掉 YouTube，left join 后 YouTube 的维度列是 null ---")
df_channel_dim_partial = df_channel_dim.filter(F.col("channel") != "YouTube")
(
    df_ad.join(df_channel_dim_partial, on="channel", how="left")
    .filter(F.col("channel") == "YouTube")
    .select("channel", "channel_type", "owner", "cost")
    .show(5, truncate=False)
)
print("可以看到 YouTube 的 channel_type 和 owner 是 null，但广告数据保留了")


# ---------- 三、right join（右连接） ----------

print("\n" + "-" * 60)
print("三、right join（右连接）：保留右表所有记录，左表匹配不上填null")
print("-" * 60)

print("\n--- 3.1 right join：广告数据 right join 渠道维度表 ---")
(
    df_ad.join(df_channel_dim, on="channel", how="right")
    .groupBy("channel", "channel_type", "owner")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("cost").alias("total_cost")
    )
    .orderBy("channel")
    .show(truncate=False)
)
print("注意：Instagram 在广告数据里没有，所以 record_count=1（null行），total_cost=null")


# ---------- 四、outer join（外连接/全连接） ----------

print("\n" + "-" * 60)
print("四、outer join（外连接）：保留两张表所有记录")
print("-" * 60)

print("\n--- 4.1 outer join：广告数据 outer join 渠道维度表 ---")
(
    df_ad.join(df_channel_dim, on="channel", how="outer")
    .groupBy("channel", "channel_type", "owner")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("cost").alias("total_cost")
    )
    .orderBy("channel")
    .show(truncate=False)
)
print("注意：两张表的所有渠道都保留了，匹配不上的填 null")


# ---------- 五、left_semi join（左半连接） ----------

print("\n" + "-" * 60)
print("五、left_semi join（左半连接）：只保留左表能匹配上的，不返回右表列")
print("-" * 60)

print("\n--- 5.1 left_semi：广告数据中在维度表里有的渠道（去重）---")
(
    df_ad.join(df_channel_dim, on="channel", how="left_semi")
    .select("channel").distinct()
    .orderBy("channel")
    .show()
)

print("\n--- 5.2 left_semi 和 inner join 的区别：left_semi 不返回右表列 ---")
print("left_semi 结果列：", df_ad.join(df_channel_dim, on="channel", how="left_semi").columns)
print("inner join 结果列：", df_ad.join(df_channel_dim, on="channel", how="inner").columns)


# ---------- 六、left_anti join（左反连接） ----------

print("\n" + "-" * 60)
print("六、left_anti join（左反连接）：只保留左表匹配不上的，不返回右表列")
print("-" * 60)

print("\n--- 6.1 left_anti：广告数据中不在维度表里的渠道（找缺失数据）---")
(
    df_ad.join(df_channel_dim_partial, on="channel", how="left_anti")
    .select("channel").distinct()
    .orderBy("channel")
    .show()
)
print("注意：维度表去掉了 YouTube，所以 left_anti 找出了 YouTube")

print("\n--- 6.2 left_anti 的实际用途：找数据缺失 ---")
print("比如：找出广告数据中有，但渠道维度表里没有维护的渠道，提醒补充维度表")


# ---------- 七、多列 join ----------

print("\n" + "-" * 60)
print("七、多列 join：按多列关联")
print("-" * 60)

# 创建一张按渠道+地区的目标表
df_target = spark.createDataFrame([
    ("Facebook", "华东", 10000),
    ("Facebook", "华南", 8000),
    ("Google", "华东", 15000),
    ("Google", "华南", 12000),
    ("TikTok", "华东", 9000)
], ["channel", "region", "target_cost"])

print("\n--- 7.1 按 channel + region 两列 join ---")
(
    df_ad.join(df_target, on=["channel", "region"], how="left")
    .groupBy("channel", "region")
    .agg(
        F.sum("cost").alias("actual_cost"),
        F.first("target_cost").alias("target_cost")
    )
    .orderBy("channel", "region")
    .show(20, truncate=False)
)


# ---------- 八、关联列名不同时的 join ----------

print("\n" + "-" * 60)
print("八、关联列名不同时的 join")
print("-" * 60)

# 创建一张列名不同的维度表
df_channel_dim_renamed = df_channel_dim.withColumnRenamed("channel", "channel_name")

print("\n--- 8.1 左表列名 channel，右表列名 channel_name ---")
print("右表列名：", df_channel_dim_renamed.columns)

print("\n--- 8.2 用 F.col() 指定关联条件 ---")
(
    df_ad.join(
        df_channel_dim_renamed,
        on=F.col("channel") == F.col("channel_name"),
        how="left"
    )
    .select("channel", "channel_name", "channel_type", "owner", "cost")
    .show(5, truncate=False)
)
print("注意：join 后 channel 和 channel_name 两列都保留了，需要手动 drop 一个")

print("\n--- 8.3 join 后 drop 掉重复的列 ---")
(
    df_ad.join(
        df_channel_dim_renamed,
        on=F.col("channel") == F.col("channel_name"),
        how="left"
    )
    .drop("channel_name")  # 删掉重复的列
    .select("channel", "channel_type", "owner", "cost")
    .show(5, truncate=False)
)


# ---------- 九、join 性能优化：广播 join ----------

print("\n" + "-" * 60)
print("九、join 性能优化：广播 join（broadcast join）")
print("-" * 60)

print("\n--- 9.1 小表 join 大表，用广播 join 避免 Shuffle ---")
print("维度表很小（5行），广告数据很大（1069行），用广播 join 把维度表广播到每个节点")

(
    df_ad.join(F.broadcast(df_channel_dim), on="channel", how="left")
    .groupBy("channel", "channel_type")
    .agg(F.sum("cost").alias("total_cost"))
    .orderBy(F.desc("total_cost"))
    .show(truncate=False)
)

print("\n--- 9.2 什么时候用广播 join？---")
print("• 小表能完全放进内存（一般几十MB以内）")
print("• 小表 join 大表")
print("• 可以显著减少 Shuffle，提升速度")
print("• Spark 会自动判断，但也可以手动指定 F.broadcast()")


# ---------- 十、综合实战：三张表关联分析 ----------

print("\n" + "-" * 60)
print("十、综合实战：三张表关联分析")
print("-" * 60)

print("\n--- 10.1 广告数据 + 渠道维度 + 地区维度，三表 join ---")
(
    df_ad
    .join(df_channel_dim, on="channel", how="left")
    .join(df_region_dim, on="region", how="left")
    .groupBy("channel_type", "region_area")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("impressions").alias("total_impressions"),
        F.sum("clicks").alias("total_clicks"),
        F.sum("conversions").alias("total_conversions"),
        F.sum("cost").alias("total_cost")
    )
    .orderBy(F.desc("total_cost"))
    .show(20, truncate=False)
)

print("\n--- 10.2 计算各渠道类型的整体 CTR、CVR、CPA ---")
(
    df_ad
    .join(df_channel_dim, on="channel", how="left")
    .groupBy("channel_type")
    .agg(
        F.sum("impressions").alias("total_impressions"),
        F.sum("clicks").alias("total_clicks"),
        F.sum("conversions").alias("total_conversions"),
        F.sum("cost").alias("total_cost")
    )
    .withColumn("overall_ctr", F.round(F.col("total_clicks") / F.col("total_impressions"), 4))
    .withColumn("overall_cvr", F.round(F.col("total_conversions") / F.col("total_clicks"), 4))
    .withColumn("overall_cpa", F.round(F.col("total_cost") / F.col("total_conversions"), 2))
    .orderBy(F.desc("total_cost"))
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

# 练习题1：创建 SparkSession，读取广告数据，创建渠道维度表（用上面的示例数据）
# 你的代码：



# 练习题2：用 inner join 把广告数据和渠道维度表关联，按渠道类型分组，计算总花费，按总花费降序排列
# 你的代码：



# 练习题3：用 left join 把广告数据和渠道维度表关联，筛选出 channel_type 为 null 的数据（也就是维度表里没有的渠道）
# 提示：left join 后用 filter(F.col("channel_type").isNull())
# 你的代码：



# 练习题4：用 left_semi join 找出广告数据中在渠道维度表里有的渠道（去重）
# 你的代码：



# 练习题5：用 left_anti join 找出广告数据中不在渠道维度表里的渠道（去重）
# 你的代码：



# 练习题6：创建地区维度表（用上面的示例数据），用 left join 把广告数据和地区维度表关联，
#           按 region_area（区域）分组，计算总花费，按总花费降序排列
# 你的代码：



# 练习题7：三张表 join：广告数据 + 渠道维度 + 地区维度，按 channel_type + region_area 分组，
#           计算记录数和总花费，按总花费降序排列，显示前10
# 你的代码：



# 练习题8：用广播 join 把广告数据（大表）和渠道维度表（小表）关联，按渠道分组计算总花费
# 提示：F.broadcast(df_channel_dim)
# 你的代码：



# 练习题9：创建一张目标表（channel, target_cost），用 left join 关联广告数据，
#           按渠道分组计算实际总花费和目标花费，然后新增一列完成率 = 实际花费 / 目标花费
# 你的代码：



# 练习题10：综合题：广告数据 left join 渠道维度表 left join 地区维度表，
#            按渠道类型 + 区域分组，计算总曝光、总点击、总转化、总花费，
#            然后计算整体 CTR、CVR、CPA，按总花费降序排列，显示所有结果
# 你的代码：



print("\n>>> 练习题完成！")
