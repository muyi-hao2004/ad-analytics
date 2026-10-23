-- =============================================================================
-- dwd.dim_campaign —— campaign 维表
-- =============================================================================
-- 为什么这份数据只有一张真正的维表：
--     Criteo 归因数据集的 22 个字段里，唯一带「主体」语义的就是 campaign（广告计划）。
--     uid 是自然人，粒度太细，不是维表；cat1~cat9 我原本以为是 campaign 的属性，
--     但实测 675 个 campaign **全部**有多个 cat 组合（最多 12 万种），
--     所以它们是「每次曝光的上下文特征」，不是 campaign 的属性。
--
-- 但 cat5 是个例外：
--     cat5 只有 51 个取值，而平均每个 campaign 只有 1.1 个 cat5 取值 ——
--     说明 cat5 基本由 campaign 决定。这是我从数据反推出来的「维度属性」，
--     判断依据是「每个 campaign 的去重取值个数」，不是猜的。
--     （判断方法写在 build_dwd.py 的打印里，面试可以直接讲这个判据。）
--
-- ⚠️ 这份维表用的是**全量 31 天**的统计，属于「期末快照」。
--     用它做 BI 报表没问题（报表本来就是月底跑）。
--     但**绝对不能**把它 join 到明细再去训练模型：
--     那样 dt=09-21 的行会带上 10-21 才发生的统计量，就是「未来函数泄露」。
--     训练数据工程里这个坑叫 point-in-time correctness（特征必须只用当时已知的信息）。
--     真要做训练集，得按天滚出版本，见 docs/data_contract.md 里的说明。
-- =============================================================================

WITH cat5_stat AS (
    -- 先数出「每个 campaign 的每个 cat5 各出现多少次」
    SELECT
        campaign AS campaign_id,
        cat5,
        COUNT(*) AS cnt
    FROM ods.ods_ad_log
    GROUP BY campaign, cat5
),

cat5_pick AS (
    -- 再取众数（出现最多的那个 cat5）作为主属性，同时记下这个 campaign 有几个 cat5 取值。
    -- variants 就是「维度漂移指标」：> 1 说明这个 campaign 的属性在 31 天里变过。
    SELECT
        campaign_id,
        cat5,
        ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY cnt DESC, cat5) AS rn,
        COUNT(*)     OVER (PARTITION BY campaign_id) AS variants
    FROM cat5_stat
),

base AS (
    SELECT
        campaign                        AS campaign_id,
        COUNT(*)                        AS impressions,
        SUM(click)                      AS clicks,
        SUM(conversion)                 AS conversions,
        SUM(attribution)                AS attributions,
        SUM(cost)                       AS total_cost,
        MIN(dt)                         AS first_dt,
        MAX(dt)                         AS last_dt,
        COUNT(DISTINCT dt)              AS active_days,
        COUNT(DISTINCT cat3)            AS cat3_cardinality,
        COUNT(DISTINCT uid)             AS reach_users
    FROM ods.ods_ad_log
    GROUP BY campaign
)

SELECT
    b.campaign_id,
    b.first_dt,
    b.last_dt,
    b.active_days,
    b.impressions,
    b.clicks,
    b.conversions,
    b.attributions,
    b.total_cost,
    b.reach_users,
    b.cat3_cardinality,
    p.cat5                              AS primary_cat5,
    p.variants                          AS cat5_variants,
    -- 派生比率放在维表里，是为了让下游不用每次重算、也不会各算各的口径。
    -- 口径定义见 docs/data_contract.md，代码不许手写公式。
    CAST(b.clicks      AS DOUBLE) / b.impressions AS ctr,
    CAST(b.conversions AS DOUBLE) / b.clicks      AS cvr,
    b.total_cost / b.clicks                       AS cpc,
    b.total_cost / b.conversions                  AS cpa
FROM base b
LEFT JOIN cat5_pick p
       ON p.campaign_id = b.campaign_id AND p.rn = 1
