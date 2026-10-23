-- =============================================================================
-- dws.dws_ad_campaign_daily —— 广告计划 × 天的汇总层
-- =============================================================================
-- 上游：dwd.dwd_ad_event（16,468,027 行，31 个 dt 分区）
-- 粒度：一个 campaign 在一天内的所有曝光 → **一行**
-- 行数：675 campaign × 31 天 ≈ 20,925 行（比明细小 786 倍）
--
-- 为什么 DWS 要存「可加指标」而不是「算好的率」：
--     这个表回答的问题是「9 月 21 号 campaign 8867068 花了多少钱、带来几次转化」。
--     这些数字必须能**再往上加**：
--         按天相加 → 一周花费
--         按 campaign 相加 → 全站花费
--     而 CTR / CPA 这类比值**不能相加**，也不能把 31 天的 CTR 直接平均
--     （那样等于给大计划和小计划同样的权重，是报表里最常见的错）。
--
--     所以本表的纪律是：
--         · 基础指标（impressions / clicks / cost …）→ 存，可任意相加
--         · 比率指标（ctr / cpa …）→ 也存，但**只供直接看**，
--           任何再聚合都必须回到基础指标重新算，不许对 ctr 求 avg()
--     这条纪律写在 docs/metrics.md 里，代码不许绕过。
--
-- 为什么要除以 0 的保护：
--     campaign 在一天里可能 0 点击（只有曝光）。CTR=0 是合理值，
--     但 CPA = cost/0 在 Spark 里返回 NULL，不是报错 ——
--     一旦 SUM(cpa) 就会静默少算一批行。所以全部用 CASE 显式处理。
-- =============================================================================

SELECT
    dt,
    campaign_id,

    -- ---------- 基础指标（可加）----------
    count(*)                                            AS impressions,
    sum(is_click)                                       AS clicks,
    sum(is_conversion)                                  AS conversions,
    sum(is_attributed)                                  AS attributions,
    sum(is_first_click)                                 AS first_clicks,
    -- 去重人数：**不可加**。9/21 的 1000 人和 9/22 的 1000 人可能是同一批，
    -- 相加得到 2000 是错的。要「周活」必须回到明细用 count(DISTINCT uid)。
    count(DISTINCT uid)                                 AS reach_users,
    sum(cost)                                           AS cost,
    sum(cost_per_click)                                 AS cost_on_clicks,

    -- ---------- 派生比率（只供直接看，禁止再聚合）----------
    CAST(sum(is_click) * 1.0 / count(*) AS DOUBLE)      AS ctr,
    CASE WHEN sum(is_click) > 0
         THEN CAST(sum(is_conversion) * 1.0 / sum(is_click) AS DOUBLE)
    END                                                 AS cvr,
    CASE WHEN sum(is_conversion) > 0
         THEN CAST(sum(cost) / sum(is_conversion) AS DOUBLE)
    END                                                 AS cpa,
    CASE WHEN sum(is_click) > 0
         THEN CAST(sum(cost) / sum(is_click) AS DOUBLE)
    END                                                 AS cpc,
    CASE WHEN sum(is_conversion) > 0
         THEN CAST(sum(is_attributed) * 1.0 / sum(is_conversion) AS DOUBLE)
    END                                                 AS attribution_rate,

    -- ---------- 质量探针（下游可以据此判断这天这计划能不能用）----------
    -- 转化延迟中位数：如果某天突然从 3.7 天掉到 0.1 天，多半是上游改了埋点
    percentile_approx(conv_delay_sec, 0.5)              AS conv_delay_p50_sec

FROM dwd.dwd_ad_event
GROUP BY dt, campaign_id
