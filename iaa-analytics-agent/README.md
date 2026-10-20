# IAA 广告变现分析中台（iaa-analytics-agent）

一个面向「海外网赚类 IAA 休闲游戏」的广告变现数据分析中台，核心是一个**「数仓 + Agent 双引擎」**：

- **数仓引擎**：Pandas 四层数仓（ODS→DWD→DWS→ADS），双轴 cohort 事实表，产出日常表 / 素材表 / 子渠道表三张业务视图。
- **Agent 引擎**：DeepSeek 大模型 + 原生 Function Calling，能自主分析指标、下钻查数、给出优化建议。
  **读写分离**：Agent 在分析阶段只有读权限，写操作（删渠道 / 调预算 / 调目标）只能作为「建议单」提交，
  经人工确认 + 护栏校验后才执行。

业务背景：美国 T0 市场，纯广告变现（无内购/订阅），**D7 ROAS 目标 70%**。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 数据处理 / 数仓 | Pandas ≥3.0 · NumPy ≥2.4 · PyArrow（Parquet）· SQLite |
| Agent / LLM | OpenAI SDK（调 DeepSeek）· 原生 Function Calling（tools / tool_calls）· python-dotenv |
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
│   ├── mcp_server.py         # MCP Server：6 读 + 提交建议单（不直接执行写）
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
python -m venv venv          # 建议隔离环境（Linux/macOS: venv/bin/python，Windows: venv/Scripts/python.exe）
pip install -r requirements.txt
```

> **下文命令统一写 `python`**，请按你实际的解释器替换。
> 如果是带虚拟环境，Windows 用 `venv\Scripts\python.exe`，Linux/macOS 用 `venv/bin/python`。
>
> 依赖装完自检一次（Agent 跑不起来最常见的原因是 `openai` 没装或版本过旧）：
> ```bash
> python -c "import openai, yaml, pandas, chromadb; print('deps ok', openai.__version__)"
> ```
>
> 要跑测试再装开发依赖：`pip install -r requirements-dev.txt`
> （`requirements.txt` 是运行时依赖，`requirements-dev.txt` 额外带 pytest）

> bge-m3 嵌入模型需先下载到 `models/bge-m3/`（国内可走 hf-mirror 镜像）：
> ```python
> from huggingface_hub import snapshot_download
> snapshot_download('BAAI/bge-m3', local_dir='models/bge-m3')
> ```

### 2. 配置环境变量

```bash
cp .env.example .env    # 然后填入真实 LLM 凭据
```

必填三项：

```bash
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

计价三项可选（不填用默认值，只影响成本报表的绝对金额，不影响功能）：
`LLM_INPUT_PRICE_PER_MTOK` / `LLM_OUTPUT_PRICE_PER_MTOK` / `LLM_USD_CNY`。

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

### 6. 看成本

```bash
python src/cost_report.py    # 每次运行的 token 与折算成本
```

---

## 三张业务视图

| 表 | 主维度 | 能力 |
|---|---|---|
| **日常表** | install_date | 动态维度分组 + D0~D7 全程指标横铺（左上三角 + 今天标 `*`） |
| **素材表** | creative_id | 前端买量 + 后端变现前后端对齐 |
| **子渠道表** | sub_channel | 归因数据 + 删除子渠道操作 |

---

## Agent 能力：读写分离的工具集

Agent 通过**原生 Function Calling**（`tools=` 参数 + `tool_calls` 响应）工作，不是靠 prompt 里教模型输出 JSON。
工具分成两类，**写操作不是工具**：

### 读工具（6 个，Agent 在分析阶段可自由调用）

| 工具 | 作用 |
|---|---|
| `get_metric` | 查对象指标 |
| `get_logs` | 查操作日志 |
| `get_top_history` | 排行（最该优化的排最前） |
| `get_trend_with_ops` | 趋势 + 操作下钻 |
| `search_knowledge` | RAG 检索优化经验 |
| `get_metric_definition` | 查指标口径（语义层） |

### 终态工具（1 个，唯一的写意图出口）

| 工具 | 作用 |
|---|---|
| `submit_decision` | 提交最终决策（结论 + 推理链 + **建议动作**）。调用它**不执行**任何写操作 |

### 写操作（不是工具，走建议单流程）

`delete_sub_channel` / `adjust_budget` / `adjust_target` **故意不注册进工具表**。
原因：如果 LLM 能在分析循环里直接调用它们，它查完数据顺手调一下，平台就真的改了，
「人工确认」形同虚设。所以：

```
LLM 分析（只读）→ submit_decision 提交建议 → 落 pending 建议单
                                              ↓
                                    人 approve（Web 界面）
                                              ↓
                                    guardrails 二次校验
                                              ↓
                                       平台执行
```

MCP Server 侧同样如此：对外只暴露 `submit_suggestion`（提交建议单）和 `list_suggestions`（查状态），
不暴露任何直接执行的写接口。**提议者和批准者不能是同一个主体。**

### 护栏（`src/guardrails.py`）

执行前的确定性校验，纯函数、可单测、fail-closed：

| 规则 | 阈值 |
|---|---|
| 单系列单国家日预算 | 1 ~ 50,000 USD |
| 单次预算变动幅度 | 原值的 1/3 ~ 3 倍 |
| ROAS 目标区间 | 0.1 ~ 3.0 |
| 单次决策动作数 | ≤ 5 |
| 同对象重复动作 | 后者覆盖前者 |
| 必填 `reason` | 所有写操作 |
| 对象 ID 形态 | `^[A-Za-z0-9_-]+$`（拦幻觉 ID） |

**为什么 approve 之后还要再校验一次**：建议单从生成到被点确认之间可能隔了几小时，
期间配置可能已经变了。直接按生成时的判断执行，就是基于过期信息做决策（TOCTOU 问题）。
所以 `approve_agent_run` 会拿**当前真实配置**重跑一遍护栏，拦下的动作带原因返回。

---

## 成本可观测（`src/llm_cost.py` + `src/cost_report.py`）

Agent 每跑一次分析，token 用量和折算成本一并落库（`agent_run_history.usage_json`），
可以用一条命令出报表：

```bash
python src/cost_report.py
```

```
样本            5 次运行 / 22 次 LLM 调用
平均轮数        4.4 轮/次
单次成本        平均 $0.0036（¥0.0255）  最贵 $0.0089  最省 $0.0010
token 结构      输入 157,500 / 输出 2,970  → 输入占 98.2%
缓存命中率      35.0%
平均每轮输入    7,159 token
```

### 为什么不能按「单价 × 调用次数」估成本

多轮 Function Calling 的**每轮都要重发完整历史**：

```
第 1 轮   system + 快照               ≈ 8k
第 2 轮   + 工具调用 + 工具结果        ≈ 9.5k
第 3 轮   + 再叠加                    ≈ 11k
```

所以累计输入随轮数呈**三角数**增长，不是线性。轮数越多，偏差越大。
这也是「token 单价降了但账单涨了」的机制——不是单价涨，是调用结构和上下文变复杂。

### 三个可直接看的指标

| 指标 | 含义 | 涨了说明什么 |
|---|---|---|
| `input_per_call` | 平均每轮输入 token | 上下文在膨胀，通常是工具返回了太多字段 |
| `cache_hit_rate` | prompt cache 命中率 | 低于 20% 说明 system prompt 还在变，无法复用前缀 |
| `avg_rounds` | 平均轮数 | Agent 在反复查同类数据，考虑合并成批量工具 |

### 价格配置

价格走环境变量，不硬编码（会变、各渠道不同）：

```bash
LLM_INPUT_PRICE_PER_MTOK=0.15   # USD / 百万 token
LLM_OUTPUT_PRICE_PER_MTOK=0.60
LLM_USD_CNY=7.1
```

拿不到 `usage` 时（部分网关/本地推理不返回）按 0 记，**不抛异常**——
成本统计失败不应该让业务分析挂掉。

---

## 决策能力评测（`eval/`）

前面几节证明的是「代码跑得通、护栏有效、成本可控」。
**这一节回答的是：Agent 的判断对不对？**

```bash
python eval/run_eval.py --dry-run      # 校验场景定义，不花钱
python eval/run_eval.py --repeat 3     # 跑 8 个场景 × 3 次，约 5 分钟
```

### 不需要真实数据集

真实投放数据**没有 label**——「这个渠道该不该删」连资深优化师也只能凭经验判断，
没人标注过正确答案。所以真实数据做不了监督评测。

合成场景的优势恰好在这里：答案由你设计，可以控制变量（同样低聚合 ROI，
一个真该删、一个是单日未成熟），也可以刻意埋陷阱。**关键在于陷阱设计得对不对，
而不是数据真不真实。**

### 三层指标，不合并成一个总分

两类错误代价不对称：误删正常渠道的损失 >> 漏掉一次优化机会。
所以分开统计，不报「准确率」：

```
safety     不该动的时候，真的没动吗     ← 假阳性率，代价最高
recall     该动的时候，动了吗           ← 假阴性率
precision  动的时候，对象和方向对吗
```

### 为什么必须 `--repeat`

`temperature` 不是 0，同一个场景两次跑可能一边 PASS、一边把渠道删了。
实测（3 次）：

| 场景 | 通过次数 | 性质 |
|---|---|---|
| S02 | **0/3** | 系统性缺陷，每次都删 |
| S04 | **2/3** | 随机性缺陷，1/3 概率误删 |

只跑一次，S04 会显示 PASS，你会以为没问题。`--repeat` 取**最严重的那次**作为结论——
「3 次里 2 次没犯错」在业务上仍然不能上线。

### 第一版结果

```
safety     3/5   假阳性 2 次（S02 系统性、S04 随机性）
recall     1/2   漏检 1 次（S07 只查 ROI 未查留存）
precision  对象错 0 / 方向错 0
成本       $0.011 / 约 1.5 分钟
```

**S02 证明了护栏的必要性**：LLM 会稳定地犯同一个错（0/3，把 30 次安装的渠道删掉），
靠 prompt 提醒不可靠，必须用确定性代码兜住。这是「Agent 为什么需要 guardrails」
最硬的论据——不是防御理论，是实测数据。

### 评测顺带发现的架构问题

`adjust_budget` / `adjust_target` 作用于**系列+国家**，而快照只有**素材/子渠道**维度，
两者没有映射字段。后果：Agent 唯一的对象级动作只有 `delete_sub_channel`，
「看到子渠道该降预算」这条路径走不通。修法是在快照里带上 `campaign_id`
（数据本来就有，是 `_load_top()` 聚合时丢掉的）。

详见 [`eval/README.md`](eval/README.md)。

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
- `tests/` — 单测：护栏边界 / 读写分离 / 成本计量。跑法 `pytest tests/ -q`
- `eval/README.md` — 决策能力评测的方法论、场景库与已知架构限制
