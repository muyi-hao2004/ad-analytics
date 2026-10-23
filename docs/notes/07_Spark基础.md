# Spark 基础

本篇覆盖：SparkSession 的创建与资源参数；CSV / Parquet / JSON / JDBC 四种数据源的读写；`select` / `filter` / `withColumn` 三个最常用的列行操作；`groupBy + agg` 与 `join` 六种类型；PySpark 窗口函数的 API 形态（语义部分见《04 SQL 基础与窗口函数》）；Spark SQL 临时视图；保存模式 `mode`；分区存储 `partitionBy`；行式与列式存储、四种数据格式的对比；以及一套来自实际练习的报错清单。

---

## 一、SparkSession 与数据读取

### 1.1 创建 SparkSession

SparkSession 是 Spark 3.x 的统一入口，持有集群配置、Catalog 和 SQL 引擎。所有操作都从它开始。

```python
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql import Window

spark = (
    SparkSession.builder
    .appName("AdAnalytics")     # 应用名，会显示在 Spark UI 上
    .master("local[*]")         # 运行模式：本地，用满所有 CPU 核
    .config("spark.sql.shuffle.partitions", "200")
    .getOrCreate()              # 已存在则复用，不存在才新建
)
```

| 配置 | 含义 | 说明 |
|---|---|---|
| `appName` | 应用名 | 便于在 UI 和日志里定位任务 |
| `master("local[*]")` | 本地模式，`*` 表示所有核 | 本地开发用；集群上由提交参数指定 |
| `master("local[4]")` | 本地 4 核 | 想限制并行度时用 |
| `getOrCreate()` | 复用或新建 | 一个 JVM 里只应有一个 SparkSession |

**为什么用 `getOrCreate()` 而不是 `SparkSession(...)`**：同一个进程里重复创建会报"another SparkContext is running"。`getOrCreate` 幂等，脚本重复执行也不会出问题。

### 1.2 读取四种数据源

```python
# CSV：需要显式声明表头和类型推断
df = spark.read.csv("data.csv", header=True, inferSchema=True)

# Parquet：自带列名与类型，不需要 header / inferSchema
df = spark.read.parquet("data.parquet")

# JSON：自动推断 schema
df = spark.read.json("data.json")

# MySQL：通过 JDBC
props = {
    "user": "root",
    "password": "******",
    "driver": "com.mysql.cj.jdbc.Driver",
}
df = spark.read.jdbc(
    url="jdbc:mysql://localhost:3306/ad_analytics",
    table="ads",
    properties=props,
)
```

| 参数 | 作用 | 注意 |
|---|---|---|
| `header=True` | 第一行作列名 | 只对 CSV 有效 |
| `inferSchema=True` | 采样推断列类型 | 多一次全文件扫描，大文件建议显式传 `schema` |
| `sep` | 分隔符 | 默认逗号 |
| `mode` | 解析异常数据的策略 | `PERMISSIVE` / `DROPMALFORMED` / `FAILFAST` |

**`inferSchema` 的代价**：它需要额外扫一遍数据才能确定类型，相当于读两次。生产上更稳的做法是显式定义 schema：

```python
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, DoubleType, DateType

schema = StructType([
    StructField("date", DateType(), True),
    StructField("channel", StringType(), True),
    StructField("campaign", StringType(), True),
    StructField("impressions", IntegerType(), True),
    StructField("clicks", IntegerType(), True),
    StructField("conversions", IntegerType(), True),
    StructField("cost", DoubleType(), True),
])

df = spark.read.csv("data.csv", header=True, schema=schema)
```

显式 schema 的另一个好处是避免"全是数字的 ID 被推断成 int、前导零丢失"——这与 pandas 的 `dtype` 是同一类问题。

### 1.3 基本查看

```python
df.show(10, truncate=False)   # 显示前 10 行，不截断长字符串
df.printSchema()              # 打印表结构（列名、类型、是否可空）
df.count()                    # 总行数（触发一次完整计算）
df.columns                    # 列名列表
df.dtypes                     # (列名, 类型) 列表
df.describe().show()          # 数值列的 count / mean / stddev / min / max
df.limit(5).show()            # 取前 5 行，比 show 更"正式"（返回 DataFrame）
```

**`show()` 与 `limit()` 的区别**：`show()` 是 Action，只用于展示，不返回 DataFrame；`limit(n)` 是 Transformation，返回新的 DataFrame，可以继续链式操作。

**`count()` 很贵**：它要扫描全部数据。调优时不要在大表上反复 `count()`，可以看 Spark UI 的 stage 统计。

---

## 二、select：选择与重命名列

```python
# 写法一：直接传列名字符串
df.select("date", "channel", "cost")

# 写法二：F.col()（推荐，可参与表达式）
df.select(F.col("date"), F.col("channel"), F.col("cost"))

# 选列 + 计算 + 改名
df.select(
    "channel",
    (F.col("clicks") / F.col("impressions")).alias("ctr"),
)

# 排除列
df.drop("temp_col", "unused_col")

# 重命名
df.withColumnRenamed("cost", "total_cost")
df.withColumnRenamed("cost", "total_cost").withColumnRenamed("clicks", "click_cnt")
```

**为什么推荐 `F.col()`**：

1. 可以参与运算（`F.col("a") + F.col("b")`），字符串不行；
2. 可以配合 `F.desc()` / `F.asc()` 排序；
3. 列名有特殊字符（点号、空格）时可用反引号转义：`F.col("`cost.usd`")`。

**链式改名的坑**：`withColumnRenamed` 一次只能改一个，多个要连着写。改名后原列名立即失效，后续引用必须用新名字。

---

## 三、filter：筛选行

```python
# 单条件
df.filter(F.col("cost") > 1000)
df.where(F.col("cost") > 1000)          # where 是 filter 的别名

# 多条件：& 且、| 或、~ 非
df.filter(
    (F.col("channel") == "Facebook") &
    (F.col("cost") > 1000)
)

# 取反
df.filter(~(F.col("channel") == "Facebook"))

# IN
df.filter(F.col("channel").isin("Facebook", "Google", "TikTok"))

# 模糊匹配
df.filter(F.col("campaign").like("%summer%"))
df.filter(F.col("campaign").rlike("双11|618"))     # 正则

# 判空
df.filter(F.col("cost").isNotNull())
df.filter(F.col("cost").isNull())

# 区间
df.filter(F.col("cost").between(100, 1000))

# SQL 字符串表达式（简单条件更方便，但不参与编译期检查）
df.filter("cost > 1000 AND channel = 'Facebook'")
```

**三条硬规则**（与 pandas 完全一致）：

1. 多条件用 `&` / `|` / `~`，不能用 `and` / `or` / `not`；
2. 每个条件必须用括号包住，`&` 的优先级高于 `==`；
3. 判空用 `isNull()` / `isNotNull()`，不能写 `== None`。

**为什么不能用 `and`**：`and` 是 Python 的关键字，要求两边是布尔值，而 `F.col("a") > 1` 返回的是 Column 对象，不是布尔值，会直接抛异常。

---

## 四、withColumn：新增与修改列

```python
# 新增一列
df.withColumn("ctr", F.col("clicks") / F.col("impressions"))

# 修改已有列（同名即覆盖）
df.withColumn("cost", F.col("cost") * 1.1)

# 新增多列：必须链式调用，一次一列
df = (
    df
    .withColumn("ctr", F.round(F.col("clicks") / F.col("impressions"), 4))
    .withColumn("cvr", F.round(F.col("conversions") / F.col("clicks"), 4))
    .withColumn("cpa", F.round(F.col("cost") / F.col("conversions"), 2))
)
```

**`withColumn` 一次只能加一列**。写成 `withColumn("ctr", ..., "cpa", ...)` 会报参数个数错误。这是练习里出现频率最高的错误之一。

**除零问题**：Spark 里整数除以 0 返回 NULL（而不是抛异常），所以指标计算通常不会崩，但结果里会出现 NULL。需要 0 时用 `F.when(...).otherwise(0)` 或 `F.coalesce` 兜底。

### 4.1 常用函数（F.xxx）

| 类别 | 函数 | 说明 |
|---|---|---|
| 引用 | `F.col("x")` | 引用列 |
| | `F.lit(1)` | 常量值（用于和列做运算、拼字符串） |
| 数值 | `F.round(x, 2)` | 四舍五入 |
| | `F.abs(x)` | 绝对值 |
| | `F.ceil(x)` / `F.floor(x)` | 向上 / 向下取整 |
| | `F.col("x").cast("int")` | 类型转换 |
| 字符串 | `F.upper(x)` / `F.lower(x)` | 大小写 |
| | `F.trim(x)` | 去首尾空格 |
| | `F.substring(x, 1, 3)` | 截取（**从 1 开始**，与 Python 的 0 不同） |
| | `F.concat(a, F.lit("-"), b)` | 拼接 |
| | `F.concat_ws("-", a, b)` | 用分隔符拼接，自动跳过 NULL |
| | `F.split(x, "-")` | 切分，返回数组，配合 `getItem(i)` 取值 |
| | `F.length(x)` | 长度 |
| | `F.regexp_replace(x, 旧, 新)` | 正则替换 |
| 日期 | `F.year(x)` / `F.month(x)` / `F.dayofmonth(x)` | 取年 / 月 / 日 |
| | `F.date_format(x, "yyyy-MM")` | 格式化 |
| | `F.date_sub(x, n)` / `F.date_add(x, n)` | 日期加减 |
| | `F.datediff(end, start)` | 相差天数 |
| | `F.to_date(x, "yyyy-MM-dd")` | 字符串转日期 |
| 条件 | `F.when(cond, v).otherwise(v2)` | 类似 CASE WHEN |
| 聚合 | `F.sum` / `F.avg` / `F.count` / `F.max` / `F.min` / `F.countDistinct` | 聚合函数 |
| 空值 | `F.coalesce(a, b, 0)` | 取第一个非 NULL |
| | `F.isnull(x)` / `F.isnan(x)` | 判空 / 判 NaN（NaN 只存在于浮点列） |
| 排序 | `F.desc(x)` / `F.asc(x)` | 排序方向 |
| 其他 | `F.broadcast(df)` | 广播小表（见 join 一节） |
| | `F.rand()` | 0~1 随机数，加盐时用 |

### 4.2 条件列（CASE WHEN）

```python
df.withColumn(
    "cost_level",
    F.when(F.col("cost") > 3000, "高")
     .when(F.col("cost") > 1000, "中")
     .otherwise("低")
)
```

**规则**：

- 从上到下匹配，命中即返回，条件要从严格到宽松排列；
- 没有 `otherwise` 时，全不匹配返回 NULL；
- `when` 可以无限链式追加。

**与 SQL 的对应**：`F.when().otherwise()` ↔ `CASE WHEN ... ELSE ... END`；`F.coalesce()` ↔ `COALESCE()`。

### 4.3 F. 什么时候用

`F` 就是 `from pyspark.sql import functions as F`。

**需要用 F. 的**：所有内置函数（`F.sum`、`F.round`、`F.when`）、列引用（`F.col`）、常量（`F.lit`）。

**不需要 F. 的**：DataFrame 自身的方法（`df.select`、`df.filter`、`df.groupBy`、`df.withColumn`）。

**记忆**：只要调用的是"函数"，就加 `F.`；调用的是"表的方法"，就不加。

---

## 五、groupBy：分组聚合

```python
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.avg("cost").alias("avg_cost"),
    F.count("*").alias("cnt"),
    F.max("cost").alias("max_cost"),
    F.min("cost").alias("min_cost"),
    F.countDistinct("campaign").alias("campaign_cnt"),
)
```

| 写法 | 说明 |
|---|---|
| `df.groupBy("channel").sum("cost")` | 快捷聚合，列名会变成 `sum(cost)`，不好用 |
| `df.groupBy("channel").count()` | 每组的行数 |
| `df.groupBy("channel").agg(...)` | **推荐**，可以用 `alias` 控制列名 |
| `df.groupBy("channel", "date")` | 多列分组，分组键是组合 |

**必须用 `alias` 的原因**：`groupBy + agg` 之后，原来的明细列就不存在了，只有聚合结果列。如果不 `alias`，列名会是 `sum(cost)` 这种带括号的名字，后续 `orderBy` / `select` 引用起来很麻烦，也容易写错。

**分组后筛选（等价 HAVING）**：

```python
df.groupBy("channel") \
  .agg(F.sum("cost").alias("total_cost")) \
  .filter(F.col("total_cost") > 10000)
```

DataFrame API 没有 HAVING，就是用聚合之后再 `filter`。因为此时 `total_cost` 已经是普通列。

**排序**：

```python
df.orderBy(F.desc("total_cost"))
df.orderBy("channel", F.desc("cost"))      # 多列，方向可不同
df.sort("cost")                            # sort 是 orderBy 的别名，默认升序
```

**常见错误**：`groupBy + agg` 之后写 `.orderBy(F.desc("cost"))`。此时 `cost` 已经不存在，必须用 `total_cost`。报错信息通常是 `cannot resolve 'cost'`。

---

## 六、join：多表关联

### 6.1 六种 join

| 类型 | 保留什么 | 典型用途 |
|---|---|---|
| `inner` | 两边都匹配上的行 | 只看匹配数据，最常用 |
| `left` | 左表全部 + 右表匹配的（缺失补 NULL） | 保留主表，最常用 |
| `right` | 右表全部 + 左表匹配的 | 较少用，一般调换表序改成 left |
| `outer` / `full` | 两边全部 | 全量对比、找差异 |
| `left_semi` | 左表中"在右表存在"的行，**只保留左表列** | 过滤（等价 `WHERE EXISTS`） |
| `left_anti` | 左表中"在右表不存在"的行，**只保留左表列** | 找缺失数据（等价 `WHERE NOT EXISTS`） |

`semi` / `anti` 的价值：不需要右表的任何列，只是想"按右表筛左表"。用 `left + filter(右表列 is null)` 也能实现 anti，但会多一次 join 和一次 shuffle，`left_anti` 更直接。

### 6.2 三种写法

```python
# 单列同名关联
df1.join(df2, on="channel", how="left")

# 多列关联
df1.join(df2, on=["channel", "date"], how="inner")

# 列名不同：传一个条件表达式
df1.join(df2, df1["channel"] == df2["ch"], how="left")

# 关联后消除重复的键列（同名关联时键只保留一份）
df1.join(df2, on="channel", how="inner").drop(df2["channel"])
```

**关联键必须唯一**：与 SQL 一样，一对多关联会导致行数膨胀、金额重复计算。join 前后对比 `count()` 是最快的自检手段。

### 6.3 广播 join

```python
# 小表 join 大表：把小表广播到每个 executor，避免大表 shuffle
result = df_large.join(F.broadcast(df_small), on="channel", how="left")
```

**适用条件**：小表几十 MB 以内。Spark 默认的广播阈值是 10MB（`spark.sql.autoBroadcastJoinThreshold`），可以调大，但通常不建议超过 100MB。

**为什么快**：普通 join 要把两边按 key 重分区（shuffle），广播 join 把小表整份复制到每个 executor，大表原地不动，直接本地匹配，**彻底省掉 shuffle**。

**风险**：小表太大会撑爆 driver 和 executor 的内存。详见《08 Spark 分布式原理与调优》。

---

## 七、窗口函数

### 7.1 窗口定义

```python
from pyspark.sql import Window

window_spec = (
    Window.partitionBy("channel")        # 分区（可选）
          .orderBy(F.desc("cost"))       # 排序（排名/累计/环比必选）
          .rowsBetween(Window.unboundedPreceding, Window.currentRow)  # 范围（累计/移动平均用）
)
```

| 部分 | 语法 | 何时必选 |
|---|---|---|
| 分区 | `partitionBy("col1", "col2")` | 需要组内计算时；不写就是全表一个窗口 |
| 排序 | `orderBy("col")` / `orderBy(F.desc("col"))` | 排名、累计、环比时必选 |
| 范围 | `rowsBetween(起, 止)` | 累计、移动平均时必选 |

`rowsBetween` 的取值：

| 写法 | 含义 |
|---|---|
| `Window.unboundedPreceding` | 分区第一行 |
| `Window.currentRow` | 当前行 |
| `Window.unboundedFollowing` | 分区最后一行 |
| `-2` | 当前行往前 2 行 |
| `2` | 当前行往后 2 行 |

### 7.2 三类函数

**排名**：

```python
window_rank = Window.partitionBy("channel").orderBy(F.desc("cost"))

top3 = (
    df.withColumn("rn", F.row_number().over(window_rank))
      .filter(F.col("rn") <= 3)
      .drop("rn")
)
```

`row_number()` / `rank()` / `dense_rank()` 三者的区别（并列时 1,2,3,4 / 1,2,2,4 / 1,2,2,3），以及"恰好 N 条"与"前 N 名含并列"的业务选择，见《04 SQL 基础与窗口函数》5.3 节。

**聚合窗口**：

```python
# 累计求和：必须写 rowsBetween，否则每行都是整个分区的总和
window_cum = (
    Window.partitionBy("channel")
          .orderBy("date")
          .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)
df.withColumn("cumulative_cost", F.sum("cost").over(window_cum))

# 3 日移动平均：当前行往前 2 行 + 当前行
window_ma3 = Window.orderBy("date").rowsBetween(-2, Window.currentRow)
df.withColumn("ma_3d", F.avg("cost").over(window_ma3))

# 分区全局聚合：每行带上本渠道总额
df.withColumn("channel_total", F.sum("cost").over(Window.partitionBy("channel")))
```

**偏移**：

```python
window_lag = Window.partitionBy("channel").orderBy("date")

df_growth = (
    df
    .withColumn("yesterday_cost", F.lag("cost", 1).over(window_lag))
    .withColumn("growth_rate", F.round(
        (F.col("cost") - F.col("yesterday_cost")) / F.col("yesterday_cost"), 4
    ))
)
```

`lead("cost", 1)` 取后一行。`first_value()` / `last_value()` 也可以配合窗口使用。

### 7.3 五个模板

| 场景 | 窗口定义 |
|---|---|
| 组内排名 / Top N | `Window.partitionBy("组").orderBy(F.desc("指标"))` |
| 累计求和 | `Window.partitionBy("组").orderBy("日期").rowsBetween(unboundedPreceding, currentRow)` |
| N 日移动平均 | `Window.orderBy("日期").rowsBetween(-(N-1), currentRow)` |
| 环比 | `Window.partitionBy("组").orderBy("日期")` |
| 分区全局聚合 | `Window.partitionBy("组")` |

### 7.4 四个常见错误

1. 累计求和忘了 `rowsBetween` → 每行都是分区总和，不是累计；
2. 排名函数写了 `rowsBetween` → 排名不需要行范围；
3. `lag` / `lead` 写了 `rowsBetween` → 只需要 `partitionBy + orderBy`；
4. 把 `partitionBy` 当 `groupBy` → 前者不合并行，后者合并行。

**"分组但不聚合"用哪个**：用 `partitionBy + 窗口函数`。`groupBy` 之后必须接聚合，否则拿到的是 `GroupedData` 对象，不能 `show()`。若要把每组的值收集成数组，用 `groupBy + F.collect_list / F.collect_set`。

---

## 八、Spark SQL

### 8.1 三步用法

```python
# 1. 注册临时视图
df.createOrReplaceTempView("ads")

# 2. 写 SQL
result = spark.sql("""
    SELECT channel, SUM(cost) AS total_cost
    FROM ads
    GROUP BY channel
    ORDER BY total_cost DESC
""")

# 3. 查看（仍是 DataFrame，可以继续链式操作）
result.show()
```

`createOrReplaceTempView` 是会话级临时视图，SparkSession 关闭即消失。要跨会话持久化，用 `saveAsTable` 写入 Hive 元存储。

### 8.2 SQL 里的窗口函数

```sql
-- 排名
SELECT channel, stat_date, cost,
       ROW_NUMBER() OVER (PARTITION BY channel ORDER BY cost DESC) AS rn
FROM ads;

-- 累计
SELECT stat_date, cost,
       SUM(cost) OVER (ORDER BY stat_date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_cost
FROM ads;

-- 环比
SELECT stat_date, cost,
       LAG(cost, 1) OVER (ORDER BY stat_date) AS prev_cost
FROM ads;
```

Spark SQL 与 MySQL 的窗口语法几乎一致，差异主要在日期函数（`date_sub(d, n)` 而不是 `INTERVAL n DAY`）和部分函数名。

### 8.3 DataFrame API 与 SQL 怎么选

| 场景 | 推荐 |
|---|---|
| 逻辑简单、步骤少 | DataFrame API，可读性好、可测试 |
| 多表关联 + 多层子查询 + 窗口 | Spark SQL，表达更自然 |
| 需要动态拼接条件 | DataFrame API，便于用 Python 变量 |
| 从现有 SQL 迁移 | Spark SQL，改造成本最低 |

两者最终都走同一个 Catalyst 优化器，性能上没有本质差别，按可读性选即可。同一个脚本里混用也很常见。

---

## 九、数据写出与存储

### 9.1 四种写出方式

```python
df.write.csv("output", header=True, mode="overwrite")
df.write.parquet("output", mode="overwrite")
df.write.json("output", mode="overwrite")
df.write.jdbc(url=..., table="ads_result", mode="overwrite", properties=props)
```

**注意**：Spark 写出的是一个**目录**，不是单个文件。目录里是按分区切分的多个 `part-00000-xxx.csv` 文件，另有一个 `_SUCCESS` 标记文件。想要单文件输出，需先 `coalesce(1)`（会退化为单任务，大表慎用）。

### 9.2 保存模式 mode

| mode | 行为 | 适用 |
|---|---|---|
| `overwrite` | 删掉目标路径后重写 | 全量任务 |
| `append` | 在已有数据后追加 | 增量任务 |
| `ignore` | 目标已存在则什么都不做，也不报错 | 只写一次 |
| `error` / `errorifexists` | 目标已存在则抛异常 | 生产默认，最安全 |

**为什么生产默认用 `error`**：`overwrite` 会无条件删数据，一次路径写错就可能清空整张表。让任务在"目标已存在"时明确失败，是更安全的默认值。

### 9.3 分区存储 partitionBy

**是什么**：按某一列（或多列）的值把数据写成多个子目录，目录名形如 `列名=值`。

```python
df.write.partitionBy("channel").parquet("output", mode="overwrite")
df.write.partitionBy("date", "channel").parquet("output", mode="overwrite")
```

目录结构：

```
output/
  date=2026-09-01/
    channel=Facebook/part-00000.parquet
    channel=Google/part-00001.parquet
  date=2026-09-02/
    channel=Facebook/part-00002.parquet
    channel=Google/part-00003.parquet
```

**三个好处**：

1. **查询更快**：`WHERE date = '2026-09-01'` 只需读对应目录，其余目录直接跳过，这叫**分区裁剪**（partition pruning）；
2. **数据组织清晰**：目录结构本身就是元数据，便于人工排查；
3. **增量写入方便**：只覆盖某一天的分区即可，不必重写全表。

**分区列的选择原则**：

| 选择 | 例子 | 原因 |
|---|---|---|
| 取值少的列 | `channel`（5 个值）、`date`（按天） | 目录数量可控 |
| 避免高基数列 | `user_id`（几百万）、`campaign`（几千） | 会产生海量小目录和小文件 |
| 常用过滤条件的列 | 查询总带日期范围 → 按 date 分区 | 才能吃到分区裁剪 |

**小文件问题**：分区数 × 每分区文件数会迅速膨胀。分区过细（比如按小时 + 渠道 + 活动）会产生大量 KB 级小文件，反而拖慢读取。分区粒度要和查询模式匹配。

**分区列不会出现在文件内容里**：从目录读回来时，Spark 会从路径解析出分区列并自动加回 DataFrame，所以不需要在文件里再存一份。

### 9.4 实际工作中的数据流

```
1. 原始 CSV（小数据，人能看懂）
   ↓
2. Spark 读取 + 清洗 + 计算
   ↓
3. 中间结果 → Parquet（大数据，快，省空间）
   ↓
4. 最终报表 → MySQL（业务查询，实时）
   ↓
5. 接口返回 → JSON（给前端 / Web）
```

这条链路体现了各格式的定位：**CSV 用于交换，Parquet 用于存储与计算，MySQL 用于在线查询，JSON 用于传输**。

---

## 十、存储格式：行式 vs 列式

| 存储方式 | 原理 | 特点 |
|---|---|---|
| **行存储** | 一行数据的所有字段连续存放 | 读整行快（`SELECT *`、按行写入）；读少数几列慢 |
| **列存储** | 同一列的数据连续存放 | 读少数几列快；压缩比高；按行写入慢 |

**为什么列存储在大数据里快**：

1. 分析查询通常只用到几十列里的几列，列存储只需读这几列，I/O 量成倍下降；
2. 同一列的数据类型一致、取值分布集中，压缩率高（字典编码、RLE），文件更小；
3. 列内数据有序时可以做 min/max 统计跳过数据块（与 Parquet 的 row group 统计信息配合）。

**代价**：点查单行、频繁更新不友好，因为要把多列拼起来。

### 四种数据格式对比

| 格式 | 类型 | 存储 | 体积 | 查询速度 | 人类可读 | 典型用途 |
|---|---|---|---|---|---|---|
| **CSV** | 文本 | 行 | 大 | 慢 | 是 | 小数据交换、Excel 导入导出 |
| **Parquet** | 二进制 | 列 | 最小 | 最快 | 否 | 大数据分析、数据仓库 |
| **JSON** | 文本 | 行 | 最大 | 慢 | 是 | API 数据、日志、半结构化 |
| **MySQL** | 数据库 | 行 | 中等 | 中等 | 是（借助工具） | 业务系统、点查、实时查询 |

**选型结论**：

- 存储层与分析层一律 Parquet；
- 与外部系统交换才用 CSV / JSON；
- 需要按主键高频点查、事务保证，才放 MySQL。

---

## 十一、常见错误清单

以下问题全部来自综合练习的实际报错，按出现频率排序。

### 11.1 拼写与大小写

| 错误写法 | 正确写法 |
|---|---|
| `withcolumn` | `withColumn`（C 大写） |
| `patitionBy` | `partitionBy` |
| `groupby` | `groupBy`（B 大写） |
| `orderby` | `orderBy` |
| `impression` | `impressions`（列名多了个 s） |
| `impreessions` | `impressions` |

**规律**：PySpark 的 API 是驼峰命名，第二个单词首字母大写。列名则完全取决于数据源，必须与 `printSchema()` 的输出逐字一致。

### 11.2 withColumn 一次只能加一列

```python
# 错误：一次传两组
df.withColumn("ctr", ..., "cpa", ...)

# 正确：链式调用
df = (
    df.withColumn("ctr", F.col("clicks") / F.col("impressions"))
      .withColumn("cpa", F.col("cost") / F.col("conversions"))
)
```

### 11.3 括号没闭合

```python
# 错误：少一个右括号
window_spec = (Window.orderBy("date").rowsBetween(Window.unboundedPreceding, Window.currentRow)

# 正确
window_spec = (
    Window.orderBy("date")
          .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)
```

**习惯**：先写一对空括号 `()`，再往中间填内容；链式调用每行一个 `.`，结尾的括号单独一行。

### 11.4 聚合后列名对不上

```python
# 错误：聚合后没有 cost 列了
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")) \
  .orderBy(F.desc("cost"))

# 正确：用 alias 之后的列名
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")) \
  .orderBy(F.desc("total_cost"))
```

**记忆**：`groupBy + agg` 之后，参与聚合的原始列全部消失，只剩分组列和聚合结果列。

### 11.5 忘记 show()

```python
df.groupBy("channel").agg(F.sum("cost").alias("total"))    # 什么都不输出
df.groupBy("channel").agg(F.sum("cost").alias("total")).show()   # 正确
```

原因见《08》的懒执行：Transformation 只记计划，必须有 Action 才执行。

### 11.6 lag 用法不对

```python
# 错误：lag 不能直接写在算术表达式里（缺少 over）
df.withColumn("growth", (F.col("cost") - F.lag("cost", 1)) / F.lag("cost", 1))

# 正确：分两步，先算出昨天的列
df = df.withColumn("yesterday", F.lag("cost", 1).over(window))
df = df.withColumn("growth",
                   (F.col("cost") - F.col("yesterday")) / F.col("yesterday"))
```

**两个问题**：一是 `lag` 是窗口函数，必须 `.over(window)`；二是即使加上 `over`，同一个表达式里重复调用两次 `lag` 也容易出错，分两步更清晰。

### 11.7 累计求和忘了 rowsBetween

```python
# 错误：每行都等于整个分区的总和
Window.partitionBy("channel").orderBy("date")

# 正确
Window.partitionBy("channel").orderBy("date") \
      .rowsBetween(Window.unboundedPreceding, Window.currentRow)
```

### 11.8 多条件少了括号

```python
# 错误
df.filter(F.col("channel") == "Facebook" & F.col("cost") > 1000)

# 正确
df.filter((F.col("channel") == "Facebook") & (F.col("cost") > 1000))
```

### 11.9 其他易错点

| 现象 | 原因 | 解决 |
|---|---|---|
| `AnalysisException: cannot resolve` | 列名拼错，或聚合后列已消失 | `printSchema()` 核对，用 alias 后的名字 |
| `Path already exists` | 写出时目标目录已存在且 mode 为 `error` | 改 `mode="overwrite"`，或先清理目录 |
| `Column ambiguous` | join 后两表有同名列 | 加表前缀，或 join 时用 `on="key"` 让键只保留一份 |
| 结果全是 NULL | 除法分母为 0，或 join 键类型不一致 | 检查分母，`printSchema()` 对比两侧类型 |
| 任务一直不结束 | 数据倾斜，个别 task 拖慢全局 | 见《08》数据倾斜一节 |
| 本地跑很慢 | `master("local[*]")` 核数不足，或 shuffle 分区数默认 200 过大 | 调 `local[N]` 与 `spark.sql.shuffle.partitions` |

**join 键类型不一致**是隐蔽性最高的一类问题：一边 `string`、一边 `bigint`，Spark 不报错，但匹配不上，结果大面积为 NULL。排查方法就是 `printSchema()` 对比两侧字段类型。

---

## 十二、综合练习参考实现

10 个递进任务，覆盖从筛选到"聚合 + 窗口"的完整链路。数据为广告日报表（`date`、`channel`、`campaign`、`impressions`、`clicks`、`conversions`、`cost`）。

**任务 1：多条件筛选 + 排序**

```python
df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

df.filter(
    (F.col("channel") == "Facebook") &
    (F.col("cost") > 1000) &
    (F.col("clicks") > 50)
).orderBy(F.desc("cost")).show(10, truncate=False)
```

**任务 2：新增计算列**

```python
df = (
    df
    .withColumn("ctr", F.round(F.col("clicks") / F.col("impressions"), 4))
    .withColumn("cpa", F.round(F.col("cost") / F.col("conversions"), 2))
)

df.select("date", "channel", "campaign", "cost", "clicks",
          "conversions", "ctr", "cpa").show(10, truncate=False)
```

**任务 3：分组聚合**

```python
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.sum("clicks").alias("total_clicks"),
    F.sum("conversions").alias("total_conversions"),
    F.avg("cost").alias("avg_cost"),
).orderBy(F.desc("total_cost")).show(truncate=False)
```

**任务 4：分组后筛选（HAVING）**

```python
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost")
).filter(F.col("total_cost") > 15000) \
 .orderBy(F.desc("total_cost")).show(truncate=False)
```

**任务 5：多列分组 + 多列排序**

```python
df.groupBy("channel", "date").agg(
    F.sum("cost").alias("total_cost")
).orderBy("channel", F.desc("total_cost")).show(20, truncate=False)
```

**任务 6：join 维度表**

```python
channel_data = [
    ("Facebook", "社交平台", "美国"),
    ("Google", "搜索平台", "美国"),
    ("TikTok", "短视频平台", "中国"),
    ("YouTube", "视频平台", "美国"),
    ("Instagram", "图片社交", "美国"),
]
df_channel = spark.createDataFrame(
    channel_data, ["channel", "channel_type", "origin_country"]
)

df.join(df_channel, on="channel", how="left") \
  .select("date", "channel", "channel_type", "cost", "clicks") \
  .show(10, truncate=False)
```

**任务 7：组内 Top 3（窗口函数）**

```python
window = Window.partitionBy("channel").orderBy(F.desc("total_cost"))

df.groupBy("channel", "campaign").agg(
    F.sum("cost").alias("total_cost")
).withColumn("rank", F.row_number().over(window)) \
 .filter(F.col("rank") <= 3) \
 .orderBy("channel", "rank") \
 .show(truncate=False)
```

**任务 8：累计求和**

```python
window_spec = Window.orderBy("date").rowsBetween(
    Window.unboundedPreceding, Window.currentRow
)

df.groupBy("date").agg(
    F.sum("cost").alias("total_cost")
).withColumn("cumulative", F.sum("total_cost").over(window_spec)) \
 .orderBy("date") \
 .show(15, truncate=False)
```

**任务 9：日环比**

```python
window_1 = Window.orderBy("date")

df_daily = df.groupBy("date").agg(F.sum("cost").alias("total_cost"))

df_daily.withColumn("yesterday_cost", F.lag("total_cost", 1).over(window_1)) \
        .withColumn("growth_rate", F.round(
            (F.col("total_cost") - F.col("yesterday_cost")) / F.col("yesterday_cost"), 4
        )) \
        .orderBy("date") \
        .show(15, truncate=False)
```

**任务 10：综合（聚合 + 派生指标 + 窗口）**

```python
# Step 1：按渠道 + 日期聚合
df1 = df.groupBy("channel", "date").agg(
    F.sum("impressions").alias("total_impressions"),
    F.sum("clicks").alias("total_clicks"),
    F.sum("conversions").alias("total_conversions"),
    F.sum("cost").alias("total_cost"),
)

# Step 2：在聚合结果上算比率
df2 = df1 \
    .withColumn("ctr", F.round(F.col("total_clicks") / F.col("total_impressions"), 4)) \
    .withColumn("cvr", F.round(F.col("total_conversions") / F.col("total_clicks"), 4))

# Step 3：每渠道取花费最高的那一天
window_2 = Window.partitionBy("channel").orderBy(F.desc("total_cost"))

df2.withColumn("rank", F.row_number().over(window_2)) \
   .filter(F.col("rank") == 1) \
   .orderBy("date") \
   .show(truncate=False)
```

**这 10 个任务体现的固定套路**：`read → filter → withColumn → groupBy/agg → join → window → write`。真实 ETL 任务就是这条链路的组合，只是环节更多、加了参数化和错误处理。

---

## 十三、面试速查

- SparkSession 是什么 → Spark 3.x 的统一入口，持有配置、Catalog 和 SQL 引擎，用 `builder.getOrCreate()` 创建。
- 为什么用 `getOrCreate()` → 幂等，同一进程重复调用不会报"SparkContext 已存在"。
- `local[*]` 是什么意思 → 本地模式，使用所有可用 CPU 核；`local[4]` 表示限定 4 核。
- 读 CSV 为什么建议显式传 schema → `inferSchema=True` 要额外扫一遍全文件，且可能推断错类型（ID 丢前导零）。
- Parquet 读取为什么不需要 header / inferSchema → 文件自带 schema，列名和类型都存在元数据里。
- `show()` 和 `limit()` 的区别 → `show` 是 Action，只展示；`limit` 是 Transformation，返回 DataFrame。
- `select` 和 `withColumn` 的区别 → `select` 选列/做投影，`withColumn` 新增或覆盖一列。
- `withColumn` 能一次加两列吗 → 不能，一次一列，多列要链式调用。
- 多条件 filter 怎么写 → 每个条件加括号，用 `&` / `|` / `~`，不能用 `and` / `or`。
- 判空怎么写 → `F.col("x").isNull()` / `isNotNull()`，不能写 `== None`。
- `groupBy + agg` 后为什么引用不到原列 → 聚合后明细列已消失，只能用分组列和 `alias` 后的结果列。
- DataFrame 里怎么实现 HAVING → 聚合之后再 `.filter()`。
- join 有哪六种 → inner、left、right、outer、left_semi、left_anti。
- `left_semi` 和 `inner` 的区别 → semi 只保留左表列，用于"按右表筛左表"，不产生列膨胀。
- 广播 join 怎么用、什么时候用 → `F.broadcast(小表)`，小表几十 MB 以内、join 大表时用，可彻底避免 shuffle。
- 窗口定义的三部分 → `partitionBy`（分组）、`orderBy`（排序）、`rowsBetween`（行范围）。
- 累计求和为什么每行都是总值 → 少了 `rowsBetween(unboundedPreceding, currentRow)`。
- `lag` 需要 `rowsBetween` 吗 → 不需要，只要 `partitionBy + orderBy`。
- `partitionBy` 和 `groupBy` 的区别 → 前者不合并行，后者把每组压成一行。
- Spark SQL 怎么用 → `createOrReplaceTempView` 注册视图，`spark.sql(...)` 查询，结果是 DataFrame。
- 保存模式有哪几种 → `overwrite` 覆盖、`append` 追加、`ignore` 忽略、`error` 报错（生产默认）。
- `partitionBy` 写出有什么用 → 按列分目录，查询时分区裁剪只读需要的目录，同时便于增量覆盖。
- 分区列怎么选 → 选取值少、查询常过滤的列（date、channel），避免 user_id 这类高基数列。
- 行存储和列存储的区别 → 行存按行连续，读整行快；列存按列连续，读少数列快、压缩比高。
- 四种数据格式怎么选 → 分析存储用 Parquet，交换用 CSV/JSON，在线点查用 MySQL。
- `F.` 什么时候要加 → 调用函数（`F.sum`、`F.col`、`F.lit`）要加；调用 DataFrame 方法（`df.select`）不加。
