# Day2：pandas read_csv 参数详解

> 学习日期：2026-09-10
> 核心内容：`pd.read_csv()` 的常用参数逐个拆解

---

## 一、read_csv 是什么？

`pd.read_csv()` 是 pandas 库的函数，作用是**把 CSV 文件读进来，变成 Python 里的表格对象（DataFrame）**。

CSV 文件本质是纯文本，每行一条记录，字段用逗号隔开：
```
date,channel,campaign,impressions,clicks,cost
2026-08-01,Facebook,夏季促销,10000,200,500.0
```

最简单用法：
```python
import pandas as pd
df = pd.read_csv('data.csv')
```

---

## 二、参数逐个拆解

### 1. filepath_or_buffer（文件路径）

- **是什么**：CSV 文件的位置，可以是相对路径、绝对路径、URL
- **为什么用**：必填参数，不告诉 pandas 文件在哪就没法读
- **工程规范**：不要写死绝对路径，用项目根目录变量拼接

```python
# 不好：写死路径
df = pd.read_csv('C:/Users/Mia/project/data.csv')

# 好：用变量拼接
df = pd.read_csv(PROJECT_ROOT / 'data' / 'raw' / 'data.csv')
```

---

### 2. sep（分隔符）

- **是什么**：每列之间用什么字符隔开，默认逗号 `,`
- **为什么用**：实际中会遇到制表符 `\t`、分号 `;`、竖线 `|` 等分隔符
- **不用会怎样**：分隔符不对的话，一整行会被当成一列，数据全乱

```python
df = pd.read_csv('data.csv')              # 逗号分隔（默认）
df = pd.read_csv('data.tsv', sep='\t')    # 制表符分隔
df = pd.read_csv('data.csv', sep=';')     # 分号分隔
```

---

### 3. header（表头在哪一行）

- **是什么**：CSV 第几行是列名，默认第0行（第一行）
- **为什么用**：有些文件没有表头，或前几行是说明文字
- **常用值**：
  - `header=0`：第一行是表头（默认）
  - `header=None`：没有表头，第一行就是数据（列名变成0,1,2...）
  - `header=2`：第3行是表头（前两行是说明）

```python
df = pd.read_csv('data.csv')               # 第一行是表头（默认）
df = pd.read_csv('data.csv', header=None)  # 没有表头
```

---

### 4. names（手动指定列名）

- **是什么**：手动给列起名字，覆盖 CSV 里的表头
- **为什么用**：CSV 没有表头、列名不规范、想改成中文名

```python
# 没有表头，手动指定列名
df = pd.read_csv('data.csv', header=None, 
                 names=['日期', '渠道', '计划', '曝光', '点击', '花费'])
```

---

### 5. dtype（指定每列数据类型）

- **是什么**：强制指定每一列的数据类型（字符串、整数、浮点数等）
- **为什么用**：pandas 自动推断类型会出错
  - ID 列全是数字会被当成 int，前导零丢失
  - 数字列里有非数字会被当成字符串，没法计算
  - 内存浪费

```python
df = pd.read_csv('data.csv', dtype={
    'campaign_id': str,       # ID强制为字符串，防止前导零丢失
    'impressions': int,        # 曝光为整数
    'cost': float,             # 花费为浮点数
})
```

> **注意**：如果列里有空值，不能直接指定 `int`（int 不能表示空值），需要用 `Int64`（pandas的可空整数类型），或者先读进来再转换。

---

### 6. parse_dates（解析日期列）

- **是什么**：告诉 pandas 哪些列是日期，自动把字符串解析成日期类型
- **为什么用**：不解析的话日期是字符串，没法做日期操作（排序、提取月份、按日期分组）

```python
# 不解析：date列是字符串，没法做日期操作
df = pd.read_csv('data.csv')
df['date'].dt.month  # 报错！

# 解析：date列是datetime类型
df = pd.read_csv('data.csv', parse_dates=['date'])
df['date'].dt.month      # 提取月份
df['date'].dt.dayofweek  # 提取星期几
```

---

### 7. na_values（自定义缺失值）

- **是什么**：指定哪些值应该被当成缺失值（NaN）
- **为什么用**：默认 pandas 只把空字符串、`NA`、`NULL` 等当成缺失值，但实际中会遇到各种表示"没有"的写法

```python
df = pd.read_csv('data.csv', na_values=['', 'N/A', 'null', '-', '无', 'NA'])
```

---

### 8. encoding（文件编码）

- **是什么**：CSV 文件的字符编码，默认 `utf-8`
- **为什么用**：Windows 上 Excel 导出的 CSV 经常是 `gbk` 编码，不指定的话中文会乱码

```python
df = pd.read_csv('data.csv', encoding='utf-8')   # UTF-8编码（默认）
df = pd.read_csv('data.csv', encoding='gbk')      # GBK编码（Windows Excel导出）
```

> **遇到乱码怎么办**：先试 `utf-8`，报错或乱码就试 `gbk`，再不行试 `gb2312`、`gb18030`。

---

### 9. nrows（只读前N行）

- **是什么**：只读取文件的前 N 行
- **为什么用**：文件很大时，先读前几行看看数据结构，不用全读进来

```python
df = pd.read_csv('big_data.csv', nrows=100)  # 只读前100行
```

---

### 10. usecols（只读指定列）

- **是什么**：只读取需要的列，其他列跳过
- **为什么用**：文件有几十列但你只需要几列，只读需要的列能省内存、加快速度

```python
# 只读这3列
df = pd.read_csv('data.csv', usecols=['date', 'channel', 'cost'])
```

---

### 11. skiprows（跳过指定行）

- **是什么**：跳过文件的前几行，或跳过特定行
- **为什么用**：文件开头有说明文字、标题行、空行等不需要的内容

```python
df = pd.read_csv('data.csv', skiprows=2)       # 跳过前2行
df = pd.read_csv('data.csv', skiprows=[0, 2, 5])  # 跳过第1、3、6行（从0开始数）
```

---

### 12. chunksize（分块读取大文件）

- **是什么**：把大文件分成小块读取，每次读 chunksize 行
- **为什么用**：文件太大（几个GB）内存装不下，分块读就能处理

```python
# 每次读10000行，循环处理
chunk_iter = pd.read_csv('big_data.csv', chunksize=10000)
for chunk in chunk_iter:
    # 处理这一块数据
    process(chunk)
```

---

## 三、工程里的完整写法

```python
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

df = pd.read_csv(
    PROJECT_ROOT / 'data' / 'raw' / 'ad_data.csv',
    sep=',',
    header=0,
    dtype={
        'campaign_id': str,
        'impressions': 'Int64',  # 可空整数
        'clicks': 'Int64',
        'cost': float,
    },
    parse_dates=['date'],
    na_values=['', 'N/A', 'null', '-'],
    encoding='utf-8',
)
```

---

## 四、常见问题

| 问题 | 原因 | 解决方法 |
|---|---|---|
| 中文乱码 | 编码不对 | 试 `encoding='gbk'` 或 `encoding='gb18030'` |
| 前导零丢失 | ID列被当成int | `dtype={'id': str}` |
| 数字列没法计算 | 列里有非数字，被当成object | 用 `pd.to_numeric(errors='coerce')` 转换 |
| 日期列是字符串 | 没解析日期 | `parse_dates=['date']` |
| 内存不够 | 文件太大 | 用 `usecols` 只读需要的列，或 `chunksize` 分块读 |
| 第一行数据被当成表头 | 用了names但没设header | 配合 `header=0` 或 `header=None` |

---

## 五、今天的收获

- 理解了 `read_csv` 的12个常用参数
- 知道了每个参数"是什么、为什么用、不用会怎样"
- 掌握了工程里的完整写法
- 以后遇到任何CSV读取问题，都能从这12个参数里找到解决方案
