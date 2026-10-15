# ============================================
# 第一个 PySpark 程序
# 功能：创建 SparkSession，读取 CSV，做基本操作
# ============================================

# ---------- 1. 导入需要的库 ----------
# SparkSession 是 Spark 2.0 之后的统一入口
# 以前有 SparkContext、SQLContext、HiveContext 等多个入口
# 现在统一用 SparkSession，所有功能都从这里开始
from pyspark.sql import SparkSession

# 导入 pyspark.sql.functions 里面的函数
# 这个模块包含了所有 Spark SQL 的内置函数（sum、avg、count、when 等）
# 一般习惯用别名 F，这样写 F.sum()、F.avg() 比较简洁
from pyspark.sql import functions as F


# ---------- 2. 创建 SparkSession ----------
# 这是所有 Spark 程序的第一步，相当于"启动 Spark 引擎"
# builder 是一个构建器，用来一步步配置 SparkSession
# 每个 .config() 都是加一个配置项
# .getOrCreate() 的意思是：如果已经有 SparkSession 就获取，没有就创建一个
spark = (
    SparkSession.builder
    # appName：给这个 Spark 应用起个名字，在 Spark UI 里能看到
    # 相当于给你的程序起个名字，方便区分
    .appName("FirstSparkProgram")
    # master：设置 Spark 运行模式
    # local[*] 表示本地模式，用本机所有 CPU 核心来运行
    # * 表示自动检测有几个核心就用几个
    # 如果写 local[2] 就是用2个核心，local 就是用1个核心
    # 实际生产环境中这里会是 yarn、k8s 等集群模式，我们学习用本地模式就行
    .master("local[*]")
    # 这是一个配置项，设置 Spark UI 的端口，默认是4040
    # 多个 Spark 程序同时跑时端口会冲突，这里可以不设，用默认的就行
    # .config("spark.ui.port", "4050")
    .getOrCreate()
)

# 打印一下，确认 SparkSession 创建成功
print("=" * 50)
print("SparkSession 创建成功！")
print("Spark 版本:", spark.version)
print("=" * 50)


# ---------- 3. 读取 CSV 文件 ----------
# spark.read 是数据读取器，用来读各种格式的数据
# .csv() 表示读 CSV 文件
# 里面的参数：
#   header=True：表示第一行是列名（表头），不是数据
#   inferSchema=True：让 Spark 自动推断每列的数据类型（int、string、double等）
#     注意：生产环境不建议用 inferSchema，因为它会多读一遍数据，而且推断可能不准
#     生产环境应该手动指定 schema（后面会讲）
#   sep=","：分隔符，默认就是逗号，可以不写
df = spark.read.csv(
    "../data/raw/sample_ad_data.csv",
    header=True,
    inferSchema=True
)

# ---------- 4. 基本操作 ----------

# 4.1 show()：显示数据
# 默认显示前20行，参数 n=10 表示显示前10行
# truncate=False 表示不截断长字符串（默认会截断超过20个字符的字符串）
print("\n--- 4.1 显示前5行数据 ---")
df.show(n=5, truncate=False)

# 4.2 printSchema()：打印表结构（每列的列名和数据类型）
# 这是非常常用的操作，读取数据后第一件事就是看 schema 对不对
print("\n--- 4.2 打印表结构 ---")
df.printSchema()

# 4.3 count()：统计行数
# 注意：count() 是 Action 操作，会真正触发计算
# 而前面的 show() 也是 Action
print("\n--- 4.3 统计行数 ---")
row_count = df.count()
print(f"总共有 {row_count} 行数据")

# 4.4 columns：查看所有列名
# 注意：columns 是属性，不是方法，所以不加括号
print("\n--- 4.4 查看所有列名 ---")
print("列名列表:", df.columns)

# 4.5 select()：选择指定的列
# 相当于 SQL 里的 SELECT 列名 FROM 表
# 这里选择 date、channel、impressions、clicks 这4列
print("\n--- 4.5 选择指定列（前5行）---")
df.select("date", "channel", "impressions", "clicks").show(n=5)

# 4.6 filter() / where()：筛选行
# 相当于 SQL 里的 WHERE 条件
# filter 和 where 是完全一样的，用哪个都行
# 这里筛选 channel = "Facebook" 的数据
# 注意：df.channel 这种写法是引用列，也可以写 F.col("channel")
print("\n--- 4.6 筛选 Facebook 渠道的数据（前5行）---")
df.filter(df.channel == "Facebook").show(n=5)

# 也可以用 SQL 表达式字符串来写条件，更接近 SQL 的写法
# df.filter("channel = 'Facebook'").show(n=5)

# 4.7 多条件筛选
# 多个条件用 &（与）、|（或）连接，每个条件要用括号括起来
# 这里筛选 Facebook 渠道 且 曝光量 > 1000 的数据
print("\n--- 4.7 多条件筛选：Facebook 且 曝光>1000（前5行）---")
df.filter(
    (df.channel == "Facebook") &
    (df.impressions > 1000)
).show(n=5)


# ---------- 5. 简单的聚合计算 ----------
# groupBy()：按某列分组，相当于 SQL 的 GROUP BY
# .agg()：聚合，可以同时计算多个聚合指标
# F.sum()、F.avg()、F.count() 都是聚合函数
print("\n--- 5. 按渠道分组计算指标 ---")
channel_stats = (
    df.groupBy("channel")
    .agg(
        F.count("*").alias("record_count"),      # 记录数
        F.sum("impressions").alias("total_impressions"),  # 总曝光
        F.sum("clicks").alias("total_clicks"),    # 总点击
        F.sum("conversions").alias("total_conversions"),  # 总转化
        F.sum("cost").alias("total_spend")          # 总花费（列名是cost）
    )
    .orderBy(F.desc("total_spend"))  # 按总花费降序排列
)
channel_stats.show(truncate=False)


# ---------- 6. 关闭 SparkSession ----------
# 程序结束后要关闭 SparkSession，释放资源
# 就像用完电脑要关机一样
print("\n程序执行完毕，关闭 SparkSession...")
spark.stop()
print("完成！")
