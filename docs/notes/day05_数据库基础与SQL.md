# Day5：数据库基础与 SQL

## 一、数据库基本概念

### 关系型数据库
- 数据库 = 专门存储管理数据的软件（超级加强版Excel）
- 我们用 MySQL 8.0.46（电脑已安装，服务名 mysql180，正在运行）

### 核心概念
- **表（Table）**：存数据的地方，有行有列
- **行（Row）**：一条记录
- **列（Column）**：一个字段
- **主键（Primary Key）**：每行唯一标识（如id），不重复
- **外键（Foreign Key）**：引用另一张表主键的列，建立表间关系
- **索引（Index）**：像书的目录，加快查询速度；不是越多越好（插入变慢、占空间）

## 二、SQL 基础

### SELECT 基本语法
```sql
SELECT 列名
FROM 表名
WHERE 条件
GROUP BY 分组列
HAVING 分组后条件
ORDER BY 排序列 DESC
LIMIT 数量;
```

### 执行顺序（重要！）
```
书写：SELECT → FROM → WHERE → GROUP BY → HAVING → ORDER BY → LIMIT
执行：FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY → LIMIT
```
- WHERE 在 GROUP BY 前，不能用聚合函数
- HAVING 在 GROUP BY 后，可以用聚合函数
- SELECT 别名 WHERE 里不能用，ORDER BY 里可以用

### 常用条件运算符
- `=` `<>`/`!=` `>` `<` `>=` `<=`
- `AND` `OR`
- `IN (...)` `NOT IN (...)`
- `BETWEEN ... AND ...`
- `LIKE '%xxx%'`（模糊匹配）
- `IS NULL` `IS NOT NULL`（判断空值必须用IS，不能用=NULL）

### 聚合函数
- `COUNT(*)` 行数、`COUNT(列)` 非空数量
- `SUM()` `AVG()` `MAX()` `MIN()`
- 用 `AS` 给结果列起别名

### GROUP BY 分组规则
- SELECT 里的列要么在 GROUP BY 里，要么用聚合函数
- 多列分组：`GROUP BY channel, date`

### WHERE vs HAVING
- WHERE：分组前筛选行，不能用聚合函数
- HAVING：分组后筛选分组结果，可以用聚合函数

## 三、JOIN 多表连接

### 四种 JOIN
| 类型 | 作用 | 表A独有 | 交集 | 表B独有 |
|---|---|---|---|---|
| INNER JOIN | 只取两边都有的 | ❌ | ✅ | ❌ |
| LEFT JOIN | 左边全保留 | ✅ | ✅ | ❌ |
| RIGHT JOIN | 右边全保留 | ❌ | ✅ | ✅ |
| FULL JOIN | 两边都全保留 | ✅ | ✅ | ✅ |

### 语法
```sql
SELECT a.id, b.name, a.cost
FROM ad_data a
INNER JOIN campaigns b ON a.campaign_id = b.id;
```
- 表别名：`a`、`b` 简化引用
- 同名列必须用 `表名.列名` 区分

### LEFT JOIN 时 ON vs WHERE（面试常考！）
- ON 里筛选右表：连接前筛选，左表不匹配的行仍保留（右表列NULL）
- WHERE 里筛选：连接后筛选整个结果，会把NULL行也过滤掉，相当于变INNER JOIN
- 结论：LEFT JOIN 要筛选右表条件，放 ON 里，不放 WHERE 里

### 多表 JOIN
可以连续 JOIN 多张表：`ad_data → campaigns → channels`

## 四、窗口函数（Window Function）

### 和 GROUP BY 的区别
- GROUP BY：把多行合并成一行（每组一行），原始行消失
- 窗口函数：不合并行，给每行加一个窗口计算结果，原始行保留

### 基本语法
```sql
函数名() OVER (
    PARTITION BY 分组列    -- 窗口范围
    ORDER BY 排序列        -- 窗口内排序
    ROWS BETWEEN ... AND ...  -- 行范围（滑动窗口）
)
```

### 三大类窗口函数

#### 1. 排名类（最常用！）
| 函数 | 作用 | 例子（花费700,700,500,300） |
|---|---|---|
| `ROW_NUMBER()` | 行号，不重复 | 1, 2, 3, 4 |
| `RANK()` | 排名，并列跳号 | 1, 1, 3, 4 |
| `DENSE_RANK()` | 密集排名，并列不跳号 | 1, 1, 2, 3 |

- 取每个分组 Top N 标准写法：
```sql
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY channel ORDER BY cost DESC) AS rn
    FROM ad_data
) t WHERE rn <= 3;
```

#### 2. 聚合类
- `SUM() OVER`：分组求和 / 累计求和（加ORDER BY就是累计）
- `AVG() OVER` + `ROWS BETWEEN 2 PRECEDING AND CURRENT ROW`：3天滑动平均
- `COUNT() OVER` `MAX() OVER` `MIN() OVER`

#### 3. 偏移类
- `LAG(列, n)`：取当前行往前第n行的值（算环比必备！）
- `LEAD(列, n)`：取当前行往后第n行的值
- `FIRST_VALUE()` `LAST_VALUE()`

- 算环比：
```sql
SELECT date, cost,
    LAG(cost) OVER (PARTITION BY channel ORDER BY date) AS prev_cost,
    (cost - LAG(cost) OVER (...)) / LAG(cost) OVER (...) AS daily_change
FROM ad_data;
```

### 窗口范围 ROWS BETWEEN
- `UNBOUNDED PRECEDING`：分区第一行
- `n PRECEDING`：往前n行
- `CURRENT ROW`：当前行
- `n FOLLOWING`：往后n行
- `UNBOUNDED FOLLOWING`：分区最后一行

## 五、SQL 和 pandas 对比
| SQL | pandas |
|---|---|
| SELECT 列 | df[['列']] |
| WHERE 条件 | df[df['列']条件] |
| ORDER BY | df.sort_values() |
| LIMIT | df.head(n) |
| GROUP BY + SUM | df.groupby('列')['列'].sum() |
| HAVING | df.groupby().filter() |
| INNER JOIN | pd.merge(how='inner') |
| LEFT JOIN | pd.merge(how='left') |
| ROW_NUMBER OVER | df.groupby()['列'].rank(method='first') |
| SUM OVER (PARTITION) | df.groupby()['列'].transform('sum') |
| SUM OVER (ORDER BY) | df.groupby()['列'].cumsum() |
| LAG OVER | df.groupby()['列'].shift(1) |
| AVG OVER (ROWS) | df.groupby()['列'].rolling(n).mean() |

## 六、本机 MySQL 信息
- 版本：MySQL 8.0.46 Community Server
- 安装路径：C:\Program Files\MySQL\MySQL Server 8.0\
- 服务名：mysql180（正在运行）
- 配置文件：C:\ProgramData\MySQL\MySQL Server 8.0\my.ini
- 连接命令：`"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p`

## 下次待做
- 连接 MySQL，创建项目数据库和用户
- 写 database.py 模块（连接数据库、建表、写入数据、查询）
- 把清洗后的广告数据写入 MySQL
- 用 SQL 查询验证指标计算结果
