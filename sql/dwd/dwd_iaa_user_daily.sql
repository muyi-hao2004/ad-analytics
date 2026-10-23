-- =============================================================================
-- lab.dwd_user_daily —— 用户日活明细层（清洗 + 去重 + 折叠时间轴）
-- =============================================================================
-- 上游：lab.ods_iaa_user_daily（31 个 dt 分区，约 5,000 万行，含 4 类脏数据）
-- 本层：把能用的数据挑出来，一行 = 一个用户在某一天的活跃记录
--
-- 与 ODS 的差别只有两件事：
--   ① 丢掉 4 类脏数据（规则 R1~R5）
--   ② 去掉 install_date 列 —— 它和 (dt, days_since_install) 信息等价，
--      留着就是三份冗余。查询时用 date_sub(dt, days_since_install) 还原。
--
-- 为什么清洗写在投影**之前**（用的是 ODS 原始列名）：
--   ① 先过滤再转换，被丢掉的行不用做 CAST；
--   ② 更重要的是审计：build_dwd_iaa.py 要用同一套列名逐条统计
--      「每条规则各拦掉多少行」。列名一旦在 SQL 里改过，
--      审计代码就得另写一遍映射 —— 两份定义迟早走偏。
--
-- 规则编号 R1~R5 必须和 scripts/build_dwd_iaa.py 里的 RULES 完全一致，
-- 脚本启动时会做文本比对，不一致直接中止（不允许悄悄不同步）。
--
-- 这个文件是纯 SELECT，不含 INSERT —— 可以单独贴进 spark-sql 直接看结果。
-- 「repartition 几个文件、怎么写分区」是执行细节，放在 build_dwd_iaa.py 里。
-- =============================================================================

WITH cleaned AS (
    -- 第一步：5 条清洗规则，全部作用在 ODS 原始列上。
    -- 排序原则：先拦「结构性错误」（主键为空、时间倒挂），再拦「取值越界」。
    -- 结构性错误不先清掉，后面的规则会在 NULL 上做比较，结果全是 NULL，
    -- WHERE 当 false 静默丢掉 —— 你就永远不知道是哪条规则拦的。
    SELECT *
    FROM lab.ods_iaa_user_daily
    WHERE
        uid IS NOT NULL                                  -- R1 主键为空：无法归因的孤儿行
        AND days_since_install >= 0                      -- R2 活跃日早于装机日：上游时钟错乱
        AND ad_revenue >= 0                              -- R3 收入为负：扣款/退款处理错误
        AND ad_impr >= 0 AND session_cnt >= 1            -- R4 展示为负或零次打开：上游统计错误
        AND rewarded_views >= 0 AND rewarded_views <= ad_impr  -- R5 激励视频数超过总展示：不可能
),

deduped AS (
    -- 第二步：按 (uid, dt) 去重，只保留一条。
    --
    -- 为什么必须去重：ODS 里有 0.5% 的完全重复行（模拟上游重跑 / exactly-once 失效）。
    -- 不去重的话 sum(ad_revenue) 直接翻倍，而且**一声不响**。
    --
    -- 为什么 ORDER BY 里要写确定的列：
    --   ROW_NUMBER() 不带 ORDER BY 时，同一组内谁排第一是不确定的，
    --   同一个 dt 跑两次可能保留不同的行 → 不幂等。
    --   这里按 (ad_revenue, install_cost) 降序，保证每次跑结果一致。
    --   （正常情况重复行内容完全相同，排序只是为了确定性。）
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY uid, dt
            ORDER BY ad_revenue DESC, install_cost DESC NULLS LAST
        ) AS _rn
    FROM cleaned
)

SELECT
    uid,
    days_since_install,
    app,
    country,
    campaign,
    sub_channel,
    creative_id,
    device_os,
    session_cnt,
    ad_impr,
    rewarded_views,
    ad_revenue,
    install_cost,
    dt
FROM deduped
WHERE _rn = 1
