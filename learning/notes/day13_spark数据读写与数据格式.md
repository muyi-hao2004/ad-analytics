# Day13：Spark 数据读写（Data I/O）与数据格式

## 一、Spark 数据处理流程

```
读取数据（从文件/数据库）→ 转换处理 → 写出结果（到文件/数据库）
```

---

## 二、核心概念：行存储 vs 列存储

| 存储方式 | 原理 | 特点 |
|---|---|---|
| **行存储** | 一行数据连续存在一起 | 读完整行快，读特定列慢 |
| **列存储** | 同一列数据连续存在一起 | 读特定列快，压缩比高 |

> 大数据分析大多只需要几列，所以列存储（Parquet）快很多。

---

## 三、四种数据格式对比

| 格式 | 类型 | 存储 | 体积 | 查询速度 | 人能看懂 | 典型用途 |
|---|---|---|---|---|---|---|
| **CSV** | 文本文件 | 行存储 | 大 | 慢 | ✅ | 小数据交换、Excel |
| **Parquet** | 二进制文件 | 列存储 | 最小 | 最快 | ❌ | 大数据分析、数据仓库 |
| **JSON** | 文本文件 | 行存储 | 最大 | 慢 | ✅ | API 数据、日志 |
| **MySQL** | 数据库 | 行存储 | 中等 | 中等 | ✅（工具） | 业务系统、实时查询 |

---

## 四、读取数据

```python
# CSV（已学）
spark.read.csv("data.csv", header=True, inferSchema=True)

# Parquet（大数据最常用！）
spark.read.parquet("data.parquet")

# JSON
spark.read.json("data.json")

# MySQL
spark.read.jdbc(url="jdbc:mysql://localhost:3306/db", table="table_name", properties=props)
```

> Parquet 自带列名和类型，不需要 header 和 inferSchema。

---

## 五、写出数据

```python
# CSV
df.write.csv("output", header=True, mode="overwrite")

# Parquet
df.write.parquet("output", mode="overwrite")

# JSON
df.write.json("output", mode="overwrite")

# MySQL
df.write.jdbc(url=..., table=..., mode="overwrite", properties=props)
```

---

## 六、保存模式（mode）

| mode | 意思 | 用途 |
|---|---|---|
| `overwrite` | 覆盖（删掉旧的重新写） | 全量任务 |
| `append` | 追加（在已有数据后面加） | 增量任务 |
| `ignore` | 忽略（已存在就不写） | 只写一次 |
| `error` | 报错（已存在就抛异常） | 生产环境默认 |

---

## 七、分区存储（partitionBy）

### 什么是分区存储？
按某一列把数据分成多个文件夹存储，是大数据性能优化的核心手段。

```python
# 按渠道分区
df.write.partitionBy("channel").parquet("output", mode="overwrite")

# 多级分区（日期+渠道）
df.write.partitionBy("date", "channel").parquet("output", mode="overwrite")
```

### 目录结构
```
output/
  date=2026-09-01/
    channel=Facebook/
    channel=Google/
  date=2026-09-02/
    channel=Facebook/
    channel=Google/
```

### 好处
1. **查询更快**：只查某个渠道/某天的数据，只读对应分区（分区裁剪）
2. **数据组织清晰**
3. **增量写入方便**

### 分区列选择原则
- ✅ 取值少的列：channel（5个值）、date（按天）
- ❌ 取值多的列：user_id（几百万值）、campaign（几千值）

---

## 八、实际工作中的数据流

```
1. 原始 CSV（小数据，人能看）
   ↓
2. Spark 读取 + 清洗 + 计算
   ↓
3. 中间结果 → Parquet（大数据，快，省空间）
   ↓
4. 最终报表 → MySQL（业务查询，实时）
   ↓
5. 接口返回 → JSON（给前端/Web）
```

---

## 九、练习文件

- `spark_basics/08_data_io.py`：数据读写完整练习（CSV/Parquet/JSON/MySQL + 分区存储 + 完整ETL流程+10道练习题）
