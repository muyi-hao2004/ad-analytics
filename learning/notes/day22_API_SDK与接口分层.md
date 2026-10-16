# Day22: API / SDK / 库 与接口分层

> 面试高频基础题：把"SDK、API、库、MCP、FastAPI"这几个词的关系彻底说清楚。
> 结合本项目（IAA 广告变现中台）的实际代码理解。

---

## 一、三个核心概念

### API（Application Programming Interface）
- **接口约定**：URL + 请求格式 + 返回格式，描述"你能做什么"。
- 本身不是代码，是"服务方开的一扇门"。
- 例：`https://api.deepseek.com/v1/chat/completions`

### SDK（Software Development Kit）
- **工具包**：把 API 封装成你语言里的现成函数 + 认证 + 类型 + 文档示例。
- 核心就是"调用 API 的函数"。
- 例：`openai` 这个 Python 包（调 DeepSeek 就靠它）。

### Library（库）
- **通用工具代码**：不一定是跟某个平台对接。
- 例：`pandas`、`numpy`。

### 一句话区别
| 名词 | 是什么 | 类比 |
|---|---|---|
| API | 接口约定（能做啥） | 餐厅菜单 |
| SDK | 封装 API 的工具包 | 料理包（含食材+步骤） |
| 库 | 通用工具（本地功能） | 菜刀、锅 |

---

## 二、两种"函数"：本地算 vs 云端算

| | 普通库的函数 | SDK 里的函数 |
|---|---|---|
| 例 | `df.groupby()` | `client.chat()` |
| 执行地点 | **本地**（你电脑） | **发请求到云端**（远程服务器） |
| 结果从哪来 | 本地算出来 | 云端算完传回来 |

> **SDK 里的函数 = "本地调用、云端执行"。**
> 你看着是调了个本地函数，实际它发了个网络请求到云服务器，等云端算完把结果传回来。

### 万能判断句（看到任何新东西用它判断是不是 SDK）
> "我调这个函数，结果是在我电脑上算的，还是飞到别人服务器上算的？"
> - 本地算 → 普通库（pandas）
> - 云端算 → SDK（openai、微信支付）

## 三、调用链：你 → SDK → API → 云端能力

```
你(代码)  →  SDK(openai包)  →  API(URL)  →  LLM(DeepSeek模型)
              封装函数          网络请求       真正干活的地方
```

- 你只写 `client.chat.completions.create(...)`；
- SDK 内部自动帮你：打包 JSON → 发 HTTP POST → 带认证头 → 解析返回。
- 你一行 HTTP 都没写。

### 准确说法（写简历/面试用）
- ✅ "调用 LLM" / "通过 OpenAI SDK 调用 LLM API" / "接入 DeepSeek 大模型"
- ❌ "调用 SDK"（SDK 是工具不是目的，说"用/集成 SDK"才对）

---

## 四、为什么能力要放云端（走 API），不放在 Python 本地？

三个原因，对应三个判断标准：

1. **装不下**：DeepSeek 几百 GB 权重、跑在 GPU 集群上，本地电脑 16G/32G 扛不住。
2. **不该给**：模型权重/算法是商业机密，服务方只租不卖（按次收费）。
3. **必须集中**：支付、数据库等账本要多人共享、对账，不能各自本地一份。

### 判断"放本地还是远程"
> ① 算力/大小你本地扛得住吗？ ② 服务方愿不愿意开源给你？
> 两个都满足 → 本地库；有一个不满足 → 走 API。

### 反例：bge-m3 就在本地跑
- 本项目 `models/bge-m3` 下载到本地，用 `sentence-transformers` 本地算向量。
- 因为它 **小**（约 2.27GB）+ **开源**（BAAI 免费放出），两个条件都满足。

---

## 五、MCP vs FastAPI（本项目里两者都要，为什么？）

| | MCP Server | FastAPI |
|---|---|---|
| 服务对象 | **AI Agent / LLM** | **人 / 浏览器 / 前端** |
| 调用方式 | 模型自动发现工具、自动调 | 手动点按钮 / 前端 fetch |
| 协议 | MCP（JSON-RPC，list_tools/call_tool） | REST（GET/POST /api/xxx） |
| 类比 | 给 AI 的 USB-C 接口 | 给用户的收银台窗口 |

### 为什么不能用同一套？
- **人**需要 URL、页面、按钮、中文返回 → REST 最自然。
- **AI**需要"自动发现有哪些工具 + 每个工具的参数 schema" → MCP 的 list_tools 专门干这个。

### 关键：业务逻辑只写一遍
本项目 `mcp_server.py` 直接复用 `agent.py` 里的 `_tool_xxx` 函数：
```python
_get_metric = agent._tool_get_metric   # MCP 复用 agent 的工具函数
```
→ **业务逻辑单一来源，接入层各自封装**（MCP 开门给 AI，FastAPI 开门给人）。

---

## 六、本项目技术栈里"谁是 SDK、谁是库"

| 项目里的东西 | 归类 | 背后是否调 API |
|---|---|---|
| `openai` | SDK | ✅ 调 DeepSeek API |
| `chromadb` | SDK | ✅ 调本地向量库（本地服务） |
| `sentence-transformers` | SDK | ⚠️ 本地跑 bge-m3（不调远程） |
| `fastapi` | 框架/SDK | 本地起 Web 服务 |
| `mcp` | SDK | 本地起 MCP 服务 |
| `pandas` / `numpy` | 库 | ❌ 纯本地计算 |

> 注意：SDK 不一定都调远程 API。`sentence-transformers` 是 SDK，但 bge-m3 在本地跑。
> 判断标准永远是：这个函数背后有没有"访问远端服务"。

---

## 七、一句话总结（面试直接说）

> **API 是"能做啥"的契约，SDK 是"怎么方便地做"的封装（核心是调用 API 的函数），库是本地通用工具。**
> 能力放云端是因为"装不下 / 不该给 / 要共享"，所以通过 SDK 发请求去调远端的 API。
> 我的项目里，MCP 给 AI 当工具接口、FastAPI 给人当 Web 接口，两者复用同一套业务函数。
