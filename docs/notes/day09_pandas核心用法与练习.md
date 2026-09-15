# Day9：Pandas 核心用法与练习

## 一、Pandas 是什么

Pandas 是 Python 里专门用来做数据处理和分析的库，可以理解成"Python 里的 Excel"。

两个核心数据结构：
- **DataFrame**：二维表格（有行有列，像 Excel 的一张表）
- **Series**：一维数据（像 Excel 的一列或一行）

---

## 二、Pandas 最常用的 10 个操作

| 排名 | 操作 | 语法 | 用途 |
|---|---|---|---|
| 1 | 读取数据 | `pd.read_csv("文件.csv")` | 从CSV读取数据 |
| 2 | 查看数据 | `df.head()` / `df.info()` / `df.describe()` / `df.shape` | 了解数据基本情况 |
| 3 | 取列 | `df["列名"]` / `df[["列1","列2"]]` | 取需要的列 |
| 4 | 条件筛选 | `df[df["列"] > 值]` / 多条件 `&`/`\|` | 筛选符合条件的行 |
| 5 | 排序 | `df.sort_values(by="列", ascending=False)` | 按某列排序 |
| 6 | 新增列 | `df["新列"] = ...` / `np.where()` / `apply()` | 计算新字段 |
| 7 | 分组聚合 | `df.groupby("列").agg(...)` | 分组统计（最核心！） |
| 8 | 缺失值处理 | `df.isna().sum()` / `df.fillna()` / `df.dropna()` | 清洗数据 |
| 9 | 合并 | `pd.merge()` / `pd.concat()` | 合并多张表 |
| 10 | 保存数据 | `df.to_csv("文件名.csv", index=False)` | 保存结果到文件 |

---

## 三、核心操作详解

### 1. 查看数据

```python
df.head()          # 前5行（df.head(10) 前10行）
df.tail()          # 后5行
df.shape           # (行数, 列数)，属性不加括号！
df.columns         # 所有列名
df.dtypes          # 每列数据类型
df.info()          # 详细信息（行数、列数、非空值、类型、内存）
df.describe()      # 数值列统计（计数、均值、标准差、最小/最大值、百分位数）
df["列"].unique()  # 某列有哪些不同值
df["列"].value_counts()  # 每个值出现多少次
```

### 2. 取列

```python
df["name"]              # 取单列，返回 Series（一层方括号）
df[["name", "math"]]    # 取多列，返回 DataFrame（两层方括号！）
```

> **易错点：** 取多列必须是两层方括号 `df[["列1", "列2"]]`，不能写成一层。

### 3. 条件筛选

```python
df[df["math"] > 80]                          # 单条件
df[(df["math"] > 80) & (df["english"] > 80)]  # 且（多条件用 &，每个条件加括号）
df[(df["math"] > 90) | (df["chinese"] > 90)]  # 或（多条件用 |）
df[df["class"] == "一班"]                     # 等于（用 == 两个等号）
df[df["class"].isin(["一班", "二班"])]         # 在某个列表里
```

> **易错点：** 多条件用 `&` / `|`，不能用 `and` / `or`；每个条件必须用括号包起来。

### 4. 排序

```python
df.sort_values(by="math", ascending=False)   # 按数学降序（从大到小）
df.sort_values(by="math", ascending=True)    # 按数学升序（从小到大，默认）
df.sort_values(by=["class", "math"], ascending=[True, False])  # 多列排序
df.sort_values(by="math", ascending=False).head(3)  # 数学最高的3个（Top N）
```

### 5. 新增列

```python
# 方法1：直接赋值（最简单）
df["total"] = df["math"] + df["english"] + df["chinese"]
df["average"] = (df["total"] / 3).round(1)  # 保留1位小数

# 方法2：np.where（简单条件判断，类似Excel的IF）
df["grade"] = np.where(df["average"] >= 90, "优秀",
              np.where(df["average"] >= 80, "良好",
              np.where(df["average"] >= 70, "中等", "及格")))

# 方法3：apply + 自定义函数（复杂逻辑）
def get_level(total):
    if total >= 270:
        return "学霸"
    elif total >= 240:
        return "优秀"
    elif total >= 210:
        return "良好"
    else:
        return "加油"

df["level"] = df["total"].apply(get_level)
```

### 6. 分组聚合（groupby，最核心！）

```python
# 基本语法：df.groupby("分组列")["要统计的列"].聚合函数()
df.groupby("class")["math"].mean()   # 按班级分组，计算数学平均分

# 多列聚合
df.groupby("class")[["math", "english", "chinese"]].mean()

# 命名聚合（最灵活，最常用！）
df.groupby("class").agg(
    人数=("name", "count"),
    数学平均分=("math", "mean"),
    数学最高分=("math", "max"),
    数学最低分=("math", "min"),
    数学及格人数=("math", lambda x: (x >= 60).sum()),
)

# 分组列还是普通列（推荐加 as_index=False）
df.groupby("class", as_index=False)["math"].mean()
```

**常用聚合函数：** `mean()` 平均、`sum()` 求和、`count()` 计数、`max()` 最大、`min()` 最小、`std()` 标准差、`median()` 中位数、`nunique()` 唯一值数量。

> **count vs sum：** count 是统计数量（有多少个），sum 是对数值求和（加起来是多少）。统计人数用 count，计算总分用 sum。

### 7. 缺失值处理

```python
df.isna().sum()              # 每列有多少缺失值
df["math"].isna().sum()      # math列有多少缺失值

df.dropna()                   # 删除任何包含缺失值的行
df.dropna(subset=["math"])    # 只删除math列有缺失值的行

df.fillna(0)                  # 所有缺失值填充为0
df["math"].fillna(df["math"].mean())  # math列缺失值用平均分填充
df.fillna({"math": 0, "english": 60})  # 不同列用不同值填充
```

### 8. 重复值处理

```python
df[df.duplicated()]           # 查看重复行
df.drop_duplicates()          # 删除重复行
df.drop_duplicates(subset=["name"], keep="first")  # 按某列去重，保留第一个
```

### 9. 类型转换

```python
df["enroll_date"] = pd.to_datetime(df["enroll_date"])  # 转成日期类型
df["math"] = df["math"].astype(int)   # 转成整数
df["math"] = df["math"].astype(float) # 转成浮点数
df["name"] = df["name"].astype(str)   # 转成字符串
```

### 10. 合并数据

```python
# merge（类似SQL的JOIN，按某列合并两张表）
pd.merge(df1, df2, on="name", how="inner")  # 内连接（只保留两边都有的）
pd.merge(df1, df2, on="name", how="left")   # 左连接（保留左边所有）
pd.merge(df1, df2, on="name", how="outer")  # 全连接（保留两边所有）

# concat（拼接）
pd.concat([df1, df2], axis=0)   # 上下拼接（行拼接，列名要一样）
pd.concat([df1, df2], axis=1)   # 左右拼接（列拼接）
```

### 11. 链式调用（把多个操作连起来写）

```python
result = (
    df2[df2["major"] == "计算机"]                    # 第1步：筛选
    .sort_values(by="math", ascending=False)         # 第2步：排序
    [["name", "class", "major", "math", "english"]]  # 第3步：取列
)
```

---

## 四、练习题（第1组：基础6题）

### 练习数据
学生成绩表，10个学生，列：name、class、gender、math、english、chinese。

### 题目
1. **查看数据**：前5行、形状、数据类型、统计信息
2. **取列**：取name列、取name和math两列、取所有成绩列
3. **条件筛选**：数学>80、一班学生、数学>80且英语>80、数学>90或语文>90
4. **排序**：按数学降序、按班级+数学降序、数学最高的3个学生
5. **新增列**：总分total、平均分average（保留1位）、等级grade（用apply或np.where）
6. **分组聚合**：各班数学平均分、各班各科平均分、各班人数+数学平均/最高/最低（命名聚合）、男女生各科平均分

---

## 五、练习题（第2组：进阶10题）

### 练习数据
- 成绩表 df_scores：11行（含1行重复），3个缺失值
- 学生信息表 df_info：学号、专业、入学日期

### 题目
1. **查看缺失值**：`df.isna().sum()`
2. **填充缺失值**：数学缺失值用平均分填充
3. **删除缺失值**：删除英语缺失的行
4. **删除重复行**：查看重复行 + 删除重复行
5. **类型转换**：入学日期转成日期类型
6. **合并两张表**：按name合并（merge）
7. **分组计算及格率**：各班数学及格率（及格人数/总人数）
8. **找各班数学最高**：先排序再groupby first
9. **apply自定义函数**：新增level列（学霸/优秀/良好/加油）
10. **综合题**：合并→筛选计算机专业→按数学降序→取指定列（链式调用）

---

## 六、常见错误与注意事项

### 1. `inplace=True` 的问题（pandas 3.0+）

**不推荐：**
```python
df["math"].fillna(df["math"].mean(), inplace=True)  # 对单列用inplace会警告
df.dropna(inplace=True)
```

**推荐（直接赋值）：**
```python
df["math"] = df["math"].fillna(df["math"].mean())
df = df.dropna()
df = df.drop_duplicates()
```

> pandas 3.0+ 尽量不要用 `inplace=True`，都改成 `df = df.方法(...)` 的直接赋值方式。

### 2. 取多列必须两层方括号

```python
df[["name", "math"]]   # ✅ 正确（两层方括号）
df["name", "math"]     # ❌ 错误（一层方括号会报错）
```

### 3. 多条件必须用括号

```python
df[(df["math"] > 80) & (df["english"] > 80)]  # ✅ 正确（每个条件加括号）
df[df["math"] > 80 & df["english"] > 80]        # ❌ 错误（没有括号会报错）
```

### 4. 多条件用 `&` / `|`，不能用 `and` / `or`

```python
df[(df["math"] > 80) & (df["english"] > 80)]  # ✅ 正确
df[(df["math"] > 80) and (df["english"] > 80)]  # ❌ 错误
```

### 5. 等于用 `==`，不是 `=`

```python
df[df["class"] == "一班"]   # ✅ 正确（两个等号是判断）
df[df["class"] = "一班"]    # ❌ 错误（一个等号是赋值）
```

### 6. count vs sum

- `count()`：统计数量（有多少个非空值），任何类型都能用
- `sum()`：对数值求和，只能用于数值列
- 统计人数用 `count`，计算总分用 `sum`

### 7. shape / columns / dtypes 是属性，不加括号

```python
df.shape      # ✅ 正确（属性，不加括号）
df.shape()    # ❌ 错误（加括号会报错）
```

### 8. 排序后找 Top N

```python
df.sort_values(by="math", ascending=False).head(3)  # 数学最高的3个
```

---

## 七、学习建议

1. **pandas 是数据工程的核心**，80%的时间都在用，必须练熟
2. **不要死记硬背**，用的时候查就行，常用的自然就记住了
3. **多做练习**，找几个CSV数据集（Kaggle、天池），自己练数据清洗、分组统计
4. **链式调用很常用**，把筛选、排序、取列连起来写，代码更简洁
5. **遇到报错先看错误信息**，大部分报错都是语法问题（括号、引号、列名拼写）
