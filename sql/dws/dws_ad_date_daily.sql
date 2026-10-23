-- =============================================================================
-- dws.dws_ad_date_daily —— 全站按天的汇总层
-- =============================================================================
-- 上游：dwd.dwd_ad_event
-- 粒度：一天一行（31 行）
--
-- 这张表存在的理由，不是为了效率（31 行的聚合一瞬间就出来），
-- 而是为了**对账**：
--     dws_ad_campaign_daily 按天汇总后，必须和这张表**完全相等**。
--     不等就说明分组过程中丢了行（比如某天某 campaign 的数据写到了别的分区）。
--     这是整条链路第二道硬约束（第一道是 ODS→DWD 的行数对账）。
--
-- 一个必须写进表的字段：active_campaigns。
--     全站花费掉了 20%，可能是「所有计划都少花 20%」，也可能是
--     「几个大计划停了，其他计划没变」—— 这两种情况的处置完全不同。
--     只看总花费看不出来，必须同时看有多少个计划在跑。
-- =============================================================================

SELECT
    dt,

    -- ---------- 基础指标 ----------
    count(*)                                    AS impressions,
    sum(is_click)                               AS clicks,
    sum(is_conversion)                          AS conversions,
    sum(is_attributed)                          AS attributions,
    sum(is_first_click)                         AS first_clicks,
    count(DISTINCT uid)                         AS reach_users,
    count(DISTINCT campaign_id)                 AS active_campaigns,
    sum(cost)                                   AS cost,

    -- ---------- 比率 ----------
    CAST(sum(is_click) * 1.0 / count(*) AS DOUBLE)   AS ctr,
    CASE WHEN sum(is_click) > 0
         THEN CAST(sum(is_conversion) * 1.0 / sum(is_click) AS DOUBLE)
    END                                         AS cvr,
    CASE WHEN sum(is_conversion) > 0
         THEN CAST(sum(cost) / sum(is_conversion) AS DOUBLE)
    END                                         AS cpa,
    CASE WHEN sum(is_click) > 0
         THEN CAST(sum(cost) / sum(is_click) AS DOUBLE)
    END                                         AS cpc,

    -- ---------- 分布探针 ----------
    percentile_approx(conv_delay_sec, 0.5)      AS conv_delay_p50_sec,
    percentile_approx(cost, 0.5)                AS cost_p50

FROM dwd.dwd_ad_event
GROUP BY dt
