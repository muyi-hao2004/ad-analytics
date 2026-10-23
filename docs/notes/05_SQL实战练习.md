# SQL 实战练习

本篇覆盖：把两套手写练习按题型重新编排，去掉流水号，按"分组聚合 → 多表关联 → 条件分支与行列转换 → 窗口取 TopN → 连续 N 天 → 留存与漏斗 → 同比环比 → 集合与空值 → 综合"由浅入深排列。每题给出场景、标准答案、易错点。语法规则见《04 SQL 基础与窗口函数》，本篇只负责"看到题目能不能写出来"。

练习建议：不要看答案背。把题目抄下来手写一遍，跑不通再对照。手写和"看懂"是两回事。

---

## 一、练习用表

两套练习各有一组表，结构如下，后文按题目需要引用。

```sql
-- 广告日报事实表（渠道粒度）
ad_daily(channel, stat_date, cost, impressions, clicks, conversions)

-- 用户行为明细表（用户粒度）
user_event(user_id, event_date, event_type)   -- 'impression' / 'click' / 'convert'

-- 广告事实表（活动粒度，本地练习库 ads，1069 行）
ads(channel, date, campaign, impressions, clicks, conversions, cost)

-- 活动维度表（campaigns，9 行）
campaigns(name, channel, owner, status)
```

两条贯穿全篇的约定：

- 表别名统一用 `a`（事实表）、`c`（维度表）；
- 指标一律"先加总再相除"，不要对行级比率求平均。

---

## 二、题型：分组聚合

最基础的一类，面试开场题。考的是 GROUP BY 规则、聚合函数选择、别名使用。

### 2.1 按维度汇总多个指标

**题目**：按渠道统计总曝光、总点击、平均花费，按总曝光降序。

```sql
SELECT channel,
       SUM(impressions) AS total_im,
       SUM(clicks)      AS total_cl,
       AVG(cost)        AS avg_cost
FROM ads
GROUP BY channel
ORDER BY total_im DESC;
```

**要点**：

- 分组列 `channel` 必须出现在 SELECT 里，否则结果无法辨认是哪一组；
- 别名前后保持一致，ORDER BY 可以直接引用 SELECT 的别名；
- `AVG(cost)` 是"单条记录的平均花费"，业务含义有限。衡量投放效率通常用 CPM（花费/曝光×1000）或 CPC（花费/点击），这类比率指标要"先加总再相除"。

**易错点**：

| 错误 | 后果 |
|---|---|
| `SELECT channel, impressions, SUM(clicks)` | 非聚合列不在 GROUP BY 里，`ONLY_FULL_GROUP_BY` 下直接报错 |
| `ORDER BY total_im DESC` 写成 `ORDER BY impressions` | 分组后 `impressions` 明细列已消失，报未知列 |
| 用 `COUNT(cost)` 代替 `COUNT(*)` 统计条数 | cost 有 NULL 时会少算 |

### 2.2 日期范围 + 前置过滤

**题目**：统计 8 月各渠道总点击，要求曝光大于 0，按点击降序。

```sql
SELECT channel, SUM(clicks) AS total_cl
FROM ads
WHERE date >= '2026-08-01' AND date < '2026-09-01'
  AND impressions > 0
GROUP BY channel
ORDER BY total_cl DESC;
```

**要点**：

- 日期用左闭右开：`>= 起` + `< 止`。写成 `<= '2026-08-31'` 会漏掉当天带时分秒的数据；
- `impressions > 0` 放在 WHERE 而不是 HAVING——它在分组前就能过滤掉无效行，参与聚合的数据更少；
- 如果条件里要判断聚合结果（比如"总点击大于 1 万"），才用 HAVING。

**易错点**：把 `WHERE` 写在 `GROUP BY` 后面。书写顺序固定为 SELECT → FROM → WHERE → GROUP BY → HAVING → ORDER BY → LIMIT，顺序错了直接语法报错。

### 2.3 转化率：先加总再相除

**题目**：统计 8 月以来各渠道的总点击、总转化与转化率，按转化率降序。

```sql
SELECT channel,
       SUM(clicks)      AS 总点击,
       SUM(conversions) AS 总转化,
       ROUND(SUM(conversions) / SUM(clicks) * 100, 2) AS 转化率
FROM ads
WHERE date >= '2026-08-01' AND clicks > 0
GROUP BY channel
ORDER BY 转化率 DESC;
```

**要点**：

- 转化率 = 总转化 / 总点击，分子分母都先 SUM；
- `clicks > 0` 防除零；
- 别名用中文在 MySQL 里合法，但跨库移植性差，生产代码建议用英文。

**易错点**：写成 `AVG(conversions / clicks)`。那是对"每行转化率"求平均，每个行权重相同，大流量行的权重被稀释，结果与真实转化率不一致。这是比率类指标最经典的错误。

### 2.4 COUNT 的三种写法

**题目**：分别统计总行数、有花费的记录数、不同渠道数。

```sql
SELECT COUNT(*)                AS rows_all,
       COUNT(cost)             AS rows_with_cost,
       COUNT(DISTINCT channel) AS channel_cnt
FROM ads;
```

| 写法 | 数什么 |
|---|---|
| `COUNT(*)` | 行数，NULL 也算 |
| `COUNT(列)` | 该列非空个数 |
| `COUNT(DISTINCT 列)` | 去重后的非空个数 |

不分组时数整表，分组后数每组。

---

## 三、题型：多表关联

### 3.1 维度过滤 + 分组

**题目**：统计每个负责人（owner）名下 active 活动的总花费，按总花费降序。

```sql
SELECT c.owner, SUM(a.cost) AS total_cost
FROM ads a
INNER JOIN campaigns c ON a.campaign = c.name
WHERE c.status = 'active'
GROUP BY c.owner
ORDER BY total_cost DESC;
```

**要点（JOIN 三要素）**：

1. FROM 主表 `ads a`；
2. JOIN 维度表 `campaigns c`；
3. ON 关联条件 `a.campaign = c.name`。

同名字段加表前缀，`a.channel = c.channel`；不同名则各写各的。

### 3.2 关联键选错导致数据放大

**题目**：同上，但最初的写法是用 `channel` 关联：

```sql
-- 错误示范
SELECT c.owner, SUM(a.cost) AS total_cost
FROM ads a
INNER JOIN campaigns c ON a.channel = c.channel
GROUP BY c.owner;
```

**现象**：Google 有 2 个活动，这 2 行都匹配上了 Google 的全部事实行，于是 Google 的花费被计算了两遍，总额虚高。

**原因**：`channel` 在维度表里不唯一（一对多），JOIN 时左表每一行会与右表所有同渠道行做笛卡尔组合。

**正确做法**：用能唯一确定一行的键关联。活动名 `name` 与事实表的 `campaign` 一一对应：

```sql
ON c.name = a.campaign
```

**通用检查**：JOIN 前后对比行数。行数暴增只有三种可能——关联键不唯一、关联条件写漏、两边都存在重复键。关联前先确认维度表的键唯一（`SELECT COUNT(*), COUNT(DISTINCT name) FROM campaigns`）。

### 3.3 LEFT JOIN 保留主表

**题目**：列出所有活动及其花费，没有投放记录的活动也要显示（花费记 0）。

```sql
SELECT c.name AS campaign,
       COALESCE(SUM(a.cost), 0) AS total_cost
FROM campaigns c
LEFT JOIN ads a ON a.campaign = c.name
GROUP BY c.name
ORDER BY total_cost DESC;
```

**要点**：

- 主表放左边（维度表全量），事实表放右边；
- 右表没匹配上时，`SUM` 返回 NULL，用 `COALESCE(..., 0)` 兜底；
- 如果写成 `WHERE a.cost IS NOT NULL`，就把没投放的活动过滤掉了，LEFT JOIN 退化成 INNER JOIN。

**易错点汇总**：

| 错误 | 结果 |
|---|---|
| 右表过滤条件写在 WHERE | 退化为 INNER JOIN，左表独有行丢失 |
| 忘记 COALESCE | 结果里出现 NULL，下游计算出错 |
| 用 `COUNT(*)` 统计有投放的活动数 | LEFT JOIN 后每行至少 1 条，NULL 行也被算成 1 |

---

## 四、题型：条件分支与行列转换

### 4.1 CASE WHEN 分组统计

**题目**：把点击量 ≥ 3000 的记录标为"高点击"，其余标为"低点击"，按渠道和档位统计条数。

```sql
SELECT channel,
       CASE WHEN clicks >= 3000 THEN '高点击' ELSE '低点击' END AS click_level,
       COUNT(*) AS cnt
FROM ads
GROUP BY channel,
         CASE WHEN clicks >= 3000 THEN '高点击' ELSE '低点击' END
ORDER BY channel, cnt DESC;
```

**要点**：

- CASE WHEN 从上到下匹配，命中即返回，条件要从严格到宽松；
- 没有 ELSE 且全不匹配时返回 NULL，会多出一个 NULL 分组；
- 条件聚合也基于 CASE：`SUM(CASE WHEN 条件 THEN 1 ELSE 0 END)` 等价于"满足条件的行数"。

**易错点**：`GROUP BY click_level` 报错。GROUP BY 在 SELECT 之前执行，别名还不存在，必须原样复制 CASE 表达式。

### 4.2 行转列（透视）

**题目**：把"每渠道每天一行"转成"日期为行、渠道为列"的花费表。

```sql
SELECT stat_date,
       SUM(CASE WHEN channel = 'Facebook' THEN cost ELSE 0 END) AS facebook_cost,
       SUM(CASE WHEN channel = 'Google'   THEN cost ELSE 0 END) AS google_cost,
       SUM(CASE WHEN channel = 'TikTok'   THEN cost ELSE 0 END) AS tiktok_cost
FROM ad_daily
GROUP BY stat_date
ORDER BY stat_date;
```

**要点**：列名是写死的，渠道有几个就要写几个 CASE。`ELSE 0` 不能省，否则不匹配的行返回 NULL，SUM 虽然会忽略 NULL，但全不匹配的日期会得到 NULL 而不是 0。

### 4.3 追问：渠道有 100 个怎么办

**SQL 做不到动态列名。** 列必须在编译时确定，这是 SQL 的固有边界。三条可行路径：

1. 应用层做——把明细查出来，用 pandas 的 `pivot` / `pivot_table`；
2. BI 工具做——Tableau、Superset 的透视表功能；
3. 确实要在 SQL 里动态生成，得用预处理语句拼字符串（`GROUP_CONCAT` 拼出 CASE 列表再 `PREPARE`），可维护性差，不推荐。

面试时答"这是 SQL 的固有边界"，比硬写 100 个 CASE 得分高。

---

## 五、题型：窗口取 TopN 与每组最新一条

出场率最高的一类。核心句式只有一句：**先排名，再筛排名**。

### 5.1 每组 Top 3

**题目**：每个渠道消耗最高的 3 天是哪几天？

```sql
SELECT channel, stat_date, cost
FROM (
    SELECT channel, stat_date, cost,
           ROW_NUMBER() OVER (PARTITION BY channel ORDER BY cost DESC) AS rn
    FROM ad_daily
) t
WHERE rn <= 3;
```

**要点**：

- `PARTITION BY channel` 决定"每组"是什么；
- `ORDER BY cost DESC` 决定"Top"按什么排，降序才有"最高 = 1"；
- 子查询必须起别名 `t`；
- 也可以写成 CTE：`WITH ranked AS (...) SELECT ... FROM ranked WHERE rn <= 3`。

**易错点**：

| 错误写法 | 为什么错 |
|---|---|
| `ORDER BY cost DESC LIMIT 3` | 这是全局 Top 3，不是每渠道各 3 条 |
| 先 `GROUP BY` 再 `ORDER BY` | 聚合后明细分层没了，取不到"哪一天" |
| 窗口函数直接写在 WHERE 里 | 语法不允许，必须包一层 |

### 5.2 每组取最新一条完整记录

**题目**：每个渠道取最近一天的完整记录（含 cost）。

```sql
SELECT channel, stat_date, cost
FROM (
    SELECT channel, stat_date, cost,
           ROW_NUMBER() OVER (PARTITION BY channel ORDER BY stat_date DESC) AS rn
    FROM ad_daily
) t
WHERE rn = 1;
```

**为什么不能用 GROUP BY + MAX**：

```sql
-- 错误
SELECT channel, MAX(stat_date), cost FROM ad_daily GROUP BY channel;
```

`MAX(stat_date)` 能拿到最新日期，但 `cost` 拿到的是组内**某一行的随机值**，不保证是那一天的花费。MySQL 5.7 起默认开启 `ONLY_FULL_GROUP_BY`，这条语句会直接报错；关闭该模式则静默返回错误数据，更危险。

**通用结论**："取最新/最大那条的其它字段"= 窗口函数，不是 GROUP BY + MAX。

### 5.3 全局 Top 3（不带分组）

**题目**：全部记录中 CVR 最高的 3 条。

```sql
SELECT channel, date, campaign, cvr
FROM (
    SELECT channel, date, campaign, conversions / clicks AS cvr,
           ROW_NUMBER() OVER (ORDER BY conversions / clicks DESC) AS rn
    FROM ads
    WHERE clicks > 0
) t
WHERE rn <= 3;
```

**要点**：

- 去掉 `PARTITION BY` 就是全局排名，整个结果集一个窗口；
- 计算列 `cvr` 和窗口函数可以放在同一层 SELECT，窗口的 ORDER BY 直接引用表达式；
- `WHERE clicks > 0` 防除零，放在内层，排名前就把脏数据排除。

### 5.4 每组 Top 2（按转化数）

**题目**：每个渠道转化数最高的 2 条记录。

```sql
SELECT channel, date, campaign, conversions, rn
FROM (
    SELECT channel, date, campaign, conversions,
           ROW_NUMBER() OVER (PARTITION BY channel ORDER BY conversions DESC) AS rn
    FROM ads
) t
WHERE rn <= 2;
```

**易错点**：误以为窗口函数要和 GROUP BY 一起用。窗口函数**不需要** GROUP BY，它自己保留每一行。

### 5.5 排名三兄弟的选择

同一组数据 `clicks = 100, 90, 90, 80`：

| clicks | ROW_NUMBER | RANK | DENSE_RANK |
|---|---|---|---|
| 100 | 1 | 1 | 1 |
| 90 | 2 | 2 | 2 |
| 90 | 3 | 2 | 2 |
| 80 | 4 | 4 | 3 |

- `ROW_NUMBER()`：不并列，硬编号，结果条数可控；
- `RANK()`：并列同号，后面**跳号**（80 跳过 3 直接第 4）；
- `DENSE_RANK()`：并列同号，后面**不跳**（80 接着第 3）。dense 意为密集，编号不断。

**业务选择**：名额固定（"取 3 条"）用 `ROW_NUMBER`；"前 3 名，含并列"用 `DENSE_RANK`。

---

## 六、题型：连续 N 天

区分度最高的一类，思路固定。

**题目**：找出连续 3 天及以上 CPA > 100 的渠道，及其起止日期。

**核心技巧：连续日期 − 递增行号 = 常数。**

- 日期 1、2、3，行号 1、2、3 → 差值都是 0 → 同一组；
- 日期 1、2、4，行号 1、2、3 → 差值 0、0、1 → 断开。

```sql
WITH daily AS (
    SELECT channel, stat_date,
           SUM(cost)         AS cost,
           SUM(conversions)  AS conv
    FROM ad_daily
    GROUP BY channel, stat_date
),
flagged AS (
    SELECT channel, stat_date,
           CASE WHEN cost / NULLIF(conv, 0) > 100 THEN 1 ELSE 0 END AS is_over
    FROM daily
),
numbered AS (
    SELECT channel, stat_date,
           ROW_NUMBER() OVER (PARTITION BY channel ORDER BY stat_date) AS rn
    FROM flagged
    WHERE is_over = 1
),
grp AS (
    SELECT channel, stat_date,
           DATE_SUB(stat_date, INTERVAL rn DAY) AS grp_key
    FROM numbered
)
SELECT channel, grp_key,
       COUNT(*)       AS days,
       MIN(stat_date) AS start_date,
       MAX(stat_date) AS end_date
FROM grp
GROUP BY channel, grp_key
HAVING COUNT(*) >= 3;
```

**要点**：

- 先在 `numbered` 里只保留满足条件的日期，行号才连续；如果不过滤，断点会被行号"填平"；
- 日期减行号得到分组键，同一段连续日期共用一个键；
- `HAVING COUNT(*) >= 3` 筛出长度达标的段；
- `PARTITION BY channel` 保证不同渠道的行号各自独立。

**易错点**：

1. 窗口函数不能直接套在 `DATE_SUB()` 里（MySQL 不允许嵌套），必须先在 CTE 里算出 `rn`，下一层再用；
2. Hive / Spark SQL 没有 `INTERVAL n DAY` 的写法，用 `date_sub(stat_date, rn)`；
3. 忘了过滤 `is_over = 1`，行号跨越了不满足条件的日期，会把两段不连续的日期并成一段；
4. `NULLIF(conv, 0)` 防除零，否则 conv = 0 时整行结果为 NULL，条件判断失效。

---

## 七、题型：留存与漏斗

### 7.1 次日留存率

**题目**：算每天新增用户的次日留存率与 7 日留存率。

```sql
WITH first_day AS (
    SELECT user_id, MIN(event_date) AS first_date
    FROM user_event
    GROUP BY user_id
),
retention AS (
    SELECT f.first_date,
           f.user_id,
           MAX(CASE WHEN e.event_date = DATE_ADD(f.first_date, INTERVAL 1 DAY)
                    THEN 1 ELSE 0 END) AS d1,
           MAX(CASE WHEN e.event_date = DATE_ADD(f.first_date, INTERVAL 7 DAY)
                    THEN 1 ELSE 0 END) AS d7
    FROM first_day f
    LEFT JOIN user_event e ON f.user_id = e.user_id
    GROUP BY f.first_date, f.user_id
)
SELECT first_date,
       COUNT(*)                     AS new_users,
       SUM(d1)                      AS d1_retained,
       ROUND(SUM(d1) / COUNT(*), 4) AS d1_rate,
       ROUND(SUM(d7) / COUNT(*), 4) AS d7_rate
FROM retention
GROUP BY first_date
ORDER BY first_date;
```

**要点**：

- `first_day` 先算出每个用户的首日，这是"新增"的定义；
- 用 `MAX(CASE WHEN ... THEN 1 ELSE 0 END)` 把"当天是否来过"压成 0/1 标记，避免一个用户多条记录导致重复计数；
- 最后按首日分组，分母是当天新增人数。

**追问一：为什么必须用 LEFT JOIN？**

写成 INNER JOIN 时，"次日没来"的用户根本没有匹配行，会被直接丢掉，分母变小，留存率虚高。必须保留全部新增用户，没来的记 0。

**追问二：分母为什么是"当天新增数"而不是"当天活跃数"？**

留存的定义就是"新增用户在第 N 天是否还来"。分母换了，指标含义就变了。面试时先把定义讲清楚再写 SQL，比直接写代码得分高。

### 7.2 漏斗转化（UV 口径）

**题目**：算某一天 曝光 → 点击 → 转化 的 UV 与转化率。

```sql
SELECT
    COUNT(DISTINCT CASE WHEN event_type = 'impression' THEN user_id END) AS uv_imp,
    COUNT(DISTINCT CASE WHEN event_type = 'click'      THEN user_id END) AS uv_clk,
    COUNT(DISTINCT CASE WHEN event_type = 'convert'    THEN user_id END) AS uv_cvt,
    ROUND(COUNT(DISTINCT CASE WHEN event_type = 'click'   THEN user_id END)
        / NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'impression' THEN user_id END), 0), 4) AS ctr,
    ROUND(COUNT(DISTINCT CASE WHEN event_type = 'convert' THEN user_id END)
        / NULLIF(COUNT(DISTINCT CASE WHEN event_type = 'click'      THEN user_id END), 0), 4) AS cvr
FROM user_event
WHERE event_date = '2026-10-09';
```

**要点**：

- `COUNT(DISTINCT CASE WHEN ...)` 一次扫表算完所有口径，不要写三个子查询或多次自连接，性能差一大截；
- 漏斗用 UV（去重人数）还是 PV（次数），取决于业务定义，两者不可混用；
- 分母用 `NULLIF(..., 0)` 兜底，某一步为 0 时返回 NULL 而不是报错。

**易错点**：`CASE WHEN event_type = 'click' THEN user_id END` 少了 ELSE，不匹配时返回 NULL，`COUNT(DISTINCT ...)` 恰好忽略 NULL，所以能正确工作——这是有意利用 NULL 的特性，不是漏写。

---

## 八、题型：同比环比

### 8.1 日环比

**题目**：算每个渠道每天的消耗环比（对比前一天）。

```sql
SELECT channel, stat_date, cost,
       LAG(cost, 1) OVER (PARTITION BY channel ORDER BY stat_date) AS prev_cost,
       ROUND(
           (cost - LAG(cost, 1) OVER (PARTITION BY channel ORDER BY stat_date))
           / NULLIF(LAG(cost, 1) OVER (PARTITION BY channel ORDER BY stat_date), 0)
       , 4) AS dod_rate
FROM ad_daily;
```

**要点**：

- 环比 =（本期 − 上期）/ 上期；
- `PARTITION BY channel` 保证不会拿 A 渠道的昨天去比 B 渠道的今天；
- `ORDER BY stat_date` 决定"上一行"是哪一行；
- 第一天 `LAG` 返回 NULL，比率为 NULL，需要的话在外层加 `WHERE prev_cost IS NOT NULL`。

### 8.2 先聚合再取上一行

**题目**：先算每天总消耗，再算日环比。

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

**要点**：聚合 + 窗口嵌套。执行顺序是先 GROUP BY 聚合，再在聚合结果上做窗口计算，所以 `LAG(SUM(cost), 1)` 可以直接写在同一个 SELECT 里。

### 8.3 同比（自连接）

**题目**：算每个月销售额的同比增长率。

```sql
SELECT a.month, a.amount AS 今年, b.amount AS 去年,
       ROUND((a.amount - b.amount) / b.amount * 100, 2) AS 同比
FROM sales a
LEFT JOIN sales b
    ON b.month = DATE_SUB(a.month, INTERVAL 1 YEAR);
```

**要点**：JOIN 条件用 `DATE_SUB(a.month, INTERVAL 1 YEAR)` 把今年每个月连到去年同月。用 LEFT JOIN 是为了保留去年没有数据的月份（同比值为 NULL，而不是整行消失）。

**环比 vs 同比**：

| | 环比 | 同比 |
|---|---|---|
| 对比对象 | 上一个周期（今天 vs 昨天） | 去年同期（今年 8 月 vs 去年 8 月） |
| 实现 | `LAG` | 自连接，日期差一年 |
| 数据缺失时 | 会错位，需改用自连接 | 天然按日期匹配，不受影响 |

---

## 九、题型：空值、去重与集合

这一组题不难，但面试常用来检查基本功。

### 9.1 去重与去重计数

```sql
SELECT DISTINCT channel FROM ads;          -- 列出不重复的 5 个值
SELECT COUNT(DISTINCT channel) FROM ads;   -- 返回 1 个数字
```

**要点**：`DISTINCT` 作用于后面所有列。`SELECT DISTINCT channel, date` 是对组合去重，不是只对 channel 去重。

### 9.2 上下拼接

```sql
SELECT channel FROM ads WHERE channel = 'TikTok'
UNION ALL
SELECT channel FROM ads WHERE channel = 'Google';
```

| 写法 | 去重 | 性能 |
|---|---|---|
| `UNION` | 是 | 慢（要排序/哈希） |
| `UNION ALL` | 否 | 快 |

**规则**：上下两个查询列数必须一致，类型兼容。JOIN 是左右拼（加列），UNION 是上下拼（加行）。

### 9.3 IN 子查询

**题目**：查"李四"负责的所有活动的投放数据。

```sql
SELECT *
FROM ads
WHERE campaign IN (SELECT name FROM campaigns WHERE owner = '李四');
```

**要点**：子查询返回单列多行。若子查询结果里含 NULL，`NOT IN` 会整体失效（`x NOT IN (1, NULL)` 结果是 UNKNOWN，永远不成立），需要先过滤 NULL。

### 9.4 空值兜底

**题目**：列出所有活动的花费，没有记录的显示 0。

```sql
SELECT c.name,
       COALESCE(SUM(a.cost), 0) AS total_cost
FROM campaigns c
LEFT JOIN ads a ON a.campaign = c.name
GROUP BY c.name
ORDER BY COALESCE(SUM(a.cost), 0) DESC;
```

**要点**：

- `COALESCE` 是标准 SQL，可传多个参数，返回第一个非 NULL；
- `IFNULL` 是 MySQL 方言，只能两个参数；
- 排序时也要 COALESCE，否则 NULL 在 MySQL 升序里排最前、降序里排最后，顺序不符合直觉。

---

## 十、题型：综合（JOIN + 聚合 + 窗口）

**题目**：每个负责人名下花费最高的活动是哪个，花费多少？

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

**要点**：

- 需求问的是"总花费"，必须先 `SUM + GROUP BY` 聚合到活动粒度，再排名；
- 窗口函数和 GROUP BY 可以同用，执行顺序是"先聚合、再开窗"；
- 窗口的 ORDER BY 里直接写聚合表达式 `SUM(b.cost)`，因为此时明细列已经不存在；
- 用 LEFT JOIN 保留没有投放记录的活动（花费为 NULL，排名时可能排最后）；
- 子查询起别名，外层才能用 `rn` 过滤。

**易错点**：

1. 直接在明细层排名，得到的是"单条记录花费最高的活动"，不是"总花费最高的活动"；
2. `ORDER BY b.cost` 在聚合后报错，必须用 `SUM(b.cost)` 或别名；
3. 忘了 `GROUP BY a.owner, a.name`，`a.name` 不在分组里会报错。

---

## 十一、易错点总表

把两套练习里踩过的坑合并，考前过一遍。

| # | 坑 | 正确做法 |
|---|---|---|
| 1 | WHERE 写在 GROUP BY 之后 | 子句顺序固定：SELECT → FROM → WHERE → GROUP BY → HAVING → ORDER BY → LIMIT |
| 2 | ORDER BY 引用分组后已消失的明细列 | 用聚合别名或聚合表达式 |
| 3 | GROUP BY 里写 SELECT 别名 | 原样复制 CASE WHEN 表达式 |
| 4 | HAVING 里写 SELECT 别名 | 写完整聚合表达式 `HAVING SUM(clicks) > 100` |
| 5 | 关联键用一对多的字段 | 用唯一键（活动名 / campaign_id），JOIN 前后核对行数 |
| 6 | LEFT JOIN 的右表条件写在 WHERE | 条件写进 ON，否则退化为 INNER JOIN |
| 7 | 除零报错或结果为 NULL | `NULLIF(分母, 0)` 或 `CASE WHEN 分母 > 0 THEN ...` |
| 8 | 整数除法 `1/2 = 0` | 乘 `1.0` 或 `CAST(... AS DECIMAL)` |
| 9 | 比率用 `AVG(分子/分母)` | 必须 `SUM(分子) / SUM(分母)` |
| 10 | `COUNT(列)` 漏算 NULL 行 | 统计行数用 `COUNT(*)` |
| 11 | 窗口函数直接写在 WHERE 里 | 包一层子查询或 CTE，外层筛选 |
| 12 | 分组 TopN 用 `ORDER BY ... LIMIT N` | 加 `PARTITION BY`，先排名再筛 `rn <= N` |
| 13 | 用 `GROUP BY + MAX` 取最新一条 | 用 `ROW_NUMBER() ... WHERE rn = 1` |
| 14 | 累计求和每行都是总值 | 补 `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` |
| 15 | 环比第一行是 NULL 却参与计算 | 外层过滤 `prev_cost IS NOT NULL` |
| 16 | `LAG` 在日期缺失时错位 | 日期不连续时改用自连接按日期匹配 |
| 17 | `BETWEEN` 日期范围漏掉当天末尾 | 用 `< 次日 00:00:00` |
| 18 | `= NULL` 判空 | 只能用 `IS NULL` / `IS NOT NULL` |
| 19 | `UNION` 白白去重 | 不需要去重就用 `UNION ALL` |
| 20 | 窗口函数嵌套进 `DATE_SUB()` | 先在 CTE 里算出行号，下一层再用 |

---

## 十二、题型与行业无关

广告只是外壳。下面 7 类题型在电商、内容、风控、出行、游戏里一模一样，只是换了表名和指标名。

| 广告 | 电商 / 交易 | 内容 / 短视频 | 风控 |
|---|---|---|---|
| 曝光 impression | 商品曝光 / 浏览 | 视频曝光 | 申请提交 |
| 点击 click | 点击 / 加购 | 播放 / 点赞 | 审核通过 |
| 转化 conversion | 下单 / 支付 | 关注 / 付费 | 放款 |
| CTR = 点击 / 曝光 | 点击率 | 播放完成率 | 通过率 |
| CPA = 消耗 / 转化 | CAC = 营销费 / 新客 | 单粉成本 | 单客获客成本 |
| 分组维度：渠道 | 品类 / 店铺 / 地区 | 作者 / 内容标签 | 产品 / 渠道 |
| 留存 | 复购率 | DAU 留存 | — |

**完全不需要翻译的**：TopN、取最新一条、连续 N 天、行转列、环比。这几类在任何行业题目里写法完全相同。

所以练这 7 类题的收益与最终去哪个行业无关。

---

## 十三、面试速查

- 30 秒写出"每渠道 Top3" → `ROW_NUMBER() OVER (PARTITION BY channel ORDER BY cost DESC)` 包一层，外层 `rn <= 3`。
- 取每组最新一条怎么写 → 同上，`ORDER BY stat_date DESC`，外层 `rn = 1`。
- 为什么不用 GROUP BY + MAX 取最新一条 → MAX 只保证日期对，其它字段取到组内随机行，且 `ONLY_FULL_GROUP_BY` 下直接报错。
- "恰好 N 条"和"前 N 名"分别用什么 → 前者 `ROW_NUMBER`，后者 `DENSE_RANK`。
- 连续 N 天怎么找 → 过滤出满足条件的日期，日期减行号得常数，同组内 `COUNT(*) >= N`。
- 留存为什么必须 LEFT JOIN → INNER JOIN 会丢掉"次日没来"的用户，分母变小，留存率虚高。
- 漏斗怎么一次算完 → `COUNT(DISTINCT CASE WHEN event_type = 'x' THEN user_id END)`。
- 行转列怎么做 → `SUM(CASE WHEN 维度 = '值' THEN 指标 ELSE 0 END)` 配合 GROUP BY。
- 100 个渠道怎么行转列 → SQL 不支持动态列，交给 pandas pivot 或 BI 工具。
- 环比和同比分别怎么实现 → 环比用 LAG 取上一行；同比用自连接按日期减一年。
- LAG 什么时候会错 → 日期不连续时，取到的是"上一行"而不是"昨天"。
- 比率指标最容易犯什么错 → 对行级比率求平均，正确做法是 `SUM(分子)/SUM(分母)`。
- JOIN 后金额翻倍是什么原因 → 关联键一对多，左表每行匹配了右表多行。
- LEFT JOIN 后右表条件写哪里 → 写 ON；写 WHERE 会退化成 INNER JOIN。
- 日期范围怎么写不漏数据 → 左闭右开，`>= 起` 且 `< 次日零点`。
