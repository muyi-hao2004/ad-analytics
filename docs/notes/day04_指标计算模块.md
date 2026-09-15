# Day4：指标计算模块

## 核心函数

### 1. df.groupby() — 分组统计
- 作用：按某列（或多列）分组，然后对每组做统计
- 关键参数：
  - `by`：按哪列分组，可多列 `['channel', 'date']`
  - `as_index=False`：分组列保留为普通列（推荐，否则变成行索引）
- 常用聚合函数：`.sum()` `.mean()` `.count()` `.max()` `.min()` `.nunique()`

### 2. .agg() — 多列多种聚合
- 作用：不同列用不同聚合方式，或同一列用多种聚合
- 字典方式：`df.groupby('channel').agg({'impressions': 'sum', 'date': 'nunique'})`
- 命名聚合（推荐）：`agg(total_impressions=('impressions', 'sum'))`，列名清晰

### 3. np.where() — 条件计算（除零保护）
- 语法：`np.where(条件, 成立时值, 不成立时值)`
- 用途：分母为0时返回0，避免inf报错
- 例：`np.where(df['impressions'] > 0, df['clicks']/df['impressions'], 0)`

### 4. .shift() — 计算环比
- 作用：把数据移动一行，取前一行的值
- 例：`df['cost_prev'] = df['cost'].shift(1)`
- 环比 = (今天 - 昨天) / 昨天
- 注意：必须先按日期排序！

### 5. .rolling() — 移动平均
- 作用：滚动窗口计算平均，平滑数据看长期趋势
- 关键参数：`window=7`（窗口大小）、`min_periods=1`（前几天也开始算）
- 例：`df['cost_ma7'] = df['cost'].rolling(window=7, min_periods=1).mean()`

### 6. .rank() — 排名
- 作用：给数据排名，找出Top N
- 关键参数：`ascending=False`（从大到小）、`method='min'`（并列取最小排名）

## 广告核心指标公式
- CTR（点击率）= 点击 / 曝光
- CVR（转化率）= 转化 / 点击
- CPC（单次点击成本）= 花费 / 点击
- CPA（单次转化成本）= 花费 / 转化
- ROI（投资回报率）= (收入 - 花费) / 花费

## 关键原则
- 整体指标 = 先加总再相除，不是每行指标的平均
- 除零必须用 np.where 保护
- 算环比前必须按日期排序

## metrics.py 7个函数
1. `calculate_basic_metrics()` — 给每行添加ctr/cvr/cpc/cpa列
2. `calculate_overall_metrics()` — 整体汇总指标
3. `group_by_dimension()` — 按维度（渠道/计划等）分组汇总+计算指标
4. `group_by_date()` — 按日期分组
5. `calculate_daily_change()` — 计算环比
6. `calculate_moving_average()` — 计算移动平均
7. `rank_by_metric()` — 按指标排序取Top N
