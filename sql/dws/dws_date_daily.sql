-- =============================================================================
-- lab.dws_date_daily —— 全站日汇总（31 行）
-- =============================================================================
-- 上游：lab.dwd_user_daily、lab.ods_iaa_acquisition_daily
-- 下游：中台首页概览、日趋势图、DAG 健康检查
--
-- 粒度：一天 → 一行。31 行，全站最粗的一层汇总。
--
-- -----------------------------------------------------------------------------
-- 这张表看起来「什么都能从别的表算出来」，为什么还要单独建
-- -----------------------------------------------------------------------------
-- 三个理由，前两个是性能，第三个是正确性：
--
-- ① 首页每次打开都要看「今天花了多少、赚了多少」，
--    如果每次都去扫 3,568 万行明细，一次 30 秒。而这张表 31 行，毫秒级。
--
-- ② 它是**对账的锚点**。所有上层表的数字最终都要能卷到这一天一行上，
--    卷不上去就说明某处口径错了。DAG 里每次跑完用它做一次全站对账，
--    比逐个分区检查快得多，也更容易发现「某天数据缺失但没人报错」。
--
-- ③ 它保留「当天新装」和「当天活跃」的**双口径**。
--    这两个数很容易被混成同一个：
--        新增用户数（installs）    = 今天第一次打开 App 的人
--        活跃用户数（active_users）= 今天打开过 App 的人（含老的）
--    在成熟产品里 DAU 可能是新增的 10 倍，混用会让所有人均指标差一个量级。
--    单独一张表把两个数并排放着，是最不容易出错的做法。
-- =============================================================================

WITH monetization AS (
    SELECT
        dt,
        count(DISTINCT uid)                                                AS active_users,
        -- 当天新装：只有 day_offset=0 的行才代表「今天第一次来」
        sum(CASE WHEN days_since_install = 0 THEN 1 ELSE 0 END)            AS installs,
        sum(ad_impr)                                                       AS ad_impressions,
        sum(rewarded_views)                                                AS rewarded_views,
        sum(session_cnt)                                                   AS sessions,
        cast(sum(ad_revenue) AS decimal(18,6))                             AS ad_revenue,
        cast(sum(CASE WHEN days_since_install = 0 THEN install_cost ELSE 0 END)
             AS decimal(18,6))                                             AS spend
    FROM lab.dwd_user_daily
    GROUP BY dt
),

acquisition AS (
    SELECT
        dt,
        sum(clicks)                                                        AS clicks,
        sum(buy_impressions)                                               AS buy_impressions
    FROM lab.ods_iaa_acquisition_daily
    GROUP BY dt
)

SELECT
    m.dt,
    m.installs,
    m.active_users,
    m.sessions,
    m.ad_impressions,
    m.rewarded_views,
    m.ad_revenue,
    m.spend,
    coalesce(a.clicks, 0)                                                  AS clicks,
    coalesce(a.buy_impressions, 0)                                         AS buy_impressions
FROM monetization m
LEFT JOIN acquisition a ON m.dt = a.dt
