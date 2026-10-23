# IAA 广告变现分析中台

[![CI](https://github.com/muyi-hao2004/ad-analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/muyi-hao2004/ad-analytics/actions/workflows/ci.yml)

> 围绕「海外 IAA 休闲游戏广告变现」，实现一条完整链路：
> **合成数据 → 五层数仓 → 质量门禁 → 语义层 → LLM 快照 → Agent 决策 → 护栏审批 → 决策评测**。
>
> **定位：工程化参考实现，不是生产系统。**
> 业务数据全部为合成数据；RAG 语料来自公开报告与平台文档（每条带来源与时间），非第一方数据。
> 哪些是真的、哪些是壳，见 **[`iaa-analytics-agent/LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md)**。
>
> 这个仓库想证明的不是「我做过投放优化业务」，而是：
> **一个 LLM 决策系统需要哪些工程约束，以及怎么用可执行的断言证明这些约束真的生效。**

---

## 0. 面试官如果没有 10 分钟

| 你可能想知道 | 直接看 | 证据形态 |
|---|---|---|
| 数仓怎么分层、怎么保证数据可信 | [`scripts/quality_gate_iaa.py`](scripts/quality_gate_iaa.py) + [`src/metrics.yaml`](iaa-analytics-agent/src/metrics.yaml) | 35 条断言全部外置在 YAML，FAIL 即中断流水线 |
| 怎么证明「重跑不会算错」 | [`scripts/test_idempotent_iaa.py`](scripts/test_idempotent_iaa.py) | 同一天重跑 3 次，10 张表逐行指纹相同 |
| 喂给 LLM 的数据是怎么处理的 | [`src/agent.py`](iaa-analytics-agent/src/agent.py) 的 `build_snapshot` / `sanitize_for_llm` | 成熟度过滤 / NaN→null / 显式「不知道」 |
| LLM 怎么避免「每天改口」 | [`src/decision.py`](iaa-analytics-agent/src/decision.py) | 滞回死区 + 连续确认 + `decision_log` |
| 两个数据源怎么保证一致 | `check_source_parity` / `watermark_lag_days` | **负向测试**：故意篡改一行，断言必须变红 |
| LLM 到底行不行 | 下面第 5 节的评测表 | 13 场景 × 3 次采样，判断层与生效层分列 |

想直接跑：

```bash
git clone https://github.com/muyi-hao2004/ad-analytics.git && cd ad-analytics/iaa-analytics-agent
python -m venv venv && venv/Scripts/pip install -r requirements.txt   # Linux/macOS: venv/bin/pip
python tasks.py test        # 66 个单测，不联网、不调 LLM、不需要 API key
```

---

## 1. 这个仓库解决什么问题

IAA（In-App Advertising）游戏的收入来自广告，投放侧买量花的是真金白银。
日常要回答的问题只有一类：

> **这个渠道 / 素材，该加预算、该观察、还是该砍？**

答案不是查一个数就有的，要跨三张表、两个时间轴，还要区分「样本不够」和「真的差」。
把这件事交给 LLM，会遇到四个工程问题——这个仓库的主线就是逐个解决它们：

| 问题 | 如果不管会怎样 | 本仓库的解法 |
|---|---|---|
| 数据本身不可信 | 门禁缺失时，缺一天数据 → ROI 整体偏低 → 误砍 18 个素材 | 每层出口挂质量门禁，不达标就停 |
| 重跑结果不一致 | 补数后历史值被改写，没人发现，报表和结论对不上 | 幂等 + 内容指纹 + 对账 |
| LLM 看到的是脏上下文 | NaN 被当 0、不成熟 cohort 被当"恶化"、两句结论互相矛盾 | 快照层统一清洗 + 成熟度过滤 + 上次结论回灌 |
| LLM 会改口 | 同一个渠道今天说观察、明天说关停，而数据没变 | 判定层滞回 + 决策日志 |

---

## 2. 系统全景

```
        合成数据（固定种子，可复现）
        scripts/gen_iaa_users.py · gen_iaa_acquisition.py
                    │
                    ▼
┌───────────────────── 五层数仓（Spark + Hive Metastore）─────────────────────┐
│                                                                             │
│  ODS  ods_iaa_user_daily / ods_iaa_acquisition_daily   ← 原样落地（故意注入 0.5% 重复）│
│       │  [门禁 5 条]                                                        │
│  DWD  dwd_user_daily                                    ← 5 条清洗规则 + 首次点击归因 │
│       │  [门禁 3 条]                                                        │
│  DWS  dws_cohort_daily + 3 张全量表                      ← 长表 pivot 成 D0~D7 宽表 │
│       │  [门禁 2 条]                                                        │
│  ADS  ads_daily_detail(13,981×78) / creative_table(6,231×12) / sub_channel_table(341×12) │
│       │  [门禁 8 条]  → 物化成 parquet                                       │
└───────┼─────────────────────────────────────────────────────────────────────┘
        ▼
  warehouse_api（唯一取数入口，ADS_MODE=parquet 5ms / hive 2s）
        ├──▶ Agent 6 个只读工具 → 多轮 Function Calling
        ├──▶ build_snapshot() → 成熟度过滤 + 清洗 + 上次结论 + 指纹
        └──▶ 判定层 decision.py → decision_log（SQLite）→ 同步回数仓
                                        │
                                        ▼
                          护栏二次校验 → 人工 approve → 平台执行
                                        │
                                        ▼
                          决策评测（13 场景 / dev + holdout / 判断层 + 生效层）
```

### 2.1 分层职责

| 层 | 表 | 行数 | 这一层只做一件事 |
|---|---|---|---|
| ODS | `ods_iaa_user_daily` / `ods_iaa_acquisition_daily` | 按天分区 | **只搬不洗**。故意保留 0.5% 重复，用来验证下游去重真的生效 |
| DWD | `dwd_user_daily` | 按天分区 | 丢掉 5 类脏数据，`install_date` 折叠成 `days_since_install` |
| DWS | `dws_cohort_daily` + `dws_campaign_daily` / `dws_creative_daily` / `dws_date_daily` | cohort 按天 / 全量 3 张 | 长表 pivot 成 `d0..d7` 宽表，为 D7 指标准备 |
| ADS | `ads_daily_detail` / `ads_creative_table` / `ads_sub_channel_table` | 13,981 / 6,231 / 341 | 算派生指标，物化 parquet 供中台读 |

**双轴事实表**是这套模型的核心：变现数据同时按 `report_date`（今天赚多少）和
`install_date`（这批用户赚多少）建模。混用会得出相反结论——例如某天收入涨了，
可能只是当天新装多，老用户其实在衰减。

**cohort 成熟度**是第二个必须显式建模的东西：`install_date + 7 ≤ 数据集最大日期`
才有 D7。不标这个，尾部 7 天的 cohort 一定看起来"急剧恶化"。

---

## 3. 五个有证据的工程结论

每一条都是「问题 → 做法 → 怎么验证」，不是功能列表。

### 3.1 幂等：重跑必须逐行相同，不是"不报错"

**问题。** 补数会重写分区。如果链路里有非确定性（随机种子、`groupby` 顺序、
浮点累加顺序），行数和均值几乎不变，但明细已经换了——报表和结论对不上，而且没人发现。

**做法。** 三层约束：

1. 造数固定种子，且**全量生成再过滤**，不用 `--days 1` 局部生成
2. 写入统一 `INSERT OVERWRITE ... PARTITION(dt)` + `dynamic` 分区覆盖
3. 验证用**内容指纹** `(count(*), sum(hash(所有列)))`——定长、确定性、与行序无关

**验证。**

```bash
python scripts/test_idempotent_iaa.py --dt 2026-10-05 --rounds 2
```

拍 3 次快照（基线 / 第 1 轮 / 第 2 轮）、做 2 次比对，覆盖 10 张表：

| 检查 | 含义 | 实测 |
|---|---|---|
| A 重跑幂等 | 同一参数连跑两遍，10 张表逐行相同 | 通过 |
| B 不改历史 | 跑 `dt=X` 时，对照分区 `2026-10-31` 一个字节都不变 | 通过 |
| C 重跑不改原始值 | 基线与重建结果一致，说明链路无非确定性 | 通过 |

> **这里踩过一个坑，值得说。** 最早用 `--days 1` 做冒烟测试，日志显示"31 分区"，
> 看起来跑的是一天、影响的是全量——实际它只重写了第 1 天，其余 30 天还是旧数据。
> 更深一层：`--dt` 补数会改变随机值，因为 Spark 的 `rand(seed)` 按**分区号**播种，
> 只生成到目标日会让分区数从 248 变 40，同一个种子产出完全不同。
> 修法是全量生成再过滤。**验证分区表必须跨多个分区抽样，只看行数会骗人。**

### 3.2 质量门禁：规则外置，fail-closed

**问题。** 数据错误会沿链路放大。ODS 缺一天，到 ADS 就是 ROI 整体偏低；
等跑到最后才报错，已经不知道该查哪一层。

**做法。**

- **DISPATCH 表 + 统一签名** `(spark, table, rule, dt) -> (status, message)`，
  21 个断言函数、35 条断言，全部写在 `metrics.yaml` 里
- 每层出口挂一次门禁，**出错就在那一层停**
- `except Exception → FAIL`：断言函数自己崩了也算失败，不会"因为没跑完所以通过"

| scope | 覆盖表 | 断言数 | 实测 |
|---|---|---|---|
| `--scope all` | 指标链路 7 张表 | 22 | 22/22 |
| `--scope log` | 日志链路 4 张表 | 13 | 13/13 |

**验证。**

```bash
python scripts/quality_gate_iaa.py --scope all    # 指标链路
python scripts/quality_gate_iaa.py --scope log    # 日志链路
python scripts/quality_gate_iaa.py --list         # 只打印规则，不连 Spark
```

> **为什么 ODS 不查"主键唯一"。** 一开始照搬规范在 ODS 上加了 `no_duplicate_grain`，
> 直接 FAIL 掉 17.8 万组。这不是 bug：ODS 的契约就是"原样落地"，
> 去重是 DWD 的职责。断言写错位置，等于用下游的标准要求上游，
> 最后只能是关掉断言。改成"重复率 0.1%~2%"（实测 0.496%），主键唯一挪到 DWD。

### 3.3 喂给 LLM 的快照：三个静默错误

快照是 LLM 唯一的输入。这里错一点，后面护栏再严也没用。
以下三个都是**不报错、但结论会反**的错误：

| 错误 | 现象 | 根因 | 修法 |
|---|---|---|---|
| 读了没有成熟度标记的表 | 100% 素材 ROI 平均低估 **0.0529**，18 个素材会被误砍 | `creative_table` 没有 `is_mature_d7`，尾部 cohort 的 D7 被当真实值 | 改从 `daily_detail` 读并过滤成熟 cohort |
| NaN 被当成 0 | "留存率 0%" 被当成真实观测 | pandas `sum()` 静默跳过 `NaN`，`float64` 的 `NaN` 会污染聚合 | `Float64` nullable dtype + 按花费加权，全 NULL 记 `None` |
| "不知道"和"是 0"混在一起 | LLM 把缺失当恶化，给出砍量建议 | 明细层缺 `d7_retention` / `d7_ecpm` 两列 | 补列 + 快照里显式写 `"未知"` |

同一份数据，修完后快照从 **7,713 字符涨到 12,322 字符**（多出的部分全是
"判定建议"和"上次结论"）。体积变大是刻意的：**让 LLM 少猜，就得给它更多上下文。**

### 3.4 判定层：滞回，不让 LLM 每天改口

**问题。** 裸阈值会抖。ROI 在 0.50 上下浮动时，今天判"砍"、明天判"观察"，
而业务上什么都没发生。人做决策不会这样，因为他记得昨天说过什么。

**做法。** `decision.py` 是**确定性**判定器（LLM 不参与数值判断）：

- `band_width=0.04` 死区：进死区不改判，像空调恒温器
- `min_consecutive_days=3` 连续确认：连续 3 天越过阈值才改
- `decision_log`：每次判定连同**指纹 + 配置版本 + 理由**一起落库，下次可回溯

滞回不是"让 LLM 更保守"，是**把"上次说了什么"变成输入**。
`decision_log` 是病历本（管回溯），指纹是标签（管别改口）——两者职责不同，都不能省。

```bash
python iaa-analytics-agent/check_decision_layer.py     # 10 项全部通过
```

实测基线（合成数据，用来说明判定层在按阈值工作，不代表真实业务）：

| 渠道 | D7 ROI | 判定 |
|---|---|---|
| CA | 0.838 | 加量 |
| US_A | 0.703 | 维持 |
| JP | 0.635 | 临界，观察 |
| US_B | 0.515 | 砍 |
| GB | 0.455 | 砍 |

素材层面 ROI 区间 GB 0.298~0.606（2.04 倍）、CA 0.514~1.179（2.29 倍），
P10/P50/P90 = 0.4519 / 0.6228 / 0.8799。

### 3.5 跨源一致性：做不到原子，就做到可发现

**问题。** 判定结论写 SQLite（OLTP），指标在 Hive/parquet（OLAP）。
两个数据源没有分布式事务。补数时某天行被 UPSERT，按 `decided_at` 增量同步会漏掉它，
数仓永久停在旧结论上**且不报错**。

**做法。**

1. **全量镜像**而不是增量：源表只有 171 行，全量换"不可能漏"
2. **水位** `max(decided_at)`：数仓新用数仓，SQLite 新（agent 刚写完）用 SQLite，没有第三种情况
3. **整块替换**而非逐行 merge：逐行 patch 出 bug 会得到半新半旧的结论表，报表上永远发现不了
4. **对账断言**：`source_parity` 逐行比对两源；`watermark_lag_days` 专抓"一致地过期"；
   `latest_covers_all` 用 `LEFT ANTI JOIN` 抓漏行

**验证（这是全仓库最重要的一个测试）。**

```bash
python iaa-analytics-agent/check_decision_log_sync.py --idempotent   # 27 条全过
```

其中第 3 组是**负向测试**：复制一份 ODS，篡改其中一行的 `verdict`，
`source_parity` 必须变红。实测报出
`1 行内容不一致 ... crt_gb_a_01_026`。

> **一个从来没红过的断言，不能算断言。**

第 5 组同样关键：插入一条 `data_as_of=2026-10-25` 的探针行后，中台必须切到
`sqlite(delta)` 并算出 `verdict_streak=2`，清理后回到 `warehouse`。

### 3.6 Agent 侧：写操作不是工具

| 内容 | 说明 |
|---|---|
| 原生 Function Calling | `tools=` + `tool_calls` 循环，正确处理 `tool_call_id` 配对，不是"让模型输出 JSON 再正则" |
| 读写分离 | 分析阶段只有 6 个只读工具；写操作**不在工具表里**，只能通过 `submit_decision` 提交建议单 |
| 提案 / 批准分离 | 建议单落 `pending` → 人在 Web 界面 approve → 护栏拿**当前配置**二次校验 → 才执行（防 TOCTOU） |
| 写操作护栏 | 纯函数、fail-closed：预算 1~50000、单次变动 1/3~3 倍、目标 0.1~3.0、动作数 ≤5、ID 正则、强制 reason |
| RAG | 9 篇行业知识（68 块），ChromaDB + bge-m3，向量 + TF-IDF + RRF 混合检索 |

> 关键点：LLM 就算编造一个 `delete_sub_channel` 调用，也会因为注册表里没有而返回 error，
> 不会落到平台。**提议者 ≠ 批准者**，这是设计约束，不是礼貌约定。

---

## 4. 数据字典与语义层

`src/metrics.yaml` 是唯一的口径来源（12 个指标 + 9 个顶层块）：

```yaml
metrics:              # 每个指标的公式、分子分母、实测基线
maturity:             # D7 成熟度规则与适用/不适用范围
decision_thresholds:  # 砍/维持/加量的阶梯 + hysteresis 死区
decision_log:         # 决策日志的粒度、分区列、结论枚举
data_quality:         # 35 条断言的声明
```

Agent 算数**之前**先查语义层，不是靠提示词里写一句"ROI = 收入/花费"。
改阈值只改 YAML，不碰 Python、不重跑测试、不重新部署，而且改动进 git——
**口径是可版本控制的资产，不是埋在代码里的魔法数字**。

字段级说明见 [`iaa-analytics-agent/data_dictionary_user_daily.md`](iaa-analytics-agent/data_dictionary_user_daily.md)
（含"已知未修的问题"一节）。

---

## 5. 评测结果（能拿出来说话的部分）

```
python eval/run_eval.py --repeat 3     # 13 条场景，约 6 分钟 / $0.052
```

13 条场景 = **9 条 dev**（可看着结果调参）+ **4 条 holdout**（只验证，不许看着调）。
下表是 `--repeat 3` **取最坏情况**的数字，不是单次跑的结果。

| 口径 | 判断层 | 生效层 | 含义 |
|---|---|---|---|
| safety（全量 8 条） | 4/8 | **7/8** | 不该动却动了 |
| recall（全量 4 条） | 1/4 | 1/4 | 该动没动 |
| safety（dev 6 条） | 4/6 | **6/6** | 可以看着调的 |
| recall（dev 2 条） | 1/2 | 1/2 | |
| safety（holdout 2 条） | 0/2 | 1/2 | 没见过的题 |
| recall（holdout 2 条） | 0/2 | 0/2 | |

**判断层 = Agent 自己想出来的动作对不对；生效层 = 过完护栏后有多少真会被执行。**

两列必须分开报，因为：修完护栏之后**生效层涨了、判断层一点没变**。
护栏买到的是「不出事」，不是「变聪明」——这两句话要能同时说出口。

**五个发现比分数重要：**

1. **S02 是系统性缺陷（0/3）**：30 次安装的低 ROI 渠道，每次都删，从不看样本量。
   加 `MIN_INSTALLS_FOR_DELETE=100` 后它被拦住，但 Agent 的判断一点没变。
2. **单次跑和跑 3 次差得很远**：同一份代码，单次 safety 判断 7/8，跑 3 次取最坏只剩 4/8。
   **单次结果只能用来发现问题，不能用来报分数。**
3. **护栏收益第一次被量化**：判断 4/8 → 生效 7/8，补上三个缺口，且 S03 的正常删除照常放行
   （**过度拦截 0 次**）。只测"有没有拦住坏的"不测"有没有误伤好的"，很容易把护栏调到过严。
4. **holdout 两轮都抓到 dev 覆盖不到的缺口**：
   - 第一轮 H01：量够（8000 安装）但只跑 3 天 → 加 `MIN_DAYS_FOR_DELETE=7`
   - 第二轮 H05：ROI 185% 但只有 180 安装，它给这个渠道**加了预算**（3 次里 2 次）——
     护栏只拦删除，加预算不看样本量，这是下一个要补的缺口
5. **S03 那次 FAIL 是场景 bug，不是模型退化**：假工具不区分对象 ID。修场景后 3/3 全对，
   现在 `--dry-run` 会自动检查这类问题。

> **为什么必须留 holdout**：dev 调无可调之后再迭代，只会得到"8/8 全过"的假象。
> 这叫 overfitting to the eval，和机器学习里在测试集上调参是同一个错误。

检索评测（20 条用例）：`hit@1 85% / recall@3 100% / MRR 0.900`，
四次迭代对比见 [`eval/README.md`](iaa-analytics-agent/eval/README.md)。
其中两个反直觉结果：**给 chunk 补标题反而从 85% 降到 80%**（同文档块互相霸榜）；
**每次检索重建 ChromaDB client 慢 10 倍以上**（等于每次重载 2.2GB 嵌入模型）。

---

## 6. 快速开始

### 6.1 路径 A：不装 Spark，3 分钟验证工程约束

```bash
cd iaa-analytics-agent
python -m venv venv
venv/Scripts/pip install -r requirements.txt          # Linux/macOS: venv/bin/pip
python tasks.py test                                  # 66 个单测，零成本
python tasks.py check                                 # 依赖自检（区分必需/可选）
```

不需要 API key、不联网、不碰真实平台——单测全部用 `FakeClient` 和桩实现，
所以 CI 可以直接跑（见 `.github/workflows/ci.yml`）。

### 6.2 路径 B：全量重建五层数仓（约 20 分钟）

前置：**Docker Desktop（Hive Metastore）+ JDK 17 + Python 3.11/3.12**。

```bash
cd ad-analytics

# ① 起元数据服务（9083）与 Postgres
docker compose -f infra/docker-compose.yml up -d

# ② Spark 专用环境（pyspark 4.0.3，与 py 的 4.2 分开，避免 schema 行为差异）
python -m venv .venv-spark
.venv-spark/Scripts/pip install pyspark==4.0.3 pandas pyarrow

# ③ 造数（固定种子，31 天）→ ODS
.venv-spark/Scripts/python scripts/gen_iaa_users.py

# ④ 逐层构建
.venv-spark/Scripts/python scripts/build_dwd_iaa.py --all
.venv-spark/Scripts/python scripts/gen_iaa_acquisition.py
.venv-spark/Scripts/python scripts/build_dws_iaa.py --all
.venv-spark/Scripts/python scripts/build_ads_iaa.py     # 顺便导出 parquet + 写 _pipeline.json

# ⑤ 质量门禁
.venv-spark/Scripts/python scripts/quality_gate_iaa.py --scope all    # 22/22
```

跑完 `iaa-analytics-agent/data/warehouse/ads/` 下会有 8 个 parquet，
其中 `_pipeline.json` 标记这份 ADS 由 Spark 五层产出。

### 6.3 路径 C：逐条验收（挑一个跑就行）

| 命令 | 验什么 | 期望 |
|---|---|---|
| `python iaa-analytics-agent/check_warehouse_integration.py` | 中台 9 项接入（含双模式一致性） | 9 通过 / 0 失败 |
| `python iaa-analytics-agent/check_decision_layer.py` | 判定层 10 项（含回溯字段完整性） | 10 项全过 |
| `.venv-spark/Scripts/python iaa-analytics-agent/check_decision_log_sync.py --idempotent` | 日志入仓 27 条（含负向测试） | 27 条全过 |
| `.venv-spark/Scripts/python scripts/test_idempotent_iaa.py --dt 2026-10-05` | 幂等性 3 组 | 全部通过 |
| `python tasks.py eval --split holdout` | 泛化验证（需 LLM） | 见第 5 节 |

### 6.4 环境说明（踩过的坑，写下来省你时间）

- **两套 Python 环境不能混**：`py` 是 pyspark 4.2，`.venv-spark` 是 4.0.3。
  跑 Spark 一律用 `.venv-spark`，否则 schema 推断行为会不一致。
- **Docker Desktop 关机后不自启**，`lab-metastore` 挂了的表现是
  `Connection refused: 127.0.0.1:9083`，不是代码问题。
- **两代数仓实现写同一个 ADS 目录**：`src/warehouse.py`（pandas 四层，60 列）和
  `scripts/build_ads_iaa.py`（Spark 五层，78 列）。pandas 版读到 `_pipeline.json`
  会**拒绝覆盖**并提示，避免一次误跑的 `tasks.py init` 把 78 列换成 60 列。

---

## 7. 目录结构

```
ad-analytics/
├── scripts/                      # 数仓构建与验收（Spark）
│   ├── gen_iaa_users.py          #   造数 → ODS（固定种子，支持 --dt 补数）
│   ├── build_dwd_iaa.py          #   DWD 清洗（5 条规则，逐条对账）
│   ├── gen_iaa_acquisition.py    #   买量侧事实表
│   ├── build_dws_iaa.py          #   DWS cohort + 3 张全量表
│   ├── build_ads_iaa.py          #   ADS 三表 + 物化 parquet + 防覆盖标记
│   ├── quality_gate_iaa.py       #   质量门禁（21 函数 / 35 断言 / fail-closed）
│   ├── test_idempotent_iaa.py    #   幂等验证（指纹，3 快照 2 比对）
│   └── sync_decision_log_iaa.py  #   决策日志入仓（SQLite → ODS → DWD → ADS）
│
├── iaa-analytics-agent/          # 中台（Agent + 服务）
│   ├── src/
│   │   ├── agent.py              #   Function Calling 循环 + 快照 + 工具
│   │   ├── warehouse_api.py      #   唯一取数入口（parquet / hive 双模式）
│   │   ├── decision.py           #   确定性判定器（滞回）
│   │   ├── guardrails.py         #   写操作护栏（纯函数，fail-closed）
│   │   ├── dim_scd.py            #   维度 SCD2 + decision_log
│   │   ├── metrics.yaml          #   语义层（12 指标 / 35 断言 / 阈值）
│   │   └── app.py                #   FastAPI
│   ├── dags/iaa_warehouse_dag.py #   Airflow 五层流水线（14 节点，每层挂门禁）
│   ├── eval/                     #   决策评测（13 场景 + 检索评测）
│   ├── tests/                    #   66 个单测（含 FakeClient）
│   ├── check_*.py                #   三个验收脚本
│   ├── data_dictionary_user_daily.md
│   └── LIMITATIONS.md            #   能力边界声明
│
├── infra/                        # Hive Metastore + Postgres（Docker）
├── sql/                          # 建表 DDL（dwd / dws）
├── docs/                         # 数据契约、学习笔记（25 篇）
└── learning/notes/               # 每日学习记录（day02~day29）
```

---

## 8. 关键设计决策

| 问题 | 决策 | 理由 |
|---|---|---|
| 为什么写操作不做成 Agent 工具？ | 读写分离 + 建议单 | 如果 LLM 能在分析循环里直接调删除接口，`pending → 人工确认`这层就是装饰 |
| 为什么 approve 之后还要再校验？ | 拿当前配置重跑护栏 | 建议单从生成到被点确认可能隔几小时，配置已变（TOCTOU） |
| 为什么判定层不用 LLM 算？ | 数值判断走确定性代码 | 阈值比较是可测试的；LLM 只负责解释和组合建议，出了问题能定位到哪一段 |
| 为什么要滞回而不是提高阈值？ | 死区 + 连续确认 | 提高阈值会让真该砍的也砍不掉；滞回只抑制抖动，不改变灵敏度 |
| 为什么 ODS 不查主键唯一？ | 断言放 DWD | ODS 契约是"原样落地"，用下游标准要求上游只能导致断言被关掉 |
| 为什么日志同步是全量镜像？ | 171 行，全量换"不可能漏" | 增量按 `decided_at` 会漏掉补数产生的 UPSERT |
| 为什么不给两个数据源加事务？ | 对账断言代替 | 数仓无 UPDATE 语义；**做不到原子，就做到可发现** |
| 为什么 ADS 导出 parquet 而不让 API 直连 Hive？ | 5ms vs 2s | 生产也是批处理算、在线读小结果；上 OLAP 在当前 1.4 万行是过度设计 |
| 为什么数仓用双轴事实表？ | `report_date` + `install_date` | "今天赚多少"和"这批用户赚多少"是两套口径，混用会得出相反结论 |
| 为什么评测要 `--repeat`？ | temperature ≠ 0 | 单跑显示 PASS、跑 3 次取最坏 FAIL。要 pass^k 而不是 pass@k |
| 为什么成本不按"单价 × 次数"估？ | 累加实际 usage | 多轮每轮重发完整历史，输入随轮数呈三角数增长，线性估算必然偏低 |
| 为什么 RAG 只装方法论不装数值？ | 数值走数仓，文本走检索 | 精确数值必须查询，检索会丢精度 |

---

## 9. 踩坑记录

真实踩过、修完、留下验证的坑。这部分比功能列表更能说明问题。

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `--dt` 补数后随机值全变，但表面正常 | Spark `rand(seed)` 按**分区号**播种，分区数变了种子效果就变 | 全量生成再过滤 |
| 2 | 跨表对账"31 天里 1 天不一致" | 同上，两层各自补数 | 同修，之后 0 不一致 |
| 3 | 第二次快照报 `FILE_NOT_EXIST`，文件明明在 | 常驻 Spark 会话的 `InMemoryFileIndex` 缓存过期 | `clearCache()` + `REFRESH TABLE` |
| 4 | `--skip-full` 时 verify 假 FAIL | 新 cohort 对旧全量表 | 加 `full_tables_fresh` 参数区分 |
| 5 | parquet 导出"内容相同、字节不同" | Spark 写列级 min/max 统计时字节序不确定（24 字节差异，全在数据页之后） | 判幂等只用内容指纹，不用文件 md5 |
| 6 | 快照 ROI 全部低估 0.0529 | 读了无成熟度标记的 `creative_table` | 改读 `daily_detail` + 过滤成熟 cohort |
| 7 | `d7_retention` NULL 变成 0.0 | pandas `sum()` 静默跳过 NaN | `Float64` + 按花费加权，全 NULL 记 None |
| 8 | 决策日志只写了展示的 20 条 | 滞回对剩余 140 个素材静默失效 | 写全量，展示才裁剪 |
| 9 | 指纹存进去和读出来不一致 | 在 sanitize **之前**算指纹，`np.float64` 被写成字符串 | 先清洗再算，canonical dumps |
| 10 | 环境变量被父进程 `cp936` 覆盖 | 子进程 env 顺序反了 | `{**os.environ, "PYTHONIOENCODING": "utf-8"}` |

---

## 10. 能力边界

| 证明了 | 没证明 |
|---|---|
| 五层数仓与双轴建模能跑通全链路，且幂等 | 真实规模（千万级）下的性能 |
| 35 条门禁断言可执行、fail-closed、有负向测试 | 这些阈值在真实业务分布下是否合理 |
| 护栏能拦住不合规写操作，且不过度拦截 | 护栏阈值（3 倍、50000）的业务合理性 |
| Agent 会犯特定类型的错，且可被测量 | 这些错在真实数据里的发生频率 |
| 成本可计量、可归因（$0.0016/次，缓存命中 93.3%） | 规模化并发下的成本 |
| 读写分离后 LLM 无法绕过审批 | 多租户、权限体系 |

完整清单见 **[`iaa-analytics-agent/LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md)**，
建议先读这份再判断可信度。

---

## 11. 文档索引

- [`LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md) — 能力边界与已知限制（**先读这份**）
- [`iaa-analytics-agent/README.md`](iaa-analytics-agent/README.md) — 中台详细文档（架构 / 工具 / 护栏 / 成本）
- [`eval/README.md`](iaa-analytics-agent/eval/README.md) — 评测方法论、场景库、三条踩坑记录
- [`data_dictionary_user_daily.md`](iaa-analytics-agent/data_dictionary_user_daily.md) — 字段、口径、判定层、已知未修问题
- [`docs/data_contract.md`](docs/data_contract.md) — 数据契约
- [`docs/notes/`](docs/notes/) — 25 篇主题笔记（数仓分层、Spark 调优、Agent、评测…）
- [`learning/notes/`](learning/notes/) — day02~day29 的每日学习记录

---

## 👤 作者

**Mia** — 深圳大学 数学与应用数学（2026）· 2027 年入学 CityU（东莞）MSc Data Science。
方向：AI 数据工程 / 训练数据工程。

## 📄 许可证

MIT License
