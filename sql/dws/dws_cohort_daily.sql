-- =============================================================================
-- lab.dws_cohort_daily —— cohort 长表（变现侧，双轴压成「装机日 + 第 N 天」）
-- =============================================================================
-- 上游：lab.dwd_user_daily（3,568 万行，31 个 dt 分区）
-- 下游：中台 agent.py 取数、metrics.yaml 的 8 个变现侧指标、D0~D30 横铺视图
--
-- 粒度：一个 cohort 在某一天的表现（一行）
--       cohort = 装机日 × app × country × campaign × sub_channel × creative_id
--       一行 = 「10-01 从 US_A_01/ch_001/crt_012 装进来的那批人，在第 7 天还剩多少」
--
-- 行数：单分区约 1.2 万行（31 个 install_date × 约 400 个渠道素材组合），
--       31 个分区合计约 31 万行 —— 比明细小 115 倍。
--
-- -----------------------------------------------------------------------------
-- 三个必须解释清楚的设计决定
-- -----------------------------------------------------------------------------
--
-- ① 为什么按 dt（= 变现日）分区，而不是按 install_date 分区？
--
--    按 install_date 分区看着更「cohort 友好」，但它是**只读一次的假分区**：
--    10-01 装的那批用户，D0 到 D30 的数据会**陆续产生**在 10-02 到 10-31。
--    也就是说「install_date=2026-10-01」这个分区，每天都要往里追加新行 ——
--    分区不再是「算完就不动」的单元，而是个持续变脏的容器。
--    后果：重跑 10-01 分区时，你必须把 10-01 到 10-31 的 DWD 全部重算一遍，
--    因为那批人的全部行为都在里面。
--
--    按 dt 分区则完全对齐：
--        DWD dt=2026-10-15  ──→  DWS dt=2026-10-15
--    一个分区进、一个分区出，重跑 10-15 只读 DWD 的 10-15。
--    这是分层数仓里最重要的一条：**每层的分区边界要能一一对上**，
--    否则「单天重跑」在第 2 层就断了。
--
-- ② 为什么 installs / spend 只在 day_offset = 0 上有值？
--
--    这是本表最大的一个坑。cohort 表里同一批用户的 installs 会出现在
--    31 行上（D0 到 D30 各一行）。如果每行都填 1000，那么
--        SUM(installs) 会得到 31000
--    而这个 cohort 真实只装了 1000 个 —— 直接放大 31 倍，且不报错。
--
--    处理方式：只在 day_offset=0 那一行填真值，其余行填 0。
--    这样 SUM(installs) 天然正确，不需要调用方记住任何附加条件。
--    这条叫「**可加性设计**」：让每个度量在任何维度上卷都得到正确答案，
--    而不是要求下游「只能按某种特定方式查」。
--
--    spend（买量花费）同理：花钱只发生在装机那一刻，不该在 D7 行里再算一次。
--
-- ③ 为什么这里可以 count(DISTINCT uid)，而明细层不建议随便用？
--
--    distinct 是全局去重，要 shuffle 全表，代价高。但这里的分组维度
--    （install_date × 渠道 × 素材 × 第 N 天）已经把数据切得很细，
--    单组内只有几十到几百个用户，去重开销可以接受。
--    而且**这一步是唯一能保留「人数」语义的地方** ——
--    再往上汇总就只能加总 active_users，不能再 distinct，
--    因为同一个人可能在两个渠道都活跃（跨渠道重叠），
--    加总后会重复计数。这就是「活跃用户数不可跨维度相加」的根源。
--
-- -----------------------------------------------------------------------------
-- 口径纪律：只存原子量，不存任何比率
-- -----------------------------------------------------------------------------
-- ctr / roi / ecpm / retention / arpu 一律不落库，查询时按 metrics.yaml 现算。
-- 理由（实测过）：比率不能二次聚合。
--     Criteo 那批数据上 avg(ctr) = 35.13%，sum(clicks)/sum(impr) = 36.12%，
--     差 2.7% —— 因为「5000 次展示的 10%」和「50 次展示的 10%」平均起来不是 10%。
-- =============================================================================

SELECT
    -- 装机日由「变现日 − 距装机的天数」反推出来。
    -- DWD 里没存 install_date，因为它和 (dt, days_since_install) 信息等价，
    -- 存两份等于给数据不一致留了口子。
    --
    -- 这里用字面量 '${dt}' 而不是 dt 列：本 SQL 的结果要写进静态分区
    -- PARTITION (dt='...')，而**静态分区的分区列不能出现在 SELECT 里**
    -- （Spark 会报 too many data columns）。用字面量既避开这个限制，
    -- 又保证整批行的 dt 完全一致 —— 不可能出现「写进 10-15 分区但 dt 是 10-14」。
    date_sub(to_date('${dt}'), days_since_install) AS install_date,
    days_since_install                            AS day_offset,

    app,
    country,
    campaign,
    sub_channel,
    creative_id,

    -- ---- 原子度量（都能在任意维度上卷）----
    count(DISTINCT uid)                           AS active_users,
    sum(ad_impr)                                  AS ad_impressions,
    sum(rewarded_views)                           AS rewarded_views,
    sum(session_cnt)                              AS sessions,
    cast(sum(ad_revenue) AS decimal(18,6))        AS ad_revenue,

    -- ---- 只在 D0 有值的度量（见上文 ②）----
    sum(CASE WHEN days_since_install = 0 THEN 1 ELSE 0 END)              AS installs,
    cast(sum(CASE WHEN days_since_install = 0 THEN install_cost ELSE 0 END)
         AS decimal(18,6))                                               AS spend

FROM lab.dwd_user_daily
WHERE dt = '${dt}'
GROUP BY
    days_since_install,
    app,
    country,
    campaign,
    sub_channel,
    creative_id
