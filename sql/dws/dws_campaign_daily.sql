-- =============================================================================
-- lab.dws_campaign_daily —— 渠道 × 天的汇总层（买量侧 + 变现侧合并）
-- =============================================================================
-- 上游：lab.dwd_user_daily（变现）、lab.ods_iaa_acquisition_daily（买量）
-- 下游：中台 daily_detail、sub_channel_table、渠道 ROI 排行、日趋势
--
-- 粒度：一个 (投放日, 国家, campaign, sub_channel) → 一行
-- 行数：31 天 × 5 campaign × 约 2.2 个 sub_channel ≈ 340 行
--
-- -----------------------------------------------------------------------------
-- 为什么这张表必须把两条轨拼在一起
-- -----------------------------------------------------------------------------
-- metrics.yaml 的 12 个指标横跨两条轨：
--     买量侧：cpi / buy_ctr / buy_cvr / buy_ecpm   粒度是「渠道 × 天」
--     变现侧：d7_roi / d7_retention / ecpm / ...   粒度是「cohort × 第 N 天」
--
-- 只拿买量轨，能算 CPI 但算不出 ROI（不知道这些人后来赚了多少钱）；
-- 只拿变现轨，能算 ROI 但算不出 CPI（不知道这些人花多少钱买来的）。
-- 而「ROI 多少才算买得值」这个核心决策，必须两个数放在一起看。
--
-- 所以这张表是**决策表**：左边花了多少钱，右边赚了多少钱，中间是效率。
-- dws_cohort_daily 是**分析表**：用来回答「第 7 天发生了什么」。
-- 两张表都是 DWS，但服务的问题不同 —— 这就是为什么不能只留一张。
--
-- -----------------------------------------------------------------------------
-- 为什么 installs / spend 取自变现侧而不是买量侧
-- -----------------------------------------------------------------------------
-- 两边数字完全相同（生成时就是从 DWD 反算的，并做了逐天对账），
-- 但取值口径要定一个**唯一权威来源**，否则将来两边一旦出现归因差，
-- 报表会随机显示两个不同的数。
-- 这里定：以 DWD（用户明细）为准，因为它是「人」的事实；
-- 买量表（平台回传）作为参考，差异应体现在 buy_installs 上而不是覆盖主口径。
--
-- -----------------------------------------------------------------------------
-- 为什么不按 dt 分区
-- -----------------------------------------------------------------------------
-- 340 行、几十 KB。分区表的最小分区粒度是「一个目录 + 一个文件」，
-- 每个文件还有几百字节的 footer 开销，31 个分区就是 31 个文件管 340 行，
-- 典型的「小文件问题」—— 读的时候要开 31 个文件句柄，比不分区慢。
--
-- 不分区意味着「重跑某天要全量重算」，但因为整表只有 340 行，
-- 全量重算也就几秒，这是**用规模换简单**的正当权衡。
-- 判断标准始终是：单分区大小是否落在 128 MB ~ 1 GB。落不进去就别分。
-- =============================================================================

WITH monetization AS (
    -- 变现侧：按投放日汇总所有活跃用户（不分 cohort，因为这一列问的是「今天怎么样」）
    SELECT
        dt,
        app,
        country,
        campaign,
        sub_channel,
        count(DISTINCT uid)                                                AS active_users,
        sum(ad_impr)                                                       AS ad_impressions,
        sum(rewarded_views)                                                AS rewarded_views,
        sum(session_cnt)                                                   AS sessions,
        cast(sum(ad_revenue) AS decimal(18,6))                             AS ad_revenue,
        sum(CASE WHEN days_since_install = 0 THEN 1 ELSE 0 END)            AS installs,
        cast(sum(CASE WHEN days_since_install = 0 THEN install_cost ELSE 0 END)
             AS decimal(18,6))                                             AS spend
    FROM lab.dwd_user_daily
    GROUP BY dt, app, country, campaign, sub_channel
),

acquisition AS (
    -- 买量侧：只到渠道 × 天，没有 cohort 概念
    SELECT
        dt,
        country,
        campaign,
        sub_channel,
        sum(clicks)                                                        AS clicks,
        sum(buy_impressions)                                               AS buy_impressions
    FROM lab.ods_iaa_acquisition_daily
    GROUP BY dt, country, campaign, sub_channel
)

SELECT
    m.dt,
    m.app,
    m.country,
    m.campaign,
    m.sub_channel,

    -- 变现侧
    m.installs,
    m.spend,
    m.active_users,
    m.ad_impressions,
    m.rewarded_views,
    m.sessions,
    m.ad_revenue,

    -- 买量侧
    -- 用 COALESCE 兜 0 而不是留 NULL：
    -- 「这天这个渠道没投广告」在业务上就是 0 次曝光 0 次点击，
    -- 是**真实观测到的 0**，不是「未知」。
    -- 留 NULL 会让 avg(ctr) 悄悄丢掉这些行，把均值抬高 —— 这就是「未知填 0」的
    -- 反面错误：该填 0 的地方留了 NULL，同样会造出假指标。
    coalesce(a.clicks, 0)                                                  AS clicks,
    coalesce(a.buy_impressions, 0)                                         AS buy_impressions

FROM monetization m
LEFT JOIN acquisition a
       ON m.dt = a.dt
      AND m.country = a.country
      AND m.campaign = a.campaign
      AND m.sub_channel = a.sub_channel
