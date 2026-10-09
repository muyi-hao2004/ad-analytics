# Day15：Spark 基础全部知识点总结

## 一、SparkSession 与读取数据

### 1.1 创建 SparkSession

```python
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("MyApp")           # 应用名
    .master("local[*]")         # 本地模式，用所有核
    .getOrCreate()
)
```

### 1.2 读取数据

```python
# CSV
df = spark.read.csv("data.csv", header=True, inferSchema=True)

# Parquet
df = spark.read.parquet("data.parquet")

# JSON
df = spark.read.json("data.json")

# MySQL
df = spark.read.jdbc(url="jdbc:mysql://localhost:3306/db", table="table", properties=props)
```

### 1.3 基本查看

```python
df.show(n=10, truncate=False)  # 显示前n行，不截断
df.printSchema()                # 打印表结构
df.count()                      # 总行数
df.columns                      # 所有列名列表
df.describe().show()            # 统计信息（均值、标准差等）
```

---

## 二、select 选择列

```python
# 写法1：直接传列名
df.select("date", "channel", "cost")

# 写法2：用 F.col()（推荐，更灵活）
from pyspark.sql import functions as F
df.select(F.col("date"), F.col("channel"), F.col("cost"))

# 选列后显示
df.select("date", "channel", "cost").show()

# 排除某些列
df.drop("temp_col", "unused_col")
```

---

## 三、filter 筛选行

```python
# 单条件
df.filter(F.col("cost") > 1000)

# 多条件（& 且，| 或，~ 非）
df.filter(
    (F.col("channel") == "Facebook") &
    (F.col("cost") > 1000)
)

# IN
df.filter(F.col("channel").isin("Facebook", "Google", "TikTok"))

# 模糊匹配
df.filter(F.col("campaign").like("%summer%"))

# 排除 null
df.filter(F.col("cost").isNotNull())
```

> **注意：** 多条件每个条件必须加括号，用 `&` 和 `|`，不能用 `and` / `or`。

---

## 四、withColumn 新增/修改列

```python
# 新增一列
df.withColumn("ctr", F.col("clicks") / F.col("impressions"))

# 修改已有列
df.withColumn("cost", F.col("cost") * 1.1)

# 新增多列（链式调用）
df = (
    df
    .withColumn("ctr", F.round(F.col("clicks") / F.col("impressions"), 4))
    .withColumn("cvr", F.round(F.col("conversions") / F.col("clicks"), 4))
    .withColumn("cpa", F.round(F.col("cost") / F.col("conversions"), 2))
)
```

### 常用函数（F.xxx）

| 类别 | 函数 | 例子 |
|---|---|---|
| **数值** | `F.col()` | 引用列 |
| | `F.lit()` | 常量值 |
| | `F.round()` | 四舍五入 |
| | `F.col().cast()` | 类型转换 |
| **字符串** | `F.upper()` | 转大写 |
| | `F.lower()` | 转小写 |
| | `F.substring()` | 截取（从1开始） |
| | `F.concat()` | 拼接字符串 |
| | `F.trim()` | 去空格 |
| **日期** | `F.year()` | 取年 |
| | `F.month()` | 取月 |
| | `F.date_format()` | 格式化日期 |
| **条件** | `F.when().otherwise()` | 类似 CASE WHEN |
| **聚合** | `F.sum()` `F.avg()` `F.count()` | 聚合函数 |

### 条件列（CASE WHEN）

```python
df.withColumn("cost_level",
    F.when(F.col("cost") > 3000, "高")
     .when(F.col("cost") > 1000, "中")
     .otherwise("低")
)
```

---

## 五、groupBy 分组聚合

```python
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.avg("cost").alias("avg_cost"),
    F.count("*").alias("count"),
    F.max("cost").alias("max_cost"),
    F.min("cost").alias("min_cost")
)
```

### 多列分组

```python
df.groupBy("channel", "region").agg(...)
```

### 分组后筛选（HAVING）

```python
df.groupBy("channel") \
  .agg(F.sum("cost").alias("total_cost")) \
  .filter(F.col("total_cost") > 10000)
```

### 排序

```python
df.orderBy(F.desc("total_cost"))          # 降序
df.orderBy("channel", F.desc("cost"))     # 多列排序
```

---

## 六、join 多表关联

### 六种 join 类型

| join 类型 | 保留 | 用途 |
|---|---|---|
| `inner` | 两张表都匹配上的 | 最常用，只看匹配数据 |
| `left` | 左表全部 + 右表匹配的 | 最常用！保留主表数据 |
| `right` | 右表全部 + 左表匹配的 | 较少用，换成 left |
| `outer` | 两张表全部 | 全量对比 |
| `left_semi` | 左表匹配上的（只有左表列） | 过滤左表 |
| `left_anti` | 左表没匹配上的（只有左表列） | 找缺失数据 |

### 基本写法

```python
# 单列关联
df1.join(df2, on="channel", how="left")

# 多列关联
df1.join(df2, on=["channel", "date"], how="inner")

# 列名不同时
df1.join(df2, on=F.col("df1.channel") == F.col("df2.ch"), how="left")
```

### 广播 join（性能优化）

```python
# 小表 join 大表，广播小表
df_large.join(F.broadcast(df_small), on="id", how="left")
```

> 小表（几十MB以内）join 大表时用，避免大表 Shuffle，快很多。

---

## 七、窗口函数（Window Functions）

### 窗口定义三部分

```python
from pyspark.sql import Window

window_spec = (
    Window.partitionBy("channel")      # 分区（可选）
          .orderBy(F.desc("cost"))     # 排序（排名/累计/环比必选）
          .rowsBetween(Window.unboundedPreceding, Window.currentRow)  # 范围（累计/移动平均用）
)
```

### 三类窗口函数

#### 1. 排名函数

| 函数 | 效果 |
|---|---|
| `row_number()` | 连续编号 1,2,3,4 |
| `rank()` | 相同值同名，跳号 1,1,3,4 |
| `dense_rank()` | 相同值同名，不跳号 1,1,2,3 |

**取 Top N：**
```python
window = Window.partitionBy("channel").orderBy(F.desc("cost"))
df.withColumn("rn", F.row_number().over(window)) \
  .filter(F.col("rn") <= 3)
```

#### 2. 聚合窗口

**累计求和：**
```python
window = Window.partitionBy("channel").orderBy("date") \
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
df.withColumn("cumulative", F.sum("cost").over(window))
```

**移动平均（3日）：**
```python
window = Window.orderBy("date").rowsBetween(-2, Window.currentRow)
df.withColumn("moving_avg", F.avg("cost").over(window))
```

#### 3. 偏移函数（环比）

```python
window = Window.partitionBy("channel").orderBy("date")

# lag：往前N行
df.withColumn("yesterday", F.lag("cost", 1).over(window))

# lead：往后N行
df.withColumn("tomorrow", F.lead("cost", 1).over(window))
```

### 5个常用模板

| 需求 | 窗口定义 |
|---|---|
| 排名/Top N | `Window.partitionBy("列").orderBy(F.desc("列"))` |
| 累计求和 | `Window.partitionBy("列").orderBy("列").rowsBetween(unboundedPreceding, currentRow)` |
| 移动平均 | `Window.orderBy("列").rowsBetween(-(N-1), currentRow)` |
| 环比 | `Window.partitionBy("列").orderBy("列")` |
| 分区全局聚合 | `Window.partitionBy("列")` |

---

## 八、SQL 查询

### 三步用 SQL

```python
# 1. 注册临时视图
df.createOrReplaceTempView("ad_data")

# 2. 写 SQL
result = spark.sql("""
    SELECT channel, SUM(cost) as total
    FROM ad_data
    GROUP BY channel
    ORDER BY total DESC
""")

# 3. 查看结果
result.show()
```

### SQL 窗口函数

```sql
-- 排名
ROW_NUMBER() OVER (PARTITION BY channel ORDER BY cost DESC)

-- 累计
SUM(cost) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)

-- 环比
LAG(cost, 1) OVER (ORDER BY date)
```

---

## 九、数据读写

### 写出数据

```python
df.write.csv("output", header=True, mode="overwrite")
df.write.parquet("output", mode="overwrite")
df.write.json("output", mode="overwrite")
df.write.jdbc(url=..., table=..., mode="overwrite", properties=props)
```

### 保存模式（mode）

| mode | 意思 | 用途 |
|---|---|---|
| `overwrite` | 覆盖 | 全量任务 |
| `append` | 追加 | 增量任务 |
| `ignore` | 忽略 | 只写一次 |
| `error` | 报错 | 生产默认 |

### 分区存储

```python
df.write.partitionBy("channel", "date").parquet("output", mode="overwrite")
```

---

## 十、数据格式对比

| 格式 | 存储 | 体积 | 速度 | 用途 |
|---|---|---|---|---|
| CSV | 行 | 大 | 慢 | 小数据交换 |
| **Parquet** | **列** | **最小** | **最快** | **大数据分析** |
| JSON | 行 | 最大 | 慢 | API/日志 |
| MySQL | 行 | 中 | 中 | 业务查询 |

---

## 十一、练习文件

- `spark_basics/01_spark_session_and_read.py`
- `spark_basics/02_select_and_filter.py`
- `spark_basics/03_with_column.py`
- `spark_basics/04_groupby_agg.py`
- `spark_basics/05_join.py`
- `spark_basics/06_window_functions.py`
- `spark_basics/07_sql_queries.py`
- `spark_basics/08_data_io.py`
