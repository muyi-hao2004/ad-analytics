# Spark 分布式原理与调优

本篇覆盖：为什么单机 pandas 撑不住、Spark 如何把计算拆到多台机器；Driver / Executor / Task / Partition / Stage / Job 的角色关系；RDD 与 DataFrame 的差别、Catalyst 优化器做了什么；懒执行与 DAG；宽依赖与窄依赖如何决定 Stage 切分；shuffle 的三个阶段与它为什么贵；数据倾斜的成因、识别与五种解法（加盐、两阶段聚合、广播小表 / map-side join、repartition、空值预处理）；广播变量；Spark 与 MapReduce 的差异；Spark Streaming 与 Flink 的微批 / 原生流之争，以及水位线、背压、精确一次；最后是 SQL / Pandas / Spark 三者的写法对照。

---

## 一、为什么需要分布式

pandas 的模型是"把整份数据装进一台机器的内存再算"。这个模型有两个硬上限：

1. **内存上限**：数据量超过可用内存就 OOM，几个 GB 的 CSV 就能压垮一台笔记本；
2. **算力上限**：单机 CPU 核数有限，处理 TB 级数据的时间不可接受。

Spark 的做法是把数据切成很多**分区**（partition），分散到多台机器的内存里同时计算，再把结果合并。同一套"拆开—并行—汇总"的思想，和调度系统把任务派给多个 worker 是一致的，只是对象从"任务"换成了"数据"。

| | pandas | Spark |
|---|---|---|
| 数据位置 | 单机内存 | 多机内存 + 磁盘 |
| 规模上限 | 百万到千万行 | TB / PB 级 |
| 故障处理 | 进程挂了就全没了 | task 失败自动重算 |
| 延迟 | 毫秒~秒 | 秒~分钟（有调度与 shuffle 开销） |

**关键认知**：分布式不是"更快的 pandas"。它有调度、序列化、网络传输、磁盘落盘的固定开销，小数据上反而比 pandas 慢得多。它的价值在于"数据量大到单机放不下时，仍然能算完"。

---

## 二、运行时角色

| 术语 | 是什么 | 类比 |
|---|---|---|
| **Driver** | 总控进程。解析代码、生成 DAG、切 Stage、派 Task、收结果，**自己不处理数据** | 调度室 |
| **Executor** | 真正干活的进程，一台机器可以跑多个，负责执行 Task 并缓存数据 | 分拣员 |
| **Task** | 最小执行单位，**一个 Task 处理一个 Partition** | 分拣员手里的一个筐 |
| **Partition** | 数据被切成的块，是并行度的单位 | 一筐包裹 |
| **Stage** | 一组可以并行执行的 Task，遇到 shuffle 就切开 | 一轮分拣 |
| **Job** | 一次 Action（`count()`、`write()`）触发的完整计算 | 一整批货的处理流程 |
| **Shuffle** | 按 key 把数据重新分组，需要跨机器搬数据 | 从"按揽收点分筐"改成"按目的地分筐" |

**一句话关系**：

```
Driver 派活 → 切成多个 Stage → 每个 Stage 拆成多个 Task
            → 每个 Task 处理一个 Partition → Executor 负责跑 Task
```

**和调度系统的对应关系**（同一套分布式思想，只是对象从"任务"换成"数据"）：

| Spark | Airflow / 分布式系统 |
|---|---|
| Driver | Scheduler（大脑） |
| Executor | Worker（干活） |
| Task | Task |
| Partition | Kafka 的 Partition |
| DAG | Airflow 的 DAG |

### 2.1 分区与 Task 的区别

| | Partition（分区） | Task（任务） |
|---|---|---|
| 是什么 | **数据**被切的一块 | **计算**，要执行的一段逻辑 |
| 层面 | 数据 | 执行 |
| 数量关系 | 一个 Task 处理一个 Partition，数量一一对应 | |

"每个 Stage 按分区切成 Task"不等于重新分区，只是"有几个分区，就派几个 Task 去分别处理"。

**真正的重新分区**发生在两处：shuffle 时（数据按 key 重新洗牌）、手动调用 `repartition()` / `coalesce()` 时。

**同一个 shuffle 的两个视角**：从流程看是"切 Stage"，从数据看是"重新分区"。

---

## 三、RDD 与 DataFrame

### 3.1 RDD

**是什么**：最底层的分布式数据抽象——不可变、可分区的元素集合。

**特点**：

- 不关心数据结构，把每条记录当成黑盒（Object）；
- 只提供粗粒度的 `map` / `filter` / `reduce` 等算子；
- 没有 schema，Spark 无法知道"这一列是什么类型"，因此**无法做列级优化**；
- 有血缘（lineage）：每个 RDD 记得自己由哪些父 RDD 经过什么算子得到，失败时可以重算。

### 3.2 DataFrame

**是什么**：RDD 之上、带列名和类型的分布式表。

**比 RDD 多的东西**：

1. **Schema**：知道每列的名字和类型；
2. **Catalyst 优化器**：能对执行计划做改写；
3. **更高效的存储**：列式内存布局（Tungsten），序列化开销远低于 Java 对象；
4. **更自然的 API**：`select` / `filter` / `groupBy`，接近 SQL。

### 3.3 Catalyst 做了什么

因为 DataFrame 知道 schema，优化器可以在执行前改写计划：

| 优化 | 含义 | 效果 |
|---|---|---|
| **谓词下推**（predicate pushdown） | 把 `filter` 尽量推到数据源附近，先过滤再 join | 参与 join 的数据量大幅减少 |
| **列裁剪**（column pruning） | 只读 SQL 里真正用到的列 | Parquet 列存下 I/O 成倍下降 |
| **常量折叠** | 编译期算掉 `1 + 2` 这类表达式 | 减少运行时计算 |
| **join 重排序** | 调整多表 join 的顺序，先接小结果 | 减少中间结果 |
| **广播判断** | 小表自动转 broadcast join | 消除一次 shuffle |

**这就是"能写 DataFrame 就不要写 RDD"的根本原因**：RDD 是黑盒，优化器无从下手。

### 3.4 完整执行链路

```
代码
 → Catalyst 优化
 → 逻辑计划（Logical Plan）
 → 物理计划（Physical Plan）
 → 切 Stage（按 shuffle 边界）
 → 切 Task（按 partition）
 → Driver 派发
 → Executor 执行
```

面试时能把这条链路说完整，比只背"Spark 快"有说服力得多。

---

## 四、懒执行与 DAG

### 4.1 Transformation 与 Action

| 类别 | 定义 | 常见算子 |
|---|---|---|
| **Transformation** | 惰性操作，只记录"要做什么"，不立即计算 | `select`、`filter`、`withColumn`、`groupBy`、`join`、`orderBy`、`distinct` |
| **Action** | 触发真正计算，返回结果或写出数据 | `show`、`count`、`collect`、`take`、`first`、`write`、`foreach` |

```python
df = spark.read.csv("data.csv")        # ① 读（Transformation）
df2 = df.filter(df.clicks > 0)         # ② 过滤（还没算）
df3 = df2.groupBy("channel").sum()     # ③ 聚合（还没算）
df3.show()                              # ④ Action，这一刻才真正执行
```

①②③ 只是在 DAG 上追加节点，直到 ④ 出现，Spark 才把整条链路一起优化、一起执行。

### 4.2 为什么要懒执行

如果每一步都立即执行，Spark 就只能看到"当前这一步"，无法做全局优化。延迟到 Action 才执行，优化器能看到完整的计划图，从而：

- 合并可以合并的算子（多个 `filter` 合成一个）；
- 把过滤条件下推；
- 裁掉用不到的列；
- 决定 join 策略。

**代价**：报错也被延迟。一个列名写错，可能要到 `show()` 那一刻才抛 `AnalysisException`，排查时要往回找。

### 4.3 DAG

DAG（有向无环图）是算子依赖关系的图。它记录了"哪些算子的输出喂给哪些算子"，是切分 Stage 的依据。

**为什么无环**：数据变换是单向的，一个 DataFrame 不能依赖自己的输出，否则会形成无限递归。

---

## 五、宽依赖与窄依赖

### 5.1 窄依赖（Narrow Dependency）

**定义**：每个父分区的数据只流向**一个**子分区，不需要跨机器搬数据。

```
父分区1 → 子分区1
父分区2 → 子分区2     （一一对应）
父分区3 → 子分区3
```

**典型算子**：`filter`、`map`、`select`、`withColumn`、`union`（某些情况）、`coalesce`。

**特点**：各分区独立计算，可以在同一个 Stage 内流水线执行，快。

### 5.2 宽依赖（Wide Dependency）

**定义**：一个父分区的数据要分散到**多个**子分区，必须跨机器搬数据，即 shuffle。

```
父分区1 ─┬→ 子分区1
父分区2 ─┼→ 子分区1     （相同 key 的数据被"洗"到一起）
父分区3 ─┘
```

**典型算子**：`groupBy`、`join`、`distinct`、`orderBy`、`repartition`。

**特点**：必须等所有父分区写完，才能开始下一阶段，中间要落盘和走网络。

### 5.3 Stage 怎么切

**规则只有一句：遇到宽依赖（shuffle）就切一刀，形成新的 Stage。**

```
filter（窄）→ groupBy（宽，切！）→ agg（窄）→ orderBy（宽，切！）→ show
    Stage 1                Stage 2                  Stage 3
```

由此得到一条可背的规律：**Stage 数 = shuffle 数 + 1**。

- Stage 内部全是窄依赖，各分区独立算，可以 pipeline；
- Stage 之间是宽依赖，必须等上游全部完成，落盘、传输、再读。

**面试题"Spark 为什么慢"，答案几乎都是"shuffle 太多"。**

---

## 六、Shuffle 到底发生了什么

### 6.1 为什么必须 shuffle

**key = 你按哪个字段分组或匹配。**

| 操作 | key | 具体 key 值 |
|---|---|---|
| `groupBy("channel").sum("cost")` | `channel` 列 | Facebook、Google、TikTok |
| `groupBy("channel", "date")` | channel + date 的组合 | ("Facebook", "2026-10-09") |
| `df1.join(df2, "campaign_id")` | `campaign_id` 列 | 每个计划 ID |

原始明细：

| date | channel | cost |
|---|---|---|
| 10-09 | Facebook | 100 |
| 10-09 | Google | 50 |
| 10-09 | Facebook | 80 |
| 10-09 | TikTok | 30 |
| 10-09 | Facebook | 120 |

按 channel 聚合后：

| channel | sum(cost) |
|---|---|
| Facebook | 300 |
| Google | 50 |
| TikTok | 30 |

**核心一句**：channel 值相同的那 3 行（Facebook），**必须被送到同一个地方**才能加起来。

如果 Facebook 不是 3 行而是 85000 行，这 85000 行照样必须全部挤进同一个分区，由同一个 Task 处理。这就是数据倾斜的根源。

**复合 key**：key 可以是多个字段的组合。`groupBy("channel", "date")` 的 key 是二元组，每一个不同的 (渠道, 日期) 组合算一个独立的 key 值。

### 6.2 三个阶段的底层过程

以"按 channel 分组求和"为例，数据分散在 2 个分区（2 台机器）：

```
阶段 1（Map 端）：每台机器先把自己手里的数据按 key 分类，写盘
阶段 2（Shuffle 搬运）：按哈希值，把相同 key 的数据跨机器搬运、归到同一个新分区
阶段 3（Reduce 端）：数据归队后，在新分区上聚合
```

```
Map Task（本地分类写盘）→ 跨机器搬运（shuffle）→ Reduce Task（聚合）
```

"新分区"不是重新切数据，而是**同 key 的数据被搬运归组到一起**。

### 6.3 为什么同一个 key 必须进同一个分区

机制是 **hash 取模**：

```
分区号 = hash(key) % 分区总数
```

hash 的关键特性是确定性：同一个字符串永远算出同一个数字。

```
"Facebook" → 12345678（永远是这个数）
12345678 % 8 = 6
→ 所有 Facebook 的行都去 6 号分区
```

**这不是"挑出来"的，是"算出来"的。** 不存在"检查一下是不是 Facebook 再放一起"这个过程——每行的 key 经过同一个公式计算，自然得到同一个结果。所以数据量大小完全不影响结果：8.5 万行 Facebook 照样全部落进同一个分区。

**为什么必须这样设计，两个原因**：

1. **聚合才能一次算完**：如果 Facebook 的行分散在 8 个分区，每个分区只能算局部和，最后还要再有一个步骤把 8 个局部和加起来，多一步。聚在一起，一个 Task 直接算出总和。
2. **join 两边必须能碰上面**（更关键）：大表的 Facebook 去了分区 6，小表的 Facebook 也必须去分区 6，才能在分区 6 本地完成匹配。如果两边去了不同分区，join 结果直接出错。所以 join 两侧必须用完全相同的 hash 函数和分区数——这是**正确性**要求，不是性能问题。

### 6.4 为什么 shuffle 贵

数据要经历：

```
写磁盘（按 key 分组写出来）→ 网络传输（发给别的机器）→ 读磁盘（对方读进来）
```

机器内部读内存是纳秒级，跨机器网络传输是毫秒级，差了几个数量级。"落盘 + 走网络"比"内存里算"慢得多。

**面试记住这句**：shuffle 是 Spark 中最昂贵的操作，优化的核心就是减少 shuffle，或让 shuffle 更均匀。

### 6.5 哪些操作会触发 shuffle

| 操作 | 为什么 |
|---|---|
| `groupBy(...).agg(...)` | 要把相同 key 的行聚到一起 |
| `join` | 要把两边相同 key 的行凑到一起 |
| `distinct()` | 要去重，得先按 key 归拢 |
| `orderBy()` / `sort()` | 全局有序，必须知道全量数据 |
| `repartition(n)` | 显式重新分区 |
| `repartitionByRange` | 按范围重分区 |

**不触发 shuffle 的**：`filter`、`select`、`withColumn`、`union`、`coalesce`（只合并分区，不搬 key）。

### 6.6 reduceByKey 与 groupByKey

同样是按 key 聚合，两者差别很大：

| | `groupByKey` | `reduceByKey` |
|---|---|---|
| 行为 | 把同 key 的所有 value 拉成一个迭代器 | 在 map 端先做本地聚合，再传结果 |
| 网络传输量 | 全量数据 | 每 key 一个局部结果 |
| 内存 | 同 key 数据全压在一个分区，容易 OOM | 压力小得多 |
| 适用 | 需要保留全部原始值（如收集成列表） | 求和、计数、取最大最小值 |

**原则**：能用 `reduceByKey` 就别用 `groupByKey`。前者在 shuffle 前先本地聚合，少传大量数据。这条在 RDD API 里尤其重要；DataFrame 的 `groupBy().agg()` 由 Catalyst 自动做了 map 端预聚合（部分聚合），所以写 DataFrame 时不用手动操心这一点。

---

## 七、Spark 是内存计算吗

**不是纯内存，是"内存为主 + 磁盘兜底"。**

- 中间计算尽量放内存，所以比 Hadoop MapReduce 快；
- **shuffle 时写盘**：要等所有 Map 端完成，且数据量可能超过内存；
- **内存不足时 spill（溢写）到磁盘**：数据超过 executor 内存时，部分结果落盘。

**面试别答"纯内存"**，要答"内存为主，shuffle 和 spill 时落盘"。答"纯内存"会引出"那数据量超过内存怎么办"的追问，很难圆。

---

## 八、数据倾斜

### 8.1 一句话定义

**shuffle 时某个 key 的数据量特别大，导致负责这个 key 的那一个 Task 要处理绝大部分数据，其他 Task 早早干完只能干等。**

### 8.2 现象

10 万行里 Facebook 占 85%（85000 行），其余 4 个渠道各 3750 行。因为"同一个 key 必须去同一个分区"，85000 行全部挤进同一个分区，由同一个 Task 处理：

| Task | 数据量 | 耗时 |
|---|---|---|
| Task 1 | 3750 行 | 0.2 秒 |
| Task 2 | 3750 行 | 0.2 秒 |
| **Task 3** | **85000 行** | **45 分钟** |
| Task 4 | 3750 行 | 0.2 秒 |
| Task 5 | 3750 行 | 0.2 秒 |

**整个 Job 的完成时间 = 最慢的那个 Task，不是平均时间。** 花了 5 个 executor 的资源，只得到 1 个 executor 的速度。

**更极端的后果是 OOM**：如果这个 key 的数据量大到单个 executor 的内存装不下，任务直接失败。这时候不是慢的问题，是根本跑不出来。

### 8.3 怎么发现

三个信号：

1. **Spark UI 上某个 Stage 卡在 99%**——剩 1~2 个 Task 跑几十分钟；
2. **Task 耗时极不均匀**——大部分几秒完成，个别几十分钟；
3. **直接 OOM 或 Executor lost**——单个 Task 数据量超过 executor 内存。

### 8.4 怎么定位是哪个 key

```python
df.groupBy("channel").count().orderBy(F.desc("count")).show()
```

输出形如：

```
|  channel|count|share_pct|
| Facebook|85000|     85.0|   ← 就是它
|   Google| 3750|      3.75|
```

**这一步必须先做。** 不确认是不是大 key 问题，后面所有优化都是瞎猜。

### 8.5 三大成因

| # | 原因 | 大白话 | 典型场景 |
|---|---|---|---|
| 1 | **业务大 key** | 数据本身就是二八分布，头部占绝大部分 | 一个大计划吃掉 80% 预算和日志 |
| 2 | **空值抱团** | `null` / `""` / `-1` 被当成同一个 key，全挤到一起 | 脏数据没清洗，某字段大量为空 |
| 3 | **类型不一致** | 一边 `"123"`、一边 `123`，匹配不上，数据全落到少数分区 | 两表 join 的字段类型不匹配 |

#### 空值为什么会抱团

在 Spark 眼里，`null` 就是一个普通的 key 值，跟 `"Facebook"` 没有任何区别。key 值相同的行必须进同一个分区，所以所有 null 行也会老老实实挤进同一个分区。

广告数据里 `campaign_id` 这个字段，有些渠道的 API 不返回计划层级数据，这些行就是空的：

```sql
SELECT COUNT(*) FROM ad_data WHERE campaign_id IS NULL;
-- 结果：120000（100 万行里有 12 万行是空的）
```

此时跑 `groupBy("campaign_id").sum("cost")`：

| key 值 | 行数 | 由谁处理 |
|---|---|---|
| campaign_001 | 8,000 | Task 1 |
| campaign_002 | 6,000 | Task 2 |
| **null** | **120,000** | **Task 3 ← 它就是最大的那个 key** |

Task 3 的数据量是其他的 15~20 倍，它跑多久整个任务就跑多久。

**不只是 null，这些一样会抱团**：只要值相同，就是同一个 key。

- `null`
- `""`（空字符串）
- `-1` / `0`（用特殊数字表示缺失）
- `"unknown"` / `"N/A"` / `"无"`

**两个容易搞混的细节**：

1. **groupBy 时**：null 会作为一个独立的分组出现在结果里，你会看到结果里有一行 key 是 null，且 count 特别大；
2. **join 时**：按 SQL 语义 `null != null`，所以 null 的行匹配不上任何行。但它们**照样会被 shuffle**（白白搬一趟数据），join 完就没了。所以 join 前把 null 过滤掉，既省时间又避免倾斜。

**怎么排查**：

```python
df.filter(F.col("campaign_id").isNull()).count()

df.groupBy("campaign_id").count().orderBy(F.desc("count")).show()
# 如果第一行是 null 且 count 特别大，就是这个原因
```

**三种处理方式（从简单到复杂）**：

| 方式 | 做法 | 什么时候用 |
|---|---|---|
| 1. 直接过滤 | `df.filter(F.col("campaign_id").isNotNull())` | 业务上不需要这些脏数据，最省事，优先选它 |
| 2. 回填真实值 | 按渠道 / 规则把 null 补成有意义的 ID | 数据不能丢，且能补得回来 |
| 3. 加随机后缀打散 | 见下方代码 | 数据必须保留，且量大到会倾斜 |

**方式 2 的坑**：如果回填的是同一个默认值（比如全部填 `"unknown"`），它们**还是同一个 key**，照样倾斜。回填必须有区分度。

方式 3 的代码：

```python
df.withColumn(
    "campaign_id",
    F.when(
        F.col("campaign_id").isNull() | (F.col("campaign_id") == ""),
        F.concat(F.lit("__null_"), (F.rand() * 20).cast("int")),
    ).otherwise(F.col("campaign_id")),
)
```

实测效果：原本聚成一坨的 null 被打散成 20 份，每份约 500 行，压力摊平。

### 8.6 解法一：加盐

**大白话**：把 85000 行 Facebook 随机分成 10 小份，给每份改个名字：`Facebook_0`、`Facebook_1`……`Facebook_9`。这样 10 个 Task 各处理 8500 行，压力摊平。最后再把这 10 份的小计加起来，就是 Facebook 的总数。

**关键洞察**：加盐就是故意打破"相同 key 必须同分区"这条规则。

| 做法 | 效果 | 代价 |
|---|---|---|
| 默认：相同 key 必须同一分区 | 一次聚合就完事 | 可能严重倾斜 |
| 加盐：把 Facebook 拆成 Facebook_0 ~ Facebook_9 | 变成 10 个不同 key，分散到 10 个分区 | 要多一次聚合 |

```python
# 阶段 1：给 key 加随机后缀（盐），打散后做局部聚合
salted = df.withColumn("salt", (F.rand() * 10).cast("int")) \
           .withColumn("salted_key", F.concat_ws("|", F.col("channel"), F.col("salt")))
partial = salted.groupBy("salted_key").agg(F.sum("cost").alias("partial_sum"))

# 阶段 2：去掉盐，把局部结果合并成全局结果
result = partial.withColumn("channel", F.split("salted_key", r"\|", limit=2).getItem(0)) \
                .groupBy("channel").agg(F.sum("partial_sum").alias("total_cost"))
```

实测效果：

```
原始 shuffle：    [3750, 85000, 7500, 3750]          最大/平均 = 3.4x   明显倾斜
加盐后 shuffle：  [18181, 11098, 10129, 27693, ...]  最大/平均 = 2.2x   改善明显
```

**代价**：多了一次 shuffle。所以只在大 key 确实明显时才用，别无脑加。

**N 取多少**：经验值 10~50。太小打散不够，太大第二阶段压力大。可以看热点 key 的数据量和普通 key 的比值来定，让打散后每个分区数据量接近平均值。

**面试加分说法**：同一个 key 进同一个分区不是物理定律，是 Spark 为了一次算完、以及保证 join 正确性做的设计选择。加盐就是故意打破它，代价是多一次 shuffle。

### 8.7 解法二：两阶段聚合

**是什么**：把一次全局聚合拆成"局部聚合 + 全局聚合"两步。第一阶段在各分区内先算局部结果，第二阶段只对局部结果做全局合并。

**为什么有效**：第一阶段的输入是海量明细，但输出只有"key × 局部份数"条，第二阶段的数据量比原始数据小几个数量级。即使某个 key 仍然偏大，被压缩后的局部结果也远小于明细。

**与加盐的关系**：加盐是"让第一阶段能并行"的手段，两阶段聚合是"整体流程"。两者通常一起用——加盐把一个大 key 拆成 N 个，局部聚合把 N 份各自算小计，第二阶段再去盐合并。

**不依赖加盐的两阶段聚合也成立**：当聚合函数可结合（sum、count、max、min）时，先按 `(key, 分区id)` 聚合一次，再按 `key` 聚合一次。Spark 在 DataFrame API 里对 `groupBy().agg()` 已经自动做了 map 端部分聚合（partial aggregation），所以纯 DataFrame 场景下这部分优化是默认开启的；RDD API 则需要用 `reduceByKey` 或 `combineByKey` 手动实现。

**判断能否两阶段**：聚合逻辑必须满足结合律。`avg` 不能直接两阶段（要先转成 `sum` 和 `count` 两个可加的量，最后再相除），`countDistinct` 也不能直接拆（需要 HyperLogLog 这类近似算法或先局部去重再全局去重）。

### 8.8 解法三：广播小表（map-side join）

**大白话**：既然 shuffle 搬数据很贵，那如果其中一张表很小，干脆给每个 executor 都发一份完整副本，大家各自在本地查，别搬了。

```python
result = big.join(F.broadcast(small), on="channel", how="inner")
```

**为什么叫 map-side join**：普通 join 是"两边都按 key 重分区，再在 reduce 端匹配"（reduce-side join）；广播 join 把匹配动作提前到 map 端，每个 map task 用本地那份小表直接完成匹配，所以叫 map-side join。

**适用条件**：小表小于 100MB（Spark 默认阈值 10MB，可调）。**这是最优先尝试的方案**——一行代码，彻底消灭 shuffle。

相关参数：

```python
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", 100 * 1024 * 1024)
```

设为 -1 可以关闭自动广播。

**风险**：小表太大会撑爆 driver 和 executor 的内存。别把 2GB 的表拿去广播。超过阈值时 Spark 会退回到 Sort-Merge Join。

### 8.9 解法四：repartition / 调大分区数

```python
spark.conf.set("spark.sql.shuffle.partitions", 400)   # 默认 200
df.repartition(400)                                    # 显式重分区（会 shuffle）
df.repartition(400, "channel")                         # 按列重分区
df.coalesce(50)                                        # 减少分区，不 shuffle
```

**原理**：分区越多，每个 Task 分到的数据越少。

**关键限制**：**这对"多个中等 key"有效，对单个超级大 key 无效**——因为同一个 key 再怎么分，也必须在同一个分区。

> 类比：多开几条分拣线。但如果深圳的包裹规定只能走某一条线，开再多线也没用。

这条是区分"真懂"和"背答案"的关键。面试官问"调大分区数能解决倾斜吗"，正确回答是"只能缓解多中等 key 的情况，单一大 key 无效"。

**repartition 与 coalesce 的区别**：

| | `repartition(n)` | `coalesce(n)` |
|---|---|---|
| 是否 shuffle | 是 | 否（只合并相邻分区） |
| 能否增加分区 | 能 | 不能（只能减少） |
| 数据分布 | 均匀 | 可能不均 |
| 用途 | 打散、扩容并行度 | 写出前减少小文件 |

### 8.10 解法五：大表 join 大表（热点 key 单独处理）

广播用不了（两边都大），那就要精细操作：

1. 先找出那几个特别大的 key（比如就 Facebook 一个）；
2. **大表**：Facebook 的行加随机盐，打散成 10 份 → `Facebook_0` … `Facebook_9`；
3. **小表**：Facebook 那一行**复制 10 份**，分别叫 `Facebook_0` … `Facebook_9`，这样大表打散后每一份都能在小表里找到对应行；
4. 除 Facebook 以外的其他 key：两边都不动，正常 join；
5. 最后把两部分结果 `union` 合并。

**类比**：深圳的包裹太多，就单独给深圳加开 10 个窗口，同时把《邮编对照表》里深圳那一页复印 10 份发给这 10 个窗口。其他城市照常走原窗口。

**关键验证**：**加盐 join 后总行数必须等于原始行数**（实测 100000 = 100000），否则就是膨胀了或丢数据了。这是判断写对没有的唯一标准。

**面试价值**：能讲到这一步，说明真的处理过生产问题，不是背答案。

### 8.11 兜底：AQE（自适应查询执行）

Spark 3.x 的 **AQE**（Adaptive Query Execution，自适应查询执行）能在运行时根据统计信息自动处理一部分倾斜 join：

```python
spark.conf.set("spark.sql.adaptive.enabled", "true")
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")
```

**边界**：AQE 对 join 倾斜有帮助，对聚合倾斜和复杂场景仍需手动干预。

**加分说法**：生产上我会开 AQE，但不能完全依赖它。

### 8.12 面试标准回答

> **问：遇到过数据倾斜吗？怎么解决的？**
>
> 遇到过。排查顺序是：
>
> **第一步先定位**——看 Spark UI 哪个 stage 卡住，然后对 key 做 `groupBy(key).count().orderBy(desc)` 找出热点 key，确认是不是大 key 问题。
>
> **第二步分情况处理**：
>
> - 如果是**聚合倾斜**，用两阶段加盐，先局部聚合再全局聚合；
> - 如果是 **join 倾斜**，先看小表能不能广播（100MB 以内直接 broadcast，最省事）；
> - 广播不了的大表 join 大表，就把热点 key 单独拆出来加盐处理，小表对应 key 膨胀 N 份，两边 join 完再 union 回去。
>
> **日常预防**：ETL 里对 null / 空字符串做预处理，别让它们聚成同一个 key；join 前检查两侧字段类型是否一致，类型不一致会导致所有数据落到少数分区。

### 8.13 追问预判

- **什么是 shuffle，为什么慢** → 按 key 跨机器重分组的全过程，要写盘、走网络、再读盘，比内存计算慢几个数量级。`groupBy`、`join`、`distinct`、`orderBy` 都会触发。
- **加盐的 N 取多少** → 经验值 10~50，按热点 key 与普通 key 的数据量比值来定，目标是打散后每个分区接近平均值。
- **广播 join 有什么风险** → 小表太大会撑爆 driver 和 executor 内存；超过阈值会退回 Sort-Merge Join。
- **怎么判断是类型不一致导致的** → 看两侧 schema，一个 `string` 一个 `bigint` 就会出现。Spark 不报错，只是数据 hash 到极少数分区。用 `printSchema()` 检查。
- **调大分区数能解决所有倾斜吗** → 不能。单个超级大 key 无效，因为同一个 key 必须在同一个分区。
- **Spark 不会自动处理吗** → 3.x 的 AQE 能处理一部分 join 倾斜，聚合倾斜和复杂场景仍需手动干预。

---

## 九、广播变量

**是什么**：把一个只读的小数据集（字典、维表、配置）广播到每个 executor，让每个 Task 直接读本地副本，而不是每个 Task 各传一份。

```python
# 广播一个 Python 字典（不是 DataFrame）
channel_map = spark.sparkContext.broadcast({
    "Facebook": "社交",
    "Google": "搜索",
    "TikTok": "短视频",
})

# 在 UDF 里使用
from pyspark.sql.functions import udf
from pyspark.sql.types import StringType

def map_channel(ch):
    return channel_map.value.get(ch, "其他")

df.withColumn("channel_type", udf(map_channel, StringType())("channel"))
```

**为什么需要**：不用广播时，每个 Task 都会把这份数据序列化一遍随任务发送。1000 个 Task 就是 1000 份副本，网络和内存都浪费。广播后每个 executor 只存一份，所有 Task 共享。

**与广播 join 的关系**：`F.broadcast(df)` 是广播变量的表级封装——把小表整体广播，让 join 在 map 端完成。底层的分发机制是同一套。

**注意事项**：

- 广播变量是**只读**的，executor 端不能修改；
- 只适合小数据（几十 MB 以内），大对象广播会拖垮 driver；
- 用完可以 `unpersist()` 释放，但通常由 GC 处理；
- 广播的是 driver 端的变量快照，之后 driver 再改这个变量，executor 看不到。

---

## 十、Spark 与 MapReduce 的区别

源笔记里没有系统的 MapReduce 对比，只在两处直接提到：Spark 是"内存为主 + 磁盘兜底"所以比 Hadoop 快；以及 `reduceByKey` 比 `groupByKey` 少传数据。基于这两点可以确定的差异是：

| 维度 | MapReduce | Spark |
|---|---|---|
| 中间结果 | 每个 Job 的中间结果落 HDFS | 尽量留在内存，shuffle / spill 时才落盘 |
| 计算模型 | 只有 Map 和 Reduce 两段，多步逻辑要串联多个 Job | DAG 表达任意多阶段依赖，一个 Job 内可完成多步 |
| 迭代计算 | 每轮都要读写 HDFS，磁盘 I/O 是瓶颈 | 可 `cache()` 复用中间结果，迭代快 |
| 任务粒度 | 每个 Task 是一个 JVM 进程，启动开销大 | 线程级 Task，复用 executor 进程 |
|  shuffle 优化 | 无 map 端预聚合的通用机制 | `reduceByKey` 等算子支持 map 端 combine |

**（源笔记未展开）**：MapReduce 的编程接口、Combiner、Partitioner 细节，以及 Spark on YARN 的资源调度差异，源笔记均未涉及。

**面试安全答法**：Spark 相对 MapReduce 的核心优势是"中间结果不落 HDFS + DAG 多阶段流水线 + 线程级任务复用"，所以迭代式和交互式场景快一个数量级；但 Spark 不是纯内存，shuffle 和 spill 仍要落盘。

---

## 十一、Spark Streaming 与 Flink

### 11.1 为什么需要实时计算

离线（批处理）：数据攒够一天 → 半夜统一算 → 出昨天的结果，**要等一天**。

实时：数据一边进来一边算，秒级 / 毫秒级出结果。

| 场景 | 需要的时效 |
|---|---|
| 广告实时出价 | 毫秒级 |
| 反欺诈（检测盗刷） | 秒级 |
| 实时大屏（大促 GMV） | 秒级 |

**离线 = 攒一批再算（慢、准、能算复杂逻辑）；实时 = 来一条算一条（快、逻辑相对简单）。**

**批与流的本质区别**：

- **批（Batch）**：一坨静止数据，像"一池子水"，舀起来算；
- **流（Stream）**：持续涌进来的数据，像"一条河"，水不停流。

### 11.2 微批 vs 原生流

| | Spark Streaming | Flink |
|---|---|---|
| 本质 | 把流切成"微批"计算，本质还是批 | 真正的一条一条处理（原生流） |
| 延迟 | 秒级 | 毫秒级 |
| 现状 | 逐渐被取代 | 实时计算主流 |
| 编程语言 | PySpark 是一等公民 | 主流 Java / Scala，PyFlink 小众 |

**关键区别（必考）**：Spark Streaming 是"微批"思路——把流按固定间隔（比如每 1 秒）切成一批，用批引擎算，所以延迟下限就是批间隔；Flink 是真流式，来一条算一条，延迟更低。

现在提"实时计算"默认指 Flink。

### 11.3 Source / Sink

- **Source**：数据从哪来（Kafka、数据库、文件）；
- **Sink**：结果写到哪（数据库、另一个 Kafka、大屏）。

Kafka 是最常见的 Source：它本身是分布式的、可回放的日志队列，天然适配流处理。

### 11.4 State（状态）

**是什么**：流处理的"记忆"，用来跨数据累积信息。

**为什么需要**：实时统计"每个广告累计点击"，处理新数据时必须记得之前的总数，才能 +1。

```
来一条"抖音点击" → 查状态：抖音之前 999 → +1 → 存回：1000
```

- 状态是"每个 key 各一份"；
- **没有 State，Flink 只能做单条变换，做不了计数、求和、去重。**

### 11.5 Checkpoint（检查点）

定期把 State 快照存到可靠存储（如 HDFS）。机器挂了，从 Checkpoint **恢复状态**接着算。

**State 是"记忆"，Checkpoint 是"给记忆做备份防失忆"。**

### 11.6 Event Time 与 Processing Time

| 时间类型 | 是什么 | 特点 |
|---|---|---|
| **Processing Time** | 数据**到达**计算节点的时间 | 简单，但受网络延迟影响，结果不可复现 |
| **Event Time** | 数据**真实发生**的时间 | 准，但要处理乱序和迟到 |

生产环境一般用 **Event Time + Watermark**。

**为什么 Event Time 难**：数据到达顺序可能乱（后发生的先到），而且会迟到。按 Processing Time 开窗，同一批数据在不同时间跑会得到不同结果；按 Event Time 开窗，结果可复现，但要解决"窗口什么时候关"的问题。

### 11.7 Watermark（水位线）

**问题**：数据乱序且会迟到，窗口该不该关？急着关会漏数据，一直等又永远等不完。

**解决**：Watermark 让窗口"到点不立刻关，再多等一小段容错时间"，让迟到数据追上。

**类比**：老师收作业，10 点截止，但实际等到 10:05 才开始批改。多等的 5 分钟就是给迟到的人留的时间。

**迟到太久的三层兜底**：

1. **Watermark**：容忍小迟到（秒~分钟级）；
2. **Allowed Lateness**：窗口关了还留一段时间，迟到的数据补进结果，并触发窗口重新计算；
3. **Side Output**：迟到太久的放到"旁路"流，单独收集处理，不丢弃。

### 11.8 窗口

无限流不能等流结束，必须切成"窗口"计算：

| 窗口 | 定义 |
|---|---|
| **滚动（Tumbling）** | 固定长度、互不重叠，如每 5 分钟一个窗口 |
| **滑动（Sliding）** | 固定长度、有重叠，如窗口 5 分钟、每 1 分钟滑动一次 |
| **会话（Session）** | 按活动间隔切分，用户一段时间没有动作就断开会话 |

### 11.9 背压与精确一次（源笔记未展开）

这两个是实时计算的常见追问，但源笔记只覆盖了 Checkpoint 的"恢复状态"作用，没有展开机制细节，这里只标注边界：

- **背压（Back Pressure）**：当下游处理不过来时，上游需要降速，否则数据堆积、内存爆掉。Flink 基于 Credit 的流量控制、Spark Streaming 的 `spark.streaming.backpressure.enabled` 都是这个思路。**（源笔记未展开）**
- **精确一次（Exactly-Once）**：端到端保证每条数据只影响结果一次。Flink 依赖 Checkpoint + 两阶段提交 Sink；Spark Streaming 依赖 WAL / 幂等写。**（源笔记未展开）**

面试被追问时，安全的说法是"依靠 Checkpoint 做状态快照与恢复，Sink 侧配合事务或幂等写入实现端到端一致"，不要展开没把握的细节。

### 11.10 和已学内容的连接

| 已学 | 在实时计算里 |
|---|---|
| Kafka | 最常见的 Source |
| Spark 分区 / 并行 | Flink 也有并行度概念 |
| shuffle | Flink 也有（`keyBy` 分组时） |
| 累加器 | 类似 State 的思想 |

**实时计算不是全新领域，是已有知识的延伸。**

### 11.11 要不要学 Flink

- Flink 核心用 Java 写，生产代码主流是 Java / Scala，PyFlink 相对小众；
- Spark 的 PySpark 是一等公民，生态成熟；
- 数据工程岗面试**懂概念即可，不必会写**。重心仍应放在 Spark 与数据链路上。

---

## 十二、SQL / Pandas / Spark 三者写法对比

**核心思想完全一样**：选列 → 筛行 → 加列 → 分组聚合 → 排序 → 取前 N。只是表达方式不同：

- SQL 用关键字；
- Pandas 用方法链；
- Spark 用方法链，和 Pandas 几乎一样。

### 12.1 读取与查看

| 操作 | SQL | Pandas | Spark |
|---|---|---|---|
| 读表 | `SELECT * FROM ad_data` | `pd.read_csv("data.csv")` | `spark.read.csv("data.csv", header=True)` |
| 读前 N 行 | `LIMIT 5` | `df.head(5)` | `df.show(5)` |
| 看列名 | `DESCRIBE ad_data` | `df.columns` | `df.printSchema()` |
| 看行数 | `SELECT COUNT(*)` | `len(df)` | `df.count()` |

### 12.2 选择列与筛选行

| 操作 | SQL | Pandas | Spark |
|---|---|---|---|
| 选单列 | `SELECT channel` | `df["channel"]` | `df.select("channel")` |
| 选多列 | `SELECT channel, cost` | `df[["channel","cost"]]` | `df.select("channel","cost")` |
| 重命名 | `SELECT cost AS total_cost` | `df.rename(columns={"cost":"total_cost"})` | `df.withColumnRenamed("cost","total_cost")` |
| 等于 | `WHERE channel='Facebook'` | `df[df["channel"]=="Facebook"]` | `df.filter(F.col("channel")=="Facebook")` |
| 多条件 AND | `WHERE a=1 AND b>2` | `df[(df["a"]==1)&(df["b"]>2)]` | `df.filter((F.col("a")==1)&(F.col("b")>2))` |
| IN | `WHERE channel IN (...)` | `df[df["channel"].isin([...])]` | `df.filter(F.col("channel").isin(...))` |
| 模糊 | `LIKE '%Face%'` | `df["channel"].str.contains("Face")` | `df.filter(F.col("channel").like("%Face%"))` |
| 空值 | `WHERE cost IS NULL` | `df[df["cost"].isnull()]` | `df.filter(F.col("cost").isNull())` |

### 12.3 新增列与条件判断

| 操作 | SQL | Pandas | Spark |
|---|---|---|---|
| 计算列 | `cost/clicks AS CPC` | `df["CPC"] = df["cost"]/df["clicks"]` | `df.withColumn("CPC", F.col("cost")/F.col("clicks"))` |
| 条件判断 | `CASE WHEN cost>1000 THEN '高' ELSE '低' END` | `df["cost"].apply(lambda x: "高" if x>1000 else "低")`，或 `np.where` | `F.when(F.col("cost")>1000,"高").otherwise("低")` |
| 四舍五入 | `ROUND(cpa, 2)` | `df["cpa"].round(2)` | `F.round(F.col("cpa"), 2)` |
| 绝对值 | `ABS(x)` | `x.abs()` | `F.abs(x)` |
| 转大写 | `UPPER(channel)` | `df["channel"].str.upper()` | `F.upper(F.col("channel"))` |
| 字符串拼接 | `CONCAT(a,'_',b)` | `df["a"]+"_"+df["b"]` | `F.concat(F.col("a"), F.lit("_"), F.col("b"))` |

### 12.4 聚合与排序

| 操作 | SQL | Pandas | Spark |
|---|---|---|---|
| 分组计数 | `COUNT(*) GROUP BY channel` | `df.groupby("channel").size()` | `df.groupBy("channel").count()` |
| 分组求和 | `SUM(cost) GROUP BY channel` | `df.groupby("channel")["cost"].sum()` | `df.groupBy("channel").sum("cost")` |
| 分组平均 | `AVG(cost) GROUP BY channel` | `df.groupby("channel")["cost"].mean()` | `df.groupBy("channel").avg("cost")` |
| 两列分组 | `GROUP BY channel, date` | `df.groupby(["channel","date"])["cost"].sum().reset_index()` | `df.groupBy("channel","date").sum("cost")` |
| 多聚合改名 | `SUM(cost) total_cost, AVG(cost) avg_cost` | `agg(total_cost=("cost","sum"), avg_cost=("cost","mean")).reset_index()` | `agg(F.sum("cost").alias("total_cost"), F.avg("cost").alias("avg_cost"))` |
| HAVING | `HAVING total_cost > 10000` | 聚合后再 `df_g[df_g["total_cost"]>10000]` | 聚合后 `.filter(F.col("total_cost")>10000)` |
| 升序 | `ORDER BY cost ASC` | `sort_values("cost", ascending=True)` | `orderBy(F.col("cost").asc())` |
| 降序 | `ORDER BY cost DESC` | `sort_values("cost", ascending=False)` | `orderBy(F.col("cost").desc())` |
| 多列排序 | `ORDER BY channel, cost DESC` | `sort_values(["channel","cost"], ascending=[True,False])` | `orderBy(F.col("channel").asc(), F.col("cost").desc())` |
| 前 N 条 | `LIMIT 3` | `df.head(3)` | `df.limit(3)` |
| 最大的 N 个 | `ORDER BY cost DESC LIMIT 3` | `df.nlargest(3, "cost")` | `orderBy(F.col("cost").desc()).limit(3)` |
| 去重 | `SELECT DISTINCT channel` | `df["channel"].unique()` | `df.select("channel").distinct()` |
| 去重计数 | `COUNT(DISTINCT channel)` | `df["channel"].nunique()` | `df.select("channel").distinct().count()` |

### 12.5 多表连接

| 操作 | SQL | Pandas | Spark |
|---|---|---|---|
| 内连接 | `a JOIN b ON a.id=b.id` | `pd.merge(a,b,on="id",how="inner")` | `a.join(b,"id","inner")` |
| 左连接 | `a LEFT JOIN b ON a.id=b.id` | `pd.merge(a,b,on="id",how="left")` | `a.join(b,"id","left")` |
| 列名不同 | `ON a.id=b.user_id` | `pd.merge(a,b,left_on="id",right_on="user_id")` | `a.join(b, a["id"]==b["user_id"], "inner")` |

### 12.6 窗口函数

| 需求 | SQL | Pandas | Spark |
|---|---|---|---|
| 组内排名 | `RANK() OVER (PARTITION BY date ORDER BY cost DESC)` | `df.groupby("date")["cost"].rank(ascending=False)` | `F.rank().over(Window.partitionBy("date").orderBy(F.col("cost").desc()))` |
| 取上一行 | `LAG(cost,1) OVER (PARTITION BY channel ORDER BY date)` | `df.groupby("channel")["cost"].shift(1)` | `F.lag("cost",1).over(Window.partitionBy("channel").orderBy("date"))` |
| 累计求和 | `SUM(cost) OVER (PARTITION BY channel ORDER BY date)` | `df.groupby("channel")["cost"].cumsum()` | `F.sum("cost").over(Window.partitionBy("channel").orderBy("date"))` |
| 滑动平均 | `AVG(cpa) OVER (PARTITION BY channel ORDER BY date ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)` | `df.groupby("channel")["cpa"].rolling(2).mean().reset_index(level=0, drop=True)` | `F.avg("cpa").over(Window.partitionBy("channel").orderBy("date").rowsBetween(-1,0))` |
| 每日 Top N | `WITH ranked AS (...) WHERE rk <= 2` | 先 `rank` 再筛选 | 先 `withColumn("rk", ...)` 再 `filter` |

**注意 Spark 累计求和的坑**：只写 `partitionBy + orderBy` 时，默认窗口框架在部分版本下等价于 `RANGE ... CURRENT ROW`，遇到并列日期会把并列行一起算入。显式写 `rowsBetween(unboundedPreceding, currentRow)` 最稳。Pandas 的 `cumsum()` 是严格按行累加的，不受此影响。

### 12.7 空值与写出

| 操作 | SQL | Pandas | Spark |
|---|---|---|---|
| 填充空值 | `COALESCE(cost, 0)` | `df["cost"].fillna(0)` | `df.na.fill({"cost": 0})` |
| 删除空值 | `DELETE ... WHERE cost IS NULL` | `df.dropna(subset=["cost"])` | `df.na.drop(subset=["cost"])` |
| 保存 | `INSERT INTO ...` | `df.to_csv("r.csv", index=False)` | `df.write.csv("r", header=True, mode="overwrite")` |

### 12.8 一句话总结

| | 优势场景 |
|---|---|
| SQL | 大数据量、多表关联、复杂窗口、持久化 |
| Pandas | 单机小数据、复杂清洗、探索分析、可视化与建模 |
| Spark | 单机放不下、需要分布式 ETL、需要批流一体 |

---

## 十三、面试速查

- 为什么需要分布式 → 单机内存和算力有上限，数据大到装不下时必须拆到多台机器并行。
- Driver 和 Executor 的分工 → Driver 拆任务、派任务、收结果，不处理数据；Executor 执行 Task、缓存数据。
- Partition 和 Task 的关系 → 一个是数据块，一个是计算单元，数量一一对应。
- Stage 怎么划分 → 遇到宽依赖（shuffle）切一刀，Stage 数 = shuffle 数 + 1。
- 窄依赖和宽依赖的区别 → 窄依赖父分区到子分区一对一，不搬数据；宽依赖要跨机器按 key 重分组。
- RDD 和 DataFrame 的区别 → RDD 无 schema、黑盒、无法优化；DataFrame 有列名和类型，能被 Catalyst 优化。
- Catalyst 做了什么 → 谓词下推、列裁剪、常量折叠、join 重排序、广播判断。
- 什么是懒执行 → Transformation 只记计划，Action 才触发计算。
- 为什么要懒执行 → 优化器要看到完整计划才能做合并、下推、裁剪。
- shuffle 是什么 → 按 key 把数据跨机器重新分组的过程。
- shuffle 为什么慢 → 写盘 + 网络传输 + 读盘，比内存计算慢几个数量级。
- shuffle 的三个阶段 → Map 端按 key 分类写盘 → 跨机器搬运 → Reduce 端聚合。
- 为什么同一个 key 必须进同一个分区 → 聚合才能一次算完；join 两边才能碰上面，这是正确性要求。
- 哪些操作会触发 shuffle → groupBy、join、distinct、orderBy、repartition。
- `reduceByKey` 和 `groupByKey` 的区别 → 前者 map 端先局部聚合，传输量小；后者拉全量 value，容易 OOM。
- Spark 是纯内存计算吗 → 不是，内存为主，shuffle 和 spill 时落盘。
- 数据倾斜是什么 → 某个 key 数据量过大，一个 Task 干完所有活，其余 Task 空等，整体耗时取决于最慢的 Task。
- 怎么发现数据倾斜 → Spark UI 某 Stage 卡 99%、Task 耗时极不均匀、OOM 或 Executor lost。
- 怎么定位热点 key → `groupBy(key).count().orderBy(F.desc("count")).show()`。
- 数据倾斜的三大成因 → 业务大 key、空值抱团、join 字段类型不一致。
- 空值为什么会倾斜 → null 被当成一个普通 key，所有 null 行必须进同一个分区。
- 空值怎么处理 → 优先过滤；不能丢就回填有区分度的值；必须保留就加随机后缀打散。
- 加盐的原理 → 给大 key 加随机后缀拆成 N 个 key，打散到 N 个分区，最后去盐合并。
- 加盐的代价 → 多一次 shuffle，只在大 key 明显时用。
- 加盐的 N 取多少 → 经验值 10~50，按热点 key 与普通 key 的比值定。
- 广播 join 的适用条件 → 小表 100MB 以内，默认阈值 10MB 可调。
- 广播 join 为什么快 → 小表复制到每个 executor，大表不动，彻底省掉 shuffle，属于 map-side join。
- 广播 join 的风险 → 小表太大撑爆 driver / executor 内存，超阈值退回 Sort-Merge Join。
- 调大分区数能解决倾斜吗 → 只能缓解多个中等 key，单个超级大 key 无效。
- 大表 join 大表怎么处理 → 热点 key 单独拆出，大表加盐打散，小表对应行膨胀 N 份，join 后 union。
- 怎么验证加盐 join 写对了 → 结果总行数必须等于原始行数。
- AQE 是什么 → Spark 3.x 的自适应查询执行，运行时按统计信息优化 join 与倾斜，但不能完全依赖。
- 广播变量和广播 join 的关系 → 同一套分发机制，前者广播任意只读对象，后者广播表以消除 join 的 shuffle。
- Spark 比 MapReduce 快在哪 → 中间结果不落 HDFS、DAG 多阶段流水线、线程级任务复用、map 端预聚合。
- Spark Streaming 和 Flink 的区别 → 微批 vs 原生流，Flink 延迟到毫秒级。
- State 是什么 → 流处理的记忆，按 key 各存一份，用于跨数据累积。
- State 丢了怎么办 → 从 Checkpoint 快照恢复。
- Event Time 和 Processing Time 的区别 → 数据真实发生时间（需处理乱序）vs 到达计算节点的时间（简单但可能不准）。
- 数据乱序怎么办 → 用 Watermark 容忍一定范围的迟到。
- 迟到太久怎么办 → Watermark → Allowed Lateness → Side Output 三层兜底。
- 窗口有哪几种 → 滚动（不重叠）、滑动（有重叠）、会话（按活动间隔切）。
- 背压是什么 → 下游处理不过来时上游降速，避免堆积。源笔记未展开细节。
- 精确一次怎么保证 → Checkpoint 快照 + Sink 端事务或幂等写。源笔记未展开细节。
- 需要会写 Flink 吗 → 数据工程岗懂概念即可，重心在 Spark 与数据链路。
