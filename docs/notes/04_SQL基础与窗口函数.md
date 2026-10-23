# SQL 基础与窗口函数

本篇覆盖：关系型数据库的数据组织方式（表、主键、外键、索引、B+ 树）；SQL 的书写顺序与执行顺序；WHERE 与 HAVING 的分工；聚合函数与 GROUP BY 规则；四类 JOIN 以及 LEFT JOIN 中 ON 与 WHERE 的差别；窗口函数的三大类（排序、聚合、偏移）、窗口范围与五个可背模板；以及 CASE WHEN、COALESCE、日期与字符串函数、自连接、同比环比。窗口函数在 SQL 面试中出场率最高，本篇一次讲透，练习题统一放在《05 SQL 实战练习》。

---

## 一、关系型数据库的基本概念

### 1.1 数据库与表

数据库是专门存储和管理数据的软件系统。相对于文件（CSV、Excel），它多了三样东西：结构化约束、索引、并发访问控制。

| 概念 | 含义 |
|---|---|
| 表（Table） | 存数据的二维结构，有行有列 |
| 行（Row） | 一条记录 |
| 列（Column） | 一个字段，有固定的数据类型 |
| 主键（Primary Key） | 每一行的唯一标识 |
| 外键（Foreign Key） | 指向另一张表主键的列，用来建立表间关系 |
| 索引（Index） | 加速查询的数据结构，类似书的目录 |

本套笔记以 MySQL 8.0 为例（8.0 起支持窗口函数与 CTE）。语法上 Hive / Spark SQL 与 MySQL 高度接近，差异点会单独标注。

### 1.2 主键

**是什么**：唯一标识每一行的列，铁律是**唯一 + 非空**，一张表只能有一个主键。

**类比**：身份证号。姓名可以重复，身份证号不会重复。

**常见形态**：

```sql
-- 自增整数
CREATE TABLE ads (
  id INT AUTO_INCREMENT PRIMARY KEY,
  channel VARCHAR(50),
  stat_date DATE,
  cost DECIMAL(12,2)
);

-- 或使用业务唯一值（订单号、活动 ID）
```

**关键点**：主键本身就是一个唯一索引，所以 `WHERE id = 100` 是最快、最准的一种查询方式。

**为什么需要**：没有主键，就无法稳定地定位"某一行"，更新和删除都会变得不可控，多表关联也失去锚点。

### 1.3 外键

**是什么**：某一列指向另一张表的主键。

**作用**：保证"引用完整性"，防止悬空引用——不允许出现指向不存在行的记录。

```sql
FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
```

### 1.4 主键 vs 外键

| | 主键 | 外键 |
|---|---|---|
| 作用 | 唯一标识**本表**每一行 | 指向**另一张表**的主键 |
| 特点 | 唯一、非空 | 取值必须来自被引用表（或为 NULL） |
| 数量 | 每表一个 | 可以有多个 |
| 类比 | 身份证号 | 户主关系 |

### 1.5 索引

**是什么**：给表建的"目录"，用来加速查询。没有索引时只能全表扫描（从头看到尾）。

**原理（三步）**：

1. 建索引时，先把该列的值**排序**；
2. 排好序之后就能**二分查找**，每次砍掉一半；
3. 查找复杂度从 O(n) 降到 O(log n)。

数据量与查找次数的对比：

| 数据量 n | 全表扫描 | 二分查找 log₂(n) |
|---|---|---|
| 1 万 | 1 万次 | 14 次 |
| 100 万 | 100 万次 | 20 次 |
| 1 亿 | 1 亿次 | 27 次 |

**B+ 树（一句话版本）**：索引的底层结构是 B+ 树，一种多叉排序树。它的关键特征是"树很矮"——一个节点能存几百个值，1 亿条数据树高也只有 3~4 层，所以一次查找只需要 3~4 次磁盘读取。

> 排序是内容，B+ 树是承载排序、并且适应磁盘读写的结构。二者是"内容"和"容器"的关系。

**索引的代价**（面试追问"为什么不给所有列都加索引"）：

1. 占空间——索引自己要存一份数据；
2. 拖慢写入——每次 INSERT / UPDATE 都要同步维护索引。

所以只在高频查询列上建索引。

**面试话术**：索引是给表建的目录，底层是 B+ 树（排序树），把全表扫描的线性查找变成二分查找，复杂度 log(n)，所以快；代价是占空间、拖慢写入，因此只在高频查询列上建。

### 1.6 逻辑关联 vs 物理外键约束

| | 物理关联（外键约束） | 逻辑关联 |
|---|---|---|
| 谁保证 | 数据库强制校验 | 写 SQL 时由 JOIN 条件保证 |
| 类比 | 门禁锁 | 两家"约好" |

**生产环境普遍用逻辑关联，原因有两条**：

1. 外键约束拖慢写入——每次插入都要跨表校验；
2. 大数据量 ETL 灌数时，加载顺序不保证，外键会频繁报错。

**面试话术**：生产环境一般不加 FOREIGN KEY 约束，用逻辑关联——建表时保证有关联列，关联关系由查询时的 JOIN 条件保证。外键会拖慢写入，也不适合大数据量 ETL。

### 1.7 概念串联

```
主键（身份证）→ 外键（关联）→ JOIN（多表查询）
                          ↓
索引（排序 + 二分查找）→ 加速查询（B+ 树，树矮读盘少）
```

### 1.8 校招考点边界

| 考点 | 状态 |
|---|---|
| 主键 / 外键 / 索引概念 | 必考 |
| 索引为什么快（log 复杂度） | 必考 |
| JOIN 区别（INNER / LEFT） | 必考 |
| 存储过程、触发器 | 校招不考 |
| 索引调优、执行计划 | 社招 / DBA 才考 |
| B+ 树节点细节 | 后端岗才考，知道"树矮读盘少"即可 |
| 事务隔离级别、三大范式 | 知道概念即可 |

---

## 二、SELECT 语句与执行顺序

### 2.1 完整语法骨架

```sql
SELECT 列名
FROM 表名
WHERE 条件
GROUP BY 分组列
HAVING 分组后条件
ORDER BY 排序列 DESC
LIMIT 数量;
```

### 2.2 书写顺序 ≠ 执行顺序

```
书写：SELECT → FROM → WHERE → GROUP BY → HAVING → ORDER BY → LIMIT
执行：FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY → LIMIT
```

记住执行顺序，很多"为什么报错"就不用背了，直接推：

| 现象 | 原因 |
|---|---|
| WHERE 里不能用聚合函数 | WHERE 在 GROUP BY 之前执行，此时还没有分组结果 |
| WHERE 里不能用 SELECT 定义的别名 | SELECT 在 WHERE 之后才执行，别名还不存在 |
| HAVING 里不能用 SELECT 定义的别名 | HAVING 在 SELECT 之前执行 |
| GROUP BY 里不能写 SELECT 别名 | 同上，GROUP BY 早于 SELECT |
| ORDER BY 里可以用别名 | ORDER BY 在 SELECT 之后执行 |
| 窗口函数不能写在 WHERE 里 | 窗口计算发生在 SELECT 阶段，WHERE 阶段还没有这一列 |

**由此得到三条硬规则**：

1. HAVING 要用聚合结果，就写完整表达式：`HAVING SUM(clicks) > 100`；
2. GROUP BY 要用 CASE WHEN 分组，必须原样复制表达式，不能写别名；
3. 窗口函数的结果要筛选，必须包一层子查询或 CTE，在外层用 WHERE。

### 2.3 条件运算符

| 运算符 | 用法 | 备注 |
|---|---|---|
| `=` `<>` `!=` `>` `<` `>=` `<=` | 比较 | `<>` 与 `!=` 等价 |
| `AND` `OR` `NOT` | 逻辑组合 | `AND` 优先级高于 `OR`，混用时加括号 |
| `IN (...)` / `NOT IN (...)` | 属于某个集合 | 集合里含 NULL 时 `NOT IN` 结果可能为空 |
| `BETWEEN a AND b` | 闭区间 | 日期范围不建议用它，见下 |
| `LIKE '%xxx%'` | 模糊匹配 | `%` 任意多个字符，`_` 恰好一个字符 |
| `IS NULL` / `IS NOT NULL` | 判空 | **必须用 IS，不能写 `= NULL`**（NULL 与任何值比较结果都是 UNKNOWN） |

**日期范围的写法**：用左闭右开，`>= 起` 且 `< 次日 00:00:00`。若写 `<= 当日 23:59:59`，会漏掉 `23:59:59.5` 这类带毫秒的数据。

```sql
WHERE stat_date >= '2026-08-01' AND stat_date < '2026-09-01'
```

### 2.4 WHERE vs HAVING

| | WHERE | HAVING |
|---|---|---|
| 执行时机 | GROUP BY **之前** | GROUP BY **之后** |
| 作用对象 | 原始行 | 分组后的结果 |
| 能否用聚合函数 | 不能 | 能 |
| 能否用 SELECT 别名 | 不能 | 不能（MySQL 方言允许，但不通用） |
| 性能 | 先过滤，参与分组的数据更少，更快 | 分组完才过滤 |

**原则**：能在 WHERE 过滤掉的，不要留到 HAVING。WHERE 先砍掉数据量，GROUP BY 的开销随之下降。

---

## 三、聚合与分组

### 3.1 聚合函数

| 函数 | 作用 | 注意 |
|---|---|---|
| `COUNT(*)` | 行数，NULL 也算 | 统计"有多少条记录" |
| `COUNT(列)` | 该列**非空**值的个数 | 列里有 NULL 时会少算 |
| `COUNT(DISTINCT 列)` | 该列去重后的非空个数 | 常用于 UV、渠道数 |
| `SUM(列)` | 求和 | 忽略 NULL；全为 NULL 时返回 NULL |
| `AVG(列)` | 平均 | 分母是**非空**个数，不是总行数 |
| `MAX(列)` / `MIN(列)` | 最大 / 最小 | 可用于日期和字符串 |

```sql
SELECT COUNT(*)                  AS rows_all,
       COUNT(cost)               AS rows_cost_not_null,
       COUNT(DISTINCT channel)   AS channel_cnt
FROM ads;
```

**COUNT 三种写法的区别**（高频面试点）：

- 不分组时，数的是整表；
- 分组后，数的是每个组。

### 3.2 GROUP BY 规则

- SELECT 里出现的列，要么在 GROUP BY 里，要么被聚合函数包住，不能有第三种情况；
- 多列分组：`GROUP BY channel, stat_date`，分组键是这两列的组合，每一个不同的组合算一个组；
- 分组后，明细列就消失了。`SELECT channel, MAX(stat_date), cost FROM ... GROUP BY channel` 里的 `cost` 取到的是组内**某一行**的值，不是最大日期那天的值。

**ONLY_FULL_GROUP_BY**：MySQL 5.7 起默认开启的 SQL 模式，强制"非聚合列必须出现在 GROUP BY 里"，就是为了防止上面这种"拿到随机行的值却以为是对的"。开启后那条语句会直接报错。面试主动提这个模式，说明真踩过坑。

**取"每组最新一条的完整记录"不能用 GROUP BY + MAX**，正确写法是窗口函数，见 5.4 节。

---

## 四、JOIN 多表连接

### 4.1 四类 JOIN

| 类型 | 结果 | 左表独有 | 交集 | 右表独有 |
|---|---|---|---|---|
| `INNER JOIN` | 两边都匹配的行 | 否 | 是 | 否 |
| `LEFT JOIN` | 左表全部 + 右表匹配的 | 是 | 是 | 否（补 NULL） |
| `RIGHT JOIN` | 右表全部 + 左表匹配的 | 否（补 NULL） | 是 | 是 |
| `FULL JOIN` | 两边全部 | 是 | 是 | 是 |

> MySQL 不直接支持 `FULL JOIN`，需要用 `LEFT JOIN UNION RIGHT JOIN` 模拟。Spark SQL 支持 `full`。

**一句话记忆**：INNER 取交集；LEFT 左表全要、右边缺失补 NULL。

### 4.2 语法与三要素

```sql
SELECT a.channel, b.owner, SUM(a.cost) AS total_cost
FROM ads a
INNER JOIN campaigns b ON a.campaign = b.name
WHERE b.status = 'active'
GROUP BY a.channel, b.owner;
```

JOIN 三要素：**FROM 主表 → JOIN 表 → ON 关联条件**。

- 表别名：`ads a`、`campaigns b`，后续一律用别名引用；
- 两表有同名列时必须加表前缀（`a.channel = b.channel`），否则报"column ambiguous"；
- ON 负责"怎么连"，WHERE 负责"连完之后筛什么"。

多表可以连续 JOIN：

```sql
FROM ad_data a
JOIN campaigns b ON a.campaign_id = b.id
JOIN channels  c ON b.channel_id  = c.id
```

### 4.3 关联键必须一一对应（数据放大的坑）

事实表关联维度表时，如果关联键是"一对多"的字段，结果会成倍膨胀。

**典型场景**：用 `channel` 关联 `ads` 和 `campaigns`。一个渠道下有多个活动，于是 Google 的 2 个活动各自"认领"了一遍完整的 Google 花费，总花费直接翻倍。

**教训**：关联键必须能唯一确定一行。事实表与维度表应当用唯一键（`campaign_id` 或活动名）关联，而不是 `channel` 这种一对多的字段。

```sql
-- 错误：channel 一对多，金额被重复计算
FROM ads a JOIN campaigns c ON a.channel = c.channel

-- 正确：活动名一一对应
FROM ads a JOIN campaigns c ON a.campaign = c.name
```

**自检方法**：JOIN 前后的行数应当可解释。若 JOIN 后行数暴增，先怀疑关联键不唯一。

### 4.4 LEFT JOIN 中 ON 与 WHERE 的区别（面试常考）

| 写法 | 语义 | 结果 |
|---|---|---|
| 右表条件放 `ON` | 连接时只匹配满足条件的右表行 | 左表**全部保留**，不匹配的右表列为 NULL |
| 右表条件放 `WHERE` | 先连接，再对结果整体过滤 | 右表列为 NULL 的行被过滤掉，**退化成 INNER JOIN** |

```sql
-- 保留所有渠道，即使没有 active 活动
FROM ads a LEFT JOIN campaigns c
  ON a.campaign = c.name AND c.status = 'active'

-- 只留下有 active 活动的渠道（等价于 INNER JOIN）
FROM ads a LEFT JOIN campaigns c
  ON a.campaign = c.name
WHERE c.status = 'active'
```

**结论**：LEFT JOIN 要筛选右表，条件写进 `ON`；写进 `WHERE` 就白左连了。

同理，`WHERE c.col IS NULL` 是**故意**用这个特性做"反连接"（找左表有、右表没有的行），属于正确用法。

---

## 五、窗口函数

### 5.1 与 GROUP BY 的本质区别

| 对比项 | GROUP BY | 窗口函数 |
|---|---|---|
| 结果行数 | 每组只剩 1 行 | 原表多少行，结果就多少行 |
| 计算范围 | 整个组 | 组内可以是前 N 行、后 N 行、累计区间 |
| 典型用途 | 分组求和、计数、平均 | 排名、累计、同比环比、移动平均 |
| 结果筛选 | 用 HAVING | 必须包一层子查询，用 WHERE |

**一句话**：GROUP BY 是"把一组人变成一个人"，窗口函数是"给每个人都算一个与他相关的统计值，人不减少"。

### 5.2 语法结构

```sql
函数名() OVER (
    PARTITION BY 分组列          -- 窗口范围：按什么分组，不写就是全表一个窗口
    ORDER BY 排序列              -- 窗口内排序：排名/累计/环比时必选
    ROWS BETWEEN 起点 AND 终点   -- 行范围：累计/移动平均时用
)
```

三部分都是可选的，但组合方式由需求决定：

| 需求 | PARTITION BY | ORDER BY | ROWS BETWEEN |
|---|---|---|---|
| 全局排名 | 不写 | 必写 | 不写 |
| 组内排名 | 写 | 必写 | 不写 |
| 分区全局聚合（每行带上本组总额） | 写 | 不写 | 不写 |
| 累计求和 | 写 | 必写 | 必写 |
| 移动平均 | 按需 | 必写 | 必写 |
| 环比 LAG / LEAD | 写 | 必写 | **不写** |

### 5.3 第一类：排序函数（最常用）

| 函数 | 并列时的行为 | 示例（100, 90, 90, 80） |
|---|---|---|
| `ROW_NUMBER()` | 强行编号，永不重复 | 1, 2, 3, 4 |
| `RANK()` | 并列同名，**跳号** | 1, 2, 2, 4 |
| `DENSE_RANK()` | 并列同名，**不跳号** | 1, 2, 2, 3 |

同样的规则套在花费 700、700、500、300 上：`ROW_NUMBER` 得 1,2,3,4；`RANK` 得 1,1,3,4；`DENSE_RANK` 得 1,1,2,3。

**业务选择**：

- 要"恰好 N 条"（名额固定，比如每渠道取 3 天）→ `ROW_NUMBER`；
- 要"前 N 名（含并列）"（比如成绩排名前 3）→ `DENSE_RANK`；
- 只是排名展示、允许并列跳号 → `RANK`。

**取每组 Top N 的标准写法**（必背）：

```sql
SELECT channel, stat_date, cost
FROM (
    SELECT channel, stat_date, cost,
           ROW_NUMBER() OVER (PARTITION BY channel ORDER BY cost DESC) AS rn
    FROM ad_daily
) t
WHERE rn <= 3;
```

三个要点：

1. 窗口函数不能直接写在 WHERE 里，必须先算成列，再在外层筛选；
2. 子查询必须起别名（`t`），否则报错；
3. 也可以写成 CTE：`WITH ranked AS (...) SELECT ... WHERE rn <= 3`。

**去掉 PARTITION BY 就是全局排名**：

```sql
SELECT channel, stat_date, cvr
FROM (
    SELECT channel, stat_date, conversions / clicks AS cvr,
           ROW_NUMBER() OVER (ORDER BY conversions / clicks DESC) AS rn
    FROM ads WHERE clicks > 0
) t
WHERE rn <= 3;
```

计算列和窗口函数可以放在同一层 SELECT 里（窗口的 ORDER BY 直接引用表达式即可）。

**常见错误**：`ORDER BY cost DESC LIMIT 3` 是全局 Top 3，不是"每个渠道各 3 条"。分组 Top N 必须用 `PARTITION BY`。

### 5.4 第二类：聚合窗口

普通聚合函数（SUM / AVG / COUNT / MAX / MIN）都能加 `OVER` 变成窗口计算。区别只在于窗口范围。

#### 累计求和

```sql
SELECT channel, stat_date, cost,
       SUM(cost) OVER (
           PARTITION BY channel
           ORDER BY stat_date
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
       ) AS cumulative_cost
FROM ad_daily;
```

**关键**：累计求和必须显式写 `ROWS BETWEEN ... CURRENT ROW`。只写 `ORDER BY` 时，不同数据库的默认框架不一致（MySQL 默认是 `RANGE UNBOUNDED PRECEDING AND CURRENT ROW`，遇到并列值会一起算进去），写清楚才不会出错。

**常见错误**：忘了 `ORDER BY`，`SUM(cost) OVER (PARTITION BY channel)` 会让每一行都等于整个渠道的总花费，而不是累计值。这个写法本身有用（"每行带上所属分组的总额"），但那是另一个需求。

#### 移动平均

```sql
-- 3 日移动平均：当前行往前 2 行 + 当前行
SELECT stat_date, cost,
       AVG(cost) OVER (
           ORDER BY stat_date
           ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
       ) AS ma_3d
FROM ad_daily;
```

`N` 日移动平均的起点是 `N-1 PRECEDING`。窗口开头的几行凑不满 N 行，就按实际存在的行数计算（SQL 不会补 NULL）。

#### 分区全局聚合

```sql
-- 每一行都带上本渠道总花费，用于算"本行占渠道的比例"
SELECT channel, stat_date, cost,
       SUM(cost) OVER (PARTITION BY channel) AS channel_total
FROM ad_daily;
```

### 5.5 第三类：偏移函数

| 函数 | 作用 |
|---|---|
| `LAG(列, n)` | 取当前行**往前**第 n 行的值 |
| `LEAD(列, n)` | 取当前行**往后**第 n 行的值 |
| `FIRST_VALUE(列)` | 窗口内第一行的值 |
| `LAST_VALUE(列)` | 窗口内最后一行的值（默认框架下常常只到当前行，需显式写 `ROWS BETWEEN ... AND UNBOUNDED FOLLOWING`） |

`LAG` / `LEAD` 只需要 `PARTITION BY + ORDER BY`，**不需要** `ROWS BETWEEN`。

#### 环比标准写法

```sql
SELECT channel, stat_date, cost,
       LAG(cost, 1) OVER (PARTITION BY channel ORDER BY stat_date) AS prev_cost,
       ROUND(
           (cost - LAG(cost, 1) OVER (PARTITION BY channel ORDER BY stat_date))
           / NULLIF(LAG(cost, 1) OVER (PARTITION BY channel ORDER BY stat_date), 0)
       , 4) AS dod_rate
FROM ad_daily;
```

**三个要点**：

1. 环比 =（本期 − 上期）/ 上期；
2. 第一行没有上一行，`LAG` 返回 NULL，算出来的比率也是 NULL，需要 `WHERE prev_cost IS NOT NULL` 过滤；
3. 分母可能为 0，用 `NULLIF(x, 0)` 把 0 变成 NULL，避免除零报错。

#### 聚合 + 窗口的嵌套

先按天聚合，再对聚合结果取上一行，是最常见的组合：

```sql
SELECT stat_date, daily_cost, prev_cost,
       ROUND((daily_cost - prev_cost) / prev_cost * 100, 2) AS dod_pct
FROM (
    SELECT stat_date,
           SUM(cost) AS daily_cost,
           LAG(SUM(cost), 1) OVER (ORDER BY stat_date) AS prev_cost
    FROM ad_daily
    GROUP BY stat_date
) t
WHERE prev_cost IS NOT NULL
ORDER BY stat_date;
```

执行顺序是"先 GROUP BY 聚合，再窗口函数排名/取上一行"。窗口函数可以和数据聚合写在同一个 SELECT 里，因为它作用在聚合之后的结果上。

#### 窗口函数与 GROUP BY 同用

```sql
SELECT owner, campaign, total_cost
FROM (
    SELECT a.owner, a.name AS campaign,
           SUM(b.cost) AS total_cost,
           ROW_NUMBER() OVER (PARTITION BY a.owner ORDER BY SUM(b.cost) DESC) AS rn
    FROM campaigns a
    LEFT JOIN ads b ON a.name = b.campaign
    GROUP BY a.owner, a.name
) t
WHERE rn = 1;
```

需求是"每个 owner 花费最高的活动"，所以必须先 `SUM + GROUP BY` 得到总花费，再在聚合结果上排名。

### 5.6 窗口范围 ROWS BETWEEN

| 关键字 | 含义 |
|---|---|
| `UNBOUNDED PRECEDING` | 分区第一行 |
| `n PRECEDING` | 往前 n 行 |
| `CURRENT ROW` | 当前行 |
| `n FOLLOWING` | 往后 n 行 |
| `UNBOUNDED FOLLOWING` | 分区最后一行 |

常见组合：

| 目标 | 写法 |
|---|---|
| 累计（从头到当前） | `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` |
| 最近 3 行 | `ROWS BETWEEN 2 PRECEDING AND CURRENT ROW` |
| 前后各 1 行（居中平滑） | `ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING` |
| 全分区 | `ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING` |

`ROWS` 按"行"数，`RANGE` 按"值"（并列值会一起进窗口）。日常取 TopN / 累计用 `ROWS` 更可控。

### 5.7 五个可背模板

| 场景 | 窗口定义 |
|---|---|
| 组内排名 / Top N | `PARTITION BY 分组列 ORDER BY 指标 DESC` |
| 累计求和 | `PARTITION BY 分组列 ORDER BY 日期 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` |
| N 日移动平均 | `ORDER BY 日期 ROWS BETWEEN (N-1) PRECEDING AND CURRENT ROW` |
| 环比（LAG） | `PARTITION BY 分组列 ORDER BY 日期` |
| 分区全局聚合 | `PARTITION BY 分组列` |

### 5.8 窗口函数四个常见错误

1. 累计求和忘了写 `ROWS BETWEEN`，每行都变成整个分区的总和；
2. 排名函数画蛇添足写了 `ROWS BETWEEN`，排名不需要行范围，写了可能改变语义或报错；
3. `LAG` / `LEAD` 写了 `ROWS BETWEEN`，它们只需要分区和排序；
4. 把 `PARTITION BY` 当成 `GROUP BY`，以为会合并行——它不合并。

另外两条语法坑：

- 窗口函数不能直接出现在 `WHERE` 或 `HAVING` 里，必须包一层子查询或 CTE；
- 窗口函数不能嵌套在 `DATE_SUB()` 这类函数内部（MySQL 不允许），要先把 `rn` 算出来，下一层再用。

### 5.9 只分组不聚合会怎样

`GROUP BY` 之后必须接聚合函数，否则拿到的是 `GroupedData` 对象，不能直接 `show()` / 查询，会报错。

三种"分组"需求的区别：

| 需求 | 用什么 | 结果行数 |
|---|---|---|
| 分组后算聚合指标 | `GROUP BY + 聚合函数` | 每组 1 行 |
| 分组但保留所有行 | `PARTITION BY + 窗口函数` | 与原表相同 |
| 分组后把值收集成数组 | `GROUP BY + COLLECT_LIST / COLLECT_SET` | 每组 1 行，含数组 |

---

## 六、CASE WHEN 条件表达式

**用途**：行级条件判断，等价于 Excel 的 IF、pandas 的 `np.where`。可以出现在 SELECT、WHERE、GROUP BY、ORDER BY、聚合函数内部。

```sql
SELECT channel, cost,
       CASE WHEN cost > 3000 THEN '高'
            WHEN cost > 1000 THEN '中'
            ELSE '低'
       END AS cost_level
FROM ad_daily;
```

**规则**：

- 从上到下匹配，命中第一个为真的分支就返回，后面的不再判断——所以条件要从严格到宽松排列；
- 没有 `ELSE` 且全部不匹配时返回 NULL；
- `CASE` 表达式可以嵌套在聚合函数里，这是"条件聚合"的基础。

**条件聚合**（一次扫表算多个指标）：

```sql
SELECT stat_date,
       SUM(CASE WHEN channel = 'Facebook' THEN cost ELSE 0 END) AS facebook_cost,
       SUM(CASE WHEN channel = 'Google'   THEN cost ELSE 0 END) AS google_cost
FROM ad_daily
GROUP BY stat_date;
```

**易错点**：`GROUP BY` 里不能写 SELECT 定义的别名，必须把整个 CASE WHEN 表达式原样复制一遍。

```sql
-- 错误
SELECT CASE WHEN clicks >= 3000 THEN '高点击' ELSE '低点击' END AS click_level,
       COUNT(*)
FROM ads
GROUP BY click_level;

-- 正确
SELECT CASE WHEN clicks >= 3000 THEN '高点击' ELSE '低点击' END AS click_level,
       COUNT(*) AS cnt
FROM ads
GROUP BY CASE WHEN clicks >= 3000 THEN '高点击' ELSE '低点击' END;
```

---

## 七、空值处理

### 7.1 NULL 的语义

- NULL 表示"未知"，不是 0，也不是空字符串；
- 任何值与 NULL 做算术运算，结果都是 NULL；
- 任何值与 NULL 比较（`=`、`>`、`<>`），结果都是 UNKNOWN，不会返回 TRUE；
- 判断空值只能用 `IS NULL` / `IS NOT NULL`；
- 聚合函数普遍忽略 NULL：`SUM` / `AVG` / `MAX` / `MIN` / `COUNT(列)` 都不把 NULL 计入，只有 `COUNT(*)` 算行数。

### 7.2 COALESCE

```sql
COALESCE(SUM(b.cost), 0)        -- NULL 变 0（最常用）
COALESCE(phone, email, '无')    -- 返回第一个非 NULL 的值
IFNULL(SUM(cost), 0)            -- MySQL 专用，只能传两个参数
```

- `COALESCE` 是 SQL 标准，所有数据库通用；`IFNULL` 是 MySQL 方言；
- 典型场景：LEFT JOIN 后右表没匹配上，聚合结果是 NULL，用 COALESCE 兜底成 0；
- 排序时也要考虑 NULL：MySQL 升序把 NULL 排在最前，降序排在最后，需要固定顺序时用 `ORDER BY COALESCE(col, 0)`。

### 7.3 NULLIF 与除零

```sql
ROUND(SUM(clicks) / NULLIF(SUM(impressions), 0), 4)
```

`NULLIF(a, b)` 在 `a = b` 时返回 NULL，否则返回 a。用它把分母 0 变成 NULL，除法结果自然是 NULL，而不是报错或产生 inf。

另一种写法是 `CASE WHEN 分母 > 0 THEN 分子/分母 END`。两种都可以，`NULLIF` 更短。

---

## 八、常用函数

### 8.1 数值与类型

| 函数 | 作用 | 备注 |
|---|---|---|
| `ROUND(x, n)` | 四舍五入到 n 位 | 结果仍是数值 |
| `ABS(x)` | 绝对值 | |
| `CAST(x AS DECIMAL(10,4))` | 类型转换 | 解决整数除法 |
| `CEIL(x)` / `FLOOR(x)` | 向上 / 向下取整 | |

**整数除法的坑**：`1 / 2` 在 MySQL 里得 `0.5`，但在某些数据库（如 Hive、部分版本的 SQL Server）里两个整数相除会截断成 `0`。保险写法是乘 `1.0` 或显式 CAST：

```sql
SUM(clicks) * 1.0 / SUM(impressions)
CAST(SUM(clicks) AS DECIMAL(10,4)) / SUM(impressions)
```

### 8.2 日期函数（MySQL）

```sql
CURDATE()                              -- 今天
DATE_SUB(CURDATE(), INTERVAL 30 DAY)   -- 30 天前
DATE_ADD(d, INTERVAL 1 DAY)            -- 后一天
DATEDIFF('2026-08-30', '2026-08-01')   -- 相差天数
DATE_FORMAT(stat_date, '%Y-%m')        -- 格式化为年月
MONTH(stat_date) / YEAR(stat_date)     -- 取月 / 取年
```

**近 30 天的标准写法**（动态，优于硬编码日期）：

```sql
WHERE stat_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
```

**Hive / Spark SQL 差异**：没有 `INTERVAL n DAY` 的写法，用 `date_sub(stat_date, rn)`、`date_add(stat_date, 1)`。

### 8.3 字符串函数

```sql
campaign LIKE '%双11%'        -- 包含
campaign LIKE '夏季%'         -- 以……开头
campaign LIKE '%词'           -- 以……结尾
campaign LIKE '_词'           -- _ 恰好匹配 1 个字符
CONCAT(channel, '-', campaign) -- 拼接
LENGTH(col)                    -- 长度
SUBSTRING(col, 起, 长)         -- 截取（MySQL 从 1 开始）
REPLACE(col, 旧, 新)           -- 替换
UPPER(col) / LOWER(col)        -- 大小写
TRIM(col)                      -- 去首尾空格
```

**LIKE 的性能提示**：以 `%` 开头的模式（`'%词'`）无法使用索引，只能全表扫描。大表上要慎用，或改用全文索引 / 搜索引擎。

---

## 九、自连接

**是什么**：一张表和自己 JOIN，必须起两个不同的别名，否则无法区分。

**用途**：同一张表内行与行比较，比如找上级、算同比、找相邻记录。

### 9.1 找上级

```sql
SELECT e.name AS 员工, m.name AS 上级
FROM employee e
LEFT JOIN employee m ON e.manager_id = m.id;
```

这里用 LEFT JOIN 是为了保留没有上级的员工（比如老板），否则会被 INNER JOIN 过滤掉。

### 9.2 同比 = 自连接 + 日期差一年

```sql
SELECT a.month, a.amount AS 今年, b.amount AS 去年,
       ROUND((a.amount - b.amount) / b.amount * 100, 2) AS 同比
FROM sales a
LEFT JOIN sales b
    ON b.month = DATE_SUB(a.month, INTERVAL 1 YEAR);
```

关键是 JOIN 条件 `b.month = DATE_SUB(a.month, INTERVAL 1 YEAR)`，把今年每个月连到去年同月。

### 9.3 环比与同比的区别

| | 环比 | 同比 |
|---|---|---|
| 对比对象 | 上一个周期（今天 vs 昨天、这月 vs 上月） | 去年同期（今年 8 月 vs 去年 8 月） |
| 实现 | `LAG` | 自连接（日期差一年） |
| 为什么 | 相邻行，天然可用偏移函数 | 跨年，中间隔了 12 行，`LAG(…, 12)` 也能做但依赖数据完整 |

`LAG` 与自连接的取舍：

| 方式 | 优点 | 坑 |
|---|---|---|
| `LAG` | 一次扫描，快，写法短 | 取的是"前第 N **行**"，日期缺失时会错位 |
| 自连接（`date = date - 1`） | 按日期值匹配，缺数据也不会错 | 扫两次，慢 |

**生产选择**：日期保证连续（每天都有分区）→ 用 LAG；日期可能缺失（停投、周末无数据）→ 按日期自连接。

---

## 十、去重与集合运算

### 10.1 DISTINCT

```sql
SELECT DISTINCT channel FROM ads;          -- 去重后列出（5 行）
SELECT COUNT(DISTINCT channel) FROM ads;   -- 去重后计数（1 个数字）
```

`DISTINCT` 作用于它后面紧跟的所有列：`SELECT DISTINCT channel, stat_date` 是对"渠道 + 日期"的组合去重，不是只对 channel 去重。

### 10.2 UNION / UNION ALL

```sql
SELECT channel FROM ads WHERE channel = 'TikTok'
UNION ALL
SELECT channel FROM ads WHERE channel = 'Google';
```

| 写法 | 是否去重 | 性能 |
|---|---|---|
| `UNION` | 自动去重 | 需要额外排序/哈希，慢 |
| `UNION ALL` | 不去重 | 直接拼接，快 |

- JOIN 是**左右拼接**（加列），UNION 是**上下拼接**（加行）；
- 上下两个查询的**列数必须一致**，类型要兼容；
- 能确定不需要去重时，一律用 `UNION ALL`。

### 10.3 IN 子查询

```sql
-- 固定列表
WHERE channel IN ('TikTok', 'Google')

-- 子查询：查"李四负责的活动"的数据
WHERE campaign IN (SELECT name FROM campaigns WHERE owner = '李四')
```

`IN` 子查询返回多行一列。数据量大时 `JOIN` 通常比 `IN` 子查询更容易被优化器改写，但可读性上 `IN` 更直观。

---

## 十一、面试速查

- 主键是什么 → 唯一标识每一行的列，唯一且非空，每表一个，本身就是一个唯一索引。
- 外键是什么 → 指向另一张表主键的列，用来保证引用完整性。
- 生产环境为什么不用外键约束 → 拖慢写入、大数据量 ETL 加载顺序不保证会频繁报错，改用逻辑关联。
- 索引为什么快 → 底层 B+ 树是有序结构，把全表扫描的 O(n) 变成二分查找的 O(log n)。
- 索引的代价是什么 → 占空间、拖慢写入，所以只在高频查询列上建。
- SQL 的执行顺序 → FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY → LIMIT。
- WHERE 和 HAVING 的区别 → WHERE 分组前筛行、不能用聚合；HAVING 分组后筛组、能用聚合。
- 为什么 WHERE 里不能用 SELECT 别名 → SELECT 在 WHERE 之后执行，别名此时还不存在。
- COUNT(*) 和 COUNT(列) 的区别 → 前者数行数（含 NULL 行），后者只数该列非空的个数。
- INNER 和 LEFT JOIN 的区别 → INNER 取交集；LEFT 保留左表全部，右表缺失补 NULL。
- LEFT JOIN 的右表条件写哪 → 写 ON 才能保留左表全部；写 WHERE 会退化成 INNER JOIN。
- 关联键选错会怎样 → 一对多关联会导致金额重复计算、行数膨胀，必须用唯一键关联。
- 窗口函数和 GROUP BY 的区别 → 窗口不合并行，每行一个结果；GROUP BY 每组只剩一行。
- ROW_NUMBER / RANK / DENSE_RANK 的区别 → 1,2,3,4 / 1,2,2,4 / 1,2,2,3；"恰好 N 条"用前者，"前 N 名含并列"用后者。
- 取每组 Top N 怎么写 → 子查询里 `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ... DESC)`，外层 `WHERE rn <= N`。
- 窗口函数能不能写在 WHERE 里 → 不能，必须包一层子查询或 CTE。
- 累计求和为什么每行都是总值 → 少了 `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`，只有 `PARTITION BY` 时窗口是整个分区。
- LAG 需要写 ROWS BETWEEN 吗 → 不需要，只要 PARTITION BY + ORDER BY。
- 环比和同比怎么算 → 环比用 LAG 取上一周期；同比用自连接按日期减一年匹配。
- LAG 有什么坑 → 取的是前第 N 行，日期缺失时会错位。
- 除零怎么处理 → `NULLIF(分母, 0)` 或 `CASE WHEN 分母 > 0 THEN ... END`。
- NULL 怎么判断 → 只能用 `IS NULL` / `IS NOT NULL`，`= NULL` 永远不成立。
- COALESCE 和 IFNULL 的区别 → COALESCE 是标准 SQL、可传多个参数取第一个非空；IFNULL 是 MySQL 方言、只能两个参数。
- 行转列怎么做 → `SUM(CASE WHEN 值 = 'x' THEN 指标 ELSE 0 END)` 配合 GROUP BY。
- 渠道有 100 个怎么行转列 → SQL 不支持动态列名，交回应用层（pandas `pivot`）或 BI 工具。
- UNION 和 UNION ALL 的区别 → UNION 去重、慢；UNION ALL 不去重、快。
- 连续 N 天怎么找 → 日期减行号得常数，同一组内差值相同，再 `GROUP BY 差值 HAVING COUNT(*) >= N`。
