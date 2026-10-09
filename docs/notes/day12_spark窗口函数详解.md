# Day12：Spark 窗口函数详解

## 一、窗口函数是什么？

窗口函数（Window Function）是一种特殊的函数，可以在一组相关的行（称为"窗口"）上进行计算，**不会把多行合并成一行**（和 groupBy 不同），每行都会返回一个结果。

### 和 groupBy 的核心区别

| 对比项 | groupBy | 窗口函数 |
|---|---|---|
| 结果行数 | 分组后每组只有1行 | 原表有多少行，结果就有多少行 |
| 计算范围 | 整个组 | 可以是组内的前N行、后N行、累计等 |
| 典型用途 | 分组聚合（求和、计数、平均） | 排名、累计、同比环比、移动平均 |
| 语法 | `df.groupBy("列").agg(...)` | `F.函数().over(Window.partitionBy(...).orderBy(...))` |

> 一句话：groupBy 是"把一组人变成一个人"（合并），窗口函数是"给每个人都算一个和他相关的统计值"（不合并）。

---

## 二、窗口定义的写法（核心！）

窗口定义由三部分组成，按顺序写，用 `.` 连接：

```python
from pyspark.sql import Window

window_spec = (
    Window.partitionBy("分组列")      # 第1部分：分区（可选）
          .orderBy("排序列")           # 第2部分：排序（排名/累计/环比时必选）
          .rowsBetween(开始, 结束)     # 第3部分：窗口范围（累计/移动平均时用，可选）
)
```

### 三部分详解

| 部分 | 语法 | 是否必选 | 作用 |
|---|---|---|---|
| 1. 分区 | `partitionBy("列1", "列2")` | 可选 | 按列分组，类似 groupBy，不写就是全局 |
| 2. 排序 | `orderBy("列1", F.desc("列2"))` | 排名/累计/环比时必选 | 每个分区内按什么排序 |
| 3. 窗口范围 | `rowsBetween(开始, 结束)` | 可选 | 计算时包含哪些行（累计/移动平均用） |

### rowsBetween 的特殊值

| 特殊值 | 意思 |
|---|---|
| `Window.unboundedPreceding` | 分区的第一行（最前面） |
| `Window.currentRow` | 当前行 |
| `Window.unboundedFollowing` | 分区的最后一行（最后面） |
| 负数（如 -2） | 当前行往前第2行 |
| 正数（如 2） | 当前行往后第2行 |

---

## 三、三类窗口函数

### 第一类：排名函数（最常用！）

| 函数 | 作用 | 特点 |
|---|---|---|
| `row_number()` | 行号，从1开始连续编号 | 即使值相同，排名也不同（1,2,3,4） |
| `rank()` | 排名，值相同排名相同，后面跳号 | 1,1,3,4（两个第1，然后是第3） |
| `dense_rank()` | 密集排名，值相同排名相同，后面不跳号 | 1,1,2,3（两个第1，然后是第2） |

#### 取 Top N 的标准写法（必背！）

```python
# 定义窗口：按渠道分区，按花费降序排序
window_spec = Window.partitionBy("channel").orderBy(F.desc("cost"))

# 先加排名列，再筛选排名 <= N
top_n = (
    df
    .withColumn("rn", F.row_number().over(window_spec))
    .filter(F.col("rn") <= 3)   # 取 Top 3
    .drop("rn")
)
```

---

### 第二类：聚合窗口函数（累计、移动平均）

普通的聚合函数（sum、avg、count）也可以用在窗口上。

#### 累计求和（必背！）

```python
# 定义窗口：按渠道分区，按日期排序，从第一行到当前行
window_cumulative = (
    Window.partitionBy("channel")
    .orderBy("date")
    .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)

# 累计求和
df.withColumn("cumulative_cost", F.sum("cost").over(window_cumulative))
```

> 注意：累计求和必须写 `rowsBetween(unboundedPreceding, currentRow)`，不然每行都是整个分区的总和！

#### 移动平均（N日移动平均）

```python
# 3日移动平均：当前行往前2行 + 当前行 = 最近3行
window_moving_3d = Window.orderBy("date").rowsBetween(-2, Window.currentRow)

df.withColumn("moving_avg_3d", F.avg("cost").over(window_moving_3d))
```

---

### 第三类：偏移函数（lag、lead）—— 环比必备！

| 函数 | 作用 |
|---|---|
| `lag("列名", 偏移量)` | 获取往前第N行的值 |
| `lead("列名", 偏移量)` | 获取往后第N行的值 |

#### 计算日环比（必背！）

```python
# 定义窗口：按渠道分区，按日期排序（不需要 rowsBetween！）
window_lag = Window.partitionBy("channel").orderBy("date")

df_growth = (
    df
    # 获取昨天的花费（往前1行）
    .withColumn("yesterday_cost", F.lag("cost", 1).over(window_lag))
    # 计算环比增长率 = (今天 - 昨天) / 昨天
    .withColumn("growth_rate", F.round(
        (F.col("cost") - F.col("yesterday_cost")) / F.col("yesterday_cost"), 4
    ))
)
```

> 注意：lag/lead 不需要写 rowsBetween，只需要 partitionBy + orderBy。

---

## 四、5个常用窗口定义模板（背下来！）

| 模板 | 窗口定义 | 适用场景 |
|---|---|---|
| 排名/Top N | `Window.partitionBy("列").orderBy(F.desc("列"))` | 每个渠道花费 Top N |
| 累计求和 | `Window.partitionBy("列").orderBy("列").rowsBetween(unboundedPreceding, currentRow)` | 累计销售额、累计花费 |
| 移动平均 | `Window.orderBy("列").rowsBetween(-(N-1), currentRow)` | 3日/7日移动平均 |
| 环比（lag） | `Window.partitionBy("列").orderBy("列")` | 日环比、周环比、月环比 |
| 分区全局聚合 | `Window.partitionBy("列")` | 每行带上所属渠道的总花费 |

---

## 五、常见错误

1. **累计求和忘了写 rowsBetween** → 每行都是整个分区的总和，不是累计
2. **排名函数写了 rowsBetween** → 排名函数不需要，写了反而可能出错
3. **lag/lead 写了 rowsBetween** → lag/lead 不需要，只需要 partitionBy + orderBy
4. **partitionBy 和 groupBy 混淆** → partitionBy 不合并行，groupBy 合并行

---

## 六、只分组不聚合是什么效果？

- `groupBy` 之后必须调用聚合函数，否则返回 `GroupedData` 对象，不能 `show()`，会报错
- 想"分组但不合并行" → 用窗口函数的 `partitionBy`
- 想"分组后把每组数据收集成数组" → 用 `groupBy + collect_list/collect_set`

| 需求 | 用什么 | 结果行数 |
|---|---|---|
| 分组后算聚合指标 | `groupBy + agg` | 每组1行（合并） |
| 分组但保留所有行 | `partitionBy + 窗口函数` | 原表多少行就多少行（不合并） |
| 分组后收集成数组 | `groupBy + collect_list` | 每组1行，包含数组 |

---

## 七、练习文件

- `spark_basics/06_window_functions.py`：窗口函数完整练习（排名/累计/移动平均/环比/综合实战+10道练习题）
