-- =============================================================================
-- lab.dws_creative_daily —— 素材 × 天的汇总层
-- =============================================================================
-- 上游：lab.dwd_user_daily、lab.ods_iaa_acquisition_daily
-- 下游：中台 creative_table、「这个素材该不该砍」的判定
--
-- 粒度：一个 (投放日, creative_id) → 一行
-- 行数：31 天 × 约 200 个素材 ≈ 6,200 行
--
-- -----------------------------------------------------------------------------
-- 为什么素材要单独一张表，不能只放在 dws_campaign_daily 里
-- -----------------------------------------------------------------------------
-- 因为「campaign 该不该停」和「素材该不该换」是两个决策，尺度不同：
--     一个 campaign 挂了 40 个素材。campaign 级 ROI 0.75（合格），
--     完全可能是「3 个素材赚翻 + 37 个素材亏钱」平均出来的。
--     这时候该砍的是那 37 个素材，不是整个 campaign。
--
-- 如果只保留 campaign 级，这个决策永远做不出来 ——
-- 这就是「粒度决定能回答什么问题」的具体例子，而不是抽象原则。
--
-- -----------------------------------------------------------------------------
-- 关于 creative_id 为 NULL 的那一组
-- -----------------------------------------------------------------------------
-- 有约 3% 的流量是渠道直投（Unity/Mintegral 的算法优化位），
-- 平台不暴露具体素材 ID。这一组的 creative_id 是 NULL。
--
-- NULL 在这里的语义是「**不适用**」——不是「未知数据」，而是
-- 「这一类投放本来就没有素材这个概念」。所以：
--     · 保留这一组（否则直投的 spend 和收入会凭空消失，渠道总额对不上）
--     · 但下游按素材排行时必须 WHERE creative_id IS NOT NULL 排除掉
--     · 绝不用 '__unknown__' 这类字符串填充 —— 那会让它看起来像个真实素材，
--       在「Top 20 素材」列表里混进去一条假数据
--
-- 这正是 NULL 三义（没发生 / 不适用 / 未知）里「不适用」的标准处理方式。
-- =============================================================================

WITH monetization AS (
    SELECT
        dt,
        creative_id,
        max(campaign)                                                      AS campaign,
        max(country)                                                       AS country,
        max(app)                                                           AS app,
        count(DISTINCT uid)                                                AS active_users,
        sum(ad_impr)                                                       AS ad_impressions,
        sum(rewarded_views)                                                AS rewarded_views,
        sum(session_cnt)                                                   AS sessions,
        cast(sum(ad_revenue) AS decimal(18,6))                             AS ad_revenue,
        sum(CASE WHEN days_since_install = 0 THEN 1 ELSE 0 END)            AS installs,
        cast(sum(CASE WHEN days_since_install = 0 THEN install_cost ELSE 0 END)
             AS decimal(18,6))                                             AS spend
    FROM lab.dwd_user_daily
    GROUP BY dt, creative_id
),

acquisition AS (
    SELECT
        dt,
        creative_id,
        sum(clicks)                                                        AS clicks,
        sum(buy_impressions)                                               AS buy_impressions
    FROM lab.ods_iaa_acquisition_daily
    GROUP BY dt, creative_id
)

SELECT
    m.dt,
    m.creative_id,
    m.campaign,
    m.country,
    m.app,

    m.installs,
    m.spend,
    m.active_users,
    m.ad_impressions,
    m.rewarded_views,
    m.sessions,
    m.ad_revenue,

    coalesce(a.clicks, 0)                                                  AS clicks,
    coalesce(a.buy_impressions, 0)                                         AS buy_impressions

FROM monetization m
LEFT JOIN acquisition a
       ON m.dt = a.dt
      -- NULL 不能直接用 = 比较：NULL = NULL 返回 NULL（不是 true），
      -- 所以两个 NULL 永远 join 不上，直投那一组的买量数据会整片丢失。
      -- 必须显式用 null-safe 等值。
      AND m.creative_id <=> a.creative_id
