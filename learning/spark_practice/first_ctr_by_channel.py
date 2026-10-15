"""第一条 Spark 实战：按渠道聚合算 CTR（点击率）

教学目的：把 Spark 的核心概念（SparkSession、DataFrame、
懒执行、Action 触发、分组聚合）落在真实数据上跑一遍。

数据：data/raw/sample_ad_data.csv（1069 行 × 9 列）
目标：每个渠道 channel 的 展示量、点击量、CTR、CPC、CPA
"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

# 1. 创建 SparkSession —— 这是 Spark 程序的入口，一切从这里开始
spark = (
    SparkSession.builder
    .appName("ad_ctr_by_channel")        # 任务名（会显示在 Spark UI 上）
    .master("local[*]")                   # 本地运行，[*] = 用满所有 CPU 核
    .getOrCreate()
)

# 2. 读 CSV 成 DataFrame —— 注意：这一步是"懒执行"，不会真的读数据
df = spark.read.csv(
    "data/raw/sample_ad_data.csv",
    header=True,      # 第一行是表头
    inferSchema=True, # 自动推断每列类型（数字列变 int/double）
)

# 3. 看一眼表结构（schema）和前几行 —— 这是第一个 Action，才真正触发计算
df.printSchema()
df.show(5)

# 4. 按渠道聚合：算 CTR / CPC / CPA
#    这一串是 Transformation（懒），直到 show() 才执行
result = (
    df.groupBy("channel")                          # 按渠道分组
    .agg(
        F.sum("impressions").alias("总展示"),
        F.sum("clicks").alias("总点击"),
        F.sum("conversions").alias("总转化"),
        F.sum("cost").alias("总花费"),
        F.round(F.avg("cost"), 2).alias("平均单次花费"),
    )
    .withColumn(                                    # 新增计算列（只能用上面聚合出来的列）
        "CTR",
        F.round(F.col("总点击") / F.col("总展示") * 100, 4),  # 点击率 %
    )
    .withColumn(
        "CPC",
        F.round(F.col("总花费") / F.col("总点击"), 2),        # 每次点击成本
    )
    .orderBy(F.col("总展示").desc())                # 按展示量降序
)

# 5. 展示结果 —— 第二个 Action，触发上面所有计算
result.show(truncate=False)

# 6. 关闭 SparkSession（程序结束前释放资源）
spark.stop()
