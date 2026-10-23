-- =============================================================================
-- dwd.dwd_ad_event —— 广告事件明细层（清洗 + 去重 + 派生）
-- =============================================================================
-- 上游：ods.ods_ad_log（31 个 dt 分区，16,468,027 行，原始 TSV 只换了格式没改内容）
-- 本层：把「能用的数据」挑出来，补上分析要用的派生列，一行 = 一次广告曝光
--
-- 为什么这个文件是纯 SELECT 而不是 INSERT OVERWRITE：
--     SELECT 可以单独贴进 Hue / spark-sql / DBeaver 直接跑，马上看到结果。
--     写盘那一步（repartition + INSERT OVERWRITE）放在 build_dwd.py 里，
--     因为「写几个文件、怎么分区」是 Spark 的执行细节，不该混进业务 SQL。
--     这是分工：SQL 说「要什么」，Python 说「怎么落」。
--
-- 为什么清洗写在重命名**之前**（用的是 ODS 的原始列名）：
--     ① 先过滤再转换，被丢掉的行不用做 CAST，省一遍无用功；
--     ② 更重要的是，scripts/dwd_rules.py 要用同一套列名逐条统计
--        「每条规则各拦掉多少行」。列名一旦在 SQL 里改过，审计代码就得
--        另写一遍映射 —— 两份定义迟早会走偏。现在只有一份。
--
-- 规则编号 R1~R9 必须和 scripts/dwd_rules.py 保持完全一致，
-- 脚本启动时会做文本比对，不一致直接报错中止（不允许悄悄不同步）。
-- =============================================================================

WITH cleaned AS (
    -- 第一步：9 条清洗规则，全部作用在 ODS 原始列上。
    -- 排序原则：先拦「结构性错误」（主键为空、取值越界），再拦「业务上不可能」（时间倒挂）。
    -- 结构性错误不先清掉，后面的规则会在一堆 NULL 上做比较，
    -- 结果全是 NULL，WHERE 当 false 静默丢掉 —— 你就永远不知道是谁拦的。
    SELECT * FROM ods.ods_ad_log
    WHERE
        uid IS NOT NULL                                                    -- R1
        AND campaign IS NOT NULL                                           -- R2
        AND timestamp >= 0                                                 -- R3
        AND click IN (0, 1)                                                -- R4
        AND conversion IN (0, 1)                                           -- R5
        AND cost IS NOT NULL AND cost > 0                                  -- R6
        AND NOT (conversion = 1 AND conversion_timestamp < timestamp)      -- R7
        AND NOT ((conversion = 1 AND (click_nb < 1 OR click_pos < 0)) OR (conversion = 0 AND (click_nb <> -1 OR click_pos <> -1)))  -- R8
        AND NOT ((attribution = 1 OR conversion = 1) AND click = 0)        -- R9
),

typed AS (
    -- 第二步：改名 + 定类型。ODS 保留原表原名是为了「可追溯」，
    -- DWD 改名是为了「可读」—— 下游看到 campaign_id 不会误解成「广告系列编号」以外的东西。
    SELECT
        CAST(timestamp              AS BIGINT) AS event_ts,
        CAST(uid                    AS BIGINT) AS uid,
        CAST(campaign               AS BIGINT) AS campaign_id,
        CAST(click                  AS INT)    AS click,
        CAST(conversion             AS INT)    AS conversion,
        CAST(attribution            AS INT)    AS attribution,
        CAST(conversion_timestamp   AS BIGINT) AS conversion_ts,
        CAST(conversion_id          AS BIGINT) AS conversion_id,
        CAST(click_pos              AS INT)    AS click_pos,
        CAST(click_nb               AS INT)    AS click_nb,
        CAST(cost                   AS DOUBLE) AS cost,
        CAST(cpo                    AS DOUBLE) AS cpo,
        CAST(time_since_last_click  AS DOUBLE) AS time_since_last_click,
        CAST(cat1 AS BIGINT) AS cat1, CAST(cat2 AS BIGINT) AS cat2,
        CAST(cat3 AS BIGINT) AS cat3, CAST(cat4 AS BIGINT) AS cat4,
        CAST(cat5 AS BIGINT) AS cat5, CAST(cat6 AS BIGINT) AS cat6,
        CAST(cat7 AS BIGINT) AS cat7, CAST(cat8 AS BIGINT) AS cat8,
        CAST(cat9 AS BIGINT) AS cat9,
        dt
    FROM cleaned
),

ranked AS (
    -- 第三步：去重。
    -- 主键为什么是 (uid, campaign_id, event_ts)：一次曝光 = 某人在某一秒看到了某个广告，
    -- 同一个人在同一秒对同一个广告理论上只能有一次曝光，所以这个三元组应当唯一。
    -- 实测这份数据 0 重复（公开数据集已去过重），但规则必须留着 ——
    -- 生产环境里重复来自「上游任务重跑 / 双写 / 采集 SDK 重发」，不是数据天生干净。
    --
    -- 排序为什么是 ORDER BY conversion DESC, click DESC, cost DESC：
    --     真正的重复（重发）内容完全一样，排序对结果没影响。
    --     但如果内容不一样，说明「同一秒发生了两件不同的事」，那是主键定错了 ——
    --     这时保留信息最全的那条（有转化 > 有点击 > 花费高），
    --     并且由 build_dwd.py 单独报警，不允许静默丢掉另一条。
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY uid, campaign_id, event_ts
            ORDER BY conversion DESC, click DESC, cost DESC
        ) AS rn
    FROM typed
),

deduped AS (
    SELECT * FROM ranked WHERE rn = 1
)

SELECT
    -- ---------- 时间 ----------
    event_ts,
    -- 相对秒还原成可读时间：原始 timestamp 从 0 开始（脱敏），映射到 2026-09-21 00:00:00 起算。
    -- CAST(bigint AS TIMESTAMP) 在 Spark 里把 bigint 当「epoch 秒」解释，
    -- 所以先加上基准日期的 epoch 秒数再 cast，正好得到真实时刻。
    CAST(unix_timestamp('2026-09-21 00:00:00') + event_ts AS TIMESTAMP) AS event_time,
    CAST(hour(CAST(unix_timestamp('2026-09-21 00:00:00') + event_ts AS TIMESTAMP)) AS INT) AS event_hour,

    -- ---------- 主体 ----------
    uid,
    campaign_id,
    cat1, cat2, cat3, cat4, cat5, cat6, cat7, cat8, cat9,

    -- ---------- 事件标志 ----------
    -- 用 0/1 不用 boolean：交接给 Python / 模型特征时不用再转一次
    click       AS is_click,
    conversion  AS is_conversion,
    attribution AS is_attributed,

    -- ---------- 点击上下文 ----------
    -- time_since_last_click = -1 表示「没有上一次点击」
    -- （实测 8,770,820 行 = 53.26%，全部恰好等于 -1，见 probe_dwd_semantics.py 的 A 段）。
    -- 哨兵值留在表里会让「平均上次点击间隔」算出一个假的负数，所以显式转 NULL；
    -- 但原始列不能删 —— 它和「是不是首次点击」直接相关。
    CASE WHEN time_since_last_click >= 0 THEN time_since_last_click END AS prev_click_gap_sec,
    -- 首次点击 = 这次点了，但在此之前没有点击记录
    CAST(CASE WHEN click = 1 AND time_since_last_click = -1 THEN 1 ELSE 0 END AS INT) AS is_first_click,

    -- ---------- 转化 ----------
    conversion_ts,
    conversion_id,
    -- 转化延迟（秒）= 转化时刻 - 曝光时刻。只有真有转化、且时间不倒挂时才有意义。
    -- 这是「归因窗口」分析的原料：延迟超过 7 天的转化该不该算给这次曝光？
    CASE
        WHEN conversion = 1 AND conversion_ts >= event_ts THEN conversion_ts - event_ts
    END AS conv_delay_sec,
    -- 下面两个都是**转化上下文**，不是点击上下文 —— 这是实测反推出来的，不是看列名猜的：
    --   click_nb  ：转化前的累计点击数，有转化时 1..174，无转化时 -1
    --   click_pos ：本次点击在这条转化路径中的位置，有转化时 0..173，无转化时 -1
    -- 实测 click_nb >= 1 与 conversion = 1 **完全等价**（806,196 行一一对应），
    -- 所以我没沿用原列名 click_nb / click_pos —— 那个名字会让人以为是「每次点击都有的字段」，
    -- 下游一旦按「有点击的行都该有值」来写逻辑，就会把 93% 的点击判成缺数据。
    CASE WHEN click_nb >= 1 THEN click_nb END AS conv_click_nb,
    CASE WHEN click_pos >= 0 THEN click_pos END AS conv_click_pos,

    -- ---------- 金额 ----------
    cost,
    -- 单次点击成本。只有点击行才有值，曝光行给 NULL 不给 0 ——
    -- 给 0 会污染 avg()，把均值往小拖，而且这个错误完全看不出来。
    CASE WHEN click = 1 THEN cost END AS cost_per_click,
    -- cpo 语义不明（实测：与 cost 不成固定比例，也不是 campaign 级常量），
    -- 按数据契约只保留、不在任何指标里引用。见 docs/data_contract.md
    cpo,

    dt
FROM deduped
