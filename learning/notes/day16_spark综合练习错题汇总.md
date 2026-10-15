# Day16：Spark 综合练习错题汇总

## 一、最常犯的错误清单（必看！）

### 1. 拼写错误（出现最多！）

| 错误写法 | 正确写法 |
|---|---|
| `withcolumn` | `withColumn`（C大写！） |
| `patitionBy` | `partitionBy` |
| `impression` | `impressions`（多了个 s） |
| `impreessions` | `impressions`（拼写错了） |

> **建议：** 写代码时多注意大小写，PySpark 函数都是驼峰命名法（第二个单词首字母大写）。

---

### 2. withColumn 一次只能加一列 ❌

**错误写法（一次加两列）：**
```python
df.withColumn("ctr", ..., "cpa", ...)
```

**正确写法（链式调用，分两次）：**
```python
df = df \
    .withColumn("ctr", F.col("clicks") / F.col("impressions")) \
    .withColumn("cpa", F.col("cost") / F.col("conversions"))
```

---

### 3. 括号没闭合 ❌

**错误写法：**
```python
window_spec = (Window.orderBy("date").rowsBetween(...)
# 少了右括号 )
```

**正确写法：**
```python
window_spec = (
    Window.orderBy("date")
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)
```

> **建议：** 写括号时成对写 `()`，再往中间填内容。

---

### 4. 聚合后列名对不上 ❌

**错误写法：**
```python
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")) \
  .orderBy(F.desc("cost"))  # 错！聚合后没有 cost 列了
```

**正确写法：**
```python
df.groupBy("channel").agg(F.sum("cost").alias("total_cost")) \
  .orderBy(F.desc("total_cost"))  # 对！用 alias 后的列名
```

> **记住：** groupBy + agg 之后，原来的 `cost` 列就没了，只有你 alias 过的 `total_cost`。

---

### 5. 忘记 show() ❌

**错误写法：**
```python
df.groupBy("channel").agg(...)
# 没 show，看不到结果
```

**正确写法：**
```python
df.groupBy("channel").agg(...).show()
```

---

### 6. lag 用法不对 ❌

**错误写法：**
```python
# lag 不能直接写在表达式里
df.withColumn("growth", (F.col("cost") - F.lag("cost", 1)) / F.lag("cost", 1))
```

**正确写法：**
```python
# 第一步：先 lag 出昨天的列
df = df.withColumn("yesterday", F.lag("cost", 1).over(window))

# 第二步：再用今天和昨天的列计算增长率
df = df.withColumn("growth", (F.col("cost") - F.col("yesterday")) / F.col("yesterday"))
```

---

## 二、10道练习题正确答案

### 第1题：基础筛选
```python
df = spark.read.csv("../data/raw/sample_ad_data.csv", header=True, inferSchema=True)

df.filter(
    (F.col("channel") == "Facebook") &
    (F.col("cost") > 1000) &
    (F.col("clicks") > 50)
).orderBy(F.desc("cost")).show(10, truncate=False)
```

---

### 第2题：新增计算列
```python
df = (
    df
    .withColumn("ctr", F.round(F.col("clicks") / F.col("impressions"), 4))
    .withColumn("cpa", F.round(F.col("cost") / F.col("conversions"), 2))
)

df.select("date", "channel", "campaign", "cost", "clicks", "conversions", "ctr", "cpa").show(10, truncate=False)
```

---

### 第3题：分组聚合
```python
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.sum("clicks").alias("total_clicks"),
    F.sum("conversions").alias("total_conversions"),
    F.avg("cost").alias("avg_cost")
).orderBy(F.desc("total_cost")).show(truncate=False)
```

---

### 第4题：分组后筛选
```python
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost")
).filter(F.col("total_cost") > 15000).orderBy(F.desc("total_cost")).show(truncate=False)
```

---

### 第5题：多条件排序
```python
df.groupBy("channel", "date").agg(
    F.sum("cost").alias("total_cost")
).orderBy("channel", F.desc("total_cost")).show(20, truncate=False)
```

---

### 第6题：join 关联
```python
# 先创建渠道维度表
channel_data = [
    ("Facebook", "社交平台", "美国"),
    ("Google", "搜索平台", "美国"),
    ("TikTok", "短视频平台", "中国"),
    ("YouTube", "视频平台", "美国"),
    ("Instagram", "图片社交", "美国"),
]
df_channel = spark.createDataFrame(channel_data, ["channel", "channel_type", "origin_country"])

# 左连接 + 选列
df.join(df_channel, on="channel", how="left") \
  .select("date", "channel", "channel_type", "cost", "clicks") \
  .show(10, truncate=False)
```

---

### 第7题：Top N（窗口函数）
```python
window = Window.partitionBy("channel").orderBy(F.desc("total_cost"))

df.groupBy("channel", "campaign").agg(
    F.sum("cost").alias("total_cost")
).withColumn("rank", F.row_number().over(window)) \
 .filter(F.col("rank") <= 3) \
 .orderBy("channel", "rank") \
 .show(truncate=False)
```

---

### 第8题：累计求和
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

---

### 第9题：环比
```python
window_1 = Window.orderBy("date")

df_daily = df.groupBy("date").agg(
    F.sum("cost").alias("total_cost")
)

df_daily.withColumn("yesterday_cost", F.lag("total_cost", 1).over(window_1)) \
        .withColumn("growth_rate", F.round(
            (F.col("total_cost") - F.col("yesterday_cost")) / F.col("yesterday_cost"), 4
        )) \
        .orderBy("date") \
        .show(15, truncate=False)
```

---

### 第10题：综合题
```python
# Step 1: 按渠道+日期聚合
df1 = df.groupBy("channel", "date").agg(
    F.sum("impressions").alias("total_impressions"),
    F.sum("clicks").alias("total_clicks"),
    F.sum("conversions").alias("total_conversions"),
    F.sum("cost").alias("total_cost")
)

# Step 2: 计算 CTR 和 CVR
df2 = df1 \
    .withColumn("ctr", F.round(F.col("total_clicks") / F.col("total_impressions"), 4)) \
    .withColumn("cvr", F.round(F.col("total_conversions") / F.col("total_clicks"), 4))

# Step 3: 窗口函数排名
window_2 = Window.partitionBy("channel").orderBy(F.desc("total_cost"))

df2.withColumn("rank", F.row_number().over(window_2)) \
   .filter(F.col("rank") == 1) \
   .orderBy("date") \
   .show(truncate=False)
```

---

## 三、你的水平评估

| 方面 | 水平 | 说明 |
|---|---|---|
| 思路理解 | ✅ 不错 | 知道每道题用什么方法 |
| 语法细节 | ⚠️ 待加强 | 拼写错误、括号、列名问题多 |
| 窗口函数 | ⚠️ 基本掌握 | 知道怎么用，但 lag 用法不熟 |
| 综合运用 | ⚠️ 待练习 | 多步组合时容易乱 |

> **下一步建议：** 把上面的正确答案复制到 total_practice.py 里，运行一遍看结果，加深印象。然后再出10道类似的题再练。
