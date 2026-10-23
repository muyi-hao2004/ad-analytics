# 最小数仓实验环境

## 这套环境装了什么

只有两个容器，但足够让你撞上真正的墙。

| 服务 | 端口 | 作用 |
|---|---|---|
| **Hive Metastore** | 9083 | 表定义服务。Spark 建表、加分区都要问它 |
| **PostgreSQL** | 5433 | Metastore 的元数据库。`TBLS` / `PARTITIONS` / `COLUMNS_V2` 就在里面 |

**为什么不装 HDFS / YARN / MinIO**：

1. 你机器 16 GB 内存，完整 Hadoop 栈至少要 8 GB，跑起来会很卡
2. 你要撞的那几个墙——**数据倾斜、shuffle、分区裁剪、幂等重跑——在单机多核上一样会发生**，因为它们由数据分布和 SQL 写法决定，跟机器数量无关
3. warehouse 用本地目录 `D:\warehouse`，**分区就是真实的子目录**，比对象存储更直观，你甚至能用资源管理器直接翻

生产环境里 warehouse 会是 `hdfs://nn:8020/user/hive/warehouse/`，这里换成 `file:///D:/warehouse/`，**其余一切不变**：表在 Metastore，数据在 warehouse，只是存储换了。

## 当前状态（2026-10-21 实测）

| 组件 | 状态 |
|---|---|
| Java 17 | 已装，可用 |
| `.venv-spark`（Python 3.13.14 + pyspark **4.0.3**） | 已建好，已通过写入测试 |
| `infra/hadoop/bin/winutils.exe` + `hadoop.dll` | 已下载（Hadoop 3.3.6 Windows 版） |
| Spark 写本地 Parquet | **已验证通过**（collect / write / read 全通） |
| Docker 容器（Metastore + PG） | **已验证通过**：建库、建分区表、写两个分区、查元数据全通 |
| 分区裁剪 | **已实测**：`PartitionFilters` 下推，只打开一个目录 |
| 数据（Criteo 归因） | **已下载并探查**：16,468,027 行 / 31 天 / 675 campaign，`D:/data/criteo/` |
| **ODS 层** | **已构建完成**：`ods.ods_ad_log` 31 个分区 / 31 个 parquet 文件 / 457.9 MB（源 TSV 2470 MB） |
| 数据（Avazu_x1） | 已下载但**弃用**，`D:/data/avazu/`（占用约 5.7 GB，可删）——原因见下面「数据源」一节 |



## 环境组成（为什么有 `.venv-spark`）

项目原来的 venv 是 **Python 3.14**，PySpark 4.x 只支持到 3.13，所以单独建了一个：

```bash
# 重建方式（已经建好了，正常情况下不需要跑）
C:/Users/Mia/.workbuddy/binaries/python/versions/3.13.12/python.exe -m venv .venv-spark
./.venv-spark/Scripts/python.exe -m pip install pyspark==4.0.3
./.venv-spark/Scripts/python.exe -m pip install huggingface_hub   # 下数据用
```

**所有 Spark 相关脚本都用 `.venv-spark/Scripts/python.exe` 跑**，不要用 `python` 或项目 venv。

## 启动

```bash
# 1. 先启动 Docker Desktop，等它显示 Engine running

# 2. 自检（用 .venv-spark 跑，它才有 pyspark）
./.venv-spark/Scripts/python.exe scripts/check_env.py

# 3. 起容器（第一次要 build，会拉 apache/hive 镜像，约 1 GB，慢）
cd infra
docker compose up -d --build
# 如果报 failed to resolve reference "docker.io/..."，说明 Docker Hub 不通，
# 先按「常见坑」表格第一条走镜像源拉取 + 打标签

# 4. 看 metastore 是否就绪
docker compose logs -f metastore
# 首次启动会先在 PostgreSQL 里建 83 张元数据表，约需 20-40 秒
```

## 三个验证

### 验证一：容器活着

```bash
docker compose ps
# 两个服务都应该是 Up (healthy)
```

### 验证二：直接看 Metastore 里的系统表

这是最有价值的一步——**你笔记 §8.6 里讲的那几张表，现在能亲眼看**。

```bash
# 一条命令跑完整套查询（推荐，报告已排版好）
docker exec -i lab-postgres psql -U hive -d metastore < infra/inspect_metastore.sql

# 或者进交互模式自己敲
docker exec -it lab-postgres psql -U hive -d metastore
\dt                    -- 83 张表，能看到 DBS / TBLS / COLUMNS_V2 / PARTITIONS / SDS
```

**注意表名必须带双引号且大写**：`SELECT * FROM "TBLS";`。写 `tbls` 会报
`relation "tbls" does not exist`——Hive 在 PostgreSQL 上建的是带引号的大写标识符，
这是 MySQL 上不会遇到的坑。

跑完 `spark_smoke_test.py` 之后，实测结果长这样：

```
 DB_ID |  NAME   |      DB_LOCATION_URI
-------+---------+----------------------------
     1 | default | file:/D:/warehouse
     2 | lab     | file:/D:/warehouse/lab.db

 TBL_ID | db  |  TBL_NAME  |    TBL_TYPE    | OWNER
--------+-----+------------+----------------+-------
      1 | lab | smoke_part | EXTERNAL_TABLE | Mia
```

**「建一张表」在物理上就是往 `TBLS` 里插了一行。** 数据行数、文件数一个都不在这里。

一个值得注意的细节：`TBL_TYPE` 是 `EXTERNAL_TABLE` 而不是 `MANAGED_TABLE`。
Spark 用数据源 API 建 Hive 表时默认建成外部表，**意味着 `DROP TABLE` 只删登记、不删数据**。
这不是 bug，是 Spark 的有意设计（防止误删）。


### 验证三：Spark 链路

```bash
# 用装了 pyspark 的那个环境（项目里已建好 .venv-spark）
./.venv-spark/Scripts/python.exe scripts/spark_smoke_test.py
```

预期最后会打印出目录树：

```
warehouse/
  lab.db/
    smoke_part/
      dt=2026-10-20/
        part-00000-xxxx.snappy.parquet
      dt=2026-10-21/
        part-00000-yyyy.snappy.parquet
```

**看到 `dt=2026-10-20/` 这个目录名，分区裁剪就不再是概念了。**

### 验证四：分区裁剪是真的不读

```bash
./.venv-spark/Scripts/python.exe scripts/demo_partition_pruning.py
```

对比两次的文件扫描计划，看 `Location` 和 `PartitionFilters`：

| 查询 | Location | PartitionFilters |
|---|---|---|
| `WHERE dt='2026-10-20'` | `InMemoryFileIndex(1 paths)[.../dt=2026-10-20]` | `[isnotnull(dt), (dt = 2026-10-20)]` |
| 全表 | `CatalogFileIndex(1 paths)[.../smoke_part]` | `[]` |

第一行说明过滤条件被下推到了「**选目录**」这一层——**另一个分区目录压根没被打开**。
省的不是 CPU，是 IO。数据量上亿时，这是能不能跑出来的区别。


## 常见坑（下面这些全部是这次真实踩到的）

| 现象 | 原因 | 解法 |
|---|---|---|
| **`failed to resolve reference "docker.io/library/postgres"`**<br>`dial tcp ... connection attempt failed` | Docker Hub 在国内直连不通 | 改用镜像源拉取再打标签：<br>`docker pull docker.m.daocloud.io/library/postgres:16-alpine`<br>`docker tag docker.m.daocloud.io/library/postgres:16-alpine postgres:16-alpine`<br>`docker pull docker.m.daocloud.io/apache/hive:4.0.0`<br>`docker tag docker.m.daocloud.io/apache/hive:4.0.0 apache/hive:4.0.0`<br>然后正常 `docker compose up -d --build` |
| **`MetaException(message:file:/D:/warehouse/lab.db/xxx is not a directory or unable to create one)`** | **Metastore 不只是「记一个字符串」**，建内部表时它会亲自 `mkdir`。它在 Linux 容器里执行，`D:` 盘不存在 | 见下面「warehouse 挂载」一节。这是本次最有价值的坑 |
| 在 Git Bash 里 `docker run -v D:/warehouse:/warehouse` 挂载失败，路径被改成 `.../PortableGit/warehouse` | Git Bash 的 MSYS 会做路径转换 | 命令前加 `export MSYS_NO_PATHCONV=1` |
| compose 报 `mount denied: ... too many colons` | 挂载目标路径里带冒号（如 `/D:/warehouse`）会被当成 `source:target` 分隔符 | 目标路径不能带冒号，见下面「warehouse 挂载」 |
| `check_env` 报 Docker daemon 未运行 | Docker Desktop 没开 | 启动它，等 Engine running |
| **`Python worker exited unexpectedly (crashed)` + `EOFException`**<br>伴随 `OSError: [WinError 10038]` | **SPARK-53759**：Windows 没有 `os.fork()`，只能用 simple-worker 路径，该路径漏了 `flush()`。Python 3.12+ 改了 GC 顺序，socket 先关、缓冲区数据丢失，JVM 读到 EOF。**Windows + Python 3.12+ 上必崩，不是配置问题** | 升到 **pyspark 4.0.3 / 4.1.2 / 3.5.9** 以上（已在 4.0.3 修复）。已帮你装好 4.0.3 |
| **`HADOOP_HOME and hadoop.home.dir are unset`** | Windows 版 Spark 的 `Shell` 类要执行 `winutils.exe`，找不到就抛 | 已下载到 `infra/hadoop/bin/`，脚本里设 `HADOOP_HOME` 指向 `infra/hadoop` |
| 换了 shell 后 worker 又崩 | driver 用 3.13，worker 用了 PATH 里的另一个 python | 必须设 `PYSPARK_PYTHON = sys.executable`，smoke test 里已设 |
| build 卡在 `ADD postgresql-42.7.4.jar` | Maven 中央仓库慢 | 浏览器手动下载后放到 `infra/`，把 Dockerfile 的 `ADD` 改成 `COPY postgresql-42.7.4.jar /opt/hive/lib/postgresql.jar` |
| metastore 起不来，日志有 `ClassNotFoundException: org.postgresql.Driver` | jar 没进 `/opt/hive/lib` | 同上，确认 `docker compose build --no-cache` |
| 9083 连不上 | 容器只绑了 127.0.0.1 或镜像启动失败 | `docker compose logs metastore` 看最后 20 行 |

### warehouse 挂载：为什么路径要对齐

这是这次撞得最值得记的一个坑，**它是真实的架构问题，不是配置失误**。

Metastore 建表时不只是往 `TBLS` 插一行，它还会**亲自在当前文件系统里 mkdir 那个目录**。
于是出现分裂：

| 角色 | 它看到的 `file:/D:/warehouse` 是什么 |
|---|---|
| Windows 上的 Spark | `D:\warehouse`（真实目录） |
| 容器里的 Metastore | `/D:/warehouse`（不存在，建不出来）→ 建表失败 |

生产里这叫「**计算节点和 Metastore 必须对存储有相同视角**」，通常靠所有机器挂同一个 HDFS/S3 解决。
这里是本地文件系统，做法是：

1. 把宿主机 `D:\warehouse` 挂到容器 `/warehouse`
2. 在容器根目录造一个名字就叫 `D:` 的软链接指向 `/`（Linux 文件名允许冒号）
3. 于是 `/D:/warehouse` → 经软链接 → `/warehouse` → 挂载进来的真实目录

这两步都在 `Dockerfile.hive-metastore` 和 `docker-compose.yml` 的注释里写清了。

**为什么不能直接挂到 `/D:/warehouse`**：compose 的挂载目标路径不允许出现冒号，
会被解析成 `source:target` 的分隔符，报 `too many colons`。


**注意**：Windows 上跑 Spark 的三个前提必须同时满足，缺一个就崩，而且报错信息完全不一样（分别是 EOF、FileNotFound、worker crash），很容易误判成"我代码写错了"。

1. `pyspark >= 4.0.3`（Python 3.12+ 时）
2. `HADOOP_HOME` 指向含 `bin\winutils.exe` 的目录
3. `PYSPARK_PYTHON` 与 driver 是同一个解释器

这三条 `scripts/spark_smoke_test.py` 里都已经处理，直接跑它就行；自己写脚本时记得抄这三行。

## 路径说明

warehouse 默认在 `D:\warehouse`（你的 D 盘还有 450 GB）。如果要用别的盘，改两个地方：

- `infra/docker-compose.yml` 里两处 `file:///D:/warehouse`
- `scripts/spark_smoke_test.py` 顶部的 `WAREHOUSE` 和 `WAREHOUSE_DIR`

## 第 1 步已完成：TSV → ODS 分区表

脚本：`scripts/build_ods.py`（跑法 `./.venv-spark/Scripts/python.exe -u scripts/build_ods.py`，约 2.3 分钟）

### 实测结果

```
分区数: 31     首尾: 2026-09-21 ... 2026-10-21
全表行数: 16,468,027        （与源 TSV 完全一致）
单日行数: 572,035 (dt=2026-09-21)

合计 31 个 parquet 文件 / 457.9 MB   （原 TSV 2,589,891,984 B ≈ 2470 MB，压缩 5.4 倍）
平均每个文件 14.8 MB

分区裁剪: Location: InMemoryFileIndex(1 paths)[file:/D:/warehouse/ods.db/ods_ad_log/dt=2026-09-21]
```

Metastore 里对应 31 行登记，`LOCATION` 精确到目录：

```
   PART_NAME   |                      LOCATION
---------------+----------------------------------------------------
 dt=2026-09-21 | file:/D:/warehouse/ods.db/ods_ad_log/dt=2026-09-21
 dt=2026-10-21 | file:/D:/warehouse/ods.db/ods_ad_log/dt=2026-10-21
```

### 日期平移（面试会问，先记住口径）

原始 `timestamp` 是**从 0 开始的相对秒**（脱敏数据），直接换算会得到 1970-01-xx。
所以映射到 2026-09-21 起算的 31 天：

```
day_offset = floor(timestamp / 86400)     # 0 .. 30
dt         = 2026-09-21 + day_offset
```

原始列 `timestamp` 原样保留在表里，**平移是无损可逆的**。这个必须能主动说清楚。

### 关于「一天 15 MB」这个数字

31 天总 458 MB，除以 31 就是 15 MB/天 —— **这是分区粒度决定的天花板**，
写法再优化也不可能超过它。Parquet 的甜点区是 128~512 MB/文件，所以这份数据
按天分区其实**偏细**，生产环境日增 10 GB 时按天分区才正好。

留着按天分区的理由不是"文件大小合适"，而是**只有按天分区才能做增量刷新和补数**。
这是「为了业务能力忍受一点存储效率」的典型取舍，要能讲出这个 trade-off。

### 这一步撞到的三个坑（全是静默失败，不报错）

| 现象 | 原因 | 解法 |
|---|---|---|
| `逻辑列数: 45`（应是 23），随后报 `Cannot safely cast timestamp "STRING" to "BIGINT"` | 在 `withColumn` 里写了带反引号的列名 `"\`timestamp\`"`。**反引号是 SQL 解析器的语法，DataFrame API 里会被当成一个全新列名**（连反引号一起算进列名），原列没被覆盖，于是 22 列变 44 列 | DataFrame API 一律用裸列名 `F.col("timestamp")`；反引号只写在 `spark.sql("...")` 的 SQL 文本里 |
| `Warning: Ignoring non-Spark config property: hive.metastore.uris` | `.config("hive.metastore.uris", ...)` 不带 `spark.` 前缀会被 SparkConf 丢弃，session **静默退回本地 Derby**，建的表只在临时目录，进程退出即消失 | 改成 `spark.hadoop.hive.metastore.uris`；`hive.exec.dynamic.partition*` 同理 |
| 31 个分区写出了 **50 个文件**，每 1~2 个交替 | `INSERT OVERWRITE ... PARTITION(dt)` 时 Spark 的 V1 写入器按**行数均分**切数据（range 切分），切分点落在某天中间就把那天拆成两个文件 | 写之前 `repartition(31, "dt")` 做 hash 重分区 —— 同一个 dt 必然落进同一个 task，一个 task 连续写一个文件 |
| 验证时报 `单日行数: 0`、`Location: InMemoryFileIndex(0 paths)` | `SHOW PARTITIONS` 返回的是 `dt=2026-09-21` 整串，不是纯日期。拿它当 `WHERE dt = '...'` 条件查不到数据，**且不报错** | `r[0].split("=", 1)[1]` 取日期部分 |

三个坑的共同点值得记：**它们都不抛异常**。列数翻倍、退回 Derby、文件数变多、查询返回 0 行，
全都"看起来跑成功了"。所以脚本里加了两处硬检查：

- 列数断言（`len(df.columns) != 23` 直接中止）
- Metastore 连通性自检（看不到 `lab` 库就中止，因为连 Derby 时也不报错）

## 第 2 步已完成：ODS → DWD（清洗 + 去重 + 派生）

### 实测结果（2026-10-22）

| 项 | 结果 |
|---|---|
| 分区 / 文件 | **31 / 31**（`dt=2026-09-21` .. `2026-10-21`），平均 19.1 MB/文件 |
| 大小 | 592.4 MB（ODS 457.9 MB → DWD 变大，因为多了 6 个派生列） |
| 行数 | **16,468,027**，与 ODS 逐行一致 |
| 对账 | `ODS 16,468,027 − 拦掉 0 = 16,468,027`，实际写入 16,468,027 ✅ |
| `dt` 与 `event_time` 一致性 | 0 行不一致 ✅（时区换算没出错） |
| 维表 | 675 个 campaign，曝光合计与明细行数一致 ✅ |
| 耗时 | 3.4 分钟（明细 74s + 维表 63s + 审计） |

### 9 条清洗规则全部 0 命中 —— 这是**结论**，不是失败

| 规则 | 命中 |
|---|---|
| R1 uid 非空 / R2 campaign 非空 / R3 时间非负 | 0 / 0 / 0 |
| R4、R5 标志只取 0-1 / R6 cost>0 | 0 / 0 |
| R7 转化时间不早于曝光 / R8 哨兵值自洽 / R9 有归因必有点击 | 0 / 0 / 0 |
| 去重（`uid, campaign, timestamp`） | 0 组重复 |

这份公开数据集是**已经被上游清洗过的**，所以规则一次都没被执行到。

但"0 命中"不能拿来证明规则有用 —— 必须用**故障注入**证明：`scripts/test_dwd_rules.py`
往真实数据里人为塞 9 种脏数据，验证每条规则精确命中、且零误伤（已跑通）。

### 派生字段实测值

| 字段 | 值 | 说明 |
|---|---|---|
| `is_click` | 5,947,563（36.12%） | 可归因点击子集，不是真实 CTR |
| `is_conversion` | 806,196（4.90%） | |
| `is_attributed` | 442,424（2.69%） | 是转化的子集（54.87%） |
| `is_first_click` | 2,640,891（占点击 44.4%） | 首次点击比想象的多：近一半点击是"这个人的第一次点击" |
| `prev_click_gap_sec` 中位数 | 3.92 天 | 最大恰好 30.00 天 |
| `conv_delay_sec` 中位数 | **3.74 天** | 最大 **恰好 30.00 天** = 归因窗口上限 |
| `sum(cost)` | 4,829.34 | 归一化单位，不是货币 |

**转化延迟的形状是真实的衰减**（第 0 天 34.36% → 第 1 天 7.09% → 第 15 天 1.69% → 第 29 天 0.95%）。
如果数据是随机生成的，每个天桶会差不多高。这里单调衰减，说明保留了真实行为特征。
**平均值 7.1 天是被长尾拉高的，对外必须报中位数 3.74 天。**

### 这一步撞到的坑

| 现象 | 原因 | 解法 |
|---|---|---|
| `sum(is_first_click) = 13,073,723,398,784` | 表是**旧结构**（早期 `--audit-only` 时建的），`CREATE TABLE IF NOT EXISTS` 对已存在的表静默跳过。`INSERT` **按位置**写列，于是新顺序的数据灌进了旧列名的槽位，从第 18 列起全部错位，**作业零报错** | 写盘前把 Metastore 里的真实列和代码里的列**逐项比对**，不一致就中止（`--recreate` 才允许重建） |
| 规则一次拦掉 33.65%（554 万行，而总点击才 594 万） | 我按列名把 `click_nb` 当成"累计点击次数"，实际它是**转化上下文**：只有转化的行才有值，其余填 `-1` 哨兵 | 先量分布再定规则。顺序反了就会用一条"看起来很有道理"的规则删掉三分之一数据，且不报错 |
| `countDistinct(col)` 报 `UNRESOLVED_ROUTINE` | 它是 **DataFrame API 的函数**，SQL 里解析不到 | SQL 里写 `count(DISTINCT col)` |
| 探查按 uid 分组跑了 14 分钟还在溢写 | `groupBy(uid).agg(count(*), count(DISTINCT campaign))` 要做**两层 shuffle**（614 万个组，每组维护一个集合），2 核 2G 放不下 | 探查阶段换近似：`approx_count_distinct` + TOP-N，目标是把假说排除掉，不是出精确报表 |
| `Unable to instantiate SessionHiveMetaStoreClient` | 这台机器重启后 Docker 引擎没起，9083 没人监听。报错信息只说了"实例化不了客户端"，**完全没提 Docker** | 跑之前先 `docker compose up -d`（`infra/` 下），并等 9083 端口通 |

## 第 3 步已完成：DWD → DWS（汇总层）

### 实测结果（2026-10-22）

| 表 | 粒度 | 行数 | 文件 | 分区 |
|---|---|---|---|---|
| `dws.dws_ad_campaign_daily` | campaign × 天 | **20,665** | 2 个 / 1,296 KB | **不分区** |
| `dws.dws_ad_date_daily` | 天 | **31** | 1 个 | **不分区** |

压缩比 **786 : 1**（16,468,027 → 20,665 行）。

### 为什么 DWS 两张表都**不分区**（和 ODS/DWD 相反）

| 层 | 一天的数据量 | 该不该按天分区 |
|---|---|---|
| ODS / DWD | 15 ~ 19 MB | 分。为了增量刷新和补数 |
| DWS | **50 KB** | **不分。** 分了就是 31 个 50KB 的小文件 |

判断标准不是"有没有时间字段"，而是**单个分区是否落在 128 MB ~ 1 GB**。
50 KB 的文件光是打开就要几十毫秒，扫 31 个文件的开销比扫数据本身还大。

### 六项验收（全部通过）

| 验收 | 结果 |
|---|---|
| 行数 = 源表 `(dt, campaign_id)` 组合数 | 20,665 = 20,665 ✅ |
| 分组键无重复 | 0 ✅ |
| **上卷对账**：按天把 campaign 明细加起来 = 全站当天 | 31 个 dt 全部一致 ✅ |
| **全表对账**：DWS 合计 = DWD 明细 | 曝光/点击/转化/cost 四项全等 ✅ |
| 比率自洽：存下来的 `ctr` = `clicks/impressions` 重算值 | 0 行不一致 ✅ |
| 物理大小 | 1,296 KB / 2 文件 ✅ |

### 一个必须能讲出来的数字

```
对 20,665 行 ctr 直接 avg()          = 35.13%   ← 错的
用 sum(clicks)/sum(impressions)      = 36.12%   ← 对的
偏差 2.7%
```

**把比率平均，等于给一个大计划和一个小计划同样的权重。**
这就是为什么 DWS 必须存"可加的基础指标"，比率只能看、不能二次聚合。
面试问"你们报表的 CTR 怎么算"，答得出这个 2.7% 的偏差，比说"用 SQL 算的"强得多。

### 这轮撞到的坑

| 现象 | 原因 | 解法 |
|---|---|---|
| 行数 20,665 比 `675 × 31 = 20,925` 少 260 | **我的断言写错了**，不是数据错。"675 个计划天天都跑"是个想当然的假设，实际平均每个计划有 0.39 天停投 | 对账基准只能是源表真实的 `count(distinct (dt, campaign_id))`，不能硬写一个整数 |
| `TypeError: float - Decimal` | Spark 4 里 `bigint * 1.0 / bigint` 返回 `Decimal`，不是 `float` | 显式 `float()`；或用 `CAST(... AS DOUBLE)` |

## 倾斜实验：4 种写法对照（`scripts/demo_skew.py`）

前提：这份数据**本身完全不倾斜**（campaign 最大 2.66%、uid 最大 880 行、cat3 最大 2.04%），
所以人为造一个"热渠道吃掉 60% 流量"的事实表来练。

| 方案 | join 策略 | 最大输出分区占比 | 整作业耗时 |
|---|---|---|---|
| baseline（sort-merge，关广播关 AQE） | SortMergeJoin | **64.7%** | 15.4s |
| broadcast（广播 200 行维表） | BroadcastHashJoin | 100%（只有 1 个分区） | **8.6s** |
| aqe_skew（开 AQE 自动拆） | SortMergeJoin | 64.7% | 10.6s |
| salted（热键拆 8 份） | SortMergeJoin | **20.0%** | 11.0s |

**能确认的**：baseline 下一个分区扛了 64.7%，salted 把它打到 20.0%；broadcast 完全不 shuffle，最快。

**不能确认的（诚实标注）**：AQE 在这个例子里**没有观察到分区拆分**（join 阶段 task 数仍是 8）。
本地 2 核能提供的并行度太低，15.4s→10.6s 这个差距不足以判断 AQE 是否有效。
要可靠评估 AQE，需要真集群 + Spark UI 的 task 级耗时分布。
**这条不写成结论，写成待验证项。**

### 这个实验的测量方法改了三版（比结果更有价值）

| 版本 | 用的指标 | 为什么错 |
|---|---|---|
| 1 | `df.rdd.glom().map(len)` | **DataFrame 转 RDD 会绕过 AQE**，等于把被测对象关掉了 |
| 2 | 写盘后按文件统计行数 | AQE 拆的是 **task** 不是分区，输出分布**天生对这个指标失明** |
| 3（当前） | 输出分布 + `SparkStageInfo.numTasks` | 两个指标各管一半：分布管 broadcast/salted，task 数管 AQE |

**教训：指标选错了比不测更危险**，因为它会给出一个自信的错误结论。
前两版都差点让我说"AQE 对倾斜没用"。

## 第 1 周接下来做什么

环境跑通之后，按这个顺序推进（每一步都会撞一个真实的墙）：

| 步骤 | 做什么 | 会撞上 |
|---|---|---|
| ~~1~~ | ~~Criteo 16.5M 行 TSV → Parquet 落到 `ods.db`，按 `dt` 分区~~ | ✅ **已完成**（31 分区 / 31 文件 / 457.9 MB） |
| 2 | ~~建 DWD 明细表，按 `dt` 分区，从 ODS 洗一遍~~ | ✅ **已完成**（31 分区 / 592.4 MB / 对账一致），坑见上一节 |
| 3 | ~~DWS 汇总：按 campaign × 天的曝光/点击/CTR/CVR/CPA~~ | ✅ **已完成**（20,665 行 / 不分区 / 六项验收通过），见上一节 |
| 4 | 用 Airflow（或 cron + 幂等脚本）每天跑一次 | **幂等**（重跑不能翻倍） |
| 5 | 写指标字典 + 数据质量校验 | **口径冲突**（同一个 CTR 两种算法差 3%） |

## 数据源：为什么最后选了 Criteo 而不是 Avazu

先说结论：**用 `criteo/criteo-attribution-dataset`。**

一开始选的是 Avazu（40.4M 行，量更大），下载完才发现两个致命问题：

1. **`reczoo/Avazu_x1` 是预处理过的**：列名全变成 `label, feat_1..feat_22`，`hour`、`site_id`、`device_model` 这些业务字段全被抹掉，你没法建维度表
2. **它没有时间字段，而且行是随机打乱的**（原文说 "randomly split into 7:1:2"），所以**做不了 dt 分区、做不了增量、做不了幂等重跑** —— 而这三个正是这次练习的核心

Criteo 这份是你需要的形状：

| 维度 | Criteo 归因数据集 |
|---|---|
| 行数 | 16,468,027（一行 = 一次展示） |
| 时间 | 31 天，按时间顺序，`timestamp` 是**相对秒**（从 0 开始） |
| 金额 | `cost`（抽象单位，不是美元） |
| 渠道 | `campaign`，675 个 |
| 指标 | 可算 CTR / CVR / CPC / CPA；**算不了 ROI**（没有收入字段） |

实测分布（`scripts/profile_criteo.py` 的真实输出）：

```
impressions = 16,468,027        clicks = 5,947,563      CTR = 36.12%
conversions =    806,196        CVR = 4.90%             cost 合计 = 4,829.34
CPA = 0.0060                    CPC = 0.0008            campaign 数 = 675

按天：31 天，日均 531,227 行，最小 436,408 / 最大 638,165（1.46 倍，比较均匀）
最大 campaign 占 2.66%（**不倾斜** —— 倾斜要从别的列制造）
```

两个必须说清的点，否则面试会被问倒：

- **CTR 36% 不符合常识**（真实展示广告 1% 左右）。原因是这份数据只包含"可归因点击"的展示子集，不是全量曝光。别把它当成真实 CTR 水平报。
- **ROI 算不了**。`cpo` 是 campaign 的转化成本口径，不是转化金额。硬编一个"收入"字段就是在造假，面试一问就露。

数据下载：

```bash
# Criteo（主用，622 MB → 解压 2.47 GB）
./.venv-spark/Scripts/python.exe scripts/download_criteo.py

# 探查（会打印上面那份分布 + 建表决策）
./.venv-spark/Scripts/python.exe -u scripts/profile_criteo.py
```

### 下载时会撞的三个坑（都已在脚本里处理）

1. **仓库里只有一个 `.tsv.gz` / `.zip`，按 `*.csv` 过滤会下到 0 个文件**
2. **huggingface.co 国内直连不通**，`hf-mirror.com` 可以。**探测不能用 TCP 连接** ——
   huggingface.co 的 443 常常能握手成功但 HTTPS 请求被丢包，必须发真实请求拿到 200 才算通。
3. **系统代理访问 hf-mirror 会 502**（`httpx2.ProxyError`），脚本会临时清掉代理变量。
   另外 `huggingface_hub` 在 **import 时** 就固定了 `HF_ENDPOINT`，之后再设环境变量无效 ——
   所以脚本改用 urllib 直接下，不用 snapshot_download。

### 内存这件事：Spark 只能用 2 GB

你机器 16 GB，Docker Desktop 自己吃掉约 5 GB，加上其它容器，宿主机常常只剩 3～4 GB。
第一次跑探查用 `local[4] + driver 3g`，进程直接被系统杀掉（`SIGTERM`，日志停在时间统计那一步，没有任何报错）。

**所以所有 Spark 脚本统一用 `local[2] + driver 2g`**，并且：

- 探查时不要"22 列当字符串整表扫两遍"，只 `select` 需要的那几列
- 类型推断用 Python 读前 20 万行做，别交给 Spark 全表推断

