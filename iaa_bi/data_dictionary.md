# IAA 广告变现分析中台 —— 数据字典（Data Dictionary）

> 本项目的数据模型地基。所有后续的造数、数仓分层、pivot、报表、Agent 都基于这份字段定义。
> 业务背景：海外网赚类 IAA（广告变现，无内购/订阅），美国 T0 市场，D7 ROAS 目标 70%。

---

## 一、核心认知：IAA 是"买量 + 变现"双轨，指标同名不同义

| 轨道 | 关心什么 | 核心指标 | 口径 |
|------|----------|----------|------|
| **买量侧（Acquisition）** | 怎么把用户买进来 | CPI、买量 CTR、买量 CVR、买量 eCPM | 花多少钱 / 买多少量 |
| **变现侧（Monetization）** | 用户进来怎么赚钱 | 变现 eCPM、观次、留存 D0~D∞、D7 ROAS | 看多少广告 / 赚多少钱 |

**关键坑**：`eCPM`、`CTR`、`CVR` 在两套体系里**同名不同义**：
- 买量 eCPM = spend / impressions × 1000（成本视角）
- 变现 eCPM = ad_revenue / ad_impressions × 1000（收入视角）

---

## 二、双轴事实表（项目地基，DWD 层核心）

### 为什么是"双轴"？
变现数据要同时回答两个问题，一个日期答不了：
1. "今天公司赚了多少" → 按 `report_date`（回传日期）切
2. "某批用户赚了多少" → 按 `install_date`（安装日期）切

所以事实表必须同时带两个日期，这就是"双轴"。

### 字段清单（15 个字段）

| 字段 | 类型 | 性质 | 含义 |
|------|------|------|------|
| `install_date` | date | 日期轴1 | 这批用户哪天安装（cohort 锚点） |
| `report_date` | date | 日期轴2 | 这笔数据哪天发生（回传日期） |
| `app` | str | 维度 | 游戏包 |
| `country` | str | 维度 | 国家 |
| `campaign` | str | 维度 | 投放系列 |
| `sub_channel` | str | 维度 | 子渠道（归因来源） |
| `creative_id` | str | 维度 | 素材ID（last-click 归因） |
| `placement` | str | 维度 | 广告位（激励视频/插屏/banner） |
| `ad_network` | str | 维度 | 广告平台（Unity/MTG/AdMob 等） |
| `installs` | int | 度量(原子) | 这批安装总数（cohort 分母） |
| `active_users` | int | 度量(原子) | 当天活跃人数（留存分子） |
| `ad_impressions` | int | 度量(原子) | **变现侧**广告展示次数（变现 eCPM 分母） |
| `rewarded_views` | int | 度量(原子) | 激励视频观看次数（观次分子） |
| `ad_revenue` | float | 度量(原子) | 当天广告收入 |
| `spend` | float | 度量(原子) | 当天花费（ROI 分母） |
| `clicks` | int | 度量(原子·买量侧) | **买量侧**点击次数（CVR 分母） |
| `buy_impressions` | int | 度量(原子·买量侧) | **买量侧**广告展示次数（买量 CTR 分母） |

> ⚠️ **关键区分**：`ad_impressions` 是变现侧（用户看广告），`buy_impressions` 是买量侧（我们投放的广告被展示）。
> 两者同名不同义，绝不可混用：变现 eCPM = ad_revenue / ad_impressions，买量 CTR = clicks / buy_impressions。

### 核心原则：只存原子，派生指标用时算

| 派生指标 | 公式 | 不存进表的原因 |
|----------|------|----------------|
| 留存率 D_N | active_users / installs | 两个原子字段一除即可 |
| eCPM | ad_revenue / ad_impressions × 1000 | 同上 |
| 人均观次 | rewarded_views / active_users | 同上（观次=用户主动看激励视频次数） |
| D_N ROI | 累计 revenue / 累计 spend | 原子字段累加 |
| CPI | spend / installs | 同上 |
| 买量 CTR | clicks / buy_impressions | 同上（买量侧） |
| 买量 CVR | installs / clicks | 同上（买量侧） |

---

## 三、数仓模型：事实表 + 维度表（星型模型）

### 事实表（fact，记录"发生了什么"）
- `cohort_daily_fact`（双轴事实表）：记录每天、每个 cohort 的度量值
- 通过**维度 ID** 关联维度表（不存名字，存 ID）

### 维度表（dim，描述"是什么"）

| 维度表 | 主键 | 关键属性 | 为什么要单独建 |
|--------|------|----------|----------------|
| `dim_campaign` | campaign_id | 系列名、策略类型、投放人、渠道 | 系列是最大投放单位，连着投放人 |
| `dim_campaign_country` | campaign_id+country | 预算、目标值 | 系列×国家粒度的配置（给 LLM 用，日常表不显示） |
| `dim_creative` | creative_id | 类型、尺寸、状态 | 素材属性多、会停用 |
| `dim_sub_channel` | sub_channel_id | 归因平台、来源、状态 | 删除功能、归因信息 |
| `dim_country` | country | 市场等级、大区、货币 | 按市场等级分析、属性会变 |

**不单独建表的维度**（直接当事实表维度字段）：
- `placement`（广告位）、`ad_network`（广告平台）——取值固定
- `ad_group`（广告组）——看的少、取决于渠道，暂缓

### 两条数据通道（核心架构认知）

```
通道 1：结果数据（事实）  —— 广告平台 API 推过来 → 四层数仓（ODS→DWD→DWS→ADS）
通道 2：配置数据（维度）  —— 优化师在平台后台设置 → Playwright 抓取 → 维度表
```

### 买量与变现分开建表（时间轴不同）

买量（前端）和变现（后端）是两条轨道，**时间轴不同，不能混在一张表**：

| | 买量日表（前端） | 双轴事实表（后端） |
|---|---|---|
| 时间轴 | **单轴**：只有 `report_date` | **双轴**：`install_date` + `report_date` |
| 本质 | "投放当天的动作"（花多少/点多少/装多少） | "某批用户第 N 天的表现" |
| 字段 | report_date + 维度 + clicks/buy_impressions/installs/spend | 15 字段（见上） |
| 派生指标 | 买量 CTR = clicks/buy_impressions，CVR = installs/clicks | 留存/eCPM/观次/ROI |

**关键**：买量没有 cohort 概念（没有 install_date），变现才有。两者只在查询时用 `creative_id` / `sub_channel_id` join，形成"素材/子渠道前后端表"。

- 结果数据：只增不减（append），一直流进来
- 配置数据：是"快照"，调整了要重新抓取
- 两者**分开走、分开更新**，只在查询/分析时 join

### SCD（缓慢变化维度，Type 2）

配置会变（目标值/预算/投放人），但 LLM 要基于历史判断，不能直接覆盖。

**Type 2 做法**：变更时"关旧行 + 插新行"，加 3 个字段：
- `eff_start_date`：生效开始
- `eff_end_date`：生效结束（NULL = 至今）
- `is_current`：是否当前生效

**粒度统一原则**：分析是日级 → 事实按天聚合 + 配置按天快照（eff_date 精确到天）+
Agent 每天最多调一次配置。三者统一到"天"粒度，消除"小时级频繁调预算对不上数据"的问题。

**做法2：配置按天快照（同天多次调整只记最终值）**
- 一天内优化师/Agent 可能对同一配置（预算/目标值）调多次，但事实表按天聚合后看不出小时差异。
- 所以配置也按"天"落库：同一天对同一配置的多次调整，**只保留最后那次**（即当天收盘时的最终值）。
- 与 SCD 关系：跨天变更仍然走"关旧+插新"保留历史；只有"同一天内"才合并成最终值。两者不冲突——SCD 管"哪天生效"，做法2 管"那天最终是多少"。

### 操作审计日志（append-only，与 SCD 联动）

记录 Agent 或投放人的**每次**策略调整（"谁在何时怎么改的"），与"配置表记最终值"互补。

| 字段 | 类型 | 含义 |
|------|------|------|
| `log_id` | int | 自增主键 |
| `op_time` | datetime | 操作时间 |
| `operator` | str | 操作者（`agent` 或投放人名字） |
| `action` | str | 动作：adjust_budget/adjust_target/delete_sub_channel/change_creative/add_country/create_campaign/pause_campaign/resume_campaign |
| `app` | str | 包名 |
| `channel` | str | 渠道 |
| `campaign_id` | str | 系列ID（可空） |
| `country` | str | 国家（可空） |
| `field` | str | 改的字段（budget/target/...） |
| `old_value` / `new_value` | str | 改前/改后值 |
| `reason` | str | 调整原因（LLM 结论） |

**关键设计**：
- **append-only**：日志只追加不修改，与 SCD 的"关旧插新"正好相反（SCD 记"某时刻配置是多少"，日志记"谁在何时做了什么事"）。
- **与 SCD 联动**：`upsert_campaign_country` 检测到值变化时，自动写一条日志；调用方传 `operator`（谁）、`reason`（为什么），日志自动生成。造数初始化时 `operator=None` 则静默不记日志。
- **价值**：① 追溯责任（人机协作，投放人保留最终决策权）② 还原操作链 ③ 给 LLM 看"以前做过什么决策、结果如何"，支持历史判断。

---

## 四、三张业务表（ADS 层，给前端看）

| 表 | 主维度 | 数据源 | 特殊能力 |
|----|--------|--------|----------|
| **日常表** | 日期 | 后端变现（cohort 口径 D0~D∞） | 多维筛选按钮（国家/包/系列/素材/子渠道） |
| **素材表(前端)** | 素材ID | 前端买量 + 简略后端 | 连续时间筛选 |
| **子渠道表** | 子渠道ID | 归因API(Adjust) + 前端 | **删除子渠道操作** |

### 日常表指标（全部 cohort 口径，D0 起可无限延伸）
- 花费、安装、CPI
- D0/D1/D2/... 留存率
- D0/D1/D2/... eCPM（第 N 天留存人群的 eCPM）
- D0/D1/D2/... 观次
- D1/D2/... ROI（累计到第 N 天的同期收入/支出）
- 后端点击率、转化率

### 素材/子渠道前后端表结构（已实现）

粒度：`install_date × creative_id`（素材表）/ `install_date × sub_channel`（子渠道表）。

| 列 | 来源 | 含义 |
|----|------|------|
| `spend` | 前端(买量日表) | 该素材/子渠道花了多少 |
| `clicks`/`buy_impressions` | 前端 | 点击/展示 |
| `ctr`/`cvr`/`cpi` | 前端派生 | clicks/buy_impressions, installs/clicks, spend/installs |
| `d7_retention` | 后端派生 | 第7天活跃/安装 |
| `d7_roi` | 后端派生 | D7累计收入/花费 |
| `d7_ecpm` | 后端派生 | D7累计收入/D7累计展示×1000 |

**对齐机制（核心）**：
- 前端是"天"口径（report_date），后端是 cohort 口径（install_date）。
- 用 `install_date` 做统一锚点：前端花费按安装日归集，后端按安装日累计到 D7。
- **demo 简化**：买量表的 report_date 近似当 install_date（IAA 点击即装，归因延迟短）；真实环境需归因匹配。
- **价值**：只看前端 CTR/CVR 看不出素材好坏，结合后端 D7 ROI 才能判定"该换不该换"。

---

## 五、完整系统蓝图（真实生产级，demo 只做其中一部分）

```
多源接入(买量/归因/变现 API) → Kafka(削峰解耦) → Flink(流式清洗关联)
  → 数仓四层(ODS→DWD→DWS→ADS) → BI 中台(三张表) → Agent(LLM分析+自动优化)
```

**Demo 边界（真实可做，不过度设计）：**
- ❌ 不做 Kafka / Flink（数据量是仿真、批处理，用不上）
- ✅ 做数仓四层（含双轴事实表 + pivot）
- ✅ 做 BI 中台（先日常表，再迭代素材表/子渠道表）
- ✅ 做 Agent（LLM 给结论 + mock API 自动执行）

---

## 六、已确认的口径

- ✅ 素材归因：**last-click**（一个安装归给最终点击的素材）
- ✅ 广告位 `placement`：作为独立维度（当前主要是激励视频，为全面加上插屏/banner）
- ✅ 广告平台 `ad_network`：作为独立维度（Unity / MTG(Mintegral) / AdMob 等）
- ✅ 观次口径：**用户主动看激励视频的平均次数**（`rewarded_views / active_users`）

## 七、待确认/遗留问题

- [ ] 数据成熟度：当天未过完的数据标星号，报表阶段处理
- [ ] 后端点击率、转化率 的具体口径，需进一步明确（是变现端广告的点击率吗）
