# ============================================
# Spark 基础练习 01：创建 SparkSession + 读取数据 + 基本查看
# ============================================
#
# 【练习目标】
# 1. 学会创建 SparkSession
# 2. 学会读取 CSV 文件
# 3. 学会基本的数据查看操作
#
# 【知识点】
# - SparkSession：Spark 的统一入口，所有操作从这里开始
# - spark.read.csv()：读取 CSV 文件
# - df.show()：显示数据
# - df.printSchema()：打印表结构
# - df.count()：统计行数
# - df.columns：查看列名
# - df.dtypes：查看每列的数据类型
# ============================================
from os import truncate

# ---------- 1. 导入需要的库 ----------
from pyspark.sql import SparkSession
from pyspark.sql import functions as F


# ---------- 2. 创建 SparkSession ----------
# 这是所有 Spark 程序的第一步
# builder 是构建器，用来一步步配置
# appName：给程序起个名字
# master：运行模式，local[*] 表示本地模式用所有CPU核心
# getOrCreate()：有就获取，没有就创建
spark = (
    SparkSession.builder
    .appName("Practice01_SparkSession")
    .master("local[*]")
    .getOrCreate()
)

print("=" * 60)
print("SparkSession 创建成功！")
print("Spark 版本:", spark.version)
print("=" * 60)


# ---------- 3. 读取 CSV 文件 ----------
# spark.read.csv() 读取 CSV
# header=True：第一行是列名
# inferSchema=True：自动推断数据类型
df = spark.read.csv(
    "../data/raw/sample_ad_data.csv",
    header=True,
    inferSchema=True
)

print("\n>>> 数据读取成功！")


# ---------- 4. 基本查看操作 ----------

# 4.1 show()：显示数据
# 默认显示前20行，n=5 表示显示前5行
# truncate=False：不截断长字符串
print("\n--- 4.1 显示前5行数据 ---")
df.show(n=5, truncate=False)

# 4.2 printSchema()：打印表结构
# 显示每列的列名和数据类型
print("\n--- 4.2 打印表结构 ---")
df.printSchema()

# 4.3 count()：统计行数
# 注意：count() 是 Action 操作，会真正触发计算
print("\n--- 4.3 统计行数 ---")
row_count = df.count()
print(f"总共有 {row_count} 行数据")

# 4.4 columns：查看所有列名
# 注意：columns 是属性，不是方法，不加括号
print("\n--- 4.4 查看所有列名 ---")
print("列名列表:", df.columns)

# 4.5 dtypes：查看每列的数据类型
# 返回一个列表，每个元素是 (列名, 类型) 的元组
print("\n--- 4.5 查看每列的数据类型 ---")
for col_name, col_type in df.dtypes:
    print(f"  {col_name}: {col_type}")

# 4.6 first() / head()：查看第一行
# first() 返回第一行数据（Row 对象）
print("\n--- 4.6 查看第一行数据 ---")
first_row = df.first()
print("第一行:", first_row)
print("第一行的 channel:", first_row["channel"])
print("第一行的 cost:", first_row["cost"])


# ---------- 5. 简单的列操作预览 ----------
# 后面会详细讲，这里先看一下效果

# 5.1 select()：选择指定列
print("\n--- 5.1 选择 date, channel, cost 三列，显示前3行 ---")
df.select("date", "channel", "cost").show(n=3, truncate=False)

# 5.2 用 F.col() 引用列（推荐写法）
print("\n--- 5.2 用 F.col() 选择列 ---")
df.select(F.col("date"), F.col("channel"), F.col("cost")).show(n=3, truncate=False)


# ---------- 6. 关闭 SparkSession ----------
print("\n>>> 练习完成，关闭 SparkSession...")
spark.stop()
print("完成！")


# ============================================
# 【练习题】
# 请在下面的区域完成练习，写完后运行这个文件查看结果
# ============================================

print("\n" + "=" * 60)
print("【练习题区域】")
print("=" * 60)

# 练习题1：重新创建一个 SparkSession，名字叫 "MyExercise"
# 提示：复制上面创建 SparkSession 的代码，改一下 appName
# 你的代码：
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder
    .appName("MyExercise")
    .master("local[*]")
    .getOrCreate()
)


# 练习题2：用你创建的 SparkSession 重新读取 CSV 文件
# 提示：复制上面读取 CSV 的代码
# 你的代码：
df = spark.read.csv(
    "../data/raw/sample_ad_data.csv",
    header=True,
    inferSchema=True
)


# 练习题3：显示前10行数据，不截断字符串
# 提示：df.show(n=?, truncate=?)
# 你的代码：
df.show(n=5, truncate=False)


# 练习题4：打印表结构
# 你的代码：
df.printSchema()


# 练习题5：统计总共有多少行数据
# 你的代码：
row_count = df.count()


# 练习题6：打印所有列名
# 你的代码：
df.columns


# 练习题7：选择 channel, impressions, clicks, conversions, cost 这5列，显示前5行
# 你的代码：
df.select("date", "channel", "impressions", "clicks").show(n=5)
df.select(F.col("date"), F.col("channel"), F.col("impressions"), F.col("clicks")).show(n=5)

# 练习题8：查看第3行数据（提示：用 df.collect()[2]，但 collect 会把所有数据拉到内存，大数据不要用）
# 你的代码：
df.collect(3)


df.filter(F.col("channel") == "Facebook")
df.filter(
    (F.col("channel") == "Facebook") &
    (F.col("impressions") > 1000)
).show(n=5)

channel_stats = (
    df.groupBy("channel")
    .agg(
        F.count("*").alias("record_count"),
        F.sum("impressions").alias("total_impressions"),
        F.sum("conversions").alias("total_conversions"),
        F.sum("cost").alias("total_spend")
    )
    .orderBy(F.desc("total_spend"))
)
channel_stats.show(truncate=False)

df = df.withColumn

# 练习题9：关闭 SparkSession
# 你的代码：
spark.stop()


print("\n>>> 练习题完成！")
