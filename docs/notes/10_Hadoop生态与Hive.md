# Hadoop 生态与 Hive

本篇覆盖：Hadoop 的由来与三件套分工（HDFS / YARN / MapReduce）、HDFS 的切块与副本机制、NameNode 与 DataNode 的职责边界。
计算与资源部分包括 MapReduce 的 Map-Shuffle-Reduce 三段及其衰落原因、YARN 的四个角色、Spark on YARN 的形态。
存储与 SQL 部分包括 Hive 的 Metastore 架构与「SQL 翻译官」定位、Hive 表在 HDFS 上的真实目录、分区裁剪与谓词下推、分区与分桶、动态分区、小文件与压缩。
第八节是存储层全景，回答「数据究竟落在几块物理介质上」：元数据的定义与三层粒度、为什么表定义必须放关系库、Hive 表为什么不是元数据表、四层数仓各自的物理落点、HDFS 与 S3 / OSS 的差异，以及 Iceberg / Delta 这类表格式与 Hive 的层次关系。
最后串起一条 Hive SQL 从提交到出结果的完整链路。
这一篇是补课性质：HDFS 基础来自既有笔记，其余按大数据通用技术事实补全，只讲公认机制，不虚构具体项目里不存在的组件。

## 一、Hadoop 是什么，为什么出现

### 1.1 要解决的问题

2003 年前后，Google 面对的是同一类问题：**单机存不下、单机算不完**。

- 网页、日志的量级到了 TB～PB，一台机器的磁盘装不下。
- 倒排索引、日志统计的计算量，单机要跑几天。
- 商用服务器故障是常态：几千台机器里每天都有硬盘坏、机器宕，软件必须假设**故障是默认状态**。

Google 给出的答案不是买一台更贵的大型机，而是**用大量廉价机器堆出一个逻辑上的超级机器，并用软件层兜住硬件故障**。

### 1.2 三篇论文与 Hadoop 的对应关系

| 年份 | Google 论文 | 开源实现 | 解决什么 |
|---|---|---|---|
| 2003 | GFS（Google File System） | **HDFS** | 海量数据怎么分布式存储 |
| 2004 | MapReduce | **MapReduce** | 海量数据怎么分布式计算 |
| 2006 | BigTable | **HBase** | 海量数据怎么做随机读写（列族数据库） |

Hadoop 由 Doug Cutting 和 Mike Cafarella 在 Nutch 项目中孵化，2006 年成为 Apache 项目，2008 年成为顶级项目。Yahoo 早期投入了大量工程资源，把它跑到了数千节点的规模。

需要区分清楚：**Hadoop 不等于 HBase**。HBase 是受 BigTable 启发独立发展的列族数据库，属于 Hadoop 生态，但不是 Hadoop 的必需组件。一个典型的离线数仓（HDFS + YARN + Hive + Spark）完全不需要 HBase。

### 1.3 Hadoop 的三代演进

| 版本 | 计算层 | 资源管理 | 关键变化 |
|---|---|---|---|
| Hadoop 1.x | MapReduce（JobTracker / TaskTracker） | 与计算层耦合 | JobTracker 既管资源又管任务，单点瓶颈，集群上限约 4000 节点 |
| Hadoop 2.x | MapReduce（MRv2） | **YARN** 独立出来 | 资源管理与计算框架解耦，Spark / Tez / Flink 都能跑在同一集群 |
| Hadoop 3.x | 同上 | YARN | 支持多 NameNode、纠删码（Erasure Coding）替代部分副本、默认端口调整 |

**2.x 引入 YARN 是分水岭**：在此之前 Hadoop 只能跑 MapReduce；在此之后它变成了"分布式操作系统"，上面可以跑任何计算框架。

## 二、三件套分工

### 2.1 一张表说清

| 组件 | 角色 | 一句话职责 | 不负责什么 |
|---|---|---|---|
| **HDFS** | 存储层 | 把大文件切块、分散到多台机器、每块存多副本，对外表现为一个巨大的文件系统 | 不管怎么算，也不管文件内部是什么格式 |
| **YARN** | 资源管理层 | 把集群的 CPU / 内存切成一碗一碗的 Container，按需分配给各个应用 | 不管数据存在哪，也不管任务内部逻辑 |
| **MapReduce** | 计算层 | 提供 map / reduce 编程模型，把计算搬到数据所在的机器上执行 | 不管资源从哪来（向 YARN 申请），也不管数据怎么存（从 HDFS 读） |

### 2.2 相互关系

```
            ┌──────────────────────────────┐
            │  计算框架（任选其一或多个）     │
            │  MapReduce / Spark / Tez / Flink │
            └──────────────┬───────────────┘
                           │ 申请资源
            ┌──────────────▼───────────────┐
            │            YARN              │
            │  ResourceManager             │
            │  NodeManager × N             │
            └──────────────┬───────────────┘
                           │ 在 Container 里启动任务
            ┌──────────────▼───────────────┐
            │            HDFS              │
            │  NameNode + DataNode × N     │
            │  数据以 Block 形式存放         │
            └──────────────────────────────┘
```

三层是**解耦**的：HDFS 可以单独用（当存储），YARN 可以跑非 MapReduce 的框架，MapReduce 也可以跑在别的资源管理器上（历史上有 Mesos 版本）。理解这种解耦，才能理解为什么"学 Hadoop"实际学的是三套彼此独立、又能拼装的机制。

## 三、HDFS

### 3.1 核心思想

HDFS（Hadoop Distributed File System）是一个**横跨多台机器的虚拟大硬盘**：存文件时自动切块、分散、备份，对外看起来就是一个普通目录树。

它放弃的东西和它提供的东西同样重要：

| 放弃 | 换来 |
|---|---|
| 低延迟随机读写 | 高吞吐的顺序读写 |
| 频繁修改 | 一次写入、多次读取的简单一致性模型 |
| 海量小文件 | 少量超大文件的高效管理 |
| POSIX 完整语义 | 跨千台机器的水平扩展 |

### 3.2 切块：默认 128MB

HDFS 把文件切成固定大小的 **Block**（Hadoop 1.x 默认 64MB，2.x 起默认 **128MB**），每块独立存到不同机器。

为什么是 128MB：

1. **让寻址开销占比足够小**。机械盘一次寻址约 10ms，顺序传输约 100MB/s。传 100MB 需要约 1 秒，寻址只占 1%；如果块只有 1MB，传输 10ms、寻址 10ms，一半时间在找磁道。
2. **NameNode 内存可控**。每个块在 NameNode 里约占 150 字节元数据。1PB 数据按 128MB 切约 800 万个块，元数据约 1.2GB；若按 8MB 切则是 1.28 亿个块，元数据近 20GB，NameNode 直接撑爆。
3. **并行度合适**。一个块通常对应一个 map task。块太大，文件切出的 task 太少，集群并行不起来，且单个 task 失败要重传整个块；块太小，task 数爆炸，调度和启动开销超过计算本身。

一句话：**块大小是"寻址开销、元数据内存、并行度"三者的折中，128MB 是机械盘时代的经验最优点。** 在 SSD / 万兆网环境下这个值可以调小（如 64MB），在超大文件归档场景可以调大（256MB）。

### 3.3 副本：默认 3 份与机架感知

每个块默认存 **3 个副本**，分布策略（机架感知，Rack Awareness）大致是：

```
副本 1：客户端所在节点（客户端在集群外则随机选一个节点）
副本 2：另一个机架上的某个节点
副本 3：与副本 2 同机架、不同节点
```

这样设计的原因：

- 副本 2 跨机架，**整机架断电或交换机故障**不会丢数据。
- 副本 3 与副本 2 同机架，写入时只需一次跨机架传输，**减少跨机架带宽消耗**。
- 读取时优先读同机架副本，降低网络延迟。

副本数可配（`dfs.replication`，可设 2 或 5），但**分块、主从架构、多副本这三个核心机制不可关闭**——它们是 HDFS 成立的前提。

### 3.4 NameNode 与 DataNode

| | NameNode | DataNode |
|---|---|---|
| 管什么 | **元数据**：目录树、文件到块的映射、块到 DataNode 的映射 | **真实数据块**，以及块的校验和 |
| 数据量 | 小（GB 级），全量放内存 | 大（TB～PB 级），放本地磁盘 |
| 故障后果 | 集群不可用（元数据是单点，靠 HA 解决） | 单节点故障只影响副本，集群自动补副本 |
| 数量 | 1 个 Active（HA 下加 1 个 Standby） | 几十到几千个 |

#### NameNode 的元数据怎么存

两块核心文件：

| 文件 | 内容 | 特点 |
|---|---|---|
| `fsimage` | 某一时刻整个命名空间的**快照**：目录树、文件属性、块列表 | 体积大，不能每次写都重刷 |
| `edits log` | 自上次快照以来的**所有写操作日志**（创建、删除、重命名、追加） | 追加写，体积小，重启时按顺序重放 |

启动流程：加载 `fsimage` → 重放 `edits log` → 在内存中重建完整的命名空间。

运行久了 `edits log` 会越来越大，重启会越来越慢，所以需要 **checkpoint**：把 `fsimage` 和 `edits` 合并成新的 `fsimage`，然后清空旧的 `edits`。做这件事的角色：

| 架构 | 谁做 checkpoint |
|---|---|
| 非 HA（Hadoop 1.x） | SecondaryNameNode（注意：它不是备份，是"合并工"） |
| HA（Hadoop 2.x+） | Standby NameNode，`edits` 通过 JournalNode 集群同步 |

HA 架构下还有 ZKFC（ZK Failover Controller）负责健康检查与主备切换，避免脑裂。

**块的物理位置不写在 `fsimage` 里**。DataNode 启动时和之后定期（默认 6 小时）发送 **Block Report**，NameNode 据此在内存中构建"块 → 节点"的映射。DataNode 每 3 秒发一次心跳，超过约 10.5 分钟没心跳就被判定死亡，其上副本会被重新复制。

#### 读写的实际路径

```
读：客户端 → NameNode 询问"这个块的副本在哪" → 拿到按网络距离排序的 DataNode 列表
     → 直接连最近的 DataNode 读块（数据不走 NameNode）
写：客户端 → NameNode 申请创建文件 → 拿到一组 DataNode
     → 建立 pipeline：Client → DN1 → DN2 → DN3
     → 按 packet 流式传输，逐级复制，ACK 逆序返回
```

关键认知：**NameNode 只做"指路"，不搬运数据**。所有真实流量都在客户端和 DataNode 之间，这是 HDFS 能横向扩展的根本原因。

### 3.5 一次写、多次读

HDFS 的模型是 **Write-Once-Read-Many**：

- 文件创建后只支持**追加**（append），不支持在任意位置覆盖写。
- 不支持随机修改某一行、某一个字节。
- 读取可以并发、可以从任意位置开始读（按块定位）。

适合：日志、埋点、落定的批量数据、离线数仓的不可变事实表。
不适合：需要频繁更新的维表、数据库式的点查与事务、大量小文件、低延迟在线服务。

**"不适合频繁修改"不是工程缺陷，而是设计取舍**：放弃原地修改，才能让副本一致性、故障恢复、吞吐都变得简单。需要更新的场景，标准做法是**写新分区 + 覆盖**（幂等重写），而不是改旧文件——这也正好和数仓的分区覆盖写对上了。

### 3.6 能配与不能配

| 类别 | 内容 |
|---|---|
| 核心机制（改不了） | 必须分块、必须 NameNode / DataNode 主从架构、必须多副本 |
| 可配参数 | 块大小（64 / 128 / 256MB）、副本数（2 / 3 / 5）、心跳间隔、副本放置策略 |
| 完全不管 | 文件内部格式（CSV / JSON / Parquet / ORC），由写入方自己决定 |

最后一条是理解"HDFS 与 Parquet 关系"的关键：**HDFS 是文件系统，Parquet 是文件格式，两者是不同层面，可以自由组合。** 一份 Parquet 文件写进 HDFS 后，照样被切成 128MB 的块、存 3 份、散落在多台机器上。Parquet 不绑定 HDFS（也能放本地盘、S3、OSS），HDFS 也不绑定 Parquet（也能存 CSV）。

## 四、MapReduce

### 4.1 编程模型

MapReduce 把计算抽象成两个函数：

```
map(k1, v1)      → list(k2, v2)      分而治之：每条记录独立处理
reduce(k2, list(v2)) → list(k3, v3)   汇总：相同 key 的值聚到一起算
```

一个 WordCount 的经典形态：

```
输入："a b a c"
Map：   (a,1) (b,1) (a,1) (c,1)
Shuffle：a → [1,1]   b → [1]   c → [1]
Reduce：a → 2        b → 1     c → 1
```

程序员只写 map 和 reduce，**切分、分发、排序、容错、重试全部由框架负责**。这是 MapReduce 最大的价值：把分布式编程的门槛降到了写两个函数。

### 4.2 三段：Map → Shuffle → Reduce

```
┌────────── Map 阶段 ──────────┐   ┌──── Shuffle 阶段 ────┐   ┌──── Reduce 阶段 ────┐
Input Split                    │   │                      │   │                     │
  → RecordReader               │   │  partition（按 key    │   │  merge sort         │
  → map(k,v)                   │   │   哈希取模决定去哪个   │   │  → reduce(k, vals)  │
  → context.write(k, v)        │   │   reducer）           │   │  → 写出 HDFS        │
  → 写入环形缓冲区              │   │  → spill 到磁盘（排序）│   │                     │
  → spill（溢写，排序 + 分区）  │   │  → merge 多个 spill   │   │                     │
  → merge                       │   │  → reducer HTTP 拉取  │   │                     │
                                │   │  → 归并排序           │   │                     │
└───────────────────────────────┘   └──────────────────────┘   └─────────────────────┘
```

逐步说明：

1. **Input Split**：HDFS 的一个块通常对应一个 split（`split size = max(minSize, min(maxSize, blockSize))`）。split 是逻辑切分，不复制数据。
2. **Map**：每条记录调用一次 `map()`，输出先写进内存的**环形缓冲区**（默认 100MB）。
3. **Spill**：缓冲区到 80% 阈值时溢写到磁盘。溢写前按 **partition（key hash % reducer 数）** 分桶、按 key 排序；可选 **Combiner**（在 map 端先做一次局部 reduce，如预聚合 `sum`），能大幅减少网络传输。
4. **Merge**：多个 spill 文件归并成一个按 key 有序的大文件。
5. **Shuffle（狭义）**：Reducer 通过 HTTP 主动**拉取**（fetch）属于自己的分区数据。注意是 pull 不是 push，这样 reducer 挂了不会影响 mapper。
6. **Reduce**：归并排序后，相同 key 的值进入同一个 `reduce()` 调用，结果写回 HDFS（每个 reducer 输出一个 part 文件）。

### 4.3 为什么 Shuffle 最慢

Shuffle 是 Map 和 Reduce 之间的**全量数据重分布**，它同时踩中了分布式系统里最贵的几件事：

| 开销来源 | 具体表现 |
|---|---|
| 磁盘 I/O | 每个 map 的输出都要 spill 到本地盘，再读出、归并；数据量等于 map 输出总量 |
| 网络传输 | 所有 map 的输出要按 key 重新路由到对应 reducer，是 all-to-all 的跨节点流量 |
| 排序 | map 端 spill 前排序、merge 时排序、reduce 端再归并排序，比较成本与 `n log n` 成正比 |
| 序列化 | 跨进程、跨网络必须序列化 / 反序列化，对象头开销大 |
| 同步屏障 | reducer 必须等所有 mapper 完成才能开始，**最慢的那个 map 决定整体时长**（长尾） |
| 内存压力 | 缓冲区满就溢写，溢写次数多则 I/O 放大 |

一句面试话术：**"Shuffle 慢，是因为它把'分区的数据'从'按块分布'重排成'按 key 分布'，这个过程必然要落盘、走网络、做排序，而且是一个全局同步点。"**

### 4.4 为什么现在少用

| 原因 | 说明 |
|---|---|
| 中间结果强制落盘 | map 输出必须写本地盘，reduce 输出再写 HDFS；多阶段作业每段都有磁盘屏障 |
| 延迟高 | 一个作业动辄分钟级，无法支撑交互式分析 |
| 多轮作业串联 | 复杂 SQL 被拆成多个 MR job，每个 job 都要重新读盘、重新 shuffle |
| 表达能力弱 | 只有 map / reduce 两个原语，join、窗口、迭代都要手工拼，代码冗长 |
| 迭代计算差 | 机器学习需要反复迭代同一份数据，MR 每轮都从头读盘 |
| 被更优引擎替代 | Spark 用 DAG + 内存缓存减少落盘；Tez 把多个 MR 合成一个有向无环图 |

它仍然在用的场景：超大规模、内存放不下的简单批处理；历史遗留作业；对延迟完全不敏感的日志清洗。**"少用"不等于"没用"，它的思想（分而治之 + 移动计算而非移动数据）被后来所有引擎继承。**

## 五、YARN

### 5.1 为什么需要 YARN

Hadoop 1.x 里 JobTracker 同时干两件事：管理集群资源 + 调度 MapReduce 任务。后果是：

- 集群规模上不去（约 4000 节点封顶），JobTracker 成为单点瓶颈。
- 只能跑 MapReduce，Spark、Tez 想用同一批机器只能另建集群。

YARN 把"资源管理"抽出来做成通用层，计算框架变成"跑在 YARN 上的应用"。**一个集群，多种框架共享。**

### 5.2 四个角色

| 角色 | 层级 | 职责 |
|---|---|---|
| **ResourceManager（RM）** | 全局，1 个（HA 下 2 个） | 整个集群的资源总账。内部含 Scheduler（按 Capacity / Fair 策略分配 Container）和 ApplicationsManager（接收应用提交、启动 AM、AM 挂了负责重启） |
| **NodeManager（NM）** | 每台机器 1 个 | 单节点管家。上报本机 CPU / 内存，启动和监控 Container，超内存就杀进程 |
| **ApplicationMaster（AM）** | 每个应用 1 个 | 应用的"项目经理"。向 RM 申请资源、向 NM 要 Container、决定任务怎么分布、监控任务、失败重试、最后向 RM 注销 |
| **Container** | 资源单位 | CPU + 内存的逻辑封装（如 2 vCore + 4GB），任务进程跑在里面 |

关系图：

```
Client 提交应用
   ↓
ResourceManager（ApplicationsManager）分配第一个 Container
   ↓
在该 Container 里启动 ApplicationMaster
   ↓
AM 向 RM 的 Scheduler 申请更多 Container
   ↓
RM 返回"某台 NodeManager 上有空闲资源"
   ↓
AM 指示对应 NodeManager 启动 Container，在里面跑 Task
   ↓
Task 定期向 AM 汇报进度；AM 向 RM 汇报应用状态
```

关键点：**RM 只负责"分资源"，不负责"管任务"**。任务级调度（谁先跑、失败了怎么办、数据本地性）是 AM 的职责。这种两级调度让 RM 的负担很轻，集群才能扩展到上万节点。

### 5.3 Spark on YARN 是什么形态

Spark 本身没有资源管理能力，跑在 YARN 上时：

| 模式 | Driver 在哪 | AM 的角色 | 适用 |
|---|---|---|---|
| **YARN Client** | 提交任务的机器（本地） | 只负责申请 Executor Container | 交互式调试、`spark-shell`，能看到本地日志 |
| **YARN Cluster** | 集群内，AM 的 Container 里 | AM 里同时跑 Driver，负责申请资源 + 调度 | 生产环境，提交后本地可以断开 |

组件映射：

```
Spark Driver        → 跑在 AM 容器里（cluster 模式）
Spark Executor      → 跑在 YARN Container 里，一个 Container 一组 Executor
Spark Task          → Executor 内的线程（不是进程，不走 YARN 调度）
```

面试要点：**YARN 调度的是 Container（进程级），Spark 内部调度的是 Task（线程级）。** 所以"Executor 数量"由 YARN 给多少 Container 决定，"并行度"由 Executor 的 core 数和 partition 数决定，这是两个层级的事。

## 六、Hive

### 6.1 核心定位：SQL 翻译官，不是数据库

Hive 由 Facebook 在 2008 年前后开源，动机很朴素：**分析师会写 SQL，不会写 Java MapReduce。**

Hive 做的事只有一件：

```
HiveQL（类 SQL） → 解析 → 优化 → 翻译成分布式作业（MR / Tez / Spark） → 交给 YARN 跑
```

它**不是数据库**，因为：

| 数据库（MySQL） | Hive |
|---|---|
| 自己管存储、有自己的存储引擎 | 不存数据，数据在 HDFS / S3 |
| 自己算，执行引擎内嵌 | 不算数据，把 SQL 翻译出去交给 MR / Tez / Spark |
| 写时校验 schema（Schema-on-Write） | 读时校验 schema（Schema-on-Read），写入时不检查，查询时才报错 |
| 毫秒级点查、行级增删改 | 分钟级批量扫描，传统上不支持行级更新 |
| 强事务、强约束 | 弱事务（ACID 需 ORC + 分桶，限制多），无主键约束 |

### 6.2 三个关键点

#### ① 元数据存 MySQL（Metastore），数据存 HDFS

```
        ┌─────────────────────────────┐
        │  Metastore（MySQL / Postgres）│
        │  库、表、字段、分区、SerDe、   │
        │  表在 HDFS 上的路径            │
        └──────────────┬──────────────┘
                       │ 提供元数据服务（Thrift）
        ┌──────────────▼──────────────┐
        │  HiveServer2 / Spark / Presto │
        └──────────────┬──────────────┘
                       │ 读真实文件
        ┌──────────────▼──────────────┐
        │  HDFS：/user/hive/warehouse   │
        └─────────────────────────────┘
```

Metastore 是 Hive 生态的**共享目录服务**：Spark SQL、Presto / Trino、Flink 都能连同一个 Metastore，从而"看到同一批表"。这也是为什么数据湖方案里，Metastore（或 AWS Glue Catalog、Unity Catalog）是核心资产。

默认的 Derby 是嵌入式单连接数据库，只能一个人用；生产必须换成 MySQL / PostgreSQL，否则 HiveServer2 多会话会冲突。

> 为什么元数据必须放在关系库、而不能顺手放在 HDFS 上？见 **§8.2**。

#### ② Hive 本身不存数据、不算数据

- 不存：`CREATE TABLE` 只是在 Metastore 里写一条元数据 + 在 HDFS 上建一个目录，**没有数据**。
- 不算：`SELECT` 会被翻译成 MR / Tez / Spark 作业，真正干活的是那些引擎和 YARN 上的 Container。
- 所以 **Hive 挂掉不影响数据本身**，只要 Metastore 和 HDFS 还在，换个引擎照样能查。

这也解释了一个常见误解："Hive 表的数据在 MySQL 里吗？"——不在。MySQL 里只有"表长什么样、数据在哪个目录"这类描述，一行业务数据都没有。

> 那「Hive 表」本身到底是什么？它和元数据表是什么关系？见 **§8.3**。

#### ③ 执行引擎可换

| 引擎 | 特点 | 适用 |
|---|---|---|
| **MapReduce** | 原始引擎，每个阶段落盘，最慢 | 兼容性最好、超大作业兜底 |
| **Tez** | 把多个 MR 合成一个 DAG，减少中间落盘 | Hive 交互查询的经典搭配 |
| **Spark** | 内存计算 + DAG 优化，比 MR 快数倍 | 通用，和 Spark 生态共用 |
| **LLAP**（Hive 2.x+） | 常驻守护进程 + 内存缓存 + 细粒度执行 | 交互式 BI，亚秒到秒级 |

`SET hive.execution.engine=tez;` 一行就能切换。**Hive 是"方言 + 元数据 + 优化器"，引擎是可插拔的部件**——这是它和 MySQL 最本质的架构差异。

### 6.3 Hive vs MySQL 对比

| 维度 | Hive | MySQL |
|---|---|---|
| 定位 | 离线数仓的 SQL 层 | OLTP 关系型数据库 |
| 数据存储 | HDFS / 对象存储 | 本地磁盘 / 云盘，InnoDB 管理 |
| 元数据 | 独立的 Metastore（MySQL / PG） | 数据字典在库内 |
| 计算方式 | 翻译成分布式作业 | 单机执行引擎 |
| 延迟 | 秒级到分钟级（LLAP 可到亚秒） | 毫秒级 |
| 数据量 | PB 级 | GB 级（单机上限） |
| 更新删除 | 传统上不支持行级，靠分区重写 | 完整支持，事务保证 |
| 索引 | 无 B+ 树，靠分区裁剪 + 列存统计信息 | B+ 树、聚簇索引 |
| 事务 | 弱（ACID 表有限制） | 强 ACID |
| 约束 | 无主键 / 外键约束 | 主键、外键、唯一约束 |
| 典型场景 | 日报、ETL、批量聚合 | 订单、用户、配置类业务数据 |

一句话：**MySQL 是"记账本"，Hive 是"仓库的检索目录 + 翻译器"。** 两者不是替代关系，数仓里常常是 Hive 算完，结果同步到 MySQL 供接口查。

## 七、Hive 表在 HDFS 上的真实形态

### 7.1 目录结构

```
/user/hive/warehouse/                     ← 默认仓库根目录（hive.metastore.warehouse.dir）
└── ad_analytics.db/                      ← 库名.db，库就是一个目录
    ├── dwd_ad_cost_detail/               ← 表名，表就是一个目录
    │   ├── dt=2026-10-01/                ← 分区键=值，分区就是一个子目录
    │   │   ├── part-00000-xxxx.snappy.parquet
    │   │   └── part-00001-xxxx.snappy.parquet
    │   ├── dt=2026-10-02/
    │   │   └── part-00000-xxxx.snappy.parquet
    │   └── dt=__HIVE_DEFAULT_PARTITION__/  ← 分区值为 NULL 时的默认目录
    └── dim_campaign/
        └── part-00000-xxxx.parquet        ← 非分区表，表目录下直接放文件
```

对应关系：

| Hive 概念 | HDFS 实体 |
|---|---|
| 数据库 | 一个目录 `库名.db` |
| 表 | 一个目录 `表名` |
| 分区 | 表目录下的一个子目录 `分区键=值` |
| 数据文件 | 分区目录下的 `part-*` 文件 |
| 表的 schema | 只存在于 Metastore，**目录里没有 schema 文件** |

最后一行很重要：HDFS 上只有一堆二进制文件和目录名，**"这是一张什么表、有哪些字段、什么类型"全部记在 Metastore 里**。把 Metastore 弄丢了，HDFS 上的数据还在，但没人知道怎么读它——这就是为什么 Metastore 必须用 MySQL 并定期备份。

内部表与外部表的区别也体现在目录归属上：

| | 内部表（Managed Table） | 外部表（External Table） |
|---|---|---|
| 默认位置 | `warehouse/库.db/表/` | 由 `LOCATION` 指定，可在任意路径 |
| `DROP TABLE` 的行为 | **连数据目录一起删除** | 只删 Metastore 元数据，**数据保留** |
| 适用 | 中间表、由 Hive 全权管理的表 | 原始数据、多引擎共享的数据、不希望被误删的数据 |

数仓 ODS 层通常建外部表指向已有数据目录，避免一次误操作把原始数据删掉；DWD / DWS 的中间表用内部表，生命周期由 Hive 管理。

分区元数据需要与 HDFS 目录保持一致。直接用 `put` 上传了一个新分区目录时，Metastore 并不知道，要执行：

```sql
MSCK REPAIR TABLE dwd_ad_cost_detail;   -- 扫描 HDFS，补齐缺失的分区元数据
SHOW PARTITIONS dwd_ad_cost_detail;     -- 查看已登记的分区
```

### 7.2 分区 = 目录级切分

`PARTITIONED BY (dt STRING)` 的物理含义非常直白：**每个不同的分区值，对应一个子目录。**

```sql
-- 建表
CREATE TABLE dwd_ad_cost_detail (
    channel      STRING,
    campaign_id  STRING,
    cost         DOUBLE,
    clicks       BIGINT
)
PARTITIONED BY (dt STRING)
STORED AS PARQUET;

-- 写入一个分区（静态分区）
INSERT OVERWRITE TABLE dwd_ad_cost_detail PARTITION (dt='2026-10-01')
SELECT channel, campaign_id, cost, clicks FROM ods_ad_cost_log WHERE dt='2026-10-01';
```

执行后 HDFS 上就多出 `/user/hive/warehouse/ad_analytics.db/dwd_ad_cost_detail/dt=2026-10-01/`。

一个容易忽略的点：**分区值不是存在文件里的普通列**，它编码在目录名里。查询时 Hive 从目录名解析出 `dt` 的值，再"虚拟"成最后一列返回。这也是为什么分区列不能和表内字段重名。

### 7.3 分区裁剪（Partition Pruning）

```sql
SELECT SUM(cost) FROM dwd_ad_cost_detail WHERE dt = '2026-10-01';
```

优化器看到 `dt = '2026-10-01'`，直接把扫描范围限定到 `dt=2026-10-01/` 这一个目录，**其他所有分区目录连打开都不打开**。这就是分区裁剪。

```sql
SELECT SUM(cost) FROM dwd_ad_cost_detail;                    -- 扫全部目录
SELECT SUM(cost) FROM dwd_ad_cost_detail WHERE dt >= '2026-10-01';  -- 只扫 >= 该值的目录
```

| 写法 | 是否裁剪 | 原因 |
|---|---|---|
| `WHERE dt = '2026-10-01'` | 是 | 常量等值，直接定位目录 |
| `WHERE dt >= '2026-10-01'` | 是 | 范围裁剪，按目录名比较 |
| `WHERE substr(dt,1,7) = '2026-10'` | **否** | 对分区列套函数，优化器无法从目录名推导 |
| `WHERE dt = (SELECT max(dt) FROM t)` | 部分 | 需要先跑子查询，可能退化为全表扫 |

**分区列上不要套函数**，这是最常被问的优化点。同理，`WHERE year(dt)=2026` 也会失效，要改写成 `dt BETWEEN '2026-01-01' AND '2026-12-31'`。

### 7.4 Parquet 内部：row group 与谓词下推

分区裁剪解决的是"跳过哪些目录"，**谓词下推（Predicate Pushdown）解决的是"跳过目录里的哪些数据"**。

Parquet 文件结构：

```
┌─────────────────────────────────────┐
│ magic "PAR1"                        │
├─────────────────────────────────────┤
│ Row Group 0  （默认 128MB 一批行）    │
│   ├─ Column Chunk: channel          │
│   │    ├─ Page 0（含 min/max 统计）  │
│   │    └─ Page 1 ...                │
│   ├─ Column Chunk: cost             │
│   └─ Column Chunk: clicks           │
├─────────────────────────────────────┤
│ Row Group 1 ...                     │
├─────────────────────────────────────┤
│ Footer：schema + 每个 row group /    │
│         每个列的 min / max / null 数 │
│         + offset 索引                │
├─────────────────────────────────────┤
│ magic "PAR1"                        │
└─────────────────────────────────────┘
```

因为文件末尾的 Footer 记录了**每个 row group 每一列的 min / max**，所以查询 `WHERE cost > 1000` 时可以：

1. **文件级**：先读 Footer，如果整个文件的 `cost` 最大值都 ≤ 1000，整个文件跳过。
2. **Row Group 级**：如果某个 row group 的 `cost` 最大值 ≤ 1000，这个 row group 跳过。
3. **Page 级**（Parquet 1.8+ 的 Column Index / Offset Index）：再细一层跳过 page。

同时还有 **列裁剪（Column Pruning）**：`SELECT channel, SUM(cost)` 只读这两列的 Column Chunk，`clicks`、`campaign_id` 的 chunk 一个字节都不读。这是列存相对行存最大的优势。

**谓词下推的效果高度依赖数据有序性。** 如果 `cost` 在文件里是随机分布的，每个 row group 的 min/max 都会覆盖全量范围（min≈0，max≈极大值），统计信息就形同虚设。所以：

- 按高频过滤字段**排序写**（`DISTRIBUTE BY ... SORT BY ...`），能显著提升下推命中率。
- 按日期分区 + 分区内按时间排序，是最常见的组合。
- 这也是 Iceberg / Hudi / Delta Lake 做 **Z-Order / Clustering** 的根本动机。

### 7.5 澄清："分区是逻辑切分，物理上数据仍散落在多台机器"

这是初学 Hive 时最容易卡住的点，拆成三层看：

**第一层：分区只是命名空间上的目录。**
`dt=2026-10-01` 是一个目录名，不产生任何"把数据放到某一台机器"的效果。HDFS 的目录是纯元数据，目录本身不占数据块。

**第二层：分区目录里的文件，仍然是普通 HDFS 文件。**
一个 `part-00000.parquet` 有 1GB，照样被切成 8 个 128MB 的 Block，每个 Block 存 3 个副本，散落在不同机架的 8～24 个 DataNode 上。**一个分区完全可能横跨整个集群。**

**第三层：分区裁剪省的是"不读其他目录"，不是"只在一台机器上读"。**
`WHERE dt='2026-10-01'` 让 Hive 不去碰其他目录的文件，但命中分区的那 1GB 数据，仍然要被并行的多个 task 从多台 DataNode 上读。所以：

| 说法 | 对错 |
|---|---|
| 分区把数据按值分到不同机器 | ❌ 分区是目录，和机器无关 |
| 分区能减少扫描的数据量 | ✅ 通过跳过无关目录 |
| 一个分区的数据只在一台机器上 | ❌ 仍然切块 + 多副本 + 分散 |
| 分区能减少 NameNode 压力 | ❌ 反而增加目录和小文件数量 |

一句话总结：**分区是"逻辑上的目录切分 + 查询时的目录跳过"，不是"物理上的机器切分"。** 真正决定数据落在哪台机器的是 HDFS 的 Block 放置策略。

### 7.6 分区 vs 分桶

| | 分区（Partition） | 分桶（Bucket） |
|---|---|---|
| 物理形态 | 目录 `dt=2026-10-01/` | 目录内的固定数量文件 `part-00000` ~ `part-00031` |
| 切分依据 | 列的**值** | 列的 **hash % 桶数** |
| 粒度 | 粗（一个值一个目录） | 细（一个桶文件含多个值） |
| 适用列 | 低基数：日期、国家、渠道 | 高基数：user_id、device_id、order_id |
| 主要收益 | 分区裁剪，跳过无关目录 | SMB Join、抽样、数据均匀分布 |
| 数量控制 | 分区数随数据增长，易失控 | 建表时固定，不会自动增长 |
| 语法 | `PARTITIONED BY (dt STRING)` | `CLUSTERED BY (user_id) INTO 32 BUCKETS` |
| 风险 | 高基数列做分区 → 目录爆炸、小文件灾难 | 数据倾斜导致桶大小不均；桶数改不了 |

分桶的两个典型用途：

```sql
-- 1. 抽样：只读 1/32 的数据，快速探查
SELECT * FROM dwd_ad_cost_detail TABLESAMPLE(BUCKET 1 OUT OF 32 ON user_id);

-- 2. SMB Join（Sort-Merge-Bucket）：两表按同一列、同样桶数分桶且有序
--    可以直接做 map 端 join，省掉整个 shuffle
CREATE TABLE fact_a (user_id STRING, ...) CLUSTERED BY (user_id) SORTED BY (user_id) INTO 32 BUCKETS;
CREATE TABLE fact_b (user_id STRING, ...) CLUSTERED BY (user_id) SORTED BY (user_id) INTO 32 BUCKETS;
```

为什么分桶能加速 join：两边都按 `hash(user_id) % 32` 切成 32 份，那么 A 的第 3 桶只可能和 B 的第 3 桶匹配，**桶与桶一一对应，不需要全局 shuffle**，每个 map 只处理一对桶。

**分区和分桶可以叠加**：先按 `dt` 分区（裁剪），再按 `user_id` 分桶（join / 抽样），是数仓里很常见的组合。

### 7.7 静态分区与动态分区

| | 静态分区 | 动态分区 |
|---|---|---|
| 写法 | `PARTITION (dt='2026-10-01')` | `PARTITION (dt)`，值来自 SELECT 最后一列 |
| 一次写入 | 一个分区 | 多个分区 |
| 是否需要预先知道值 | 是 | 否 |
| 适用 | 单日补数、定时任务已知日期 | 一次导入多天数据、按多维自动分裂 |
| 风险 | 分区多时要写很多条语句 | 分区数失控、小文件爆炸 |

```sql
-- 静态：明确写死分区值
INSERT OVERWRITE TABLE t PARTITION (dt='2026-10-01')
SELECT col1, col2 FROM src WHERE dt='2026-10-01';

-- 动态：分区列放在 SELECT 的最后
INSERT OVERWRITE TABLE t PARTITION (dt)
SELECT col1, col2, dt FROM src;   -- 最后一列 dt 决定去哪个分区

-- 混合：静态 + 动态（静态列必须写在前面）
INSERT OVERWRITE TABLE t PARTITION (country='US', dt)
SELECT col1, col2, dt FROM src WHERE country='US';
```

动态分区必须开的参数：

| 参数 | 默认 | 作用 |
|---|---|---|
| `hive.exec.dynamic.partition` | false | 开启动态分区 |
| `hive.exec.dynamic.partition.mode` | strict | `strict` 要求至少一个静态分区；`nonstrict` 允许全动态 |
| `hive.exec.max.dynamic.partitions` | 1000 | 全局最大分区数 |
| `hive.exec.max.dynamic.partitions.pernode` | 100 | 单节点最大分区数 |
| `hive.exec.max.created.files` | 100000 | 全局最大创建文件数 |

动态分区最典型的坑：**每个分区至少产生一个文件，分区一多就是小文件灾难**。常见缓解手段是加 `DISTRIBUTE BY dt`，让同一个分区落到同一个 reducer，减少文件数。

### 7.8 小文件问题

**什么是小文件**：显著小于 HDFS 块大小（128MB）的文件，几 KB 到几 MB。

**为什么是问题**：

| 层面 | 后果 |
|---|---|
| NameNode 内存 | 每个文件 + 每个块约占 150 字节元数据，1 亿个小文件就是十几 GB 堆内存，直接压垮 NameNode |
| 计算 | 一个文件至少对应一个 task，task 启动（JVM 启动、申请 Container）开销远大于读几 KB 数据 |
| 存储 | 每个块无论多小都占一个块位，副本按块复制，空间利用率低 |

**怎么产生**：动态分区、reducer 数过多、上游按小时/分钟频繁落盘、流式小批写入、`INSERT` 多次追加。

**怎么治理**：

| 手段 | 说明 |
|---|---|
| 写入前合并 | `DISTRIBUTE BY` 控制 reducer 数，或 Spark 侧 `coalesce` / `repartition` |
| Hive 自动合并 | `hive.merge.mapfiles=true`、`hive.merge.mapredfiles=true`、`hive.merge.size.per.task=256MB`、`hive.merge.smallfiles.avgsize=128MB` |
| 控制 reduce 数 | `hive.exec.reducers.bytes.per.reducer`（默认 256MB）调大 |
| 压缩归档 | HAR 文件、ORC 的 ACID compaction |
| 定期重写 | 用 Spark 读整个分区，`repartition(N)` 后覆盖写 |

### 7.9 为什么要压缩

| 收益 | 机制 |
|---|---|
| 省存储 | 压缩比通常 3～10 倍 |
| 省磁盘 I/O | 读的数据量按压缩比下降，这是查询提速的主要来源 |
| 省网络 | Shuffle 传输的是压缩后的数据 |
| 省 CPU（间接） | I/O 等待减少，CPU 利用率反而上升 |

代价是**编解码消耗 CPU**。在分布式系统里通常划算，因为瓶颈几乎总是 I/O 和网络，不是 CPU。

| 算法 | 压缩比 | 速度 | 是否可切分 | 适用 |
|---|---|---|---|---|
| **snappy** | 中 | 很快 | 容器格式内可切 | 热数据、Shuffle 中间结果，Hive/Spark 默认首选 |
| **gzip** | 高 | 慢 | **不可切分** | 冷数据归档；单个大 gzip 文件只能一个 task 读 |
| **zstd** | 高 | 较快 | 新版本支持 | 兼顾压缩比与速度，逐渐替代 gzip |
| **lzo** | 中 | 快 | 可切分（需建索引） | 历史方案，现已少用 |
| **bzip2** | 很高 | 很慢 | 可切分 | 极少用，CPU 代价过高 |

一个重要细节：**Parquet / ORC 文件本身是可切分的**，因为切分点在 row group / stripe 边界，与内部用的压缩算法无关。所以"gzip 不可切分"主要影响的是 **gzip 压缩的 CSV / 文本文件**，不是 Parquet。

配置示例：

```sql
-- 中间结果压缩（map 输出 → reduce），追求速度用 snappy
SET hive.exec.compress.intermediate=true;
SET hive.intermediate.compression.codec=org.apache.hadoop.io.compress.SnappyCodec;

-- 最终输出压缩
SET hive.exec.compress.output=true;
SET mapreduce.output.fileoutputformat.compress.codec=org.apache.hadoop.io.compress.SnappyCodec;

-- Parquet 表级压缩
CREATE TABLE t (...) STORED AS PARQUET
TBLPROPERTIES ("parquet.compression"="SNAPPY");
```

## 八、存储层全景：数据到底存在哪

前面七节讲了「有什么组件」，这一节回答一个更基础的问题：**一份数据从产生到被查，究竟落在几块物理介质上。**
理解了这一节，Hive、Metastore、对象存储、OLAP 这几样东西的关系就不会再混。

### 8.1 「元数据」是什么

**元数据（metadata）= 描述数据的数据。** 它不含数据内容本身，只描述数据叫什么、由什么组成、存在哪、谁负责。

图书馆的类比最贴切：书架上的书是数据，卡片目录是元数据——卡片上写着书名、作者、编号、在哪个书架的哪一层。**读者找书查的是目录，真正读的是书。** 没有目录，几百万册书就只是纸堆。

数仓里的元数据分三类：

| 类别 | 内容 | 例子 |
|---|---|---|
| 技术元数据 | 表名、字段名、类型、分区键、文件位置、行数、大小 | `dwd_ad_fact.installs` 是 `bigint`，位于 `/warehouse/dwd.db/` |
| 业务元数据 | 字段业务含义、指标口径、负责人 | `roi = 收入 / 花费`，归属投放组 |
| 管理元数据 | 权限、血缘、更新频率、上次产出时间 | 仅分析师可读；由 `dwd_ad_fact` 加工而来；每日 2:00 更新 |

核心认知：

> **「表」是虚拟的。表 = 文件 + 元数据，缺一半都不成立。**

HDFS 上只有一堆叫 `part-00000-xxxx.parquet` 的二进制文件。它们不知道自己属于哪张表、是哪一天的分区、字段叫什么。是 Metastore 里那条登记把这些文件「认领」了，才拼成一张能 `SELECT` 的表。

反过来也成立：**删掉 MySQL 里那条登记，HDFS 上的文件一个字节都没少，但那张表「消失」了**——所有引擎都找不到它，只能在 HDFS 上看到一个孤儿目录。

Parquet 文件自带 schema，但那只够回答「这个文件里有哪些列」。它回答不了「这 3000 个文件里哪几个是昨天的」「哪些属于同一张表」「谁有权限读」。这些必须由独立的元数据系统管。

**但「元数据」这个词在三个不同粒度上被使用，这是最容易把人绕晕的地方：**

| | 回答什么问题 | 存在哪 | 例子 |
|---|---|---|---|
| 第一层 · 文件自述 | 这个文件里有哪些列？ | parquet 文件内部（文件头尾） | `installs` 是 `bigint` |
| 第二层 · 表定义 | 表叫什么、有哪些字段、数据在哪个**目录** | MySQL 里的一行（Metastore） | `dwd_ad_fact` → 12 字段 → `/warehouse/dwd.db/dwd_ad_fact` |
| 第三层 · 版本清单 | 这张表**当前这一版**由哪几个**文件**组成 | 数据目录旁边的小文件（表格式管的） | 第 2 版 = 这 3 个 parquet |

**Hive 只有前两层，Iceberg / Delta 补上的正是第三层**，详见 §8.6。

差别在于粒度：第二层只记到「目录」。它说「数据在这个文件夹里」，但文件夹里今天新加了什么、哪几个文件是刚写坏的、昨天的版本由哪些文件组成——它一概不知。第三层补的就是这个缺口。

### 8.2 为什么元数据必须放关系库，不能放 HDFS

直觉上会想：既然什么都存 HDFS，元数据顺手存那儿不就行了？不行，因为 **HDFS 恰好在两个最关键的属性上不行：不能改、不够快。**

| 维度 | HDFS 的设计目标 | 元数据的实际需求 |
|---|---|---|
| 单次读写量 | 几百 MB 到几 GB | 几 KB |
| 访问模式 | 顺序扫完整个文件 | 随机定位到某一张表 |
| 修改 | 一次写入，改内容要整份重写 | 一天改几百次（加字段、加分区、改注释） |
| 并发 | 无跨客户端事务 | 二十个调度任务同时加分区 |
| 延迟 | 单次操作固定开销几十毫秒 | 一天被查几十万次，要毫秒返回 |

具体三个原因：

**① HDFS 不能原地修改。** HDFS 是一次写入多次读取（WORM）模型，文件写完只能整份删掉重写，没有「改其中一行」这回事。

而元数据天天在变：建表加一条记录、`ALTER TABLE ADD COLUMNS` 改字段列表、每跑一批 `ADD PARTITION` 加一个分区。**如果元数据存在 HDFS 上，每加一个分区都要重写整个元数据文件**——几十万张表、几百万个分区时彻底不可行。MySQL 里这只是改一行、提交一个事务，几十微秒。

**② 并发写需要锁和事务。** 上午 9 点可能有二十个任务同时跑，其中三个要往同一张表加分区。放 HDFS 上就是「读出来 → 改 → 写回」，后写的覆盖先写的，**先加的分区无声消失**（典型丢失更新）。MySQL 靠行锁 + 事务，后来者排队，谁都丢不了。

Hive 早期用内嵌 Derby 做 Metastore，单机单用户还能跑，多人一用就锁死或损坏——这是 2010 年前后的真实痛点，所以生产必须换独立 MySQL / PostgreSQL。

**③ HDFS 自己也是这么干的（同一条规则的自证）。** NameNode 管理整个集群的目录树和块位置，它把这些信息放在**自己的内存 + 本地磁盘**（`fsimage` + `editlog`），而不是塞进 DataNode。**元数据必须比数据本身更快、可改，所以必须换一种存储**——Hive 只是把同一条规则又往上推了一层。

**额外收益：解耦。**

Metastore 独立成服务（Thrift 协议）后：

- **多引擎共享一份定义**：Spark、Trino/Presto、Flink 连同一个 Metastore，看到同一批表。若元数据绑在 HDFS 里，就只有 Hadoop 自己能读。
- **元数据与存储彻底解耦**：现在很多公司的 Hive 表存在 S3 / OSS 上，元数据仍在机房的 MySQL 里。表在哪、元数据在哪，是两件不相干的事。

**补充：Iceberg 把元数据放回对象存储了，为什么没推翻这个结论？**

Iceberg 确实把 metadata.json、manifest 放在对象存储上，但它用的是**不可变文件 + 追加式提交**：每次提交写一个新快照文件，不修改旧文件，然后原子切换指针。这把「不能改」绕成了「只追加」。

但「当前最新快照是哪一个」这个指针，仍然存在数据库里（JDBC Catalog / Hive Metastore / REST Catalog）。因为指针要高频读、原子切换、支持并发写——这些需求一点没变。

> 所以准确的规则不是「元数据必须放数据库」，而是：**访问模式决定存储选择。大块元数据可以挪走，需要高频读写的状态永远留在事务型数据库里。**

Iceberg / Delta 这套「表格式」到底与 Hive 是什么关系、它把哪些元数据放到了哪里，见 **§8.6**。

### 8.3 澄清：Hive 表不是「元数据表」

一个常见混淆：以为 `CREATE TABLE` 建出来的就是一张元数据表。

不是。**Hive 表由两个物理上独立的部分组成：**

| 组成 | 在哪 | 内容 | 体积 |
|---|---|---|---|
| 表定义（元数据） | MySQL 的 Metastore | 表名、字段名与类型、分区键、指向的目录 | 几 KB |
| 表数据 | HDFS / 对象存储 | `part-*.parquet` 里的真实数值 | 几 GB 到几 TB |

**房产证的类比**：房子是实体的，房产证写着「几号楼几零几、面积、产权人」。房产局系统里能查到这套房的全部登记信息，但这和你「住进去」是两件事。**Hive 表 = 房子 + 房产证**，不能说「房子就是房产证」。

**一次 `SELECT` 的实际链路，最直接地证明这一点：**

```
① 客户端 → 元数据服务：dwd_ad_fact 有哪些字段？文件在哪？
② Metastore 查 MySQL → 返回 12 个字段 + /warehouse/dwd.db/dwd_ad_fact
③ 客户端拿着路径直奔 HDFS 读文件 —— MySQL 全程不再参与
④ 几 GB 数据从 DataNode 流出来，一个字节都不经过 MySQL
```

**三个可以自己验证的证据：**

| 证据 | 现象 | 说明什么 |
|---|---|---|
| Metastore 挂了 | 已启动的任务照跑完 | 它已拿到路径，不再需要问「表在哪」 |
| 删掉 HDFS 目录 | `DESCRIBE` 正常，`SELECT` 报 `FileNotFoundException` | 表定义与数据可以一半在、一半不在 |
| 读数据过程 | `SELECT` 期间 MySQL 零流量 | 数据压根不在 MySQL 里 |

**「元数据表」这个词的两种含义，别混：**

- **MySQL 里的系统表**：`TBLS`（每行一张 Hive 表）、`COLUMNS_V2`（每行一个字段）、`PARTITIONS`（每行一个分区）。这些确实叫元数据表，是字面意义的表。但查 `TBLS` 得到的是「仓库里有哪几张表」的清单，**不是任何表里的业务数据**。
- **你自己建的 `dwd_ad_fact`**：名字叫表，实体是 HDFS 上的一堆文件；它在 `TBLS` 里只占**一行记录**。所以它「在 Metastore 里」并不是「作为一张表存在」，而是「被登记了一次」。

### 8.4 数仓四层的物理落点

四层（ODS / DWD / DWS / ADS）**不是四个数据库、四台机器**，而是同一块存储上的四个目录。层与层之间没有换介质，换的是四件事：**路径、格式、粒度、寿命**。

| 层 | 物理路径 | 主要格式 | 保留期 | 谁写 / 谁读 |
|---|---|---|---|---|
| ODS | `warehouse/ods.db/表/dt=2026-10-19/` | Text / JSON / Avro（也可直接 Parquet） | 7–30 天 | 采集任务写；几乎没人读，出问题才回溯 |
| DWD | `warehouse/dwd.db/表/dt=.../` | Parquet / ORC + snappy 或 zstd | 1–3 年 | 清洗任务写；分析师和下游任务读 |
| DWS | `warehouse/dws.db/表/dt=.../` | Parquet | 约 1 年 | 汇总任务写；ADS 和报表读 |
| ADS | `warehouse/ads.db/表/` | Parquet + 导出到 MySQL / Doris / ES | 跟报表走 | 结果任务写；BI 与接口读 |

**成本结构**：ODS 只留一个月，ADS 量最小，真正的成本在 DWD——全量明细存三年，通常占集群 70% 以上空间。所以压缩算法、分区粒度的讨论，绝大多数时候是在讨论 DWD。

**只有 ADS 需要「搬出去」。** 原因不是图省事，是物理结构决定的：

- HDFS / Parquet：按列存、适合全表扫，但定位一行要翻遍文件。
- MySQL：B+ 树索引，定位一行毫秒级，但全表扫极慢。
- Doris / ClickHouse：列存 + 稀疏索引 + 预聚合，报表几十毫秒，但写入和更新代价高。

**没有一种引擎两头都能打，所以同一份数据按用途复制成多份，每份用最适合它的形态。** 这就是 ADS 导出的根本原因。

导出的三种刷新方式：全量覆盖（`TRUNCATE` + 插）、增量追加、**只重刷最近 N 天**（最常用，因为历史一般不变）。

三项代价：

1. **一致性延迟**：从 Hive 侧跑完到 BI 上可见，中间总有时间差。/ 非实时。
2. **双份存储成本**。
3. **schema 双份维护**：ADS 加一列，HDFS 侧和 MySQL 侧都得加，否则导出任务报错。这是数据团队常见的事故来源。

**对照本项目**（`ad-analytics`）：

```
data/warehouse/
├── ods/ods_ad_log.parquet
├── dwd/dwd_ad_fact.parquet, dim_campaign.parquet
├── dws/dws_ad_channel_daily.parquet, dws_ad_date_daily.parquet
└── ads/ads_anomaly.parquet
```

结构与生产完全一致，只是退化成单文件：没有分区目录、没有 Metastore、没有服务层。`iaa-analytics-agent/data/dim.db` 那个 SQLite 扮演了维表与小规模元数据的角色。

面试口径：**「我的四层是 `data/warehouse/` 下四个目录，生产上对应 HDFS 的四个 database，ADS 再导一份到 MySQL 供 BI。」**

### 8.5 HDFS 与 S3 / OSS：同一类，设计目标相反

S3、OSS 是**对象存储**。在数仓里它们和 HDFS 扮演同一角色——**底层存储**，上层代码只认「一个路径」，换存储不用改逻辑。但两者的设计目标几乎相反。

先解释「对象」：**一个文件 + 一组随它的标签，被打包成不可分割的整体。** 命名空间只有 **桶（bucket）+ 键（key）** 这两层，是扁平的。你看到的 `dwd/dt=2026-10-19/part-0.parquet` 里，`/` 只是 key 字符串中的一个普通字符，**不是目录**。控制台把它渲染成目录树，但内部不存在目录。

HDFS 是分布式文件系统，有真实目录树，由 NameNode 集中管理「每个文件的每一块在哪台机器」。

| 维度 | HDFS | S3 / OSS |
|---|---|---|
| 目录 | 真实目录树 | 扁平 key，`/` 只是字符 |
| 修改文件 | 支持追加（append），不支持随机改写 | 不能追加也不能改，只能整份重写 |
| 重命名 | 元数据改一下，瞬间完成 | **没有 rename**，等于「复制 + 删除」 |
| 元数据管理 | NameNode 集中管理，有单点（靠 HA 兜） | 分布式索引，无单点 |
| 一致性 | 强一致 | S3 在 2020 年后改为强一致，OSS 也是；早期最终一致曾是大坑 |
| 计费 | 机器与磁盘，包月 | 存储 + **请求次数**都收费 |
| 数据本地性 | 有，计算会被调度到有数据的机器 | 没有，数据必须过网络 |
| 权限模型 | POSIX 风格（user/group/other） | IAM 策略 + 桶策略，无「文件所有者」 |

**三个真会咬人的差异：**

**① 「没有 rename」直接决定了 Iceberg 的设计。** Spark 写 HDFS 时先写 `_temporary/`，写完用 rename 原子切换成正式文件——在 HDFS 上安全又便宜。S3 上 rename 本质是「复制整个文件 + 删除原文件」，**又慢又不原子**，复制一半失败就留垃圾。所以对象存储上的表格式必须放弃这个技巧，改成「写新快照 + 切指针」，指针放数据库。**Iceberg 那套 metadata.json 设计，很大程度是被 S3 逼出来的。**

**② 「按请求计费」让小文件问题从「慢」变成「又慢又贵」。** HDFS 上几千个小文件只是慢；S3 上每 LIST 一次、每 GET 一个文件都产生费用。一个分区塞一万个 1MB 小文件，光 LIST 账单就很可观。所以对象存储上的数仓会不遗余力地合并小文件，不只是为了性能。

**③ 「没有数据本地性」是 Spark on S3 比 on HDFS 慢的主因。** HDFS 上 YARN 会把 task 调度到存着那块数据的机器上，只有代码动、数据不动。S3 上这条路径不存在，所有数据都要穿过网络拉到计算节点。云上数仓的瓶颈常是**网络带宽和出口费用**，不是 CPU。

**还有一个常被忽略的：没有 append。** HDFS 支持 `appendToFile`（Hive 场景少用，但流式写入需要）。S3 完全不支持追加，一个对象只能整份重写。所以 Kafka → S3 必须先攒批，攒够再写完整对象。

> 一句话：**HDFS 是机房里的硬盘阵列，S3 是互联网上的云端水箱。** 都能存水、都不丢，但一个是「走过去接」，一个是「报编号让水罐送到门口」，价格结构和速度特性完全不同。

这也是云上数仓把**存储放对象存储、计算放 Kubernetes、元数据放数据库**三件事彻底拆开的原因：三者的扩展节奏和成本模型完全不同，绑在一起会互相拖累。

对象存储没有 rename 这一点，直接催生了 Iceberg / Delta 这类**表格式**——下一节讲它们与 Hive 到底是什么关系。

### 8.6 表格式：Iceberg / Delta 与 Hive 是什么关系

最常见的误解是「Iceberg / Delta 跟 Hive 作用一样，都是搞表的」。实际它们不在同一层，**不存在替代关系**。

之所以会形成「一样」的印象，是因为在 Spark 里写 `INSERT INTO db.table` 时，两者用起来完全一致。差别只在底层，出问题时才显形。

#### ① Hive 其实是三个东西叠在一起

这是混淆的根源：

1. **SQL 引擎**：把 HiveQL 翻译成 MR / Tez / Spark 作业
2. **表格式**：所谓「Hive 表」，即用目录承载数据 + Metastore 里登记一行
3. **元数据服务**：Hive Metastore，通过 Thrift 对外服务

**Iceberg / Delta 只占第二层**——它们是纯粹的**表格式（table format）**。不提供 SQL 引擎（必须配 Spark / Trino / Flink），也不提供元数据服务（复用 Metastore / Glue / REST Catalog）。

#### ② 表格式是什么

**表格式 = 一套规范，规定「描述当前这一版数据的清单文件该怎么放、写成什么样、怎么更新」。**

准确说它不是数据也不是程序，是**规则**。类比：

- **表格式** ≈ 记账准则（借贷记账法，规定账本怎么记）
- **metadata.json / manifest** ≈ 按准则记出来的**账本**

不用这套准则也行，那就是 Hive 的做法：目录里只放数据文件，不记账。

#### ③ 目录结构对比：多出来的就是表格式

```
【Hive 表】                          【Iceberg 表】
dwd_ad_fact/                        dwd_ad_fact/
├── dt=10-19/                       ├── data/
│   └── part-00000.parquet          │   ├── dt=10-19/part-00000.parquet
└── dt=10-20/                       │   └── dt=10-20/part-00001.parquet
    └── part-00001.parquet          └── metadata/
                                        ├── v1.metadata.json
                                        ├── v2.metadata.json
                                        ├── snap-8f3a.avro
                                        └── version-hint.text
```

- 左边：**目录里有什么，什么就是数据**。没有版本，没有历史。
- 右边：`metadata/` 才是表格式的物理形态。每次写入新增一个版本文件，旧的保留；`version-hint.text` 只写一个数字，指向当前该读哪一版——**切换版本就是改这一个数字**。

#### ④ 能力差异：从「不知道版本」来的

Hive 表对数据的理解只有一句：「这个目录下所有文件都是这张表的数据」。它没有「当前版本」这个概念，由此产生四个限制：

| 能力 | Hive 表 | Iceberg / Delta |
|---|---|---|
| 知道当前版本由哪几个文件组成 | ✗ 整个目录都算 | ✓ 由 snapshot 列清单 |
| 原子提交 | ✗ 写一半失败留半份脏数据 | ✓ 写完文件切指针 |
| 时间旅行 | ✗ | ✓ 可查任意历史快照 |
| 改 schema | 基本等于重写全表 | ✓ 只在元数据层演进 |
| 分区粒度变更 | 必须重写历史 | ✓ 隐藏分区，加映射即可 |
| 增量读 | ✗ 只能按分区 | ✓ 只读上次之后新增的文件 |

第四条和第五条很实际：原来按天分区，业务要改成按小时，Hive 表只能重写全部历史；Iceberg 在元数据里加一层映射，历史文件一个字节都不动。

补充：Hive 3 提供 ACID 表（ORC + 分桶 + 表属性），能部分支持更新与事务，但限制多、不是默认行为，生产上很少拿它当 Iceberg 用。

#### ⑤ 为什么两类元数据要分开放

| 东西 | 放哪 | 为什么 |
|---|---|---|
| 数据文件 | HDFS / S3 | 容量大，要顺序扫 |
| **表定义** | **MySQL 的一行** | 一天改几百次、二十个任务并发写 → 需要事务 |
| **版本清单** | **HDFS / S3 上，与数据同目录** | 每次写入新增一个文件 → 必须与数据一起原子提交 |

表面看与 §8.2「元数据不能放 HDFS」矛盾，实际不矛盾，因为这是**两种需求相反的元数据**：

- **表定义**：数量极少（一表一行），但改得勤、抢着改 → 必须放数据库
- **版本清单**：数量很多（每次写入一个），但写完就不动，且必须与数据文件同处一个存储，才能用「写新文件 + 切指针」一次提交成功

若版本清单放 MySQL，就变成「先写 HDFS 数据、再写 MySQL 清单」跨两个系统，中间失败两边对不上。**放在一起，才能一起提交。**

#### ⑥ Metastore 里到底有什么

就是一个普通的关系库（MySQL / PostgreSQL），库名通常叫 `metastore` 或 `hive`，里面是几张普通的表：

| 表名 | 存什么 |
|---|---|
| `DBS` | 每个库一行 |
| `TBLS` | 每张 Hive 表一行 |
| `COLUMNS_V2` | 每个字段一行 |
| `PARTITIONS` | 每个分区一行 |
| `SDS` | 存储位置（那个 `LOCATION` 路径） |

想看「表名 → HDFS 路径」的映射，直接查即可，没有黑魔法：

```sql
SELECT t.TBL_NAME, s.LOCATION
FROM TBLS t JOIN SDS s ON t.SD_ID = s.SD_ID
WHERE t.TBL_NAME = 'dwd_ad_fact';
-- dwd_ad_fact | hdfs://nn:8020/user/hive/warehouse/dwd.db/dwd_ad_fact
```

#### ⑦ 判据：能叠加，就不是替代

最直接的证据：**一张 Iceberg 表可以直接在 Hive 里查**（建带 `HiveIcebergStorageHandler` 的外部表），Hive 表也能被 Spark / Trino 直接读。

既然两边互相能读，说明它们不是同一位置上的竞争关系，而是上下层关系。真正被替代的只有一件事：**「一张表用什么方式记录自己有哪些文件」这一层**。

#### ⑧ 三者的分工

| 格式 | 特点 | 适合 |
|---|---|---|
| **Iceberg** | 规范最开放，Trino / Flink / Spark 支持都好；隐藏分区是杀手锏 | 多引擎混合的团队 |
| **Delta** | Spark 生态最深（Databricks 主导），有新 UniForm 兼容 | Spark 为主 |
| **Hudi** | 侧重流式 upsert | 近实时入湖 |

## 九、一条 Hive SQL 的完整链路

以这条查询为例：

```sql
SELECT channel, SUM(cost) AS total_cost
FROM dwd_ad_cost_detail
WHERE dt BETWEEN '2026-10-01' AND '2026-10-07'
  AND cost > 0
GROUP BY channel;
```

### 9.1 九个阶段

```
① 提交
   Beeline / JDBC / CLI → HiveServer2（或 Hive CLI 直连）
        │
② 解析（Parse）
   Antlr 语法分析 → AST（抽象语法树）
   检查关键字、括号、语法合法性
        │
③ 语义分析（Semantic Analysis）
   访问 Metastore：表是否存在？字段是否存在？类型是否匹配？
   展开 SELECT *、补全隐式类型转换、校验分区列
        │
④ 逻辑计划（Logical Plan）
   生成操作符树：TableScan → Filter → GroupBy → Select
        │
⑤ 优化（Optimizer）
   · 分区裁剪：只保留 dt 在 10-01 ~ 10-07 的目录
   · 列裁剪：只读 channel、cost 两列
   · 谓词下推：cost > 0 推到 TableScan，甚至推到 Parquet row group
   · CBO：基于统计信息决定 join 顺序、是否 map-side join
        │
⑥ 物理计划（Physical Plan）
   按引擎生成可执行单元：
   · MR  → 一个或多个 MapReduce Job
   · Tez → 一张 DAG（顶点 = 阶段，边 = 数据流）
   · Spark → 一个 Spark Job（DAG of Stages）
        │
⑦ 资源申请
   提交到 YARN → ResourceManager 分配 Container
   → 启动 ApplicationMaster → AM 申请更多 Container
        │
⑧ 分布式执行
   每个 task 从 HDFS 读最近的 Block（数据本地性）
   → 列解码 + 解压 → 过滤 → 局部聚合（Combiner）
   → Shuffle（按 channel 分区、排序、跨节点传输）
   → 最终聚合 → 写临时目录 / 返回结果
        │
⑨ 返回
   HiveServer2 拉取结果集 → 返回客户端
   DDL / INSERT 类语句返回执行状态与写入行数
```

### 9.2 各阶段的关键点

| 阶段 | 谁在做 | 出错会怎样 |
|---|---|---|
| 解析 | Hive 编译器 | 语法错误，立刻报 `ParseException`，不消耗集群资源 |
| 语义分析 | Hive + Metastore | 表/字段不存在、类型不匹配，报 `SemanticException` |
| 优化 | Hive 优化器（Calcite / CBO） | 优化不足只影响性能，不影响正确性 |
| 物理计划 | Hive + 执行引擎 | 选错引擎会导致慢数倍（如该用 Tez 却用了 MR） |
| 资源申请 | YARN | 队列资源不足 → 作业一直 `ACCEPTED` 排队 |
| 执行 | YARN Container 里的 task | 数据倾斜 → 少数 task 跑几小时，其余早完成 |
| 返回 | HiveServer2 | 结果集过大 → 内存溢出，应改为 `INSERT` 落表 |

### 9.3 面试时怎么讲这条链路

一句话版本：

> SQL 提交到 HiveServer2 后，先做语法解析和基于 Metastore 的语义分析，生成逻辑计划；优化器做分区裁剪、列裁剪、谓词下推；然后按引擎（MR / Tez / Spark）生成物理计划，提交到 YARN 申请 Container 执行；task 从 HDFS 按数据本地性读 Block，列解码后过滤、聚合、Shuffle，最后结果回传或落表。

追问"哪里最可能慢"时的回答顺序：

1. **没走分区裁剪**（分区列套了函数、或漏了分区条件）→ 全表扫描。
2. **数据倾斜**（某个 channel 占了 80% 数据）→ 单个 reducer 长尾。
3. **小文件过多** → task 启动开销压过计算。
4. **Shuffle 量大** → join / group by 的 key 基数太高。
5. **资源不足** → 队列排队，不是 SQL 本身的问题。

## 面试速查

- **Hadoop 是什么？** → 用廉价机器堆出分布式存储与计算能力的开源框架，核心是 HDFS + YARN + MapReduce。
- **Google 三篇论文对应什么？** → GFS→HDFS，MapReduce→MapReduce，BigTable→HBase。
- **三件套怎么分工？** → HDFS 存、YARN 分配资源、MapReduce 算；三者解耦，可各自替换。
- **HDFS 块为什么默认 128MB？** → 寻址开销占比约 1%、NameNode 元数据可控、并行度合适，三者折中。
- **块太大会怎样？** → task 数太少并行不足，单任务失败要重传整块，长尾更明显。
- **块太小会怎样？** → NameNode 元数据爆炸，task 启动开销超过计算本身。
- **为什么默认 3 副本？** → 容忍整机架故障，同时跨机架流量可控；副本放置遵循机架感知。
- **NameNode 和 DataNode 的区别？** → NameNode 管元数据（fsimage + edits log），DataNode 存真实块。
- **fsimage 和 edits log 各是什么？** → 前者是命名空间快照，后者是增量写日志；启动时重放，checkpoint 时合并。
- **SecondaryNameNode 是备份吗？** → 不是，它只做 fsimage 与 edits 的合并（checkpoint）。
- **HDFS 为什么不适合频繁修改？** → 一次写多次读模型，只支持追加，随机改写会破坏副本一致性与吞吐优势。
- **MapReduce 三个阶段？** → Map 分而治之、Shuffle 按 key 重分布、Reduce 聚合输出。
- **Shuffle 为什么慢？** → 落盘 + 跨网络 + 多次排序 + 全局同步屏障，最慢的 map 决定整体时长。
- **MapReduce 为什么现在少用？** → 中间结果强制落盘、延迟高、多阶段作业反复读写、表达能力弱，被 Spark / Tez 替代。
- **YARN 四个角色？** → RM 管全局资源、NM 管单节点、AM 管单个应用、Container 是资源单位。
- **RM 和 AM 的分工？** → RM 只分资源不管任务；AM 负责申请资源、调度任务、处理失败。
- **Spark on YARN 两种模式？** → client 模式 Driver 在本地，cluster 模式 Driver 在 AM 容器里；生产用 cluster。
- **Hive 是数据库吗？** → 不是，它是把 SQL 翻译成分布式作业的翻译官，不存数据、不算数据。
- **Hive 的元数据在哪？数据在哪？** → 元数据在 Metastore（MySQL / PG），数据在 HDFS。
- **元数据是什么？** → 描述数据的数据：表名、字段、类型、位置、权限、血缘；分技术 / 业务 / 管理三类。
- **为什么元数据不放 HDFS 上？** → HDFS 不能原地改（改一个分区要重写整份元数据文件），也没有跨客户端事务（并发加分区会丢失更新）；NameNode 自己也把元数据放内存 + 本地盘，是同一条规则的自证。
- **Hive 表就是元数据表吗？** → 不是。表 = Metastore 里几 KB 的登记 + HDFS 上几 GB 的文件；`SELECT` 时元数据只在规划阶段被问一次，读数据不经 MySQL。删了 HDFS 目录 `DESCRIBE` 仍正常，说明两者可分离。
- **四层数仓的数据分别存哪？** → 同一块存储的四个库目录 `warehouse/{ods,dwd,dws,ads}.db/`，差别只在格式、粒度、保留期；只有 ADS 会额外导一份到 MySQL / OLAP 供 BI。
- **ADS 为什么要导一份到 OLAP？** → 存储结构与查询模式要匹配：HDFS 擅长全表扫、不擅长定位一行；MySQL 擅长点查、不擅长全扫。没有一种引擎两头都能打，所以按用途复制多份。代价是一致性延迟和双份 schema 维护。
- **HDFS 和 S3 / OSS 的区别？** → 都是底层存储，但 S3 是扁平 key、无真实目录、无 rename（等于复制 + 删除）、无 append、按请求计费、无数据本地性；S3 在 2020 年后已强一致。
- **为什么 Iceberg 在对象存储上不用 rename 提交？** → S3 的 rename 是复制 + 删除，慢且不原子；改用「写新快照 + 切换指针」，指针仍存在数据库里。
- **元数据分几层？** → 三层：文件自述（parquet 内部的列信息）、表定义（Metastore 一行，只记到目录）、版本清单（表格式管，记到具体文件）；Hive 只有前两层。
- **表格式（table format）是什么？** → 一套规范，规定「描述当前版本数据的清单文件怎么放、写什么、怎么更新」。它是规则不是程序，产物才是元数据（metadata.json、manifest）。
- **Iceberg / Delta 和 Hive 作用一样吗？** → 不一样。Hive 横跨三层（SQL 引擎 + 表格式 + 元数据服务），Iceberg / Delta 只占表格式一层，没有引擎也没有元数据服务。两者能叠加：Iceberg 表可用 Hive 查，Hive 表也能被 Spark 读。
- **Hive 表缺什么？** → 缺「当前版本由哪些文件组成」的清单。所以无法原子提交、无法时间旅行、改 schema 要重写、分区粒度变更要重写历史。
- **为什么版本清单放数据旁边，表定义却放数据库？** → 需求相反：表定义数量极少但高频改、并发写（需事务）；版本清单数量多、写完不动，且必须与数据同存储才能原子提交。
- **Hive Metastore 里有什么？** → 一个普通关系库，`DBS` / `TBLS` / `COLUMNS_V2` / `PARTITIONS` / `SDS`，join `TBLS` 与 `SDS` 就能拿到「表名 → HDFS 路径」。
- **Hive 的执行引擎有哪些？** → MapReduce、Tez、Spark，Hive 2.x 起还有 LLAP。
- **Hive 和 MySQL 的核心区别？** → Hive 是 Schema-on-Read 的分布式批处理 SQL 层，MySQL 是 Schema-on-Write 的单机事务库。
- **Hive 表的目录结构？** → `/warehouse/库.db/表/分区键=值/part-*.parquet`，库、表、分区都是目录。
- **分区裁剪是什么？** → 查询带分区条件时只读对应目录，跳过其他分区。
- **分区列套函数为什么失效？** → 优化器无法从目录名推导函数结果，只能退化为全表扫描。
- **谓词下推靠什么实现？** → Parquet Footer 里每个 row group、每个列的 min / max 统计信息。
- **为什么谓词下推有时没效果？** → 数据在文件内无序，min/max 覆盖全量范围，统计信息失去区分度。
- **分区是物理切分吗？** → 不是，分区是目录级逻辑切分；分区内的文件仍切块、多副本、散落在多个 DataNode。
- **分区和分桶的区别？** → 分区按值切目录（粗、裁剪用），分桶按 hash 取模切文件（细、join 和抽样用）。
- **分桶为什么能加速 join？** → 同 key 同桶号一一对应，可做 SMB Join，省掉全局 shuffle。
- **静态分区和动态分区？** → 静态写死分区值一次一个分区；动态从数据列取值一次写多个分区，需开参数且易产生小文件。
- **小文件为什么有害？** → 压 NameNode 内存，且每个文件一个 task，启动开销远大于计算。
- **怎么治理小文件？** → 控制 reducer 数、`DISTRIBUTE BY`、Hive merge 参数、定期重写合并。
- **为什么压缩？** → 省存储、省 I/O、省网络，代价是 CPU；I/O 通常是瓶颈所以划算。
- **snappy 和 gzip 怎么选？** → 热数据、中间结果用 snappy（快）；冷数据归档用 gzip / zstd（压缩比高）。
- **gzip 不可切分影响 Parquet 吗？** → 不影响，Parquet 按 row group 切分，与内部压缩算法无关。
- **一条 Hive SQL 的执行链路？** → 解析 → 语义分析（查 Metastore）→ 逻辑计划 → 优化（裁剪 / 下推）→ 物理计划 → YARN 申请资源 → 分布式执行 → 返回结果。
