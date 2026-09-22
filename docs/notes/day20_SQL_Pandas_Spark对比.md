# Day20: SQL / Pandas / Spark 核心知识点对比

---

## 一、导入和初始化

### Pandas
```python
import pandas as pd
```

### Spark
```python
from pyspark.sql import SparkSession
from pyspark.sql import functions as F  # 所有函数都在这里
from pyspark.sql.window import Window

spark = SparkSession.builder.appName("Test").getOrCreate()
```

---

## 二、数据读取

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 读表 | `SELECT * FROM ad_data` | `pd.read_csv("data.csv")` | `spark.read.csv("data.csv", header=True)` |
| 读前N行 | `SELECT * FROM ad_data LIMIT 5` | `df.head(5)` | `df.show(5)` |
| 看列名 | `DESCRIBE ad_data` | `df.columns` | `df.printSchema()` |
| 看行数 | `SELECT COUNT(*) FROM ad_data` | `len(df)` | `df.count()` |

---

## 三、选择列（SELECT）

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 选单列 | `SELECT channel FROM ad_data` | `df["channel"]` | `df.select("channel")` |
| 选多列 | `SELECT channel, cost FROM ad_data` | `df[["channel", "cost"]]` | `df.select("channel", "cost")` |
| 重命名列 | `SELECT cost AS total_cost FROM ad_data` | `df.rename(columns={"cost": "total_cost"})` | `df.withColumnRenamed("cost", "total_cost")` |

---

## 四、筛选行（WHERE）

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 等于 | `WHERE channel = 'Facebook'` | `df[df["channel"] == "Facebook"]` | `df.filter(F.col("channel") == "Facebook")` |
| 大于 | `WHERE cost > 1000` | `df[df["cost"] > 1000]` | `df.filter(F.col("cost") > 1000)` |
| 多条件AND | `WHERE a = 1 AND b > 2` | `df[(df["a"]==1) & (df["b"]>2)]` | `df.filter((F.col("a")==1) & (F.col("b")>2))` |
| IN | `WHERE channel IN ('Facebook','Google')` | `df[df["channel"].isin(["Facebook","Google"])]` | `df.filter(F.col("channel").isin("Facebook","Google"))` |
| 模糊查询 | `WHERE channel LIKE '%Face%'` | `df[df["channel"].str.contains("Face")]` | `df.filter(F.col("channel").like("%Face%"))` |
| 空值 | `WHERE cost IS NULL` | `df[df["cost"].isnull()]` | `df.filter(F.col("cost").isNull())` |

---

## 五、新增列 / 计算字段

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 新增计算列 | `SELECT cost/clicks AS CPC FROM ad_data` | `df["CPC"] = df["cost"] / df["clicks"]` | `df.withColumn("CPC", F.col("cost") / F.col("clicks"))` |
| 条件判断 | `SELECT CASE WHEN cost>1000 THEN '高' ELSE '低' END AS level` | `df["level"] = df["cost"].apply(lambda x: '高' if x>1000 else '低')` | `df.withColumn("level", F.when(F.col("cost")>1000, "高").otherwise("低"))` |
| 四舍五入 | `SELECT ROUND(cpa, 2) FROM ad_data` | `df["cpa"].round(2)` | `df.withColumn("cpa", F.round(F.col("cpa"), 2))` |

---

## 六、聚合（GROUP BY）

### 6.1 基础聚合

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 分组计数 | `SELECT channel, COUNT(*) FROM ad_data GROUP BY channel` | `df.groupby("channel").size()` | `df.groupBy("channel").count()` |
| 分组求和 | `SELECT channel, SUM(cost) FROM ad_data GROUP BY channel` | `df.groupby("channel")["cost"].sum()` | `df.groupBy("channel").sum("cost")` |
| 分组平均 | `SELECT channel, AVG(cost) FROM ad_data GROUP BY channel` | `df.groupby("channel")["cost"].mean()` | `df.groupBy("channel").avg("cost")` |

---

### 6.2 多个聚合 + 重命名列

**SQL：**
```sql
SELECT channel, 
       SUM(cost) as total_cost, 
       AVG(cost) as avg_cost
FROM ad_data 
GROUP BY channel
```

**Pandas：**
```python
df.groupby("channel").agg(
    total_cost=("cost", "sum"),
    avg_cost=("cost", "mean")
).reset_index()
```

**Spark：**
```python
df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.avg("cost").alias("avg_cost")
)
```

---

### 6.3 筛选聚合结果（HAVING）

**SQL：**
```sql
SELECT channel, SUM(cost) as total_cost
FROM ad_data
GROUP BY channel
HAVING total_cost > 10000
```

**Pandas：**
```python
df_grouped = df.groupby("channel")["cost"].sum().reset_index(name="total_cost")
df_grouped[df_grouped["total_cost"] > 10000]
```

**Spark：**
```python
df.groupBy("channel") \
  .agg(F.sum("cost").alias("total_cost")) \
  .filter(F.col("total_cost") > 10000)
```

---

## 七、排序（ORDER BY）

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 升序 | `ORDER BY cost ASC` | `df.sort_values("cost", ascending=True)` | `df.orderBy(F.col("cost").asc())` |
| 降序 | `ORDER BY cost DESC` | `df.sort_values("cost", ascending=False)` | `df.orderBy(F.col("cost").desc())` |
| 多列排序 | `ORDER BY channel, cost DESC` | `df.sort_values(["channel","cost"], ascending=[True,False])` | `df.orderBy(F.col("channel").asc(), F.col("cost").desc())` |

---

## 八、取前N条（LIMIT）

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 前N条 | `LIMIT 3` | `df.head(3)` | `df.limit(3)` |
| 最大的N个 | `ORDER BY cost DESC LIMIT 3` | `df.nlargest(3, "cost")` | `df.orderBy(F.col("cost").desc()).limit(3)` |

---

## 九、去重（DISTINCT）

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 去重 | `SELECT DISTINCT channel FROM ad_data` | `df["channel"].unique()` | `df.select("channel").distinct()` |
| 去重计数 | `SELECT COUNT(DISTINCT channel) FROM ad_data` | `df["channel"].nunique()` | `df.select("channel").distinct().count()` |

---

## 十、多表连接（JOIN）

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 内连接 | `SELECT * FROM a JOIN b ON a.id = b.id` | `pd.merge(a, b, on="id", how="inner")` | `a.join(b, "id", "inner")` |
| 左连接 | `SELECT * FROM a LEFT JOIN b ON a.id = b.id` | `pd.merge(a, b, on="id", how="left")` | `a.join(b, "id", "left")` |
| 不同列名连接 | `SELECT * FROM a JOIN b ON a.id = b.user_id` | `pd.merge(a, b, left_on="id", right_on="user_id")` | `a.join(b, a["id"] == b["user_id"], "inner")` |

---

## 十一、窗口函数

### 11.1 排名

**SQL：**
```sql
SELECT channel, cost,
       RANK() OVER (PARTITION BY date ORDER BY cost DESC) as rk
FROM ad_data
```

**Pandas：**
```python
df["rk"] = df.groupby("date")["cost"].rank(ascending=False)
```

**Spark：**
```python
window = Window.partitionBy("date").orderBy(F.col("cost").desc())
df.withColumn("rk", F.rank().over(window))
```

---

### 11.2 取前N行（LAG）

**SQL：**
```sql
SELECT date, channel, cost,
       LAG(cost, 1) OVER (PARTITION BY channel ORDER BY date) as prev_cost
FROM ad_data
```

**Pandas：**
```python
df["prev_cost"] = df.groupby("channel")["cost"].shift(1)
```

**Spark：**
```python
window = Window.partitionBy("channel").orderBy("date")
df.withColumn("prev_cost", F.lag("cost", 1).over(window))
```

---

### 11.3 累计求和

**SQL：**
```sql
SELECT date, channel, cost,
       SUM(cost) OVER (PARTITION BY channel ORDER BY date) as cum_cost
FROM ad_data
```

**Pandas：**
```python
df["cum_cost"] = df.groupby("channel")["cost"].cumsum()
```

---

## 十二、空值处理

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 填充空值 | `SELECT COALESCE(cost, 0) FROM ad_data` | `df["cost"].fillna(0)` | `df.na.fill({"cost": 0})` |
| 删除空值 | `DELETE FROM ad_data WHERE cost IS NULL` | `df.dropna(subset=["cost"])` | `df.na.drop(subset=["cost"])` |

---

## 十三、常用函数

### 数学函数
| 功能 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 四舍五入 | `ROUND(x, 2)` | `x.round(2)` | `F.round(x, 2)` |
| 绝对值 | `ABS(x)` | `x.abs()` | `F.abs(x)` |

### 字符串函数
| 功能 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 转大写 | `UPPER(channel)` | `df["channel"].str.upper()` | `F.upper(F.col("channel"))` |
| 字符串拼接 | `CONCAT(a, '_', b)` | `df["a"] + "_" + df["b"]` | `F.concat(F.col("a"), F.lit("_"), F.col("b"))` |

---

## 十四、保存数据

| 操作 | SQL | Pandas | Spark |
|------|-----|--------|-------|
| 保存 | `INSERT INTO ...` | `df.to_csv("result.csv", index=False)` | `df.write.csv("result", header=True, mode="overwrite")` |

---

## 十五、Spark的F.什么时候用？

**F.就是 `from pyspark.sql import functions as F`**

### 需要用F.的情况：
- 所有内置函数：`F.sum()`, `F.avg()`, `F.round()`, `F.when()`
- 列引用：`F.col("cost")`
- 字符串常量：`F.lit("_")`

### 不需要F.的情况：
- DataFrame自身的方法：`df.select()`, `df.filter()`, `df.groupBy()`
- 简单的字符串表达式：`df.filter("cost > 1000")`

**记住：只要用到函数，就加F.。**

---

## 总结

**核心思想完全一样：选列 → 筛行 → 加列 → 分组聚合 → 排序 → 取前N**

只是语法不同：
- SQL：用关键字
- Pandas：用方法链
- Spark：和Pandas几乎一样
