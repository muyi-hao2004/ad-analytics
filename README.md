# IAA 广告变现分析中台

[![CI](https://github.com/muyi-hao2004/ad-analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/muyi-hao2004/ad-analytics/actions/workflows/ci.yml)

![端到端链路](docs/images/architecture.svg)

围绕海外 IAA 休闲游戏的广告变现，做了一条完整链路：合成数据、五层数仓、质量门禁、
语义层、LLM 快照、Agent 决策、护栏审批、决策评测。

数据是造的，业务口径和判断逻辑来自实习时的真实工作；RAG 语料取自公开报告，
每条都标了来源和时间。哪些是真的、哪些只是占位，写在
[`LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md) 里。

这个仓库想说明的主要是一件事：让 LLM 参与业务决策，工程上要补哪些约束，
以及这些约束怎么验证。

---

## 0. 如果只有 10 分钟

| 你可能想知道 | 打开这个文件 | 能看到什么 |
|---|---|---|
| 数仓怎么分层，数据凭什么可信 | [`scripts/quality_gate_iaa.py`](scripts/quality_gate_iaa.py) 和 [`metrics.yaml`](iaa-analytics-agent/src/metrics.yaml) | 35 条断言写在 YAML 里，不达标直接中断流水线 |
| 怎么确认重跑不会算错 | [`scripts/test_idempotent_iaa.py`](scripts/test_idempotent_iaa.py) | 同一天重跑 3 次，10 张表逐行比对 |
| 送给 LLM 的数据怎么处理 | [`src/agent.py`](iaa-analytics-agent/src/agent.py) 里的 `build_snapshot` | 成熟度过滤、NaN 转 null、缺数据写「不知道」 |
| LLM 为什么不会每天改口 | [`src/decision.py`](iaa-analytics-agent/src/decision.py) | 死区、连续确认、决策日志 |
| 两个数据源怎么保证一致 | `check_source_parity` | 篡改一行做负向测试，断言必须报错 |
| LLM 的判断到底行不行 | 第 5 节 | 13 个场景、3 次采样，判断层和生效层分开报 |

想直接跑：

```bash
git clone https://github.com/muyi-hao2004/ad-analytics.git
cd ad-analytics/iaa-analytics-agent
python -m venv venv && venv/Scripts/pip install -r requirements.txt
python tasks.py test        # 66 个单测，不联网、不调 LLM、不需要 API key
```

Linux/macOS 把 `venv/Scripts/pip` 换成 `venv/bin/pip`。

---

## 1. 要解决的问题

IAA 游戏的收入来自广告，投放侧花的是真金白银。日常要回答的问题其实只有一个：

> 这个渠道、这个素材，该加预算、该观察，还是该砍？

这件事不好交给人手算。要看三张表、两个时间轴，还要分清「样本太少」和「真的不行」。
交给 LLM 也不行，会冒出四个工程问题：

| 问题 | 不管它会怎样 | 仓库里的做法 |
|---|---|---|
| 数据本身不可信 | 少一天数据，ROI 整体偏低，一批素材被误砍 | 每层出口挂门禁，不达标就停 |
| 重跑结果不一致 | 补数后历史值被改写，报表和结论对不上 | 幂等、内容指纹、跨表对账 |
| 给 LLM 的上下文是脏的 | NaN 当 0，不成熟的 cohort 当恶化，前后结论打架 | 快照层统一清洗，加成熟度过滤和上次结论 |
| LLM 会改口 | 同一个渠道今天说观察、明天说关停，数据没变 | 判定层加滞回，结论落日志 |

---

## 2. 分层与表

| 层 | 表 | 行数 | 这一层只做一件事 |
|---|---|---|---|
| ODS | `ods_iaa_user_daily`、`ods_iaa_acquisition_daily` | 按天分区 | 原样落地。故意留 0.5% 重复数据，用来验证下游去重真的在跑 |
| DWD | `dwd_user_daily` | 按天分区 | 丢掉 5 类脏数据，把 `install_date` 折成 `days_since_install` |
| DWS | `dws_cohort_daily` 加 3 张全量表 | cohort 按天，全量表 3 张 | 长表 pivot 成 `d0..d7` 宽表，给 D7 指标准备 |
| ADS | `ads_daily_detail`、`ads_creative_table`、`ads_sub_channel_table` | 13,981 / 6,231 / 341 | 算派生指标，物化成 parquet 给中台读 |

两个建模上的决定，后面很多问题都跟它们有关。

**双轴事实表。** 变现数据同时按 `report_date` 和 `install_date` 建模。
前者回答「今天赚了多少」，后者回答「这批用户赚了多少」。混用会得出相反结论，
比如某天收入涨了，可能只是当天新装多，老用户在衰减。

**cohort 成熟度。** 只有 `install_date + 7 ≤ 数据集最大日期`，这批用户才有 D7 数据。
不标这个标记，最后 7 天的 cohort 一定看起来在急剧恶化。

---

## 3. 五件可以验证的事

每一条都按「问题、做法、怎么验证」写。

### 3.1 幂等：重跑要得到同一个结果

补数会重写分区。链路里只要有一处不确定，比如随机种子、`groupby` 顺序、浮点累加顺序，
行数和均值可能都没变，明细已经换了。这种错误不会报，只会让报表和结论悄悄对不上。

做了三件事：

1. 造数固定种子，并且全量生成再过滤，不用 `--days 1` 局部生成
2. 写入统一用 `INSERT OVERWRITE ... PARTITION(dt)` 加 dynamic 分区覆盖
3. 验证用内容指纹 `(count(*), sum(hash(所有列)))`，与行序无关

```bash
python scripts/test_idempotent_iaa.py --dt 2026-10-05 --rounds 2
```

脚本拍 3 次快照、做 2 次比对，覆盖 10 张表：

| 检查 | 比什么 | 结果 |
|---|---|---|
| A 重跑幂等 | 同一参数连跑两遍，10 张表是否逐行相同 | 通过 |
| B 不改历史 | 跑 `dt=X` 时，对照分区 `2026-10-31` 有没有被动过 | 通过 |
| C 重跑不改原始值 | 基线与重建结果是否一致 | 通过 |

这里有个教训。最早用 `--days 1` 做冒烟测试，日志显示「31 分区」，
看着像只跑一天、影响全量，实际只重写了第 1 天，其余 30 天还是旧数据。
再往下查，`--dt` 补数会改掉随机值，因为 Spark 的 `rand(seed)` 是按分区号播种的：
只生成到目标日，分区数从 248 变成 40，同一个种子出来的数完全不同。

所以验证分区表要跨多个分区抽样，只看行数会被骗。

### 3.2 质量门禁：规则写在配置里，出错就停

数据错误会顺着链路放大。ODS 少一天，到 ADS 就是 ROI 整体偏低。
等跑到最后才报错，已经不知道该查哪一层。

做法是把断言外置。`quality_gate_iaa.py` 里是一张 DISPATCH 表，21 个断言函数，
签名统一成 `(spark, table, rule, dt) -> (status, message)`；具体查哪些表、阈值多少，
全部写在 `metrics.yaml`。每层出口挂一次门禁，出问题就在那一层停下。

断言函数自己抛异常也算 FAIL，不会出现「没跑完所以通过」这种结果。

| scope | 覆盖 | 断言数 | 实测 |
|---|---|---|---|
| `--scope all` | 指标链路 7 张表 | 22 | 22/22 |
| `--scope log` | 日志链路 4 张表 | 13 | 13/13 |

```bash
python scripts/quality_gate_iaa.py --scope all
python scripts/quality_gate_iaa.py --scope log
python scripts/quality_gate_iaa.py --list     # 只列规则，不连 Spark
```

ODS 上最初照规范加了「主键唯一」，一跑 FAIL 掉 17.8 万组。查下来不是 bug：
ODS 的契约就是原样落地，去重是 DWD 的活。断言写错位置，最后只能被关掉。
改成「重复率在 0.1% 到 2% 之间」（实测 0.496%），主键唯一挪到 DWD。

### 3.3 快照：三个不报错但结论会反的坑

快照是 LLM 唯一的输入。这里错一点，后面护栏再严也没用。

| 错误 | 现象 | 根因 | 修法 |
|---|---|---|---|
| 读了没有成熟度标记的表 | 素材 ROI 平均低估 0.0529，18 个素材会被误砍 | `creative_table` 没有 `is_mature_d7`，尾部 cohort 的 D7 被当成真实值 | 改从 `daily_detail` 读，过滤掉不成熟 cohort |
| NaN 被当成 0 | 「留存率 0%」被当成真实观测 | pandas 的 `sum()` 会静默跳过 `NaN` | 换 `Float64`，按花费加权，全 NULL 记 `None` |
| 「不知道」和「是 0」混在一起 | LLM 把缺数据当恶化，给出砍量建议 | 明细层缺 `d7_retention`、`d7_ecpm` 两列 | 补列，快照里显式写「未知」 |

同一份数据，修完之后快照从 7,713 字符涨到 12,322 字符。
多出来的部分是「判定建议」和「上次结论」。体积变大是有意的：想让 LLM 少猜，就得多给它上下文。

### 3.4 判定层：把数值判断从 LLM 手里拿走

裸阈值会抖。ROI 在 0.50 附近浮动时，今天判「砍」、明天判「观察」，业务上什么都没发生。
人不会这样，因为他记得昨天说过什么。

`decision.py` 是确定性判定器，LLM 不参与数值比较，只负责解释和组织建议：

- `band_width=0.04` 的死区，进了死区就不改判
- `min_consecutive_days=3`，连续 3 天越过阈值才改
- 每次判定连同指纹、配置版本、理由一起写进 `decision_log`，下次可以回溯

滞回不是让 LLM 更保守，是把「上次说了什么」变成输入。

```bash
python iaa-analytics-agent/check_decision_layer.py     # 10 项全部通过
```

合成数据上的基线（用来说明判定层在按阈值工作，不代表真实业务）：

| 渠道 | D7 ROI | 判定 |
|---|---|---|
| CA | 0.838 | 加量 |
| US_A | 0.703 | 维持 |
| JP | 0.635 | 临界，观察 |
| US_B | 0.515 | 砍 |
| GB | 0.455 | 砍 |

素材层面，GB 的 ROI 区间 0.298~0.606（2.04 倍），CA 是 0.514~1.179（2.29 倍），
全站 P10/P50/P90 是 0.4519 / 0.6228 / 0.8799。

### 3.5 两个数据源：不做事务，做对账

判定结论写在 SQLite，指标在 Hive 和 parquet，两套数据源，没有分布式事务。

同步有一处坑：按 `decided_at` 做增量会漏。补数时某天的行会被 UPSERT，
`data_as_of` 没变，`decided_at` 变新，增量窗口扫不到它，数仓就永远停在旧结论上，
而且不报错。源表只有 171 行，所以直接全量镜像。

写进数仓之后，一致性靠三条断言兜住：

- `source_parity` 逐行比对两源
- `watermark_lag_days` 抓「两边一起停在旧数据」这种反常的一致
- `latest_covers_all` 用 `LEFT ANTI JOIN` 抓漏掉的对象

```bash
python iaa-analytics-agent/check_decision_log_sync.py --idempotent   # 27 条全过
```

其中第 3 组是负向测试：复制一份 ODS，改掉一行 `verdict`，`source_parity` 必须报错。
实测报出 `1 行内容不一致 ... crt_gb_a_01_026`。

一个从来没红过的断言，不能算断言。

第 5 组验增量水印：插一条 `data_as_of=2026-10-25` 的探针行，
中台要切到 `sqlite(delta)` 并算出 `verdict_streak=2`，清理后回到 `warehouse`。

### 3.6 Agent 侧：写操作不进工具表

| 内容 | 说明 |
|---|---|
| 原生 Function Calling | `tools=` 加 `tool_calls` 循环，处理好 `tool_call_id` 配对，不是让模型吐 JSON 再正则 |
| 读写分离 | 分析阶段只有 6 个只读工具。写操作不在工具表里，只能通过 `submit_decision` 提交建议单 |
| 提案与批准分离 | 建议单落 `pending`，人在 Web 界面批准，护栏拿当前配置再校验一次，然后才执行 |
| 写操作护栏 | 纯函数，fail-closed：预算 1~50000、单次变动 1/3~3 倍、目标 0.1~3.0、动作数 ≤5、ID 正则、强制填 reason |
| RAG | 9 篇行业知识、68 块，ChromaDB 加 bge-m3，向量与 TF-IDF 混合检索，RRF 融合 |

批准时要重算，是因为建议单从生成到被点确认可能隔几个小时，期间配置可能已经变了。
按旧判断直接执行，就是用过期信息做决策（TOCTOU）。

LLM 就算编一个 `delete_sub_channel` 调用，注册表里没有，直接返回 error，不会落到平台。

---

## 4. 口径写在 YAML 里

`src/metrics.yaml` 是唯一的口径来源，12 个指标，下面几个块：

```yaml
metrics:              # 公式、分子分母、实测基线
maturity:             # D7 成熟度规则，以及哪些查询要套、哪些不能套
decision_thresholds:  # 砍 / 维持 / 加量的阶梯，含 hysteresis 死区
decision_log:         # 决策日志的粒度、分区列、结论枚举
data_quality:         # 35 条断言
```

Agent 算数之前先查语义层，不靠提示词里写一句「ROI = 收入 / 花费」。
改阈值只改 YAML，不动 Python，不用重跑测试，也不用重新部署。改动还能进 git，
谁哪天把阈值调了、为什么调，`git log` 里看得到。

字段级说明在
[`data_dictionary_user_daily.md`](iaa-analytics-agent/data_dictionary_user_daily.md)，
最后一节列了已知未修的问题。

---

## 5. 评测结果

![判断层与生效层](docs/images/eval-layers.svg)

```
python eval/run_eval.py --repeat 3     # 13 条场景，约 6 分钟 / $0.052
```

13 条场景分两组：9 条 dev，可以看着结果调参；4 条 holdout，只用来验证，不许看着调。
下表是 `--repeat 3` 取最坏情况，不是单次跑的数字。

| 口径 | 判断层 | 生效层 | 含义 |
|---|---|---|---|
| safety（全量 8 条） | 4/8 | 7/8 | 不该动却动了 |
| recall（全量 4 条） | 1/4 | 1/4 | 该动没动 |
| safety（dev 6 条） | 4/6 | 6/6 | 可以看着调的 |
| recall（dev 2 条） | 1/2 | 1/2 | |
| safety（holdout 2 条） | 0/2 | 1/2 | 没见过的题 |
| recall（holdout 2 条） | 0/2 | 0/2 | |

判断层是 Agent 自己想出来的动作对不对，生效层是过完护栏之后有多少真的会执行。
两列必须分开报，因为修完护栏以后，生效层涨了，判断层一点没变。
护栏买到的是「不出事」，不是「变聪明」。

几个比分数更值得说的发现：

1. **S02 是系统性缺陷，3 次全错。** 30 次安装的低 ROI 渠道，每次都删，从不看样本量。
   加了 `MIN_INSTALLS_FOR_DELETE=100` 之后它被拦住，但 Agent 的判断没变。
2. **单次跑和跑 3 次差很远。** 同一份代码，单次 safety 判断 7/8，跑 3 次取最坏只剩 4/8。
   差别来自 S04、H03、H04 在不同轮次里翻转。单次结果只能用来发现问题，不能拿来报分数。
3. **护栏的收益第一次被量化。** 判断 4/8，生效 7/8，补上三个缺口，
   同时 S03 的正常删除照常放行，过度拦截 0 次。只测「有没有拦住坏的」而不测「有没有误伤好的」，
   很容易把护栏调得过严。
4. **holdout 两轮都抓到 dev 覆盖不到的地方。**
   第一轮 H01：量够（8000 安装）但只跑了 3 天，于是加了 `MIN_DAYS_FOR_DELETE=7`。
   第二轮 H05：ROI 185% 但只有 180 安装，它给这个渠道加预算，3 次里 2 次。
   护栏只拦删除，加预算不看样本量，这是下一个要补的缺口。
5. **S03 那次 FAIL 是场景本身的问题。** 它报「明细与聚合矛盾」，查下来是假工具不区分对象 ID。
   修完场景 3/3 全对。现在 `--dry-run` 会自动检查这类问题。

留 holdout 的理由很直接：dev 那几条调无可调之后再迭代，只会得到 8/8 全过的假象。
这和机器学习里在测试集上调参是同一个错误。

检索评测 20 条用例，`hit@1 85% / recall@3 100% / MRR 0.900`。
四次迭代的对比在 [`eval/README.md`](iaa-analytics-agent/eval/README.md)。
里面有两个反直觉结果：给 chunk 补标题反而从 85% 降到 80%，原因是同文档的块互相霸榜；
每次检索重建 ChromaDB client 会慢十倍以上，等于每次重新加载 2.2GB 的嵌入模型。

评测还顺带发现一个架构问题：`adjust_budget` 作用在「系列加国家」维度，
而快照只有「素材、子渠道」维度，没有映射字段。已在 eval 里标成 `known_gap`，不计入指标。

---

## 6. 怎么跑起来

### 路径 A：不装 Spark，先验证工程约束

```bash
cd iaa-analytics-agent
python -m venv venv
venv/Scripts/pip install -r requirements.txt
python tasks.py test       # 66 个单测，零成本
python tasks.py check      # 依赖自检，区分必需和可选
```

单测全部用 `FakeClient` 和桩实现，不联网、不需要 key，所以 CI 能直接跑。

### 路径 B：全量重建五层数仓

需要 Docker Desktop（跑 Hive Metastore）、JDK 17、Python 3.11 或 3.12。全链路约 20 分钟。

```bash
cd ad-analytics

# 起元数据服务和 Postgres
docker compose -f infra/docker-compose.yml up -d

# Spark 专用环境。pyspark 4.0.3，和系统 py 的 4.2 分开
python -m venv .venv-spark
.venv-spark/Scripts/pip install pyspark==4.0.3 pandas pyarrow

# 造数落 ODS
.venv-spark/Scripts/python scripts/gen_iaa_users.py

# 逐层构建
.venv-spark/Scripts/python scripts/build_dwd_iaa.py --all
.venv-spark/Scripts/python scripts/gen_iaa_acquisition.py
.venv-spark/Scripts/python scripts/build_dws_iaa.py --all
.venv-spark/Scripts/python scripts/build_ads_iaa.py

# 门禁
.venv-spark/Scripts/python scripts/quality_gate_iaa.py --scope all
```

跑完 `iaa-analytics-agent/data/warehouse/ads/` 下会有 8 个 parquet，
其中 `_pipeline.json` 标记这份 ADS 是 Spark 五层产出的。

### 路径 C：逐条验收

| 命令 | 验什么 | 期望 |
|---|---|---|
| `python iaa-analytics-agent/check_warehouse_integration.py` | 中台 9 项接入，含双模式一致性 | 9 通过 / 0 失败 |
| `python iaa-analytics-agent/check_decision_layer.py` | 判定层 10 项，含回溯字段 | 10 项全过 |
| `.venv-spark/Scripts/python iaa-analytics-agent/check_decision_log_sync.py --idempotent` | 日志入仓 27 条，含负向测试 | 27 条全过 |
| `.venv-spark/Scripts/python scripts/test_idempotent_iaa.py --dt 2026-10-05` | 幂等性 3 组 | 全部通过 |
| `python tasks.py eval --split holdout` | 泛化验证，需要 LLM | 见第 5 节 |

### 几个环境上的注意点

- 两套 Python 不能混用。`py` 是 pyspark 4.2，`.venv-spark` 是 4.0.3，
  跑 Spark 一律用后者，否则 schema 推断会有差异。
- Docker Desktop 关机后不会自启。`lab-metastore` 没起来的表现是
  `Connection refused: 127.0.0.1:9083`，不是代码问题。
- 两代数仓实现写的是同一个 ADS 目录。`src/warehouse.py` 是 pandas 版，60 列；
  `scripts/build_ads_iaa.py` 是 Spark 版，78 列。pandas 版读到 `_pipeline.json` 会拒绝覆盖，
  避免误跑一次 `tasks.py init` 把 78 列换成 60 列。

---

## 7. 目录

```
ad-analytics/
├── scripts/                      # 数仓构建与验收（Spark）
│   ├── gen_iaa_users.py          #   造数落 ODS，固定种子，支持 --dt 补数
│   ├── build_dwd_iaa.py          #   DWD 清洗，5 条规则，逐条对账
│   ├── gen_iaa_acquisition.py    #   买量侧事实表
│   ├── build_dws_iaa.py          #   DWS cohort 加 3 张全量表
│   ├── build_ads_iaa.py          #   ADS 三表，导出 parquet，写防覆盖标记
│   ├── quality_gate_iaa.py       #   门禁，21 个函数、35 条断言
│   ├── test_idempotent_iaa.py    #   幂等验证，3 次快照 2 次比对
│   └── sync_decision_log_iaa.py  #   决策日志入仓，SQLite 到 ODS 到 ADS
│
├── iaa-analytics-agent/          # 中台
│   ├── src/
│   │   ├── agent.py              #   Function Calling 循环、快照、工具
│   │   ├── warehouse_api.py      #   唯一取数入口，parquet 与 hive 双模式
│   │   ├── decision.py           #   确定性判定器
│   │   ├── guardrails.py         #   写操作护栏，纯函数
│   │   ├── dim_scd.py            #   维度 SCD2 和 decision_log
│   │   ├── metrics.yaml          #   语义层
│   │   └── app.py                #   FastAPI
│   ├── dags/iaa_warehouse_dag.py #   Airflow 五层流水线，14 个节点
│   ├── eval/                     #   决策评测和检索评测
│   ├── tests/                    #   66 个单测
│   ├── check_*.py                #   三个验收脚本
│   ├── data_dictionary_user_daily.md
│   └── LIMITATIONS.md
│
├── docs/images/                  # 图，含生成脚本 gen_diagrams.py
├── infra/                        # Hive Metastore 和 Postgres
├── sql/                          # 建表 DDL
├── docs/                         # 数据契约、主题笔记 25 篇
└── learning/notes/               # 每日学习记录 day02~day29
```

---

## 8. 设计决策

| 问题 | 决定 | 理由 |
|---|---|---|
| 写操作要不要做成 Agent 工具 | 不做 | 如果 LLM 能在分析循环里直接调删除接口，那层人工确认就是摆设 |
| 批准之后要不要再校验 | 要，拿当前配置重算 | 建议单放几小时再点确认，这期间配置可能已经变了 |
| 数值判断为什么不用 LLM | 用确定性代码 | 阈值比较可以测试，出问题能定位到哪一段 |
| 为什么用滞回而不是提高阈值 | 滞回 | 提高阈值会让真该砍的也砍不掉，滞回只压抖动，不改变灵敏度 |
| ODS 要不要查主键唯一 | 不查 | ODS 契约是原样落地，用下游标准要求上游，最后只会被关掉 |
| 日志同步用全量还是增量 | 全量 | 源表 171 行，全量不会漏掉补数产生的 UPSERT |
| 两个数据源要不要加事务 | 不加，用对账 | 数仓没有 UPDATE 语义；做不到原子，就做到能发现 |
| ADS 为什么不直接让 API 连 Hive | 导出 parquet | 5ms 和 2s 的差别；生产也是批处理算、在线读小结果 |
| 为什么用双轴事实表 | 两个日期轴 | 两套口径混用会得出相反结论 |
| 评测为什么要 `--repeat` | temperature 不为 0 | 单跑显示 PASS，跑 3 次取最坏就 FAIL；要 pass^k 而不是 pass@k |
| 成本为什么不能按单价乘次数估 | 累加实际 usage | 多轮每轮都重发完整历史，输入随轮数呈三角数增长 |
| RAG 为什么不装数值 | 只装方法论 | 精确数值必须查询，检索会丢精度 |

---

## 9. 踩过的坑

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `--dt` 补数后随机值全变，表面看不出异常 | `rand(seed)` 按分区号播种，分区数变了结果就变 | 全量生成再过滤 |
| 2 | 跨表对账显示「31 天里 1 天不一致」 | 同上，两层各自补数 | 同修，之后 0 不一致 |
| 3 | 第二次快照报 `FILE_NOT_EXIST`，文件明明在 | 常驻 Spark 会话的 `InMemoryFileIndex` 缓存过期 | `clearCache()` 加 `REFRESH TABLE` |
| 4 | `--skip-full` 时 verify 假 FAIL | 新 cohort 对旧全量表 | 加 `full_tables_fresh` 参数 |
| 5 | parquet 导出内容相同、字节不同 | 写列级 min/max 统计时字节序不确定，差 24 字节，全在数据页之后 | 判幂等用内容指纹，不用文件 md5 |
| 6 | 快照 ROI 全部低估 0.0529 | 读了没有成熟度标记的 `creative_table` | 改读 `daily_detail`，过滤成熟 cohort |
| 7 | `d7_retention` 的 NULL 变成 0.0 | pandas 的 `sum()` 跳过 NaN | 换 `Float64`，按花费加权 |
| 8 | 决策日志只写了展示的 20 条 | 剩下 140 个素材下次「上次」为空，滞回对它们失效 | 写全量，展示时再裁剪 |
| 9 | 指纹存进去和读出来不一致 | 在清洗之前算指纹，`np.float64` 被写成字符串 | 先清洗再算 |
| 10 | 环境变量被父进程的 cp936 覆盖 | 子进程 env 顺序写反了 | `{**os.environ, "PYTHONIOENCODING": "utf-8"}` |

---

## 10. 能证明什么，不能证明什么

| 能证明 | 不能证明 |
|---|---|
| 五层数仓和双轴建模能跑通全链路，而且幂等 | 千万级数据量下的性能 |
| 35 条断言可执行、fail-closed、有负向测试 | 这些阈值在真实业务分布下是否合理 |
| 护栏能拦住不合规写操作，且没有过度拦截 | 护栏参数（3 倍、50000）在真实业务里合不合适 |
| Agent 会犯某些类型的错，而且能测出来 | 这些错在真实数据里的发生频率 |
| 成本可计量，单次 $0.0016，缓存命中 93.3% | 并发规模下的成本 |
| 读写分离之后 LLM 绕不过审批 | 多租户和权限体系 |

完整清单在 [`LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md)，建议先看这份再判断可信度。

---

## 11. 文档

- [`LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md)：能力边界和已知限制
- [`iaa-analytics-agent/README.md`](iaa-analytics-agent/README.md)：中台详细文档，架构、工具、护栏、成本
- [`eval/README.md`](iaa-analytics-agent/eval/README.md)：评测方法、场景库、三轮踩坑
- [`data_dictionary_user_daily.md`](iaa-analytics-agent/data_dictionary_user_daily.md)：字段、口径、判定层
- [`docs/data_contract.md`](docs/data_contract.md)：数据契约
- [`docs/notes/`](docs/notes/)：25 篇主题笔记
- [`learning/notes/`](learning/notes/)：day02 到 day29 的学习记录
- 图由 [`docs/images/gen_diagrams.py`](docs/images/gen_diagrams.py) 生成，改数字后重跑脚本即可

---

## 作者

Mia，深圳大学数学与应用数学（2026），2027 年入学 CityU（东莞）MSc Data Science。
方向是 AI 数据工程和训练数据工程。

## 许可证

MIT
