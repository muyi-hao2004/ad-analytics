# 广告投放智能助手 (Ad Agent)

基于大模型 Function Calling 实现的广告投放智能助手，Agent 可以自动判断用户意图，调用对应的工具完成任务。

## 功能特性

- **数据查询**：查询各渠道的广告投放数据（花费、曝光、点击、转化、CTR、CPC、CPA）
- **异常检测**：自动检测 CPA 异常上涨、CTR 异常下降等情况
- **知识库问答**：查询指标定义、异常原因、优化建议等概念性问题
- **日报生成**：生成完整的广告投放日报，包含整体汇总和分渠道详情
- **自主决策**：大模型自动判断该调用哪个工具，支持多轮工具调用

## 技术栈

- **大模型**：DeepSeek API（Function Calling）
- **数据库**：MySQL 8.0
- **数据处理**：Pandas
- **开发语言**：Python 3.14

## 项目结构

```
ad_agent/
├── .env              # 环境变量（API Key等，不提交到Git）
├── agent.py          # Agent核心逻辑
├── tools.py          # 工具层（所有可调用的工具函数）
└── README.md         # 项目说明
```

## 工具列表

| 工具名 | 功能 | 什么时候调用 |
|--------|------|-------------|
| `query_ad_data` | 查询广告投放数据 | 问具体数字、对比数据 |
| `detect_anomaly` | 检测投放异常 | 问"是不是异常"、"有没有问题" |
| `query_knowledge_base` | 查询知识库 | 问"什么是"、"为什么"、"怎么办" |
| `get_daily_report` | 生成日报 | 要"日报"、"汇总"、"整体情况" |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

创建 `.env` 文件：

```
DEEPSEEK_API_KEY=your_api_key_here
```

### 3. 配置数据库

确保 MySQL 中有名为 `ad_analytics` 的数据库，以及 `ad_data` 表。

### 4. 运行

```bash
python agent.py
```

## 使用示例

```python
from agent import ad_agent_chat

# 问具体数据
ad_agent_chat("今天Facebook的CPA多少？")

# 问概念
ad_agent_chat("什么是CPA？")

# 异常检测
ad_agent_chat("Facebook今天投放正常吗？有没有异常？")

# 生成日报
ad_agent_chat("给我一份今天的投放日报")
```

## 工作原理

```
用户提问
    ↓
Agent（大模型）判断意图
    ↓
    ├── 查数据 → query_ad_data
    ├── 检测异常 → detect_anomaly
    ├── 查知识库 → query_knowledge_base
    └── 生成日报 → get_daily_report
    ↓
Agent整理结果，返回自然语言回答
```

## 后续优化方向

- [ ] 接入真实 RAG（ChromaDB + Embedding），替代简单关键词匹配
- [ ] 支持流式输出
- [ ] 包装成 FastAPI 接口
- [ ] 加前端聊天界面
- [ ] 加记忆功能，记住用户的历史对话
- [ ] 加多 Agent 协作
