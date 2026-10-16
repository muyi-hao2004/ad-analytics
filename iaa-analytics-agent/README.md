# IAA 广告变现分析中台（iaa-analytics-agent）

一个面向「海外网赚类 IAA 休闲游戏」的广告变现数据分析中台，核心是一个**「数仓 + Agent 双引擎」**：

- **数仓引擎**：Pandas 四层数仓（ODS→DWD→DWS→ADS），双轴 cohort 事实表，产出日常表 / 素材表 / 子渠道表三张业务视图。
- **Agent 引擎**：DeepSeek 大模型 + Function Calling，能自主分析指标、给出优化决策，并通过 MCP / FastAPI 执行（删子渠道、调预算、调目标）。

业务背景：美国 T0 市场，纯广告变现（无内购/订阅），**D7 ROAS 目标 70%**。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 数据处理 / 数仓 | Pandas ≥3.0 · NumPy ≥2.4 · PyArrow（Parquet）· SQLite |
| Agent / LLM | OpenAI SDK（调 DeepSeek）· Function Calling · python-dotenv |
| RAG 知识库 | ChromaDB · sentence-transformers · bge-m3（本地嵌入） |
| 语义层 | PyYAML（`src/metrics.yaml` 声明式指标口径） |
| 接口 | MCP 2.x（给 AI 的工具接口）· FastAPI + Uvicorn（给人的 Web 接口） |
| 前端 | 原生 HTML/CSS/JS 单文件报表（零第三方依赖） |
| 采集 | Playwright（抓 Unity 广告平台配置） |
| 调度 | Apache Airflow 2.10.5（Docker + PostgreSQL） |

完整技术栈清单见 `data_dictionary.md` 与 `src/metrics.yaml`。

---

## 核心架构

```
采集层    Playwright → Unity 广告平台配置
数仓层    Pandas 四层：ODS→DWD→DWS→ADS（Parquet 落盘）
语义层    metrics.yaml → 声明式指标口径（先查口径再算数）
RAG 层    ChromaDB + bge-m3 → 优化经验知识检索
Agent层   DeepSeek(OpenAI SDK) + Function Calling → 自主分析决策
接口层    MCP(给AI) + FastAPI(给人)，复用同一套工具函数
展示层    原生 JS 单文件报表（动态维度分组 + 左上三角 Cohort）
调度层    Airflow 每日自动跑数仓 + Agent
```

**关键设计**：
- **双轴事实表**：`install_date`（cohort 锚点）+ `report_date`（回传日期），一表同时回答"今天赚了多少"和"某批用户赚了多少"。
- **只存原子，派生用时算**：留存率/ROI/eCPM/观次等比率指标不落库，查询时按公式现算，避免口径漂移。
- **语义层 vs RAG 分工**：硬口径走语义层（精确键值查询），软经验走 RAG（相似检索），互不越界。
- **动态 GROUP BY 报表**：日常表默认按 `install_date` 汇总，筛选某维度时该维度作为列展开、行按维度拆细。

---

## 目录结构

```
iaa-analytics-agent/
├── src/                      # 核心源码
│   ├── config.py             # 全局配置（路径 / LLM / 渠道凭据）
│   ├── warehouse.py          # 数仓四层（ODS→DWD→DWS→ADS）
│   ├── prepare_report_data.py# 造数 + 完整数仓调度入口
│   ├── build_report.py       # 生成静态报表 bi_report.html
│   ├── agent.py              # Agent：LLM + Function Calling 工具注册表
│   ├── knowledge_base.py     # RAG：ChromaDB + bge-m3
│   ├── semantic_layer.py     # 语义层：读 metrics.yaml
│   ├── metrics.yaml          # 指标口径定义（12 个指标）
│   ├── mcp_server.py         # MCP Server：封装 9 个工具
│   ├── app.py                # FastAPI 后端
│   ├── dim_scd.py            # 缓慢变化维度（SCD Type 2）
│   ├── platform_layer.py     # 平台操作抽象层
│   └── fetchers/             # Playwright 采集（Unity）
├── data/                     # 数仓数据（ods/dwd/dws/ads + chroma_kb + knowledge）
├── dags/                     # Airflow DAG（每日流水线）
├── models/bge-m3/            # 本地嵌入模型
├── reports/                  # 生成的 HTML 报表
├── mock_pages/               # Playwright 测试用模拟页面
├── data_dictionary.md        # 数据字典（字段/模型/口径）
├── docker-compose.yaml       # Airflow 编排
└── requirements.txt          # Python 依赖
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> bge-m3 嵌入模型需先下载到 `models/bge-m3/`（国内可走 hf-mirror 镜像）：
> ```python
> from huggingface_hub import snapshot_download
> snapshot_download('BAAI/bge-m3', local_dir='models/bge-m3')
> ```

### 2. 配置环境变量

复制 `.env` 填入 LLM 凭据：

```bash
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

### 3. 跑数仓 + 生成报表

```bash
# 造数 + 完整数仓（ODS→DWD→DWS→ADS）
python src/prepare_report_data.py

# 生成静态报表 reports/bi_report.html（直接浏览器打开即可）
python src/build_report.py
```

### 4. 启动后端（可选，用于删除子渠道等写操作）

```bash
python -m uvicorn src.app:app --reload --port 8000
```

### 5. 启动 Agent 每日调度（Airflow）

```bash
docker compose up -d   # 启动 Postgres + Airflow，Web UI: http://localhost:8080 (admin/admin)
```

---

## 三张业务视图

| 表 | 主维度 | 能力 |
|---|---|---|
| **日常表** | install_date | 动态维度分组 + D0~D7 全程指标横铺（左上三角 + 今天标 `*`） |
| **素材表** | creative_id | 前端买量 + 后端变现前后端对齐 |
| **子渠道表** | sub_channel | 归因数据 + 删除子渠道操作 |

---

## Agent 能力（工具注册表）

Agent 通过 Function Calling 可调用 9 个工具（MCP 封装）：

| 工具 | 作用 |
|---|---|
| `get_metric` | 查对象指标 |
| `get_logs` | 查操作日志 |
| `get_top_history` | 排行（最该优化的排最前） |
| `get_trend_with_ops` | 趋势 + 操作下钻 |
| `search_knowledge` | RAG 检索优化经验 |
| `get_metric_definition` | 查指标口径（语义层） |
| `delete_sub_channel` | 删子渠道 |
| `adjust_target` | 调目标值 |
| `adjust_budget` | 调预算 |

---

## 指标口径（语义层）

12 个核心指标定义在 `src/metrics.yaml`，命名统一：

- **买量侧**：`cpi`、`buy_ctr`、`buy_cvr`、`buy_ecpm`
- **变现侧**：`d7_roi`、`d7_retention`、`monetization_ecpm`、`rewarded_views_per_user`
- **全程（第 N 天）**：`retention_dn`、`roi_dn`、`ecpm_dn`、`views_dn`

> 关键区分：买量 `eCPM`（成本视角，`spend/buy_impressions`）与变现 `eCPM`（收入视角，`ad_revenue/ad_impressions`）同名不同义，绝不可混用。

---

## 相关文档

- `data_dictionary.md` — 数据字典（字段、数仓模型、口径）
- `src/metrics.yaml` — 语义层指标口径
- `learning/notes/` — 学习笔记（day01 ~ day22）
