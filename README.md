# IAA 广告变现分析中台（IAA Monetization Analytics Platform）

> **定位：学习型项目 + Agent 工程化参考实现，不是生产系统。**
> 业务数据为合成，RAG 语料为通用方法论。哪些是真的、哪些是壳，见
> **[`iaa-analytics-agent/LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md)**。
>
> 这个仓库要展示的不是"我做过投放优化业务"，而是：
> **一个 Agent 系统该有哪些工程约束，以及怎么证明这些约束真的生效。**

---

## 一句话说明

围绕「海外 IAA 休闲游戏广告变现」这个业务主题，走通一条完整链路：

```
合成数据 → 四层数仓（ODS/DWD/DWS/ADS）→ 语义层口径
    → Agent 多轮 Function Calling 分析
    → 护栏校验 → 人工确认 → 执行
    → 决策评测（8 场景 / 三层指标）→ 成本计量
```

业务主题（CPA/ROAS/eCPM 口径、D7 ROAS 70% 红线、素材→子渠道→国家下钻）
来自实习期间的真实业务理解，**数据是合成的，判断逻辑不是编的**。

---

## ✨ 核心内容

### 数据侧

| 内容 | 说明 |
|---|---|
| **双轴事实表 + SCD Type 2** | 变现数据同时按 `report_date` 和 `install_date` 建模，区分"今天赚多少"与"这批用户赚多少"；维度表用 SCD2 追踪历史变化 |
| **四层数仓** | ODS → DWD → DWS → ADS，买量/变现拆表，产出日常表 / 素材表 / 子渠道表三张视图 |
| **语义层** | `metrics.yaml` 统一 16 个指标口径，防止"同名不同义"（eCPM 分买量侧和变现侧两套算法） |

### Agent 侧

| 内容 | 说明 |
|---|---|
| **原生 Function Calling** | `tools=` + `tool_calls` 循环，正确处理 `tool_call_id` 配对，不是"让模型输出 JSON 再正则解析" |
| **读写分离** | 分析阶段只有 **6 个只读工具**；写操作**不是工具**，只能通过 `submit_decision` 提交建议单 |
| **提案 / 批准分离** | 建议单落 `pending` → 人在 Web 界面 approve → 护栏二次校验 → 才执行。**提议者和批准者不能是同一主体** |
| **写操作护栏** | 纯函数、fail-closed：预算 1~50000、单次变动 1/3~3 倍、目标 0.1~3.0、动作数 ≤5、ID 形态正则、强制 reason |
| **RAG 检索链路** | ChromaDB + bge-m3 中文嵌入，语料 2 篇通用优化方法论。**价值在演示检索链路（分块/嵌入/相似度检索），不在语料规模** |

### 工程侧（这部分才是最值得看的）

| 内容 | 说明 |
|---|---|
| **决策评测** | 8 个场景、三层指标（safety / recall / precision）、`--repeat` 多次采样。**故意做成对设计**：同样低 ROI，一个该不动、一个该动 |
| **成本可观测** | 每次运行记录 token 与折算成本，`cost_report.py` 出报表。实测单次 $0.0016，缓存命中 93.3% |
| **单测 49 个** | 护栏边界 / 读写分离（含 LLM 硬调写工具被拒）/ 成本计量，不依赖网络 |
| **能力边界声明** | `LIMITATIONS.md`：哪些是模拟的、为什么、替代方案是什么 |

---

## 📊 评测结果（这才是能拿出来说话的东西）

```
python eval/run_eval.py --repeat 3
```

| 指标 | 结果 | 含义 |
|---|---|---|
| safety | 3/5 | 假阳性 2 次（不该动却动了） |
| recall | 1/2 | 漏检 1 次（该动没动） |
| precision | 0 错 | 动作对象与方向都对 |

**三个发现比分数重要：**

1. **S02 是系统性缺陷（0/3）**——30 次安装的低 ROI 渠道，每次都删，从不看样本量。
2. **S04 是随机性缺陷（2/3）**——整体 35% 但美国 95%，1/3 概率整体删。
   单跑一次会显示 PASS。**单次通过率 66.7% 的 Agent，pass³ ≈ 29.6%**，业务上仍不能上线。
3. **LLM 会稳定犯同一个错**，靠 prompt 提醒不可靠 → 这是「Agent 为什么需要 guardrails」
   的实测依据，不是防御理论。

**评测还顺带发现一个架构问题**（`adjust_budget` 作用于「系列+国家」，而快照只有
「素材/子渠道」维度，无映射字段），已在 eval 中标为 `known_gap`，不计入指标。

---

## 🧪 这项目证明了什么、没证明什么

| 证明了 | 没证明 |
|---|---|
| 四层数仓与双轴建模能跑通全链路 | 在真实规模数据上的性能 |
| 护栏能拦住不合规写操作（49 单测 + 6 条冒烟） | 护栏阈值（3 倍、50000）在真实业务里是否合理 |
| Agent 会犯特定类型的错，且可被测量 | 这些错在真实数据分布里的发生频率 |
| 成本可计量、可归因 | 规模化并发下的成本 |
| 读写分离后 LLM 无法绕过审批 | —— |

完整清单见 **[`LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md)**。

---

## 🏗️ 系统架构

```
                    ┌──────────────────────────────────────────┐
                    │        Agent（分析阶段：只读）             │
                    │  原生 Function Calling · 6 个读工具        │
                    └──────┬──────────────┬──────────────┬──────┘
                           │ 查数仓        │ 查口径        │ 提交建议
                           ▼              ▼              ▼
              ┌──────────────┐   ┌──────────────┐  ┌──────────────┐
              │  四层数仓      │   │ 语义层 + RAG  │  │ submit_decision│
              │ ODS/DWD/DWS/ │   │ metrics.yaml │  │  （不执行）    │
              │ ADS (Parquet)│   │ ChromaDB     │  └──────┬───────┘
              └──────▲───────┘   └──────────────┘         │
                     │ 数据加工                            ▼
              ┌──────┴───────┐              ┌──────────────────────┐
              │  Airflow 调度  │              │  pending 建议单        │
              │ (Docker+PG)  │              └──────────┬───────────┘
              └──────────────┘                         │ 人工 approve
                                                       ▼
                                          ┌────────────────────────┐
                                          │ guardrails 二次校验      │
                                          │ （拿当前配置重算幅度）    │
                                          └────────────┬───────────┘
                                                       ▼
                                                 ┌──────────┐
                                                 │ 平台执行   │
                                                 └──────────┘
```

> 关键点：**写操作不在工具表里**。LLM 就算编造一个 `delete_sub_channel` 调用，
> 也会因为注册表里没有而返回 error，不会落到平台。

---

## 🚀 快速开始

> **跨平台任务入口**（不需要 make，Windows 可直接用）：
> ```bash
> cd iaa-analytics-agent
> python tasks.py check    # 依赖自检（区分必需/可选）
> python tasks.py all      # check → 建数仓 → 跑测试，全程不花钱
> python tasks.py eval --repeat 3
> ```
> 下面分步命令与 `tasks.py` 等价，看你想控制哪一步。

### 1. 依赖

```bash
cd iaa-analytics-agent
python -m venv venv
pip install -r requirements.txt          # 运行时依赖
pip install -r requirements-dev.txt      # 额外带 pytest
```

自检（Agent 跑不起来最常见原因是 `openai` 没装或版本过旧）：

```bash
python -c "import openai, yaml, pandas; print('deps ok', openai.__version__)"
```

### 2. 初始化数仓（不含 RAG）

```bash
cd src
python init_campaign_dims.py     # 建维度表（含 SCD2）
python demo_warehouse.py         # 建四层数仓
```

### 3. 跑 Agent（需要 LLM 凭据）

```bash
cp .env.example .env    # 填 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
cd src
python demo_multi_round.py       # 多轮 Function Calling 演示，会打印成本
```

### 4. 跑测试（不需要网络、不需要 LLM）

```bash
cd iaa-analytics-agent
python -m pytest tests/ -q
```

### 5. 跑决策评测（需要 LLM，约 $0.01 / 1.5 分钟）

```bash
python eval/run_eval.py --dry-run          # 只校验场景定义，不花钱
python eval/run_eval.py --repeat 3         # 8 场景 × 3 次
```

### 6. 看成本

```bash
python src/cost_report.py
```

### 7.（可选）建 RAG 知识库

**需要下载 bge-m3 模型（约 2.27 GB）。** 不建也能跑其余全部功能——
Agent 调 `search_knowledge` 时会返回空结果，不影响其他工具。

```bash
cd src
python init_knowledge.py
```

### 8.（可选）Airflow 调度 / FastAPI

```bash
cd iaa-analytics-agent && docker compose up -d    # Airflow: localhost:8080 (admin/admin)
cd src && python app.py                            # FastAPI
```

---

## 🔑 关键设计决策（为什么这么做）

| 问题 | 决策 | 理由 |
|---|---|---|
| 为什么写操作不做成 Agent 工具？ | 读写分离 + 建议单 | 如果 LLM 能在分析循环里直接调删除接口，`pending → 人工确认`这层就是装饰。**提议者 ≠ 批准者** |
| 为什么 approve 之后还要再校验一次？ | 拿当前配置重跑护栏 | 建议单从生成到被点确认可能隔几小时，期间配置可能已变。直接按旧判断执行 = 用过期信息做决策（TOCTOU） |
| 为什么用 LLM+工具+检索，不训练模型？ | Agent 路线 | 优化场景「输出不唯一 + 单点数据量小」，训练模型欠拟合；LLM 有先验，工具补精确数据 |
| 为什么 RAG 只装方法论、不装数值？ | 数值走数仓，文本走检索 | 精确数值必须查询（检索会丢精度），方法论是语义匹配 |
| 为什么数仓用双轴事实表？ | `report_date` + `install_date` | 「今天赚多少」和「这批用户赚多少」是两套口径，混用会得出相反结论 |
| 为什么评测要 `--repeat`？ | temperature ≠ 0 | 实测 S04 三次里有一次误删，单跑显示 PASS。取**最严重那次**而非多数（pass^k 而非 pass@k） |
| 为什么成本不能按「单价 × 调用次数」估？ | 累加实际 usage | 多轮每轮重发完整历史，累计输入随轮数呈三角数增长，线性估算必然偏低 |

---

## 🧠 技术栈

| 层级 | 技术 |
|---|---|
| 数据建模 | 星型模型、SCD Type 2、双轴事实表 |
| 数仓 | Parquet 落盘、ODS/DWD/DWS/ADS 分层、Pandas |
| 调度 | Apache Airflow（Docker + Postgres + LocalExecutor） |
| Agent | OpenAI SDK（调 DeepSeek）、原生 Function Calling、工具注册表 |
| Agent 安全 | 读写分离、建议单审批、确定性护栏（fail-closed）、TOCTOU 二次校验 |
| RAG | ChromaDB、bge-m3（sentence-transformers）、语义检索 |
| 评测 | 自建 golden set（8 场景）、programmatic scorer、三层指标、pass^k |
| 后端 | FastAPI、平台抽象层、Playwright |
| 测试 | pytest（49 个，含 FakeClient 模拟 tool_calls） |

---

## 📄 参考文档

- **[`iaa-analytics-agent/LIMITATIONS.md`](iaa-analytics-agent/LIMITATIONS.md)** — 能力边界与已知限制。**先读这份再判断可信度**
- [`iaa-analytics-agent/README.md`](iaa-analytics-agent/README.md) — 中台详细文档（架构 / 工具 / 护栏 / 成本）
- [`iaa-analytics-agent/eval/README.md`](iaa-analytics-agent/eval/README.md) — 评测方法论、场景库、三条踩坑记录
- `iaa-analytics-agent/data_dictionary.md` — 数据字典（字段与口径）
- `iaa-analytics-agent/src/metrics.yaml` — 语义层 16 个指标定义
- `learning/notes/` — 学习过程笔记（day02 ~ day29）

---

## 👤 作者

**Mia** — 深圳大学数学与应用数学（2026）· 2027 年入学 CityU（东莞）MSc Data Science。
方向：AI 数据工程 / 训练数据工程。

## 📄 许可证

MIT License
