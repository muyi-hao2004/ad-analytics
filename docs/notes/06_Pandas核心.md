# Pandas 核心

本篇覆盖：DataFrame 与 Series 两个数据结构；`read_csv` 的 12 个常用参数逐个拆解；日常最高频的 10 个操作；分析场景里最核心的 `groupby / agg / np.where / shift / rolling / rank`；缺失值处理与类型转换；8 个常见报错；以及"什么时候用 pandas、什么时候该交给 SQL"的判断标准。广告指标（CTR / CVR / CPC / CPA / ROI）的定义见 19 篇，本篇不重复展开。

---

## 一、pandas 是什么

pandas 是 Python 里做表格数据处理的库，可以理解为"能编程的 Excel"。

两个核心数据结构：

| 结构 | 维度 | 类比 |
|---|---|---|
| `DataFrame` | 二维，有行索引和列名 | Excel 的一张表 |
| `Series` | 一维，带索引 | 表里的一列 |

```python
import pandas as pd
import numpy as np

df = pd.DataFrame({
    "channel": ["Facebook", "Google", "TikTok"],
    "cost": [500.0, 300.0, 200.0],
})
df["cost"]        # Series
df[["channel"]]   # DataFrame（一层方括号取列，两层取表）
```

理解"单列是 Series、多列是 DataFrame"很重要，因为很多方法只存在于其中一个上：`df["cost"].dt.month` 能跑，`df.dt` 不能。

---

## 二、read_csv 参数详解

`pd.read_csv()` 把 CSV 文本读成 DataFrame。CSV 本质是纯文本，每行一条记录，字段用分隔符隔开：

```
date,channel,campaign,impressions,clicks,cost
2026-08-01,Facebook,夏季促销,10000,200,500.0
```

最简单的用法：

```python
df = pd.read_csv("data.csv")
```

默认参数能应付干净的小文件，真实数据几乎一定会踩到下面 12 个参数中的某几个。

### 2.1 参数总表

| 参数 | 作用 | 默认值 | 典型场景 |
|---|---|---|---|
| `filepath_or_buffer` | 文件路径 / URL / 缓冲区 | 必填 | 指定数据位置 |
| `sep` | 字段分隔符 | `,` | TSV 用 `\t`，导出文件用 `;` |
| `header` | 第几行作表头 | `0` | 无表头用 `None`，前有说明行用 `2` |
| `names` | 手动指定列名 | `None` | 无表头或列名不规范 |
| `dtype` | 指定列类型 | 自动推断 | ID 防前导零、空值列用 `Int64` |
| `parse_dates` | 解析日期列 | `False` | 需要按时间分组/排序 |
| `na_values` | 自定义缺失值标记 | 内置若干 | `-`、`无`、`N/A` 等 |
| `encoding` | 文件编码 | `utf-8` | Windows Excel 导出常用 `gbk` |
| `nrows` | 只读前 N 行 | `None` | 大文件先探结构 |
| `usecols` | 只读指定列 | `None` | 几十列里只要几列 |
| `skiprows` | 跳过开头若干行 | `None` | 文件头有说明文字 |
| `chunksize` | 分块读取，返回迭代器 | `None` | 单机内存装不下 |

### 2.2 路径：filepath_or_buffer

**是什么**：CSV 的位置，可以是相对路径、绝对路径、URL，也可以是已打开的文件对象。

**为什么注意**：这是唯一必填参数。工程上不要写死绝对路径，否则换机器就跑不了。

```python
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent

# 不推荐：写死路径，换机器就失效
df = pd.read_csv(r"C:\Users\Mia\project\data\raw\data.csv")

# 推荐：用项目根目录拼接
df = pd.read_csv(PROJECT_ROOT / "data" / "raw" / "data.csv")
```

### 2.3 分隔符：sep

**是什么**：列与列之间用什么字符隔开，默认逗号。

**不用会怎样**：分隔符不对时，整行会被当成一列，列数变成 1，后续所有操作都失效。

```python
pd.read_csv("data.csv")                 # 逗号（默认）
pd.read_csv("data.tsv", sep="\t")       # 制表符
pd.read_csv("data.csv", sep=";")        # 分号（部分欧洲区 Excel 导出）
pd.read_csv("data.txt", sep="|")        # 竖线
pd.read_csv("data.txt", sep=r"\s+")     # 连续空白，正则分隔
```

判断方法：读进来后 `df.shape` 的列数明显不对、`df.columns` 只有一个长字符串，基本就是 sep 错了。

### 2.4 表头：header 与 names

**header**：第几行是列名，默认第 0 行（第一行）。

| 取值 | 含义 |
|---|---|
| `header=0` | 第一行是表头（默认） |
| `header=None` | 没有表头，第一行就是数据，列名变成 0, 1, 2… |
| `header=2` | 第三行是表头，前两行是说明 |

**names**：手动指定列名，会覆盖文件里的表头。

```python
# 没有表头，手动命名
df = pd.read_csv("data.csv", header=None,
                 names=["日期", "渠道", "计划", "曝光", "点击", "花费"])
```

**易错点**：文件本身有表头，却又传了 `names`，第一行数据会被当成数据行读进来，导致多一行脏数据、所有列变成 object。此时要配合 `header=0`（丢弃原表头）或 `skiprows=1`。

### 2.5 类型：dtype

**是什么**：强制指定每列的数据类型。

**为什么必须用**：pandas 的自动推断在四类情况下会出错。

| 情况 | 后果 |
|---|---|
| ID 列全是数字 | 被推断成 int，前导零丢失（`"007"` → `7`） |
| 本该是数值的列里混了 `"-"`、`"无"` | 整列变成 object，无法计算 |
| 金额列 | 可能被推断成 float，出现精度误差 |
| 大文件 | 默认 int64 / float64 占内存多，可用 int32 / float32 |

```python
df = pd.read_csv("data.csv", dtype={
    "campaign_id": str,      # ID 强制字符串，防止前导零丢失
    "impressions": "Int64",  # 可空整数
    "cost": float,
})
```

**关键细节**：列里有空值时不能直接指定 `int`，因为 numpy 的 int 无法表示空值，会报错。要用 pandas 的可空整数类型 `Int64`（首字母大写），或先按 float 读进来再转换。

### 2.6 日期：parse_dates

**是什么**：告诉 pandas 哪些列是日期，读入时直接解析成 `datetime64`。

**为什么**：不解析的话日期是字符串，`dt` 访问器完全不可用。

```python
# 不解析
df = pd.read_csv("data.csv")
df["date"].dt.month        # AttributeError: Can only use .dt accessor with datetimelike values

# 解析
df = pd.read_csv("data.csv", parse_dates=["date"])
df["date"].dt.month        # 提取月份
df["date"].dt.dayofweek    # 星期几（0 = 周一）
df["date"].dt.year         # 年份
```

多列合并成一个日期列时传嵌套列表：

```python
pd.read_csv("data.csv", parse_dates=[["date", "time"]])
```

### 2.7 缺失值标记：na_values

**是什么**：指定哪些字面量应当被当作缺失值（NaN）。

**为什么**：pandas 默认只识别空字符串、`NA`、`N/A`、`NULL`、`NaN` 等少数几种。业务数据里常见的 `-`、`无`、`未知`、`0`（某些系统用 0 表示缺失）不会自动识别，会以字符串形式混进数值列。

```python
df = pd.read_csv("data.csv",
                 na_values=["", "N/A", "null", "-", "无", "NA"])
```

按列指定：

```python
pd.read_csv("data.csv", na_values={"cost": ["-", "无"], "clicks": ["N/A"]})
```

> 用 `0` 当缺失标记要谨慎：0 可能真的是"零次点击"，替换成 NaN 会改变业务含义。这种情况应在读入后按业务规则处理，而不是在读文件时一刀切。

### 2.8 编码：encoding

**是什么**：文件的字符编码，默认 `utf-8`。

**为什么**：Windows 上 Excel 导出的 CSV 默认是 `gbk`，用 utf-8 读会报 `UnicodeDecodeError`，或中文变成乱码。

```python
pd.read_csv("data.csv", encoding="utf-8")     # 默认
pd.read_csv("data.csv", encoding="gbk")       # Windows Excel 导出
pd.read_csv("data.csv", encoding="gb18030")   # gbk 的超集，兼容性更好
```

**排查顺序**：utf-8 → gbk → gb2312 → gb18030 → `latin-1`（最后手段，能读但中文可能仍乱）。另有一个常被忽略的编码问题：文件开头带 BOM 时，第一列列名会变成 `\ufeffdate`，此时用 `encoding="utf-8-sig"`。

### 2.9 大文件三件套：nrows / usecols / chunksize

**nrows**：只读前 N 行，用于快速探查结构。

```python
df = pd.read_csv("big_data.csv", nrows=100)
```

**usecols**：只读需要的列，直接减少内存占用和解析时间。

```python
df = pd.read_csv("data.csv", usecols=["date", "channel", "cost"])
```

**chunksize**：分块读取，返回一个可迭代对象，每次给一个 DataFrame。

```python
chunk_iter = pd.read_csv("big_data.csv", chunksize=10000)
for chunk in chunk_iter:
    process(chunk)
```

**为什么需要**：pandas 是单机内存计算，几个 GB 的文件全量读入会直接 MemoryError。分块后内存占用只跟 `chunksize` 有关，与文件大小无关。

**分块的代价**：跨块的操作做不了。比如全局去重、全局排序、全局中位数，必须把所有块的结果汇总后再算一次（典型做法是分块算局部聚合，最后合并）。

### 2.10 skiprows

**是什么**：跳过文件开头的若干行，或跳过指定的若干行。

```python
pd.read_csv("data.csv", skiprows=2)          # 跳过前 2 行
pd.read_csv("data.csv", skiprows=[0, 2, 5])  # 跳过第 1、3、6 行（从 0 开始数）
pd.read_csv("data.csv", skiprows=lambda x: x % 2 == 0)  # 按规则跳（隔行取样）
```

**典型场景**：报表文件前几行是标题、导出时间、空行；或者底部有合计行需要跳过（合计行用 `skipfooter`，需配合 `engine="python"`）。

### 2.11 工程里的完整写法

```python
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

df = pd.read_csv(
    PROJECT_ROOT / "data" / "raw" / "ad_data.csv",
    sep=",",
    header=0,
    dtype={
        "campaign_id": str,
        "impressions": "Int64",
        "clicks": "Int64",
        "cost": float,
    },
    parse_dates=["date"],
    na_values=["", "N/A", "null", "-"],
    encoding="utf-8",
)
```

这份配置覆盖了工程里 90% 的坑：路径可移植、ID 不丢前导零、空值列能正常参与计算、日期可直接做时间运算、缺失值标记统一。

### 2.12 读取常见问题速查

| 问题 | 原因 | 解决方法 |
|---|---|---|
| 中文乱码 | 编码不对 | 试 `encoding="gbk"` / `"gb18030"` / `"utf-8-sig"` |
| 前导零丢失 | ID 列被推断成 int | `dtype={"id": str}` |
| 数字列没法计算 | 列里有非数字，整列是 object | `pd.to_numeric(errors="coerce")` |
| 日期列是字符串 | 没解析 | `parse_dates=["date"]` |
| 内存不够 | 文件太大 | `usecols` 选列，或 `chunksize` 分块 |
| 第一行数据被当成表头 | 用了 names 但没设 header | 配合 `header=0` 或 `header=None` |
| 所有列挤成一列 | sep 不对 | 指定正确的分隔符 |
| 列名多了个 `\ufeff` | 文件带 BOM | `encoding="utf-8-sig"` |
| 空值列指定 `int` 报错 | int 不能表示空 | 用 `"Int64"`（大写 I） |

---

## 三、最常用的 10 个操作

| 排名 | 操作 | 语法 | 用途 |
|---|---|---|---|
| 1 | 读取数据 | `pd.read_csv("文件.csv")` | 从 CSV 读入 |
| 2 | 查看数据 | `df.head()` / `df.info()` / `df.describe()` / `df.shape` | 了解数据基本情况 |
| 3 | 取列 | `df["列名"]` / `df[["列1","列2"]]` | 取需要的列 |
| 4 | 条件筛选 | `df[df["列"] > 值]`，多条件用 `&` / `\|` | 筛选行 |
| 5 | 排序 | `df.sort_values(by="列", ascending=False)` | 按某列排序 |
| 6 | 新增列 | `df["新列"] = ...` / `np.where()` / `apply()` | 计算衍生字段 |
| 7 | 分组聚合 | `df.groupby("列").agg(...)` | 分组统计（最核心） |
| 8 | 缺失值处理 | `df.isna().sum()` / `fillna()` / `dropna()` | 清洗数据 |
| 9 | 合并 | `pd.merge()` / `pd.concat()` | 合并多张表 |
| 10 | 保存数据 | `df.to_csv("文件名.csv", index=False)` | 结果落盘 |

### 3.1 查看数据

```python
df.head()                # 前 5 行，df.head(10) 前 10 行
df.tail()                # 后 5 行
df.shape                 # (行数, 列数)，属性，不加括号
df.columns               # 所有列名
df.dtypes                # 每列数据类型
df.info()                # 行数、列数、非空值个数、类型、内存占用
df.describe()            # 数值列的计数、均值、标准差、最小、四分位、最大
df["列"].unique()        # 该列有哪些不同值（返回数组）
df["列"].value_counts()  # 每个值出现多少次（默认按频次降序）
df["列"].nunique()       # 不同值的个数
```

`df.info()` 是排查问题的第一站：它一眼能看出哪些列有缺失（非空数 < 总行数）、哪些列类型不对（object 却应该是数字）。

### 3.2 取列

```python
df["name"]              # 单列 → Series（一层方括号）
df[["name", "math"]]    # 多列 → DataFrame（两层方括号）
```

**易错点**：多列必须两层方括号。`df["name", "math"]` 会被解释成用元组做索引，直接 KeyError。

### 3.3 条件筛选

```python
df[df["math"] > 80]                            # 单条件
df[(df["math"] > 80) & (df["english"] > 80)]   # 且
df[(df["math"] > 90) | (df["chinese"] > 90)]   # 或
df[~(df["math"] > 80)]                         # 非
df[df["class"] == "一班"]                       # 等于用 ==
df[df["class"].isin(["一班", "二班"])]           # 属于集合
df[df["name"].str.contains("张")]               # 字符串包含
df[df["cost"].between(100, 500)]                # 区间（闭区间）
df[df["cost"].isna()]                           # 判空
```

**三条硬规则**：

1. 多条件用 `&` / `|` / `~`，**不能用 `and` / `or` / `not`**。前者是逐元素的位运算，后者要求整个对象给出一个布尔值，会报 `ValueError: The truth value of a Series is ambiguous`；
2. 每个条件必须用括号包住，`&` 的优先级高于比较运算符；
3. 等于用 `==`，`=` 是赋值。

### 3.4 排序

```python
df.sort_values(by="math", ascending=False)                     # 降序
df.sort_values(by="math")                                      # 升序（默认）
df.sort_values(by=["class", "math"], ascending=[True, False])   # 多列，方向可分别指定
df.sort_values(by="math", ascending=False).head(3)              # Top 3
```

**注意**：`sort_values` 默认不修改原表，返回新表。要保留结果必须赋值：`df = df.sort_values(...)`。

`na_position="first"` / `"last"` 控制缺失值位置，默认排在最后。

### 3.5 新增列

```python
# 方式一：直接赋值（向量化，最快）
df["total"] = df["math"] + df["english"] + df["chinese"]
df["average"] = (df["total"] / 3).round(1)

# 方式二：np.where 做条件判断（类似 Excel 的 IF）
df["grade"] = np.where(df["average"] >= 90, "优秀",
              np.where(df["average"] >= 80, "良好",
              np.where(df["average"] >= 70, "中等", "及格")))

# 方式三：apply + 自定义函数（复杂逻辑）
def get_level(total):
    if total >= 270:
        return "学霸"
    elif total >= 240:
        return "优秀"
    elif total >= 210:
        return "良好"
    return "加油"

df["level"] = df["total"].apply(get_level)
```

**三者的取舍**：

| 方式 | 适用 | 性能 |
|---|---|---|
| 直接赋值 / 向量化运算 | 算术、字符串拼接 | 最快 |
| `np.where` | 2~4 个分支的简单条件 | 快 |
| `apply` | 分支多、逻辑复杂、要调外部函数 | 慢（逐行 Python 调用） |

能用向量化就不要用 `apply`。十万行以内差别不明显，百万行以上差距是数量级的。

### 3.6 分组聚合

见下一节，这是 pandas 分析的核心。

### 3.7 缺失值处理

```python
df.isna().sum()                        # 每列缺失值个数
df.isna().sum() / len(df)              # 每列缺失比例
df["math"].isna().sum()                # 单列缺失数

df.dropna()                            # 删掉任何含缺失的行
df.dropna(subset=["math"])             # 只按 math 列判断
df.dropna(axis=1)                      # 删掉含缺失的列
df.dropna(thresh=5)                    # 至少 5 个非空才保留

df.fillna(0)                           # 全部填 0
df["math"].fillna(df["math"].mean())   # 用均值填
df.fillna({"math": 0, "english": 60})  # 按列填不同值
df["math"].ffill()                     # 用前一个值填充（时间序列常用）
df["math"].interpolate()               # 线性插值
```

**选择原则**：

- 缺失比例很低（< 5%）且随机分布 → 删行；
- 数值列 → 均值/中位数填充（有离群值用中位数）；
- 类别列 → 众数填充，或单独设一个 "unknown" 类别；
- 时间序列 → 前向填充 `ffill`；
- 缺失本身有业务含义（"没有投放"就是 0）→ 填 0，而不是填均值。

### 3.8 重复值处理

```python
df[df.duplicated()]                                  # 查看重复行
df.duplicated(subset=["name"]).sum()                 # 按某列判断重复的条数
df.drop_duplicates()                                 # 删除完全重复的行
df.drop_duplicates(subset=["name"], keep="first")    # 按某列去重，保留第一条
df.drop_duplicates(subset=["name"], keep="last")     # 保留最后一条
df.drop_duplicates(subset=["name"], keep=False)      # 所有重复项都删掉
```

`keep="first"` 是默认值。做"取最新一条"的去重时，先按时间降序排序，再 `keep="first"`。

### 3.9 类型转换

```python
df["enroll_date"] = pd.to_datetime(df["enroll_date"])       # 转日期
df["math"] = df["math"].astype(int)                          # 转整数
df["math"] = df["math"].astype(float)                        # 转浮点
df["name"] = df["name"].astype(str)                          # 转字符串
df["cost"] = pd.to_numeric(df["cost"], errors="coerce")      # 转数值，转不了变 NaN
```

**`errors="coerce"` 是关键**：默认 `errors="raise"` 遇到 `"-"` 这种值会直接抛异常；`coerce` 把它变成 NaN，让脏数据显性化，之后统一处理。

**转 int 的前提**：列里不能有 NaN。有缺失时先填充，或用 `"Int64"`：

```python
df["impressions"] = df["impressions"].astype("Int64")
```

### 3.10 合并数据

```python
# merge：按列关联，类似 SQL JOIN
pd.merge(df1, df2, on="name", how="inner")             # 内连接
pd.merge(df1, df2, on="name", how="left")              # 左连接
pd.merge(df1, df2, on="name", how="outer")             # 全连接
pd.merge(df1, df2, left_on="id", right_on="user_id")   # 两边列名不同
pd.merge(df1, df2, on="name", suffixes=("_x", "_y"))   # 同名列加后缀

# concat：拼接
pd.concat([df1, df2], axis=0)   # 上下拼（加行），列名要对齐
pd.concat([df1, df2], axis=1)   # 左右拼（加列），按索引对齐
```

**merge 的坑**：

- 关联键在一方不唯一时，结果行数会膨胀（与 SQL 的"一对多"放大同理）。合并前先 `df2["id"].duplicated().sum()` 检查；
- 默认 `how="inner"`，忘记写会静默丢数据；
- `on` 的列名不一致时必须用 `left_on` / `right_on`，否则报 `KeyError`；
- 合并后行数变多，先怀疑键重复，不要急着继续算。

### 3.11 链式调用

把筛选、排序、取列连起来写，避免一堆中间变量：

```python
result = (
    df2[df2["major"] == "计算机"]                     # 筛选
    .sort_values(by="math", ascending=False)          # 排序
    [["name", "class", "major", "math", "english"]]   # 取列
)
```

链式调用的可读性来自"一行一步"。步骤超过 6~7 步时，拆成带名字的中间变量更好维护。

---

## 四、groupby / agg / np.where / shift / rolling / rank

这六个是分析场景的核心工具。前两个负责"汇总"，中间两个负责"行间计算"，后两个负责"排名与平滑"。

### 4.1 groupby：分组统计

**是什么**：按一列或多列把数据切成若干组，对每组独立做统计。

**为什么**：几乎所有业务指标都要"按维度看"——按渠道、按日期、按活动。没有 groupby，只能一遍遍手工筛。

```python
# 基本形式：df.groupby(分组列)[统计列].聚合函数()
df.groupby("class")["math"].mean()          # 各班数学平均分
df.groupby("class")[["math", "english"]].mean()   # 多列聚合

# 分组列保留为普通列（推荐）
df.groupby("class", as_index=False)["math"].mean()

# 等价的另一种写法
df.groupby("class")["math"].mean().reset_index()
```

**`as_index=False` 为什么重要**：默认分组列会变成行索引，后续想按这一列做筛选或合并会很别扭；设成 False 后分组列是普通列，结果直接就是一张规整的表。

**常用聚合函数**：

| 函数 | 含义 | 备注 |
|---|---|---|
| `sum()` | 求和 | 只对数值列有效 |
| `mean()` | 平均 | 忽略 NaN，分母是非空个数 |
| `count()` | 非空计数 | 任何类型都能用 |
| `size()` | 行数 | 含 NaN 行，与 count 不同 |
| `max()` / `min()` | 最大 / 最小 | 可用于日期 |
| `std()` | 标准差 | |
| `median()` | 中位数 | 抗离群值 |
| `nunique()` | 不同值个数 | 常用于"有多少个活动" |
| `first()` / `last()` | 组内第一 / 最后一条 | 配合排序可做"取最新" |

**count 与 sum 的区别**：`count` 数"有多少个"，`sum` 把值加起来。统计人数用 count，计算总花费用 sum。`count` 忽略 NaN，`size` 不忽略。

### 4.2 agg：多列多种聚合

**是什么**：让不同列用不同聚合方式，或同一列同时算多个指标。

**为什么**：一次 groupby 出齐所有指标，比多次 groupby 再 merge 快得多，也不容易对错行。

```python
# 字典方式：{列名: 聚合函数}
df.groupby("channel").agg({
    "impressions": "sum",
    "cost": "sum",
    "date": "nunique",
})

# 命名聚合（推荐）：结果列名清晰，不用二次改名
df.groupby("class").agg(
    人数=("name", "count"),
    数学平均分=("math", "mean"),
    数学最高分=("math", "max"),
    数学最低分=("math", "min"),
    数学及格人数=("math", lambda x: (x >= 60).sum()),
)

# 同一列多种聚合
df.groupby("channel").agg(
    cost_sum=("cost", "sum"),
    cost_avg=("cost", "mean"),
    cost_max=("cost", "max"),
)
```

**命名聚合的语法**：`新列名=(原列名, 聚合函数)`。这个形式比字典方式好两点——列名可控、可以做多指标同列。

**用 lambda 做条件计数**：`("math", lambda x: (x >= 60).sum())`。注意 lambda 里拿到的是 Series，要返回标量；返回 Series 会报错。性能上 lambda 是逐组 Python 调用，数据量大时可用 `(df["math"] >= 60)` 先造标记列再 sum。

**agg 与 apply 的区别**：`agg` 要求每个分组返回标量（或一列），结果是规整的表；`apply` 可以返回任意形状，灵活但慢，且结果结构不容易预测。

### 4.3 np.where：条件计算与除零保护

**语法**：`np.where(条件, 成立时的值, 不成立时的值)`。

**用途一：条件分支**，等价于 Excel 的 IF，可以嵌套：

```python
df["grade"] = np.where(df["average"] >= 90, "优秀",
              np.where(df["average"] >= 80, "良好", "及格"))
```

**用途二：除零保护**（更重要的用途）。直接相除，分母为 0 时 pandas 返回 `inf`，后续求平均、求和都会被污染。

```python
df["ctr"] = np.where(
    df["impressions"] > 0,
    df["clicks"] / df["impressions"],
    0,
)
```

**为什么不用 `if/else`**：`if` 只能判断一个标量，无法对整个 Series 逐元素判断。

**嵌套层数**：超过 3 层就该改用 `pd.cut` 或 `apply`，可读性会崩。

### 4.4 shift：计算环比

**是什么**：把整列数据沿索引方向移动若干位，用来取"上一行"的值。

```python
df = df.sort_values("date")                  # 必须先排序！
df["cost_prev"] = df["cost"].shift(1)        # 上一行的 cost
df["cost_next"] = df["cost"].shift(-1)       # 下一行的 cost
df["change"] = (df["cost"] - df["cost_prev"]) / df["cost_prev"]
```

**三个要点**：

1. **必须先按日期排序**。`shift` 取的是"上一行"，不排序就等于随机取一条；
2. 第一行 `shift(1)` 得到 NaN，算出的环比也是 NaN，需要 `dropna` 或 `fillna`；
3. 分组内做环比，要先 `groupby` 再 `shift`：

```python
df = df.sort_values(["channel", "date"])
df["prev_cost"] = df.groupby("channel")["cost"].shift(1)
```

**shift 与 diff 的区别**：`diff()` 直接给差值（本期 − 上期），`shift()` 给上一期的值。算增长率必须用 shift，因为要拿上一期的值做分母。

**shift 的坑**：它取的是"上一行"，不是"昨天"。日期有缺失时会错位——这一点与 SQL 的 `LAG` 完全一样，日期不连续时应改为按日期自关联。

### 4.5 rolling：移动平均

**是什么**：在索引方向上开一个固定长度的滑动窗口，对窗口内的数据做统计。

**为什么**：日数据波动大，移动平均能平滑噪声、看长期趋势。

```python
df = df.sort_values("date")
df["cost_ma7"] = df["cost"].rolling(window=7, min_periods=1).mean()
```

| 参数 | 作用 |
|---|---|
| `window` | 窗口大小，7 表示最近 7 行 |
| `min_periods` | 窗口内至少有多少个非空值才计算，默认等于 window |
| `center` | 是否居中（默认 False，窗口右对齐） |

**`min_periods=1` 的作用**：默认情况下前 6 行凑不满 7 个值，结果全是 NaN。设成 1 后，前几行按实际拥有的数据计算，曲线从第一天就有值。

**分组滚动**：

```python
df["ma7"] = (df.groupby("channel")["cost"]
               .rolling(7, min_periods=1).mean()
               .reset_index(level=0, drop=True))
```

`reset_index(level=0, drop=True)` 是为了把 groupby 产生的多余索引层级丢掉，让结果能直接赋回原表。这是 pandas 里最容易写错的一处。

**rolling 与 expanding 的区别**：`rolling(7)` 窗口长度固定、会滑动；`expanding()` 窗口从第一行不断扩大到当前行，等价于"累计"。累计求和可以用 `cumsum()`，累计均值用 `expanding().mean()`。

### 4.6 rank：排名

**是什么**：给一列值排名，返回名次。

```python
df["rn"] = df["cost"].rank(ascending=False, method="min")
df["rn"] = df.groupby("channel")["cost"].rank(ascending=False, method="first")
```

| 参数 | 取值 | 含义 |
|---|---|---|
| `ascending` | `True`（默认）/ `False` | 升序排名 / 降序排名（大值排第 1） |
| `method` | `average`（默认） | 并列取平均名次（1.5, 1.5, 3） |
| | `min` | 并列取最小名次（1, 1, 3），等价 SQL 的 `RANK` |
| | `max` | 并列取最大名次（2, 2, 3） |
| | `first` | 按出现顺序给唯一名次（1, 2, 3），等价 SQL 的 `ROW_NUMBER` |
| | `dense` | 密集排名（1, 1, 2），等价 SQL 的 `DENSE_RANK` |
| `na_option` | `keep` / `top` / `bottom` | 缺失值的处理方式 |

**取 Top N 的两种写法**：

```python
# 写法一：排序 + head
df.sort_values("cost", ascending=False).head(3)

# 写法二：rank + 筛选（能保留并列，也能分组）
df[df["cost"].rank(ascending=False, method="first") <= 3]

# 分组 Top N
df["rn"] = df.groupby("channel")["cost"].rank(ascending=False, method="first")
df[df["rn"] <= 3]
```

**与 SQL 的对应**：`rank(method="first")` ↔ `ROW_NUMBER()`；`method="min"` ↔ `RANK()`；`method="dense"` ↔ `DENSE_RANK()`。pandas 没有 `PARTITION BY`，用 `groupby(...)[列].rank(...)` 达到同样效果。

### 4.7 六个工具的配合：一个完整的指标模块

实际代码里，这六个工具通常按下面的顺序串起来：

| 步骤 | 工具 | 作用 |
|---|---|---|
| 1 | 新增列 | 给每行算 CTR / CVR / CPC / CPA |
| 2 | `groupby + agg` | 按渠道 / 日期汇总曝光、点击、转化、花费 |
| 3 | `np.where` | 汇总后再算比率，并做除零保护 |
| 4 | `sort_values` + `shift` | 算环比 |
| 5 | `rolling` | 算 7 日移动平均，平滑波动 |
| 6 | `rank` | 按指标排名取 Top N |

工程上通常拆成 7 个函数，一个函数只做一件事：

| 函数 | 职责 |
|---|---|
| `calculate_basic_metrics()` | 给每行添加 ctr / cvr / cpc / cpa 列 |
| `calculate_overall_metrics()` | 整体汇总指标（先加总再相除） |
| `group_by_dimension()` | 按维度（渠道 / 计划）分组汇总并计算指标 |
| `group_by_date()` | 按日期分组 |
| `calculate_daily_change()` | 计算环比 |
| `calculate_moving_average()` | 计算移动平均 |
| `rank_by_metric()` | 按指标排序取 Top N |

**三条不可违背的计算原则**：

1. **整体指标 = 先加总再相除**，不是每行指标的平均。CTR 是如此，CVR、CPC、CPA、ROI 都是如此；
2. **除法必须做除零保护**，用 `np.where` 或把分母 0 替换成 NaN；
3. **算环比之前必须按日期排序**，否则 `shift(1)` 取到的是随机行。

---

## 五、缺失值与数据类型

### 5.1 pandas 的缺失值表示

| 场景 | 表示 | 判断 |
|---|---|---|
| 数值列 | `NaN`（float） | `pd.isna(x)` / `df["col"].isna()` |
| 可空整数 | `pd.NA` | 同上 |
| 日期列 | `NaT` | 同上 |
| 对象列 | `None` 或 `NaN` | 同上 |

`pd.isna()` 统一处理以上所有情况，是唯一推荐的判断方式。`x != x` 这种老技巧只对 NaN 有效，对 `pd.NA` 会返回 `pd.NA`。

### 5.2 三种整数类型的区别

| 类型 | 能表示空值 | 用途 |
|---|---|---|
| `int64` | 否 | 确定无缺失的整数 |
| `float64` | 是（NaN） | 有缺失的整数被迫升为 float，会出现 `1.0` 这种显示 |
| `Int64` | 是（pd.NA） | pandas 的可空整数，既保持整数语义又能有空值 |

这就是 `read_csv` 里 `dtype={"impressions": "Int64"}` 的原因：用 `int` 遇到空值报错，用默认推断会变成 float，用 `Int64` 两全。

### 5.3 类型检查与转换

```python
df.dtypes                                   # 查看所有列类型
df["cost"].dtype                            # 单列类型
pd.api.types.is_numeric_dtype(df["cost"])   # 是否数值型

df["cost"] = pd.to_numeric(df["cost"], errors="coerce")
df["date"] = pd.to_datetime(df["date"], errors="coerce")
df["flag"] = df["flag"].astype(bool)
df["category"] = df["channel"].astype("category")   # 低基数字符串列，省内存
```

**`category` 类型的价值**：当一列只有少量重复值（渠道、省份、状态）时，转成 category 后内存占用可以降到原来的几十分之一，groupby 也更快。

---

## 六、常见报错与易错点

### 6.1 不要用 inplace=True

```python
# 不推荐
df["math"].fillna(df["math"].mean(), inplace=True)
df.dropna(inplace=True)

# 推荐：直接赋值
df["math"] = df["math"].fillna(df["math"].mean())
df = df.dropna()
df = df.drop_duplicates()
```

原因有三：

1. 对单列（Series）用 `inplace=True` 在新版 pandas 会报警告，且可能不生效（操作的是切片副本）；
2. `inplace` 阻断链式调用；
3. pandas 3.0 起 `inplace` 逐步废弃，写 `df = df.method(...)` 才是长期可用的写法。

### 6.2 取多列必须两层方括号

```python
df[["name", "math"]]   # 正确，返回 DataFrame
df["name", "math"]     # 错误，KeyError
```

### 6.3 多条件必须加括号，且用 & / |

```python
df[(df["math"] > 80) & (df["english"] > 80)]     # 正确
df[df["math"] > 80 & df["english"] > 80]         # 错误：优先级问题
df[(df["math"] > 80) and (df["english"] > 80)]   # 错误：and 不能用于 Series
```

### 6.4 等于用 ==

```python
df[df["class"] == "一班"]   # 正确
df[df["class"] = "一班"]    # SyntaxError
```

### 6.5 count 与 sum 混用

`count()` 数非空个数，任何类型都能用；`sum()` 求和，只对数值列有意义。对字符串列 `sum` 会做拼接，结果通常不是你想要的。

### 6.6 shape / columns / dtypes 是属性

```python
df.shape      # 正确，(行数, 列数)
df.shape()    # 错误：'tuple' object is not callable
```

判断方法：看文档里是 property 还是 method。属性不加括号，方法加括号。

### 6.7 排序后忘记赋值

```python
df.sort_values("cost")            # 结果丢了
df = df.sort_values("cost")       # 正确
df.sort_values("cost", inplace=True)   # 可用，但不推荐
```

### 6.8 SettingWithCopyWarning

```python
# 触发警告：sub 是 df 的切片，赋值可能不写回原表
sub = df[df["cost"] > 100]
sub["ctr"] = 0

# 正确：显式 copy
sub = df[df["cost"] > 100].copy()
sub["ctr"] = 0
```

这个警告的本质是"你不确定在改原表还是改副本"。凡是准备对筛选结果做赋值，先 `.copy()`。

### 6.9 其他高频问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `KeyError: 'xxx'` | 列名拼错、有空格、大小写不一致 | `df.columns.tolist()` 核对 |
| `ValueError: cannot convert float NaN to integer` | 有缺失还转 int | 先 fillna，或用 `"Int64"` |
| 分组结果是多层索引，取列麻烦 | 默认 `as_index=True` | `as_index=False` 或 `reset_index()` |
| merge 后行数变多 | 关联键重复 | 先去重或改用唯一键 |
| 中文列名报错 | 某些操作对非 ASCII 列名兼容差 | 统一改成英文列名 |
| 内存占用过大 | 全量读入 + 默认 float64 | `usecols` / `chunksize` / 降精度 / `category` |

---

## 七、pandas vs SQL：什么时候用哪个

### 7.1 本质区别

| | pandas | SQL |
|---|---|---|
| 数据在哪 | **内存**里的 DataFrame | **数据库 / 硬盘**里的表 |
| 类比 | 计算器 + 草稿纸，算得快但纸有限 | 图书馆查询系统，书再多也能快速找到 |
| 上限 | 受单机内存限制 | 受集群规模限制，几亿行也能查 |

### 7.2 pandas 擅长的五件事

1. **数据清洗**（最擅长）：缺失值填充、异常值处理、类型转换、去重、字段名标准化；
2. **复杂数据转换**：透视表 `pivot`、长宽表转换 `melt`、`apply` 自定义函数、多列衍生指标；
3. **小数据量灵活探索**：几万到几十万行，反复改、反复看；
4. **与 Python 生态集成**：matplotlib 可视化、sklearn 建模、LangChain 调模型；
5. **读写各种格式**：CSV、Excel、JSON、Parquet、数据库。

### 7.3 pandas 不擅长的四件事

1. **大数据量**（致命弱点）：全部读进内存，几百万行就可能 OOM；
2. **多表 JOIN**：`merge` 慢、占内存，多表关联写起来也繁琐；
3. **复杂窗口函数 / 多层子查询**：写起来绕，一个逻辑要拆很多步；
4. **持久化、多人共享、并发访问**：数据在内存里，程序一关就没了。

### 7.4 SQL 擅长的五件事

1. **大数据量查询**（最擅长）：有索引和执行计划优化，亿级数据也能秒级返回；
2. **多表关联**：数据库的看家本领，3~10 张表关联都很轻松；
3. **复杂查询**：窗口函数、子查询、CTE，表达清晰；
4. **持久化存储**：数据在硬盘，关机不丢；
5. **多人共享 / 并发 / 权限管理**：多个同事同时查，互不影响。

### 7.5 SQL 不擅长的三件事

1. **复杂数据清洗**：自定义清洗逻辑写起来麻烦，调试困难；
2. **灵活探索性分析**：频繁改查询不如 pandas 顺手；
3. **可视化 / 机器学习**：得把结果导出来交给别的工具。

### 7.6 决策流程

```
第 1 步：数据量多大？
  ├─ 小（< 100 万行）→ 可以考虑 pandas
  └─ 大（> 100 万行）→ 优先用 SQL

第 2 步：要做什么？
  ├─ 清洗 / 复杂转换 / 探索 / 可视化 / 建模 → pandas
  ├─ 多表关联 / 复杂窗口函数 / 大数据查询 / 持久化 → SQL
  └─ 建库建表 / 权限管理 → 必须用 SQL
```

### 7.7 实际工作流（99% 的场景是混用）

```
1. 原始数据存在数据库（数据量大）
2. 用 SQL 初步筛选聚合（几千万行 → 几万行）
3. 结果读进 pandas，做精细清洗 / 转换 / 探索
4. pandas + matplotlib 可视化，或 + sklearn 建模
5. 结果存回数据库，或导出报表
```

**一句话**：SQL 负责搬数据、筛数据、聚合（重活），pandas 负责精细加工、分析、可视化（细活）。

### 7.8 SQL 与 pandas 语法对照

| SQL | pandas |
|---|---|
| `SELECT 列` | `df[["列"]]` |
| `WHERE 条件` | `df[df["列"] 条件]` |
| `ORDER BY` | `df.sort_values()` |
| `LIMIT n` | `df.head(n)` |
| `GROUP BY + SUM` | `df.groupby("列")["列"].sum()` |
| `HAVING` | `df.groupby().filter()`，或聚合后再筛 |
| `INNER JOIN` | `pd.merge(how="inner")` |
| `LEFT JOIN` | `pd.merge(how="left")` |
| `ROW_NUMBER() OVER` | `df.groupby()["列"].rank(method="first")` |
| `RANK() OVER` | `df.groupby()["列"].rank(method="min")` |
| `SUM() OVER (PARTITION BY)` | `df.groupby()["列"].transform("sum")` |
| `SUM() OVER (ORDER BY)` | `df.groupby()["列"].cumsum()` |
| `LAG() OVER` | `df.groupby()["列"].shift(1)` |
| `AVG() OVER (ROWS BETWEEN)` | `df.groupby()["列"].rolling(n).mean()` |
| `COUNT(DISTINCT 列)` | `df["列"].nunique()` |

**`transform` 与 `agg` 的区别**：`agg` 把每组压成一行，`transform` 把每组的计算结果**广播回原表的每一行**，返回与原表等长的 Series。这是 pandas 里实现"窗口聚合"的标准手段：

```python
df["channel_total"] = df.groupby("channel")["cost"].transform("sum")
df["share"] = df["cost"] / df["channel_total"]     # 每行占本渠道的比例
```

---

## 八、练习清单

两组由浅入深的题目，覆盖本篇全部操作。建议先手写再看答案，重点在"能不能一次写对"，而不是"看懂"。

**第一组：基础操作**（学生成绩表，10 行，列：`name`、`class`、`gender`、`math`、`english`、`chinese`）

1. 查看数据：前 5 行、形状、数据类型、统计信息
2. 取列：取 `name` 单列、取 `name` 和 `math` 两列、取所有成绩列
3. 条件筛选：数学 > 80、一班学生、数学 > 80 且英语 > 80、数学 > 90 或语文 > 90
4. 排序：按数学降序、按班级 + 数学降序、数学最高的 3 个学生
5. 新增列：总分 `total`、平均分 `average`（保留 1 位）、等级 `grade`（用 `np.where` 或 `apply`）
6. 分组聚合：各班数学平均分、各班各科平均分、各班人数 + 数学平均/最高/最低（命名聚合）、男女生各科平均分

**第二组：进阶清洗**（成绩表 11 行，含 1 行重复、3 个缺失值；另有学生信息表，含学号、专业、入学日期）

1. 查看缺失值：`df.isna().sum()`
2. 填充缺失值：数学缺失用平均分填充
3. 删除缺失值：删除英语缺失的行
4. 删除重复行：查看重复行 + 删除重复行
5. 类型转换：入学日期转成日期类型
6. 合并两张表：按 `name` 做 `merge`
7. 分组计算及格率：各班数学及格人数 / 总人数
8. 找各班数学最高：先排序再 `groupby().first()`，或用 `rank`
9. `apply` 自定义函数：新增 `level` 列（学霸 / 优秀 / 良好 / 加油）
10. 综合题：合并 → 筛选计算机专业 → 按数学降序 → 取指定列（链式调用）

---

## 九、面试速查

- DataFrame 和 Series 的区别 → DataFrame 是二维表，Series 是一维带索引的列。
- `read_csv` 最关键的几个参数 → `dtype`（防类型推断错误）、`parse_dates`（日期可运算）、`encoding`（防乱码）、`usecols` / `chunksize`（控内存）。
- 为什么要指定 `dtype` → 自动推断会让 ID 丢前导零、含脏值的数值列变成 object、大文件内存浪费。
- 列里有空值为什么不能指定 `int` → numpy 的 int 无法表示空值，要用可空整数 `Int64`。
- 中文乱码怎么解决 → 依次尝试 `gbk`、`gb18030`、`utf-8-sig`。
- 大文件读不进来怎么办 → `usecols` 只读需要的列，`chunksize` 分块处理，或换 Parquet / Spark。
- 多条件筛选为什么报错 → 用了 `and` / `or`，必须改成 `&` / `|`，且每个条件加括号。
- `count` 和 `size` 的区别 → `count` 数非空值，`size` 数行数（含空值）。
- `agg` 和 `transform` 的区别 → `agg` 每组一行，`transform` 结果广播回原表每一行。
- `agg` 和 `apply` 的区别 → `agg` 每组返回标量、结果规整；`apply` 可返回任意形状、更灵活但更慢。
- `shift` 用之前必须做什么 → 按时间排序，否则取到的是随机行。
- `rolling` 的 `min_periods` 有什么用 → 控制窗口开头凑不满时是否计算，设 1 可避免前几行全是 NaN。
- `rank` 的 `method` 怎么选 → `first` 对应 ROW_NUMBER，`min` 对应 RANK，`dense` 对应 DENSE_RANK。
- pandas 里怎么实现分组 Top N → `groupby(...)[列].rank(method="first")` 后筛选。
- 环比怎么算 → 排序后 `shift(1)` 取上一期，`(本期 - 上期) / 上期`；分组环比先 `groupby` 再 `shift`。
- 为什么不用 `inplace=True` → 新版逐步废弃、阻断链式调用、对切片可能不生效。
- `SettingWithCopyWarning` 怎么消除 → 对筛选结果显式 `.copy()` 后再赋值。
- merge 后行数变多是什么原因 → 关联键在一方不唯一，产生了笛卡尔组合，与 SQL 的一对多放大同理。
- 什么时候该用 SQL 而不是 pandas → 数据超过百万行、需要多表 JOIN、需要复杂窗口函数、需要持久化和并发访问。
- 实际工作中两者怎么配合 → SQL 先做大表筛选聚合，结果落到 pandas 做精细清洗、分析和可视化。
