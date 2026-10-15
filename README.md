# IAA 广告变现分析中台（IAA Monetization Analytics Platform）

> 面向海外网赚类 IAA（广告变现）业务的分析中台，覆盖「数据建模 → 四层数仓 → 调度编排 → 数据分析 Agent → RAG 行业经验库」全链路。业务背景：美国 T0 市场，D7 ROAS 目标 70%。

一个能回答真实业务问题的系统：优化师用自然语言提问（"为什么这个渠道的 D7 ROAS 跌了？"），Agent 自动查数仓、调工具、查行业经验，给出带依据的结论，并对高危操作（如删子渠道）要求人工确认。

---

## ✨ 核心亮点（面试可展开讲）

| 亮点 | 说明 |
|---|---|
| **双轴事实表 + SCD Type 2** | 变现数据同时按 `report_date` 和 `install_date` 双日期建模，解决"今天赚多少 vs 这批用户赚多少"；维度表用 SCD2 追踪渠道/计划历史变化 |
| **四层数仓** | ODS → DWD → DWS → ADS，买量/变现拆表，素材、子渠道前后端表，全链路可回溯 |
| **Agent 多轮 Function Calling** | 8 个工具（查指标/趋势/日志/横向对比/知识库检索/写操作），LLM 自主规划多步工具调用，非一次性问答 |
| **人机协作安全闸** | 高危写操作（删子渠道等）先分析、后经人工确认才执行，`dry_run` 只分析不落地 |
| **RAG 行业经验库** | ChromaDB 向量库 + bge-m3 中文嵌入，把优化师方法论做成可检索知识，弥补 LLM 不懂"自家玩法" |
| **可解释性** | 决策附带"数据依据 + 操作日志 + 知识库参考"，不是黑盒输出 |
| **Airflow Docker 编排** | Postgres 元数据库 + LocalExecutor 并行，每日定时 pipeline |

---

## 🏗️ 系统架构

```
                    ┌─────────────────────────────────────────┐
                    │            数据分析 Agent (agent.py)      │
                    │  多轮 Function Calling · 8 工具 · 人工确认 │
                    └──────┬──────────────┬──────────────┬─────┘
                           │ 查数仓        │ 查经验        │ 写操作
                           ▼              ▼              ▼
              ┌──────────────┐   ┌──────────────┐   ┌──────────┐
              │  四层数仓      │   │  RAG 知识库   │   │ 平台抽象层 │
              │ ODS/DWD/DWS/ │   │ ChromaDB+    │   │ FastAPI   │
              │ ADS (Parquet)│   │ bge-m3 嵌入   │   │ 后端 API  │
              └──────▲───────┘   └──────────────┘   └────▲─────┘
                     │ 数据加工                              │ 配置抓取
              ┌──────┴───────┐                     ┌────────┴─────┐
              │  Airflow 调度  │                     │  Playwright   │
              │ (Docker+PG)  │                     │  抓取配置     │
              └──────────────┘                     └──────────────┘
```

**数据链路**：Airflow 每日定时 → 抓取广告平台配置/数据 → 数仓四层加工 → Agent 按需查询分析 → 生成决策/报告。

---

## 📂 目录结构

```
ad-analytics/
├── iaa-analytics-agent/                          # ★ IAA 中台（核心项目）
│   ├── src/
│   │   ├── config.py                # 全局配置（路径/字段/业务常量）
│   │   ├── warehouse.py             # 四层数仓（ODS/DWD/DWS/ADS）
│   │   ├── dim_scd.py               # SCD Type 2 慢变维
│   │   ├── agent.py                 # 数据分析 Agent（8 工具 Function Calling）
│   │   ├── knowledge_base.py        # RAG 知识库（ChromaDB + bge-m3）
│   │   ├── app.py                   # FastAPI 后端
│   │   ├── platform_layer.py        # 平台抽象层（BasePlatform/工厂）
│   │   ├── build_report.py          # 报表生成
│   │   └── fetchers/                # Playwright 配置抓取（策略模式）
│   ├── dags/iaa_daily_pipeline.py   # Airflow DAG（每日 pipeline）
│   ├── data/knowledge/              # 优化师经验文档（RAG 语料）
│   ├── data_dictionary.md           # 数据字典（指标口径/字段定义）
│   ├── docker-compose.yaml          # Airflow Docker 编排（Postgres）
│   └── Dockerfile
├── learning/                        # 学习过程归档（不属中台本体）
│   ├── notes/                       # day02~day21 自学笔记
│   ├── practice/ spark_basics/ fastapi_demo/ rag_demo/ 等练习
└── src/ad_analytics/                # 早期单体版（中台的雏形）
```

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.10+（中台在 3.13/3.14 下验证）
- Docker（跑 Airflow 需）
- 依赖：`fastapi` / `pandas` / `chromadb` / `sentence-transformers` / `openai` / `playwright`

### 2. 初始化数仓 + 知识库

```bash
cd iaa-analytics-agent/src

# 建四层数仓 + 维度表（含 SCD2）
python init_campaign_dims.py
python demo_warehouse.py

# 建 RAG 知识库（bge-m3 中文嵌入，首次会下载约 2.27GB 模型）
python init_knowledge.py
```

### 3. 跑数据分析 Agent

```bash
cd iaa-analytics-agent/src
python demo_multi_round.py    # 多轮 Function Calling 演示
```

配置 `.env`（参考 `iaa-analytics-agent/.env`）：`LLM_API_KEY`（deepseek）等。

### 4. 启动 Airflow（Docker）

```bash
cd iaa-analytics-agent
docker compose up -d
# Web UI: http://localhost:8080 (admin/admin)
# 每日 08:00 自动跑 update_warehouse → run_agent
```

### 5. 启动后端 API

```bash
cd iaa-analytics-agent/src
python app.py    # FastAPI，含 /api/agent 等接口
```

---

## 🔑 关键设计决策（为什么这么做）

| 问题 | 决策 | 理由 |
|---|---|---|
| 为什么用 LLM+工具+知识库，而不是训练模型？ | Agent + RAG | 优化场景「输出不唯一 + 数据量小」，训练模型欠拟合/过拟合；LLM 有预训练常识，工具补精确数据，RAG 补领域经验 |
| 为什么 RAG 只装经验、不装精确数值？ | 数值走数仓，文本走 RAG | 精确数值必须写 SQL 查（RAG 检索会丢精度），经验是语义匹配 |
| 为什么数仓用双轴事实表？ | report_date + install_date | 变现数据"今天赚多少"和"这批用户赚多少"是两套口径 |
| 为什么 Agent 要人工确认开关？ | 写操作先确认 | 删子渠道等操作不可逆，人机协作防误删 |

---

## 🧠 技术栈

| 层级 | 技术 |
|---|---|
| 数据建模 | 星型模型、SCD Type 2、双轴事实表 |
| 数仓 | Parquet 落盘、ODS/DWD/DWS/ADS 分层 |
| 调度 | Apache Airflow（Docker + Postgres + LocalExecutor） |
| Agent | OpenAI Function Calling（deepseek）、多轮对话、工具注册 |
| RAG | ChromaDB、bge-m3 嵌入（sentence-transformers）、语义检索 |
| 后端 | FastAPI、平台抽象层、Playwright |

---

## 📄 参考文档

- `iaa-analytics-agent/data_dictionary.md` — 数据字典（15 字段口径、指标定义）
- `learning/notes/` — 学习过程笔记（day02~day21）

## 👤 作者

**Mia** — 深圳大学数学本科，目标 2027 年港校数据科学硕士。方向：AI 数据工程 / MLOps / 大模型应用。

## 📄 许可证

MIT License
