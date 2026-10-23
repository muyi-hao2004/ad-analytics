# 后端基础：HTTP、API 与 FastAPI

本篇覆盖：库 / SDK / API 三个易混概念的辨析与调用链；能力为什么放云端；一次请求从 URL 到函数的完整路径；HTTP 报文、方法、状态码、请求头；RESTful 设计规范；FastAPI 的路由、三类参数、Pydantic 模型、响应模型、async/await 与事件循环、lifespan 生命周期；数据加载模块 data_loader 的多来源读取与清洗分层；数据库连接层 database.py 的封装、参数化查询与 ORM 取舍；最后是大模型通过 Function Calling 调用业务 API 的闭环。

主线是一条：**能力被封在远端或本地的服务里 → 用 HTTP 这门语言去敲门 → 用 RESTful 约定把门牌写清楚 → 用 FastAPI 把门造出来 → 门后接的是数据加载层和数据库层。**

## 一、库、SDK、API：三个被混用的词

### 1.1 API 是契约，不是代码

API（Application Programming Interface，应用程序编程接口）是**接口约定**：一个 URL + 请求格式 + 返回格式，描述"这个服务能做什么"。

- 它本身不是可执行代码，是服务方开的一扇门。
- 例：`https://api.deepseek.com/v1/chat/completions` 加上约定的 JSON 结构，就构成一个 API。
- 门后面怎么实现、用什么模型、几台机器，调用方完全不需要知道。

### 1.2 SDK 是封装 API 的工具包

SDK（Software Development Kit）把 API 封装成本语言里的现成函数，并附带认证、类型定义、重试、文档示例。

- 核心就是"调用 API 的函数"。
- 例：`openai` 这个 Python 包，调 DeepSeek 也靠它（DeepSeek 兼容 OpenAI 协议）。
- SDK 的存在意义：把"拼 JSON、发 POST、加认证头、解析返回、处理异常"这些脏活包起来。

### 1.3 库是本地通用工具

Library（库）是通用工具代码，不一定对接某个平台。

- 例：`pandas`、`numpy`。装好之后在本地内存里算，不产生网络请求。

### 1.4 一句话区别

| 名词 | 是什么 | 类比 |
|---|---|---|
| API | 接口约定（能做啥） | 餐厅菜单 |
| SDK | 封装 API 的工具包 | 料理包（含食材 + 步骤） |
| 库 | 通用工具（本地功能） | 菜刀、锅 |

### 1.5 两种"函数"：本地算 vs 云端算

| | 普通库的函数 | SDK 里的函数 |
|---|---|---|
| 例 | `df.groupby()` | `client.chat.completions.create()` |
| 执行地点 | 本地（自己的机器） | 发请求到云端（远程服务器） |
| 结果从哪来 | 本地算出来 | 云端算完传回来 |
| 失败模式 | 内存不够、类型错 | 超时、401、429、5xx |

**SDK 里的函数 = 本地调用、云端执行。** 看着是调了个本地函数，实际它发了个网络请求到云服务器，等云端算完把结果传回来。

万能判断句：**"我调这个函数，结果是在我电脑上算的，还是飞到别人服务器上算的？"**

- 本地算 → 普通库（pandas）
- 云端算 → SDK（openai、微信支付）

### 1.6 调用链：调用方 → SDK → API → 云端

```
调用方代码  →  SDK(openai 包)  →  API(URL)  →  LLM(DeepSeek 模型)
              封装函数           网络请求       真正干活的地方
```

调用方只写 `client.chat.completions.create(...)`；SDK 内部自动完成：打包 JSON → 发 HTTP POST → 带认证头 → 解析返回。

整个过程一行 HTTP 都没手写，但底层一定是 HTTP。

### 1.7 说法规范（写简历 / 面试用）

- 正确："调用 LLM" / "通过 OpenAI SDK 调用 LLM API" / "接入 DeepSeek 大模型"
- 错误："调用 SDK"——SDK 是工具不是目的，应说"使用 / 集成 SDK"

### 1.8 项目技术栈里谁是 SDK、谁是库

| 组件 | 归类 | 背后是否调 API |
|---|---|---|
| `openai` | SDK | 是，调 DeepSeek API |
| `chromadb` | SDK | 是，调本地向量库（本地服务） |
| `sentence-transformers` | SDK | 否，本地跑 bge-m3 |
| `fastapi` | 框架 / SDK | 本地起 Web 服务 |
| `mcp` | SDK | 本地起 MCP 服务 |
| `pandas` / `numpy` | 库 | 否，纯本地计算 |

### 1.9 边界：SDK 不一定都调远程

`sentence-transformers` 是 SDK，但 bge-m3 在本地跑。判断标准永远是：**这个函数背后有没有"访问远端服务"**，而不是它叫什么名字、装没装成包。

## 二、为什么能力放云端（走 API），不放在本地

### 2.1 三个原因

1. **装不下**：DeepSeek 几百 GB 权重、跑在 GPU 集群上，普通笔记本 16G / 32G 内存扛不住。
2. **不该给**：模型权重和算法是商业机密，服务方只租不卖，按 token 收费。
3. **必须集中**：支付、账本、主数据库这类状态需要多人共享、统一对账，不能各自本地存一份。

### 2.2 判断"放本地还是远程"的两条标准

- ① 算力 / 体积本地扛得住吗？
- ② 服务方愿不愿意开源给？

两条都满足 → 本地库；有一条不满足 → 走 API。

### 2.3 反例：bge-m3 就在本地跑

项目把 `models/bge-m3` 下载到本地，用 `sentence-transformers` 算向量。原因是它**小**（约 2.27GB）+ **开源**（BAAI 免费放出），两个条件都满足。

同样的逻辑，`pandas` 永远不会做成 API——本地算更快、数据也不用出内网。

### 2.4 反过来：本地也要开 API

远端服务对调用方是黑盒；本地服务同样需要一层"门"，否则前端、Agent、其他系统没法用统一方式访问。这就是 FastAPI 的位置：**把本地能力（查库、算指标、跑异常检测）包装成 HTTP 接口**，供三类消费者调用：

- 人 / 浏览器 / 前端页面
- 其他程序 / 脚本
- AI Agent（通过工具调用）

## 三、从 URL 到函数：一次请求的完整链路

### 3.1 服务器、部署、云服务器、互联网

- **服务器** = 一台不关机、大家能通过网络连上的公共电脑。
- **部署** = 把写好的程序搬到服务器上、启动起来，让它对外服务。
- **云服务器** = 云厂商（阿里云 / 腾讯云 / AWS）把机房里的服务器租出来，几十秒就能拿到一台远程电脑。
- **互联网** = 客户端和无数服务器之间的连接网，看到的每个网页内容都来自某台服务器。

### 3.2 开发环境 vs 生产环境

| | 开发环境 | 生产环境 |
|---|---|---|
| 是什么 | 本机写代码、调试 | 部署到服务器给用户用 |
| 出错 | 无所谓，重跑 | 事故，要留日志 |
| 要求 | 信息越详细越好 | 稳定，只记关键信息 |

程序通过配置 `APP_ENV=development/production` 知道自己在哪，从而切换行为（日志级别、是否返回堆栈、是否开启调试）。

**核心思想：配置与代码分离**——换环境只改配置，不改代码。

### 3.3 URL 的本质

URL 是定位"资源在哪、怎么访问"的地址，格式：

```
协议://主机:端口/路径?查询参数
https://api.example.com:443/v1/channels?limit=10
```

几个容易混的点：

- **URL 是"地址"的一种，不是所有地址都叫 URL**。MySQL 用的是「连接串」（主机 + 端口 + 用户 + 密码 + 库），不是网址；IP、文件路径、邮箱也都是地址，但不是 URL。
- **URL 是给人读的写法** → HTTP 协议把它翻译成报文 → 再变成 0/1 二进制传输。
- 路径区分"资源"，查询串区分"过滤条件"。

### 3.4 端口是什么

> 端口 = 操作系统内核「端口分配表」里的一个编号，用来标记"网络数据该转给哪个程序"。

- 端口不是文件、不是硬件，是**虚拟编号**，程序启动时向操作系统申请。
- **IP 定位哪台电脑，端口定位电脑上的哪个服务。**
- 常见端口：8000（FastAPI / uvicorn）、3306（MySQL）、5432（PostgreSQL）、80 / 443（网站）。
- **端口被占用**（Windows 上报 `WinError 10013`）→ 关掉旧进程，或换端口 `--port 8080`。

### 3.5 一次请求穿过的完整链路

```
客户端（浏览器 / curl / 程序）
  → 输入 URL，发出 HTTP 请求
  → 网络传输（URL 变成 HTTP 报文，再变成 0/1 二进制）
  → 操作系统按端口号，把数据转给对应程序
  → uvicorn（监听端口，解析报文，抽出「方法 + 路径」）
  → FastAPI（查路由表，匹配到对应函数）
  → 业务函数执行，return 数据
  → 序列化成 JSON，原路返回给客户端
```

能把这套链路完整讲出来，就是"懂原理"而不是"会用框架"的区别。

### 3.6 FastAPI 和 uvicorn 的分工

| 工具 | 角色 | 类比 |
|---|---|---|
| FastAPI | 定义接口、匹配路径、调用函数、组织数据 | 菜谱（写清楚做什么菜） |
| uvicorn | 监听端口、接收 / 发送网络请求 | 厨师（真正做菜、端上桌） |

- **FastAPI 自己不会跑，要交给 uvicorn 运行**：`uvicorn main:app --reload`。
- `main` 是模块名，`app` 是模块里的 FastAPI 实例，`--reload` 只在开发环境用（改代码自动重启）。
- 生产环境通常用 `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4`，或用 gunicorn 管理多个 uvicorn worker。

## 四、HTTP 协议

### 4.1 HTTP 是什么

HTTP（HyperText Transfer Protocol）是客户端和服务器之间"怎么对话"的约定。

- 一次请求（Request）+ 一次响应（Response），一问一答。
- SDK 底层发的"网络请求"，就是 HTTP 请求。

```
客户端(浏览器 / SDK)  ──HTTP 请求──▶  服务器(真正干活)
                      ◀──HTTP 响应──
```

协议规定三件事：**报文格式、请求方法、状态码含义**。

### 4.2 一个 HTTP 请求的四部分

```
POST /v1/chat/completions HTTP/1.1      ← ① 请求行（方法 + 路径 + 版本）
Host: api.deepseek.com                   ← ② 请求头（key: value）
Authorization: Bearer sk-xxxx
Content-Type: application/json
                                         ← ③ 空行（分隔，必须有）
{"model":"deepseek-chat",...}            ← ④ 请求体（要传的数据）
```

| 部分 | 是什么 |
|---|---|
| 请求行 | 干什么 + 对谁干 |
| 请求头 | 元信息（认证、格式说明、客户端信息） |
| 空行 | 头和体的分隔符，缺了服务器会把 body 当成头解析 |
| 请求体 | 真正要传的数据（GET 通常没有） |

### 4.3 响应报文

结构对称：

```
HTTP/1.1 200 OK                 ← 状态行（版本 + 状态码 + 状态文字）
Content-Type: application/json  ← 响应头
Content-Length: 348
                                ← 空行
{"id":"...","choices":[...]}    ← 响应体
```

### 4.4 HTTP 方法（动词）

| 方法 | 含义 | 类比 | 幂等 | 典型路径 |
|---|---|---|---|---|
| GET | 读取 | 只看不摸 | 是 | `/api/logs` |
| POST | 新建 / 提交 | 交新东西 | 否 | `/api/channels` |
| PUT | 整体更新 | 整个换掉 | 是 | `/api/channels/123` |
| PATCH | 局部更新 | 只改一处 | 通常不保证 | `/api/channels/123` |
| DELETE | 删除 | 删掉 | 是 | `/api/channels/123` |

**幂等**：同一个请求执行一次和执行 N 次，服务端状态结果一致。GET 读多少次结果一样；DELETE 第一次删掉，第二次返回 404，但"资源已不存在"这个状态没变，仍算幂等；POST 每调一次就新建一条，所以不幂等。

### 4.5 GET vs POST（高频）

| | GET | POST |
|---|---|---|
| 作用 | 查数据 | 提交 / 创建数据 |
| 参数位置 | URL 上（`?channel=xxx`） | 请求体（JSON） |
| 可缓存 | 是 | 否（默认） |
| 可收藏 / 可分享 | 是（参数在链接里） | 否 |
| 幂等 | 是 | 否 |
| 长度限制 | 受 URL 长度限制（浏览器 / 服务器有上限） | 基本不受限 |
| 安全性 | 参数会进日志、浏览器历史 | body 不进 URL，但**不加 HTTPS 一样能被抓包** |

最后一条要强调：**POST 不等于安全**，只是不把参数暴露在 URL 里。真正的机密性靠 HTTPS。

### 4.6 状态码

| 开头 | 含义 | 重点几个 |
|---|---|---|
| 2xx | 成功 | 200 OK；201 Created（POST 新建成功） |
| 3xx | 重定向 | 301 永久重定向；302 临时重定向 |
| 4xx | 客户端错（调用方的锅） | 400 参数错；401 未认证 / 无权限；404 资源不存在；422 校验失败；429 限流 |
| 5xx | 服务端错（服务方的锅） | 500 内部错误；503 服务不可用（过载 / 维护） |

口诀：**4xx 归调用方，5xx 归服务方，2xx 成了。**

几个容易混的：

- **400 vs 422**：400 是请求本身不合法（JSON 语法错、缺必填字段的粗粒度错误）；422 是语法没问题但语义校验不过（字段类型不对、数值越界）。FastAPI + Pydantic 校验失败默认返回 422。
- **401 vs 404**：401 是"没证明身份"；有些系统为避免泄露资源是否存在，会把无权限也返回 404。
- **429**：下载模型被镜像站限流时收到的就是 `429 Too Many Requests`，处理方式是退避重试（指数退避 + 随机抖动），不是立刻重试。

### 4.7 请求头与 Content-Type

请求头是 `key: value` 形式的元信息，常见几类：

| 头 | 作用 | 例子 |
|---|---|---|
| `Host` | 目标主机（同一 IP 可能挂多个站点） | `api.deepseek.com` |
| `Authorization` | 身份凭证 | `Bearer sk-xxxx` |
| `Content-Type` | **请求体的格式**，告诉服务器怎么解析 | `application/json` |
| `Accept` | 期望的响应格式 | `application/json` |
| `Content-Length` | 请求体字节数 | `348` |
| `User-Agent` | 客户端标识 | `python-requests/2.31` |

**Content-Type 是最容易被忽略、又最容易出错的一个**：它决定 body 按什么规则解码。

| 值 | 用途 | body 长什么样 |
|---|---|---|
| `application/json` | 现代 API 默认 | `{"name":"abc","cost":1.2}` |
| `application/x-www-form-urlencoded` | 传统表单 | `name=abc&cost=1.2` |
| `multipart/form-data` | 带文件上传 | 分段二进制 + 边界 |
| `text/plain` | 纯文本 | 任意文本 |

两个实践点：

- 用 `requests.post(url, json=payload)` 时，库会自动设 `Content-Type: application/json`；手写 `data=json.dumps(payload)` 则要自己加头，否则服务器可能按表单解析，字段全丢。
- 中文场景建议带 charset：`application/json; charset=utf-8`，避免服务端按 latin-1 解码。

### 4.8 无状态

> HTTP 是**无状态**的：服务器不记得上一次请求是谁发的。

所以每次请求都要自报家门，靠 `Token` / `Cookie` / `Session` 把身份带上。这也是为什么 API 常见 `Authorization: Bearer <token>`——服务端每次验票，不维护此前的连接状态。

无状态的好处：任何一台服务器都能处理任何一次请求，水平扩容容易；代价是每次都要带凭证、鉴权有开销。

### 4.9 序列化与 JSON

函数返回的是内存里的 Python 对象（dict / list / Pydantic 模型），网络只能传字节流，中间必须转换：

```
函数 return Python 对象
  → FastAPI 序列化成 JSON 字符串
  → uvicorn 打包成 HTTP 响应报文
  → 客户端收到，反序列化还原成自己语言的数据结构
```

**序列化 = 把内存对象变成可传输的文本；反序列化 = 反过来。**

为什么 API 普遍用 JSON 传数据：

1. **纯文本、语言无关**：Python / Java / Go / JS 都有成熟解析器。
2. **结构和 dict / list 一一对应**：嵌套、数组天然支持，不需要额外建模。
3. **自描述**：每个字段带 key，可读性远好于定长二进制。
4. **浏览器原生支持**：`JSON.parse()` / `JSON.stringify()` 内置。
5. **比 XML 轻**：没有大量闭合标签，解析更快。

JSON 的局限也要知道：

- **没有类型系统**：日期、Decimal 都退化成字符串或浮点，金额精度可能丢，需要约定格式（如金额用"分"存整数）。
- **体积比二进制大**：内部服务间高吞吐场景会用 Protobuf / Avro / Parquet，对外 API 仍用 JSON（可读、好调试）。

## 五、RESTful 设计规范

### 5.1 两条核心

1. **URL 指资源（名词）**：`/api/campaigns`、`/api/campaigns/123`
2. **HTTP 方法指动作（动词）**：GET 查 / POST 建 / PUT 改 / DELETE 删

### 5.2 增删改查的标准写法

| 动作 | 方法 + 路径 |
|---|---|
| 查所有 | `GET /api/campaigns` |
| 查单个 | `GET /api/campaigns/123` |
| 新建 | `POST /api/campaigns` |
| 整体改 | `PUT /api/campaigns/123` |
| 局部改 | `PATCH /api/campaigns/123` |
| 删 | `DELETE /api/campaigns/123` |

### 5.3 五个特征（面试常问）

1. **资源用名词复数**：`/users`、`/orders`，不是 `/getUser`。
2. **动作用 HTTP 方法表达**，不写进 URL。
3. **用 URL 层级表达关系**：`/users/123/orders` = 用户 123 的订单。
4. **无状态**：每个请求独立，靠 token 鉴权。
5. **用状态码表达结果**，而不是永远返回 200 + `{"code": 500}`。

### 5.4 判断 RESTful vs 不 RESTful

| 写法 | 判断 |
|---|---|
| `GET /users/123` | 是 |
| `GET /getUserById?id=123` | 否，动作写进了 URL |
| `POST /deleteUser` | 否，动词和动作双错 |
| `POST /api/delete_sub_channel` | 否，动作在路径里且用了 POST |

核心标准只有一句：**动作只能由 HTTP 方法表达，URL 只负责指明资源。**

### 5.5 接口三要素

```
接口 = 方法（GET/POST/PUT/DELETE） + 路径（/channels） + 处理函数
```

三个缺一不可。**同一个路径，用不同方法，做不同的事**——这就是 REST 能把接口数量压下来的原因。

### 5.6 REST 和 FastAPI 的关系（理念 vs 工具）

- **REST 是设计理念 / 规范（抽象），FastAPI 是写接口的框架 / 工具（具体）。**
- 两者不是包含关系，是"理念 + 工具"配合：按 REST 设计，用 FastAPI 实现，得到 RESTful API。
- FastAPI 不强制 REST——完全可以写 `@app.get("/getUser")`，但它的方法装饰器（`@app.get/post/put/delete`）天然鼓励 REST。
- 准确说法：**FastAPI 是"适合开发 REST API 的框架"**，不是"FastAPI 就是 REST"。

### 5.7 项目里的一个可改进点（面试加分）

删除子渠道写成了：

```
POST /api/delete_sub_channel     ← 动作写在路径里，用 POST
```

更规范的写法：

```
DELETE /api/sub_channels/{sub_channel}   ← 动作用 DELETE，资源用路径
```

这属于"风格问题"而非"错误"（很多老项目也这么写），但能主动指出并给出改法，说明真的理解 RESTful。

## 六、FastAPI

### 6.1 它是什么

Python 写 API 的 Web 框架：快、代码少、基于类型注解自动校验、自动生成交互式文档。

写一个普通 Python 函数，加个装饰器，它就变成 API。

### 6.2 路由与装饰器

```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/channels")
def list_channels():
    return [{"id": 1, "name": "Facebook"}]
```

装饰器 `@app.get("/channels")` 的作用是**登记**：告诉 FastAPI"下面这个函数是接口，方法是 GET，路径是 /channels，请监听它"。

- 没有装饰器，函数只是普通函数，不会被当作接口。
- 装饰器是 Python 通用语法，本质是"给函数附加能力 / 标签而不改函数本身"。

### 6.3 三类参数：路径参数、查询参数、请求体

| 参数类型 | 位置 | 例子 | 是否必填 |
|---|---|---|---|
| 路径参数 | 路径里，`{花括号}` | `/channels/{channel_id}` | 必填 |
| 查询参数 | `?` 后面，`键=值` | `/channels?limit=10&channel=Facebook` | 默认可选 |
| 请求体 | Body 里（不在网址上） | POST 的 JSON | 看模型定义 |

```python
@app.get("/channels/{channel_id}")          # 路径参数
def get_channel(channel_id: int): ...

@app.get("/channels")                        # 查询参数
def list_channels(limit: int = 10, channel: str | None = None): ...

@app.post("/channels")                       # 请求体
def create_channel(channel: Channel): ...
```

要点：

- 路径参数**必须传**，且类型由注解决定（`channel_id: int` 会自动转换，传 `abc` 返回 422）。
- 查询参数有默认值就可选，没默认值就必填。
- 同名参数同时出现在路径和函数签名里，FastAPI 自动按位置绑定，不需要额外声明。
- 查询参数适合**过滤、分页、排序**；路径参数适合**定位唯一资源**；请求体适合**结构复杂、字段多、含敏感信息**的提交。

### 6.4 请求体与 Pydantic 模型

```python
from pydantic import BaseModel, Field

class Channel(BaseModel):
    name: str
    cost: float = Field(ge=0)        # 必须 >= 0
    clicks: int = Field(ge=0)
    cpa: float | None = None
```

`Channel` 继承自 `BaseModel`，自动获得：

- **解析**：把 JSON 转成 Python 对象。
- **校验**：类型不对、缺字段、越界 → 自动返回 422，并指出是哪个字段。
- **文档**：字段名、类型、约束自动进 `/docs`。
- **序列化**：`.model_dump()` 转回 dict（Pydantic v1 是 `.dict()`）。

传错类型、少字段，BaseModel 直接拒绝，业务函数根本不会被执行——这是"在边界处校验"的典型做法。

### 6.5 响应模型

```python
class ChannelOut(BaseModel):
    id: int
    name: str
    cost: float

@app.get("/channels/{channel_id}", response_model=ChannelOut)
def get_channel(channel_id: int): ...
```

`response_model` 干三件事：

1. **过滤字段**：数据库里有 `password`、`internal_note`，模型里不声明就不会返回，防止敏感字段泄露。
2. **统一输出格式**：不管内部返回 ORM 对象还是 dict，都按模型序列化。
3. **生成文档**：`/docs` 里的响应示例由模型推导。

配套参数：

- `response_model_exclude_none=True`：值为 None 的字段不输出。
- `response_model_exclude={"internal_note"}`：临时排除字段。
- 返回类型注解（`-> ChannelOut`）也能起到类似作用，但 `response_model` 的过滤语义更明确。

### 6.6 错误处理：raise HTTPException

```python
from fastapi import HTTPException

raise HTTPException(status_code=404, detail="渠道不存在")
```

- `raise` = 主动抛错、立即停止函数。
- 用于"数据查不到"时返回 404，而不是返回空结果让调用方猜。
- FastAPI 捕获后自动转成标准 JSON：`{"detail": "渠道不存在"}`。
- 常见状态码用法：200 成功、201 创建成功、400 参数错、401 未认证、404 找不到、422 校验失败、500 服务器错。

### 6.7 完整可运行的增删改查

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class Channel(BaseModel):
    name: str
    cost: float
    clicks: int
    cpa: float

channels = []          # 内存列表假装数据库
next_id = 1

@app.get("/channels")                       # 查全部
def list_channels():
    return channels

@app.get("/channels/{channel_id}")          # 查单个
def get_channel(channel_id: int):
    for ch in channels:
        if ch["id"] == channel_id:
            return ch
    raise HTTPException(status_code=404, detail="渠道不存在")

@app.post("/channels", status_code=201)     # 新增
def create_channel(channel: Channel):
    global next_id
    new_ch = channel.model_dump()
    new_ch["id"] = next_id
    next_id += 1
    channels.append(new_ch)
    return new_ch

@app.put("/channels/{channel_id}")          # 修改
def update_channel(channel_id: int, channel: Channel):
    for ch in channels:
        if ch["id"] == channel_id:
            ch.update(channel.model_dump())
            return ch
    raise HTTPException(status_code=404, detail="渠道不存在")

@app.delete("/channels/{channel_id}")       # 删除
def delete_channel(channel_id: int):
    for i, ch in enumerate(channels):
        if ch["id"] == channel_id:
            removed = channels.pop(i)
            return {"message": "已删除", "removed": removed}
    raise HTTPException(status_code=404, detail="渠道不存在")
```

启动：`uvicorn main:app --reload`，访问 `http://127.0.0.1:8000/docs` 直接点按钮测试。

### 6.8 自动文档 /docs

- FastAPI 启动时扫描所有路由和 Pydantic 模型，生成 OpenAPI 描述，再渲染成 Swagger UI。
- `/docs` 是交互式文档：能看到所有接口、参数、响应示例，还能在线发请求。
- `/openapi.json` 是机器可读的接口描述，可用来生成客户端 SDK。
- 改了代码文档自动更新，不用手写维护——这是 FastAPI 相对 Flask 的最大体验优势之一。

### 6.9 async/await 与 asyncio 事件循环

> 本节为补写内容：源笔记中只有零散提及，没有系统记录。

#### 6.9.1 要解决的问题

一个 Web 服务大部分时间不在算，而在等：等数据库返回、等第三方 API、等磁盘 IO。同步模型下，一个请求占一个线程，线程在 `recv()` 上阻塞，什么也干不了。并发 1000 个请求就要 1000 个线程，线程切换和内存开销很快撑不住。

异步模型的目标：**用一个线程，在等待期间切换去处理别的请求。**

#### 6.9.2 三个概念

| 概念 | 含义 |
|---|---|
| 协程（coroutine） | 用 `async def` 定义的函数，调用它不会立即执行，而是返回一个协程对象 |
| `await` | 挂起当前协程，把控制权交回事件循环，等被等待的操作完成后再恢复 |
| 事件循环（event loop） | 一个单线程的调度器，维护"就绪任务队列"和"IO 就绪事件"，不断取出可运行的任务执行 |

关键认知：

- **`await` 不是"阻塞等待"，是"让出 CPU"**。挂起期间，事件循环去跑别的协程。
- **协程是并发，不是并行**。同一时刻只有一个协程在跑，只是切换发生在等待点，所以适合 IO 密集，不适合 CPU 密集。
- **一旦协程里出现同步阻塞调用（`requests.get`、`time.sleep`、同步 MySQL 驱动），整个事件循环会被卡住**，所有并发请求一起变慢。这是异步框架里最常见、也最致命的问题。

#### 6.9.3 最小示例

```python
import asyncio, time

async def fetch(name, sec):
    print(f"start {name}")
    await asyncio.sleep(sec)     # 让出控制权，别的协程可以跑
    print(f"done {name}")

async def main():
    t = time.time()
    await asyncio.gather(         # 并发发起三个
        fetch("a", 1), fetch("b", 1), fetch("c", 1)
    )
    print(f"elapsed={time.time()-t:.2f}s")   # 约 1s，而不是 3s

asyncio.run(main())
```

`asyncio.gather` 把多个协程注册进事件循环并发执行；总耗时约等于最慢的那个，而不是求和。

#### 6.9.4 FastAPI 里 def 和 async def 的区别（重点）

| 写法 | 谁执行 | 适用 |
|---|---|---|
| `def` 路由 | FastAPI 丢进**线程池**（anyio，默认约 40 个线程）执行 | 内部全是同步库（pymysql、requests、pandas） |
| `async def` 路由 | 直接在**事件循环**里执行 | 内部全是异步库（httpx.AsyncClient、aiomysql、asyncpg） |

规则很简单：

- **`async def` 里只能 `await` 异步函数**，不能放同步阻塞调用。
- 如果 `async def` 里调了 `requests.get()`，这个请求会把整个事件循环堵住，吞吐量比同步 `def` 还差。
- 拿不准时写 `def`，让 FastAPI 用线程池兜底，反而安全。

错误写法与正确写法：

```python
# 错误：async def 里用同步阻塞库
@app.get("/slow")
async def slow():
    r = requests.get("https://api.example.com/data", timeout=10)  # 卡死事件循环
    return r.json()

# 写法一：改成同步 def，交给线程池
@app.get("/slow")
def slow():
    r = requests.get("https://api.example.com/data", timeout=10)
    return r.json()

# 写法二：保持 async，改用异步客户端
import httpx

@app.get("/slow")
async def slow():
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://api.example.com/data")
    return r.json()

# 写法三：确实只有同步库时，显式丢进线程池
from starlette.concurrency import run_in_threadpool

@app.get("/slow")
async def slow():
    return await run_in_threadpool(requests.get, "https://api.example.com/data")
```

#### 6.9.5 什么时候不该用异步

- **CPU 密集**：图像处理、大矩阵运算、复杂 pandas 计算。协程无法并行利用多核，应该用多进程（`ProcessPoolExecutor`）或任务队列（Celery / RQ / Arq）。
- **依赖库全是同步的**：硬套 `async def` 只会把事件循环堵死，不如老老实实写 `def`。
- **流量很小**：异步带来的复杂度（全链路异步、调试困难、异常栈变长）不值得。

一句话总结：**异步解决的是"等"的问题，不是"算"的问题。**

### 6.10 lifespan 生命周期（启动 / 关闭钩子）

> 本节为补写内容：源笔记中未记录。

#### 6.10.1 为什么需要

有些资源必须"整个应用只初始化一次"，而不是每个请求一次：

- 数据库连接池
- Redis 客户端
- 加载 embedding 模型 / 分词器到显存
- 预热配置、校验环境变量
- 启动后台任务（定时刷新缓存）

这些放在路由函数里会每请求重建，放在模块顶层又无法优雅关闭。lifespan 就是官方给的钩子。

#### 6.10.2 写法

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- 启动阶段：yield 之前 ----
    app.state.pool = await create_pool()          # 建连接池
    app.state.model = load_model("bge-m3")        # 加载模型
    print("startup done")

    yield                                          # 应用在此运行

    # ---- 关闭阶段：yield 之后 ----
    await app.state.pool.close()
    print("shutdown done")

app = FastAPI(lifespan=lifespan)
```

使用：

```python
@app.get("/search")
async def search(q: str):
    vec = app.state.model.encode(q)
    async with app.state.pool.acquire() as conn:
        ...
```

要点：

- `yield` 之前的代码在**应用启动时执行一次**；`yield` 之后的代码在**应用关闭时执行一次**。
- 必须用 `@asynccontextmanager` 装饰，`yield` 只能出现一次。
- 资源挂在 `app.state` 上，全局可访问，避免用模块级全局变量（测试时多实例会互相污染）。
- **不要在 lifespan 里做长耗时阻塞操作**，否则服务迟迟不就绪，健康检查会失败；重活应改成后台任务或懒加载。
- 旧写法 `@app.on_event("startup")` / `@app.on_event("shutdown")` 已废弃，新项目统一用 lifespan。
- 用 `TestClient(app)` 做测试时，进入上下文管理器（`with TestClient(app) as c:`）才会触发 lifespan。

#### 6.10.3 和 Depends 的区别

| | lifespan | `Depends(get_db)` |
|---|---|---|
| 作用范围 | 应用级，进程启动到关闭 | 请求级，每次请求创建、用完销毁 |
| 典型对象 | 连接池、模型、全局缓存 | 单个 session / 单次事务 |
| 执行次数 | 一次 | 每请求一次 |

**连接池放 lifespan，会话放 Depends**——池是长期资产，会话是临时借用。

### 6.11 工程化项目结构

```
project/
├── app/
│   ├── main.py          # 入口，注册路由与 lifespan
│   ├── config.py        # 配置（读 .env）
│   ├── models/          # 表结构（ORM）
│   ├── schemas/         # 接口输入输出（Pydantic）
│   ├── routers/         # 接口层：解析参数、返回响应
│   ├── services/        # 业务逻辑层：算指标、编排
│   └── repositories/    # 数据访问层：只写 SQL / ORM 查询
├── tests/
├── requirements.txt
├── .env                 # 密钥，不提交 git
├── .gitignore
└── README.md
```

分层思想：**路由层（前台）→ 业务层（厨师）→ 数据层（仓库）→ 数据库，每层只干一件事。**

- 路由层不写 SQL，只做参数校验和响应组装。
- 业务层不关心 HTTP，可被脚本、定时任务、Agent 复用。
- 数据层不关心业务，只负责取数。

真实项目 vs 练习项目的四个差异：

1. 路径带版本号：`/api/v1/channels`，方便不兼容升级。
2. 接口分文件分层，不塞进一个 `main.py`。
3. 加鉴权（`Depends(get_current_user)`）和依赖注入。
4. 数据存 MySQL，不是内存列表。

### 6.12 手写时最容易犯的错

1. **忘记 import**：用了 `BaseModel`、`HTTPException` 却没导入 → `NameError`。
2. **变量名写错**：`next_id` 写成 `new_id`。
3. **PUT 只查不改**：漏了 `ch.update(...)` 那一行，返回 200 但数据没变。
4. **大小写不一致**：`Cost` vs `cost`，程序当成两个不同的键。
5. **端口被占**：忘了关旧服务器，报 `WinError 10013`。
6. **`async def` 里放同步阻塞调用**：接口单看能跑，一压测全站变慢。

## 七、数据加载模块 data_loader

### 7.1 它在项目里的位置

```
原始数据（CSV 文件 / 广告平台 API）
        ↓
【data_loader.py】← 数据入口（第一站）
        ↓  加载 + 验证
DataFrame（规整的表）
        ↓
后面的模块（清洗、分析、报表、异常检测……）
```

一句话：**它是数据的入口，把外面格式各异的原始数据，变成项目里能用的、格式统一的数据。**

### 7.2 四个函数（整体骨架）

| 动作 | 函数 | 输入 → 输出 |
|---|---|---|
| 加载 | `load_from_csv()` | CSV 路径 → DataFrame |
| 加载 | `load_from_api()` | 日期范围 → DataFrame |
| 验证 | `validate_data()` | DataFrame → 验证报告 dict |
| 理解 | `get_data_summary()` | DataFrame → 统计摘要 dict |

### 7.3 数据长什么样

一份 1069 行 × 9 列的广告投放数据（整月粒度）。

```
date, channel, campaign, region, device    ← 维度（描述"钱花在哪、什么时候、什么方式"）
impressions, clicks, conversions, cost     ← 指标（描述"效果如何、花了多少"）
```

- **维度（dimension）**：用来分组、筛选（按渠道分、按地区筛）。
- **指标（metric）**：用来计算、加总（曝光、点击、转化、花费）。

**数据分析的通用结构：按维度分组，对指标做计算。**

| 列名 | 含义 |
|---|---|
| `date` | 日期 |
| `channel` | 渠道（YouTube / Google / TikTok / Instagram / Facebook） |
| `campaign` | 广告活动（双 11 预热、618 大促等） |
| `region` | 地区（华中 / 华南等） |
| `device` | 设备（Mobile / Desktop / Tablet） |
| `impressions` | 曝光量（被看到多少次） |
| `clicks` | 点击量 |
| `conversions` | 转化量（下单 / 注册） |
| `cost` | 消耗（花了多少钱） |

### 7.4 多来源读取

#### 7.4.1 CSV

```python
df = pd.read_csv(file_path, parse_dates=['date'], encoding='utf-8')
```

- `parse_dates=['date']`：读入时就把 date 解析成**日期类型**，否则后面无法正确排序、比较、按区间过滤。
- `encoding`：中文 Windows 导出的 CSV 常是 `gbk`，读错会乱码或直接抛 `UnicodeDecodeError`。
- 大文件要配 `usecols`（只读需要的列）、`dtype`（显式指定类型，避免前几行推断错）、`chunksize`（分块流式处理）。

#### 7.4.2 Parquet

```python
df = pd.read_parquet(path, columns=['date', 'channel', 'cost'])
```

Parquet 是列式存储，适合分析场景：

- **列裁剪**：只读需要的列，IO 大幅下降。
- **压缩率高**：通常比 CSV 小几倍。
- **保留 schema**：类型、可空性都存下来，不用再猜。
- **谓词下推**：配合分区目录（`dt=<日期>/`）只扫相关分区。

CSV 适合交换和人工查看，Parquet 适合在数据管道中间落盘。

#### 7.4.3 API

```python
headers = {}
if api_token:
    headers['Authorization'] = f'Bearer {api_token}'

try:
    response = requests.get(api_url, params={...}, headers=headers, timeout=30)
    response.raise_for_status()      # 状态码不是 2xx 就抛异常
    data = response.json()
except requests.exceptions.Timeout:
    ...
except requests.exceptions.ConnectionError:
    ...
except requests.exceptions.HTTPError:
    ...
except ValueError:
    ...
```

四个必处理的异常：

| 异常 | 何时发生 |
|---|---|
| `Timeout` | 超过 30 秒没响应 |
| `ConnectionError` | 断网 / 地址错 / DNS 失败 |
| `HTTPError` | 401 没权限 / 404 找不到 / 429 限流 / 5xx |
| `ValueError` | 返回的不是合法 JSON（常见于网关返回 HTML 错误页） |

几个细节：

- **`timeout=30` 必须设**。不设的话请求可能永久挂起，把整个任务拖死。
- **`raise_for_status()` 必须调**。`requests` 默认对 4xx / 5xx 不抛异常，不检查就会把错误页当数据解析。
- **`Bearer {token}`**：`Authorization` 是 HTTP 标准请求头，专门放身份凭证；`Bearer` 是认证方案（持票人令牌），token 是凭证本身。
- 每种错误记日志后 `raise`，原样抛给上层——不要在数据层吞掉异常，否则上层以为加载成功。

兼容不同返回格式：

```python
if isinstance(data, dict) and 'data' in data:   # {"data": [...]}
    records = data['data']
elif isinstance(data, list):                     # 直接 [...]
    records = data
else:
    raise ValueError(f"未知返回格式: {type(data)}")
df = pd.DataFrame(records)
```

**CSV 与 API 的对比**：获取方式不同，但拿到之后做的事一样（转类型、验证、返回），所以共用同一个 `validate_data()`。

### 7.5 清洗步骤

加载只负责"读进来"，清洗负责"变得可用"。典型顺序：

```
结构对齐 → 标准化 → 类型转换 → 去重 → 缺失处理 → 异常处理 → 业务过滤
```

#### 7.5.1 结构对齐

- 检查必需列是否存在，缺列直接报错，不要静默补空列。
- 多余列可丢弃，避免下游误用。
- 多平台 CSV 列名不同时，维护一张字段映射表（`spend → cost`、`impressions → 曝光量`），而不是给每个平台写一套代码。

#### 7.5.2 标准化

- **列名**：统一小写 + 下划线（`Total Cost` → `total_cost`），去掉首尾空格。
- **枚举值**：`Facebook` / `facebook` / `FB` / `facebook ` 统一成一个值。
- **单位与币种**：金额统一到分或元，汇率换算要记录汇率日期。
- **时区**：统一到同一时区（如 UTC+8），跨时区数据必须带时区信息。
- **日期格式**：`2024/8/1`、`2024-08-01`、`01/08/2024` 统一解析，注意日月顺序歧义。

#### 7.5.3 类型转换

```python
df['date'] = pd.to_datetime(df['date'], errors='coerce')
df['cost'] = pd.to_numeric(df['cost'], errors='coerce')
df['channel'] = df['channel'].astype('category')
```

- `errors='coerce'`：转不动的置为 NaN，而不是整批报错；之后再用缺失值检查兜住。
- 金额避免用 float 累加（精度误差），必要时用 `Decimal` 或整数分。
- 类别型转 `category` 能省内存、加速 groupby。

#### 7.5.4 去重

```python
df = df.drop_duplicates(subset=['date', 'channel', 'campaign', 'region', 'device'], keep='last')
```

- 先去重再算指标，否则曝光和花费会被重复累加。
- `subset` 用业务主键，不要用整行——整行去重会漏掉"主键相同但指标被修正"的记录。
- `keep='last'` 表示保留最新一条（配合按更新时间排序），适合增量同步场景。

#### 7.5.5 缺失处理

先判断缺失机制，再决定策略：

| 情况 | 处理 |
|---|---|
| 整行关键字段缺失（date / channel） | 删除该行，无法归属 |
| 指标缺失且量小 | 删除或按同维度中位数填补 |
| 指标缺失但有业务含义（无花费） | 填 0 |
| 类别缺失 | 填 `unknown`，保留"未知"这一类的信息 |

不要无脑 `fillna(0)`：把"没采集到"当成"值为 0"，会系统性低估指标。

#### 7.5.6 异常处理

广告数据的典型异常：

- 负的曝光 / 点击 / 花费。
- `clicks > impressions`（点击率 > 100%）。
- `conversions > clicks`（转化率 > 100%）。
- `impressions == 0` 但 `cost > 0`（没曝光却花钱）。
- 日期超出预期范围（时区错位导致跑到未来或去年）。
- 单日花费是历史均值的几十倍（漏了小数点、币种错）。

处理方式三选一：**截断**（置为边界值）、**置空**（当缺失处理）、**隔离**（写到异常表，人工核查）。不要直接删——删掉就失去了排查线索。

#### 7.5.7 业务过滤

- 日期范围：只保留目标统计周期。
- 排除测试渠道、内部流量、爬虫流量。
- 排除金额为 0 的占位记录。

#### 7.5.8 两条原则

- **清洗必须可复现**：同一份原始数据，跑同样的代码得到同样的结果；随机性操作要固定种子。
- **原始层不动**：清洗结果写到新表 / 新目录，保留 raw 以便回溯和重跑。

### 7.6 分层加载思路

对应数仓分层，加载也可以分三层：

| 层 | 内容 | 特点 |
|---|---|---|
| 原始层（ODS） | 原样落盘的 CSV / API 响应 | 不改一行，可追溯 |
| 标准层（DWD） | 清洗、去重、类型统一后的明细 | 一份口径，多个下游共用 |
| 应用层（ADS） | 按维度预聚合的指标 | 查询快，直接给报表 / API |

工程上的三个做法：

- **只加载需要的列和分区**（列裁剪 + 分区裁剪），不要每次全表扫。
- **增量优先**：按日期分区，只处理新增分区；重跑时按分区覆盖写，保证幂等。
- **加载与计算分离**：加载层不写业务逻辑，业务逻辑放上层，换数据源不影响指标定义。

### 7.7 get_data_summary：把指标口径固化

广告漏斗：**花钱 → 曝光 → 点击 → 转化**

| 指标 | 公式 | 含义 |
|---|---|---|
| CTR | 点击 / 曝光 | 点击率 |
| CVR | 转化 / 点击 | 转化率 |
| CPC | 消耗 / 点击 | 每次点击成本 |

```python
summary['total_cost'] = round(float(df['cost'].sum()), 2)      # 金额 2 位

if summary['total_impressions'] > 0:                            # 除零保护
    summary['overall_ctr'] = round(
        summary['total_clicks'] / summary['total_impressions'], 6
    )
```

两个关键点：

- **`round(x, n)` 保留 n 位**：金额 2 位、比率 6 位、CPC 4 位。统一位数避免"同一个指标两个页面显示不一样"。
- **除零保护**：算比率 / 平均值前先判断分母 > 0，否则抛 `ZeroDivisionError`，或者得到 `inf` / `NaN` 污染下游。

这个函数真正的价值是**把业务指标的口径用代码固化**——否则每个人对"CTR 是什么"理解不同（是按天平均还是总量相除），数据就对不上。

### 7.8 类型注解

```python
from typing import Optional

def load_from_csv(file_path: Optional[str] = None) -> pd.DataFrame:
    ...
```

- `参数: 类型` 是参数注解，`-> 类型` 是返回注解。
- **注解只是提示，不强制**：写 `-> pd.DataFrame` 却返回字符串，Python 运行时不报错。
- 作用：代码自解释、编辑器智能提示、可配合 mypy 做静态检查。
- `Optional[str]` = `str | None`，表示可以不传。

### 7.9 这个模块的可改进点

| 维度 | 现在 | 真实工程 |
|---|---|---|
| 数据源 | 写死 CSV / API 两个函数 | 抽象成接口，可扩展 DB / Kafka / S3 |
| 列名 | 假设一种标准格式 | 字段映射，适配各平台不同 CSV |
| 类型转换 | 硬编码 `astype` | 容错转换（`pd.to_numeric(errors='coerce')`） |
| 验证失败 | 只记录不中断 | 可配置是否中断、是否告警 |

核心洞察：**数据源是多样的、格式是不统一的**——这正是数据工程师每天面对的核心问题。

## 八、数据库连接层 database.py

### 8.1 为什么要有这一层

完整数据流：

```
原始数据（CSV / API）→ Python 读取 + 清洗 → 写入 MySQL → 需要时 SQL 查询 → pandas 分析 / 报表
```

数据库是数据的"家"：清洗好的数据存进去，需要时再查，不是每次都重新读 CSV。

把数据写进数据库的四个理由：

1. **持久化**：长期保存，新数据追加，历史不丢。
2. **查询快**：有索引优化，几百万条也能秒查（pandas 读大文件会卡）。
3. **多人共享**：多个系统 / 同事同时连库查询，不用传来传去。
4. **复杂查询**：多表 JOIN、窗口函数、子查询，SQL 比 pandas 更直观也更快。

封装成 `database.py` 而不是到处写连接代码，理由有六条：

1. **连接参数集中**：host / port / user / password 从 `config` 和 `.env` 读，不散落在业务代码里。
2. **连接生命周期统一**：`try...finally` 保证关闭，避免连接泄露。
3. **SQL 集中管理**：改表名、加索引只改一处。
4. **换库 / 换驱动只改一层**：pymysql 换 asyncpg，上层无感。
5. **统一异常与日志**：连接失败、SQL 报错有统一处理。
6. **上层不关心数据库细节**：API 层、脚本层只调函数。

### 8.2 get_connection：连接数据库

```python
import pymysql
from .config import settings

def get_connection():
    conn = pymysql.connect(
        host=settings.DB_HOST,          # localhost
        port=settings.DB_PORT,          # 3306
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,      # ad_analytics
        charset='utf8mb4',              # 支持中文与 emoji
        cursorclass=pymysql.cursors.DictCursor,   # 结果返回 dict
    )
    return conn
```

- 参数和 DataGrip 里填的连接信息一一对应。
- **`charset='utf8mb4'`**：MySQL 的 `utf8` 是三字节的残废实现，存 emoji 会报错，必须用 `utf8mb4`。
- **`DictCursor`**：查询结果从 tuple 变成 dict，可以直接 `row['cost']`，也能直接喂给 `pd.DataFrame`。
- **用完必须关闭**：`cursor.close()` + `conn.close()`，放在 `finally` 里。

### 8.3 create_database：创建库

- 创建数据库时**不能指定 `database` 参数**——库还不存在，连上去会直接报错。
- SQL：`CREATE DATABASE IF NOT EXISTS 库名 DEFAULT CHARACTER SET utf8mb4`
- `IF NOT EXISTS`：已存在就跳过，不报错，脚本可重复执行。
- 修改类操作要 `conn.commit()` 提交。
- 用 `try...finally` 保证连接一定关闭。

### 8.4 create_tables：建表

```sql
CREATE TABLE IF NOT EXISTS ad_data (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    date DATE NOT NULL,
    channel VARCHAR(50) NOT NULL,
    campaign VARCHAR(100),
    impressions BIGINT DEFAULT 0,
    clicks BIGINT DEFAULT 0,
    conversions BIGINT DEFAULT 0,
    cost DECIMAL(12, 2) DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_date (date),
    INDEX idx_channel (channel)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

列定义格式：`列名 类型 约束 COMMENT '注释'`。

**MySQL 常用数据类型：**

| 类型 | 说明 | 例子 |
|---|---|---|
| `INT` | 整数 | id、clicks |
| `BIGINT` | 大整数 | 超大数量、曝光 |
| `FLOAT` / `DOUBLE` | 浮点数 | ctr |
| `DECIMAL(p, s)` | 定点小数 | 金额（避免浮点误差） |
| `VARCHAR(n)` | 变长字符串 | name、channel |
| `TEXT` | 长文本 | 备注 |
| `DATE` | 日期 | date |
| `DATETIME` | 日期时间 | created_at |
| `BOOLEAN` | 布尔值 | is_active |

**常用约束：**

| 约束 | 说明 |
|---|---|
| `PRIMARY KEY` | 主键，唯一标识一行 |
| `AUTO_INCREMENT` | 自增，插入时自动 +1 |
| `NOT NULL` | 不允许为空 |
| `DEFAULT 值` | 默认值 |
| `UNIQUE` | 值唯一 |

**索引**：`INDEX 索引名 (列名)`。经常出现在 `WHERE`、`JOIN`、`ORDER BY` 里的列要建索引（date、channel、campaign_id）。

索引不是越多越好：每个索引都占空间、拖慢写入。区分度低的列（如性别）建索引几乎没用。

### 8.5 insert_ad_data：DataFrame 写入

```python
df.to_sql(
    name='ad_data',
    con=engine,             # SQLAlchemy 引擎
    if_exists='append',
    index=False,
    chunksize=1000,
)
```

- **`con` 必须是 SQLAlchemy 引擎**（`create_engine(...)`），不能直接传 pymysql 连接——pandas 内部依赖 SQLAlchemy 的方言层。
- `index=False`：不把 DataFrame 的行号写进表，否则多一列无意义的 `index`。
- `chunksize=1000`：每批 1000 行，避免一次性构造巨大 SQL 导致内存爆掉。

`if_exists` 三个选项：

| 值 | 行为 | 风险 |
|---|---|---|
| `'fail'` | 表存在就报错 | 安全，但需要自己建表 |
| `'replace'` | 删表重建 | **危险**，原有数据和索引全没 |
| `'append'` | 追加 | 最常用，但重复跑会产生重复数据 |

`append` 的重复问题要靠**幂等**解决：按日期先删后插，或建唯一键 + `ON DUPLICATE KEY UPDATE`。

### 8.6 query_to_df：查询返回 DataFrame

```python
df = pd.read_sql("SELECT * FROM ad_data WHERE cost > 1000", con=engine)
```

- 在 DataGrip 里写好的 SQL 直接复制过来就能用。
- 结果直接是 DataFrame，可以继续用 pandas 分析。
- 大结果集要加 `LIMIT` 或分页，避免一次性拉进内存。

### 8.7 参数化查询防注入

这是数据库层最重要的安全点。

```python
# 错误：字符串拼接
channel = "Facebook' OR '1'='1"
sql = f"SELECT * FROM ad_data WHERE channel = '{channel}'"
cursor.execute(sql)
# 实际执行：WHERE channel = 'Facebook' OR '1'='1'  → 全表泄露
```

```python
# 正确：占位符 + 参数元组
sql = "SELECT * FROM ad_data WHERE channel = %s AND date >= %s"
cursor.execute(sql, (channel, start_date))
```

原理：**驱动先把 SQL 模板发给数据库编译，再把参数作为"纯数据"传入**。参数里即使含有引号、分号、`--`，也只会被当成值，不会变成语法。

三条纪律：

1. **所有外部输入都走占位符**：请求参数、CSV 里的值、API 返回值，一律不拼字符串。
2. **表名 / 列名不能参数化**（占位符只作用于值），必须用白名单校验：
   ```python
   ALLOWED = {"date", "channel", "cost"}
   if sort_by not in ALLOWED:
       raise ValueError("非法排序字段")
   sql = f"SELECT * FROM ad_data ORDER BY {sort_by}"
   ```
3. **ORM 的 `filter()` 内部也是参数化的**，但 `text()` 里手拼字符串同样会注入。

### 8.8 为什么用函数 + SQL，不用 ORM

这是两条都能走通的路，本项目选了前者。

**选函数 + SQL 的理由：**

1. **分析型 SQL 复杂**：窗口函数、多表 JOIN、CTE，直接写 SQL 更直观，ORM 表达起来别扭甚至写不出。
2. **性能可控**：能看执行计划、能加 hint、能手工改写；ORM 生成的 SQL 有时出人意料（N+1 查询、多余 JOIN）。
3. **依赖更少**：只要 `pymysql` + `pandas`，不用维护模型映射和迁移脚本。
4. **团队门槛低**：会 SQL 的人都能改，不需要先学一套 ORM API。
5. **与 pandas 天然配合**：`read_sql` / `to_sql` 直接进出 DataFrame。

**ORM 的优势也要承认：**

- 换数据库几乎不改代码（方言由 ORM 抹平）。
- 对象化操作，配合 Pydantic 体验统一。
- 默认参数化，安全。
- 关系维护（一对多、多对多）和迁移工具成熟。

**结论**：交易型 CRUD、多人协作的业务系统适合 ORM；分析型取数、复杂 SQL、追求性能的数据管道适合裸 SQL。不是谁对谁错，看场景。

### 8.9 ORM 路线（SQLAlchemy）

源笔记里也实践过 ORM，作为对照保留。

**为什么需要 SQLAlchemy**：Python（对象）和 MySQL（表）语言不通，ORM（Object-Relational Mapping，对象关系映射）是翻译官——把 Python 类映射到数据库表，`db.query(Channel)` 翻译成 `SELECT ... FROM channels`，再把结果还原成对象。

换数据库只需改连接串：

```python
DATABASE_URL = "mysql+pymysql://root:密码@127.0.0.1:3306/ad_demo"
```

裸写 SQL 时，不同数据库分页语法不同（`LIMIT 1` / `TOP 1` / `ROWNUM=1`），换库要改一堆；ORM 抹平了差异。

**四个文件的分工：**

| 文件 | 作用 |
|---|---|
| `database.py` | 连接配置（engine + SessionLocal + Base） |
| `models.py` | 表结构映射（类 ↔ 表） |
| `schemas.py` | 接口输入输出格式（Pydantic） |
| `main.py` | 接口 + 增删改查逻辑 |

**三个对象（必背）：**

| 对象 | 一句话 | 建几次 |
|---|---|---|
| `engine` | 连接池 / 总机 | 一次 |
| `SessionLocal` | 发会话的工厂 | 一次 |
| `db`（session） | 每次的通话线 | 每请求一个，用完关 |

类比：**engine 是总水管（常驻），db 是每次接水的杯子（用完收走）。**

**五个核心套路：**

```python
# 查全部
db.query(models.Channel).all()

# 查单个
db.query(models.Channel).filter(models.Channel.id == id).first()

# 新增（必须 commit）
new_ch = models.Channel(**channel.model_dump())
db.add(new_ch); db.commit(); db.refresh(new_ch)

# 修改（必须 commit）
ch.cost = 100.0; db.commit()

# 删除（必须 commit）
db.delete(ch); db.commit()
```

**铁律：增、删、改都要 `commit()`，查不用。不 commit，改动不会真正写进数据库。**

**Depends 和 yield：**

```python
def get_db():
    db = SessionLocal()
    try:
        yield db          # 把 db 借出去用，用完继续往下走
    finally:
        db.close()

@app.get("/channels")
def list_channels(db: Session = Depends(get_db)):
    return db.query(models.Channel).all()
```

- `Depends(get_db)` 声明"我需要 db"，FastAPI 自动创建和回收。
- `yield` vs `return`：`return` 函数立即结束；`yield` 暂停返回后还能继续，所以能"用完再 close"。

**两个"继承"的区别（易混）：**

| 写法 | 母类 | 白拿的能力 |
|---|---|---|
| `class Channel(BaseModel)` | Pydantic | 数据校验 |
| `class Channel(Base)` | SQLAlchemy | 数据库操作（表映射） |

**踩坑**：`__tablename__` 写成 `_tablename_`（前后各一个下划线）→ 数据没写入正确的表，**不报错但结果错**，极难发现。Python 特殊名字都是前后各两个下划线（`__init__`、`__str__`）。

### 8.10 关键注意事项

1. **用完一定要关闭连接**：`cursor.close()` + `conn.close()`，用 `try...finally` 保证。
2. **修改操作要 commit**：`CREATE`、`INSERT`、`UPDATE`、`DELETE` 需要 `conn.commit()`，`SELECT` 不需要。
3. **pandas `to_sql` / `read_sql` 需要 SQLAlchemy 引擎**，不能直接用 pymysql 连接。
4. **密码不写死在代码里**：存在 `.env`，通过 `config.py` 读取，`.env` 加入 `.gitignore`。
5. **建表加 `IF NOT EXISTS`**：重复运行不报错。
6. **写入用 `append` 模式**，不要用 `replace`（会删掉原有数据）；重复跑靠幂等去重。
7. **连接是稀缺资源**：pymysql 默认短连接，高并发下频繁建连开销大，生产环境应使用连接池（SQLAlchemy `pool_size` / `max_overflow` / `pool_recycle`，或用 `DBUtils.PooledDB`）。（源笔记未展开连接池参数细节）

## 九、大模型如何调用业务 API（Function Calling）

### 9.1 为什么需要调用 API

三个硬约束：

1. **大模型不知道内部数据**：数仓里的投放数据、渠道成本，训练语料里没有。
2. **大模型不擅长精确数值计算**：它是"预测下一个词"，不是计算器，算同比、求和容易错。
3. **上下文窗口有限**：装不下整张表，也不可能每次把全量数据塞进 prompt。

所以让模型"决定调哪个接口、传什么参数"，真实数据由业务 API 从数据库取——这就是 Function Calling（工具调用）。

### 9.2 完整流程

```
用户提问
    ↓
大模型理解问题，决定调用哪个工具，并生成参数
    ↓
调用业务 API → API 查数据库 → 返回结构化数据
    ↓
大模型拿到数据，组织成自然语言回答
    ↓
用户看到答案
```

关键点：**模型不直接连数据库**。它只输出"要调 `get_metric(channel='Facebook', date='2024-08-01')`"，真正执行的是后端的工具函数。这样权限、校验、审计都留在服务端。

### 9.3 MCP 与 FastAPI：给 AI 开门 vs 给人开门

| | MCP Server | FastAPI |
|---|---|---|
| 服务对象 | AI Agent / LLM | 人 / 浏览器 / 前端 |
| 调用方式 | 模型自动发现工具、自动调 | 手动点按钮 / 前端 fetch |
| 协议 | MCP（JSON-RPC，`list_tools` / `call_tool`） | REST（GET/POST `/api/xxx`） |
| 类比 | 给 AI 的 USB-C 接口 | 给用户的收银台窗口 |

为什么不能只用一套：

- **人**需要 URL、页面、按钮、中文返回 → REST 最自然。
- **AI**需要"自动发现有哪些工具 + 每个工具的参数 schema" → MCP 的 `list_tools` 专门干这个。

关键设计：**业务逻辑只写一遍，接入层各自封装**。

```python
_get_metric = agent._tool_get_metric   # MCP 复用 agent 的工具函数
```

MCP 开门给 AI，FastAPI 开门给人，门后是同一套函数。否则同一份查询逻辑写两遍，迟早不一致。

### 9.4 技术栈怎么串起来

| 学过的技术 | 在 AI 应用里的角色 |
|---|---|
| 数据清洗、Spark | 准备数据，存到数据库 |
| SQL、MySQL | 存数据，提供查询能力 |
| FastAPI | 把查询包装成 API，给大模型调用 |
| Python 函数 | 异常检测、指标计算，也包装成 API |
| 大模型 API | 理解问题，决定调用哪个工具 |
| RAG / Agent | 把这些串起来，变成智能助手 |

## 十、面试速查

**Q：API、SDK、库有什么区别？**
A：API 是"能做什么"的接口契约；SDK 是封装 API 的工具包，核心是调用 API 的函数；库是本地通用工具。

**Q：怎么快速判断一个包是不是 SDK？**
A：看函数执行结果是在本地算的还是发请求到远端算的——远端算就是 SDK。

**Q：为什么大模型能力不放本地？**
A：装不下（几百 GB 权重 + GPU 集群）、不该给（商业机密只租不卖）、必须集中（状态要共享对账）。

**Q：一次 HTTP 请求由哪几部分组成？**
A：请求行（方法 + 路径 + 版本）、请求头（key: value）、空行、请求体。

**Q：GET 和 POST 的区别？**
A：GET 参数在 URL、可缓存、可收藏、幂等，用于查询；POST 数据在 body、不可缓存、不幂等，用于提交或创建。POST 不等于安全，机密性靠 HTTPS。

**Q：301 和 302 的区别？**
A：301 永久重定向（可被缓存），302 临时重定向。

**Q：401 和 404 的区别？**
A：401 是没通过身份认证；404 是资源不存在。

**Q：429 是什么？怎么处理？**
A：请求过多被限流。应退避重试（指数退避 + 抖动），不是立刻重试。

**Q：500 和 400 是谁的错？**
A：4xx 是调用方的错（参数、认证、路径），5xx 是服务端的错（代码异常、过载）。

**Q：Content-Type 有什么用？**
A：告诉服务器请求体按什么格式解析（JSON / 表单 / 文件），设错会导致字段解析不到。

**Q：什么是 RESTful？**
A：URL 只表示资源（名词复数），动作由 HTTP 方法表达，配合状态码和无状态设计。

**Q：REST 和 FastAPI 什么关系？**
A：REST 是设计规范（理念），FastAPI 是实现工具；FastAPI 适合写 REST API，但不强制。

**Q：FastAPI 和 uvicorn 什么关系？**
A：FastAPI 定义路由和业务逻辑（菜谱），uvicorn 监听端口收发请求（厨师）；FastAPI 要靠 uvicorn 运行。

**Q：路径参数、查询参数、请求体怎么选？**
A：定位唯一资源用路径参数；过滤 / 分页 / 排序用查询参数；字段多、结构复杂的提交用请求体。

**Q：Pydantic 模型有什么用？**
A：声明请求体字段与类型，自动解析、校验（失败返回 422）、生成文档、序列化输出。

**Q：response_model 有什么用？**
A：过滤敏感字段、统一输出结构、生成文档示例。

**Q：为什么 FastAPI 里有时用 def 有时用 async def？**
A：`def` 会被丢进线程池，适合同步库；`async def` 直接在事件循环跑，只能调异步库。`async def` 里放同步阻塞调用会卡死整个事件循环。

**Q：asyncio 事件循环是什么？**
A：单线程调度器，维护就绪任务和 IO 事件；协程在 `await` 处让出控制权，实现单线程并发。是并发不是并行，不适合 CPU 密集任务。

**Q：lifespan 是干什么的？**
A：应用级启动 / 关闭钩子，`yield` 前初始化（连接池、模型），`yield` 后清理。作用是避免每请求重复初始化，并保证优雅关闭。

**Q：lifespan 和 Depends 的区别？**
A：lifespan 是应用级、只执行一次；Depends 是请求级、每请求创建和销毁。连接池放 lifespan，会话放 Depends。

**Q：什么是序列化？**
A：把内存对象（dict / 模型）转成可传输的文本（JSON）；反序列化是反过来。

**Q：为什么 API 用 JSON？**
A：纯文本、语言无关、结构对应 dict/list、可读、浏览器原生支持、比 XML 轻。缺点是弱类型、体积比二进制大。

**Q：HTTP 无状态是什么意思？**
A：服务器不记忆此前的请求，每次都要带 Token / Cookie 自报身份。好处是易扩容。

**Q：数据加载模块有哪几个函数？**
A：`load_from_csv`、`load_from_api`、`validate_data`、`get_data_summary`，对应加载 → 验证 → 理解。

**Q：维度与指标的区别？**
A：维度用来分组和筛选（渠道、地区、设备），指标用来计算和加总（曝光、点击、花费）。

**Q：`parse_dates=['date']` 有什么用？**
A：读入时把字符串转成日期类型，否则无法正确排序、比较和按区间过滤。

**Q：调 API 要处理哪四类异常？**
A：`Timeout`（超时）、`ConnectionError`（连不上）、`HTTPError`（4xx/5xx）、`ValueError`（JSON 解析失败）。

**Q：`Authorization: Bearer xxx` 里的 Bearer 是什么？**
A：认证方案（持票人令牌），表示持有该 token 即可访问；token 是凭证本身。

**Q：数据质量检查有哪六项？**
A：缺列、缺失值、负值、零曝光、重复行、日期范围。

**Q：为什么验证与清洗要分离？**
A：验证只发现问题不自动修复；该不该修、怎么修需要人工判断，自动修复会掩盖问题。

**Q：CTR / CVR / CPC 怎么算？**
A：CTR = 点击 / 曝光；CVR = 转化 / 点击；CPC = 消耗 / 点击。

**Q：算比率为什么要除零保护？**
A：分母为 0 会抛 `ZeroDivisionError` 或产生 `inf`/`NaN` 污染下游，必须先判断分母 > 0。

**Q：`to_sql` 的 `if_exists` 三个选项？**
A：`fail` 报错、`replace` 删表重建（危险）、`append` 追加（最常用）。

**Q：为什么 pandas 写库要 SQLAlchemy 引擎？**
A：pandas 依赖 SQLAlchemy 的方言层生成对应数据库的 SQL，pymysql 连接不提供这层抽象。

**Q：怎么防 SQL 注入？**
A：用占位符 + 参数元组，让驱动把参数当纯数据传入；表名 / 列名不能参数化，必须白名单校验。

**Q：为什么这个项目用函数 + SQL 而不是 ORM？**
A：分析型 SQL 复杂（窗口、JOIN），裸 SQL 更直观可控；依赖少、性能可调、团队都会 SQL。ORM 的优势在换库和交易型 CRUD。

**Q：大模型为什么要调业务 API？**
A：它不知道内部数据、不擅长精确计算、上下文装不下全量数据，只能决定"调什么工具"，真实数据由后端查库返回。

**Q：MCP 和 FastAPI 为什么都要？**
A：服务对象不同——MCP 让 AI 自动发现和调用工具，FastAPI 给人 / 前端用；两者复用同一套业务函数，逻辑只写一遍。
