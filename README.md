# 广告投放智能分析平台 (Ad Analytics Platform)

> 从0到1搭建的生产级广告投放数据分析系统，覆盖数据采集、清洗、指标计算、异常检测、数据库存储、可视化报表全流程。后续将扩展 RAG 知识库和数据分析 Agent。

## 📋 项目简介

本项目以**海外广告投放数据分析**为业务场景，模拟真实数据工程师的工作流程：从原始广告数据（CSV/API）出发，经过清洗、计算、检测，最终输出可交互的 HTML 日报和异常告警。

项目采用**模块化工程化设计**，每个模块职责清晰，可独立测试和复用，是学习 Python 数据工程的完整实战项目。

## ✨ 功能特性

### 已完成（第一阶段 V1）

| 模块 | 功能 | 状态 |
|---|---|---|
| **数据加载** | 支持 CSV 文件读取、API 数据拉取、数据校验、数据摘要 | ✅ |
| **数据清洗** | 缺失值填充、重复值删除、类型转换、字段标准化、异常值处理、零曝光过滤 | ✅ |
| **指标计算** | CTR/CVR/CPC/CPA/ROI 等核心广告指标，支持按渠道/日期/计划多维度汇总、环比、移动平均、排名 | ✅ |
| **异常检测** | 5种异常检测：花费暴涨、点击率过低、转化率过低、CPA过高、零曝光，支持严重程度分级 | ✅ |
| **数据库存储** | MySQL 数据库，支持建库建表、数据写入、SQL查询、批量导入 | ✅ |
| **报表生成** | Jinja2 模板引擎生成 HTML 日报，包含核心指标卡片、渠道汇总表、每日趋势表、异常告警列表 | ✅ |
| **工程化** | 配置管理（.env）、日志系统、虚拟环境、单元测试、代码格式化 | ✅ |

### 规划中（第二、三阶段）

- [ ] 数据仓库（ODS/DWD/DWS/ADS 四层分层）
- [ ] ETL 自动化（Spark + Airflow）
- [ ] API 服务（FastAPI + Redis 缓存）
- [ ] RAG 知识库（广告平台规则、操作手册智能问答）
- [ ] 数据分析 Agent（自然语言查数、自动生成报告）
- [ ] 可视化界面（Streamlit）
- [ ] Docker 容器化部署

## 🛠 技术栈

| 层级 | 技术 |
|---|---|
| **语言** | Python 3.14 |
| **数据处理** | Pandas 3.0, NumPy 2.5 |
| **数据库** | MySQL 8.0, SQLAlchemy, PyMySQL |
| **报表模板** | Jinja2 3.1 |
| **HTTP请求** | Requests |
| **配置管理** | python-dotenv, PyYAML |
| **测试** | pytest, pytest-cov |
| **代码质量** | black, flake8 |
| **项目管理** | pip, venv, pyproject.toml |

## 📂 项目结构

```
ad-analytics/
├── src/
│   └── ad_analytics/              # 主包
│       ├── __init__.py            # 包标识（版本号、作者）
│       ├── config.py              # 配置管理（Settings类，.env环境变量）
│       ├── logger.py              # 日志配置（统一格式、控制台+文件输出）
│       ├── data_loader.py         # 数据加载（CSV/API读取、校验、摘要）
│       ├── cleaning.py            # 数据清洗（6步清洗流程、清洗报告）
│       ├── metrics.py             # 指标计算（核心广告指标、多维度汇总、环比、排名）
│       ├── anomaly_detection.py   # 异常检测（5种异常检测、严重程度分级）
│       ├── database.py            # 数据库操作（MySQL连接、建表、写入、查询）
│       └── report.py              # 报表生成（Jinja2模板、HTML日报）
├── tests/                         # 单元测试
│   ├── test_data_loader.py        # 数据加载模块测试（11个测试）
│   └── test_cleaning.py           # 数据清洗模块测试（19个测试）
├── scripts/                       # 验证脚本
│   ├── generate_sample_data.py    # 生成模拟广告数据
│   ├── verify_day2.py             # Day2验证
│   ├── verify_day3.py             # Day3验证
│   ├── verify_day6.py             # Day6验证（数据库全流程）
│   ├── verify_day8.py             # Day8验证（异常检测）
│   └── verify_day9.py             # Day9验证（报表生成全流程）
├── data/                          # 数据目录（.gitignore忽略）
│   ├── raw/                       # 原始数据（sample_ad_data.csv，1069条）
│   └── processed/                 # 处理后数据（anomalies.csv等）
├── reports/                       # 生成的报表（HTML日报）
├── docs/                          # 项目文档
│   └── notes/                     # 学习笔记（Day2-Day9）
├── practice/                      # 练习目录
├── main.py                        # 项目入口（一键运行全流程）
├── pyproject.toml                 # 项目配置（现代Python项目）
├── requirements.txt               # 依赖清单
├── .env                           # 环境变量（密码等敏感信息，.gitignore忽略）
├── .env.example                   # 环境变量示例
├── .gitignore                     # Git忽略配置
└── README.md                      # 项目说明（本文件）
```

## 🔄 数据流向（完整流程）

```
原始数据 (CSV/API)
    ↓
[数据加载] data_loader.py
    ├─ 读取CSV/API
    ├─ 数据校验
    └─ 数据摘要
    ↓
[数据清洗] cleaning.py
    ├─ 字段标准化
    ├─ 类型转换
    ├─ 删除重复值
    ├─ 处理缺失值
    ├─ 处理异常值
    └─ 零曝光过滤
    ↓
[指标计算] metrics.py
    ├─ 基础指标（CTR/CVR/CPC/CPA）
    ├─ 整体指标汇总
    ├─ 按渠道/日期/计划分组
    ├─ 环比计算
    ├─ 移动平均
    └─ 排名
    ↓
[异常检测] anomaly_detection.py
    ├─ 花费暴涨检测
    ├─ 点击率过低检测
    ├─ 转化率过低检测
    ├─ CPA过高检测
    └─ 零曝光检测
    ↓
[数据库存储] database.py
    ├─ 初始化数据库（建库建表）
    └─ 写入清洗后的数据
    ↓
[报表生成] report.py
    ├─ 准备核心指标
    ├─ 准备渠道汇总
    ├─ 准备每日趋势
    ├─ 准备异常列表
    ├─ Jinja2模板渲染
    └─ 生成HTML日报
    ↓
输出：HTML报表 + 异常CSV + 数据库数据
```

## 🚀 快速开始

### 1. 环境要求

- Python 3.10+（本项目使用 Python 3.14）
- MySQL 8.0+（可选，不配置数据库也能运行，会跳过数据库步骤）

### 2. 克隆项目

```bash
git clone <repo-url>
cd ad-analytics
```

### 3. 创建虚拟环境并安装依赖

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows (PowerShell)
.\venv\Scripts\Activate.ps1
# Windows (CMD)
.\venv\Scripts\activate.bat
# Mac/Linux
source venv/bin/activate

# 安装依赖（开发模式，包本身也安装）
pip install -e .
```

### 4. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入你的配置
# 主要配置：
# DB_HOST=localhost
# DB_PORT=3306
# DB_USER=root
# DB_PASSWORD=你的密码
# DB_NAME=ad_analytics
```

### 5. 生成模拟数据（首次运行）

```bash
python scripts/generate_sample_data.py
```

会在 `data/raw/sample_ad_data.csv` 生成1069条模拟广告投放数据（30天、5个渠道、9个计划）。

### 6. 运行项目（一键全流程）

```bash
python main.py
```

运行后会自动完成：数据加载 → 清洗 → 指标计算 → 异常检测 → 写入数据库 → 生成报表，全程有详细日志输出。

### 7. 查看报表

运行完成后，在 `reports/` 目录下找到生成的 HTML 日报，双击用浏览器打开即可查看。

### 8. 运行单元测试

```bash
# 运行所有测试
pytest tests/ -v

# 查看测试覆盖率
pytest tests/ --cov=src/ad_analytics --cov-report=term-missing
```

## 📊 项目成果

### 数据规模

| 指标 | 数值 |
|---|---|
| 原始数据 | 1069 条记录 |
| 时间范围 | 30天（2026-08-01 ~ 2026-08-30） |
| 渠道数量 | 5个（Facebook/Google/TikTok/Instagram/YouTube） |
| 广告计划 | 9个 |
| 清洗后数据 | 1041 条（删除28条零曝光，2.62%） |

### 核心指标（示例）

| 指标 | 数值 |
|---|---|
| 总花费 | ¥XX,XXX |
| 总曝光 | XXX,XXX |
| 总点击 | XX,XXX |
| 总转化 | X,XXX |
| 整体CTR | X.XX% |
| 整体CVR | X.XX% |
| 整体CPC | ¥X.XX |
| 整体CPA | ¥XX.XX |

### 异常检测结果

| 异常类型 | 数量 |
|---|---|
| 点击率过低 | XX 条 |
| CPA过高 | XX 条 |
| 花费暴涨 | XX 条 |
| 转化率过低 | XX 条 |
| 零曝光 | 0 条（清洗时已删除） |

### 测试覆盖

- 数据加载模块：11个测试 ✅
- 数据清洗模块：19个测试 ✅
- 总测试数：30个 ✅

## 📝 学习记录

### 第一阶段：数据工程筑基（已完成）

- [x] **Day1**：项目骨架 + 配置管理（Settings类、.env）+ 日志模块
- [x] **Day2**：数据加载模块（CSV/API读取、pandas read_csv参数详解、数据校验）
- [x] **Day3**：数据清洗模块（6步清洗流程、缺失值/重复值/类型转换、pandas取数详解）
- [x] **Day4**：指标计算模块（CTR/CVR/CPA/ROI、groupby分组聚合、环比、移动平均）
- [x] **Day5**：数据库基础 + SQL（MySQL安装、SQL基础、JOIN、窗口函数）
- [x] **Day6**：Python连接MySQL + database.py（cursor/conn/commit/close、SQL建表DDL）
- [x] **Day7**：pandas vs SQL选择 + Python面向对象（类/继承/封装/接口/装饰器）
- [x] **Day8**：异常检测模块（5种异常检测、严重程度分级、从看懂到写出的练习方法）
- [x] **Day9**：报表生成模块（HTML/CSS基础、Jinja2模板引擎、HTML日报生成）
- [x] **Day10**：Pandas核心用法巩固 + 进阶练习 + 完整main.py流水线 + README完善

### 第二阶段：数据仓库与ETL（规划中）

- [ ] 数据仓库理论（OLTP/OLAP、星型模型、ODS/DWD/DWS/ADS分层）
- [ ] ETL开发（增量加载、全量加载、幂等性、失败重跑）
- [ ] Spark基础（DataFrame、分区、缓存、Shuffle）
- [ ] Airflow调度（DAG、Task、定时调度、重试）
- [ ] Docker容器化

### 第三阶段：大模型应用（规划中）

- [ ] FastAPI后端服务
- [ ] RAG知识库（文档解析、Embedding、向量数据库、相似度搜索）
- [ ] Agent开发（Function Calling、工具调用、多轮对话）
- [ ] 系统优化与部署

## 🧠 核心知识点

### Python工程化
- 模块与包、虚拟环境、依赖管理
- 配置管理（环境变量、Settings类）
- 日志系统（统一格式、分级输出）
- 异常处理、单元测试、代码格式化

### 数据处理（Pandas）
- 数据读取（read_csv常用参数）
- 数据清洗（缺失值、重复值、类型转换、异常值）
- 数据取数（loc/iloc、条件筛选、多条件）
- 分组聚合（groupby、agg命名聚合、多维度汇总）
- 合并（merge、concat）
- 时间序列（日期处理、shift环比、rolling移动平均）

### 数据库
- SQL基础（SELECT、WHERE、GROUP BY、HAVING、ORDER BY）
- 多表连接（INNER/LEFT/RIGHT JOIN）
- 窗口函数（ROW_NUMBER、RANK、SUM OVER、LAG/LEAD）
- Python连接数据库（cursor、conn、commit、close）
- SQLAlchemy引擎（连接池、统一接口）

### 前端与报表
- HTML基础（标签、表格、表单）
- CSS基础（选择器、常用属性、盒模型）
- Jinja2模板引擎（变量、循环、条件）
- 模板渲染流程

## 👤 作者

**Mia**
- 深圳大学 应用数学（师范）本科
- 目标：2027年港校数据科学硕士
- 方向：AI大数据开发 / 大模型应用工程
- 背景：3个月海外广告投放数据分析经验

## 📄 许可证

MIT License

---

> 本项目为学习实战项目，持续更新中。如有问题或建议，欢迎交流！
