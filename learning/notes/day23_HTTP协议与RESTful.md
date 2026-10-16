# Day23: HTTP 协议与 RESTful API

> 承接 day22（SDK 底层发 HTTP 请求）。这篇讲清楚 HTTP 到底是什么、请求/响应长什么样、以及 RESTful 设计规范。
> 面试高频：HTTP 方法、状态码、GET vs POST、RESTful 原则。

---

## 一、HTTP 是什么

**HTTP（HyperText Transfer Protocol）** = 客户端和服务器之间"怎么对话"的约定。

- 一次请求（Request）+ 一次响应（Response），一问一答。
- 昨天学的 SDK 底层发的"网络请求"，就是 HTTP 请求。

```
客户端(浏览器/SDK)  ──HTTP请求──▶  服务器(真正干活)
                   ◀──HTTP响应──
```

---

## 二、一个 HTTP 请求长什么样（四部分）

```
POST /v1/chat/completions HTTP/1.1      ← ① 请求行（方法+路径+版本）
Host: api.deepseek.com                   ← ② 请求头（key: value）
Authorization: Bearer sk-xxxx
Content-Type: application/json
                                         ← ③ 空行（分隔）
{"model":"deepseek-chat",...}            ← ④ 请求体（要传的数据）
```

| 部分 | 是什么 |
|---|---|
| 请求行 | 干什么 + 对谁干 |
| 请求头 | 元信息（认证、格式说明） |
| 空行 | 头和体的分隔符 |
| 请求体 | 真正要传的数据（GET 通常没有） |

---

## 三、HTTP 方法（动词，面试必考）

| 方法 | 含义 | 类比 | 项目里 |
|---|---|---|---|
| GET | 读取 | 只看不摸 | `/api/logs` |
| POST | 新建/提交 | 交新东西 | `/api/delete_sub_channel` |
| PUT | 整体更新 | 整个换掉 | 更新配置 |
| PATCH | 局部更新 | 只改一处 | 改预算 |
| DELETE | 删除 | 删掉 | 删记录 |

**GET vs POST 区别（高频）**：
- GET：参数在 URL 上（`?a=1`）、可缓存、可收藏、幂等（读多少次结果一样）
- POST：数据在请求体里、不可缓存、用于提交/修改

---

## 四、状态码（服务器用数字回话）

| 开头 | 含义 | 例子 |
|---|---|---|
| 2xx | 成功 | 200 OK / 201 Created |
| 3xx | 重定向 | 301 永久 / 302 临时 |
| 4xx | 客户端错（你的锅） | 400 参数错 / 401 未认证 / 404 找不到 / 429 限流 |
| 5xx | 服务器错（它的锅） | 500 内部错 / 503 不可用 |

口诀：**4xx 你错了，5xx 我错了，2xx 成了。**

> 实战记忆：昨天下载 bge-m3 被 hf-mirror 限流，就是收到 `429 Too Many Requests`。

---

## 五、RESTful API（设计规范）

两条核心：
1. **URL 指资源（名词）**：`/api/campaigns`、`/api/campaigns/123`
2. **HTTP 方法指动作（动词）**：GET 查 / POST 建 / DELETE 删

| 动作 | 方法 + 路径 |
|---|---|
| 查所有 | GET /api/campaigns |
| 查单个 | GET /api/campaigns/123 |
| 新建 | POST /api/campaigns |
| 改 | PUT /api/campaigns/123 |
| 删 | DELETE /api/campaigns/123 |

**价值**：约定俗成，看 `DELETE /campaigns/123` 不用看文档就知道"删 id=123 的系列"。

---

## 六、项目里一个可改进点（面试加分）

`app.py` 删子渠道写的是：
```
POST /api/delete_sub_channel     ← 动作写在路径里，用 POST
```

更规范的 RESTful 写法：
```
DELETE /api/sub_channels/{sub_channel}   ← 动作用 DELETE 表达，资源用路径
```

属于"风格问题"而非"错误"（很多老项目也这么写），但能主动指出说明懂 RESTful。

---

## 七、一句话总结

> HTTP 是客户端和服务器一问一答的协议，请求含「请求行 + 头 + 体」，方法（GET/POST/PUT/DELETE）表达动作，状态码（2xx/4xx/5xx）表达结果。RESTful 用「名词 URL + 动词方法」把 API 设计得一眼就懂。
