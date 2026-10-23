# Function Calling 与 Agent

本篇覆盖：决策模型与生成模型的数学差别、Function Calling 的完整协议流程、原生 `tools` 参数与"把 JSON 塞进 prompt"的取舍、工具结果回传的消息角色、多轮循环与 `max_rounds`。
工程部分讲工具 schema 设计（description 是命门）、语义层如何防口径猜错、MCP 如何把 N×M 集成问题变成 N+M，以及快照、trace、人工确认、dry_run 这些上线必备件。
最后回答两个面试高频问题：**什么任务该交给模型、什么任务规则引擎就够**，以及**可解释性到底该怎么给**。

## 一、模型为什么要调工具

### 1.1 三个硬约束

| 约束 | 表现 | 工具如何解决 |
|---|---|---|
| 不知道内部数据 | 数仓里的投放数据、操作日志、内部指标，训练语料里根本没有 | 工具去查库，把结果回传给模型 |
| 不擅长精确计算 | 它是"预测下一个词"，不是算到小数点后三位的计算器 | 指标由代码算好，模型只读结论 |
| 上下文窗口有限 | 全量明细塞不进 prompt，塞进去也会读错、还会烧钱 | 需要细节时按需查，不一次全喂 |

第三条尤其重要：**不是"窗口够大就能全塞"**。数据一多，模型在长上下文里对中段信息的召回会明显下降，而且每轮都要重发全部历史，成本是线性上涨的。

### 1.2 数据工程的能力怎么串起来

| 学过的技术 | 在 AI 应用里的角色 |
|---|---|
| 数据清洗、Spark | 准备数据，写进数据库 |
| SQL、MySQL | 存数据，提供查询能力 |
| FastAPI | 把查询包装成 HTTP API，供模型/Agent 调用 |
| Python 函数 | 异常检测、指标计算，同样包装成工具 |
| 大模型 API | 理解问题，决定调哪个工具、填什么参数 |
| RAG / Agent | 把上面这些串成一个能自己干活的系统 |

这条链的含义：**AI 应用不是替代数据工程，而是给数据工程加了一个自然语言入口。** 底下的数仓、口径、调度一样不能少，模型只是最上面那层"会说话的门面"。

### 1.3 Agent 是什么

**Agent = 大模型 + 一堆工具。**

大模型自己决定四件事：什么时候该调工具、该调哪个工具、参数怎么填、调完下一步干什么。它不执行任何操作，只负责"想"和"选"。

和普通对话的区别：

| | 普通对话 | Agent |
|---|---|---|
| 能干什么 | 只能聊天 | 能调用工具干活 |
| 怎么回答 | 靠训练时记住的知识 | 自己查数据、查知识库 |
| 能不能用你的系统 | 不能 | 能，调用你的 API / 工具 |
| 出错的方式 | 说错话 | 说错话，或者**做错事**（所以需要确认和 dry_run） |

最后一行是关键：Agent 多了"行动"能力，风险等级随之上升。这也是后面要专门讲人工确认、`dry_run`、写操作权限隔离的原因。

## 二、生成模型 vs 决策模型

### 2.1 同一个底座，最后一层用法不同

| | 生成模型（Generative） | 决策模型（判别式 / 打分） |
|---|---|---|
| 干什么 | 逐 token 生成文字 | 给候选打分、排序、选一个 |
| 输出 | 一段话 | 概率分布 / 分数 |
| 本质 | 自回归采样 | 分类 / 排序 |
| 特点 | 慢、贵、可能幻觉 | 快、省、更可控 |

两者共用预训练 Transformer 底座，差别只在最后一层接什么头、怎么解码。

### 2.2 三个关键数学机制

**① 自回归概率链（生成侧）**

```
P(w1, ..., wT) = Π P(wt | w1 ... wt-1)
```

生成就是一步步从条件概率里采样。每多一个 token 就多一次前向，所以"生成一段话"和"输出一个短 JSON"的成本差了一个数量级。

**② 分类打分（决策侧）**

```
P(选项_i) = e^{z_i} / Σ_j e^{z_j}        # softmax
choice = argmax_i P(选项_i)
```

模型对 N 个候选各算一个 logit `z_i`，softmax 归一化成概率，取最大。**一次前向就出结果，不需要逐字生成。**

**③ 偏好对齐训练（让它"选得对"）**

光预训练只会"补全"，不会"选对"——它学的是"下一个词最可能是什么"，不是"哪个选项业务上最优"。

- **RLHF**：人类给多个回答排序 → 训练一个奖励模型 `r(x)` → 主模型最大化奖励。
- **DPO**：跳过奖励模型，直接拿"好/坏"成对样本调模型，工程上更简单、更稳定。

**与已有知识的连接**：XGBoost 二分类（破产预测）本质就是决策模型——输入特征，输出概率，再选阈值。区别只在输入形态：XGBoost 吃结构化财务比率，LLM 吃文本隐向量。softmax 回归是决策侧最基础形态，logistic 回归是它的二分类特例。RLHF 里的"损失函数"就是交叉熵或 pairwise ranking loss。

### 2.3 为什么"选函数"是决策问题

**函数调用不是"让模型写一段调用代码"，而是"从固定候选里选一个，并填参数"。**

| | 生成式调函数（把 JSON 当文本生成） | 决策式选函数 |
|---|---|---|
| 怎么决定调哪个 | 生成函数名 + 参数 JSON，可能拼错、可能编出不存在的函数 | 从注册列表里打分选 Top-1 |
| 可靠性 | 参数幻觉、函数名拼错、多带解释文字 | 候选即白名单，选不到不存在的工具 |
| 可控性 | 弱，只能靠 prompt 约束 | 强，候选集就是权限边界 |

完整闭环：

```
用户意图
  → [决策①] 意图分类（查数据 / 闲聊 / 执行操作）
  → [决策②] 工具选择（N 个函数里打分选 Top-1）
  → 执行函数（真实查库 / 调 API / 写文件）
  → 返回结构化结果
  → [可选] 生成模型润色成人话
```

**分工：决策（选工具、填参数）交给决策侧；表达（把结果说成人话）交给生成侧。**

### 2.4 值不值得换"独立小决策模型"

先纠正一个前提：**现在没有"独立决策模型"让你随便替换。** 同一个 GPT 就是通过 Function Calling 做决策的，不是二选一。

真实的三档对比：

| 方案 | 实际是什么 |
|---|---|
| 文字下指令（无工具） | LLM 生成一段废话，你再人工解析 |
| Function Calling | 同一个 LLM，只输出一小段结构化 JSON |
| 真·独立决策模型 | 小专用分类器（BERT / 小模型），一次前向出分 |

**判断标准：候选集能不能预先枚举。**

> 候选集有限、固定 → 用小决策模型（延迟降约 10 倍、成本降约 100 倍），值得。
> 候选集开放、多变 → 用大模型 + Function Calling，别省这个 token。

比喻：大模型是全科医生，什么都能看但慢且贵；小决策模型是分诊台护士，只做"往哪个科送"这一件事，又快又便宜。

### 2.5 不接 RAG，决策模型也不会更聪明

**换成决策模型，只是省了生成文字，没有让它变聪明。** 它的决策依据只有两个：

1. 预训练权重里的通用知识（可能错、可能过期）；
2. 你喂的 prompt 和候选选项。

**模型上限由输入信息决定，不由模型结构决定。** 这和 XGBoost 是同一个道理：特征全是垃圾，换更深的树 AUC 也上不去。

完整形态必须是：

```
行业知识（RAG / 规则 / DB） → 生成候选集 → 决策模型打分 → 选 Top-1
```

所以"省 token"和"提质量"是两件独立的事：前者靠 Prompt Caching 和精简上下文（见《18_上下文工程与成本优化》），后者靠把正确的信息喂进去。

## 三、Function Calling 原理

### 3.1 一句话定义

**模型不只输出文字，还能输出"我要调用哪个函数、参数是什么"。模型负责想，代码负责做。**

模型永远不真正执行任何东西。它只产出一个结构化意图，执行发生在宿主程序里——这个边界是安全模型的基础，也是"人工确认"能插进去的位置。

### 3.2 七步流程

```
1. 你把工具清单（name / description / parameters）交给模型
2. 用户提问
3. 模型判断：这个问题需要调工具
4. 模型返回：函数名 + 参数 JSON（不返回正文）
5. 你的代码去查表、真正执行这个函数
6. 把执行结果按 tool 角色回传给模型
7. 模型基于结果生成最终回答，或继续要求调用下一个工具
```

### 3.3 一次完整调用的消息序列

```python
import json
from openai import OpenAI

client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": "crt_002 这个素材要不要删？"},
]

# 第一轮：模型返回 tool_calls
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    tools=TOOLS,
    tool_choice="auto",
)
msg = resp.choices[0].message

if msg.tool_calls:
    messages.append({
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
    })

    for tc in msg.tool_calls:
        args = json.loads(tc.function.arguments)
        result = TOOLS_REGISTRY[tc.function.name](**args)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,          # 必须与请求配对
            "name": tc.function.name,
            "content": json.dumps(result, ensure_ascii=False),
        })

    # 第二轮：模型拿着工具结果继续
    resp = client.chat.completions.create(
        model="deepseek-chat", messages=messages, tools=TOOLS,
    )
```

三个容易漏的细节：

- 带 `tool_calls` 的那条 assistant 消息**必须原样 append 回 messages**，否则下一轮模型看到的是"工具结果凭空出现"，会报协议错误或行为错乱。
- `tool_call_id` 必须与请求里的 `id` 一一对应。并行调用多个工具时，这是唯一的配对依据。
- 工具返回的 `content` 必须是字符串。结构体要 `json.dumps`，不要直接塞 dict。

### 3.4 原生 `tools` 参数 vs 把 JSON 塞进 prompt

这是本项目一个**已知的实现缺陷**，也是很多人手写 Agent 时的通病：把工具说明书拼成一大段文本塞进 system prompt，再要求模型"按 JSON 格式输出你要调用的函数"，然后用正则或 `json.loads` 去解析模型输出的文本。

两种做法摆在一起：

| | 原生 `tools` 参数 | 把 JSON 塞进 prompt |
|---|---|---|
| 工具清单在哪 | 独立的 `tools` 数组，走协议字段 | system prompt 里的一大段字符串 |
| 模型输出什么 | 结构化 `tool_calls` 对象 | 一段自由文本，里面"应该"是 JSON |
| 参数校验 | 平台按 JSON Schema 约束，非法参数会被约束或报错 | 只能事后自己校验，错了要重试整轮 |
| 函数名可靠性 | 只能取清单里的名字 | 可能拼错、可能编出一个不存在的函数 |
| 多余内容 | 协议层保证不掺杂解释文字 | 常见"好的，我将调用……"、markdown 代码块包裹、尾随注释 |
| 并行调用 | 原生支持一次返回多个 `tool_calls` | 要自己设计数组格式并祈祷模型遵守 |
| 结果配对 | `tool_call_id` 天然配对 | 没有 id，多轮/并行时容易错配 |
| 可观测性 | 每轮结构清晰，好记 trace | 要把文本再解析一遍才能记日志 |
| token 开销 | 工具清单走专门字段，部分平台有优化 | 同一段说明书每轮都当正文重算 |

**具体会坏在哪**（都是实际会遇到的）：

1. **解析失败**。模型在 JSON 前后加一句"好的，我来查询"，`json.loads` 直接抛异常。你得写容错：截取第一个 `{` 到最后一个 `}`，还要处理 markdown 代码块。
2. **字段漂移**。schema 里叫 `obj_type`，模型某次写成 `object_type`，你的函数收到意料外的关键字参数，`TypeError`。
3. **幻觉函数**。清单里没有 `delete_campaign`，模型觉得该有，就编一个。你必须维护一份白名单做二次校验，否则就是"模型能调用任意函数"。
4. **类型错误**。schema 要求 `budget` 是数字，模型给了 `"5000"` 字符串或 `"5k"`。原生 schema 会在解码阶段就施加约束。
5. **无法并行**。一次只能返回一个调用，多对象查询要串行多轮，轮次和 token 都翻倍。
6. **注入面变大**。工具说明书和业务规则混在同一段文本里，用户输入里的"忽略以上规则"更容易生效。

**正确做法**：

```python
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    tools=TOOLS,              # 走协议字段，不拼进 system prompt
    tool_choice="auto",       # 或 "required" 强制调用，或 {"type":"function","function":{"name":"get_metric"}} 指定
    parallel_tool_calls=True, # 支持时开启，多对象查询一次返回
)
```

配套的三件事：

- **`TOOLS` 用 JSON Schema 写**，`enum` 锁值域、`required` 锁必填、`type` 锁类型。约束交给 schema，不要靠 prompt 里写"请注意 budget 必须是数字"。
- **保留一份 `TOOLS_REGISTRY`（名字 → 函数）**，执行前先查表。即使平台已经约束，自己再校验一次名字是否存在，成本几乎为零。
- **对 `arguments` 做 `json.loads` + 参数校验**，失败时不要把异常直接抛给模型，而是回一条 `role=tool` 的错误说明，让它自己修正一次。

补充一点现实约束：原生 `tools` 依赖模型服务商支持 OpenAI 风格的 function calling。主流中文模型（DeepSeek、通义、智谱等）基本都支持；如果遇到确实不支持的模型，才退回"prompt 里塞 schema + 严格 JSON 输出模式 + 白名单校验"这套降级方案，并且要接受上面列的这些缺陷。

### 3.5 `tool_choice` 的三种用法

| 值 | 行为 | 场景 |
|---|---|---|
| `"auto"` | 模型自己决定调不调、调哪个 | 通用 Agent，默认值 |
| `"required"` | 强制至少调一个工具 | 明确知道这轮必须查数据 |
| `{"type":"function","function":{"name":"get_metric"}}` | 强制调指定工具 | 流程化场景，如"每天先拉快照" |

## 四、工具结果的回传与多轮循环

### 4.1 消息角色：`role=tool` + `tool_call_id`

工具执行结果必须作为独立的一条消息回传，角色是 `tool`：

```python
{
    "role": "tool",
    "tool_call_id": "call_abc123",
    "name": "get_metric",
    "content": "{\"obj_id\": \"crt_002\", \"d7_roas\": 0.037, \"days\": 8}",
}
```

**不要图省事用 `role=user` 顶替**，把结果拼成"查询结果如下：……"塞进用户消息。原因有四条：

1. **语义丢失**。模型分不清"这是用户告诉我的"和"这是我调工具查到的"。前者是需求，后者是证据，混淆后它会用用户的口气复述数据，甚至在数据与用户说法冲突时选择相信用户。
2. **配对断裂**。并行调用多个工具时，没有 `tool_call_id` 就不知道哪条结果对应哪次调用，结果会错配到别的参数上——这类 bug 极难复现。
3. **协议报错**。多数 API 会校验"assistant 的每个 `tool_call` 后面必须跟一条同 id 的 `tool` 消息"，缺了直接 400。
4. **注入风险**。工具返回的内容里如果包含"忽略之前的指令"这类文本（比如日志里有用户填的昵称），放在 `user` 角色下，模型更容易把它当指令执行；放在 `tool` 角色下，至少语义上是"数据"。

### 4.2 多轮循环的骨架

```python
def run_llm_loop(messages, max_rounds=8):
    for round_no in range(max_rounds):
        resp = client.chat.completions.create(
            model="deepseek-chat", messages=messages,
            tools=TOOLS, tool_choice="auto",
        )
        msg = resp.choices[0].message

        # 没有 tool_calls → 模型给出最终结论，退出
        if not msg.tool_calls:
            return {"status": "finished", "content": msg.content, "rounds": round_no + 1}

        messages.append({
            "role": "assistant", "content": msg.content,
            "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
        })

        for tc in msg.tool_calls:
            name = tc.function.name
            if name not in TOOLS_REGISTRY:
                content = json.dumps({"error": f"unknown tool: {name}"})
            else:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    content = json.dumps(TOOLS_REGISTRY[name](**args), ensure_ascii=False, default=str)
                except Exception as e:
                    content = json.dumps({"error": str(e)}, ensure_ascii=False)
            messages.append({
                "role": "tool", "tool_call_id": tc.id, "name": name, "content": content,
            })

    # 轮次耗尽 → 降级
    return {"status": "exhausted", "content": summarize_without_tools(messages), "rounds": max_rounds}
```

### 4.3 为什么要升级成多轮

**旧做法（一次性）**：模型一次输出所有动作，代码按动作类型 `if` 分发执行。问题很致命——**模型只能看快照，不会主动查细节**。

**新做法（多轮循环）**：调一次 → 看结果 → 再决定下一步，直到它认为信息够了。

多轮的价值可以用一个"误删陷阱"讲清楚：

> 快照里 `crt_002` 的聚合 ROI 只有 3.7%，看起来早该删了。
> 但真实明细是：前 7 天有 80% 的天数达标，只有最后一天暴跌。
>
> - **多轮 Agent**：主动三步 `get_metric` 查逐日明细 → `get_logs` 查操作历史 → `finish` 给出「属单日异常，非素材问题，不删」。
> - **一次性 Agent**：看到 3.7% 就直接判删，误杀一个还在赚钱的素材。

**这就是多轮的核心价值：模型按需取数，而不是你喂什么它只能看什么。**

### 4.4 `max_rounds` 与耗尽后的降级

`max_rounds=8` 是防死循环的硬闸门。没有它，模型可能陷入"查了觉得不够 → 再查一遍同样参数 → 还是不够"的循环，一轮几毛钱，跑一晚上能把预算烧穿。

耗尽后不能直接抛异常，要做**降级总结**：

1. 把已有的工具结果整理成一段结构化摘要，让模型**在禁止调用工具的前提下**给出结论（`tool_choice="none"`）。
2. 结论里必须显式标注"信息不完整"，以及缺的是哪一部分。
3. 记一条 `status="exhausted"` 的 trace，这类样本是后续优化工具设计的最好素材——**频繁耗尽通常说明工具粒度不对或 description 没写清**。

### 4.5 并行调用

原生 `tool_calls` 是数组，一次可以返回多个调用。典型场景："对比 crt_001 和 crt_002 的 D7 ROAS"——两个查询互不依赖，应该并行。

并行的收益是**轮次减少**：串行两轮 = 两次完整上下文重发，并行一轮 = 一次。在首轮上下文 7500 token 的情况下，省一轮就是省 7500 输入 token。

限制：只有**互不依赖**的调用能并行。如果第二个工具的参数依赖第一个工具的结果（先查对象列表，再逐个查明细），必须串行。

## 五、工具设计

### 5.1 三个字段

| 字段 | 作用 | 写不好的后果 |
|---|---|---|
| `name` | 函数标识 | 名字含糊（`get_data`）→ 模型分不清它和别的工具的边界 |
| `description` | **最重要**。告诉模型什么时候用它 | 写不清 → 模型不调用，或调错工具 |
| `parameters` | JSON Schema，定义参数名/类型/必填/取值 | 不约束 → 模型瞎填，运行时报错 |

### 5.2 `description` 是命门

模型选不选这个工具，**几乎完全取决于 description**。一个合格的 description 要写三样：

1. **触发场景**——什么问法该用它。
2. **边界**——什么情况不该用它。
3. **与相似工具的区别**——尤其是几个工具功能相近时。

四个读工具必须互相划清边界：

| 工具 | 边界 | 典型问法 |
|---|---|---|
| `get_metric` | 单对象下钻，查某对象的逐日指标 | "crt_002 昨天 ROI 多少" |
| `get_top_history` | 横向对比，多对象排行 | "哪些素材 ROI 最差" |
| `get_trend_with_ops` | 因果归因，趋势 + 操作对齐 | "为什么 ch_001 前天掉了" |
| `get_logs` | 只查操作记录 | "这个组被谁改过预算" |

如果不写边界，模型面对"为什么掉了"会随机挑一个：可能用 `get_top_history` 拿了个排行，然后编一个原因。

### 5.3 `parameters` 的三件套

- **`enum` 锁值域**：`metric` 只能在 `["d7_roas","cpa","ctr","cvr"]` 里选，模型没法瞎填一个 `retention`。
- **`required` 锁必填**：不填就报错，而不是让模型自己补一个默认值。
- **格式示范**：`description` 里给一个真实样例，如 `"obj_id 形如 ch_001 / crt_002 / camp_003"`，比写十遍"请遵守命名规范"都有效。

### 5.4 最小可用 schema

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_metric",
            "description": (
                "查询单个投放对象（渠道/子渠道/素材/广告组）的逐日指标明细。"
                "用于回答『某个对象某天/某几天的表现如何』这类下钻问题。"
                "不用于多对象排行（用 get_top_history），"
                "不用于查操作记录（用 get_logs）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "obj_type": {
                        "type": "string",
                        "enum": ["channel", "sub_channel", "creative", "campaign"],
                        "description": "对象类型",
                    },
                    "obj_id": {
                        "type": "string",
                        "description": "对象 ID，形如 ch_001 / crt_002 / camp_003",
                    },
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["d7_roas", "cpa", "ctr", "cvr", "spend", "revenue"],
                        },
                        "description": "要查的指标，不含留存类指标",
                    },
                    "days": {
                        "type": "integer", "enum": [1, 3, 7, 14, 30],
                        "description": "回看天数，默认 7",
                    },
                },
                "required": ["obj_type", "obj_id", "metrics"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_sub_channel",
            "description": (
                "删除一个子渠道（写操作，需人工确认后执行）。"
                "调用前必须先用 get_metric 和 get_logs 验证该对象确实持续不达标，"
                "单日异常不允许删除。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sub_channel_id": {"type": "string", "description": "形如 sub_004"},
                    "reason": {"type": "string", "description": "删除理由，需引用具体指标与天数"},
                },
                "required": ["sub_channel_id", "reason"],
            },
        },
    },
]
```

### 5.5 工具粒度的取舍

**参数化优于堆函数。** 项目的 `get_metric` 用 `obj_type × metric` 两个维度，一个函数吃掉了"4 类对象 × 6 个指标"的所有组合，而不是写 24 个函数。

- 好处：工具清单短（省 token）、description 不用重复 24 遍、加指标只改一个 `enum`。
- 代价：单次调用的参数更多，模型填错的概率略升——用 `enum` + 格式示范压住。

反过来的情况：如果两个操作**语义完全不同**（读 vs 写），不要合并成一个带 `action` 参数的函数。写操作需要不同的人工确认策略和权限，混在一起会让"确认"这道闸门失效。

### 5.6 注册表：加工具只加一行

```python
TOOLS_REGISTRY = {
    "get_metric": get_metric,
    "get_top_history": get_top_history,
    "get_trend_with_ops": get_trend_with_ops,
    "get_logs": get_logs,
    "delete_sub_channel": delete_sub_channel,
    "adjust_budget": adjust_budget,
    "adjust_target": adjust_target,
    "search_knowledge": search_knowledge,
    "get_metric_definition": get_metric_definition,
}
```

职责分离很清楚：`TOOLS`（给模型看的说明书）和 `TOOLS_REGISTRY`（给代码用的执行表）是两份东西，靠 `name` 对齐。加一个工具 = 加一段 schema + 加一行字典，不动循环逻辑。

### 5.7 参数层的实战优化

这几条是"真的调过"才写得出来的：

- `get_top_history` 的 `metric` 描述里补一句「**不含留存**」——否则模型会把留存当成可选指标瞎传，然后拿到空结果。
- `delete_sub_channel` 的 `target` 补上前置约束「**必须先用 get_metric / get_logs 验证过**」——把业务红线写进工具定义，比写在 system prompt 里更难被忽略。
- SYSTEM_PROMPT 里把 RAG 引导改成「**先数据后经验**」，明确优先级；顺带修掉重复编号（3.5 改成 5）——提示词里的编号错乱会让模型引用错章节规则。

### 5.8 工具清单的演进

最早的原型只有 4 个工具，靠 description 做路由：

| 工具名 | 功能 |
|---|---|
| `query_ad_data` | 查询广告投放数据 |
| `detect_anomaly` | 检测投放异常 |
| `query_knowledge_base` | 查询知识库 |
| `get_daily_report` | 生成日报 |

实际的路由效果：

| 用户问法 | 模型选的工具 |
|---|---|
| "今天 Facebook 的 CPA 多少？" | `query_ad_data` |
| "什么是 CPA？" | `query_knowledge_base` |
| "Facebook 有没有异常？" | 先 `detect_anomaly`，再 `query_ad_data` 查详情 |
| "给我一份日报" | `get_daily_report` |

第三行值得注意：**同一个问题里出现了两次工具调用**，而且第二次的参数依赖第一次的结果（先知道哪个渠道异常，再查它的详情）。这正是后面必须升级成多轮循环的直接原因——一次性分发做不了这种链式查询。

随着场景变复杂，工具从 4 个扩到 9 个：`get_metric`（参数化下钻）、`get_top_history`、`get_trend_with_ops`、`get_logs`、`delete_sub_channel`、`adjust_budget`、`adjust_target`、`search_knowledge`、`get_metric_definition`。

扩到 9 个之后，工具之间开始互相重叠——`get_metric` 也能查多对象，`get_logs` 也带一点趋势。**这时候 description 的"边界"就比"功能"更重要**，否则模型会挑一个"看起来也能用"的。

## 六、语义层（Semantic Layer）

### 6.1 问题背景

一个反直觉的事实：**让模型写 SQL（text-to-SQL）本身已经基本被解决，真正没解决的是"让模型不猜业务口径"。**

裸 LLM 直接对原始表写 SQL，在企业真实场景的准确率只有约 21%（Spider 2.0 基准），在更贴近真实数仓的 BEAVER 基准上甚至接近 0。

**只要加一层语义层，准确率能拉到 90%+。**

所以当前主流产品（Snowflake Cortex Analyst、Databricks Genie、dbt Semantic Layer、Cube、Wren AI）走的是同一条路：**天花板不在模型，在"口径喂得准不准"。**

### 6.2 它是什么

> 在"原始数据表"和"AI / 用户"之间加一层**业务语言翻译器**：把冷冰冰的表名、字段、计算逻辑，翻译成业务能懂、Agent 能正确使用的概念。

Agent 要回答"D7 ROAS 是多少"，面对裸表 `fact_daily_detail`，它必须猜三件事：

- ROAS = `revenue / spend` 吗？还是 `revenue / (spend + 分成)`？
- "D7" 是第 7 天当天的值，还是安装后 7 天累计？
- 退款要不要扣？只算已归因的还是全量？

**裸 LLM 会猜，猜错口径就是灾难**——数字看起来很正常，结论完全反了。语义层就是把口径显式、结构化地存下来，让它从"猜"变成"照抄标准答案"。

```yaml
metrics:
  d7_roas:
    definition: "D7 窗口内累计收入 / 累计广告花费"
    formula: "SUM(revenue) / SUM(spend)"
    fields: [revenue, spend, install_date]
    window: "install_date + 0..6 day"
    filters: ["refund_flag = 0", "attribution_status = 'attributed'"]
    note: "退款已剔除，只算已归因"
  cpa:
    definition: "获客成本 = 花费 / 归因安装数"
    formula: "SUM(spend) / SUM(installs)"
    note: "不含自然量"
```

### 6.3 同名不同义：IAA 业务的活教材

`eCPM`、`CTR`、`CVR` 在**买量侧和变现侧各有一套口径**：

| 指标 | 买量侧 | 变现侧 |
|---|---|---|
| eCPM | `spend / impressions × 1000`（成本视角） | `ad_revenue / ad_impressions × 1000`（收入视角） |

同名、不同轨道、算法不同、量级可能差几倍。不锁死口径，Agent 会在"买量 eCPM 涨了"和"变现 eCPM 涨了"之间随机选一个，然后给出完全相反的优化建议。

**这就是"必须靠语义层锁死口径"最有说服力的例子**——不是理论风险，是每天都会发生的错误。

### 6.4 落地形态

| 文件 | 作用 |
|---|---|
| `data_dictionary.md` | **旧指标库**：给人看的 Markdown，内容零散，Agent 不能直接查 |
| `metrics.yaml` | **新语义层**：结构化 YAML，Agent 能精确查 |
| `semantic_layer.py` | 加载器，提供 `get_metric_definition(metric)` 只读查询 |
| `agent.py` | 新增第 9 个工具 `get_metric_definition`，决策前先查口径 |

**指标库的演进：`data_dictionary.md`（给人读）→ `metrics.yaml`（给 Agent 查）。**

关键设计是**声明式 YAML**：指标定义和代码分离。加一个指标只改 YAML，不改 Python；这也正是 dbt / Cube / Snowflake 的主流做法，不是自创。

```python
def get_metric_definition(metric: str) -> dict:
    """只读查询，返回 definition / formula / filters / note"""
    return METRICS.get(metric) or {"error": f"unknown metric: {metric}"}
```

（源笔记未展开：YAML 的加载方式、指标缺失时的降级策略、多版本口径如何共存，均未记录。）

作为第 9 个工具接进 Function Calling 后，Agent 的决策顺序变成：

```
用户问 D7 ROAS
  → get_metric_definition("d7_roas")   # 先拿口径
  → get_metric(...)                     # 再按口径取数
  → 结论
```

多花一次工具调用，换掉"算错口径"这个最高频的严重错误，非常划算。

### 6.5 语义层 vs RAG

| | 语义层 | RAG |
|---|---|---|
| 装什么 | 硬口径：定义、公式、过滤条件 | 软经验：方法论、踩坑、行业知识 |
| 检索方式 | 精确键值查询 | 语义相似度检索 |
| 出错方式 | 查不到（可发现） | 查到了但排错序（不可发现） |
| 能不能错 | 一个标点都不能错 | 允许"差不多" |

一句话：**RAG 是"猜最像的"，语义层是"查确定的"。** 详见《16_RAG知识库》第 5.4 节。

## 七、MCP（Model Context Protocol）

### 7.1 它解决什么问题

没有标准之前，集成是 **N×M 问题**：3 个模型 × 5 个数据源 = 15 套适配代码。每加一个模型，要把 5 个数据源重写一遍；每加一个数据源，要在 3 个模型上各接一次。

有了标准协议，变成 **N+M**：每个数据源写一个 MCP Server，每个模型/客户端实现一个 MCP Client，之后任意组合自动连通。

```
无协议：模型A─┬─数据源1  模型B─┬─数据源1  ...
              ├─数据源2         ├─数据源2
              └─数据源3         └─数据源3      → 3×5 = 15 套

有 MCP：模型A ─┐                  ┌─ Server1
       模型B ─┼─ MCP 协议 ────────┼─ Server2   → 3+5 = 8 套
       模型C ─┘                  └─ Server3
```

类比：**MCP 是 AI 界的 USB-C**——不管什么设备，插同一个口就能用。它是开放协议/标准（Anthropic 发起，全行业支持），不是一个具体产品。

MCP 发布后生态增长极快，下载量从十万级涨到接近一亿，已经成为事实标准。与之互补的是 **A2A（Agent-to-Agent）**，解决"Agent 连 Agent"：**MCP 连工具，A2A 连 Agent。**

### 7.2 核心概念

| 概念 | 是什么 | 类比 | 项目里的对应物 |
|---|---|---|---|
| **Server** | 暴露能力的一方，一个进程/服务 | USB 设备 | `mcp_server.py` |
| **Client** | 连接 Server 的一方，通常内嵌在宿主应用里 | 电脑的 USB 控制器 | Agent 侧的 MCP 客户端 |
| **Tool** | 可被调用的函数，带 name/description/inputSchema | 设备上的一个按钮 | 9 个工具 |
| **Resource** | 可被读取的数据，按 URI 寻址 | 设备里的一个文件 | 数仓表、知识库文档 |
| **Prompt** | 预置的提示模板，可带参数 | 设备附带的说明书 | SYSTEM_PROMPT |
| **Transport** | 传输层，常见 stdio（本地进程）与 HTTP/SSE（远程） | 线缆类型 | 本地开发用 stdio |

Tool 和 Resource 的区别值得说清：**Tool 是"做一件事"，可能有副作用，需要参数；Resource 是"读一份数据"，幂等、只读、按 URI 取。** 把只读数据硬做成 Tool 也能跑，但会丢掉"可缓存、可列举、无副作用"这些语义。

### 7.3 FastMCP 是什么

**帮你快速写 MCP Server 的 Python 库**，是官方 `mcp` 库之上的一层"省事封装"。

- 官方 `mcp`：底层、标准，样板代码多——手动管 JSON-RPC、注册 schema、连接握手。
- FastMCP：高层封装，一个 `@mcp.tool()` 装饰器就把 Python 函数变成工具，类型注解自动转成 `inputSchema`。

```python
from fastmcp import FastMCP   # 或 mcp 2.x 里的 MCPServer

mcp = FastMCP("iaa-analytics")

@mcp.tool()
def get_metric(obj_type: str, obj_id: str, metrics: list[str], days: int = 7) -> dict:
    """查询单个对象的逐日指标明细。用于下钻，不用于排行。"""
    return query_metric(obj_type, obj_id, metrics, days)

if __name__ == "__main__":
    mcp.run()   # 默认 stdio
```

**一个真实的版本坑**：pip 现在默认装的是 `mcp` 2.x，这个版本把 `FastMCP` 类名改成了 `MCPServer`（API 几乎一样，只是类名和 import 路径变了）。所以项目里 `mcp_server.py` 实际用的是 `MCPServer`，但写法血统就是 FastMCP 那一套——这反而说明用的是最新标准。

### 7.4 与 FastAPI 的分工

| | FastAPI | MCP |
|---|---|---|
| 面向谁 | 人、前端、其他后端服务 | AI 客户端 / Agent |
| 接口描述 | OpenAPI（给人看文档、给代码生成 SDK） | Tool / Resource schema（给模型看） |
| 调用方式 | HTTP 请求，路径 + 参数 | JSON-RPC，方法名 + 结构化参数 |
| 发现问题 | 读文档或 OpenAPI | 客户端启动时 `list_tools` 自动发现 |
| 适合 | 业务 API、增删改查、给前端用 | 把能力暴露给任意 Agent |

两者不冲突，通常是叠加：**底层用 FastAPI 提供业务 API，上层用 MCP Server 把同一批能力包装成模型可发现的工具。** 也可以让 MCP Server 内部直接调函数，省掉一次 HTTP。

### 7.5 项目里的演进

- **之前**：8 个工具硬编码在 `agent.py` 的 `TOOLS_DEFINITION` 里，只有自己的 Agent 能调。
- **现在**：`mcp_server.py` 把 9 个工具封装成标准 MCP Server，**任何支持 MCP 的 Agent**（换 DeepSeek、Claude、别人的 Agent）都能发现并调用，不用重写工具代码。

三个好处：

1. 跟上标准，不被单一模型绑定。
2. 解耦——数仓逻辑独立成服务，Agent 只依赖协议。
3. 简历上"用 MCP 封装数仓能力"比"手写 Function Calling"更能体现架构意识。

（源笔记未展开：`mcp_server.py` 的 transport 选择、鉴权方式、多租户隔离都没有记录。）

### 7.6 语义层 vs MCP：一个硬币的两面

| | 解决什么 | 一句话 |
|---|---|---|
| **语义层** | Agent **看到什么** | 喂正确的口径、表关系、指标定义 |
| **MCP** | Agent **怎么拿到** | 定义标准接口，让任何 Agent 发现并调用 |

```
你的 Agent（LLM，负责决策）
        │  MCP 协议（标准管道）
        ▼
   MCP Server（封装数仓 / 知识库）
        │  语义层（保证"算得对"）
        ▼
   数仓 / 知识库
```

记忆：**语义层是"内容"，MCP 是"管道"。**

## 八、Agent 工程要点

模型能力只决定上限，工程细节决定它能不能上线。下面每一条都对应一个真实会踩的坑。

### 8.1 快照只给结论，不给原始数据

发给 LLM 的不是数仓原始表，而是**聚合好的精简快照**：每个维度（素材 / 子渠道 / 系列）聚合成一行，只放派生指标结论。

两个原因：

- **token**：原始明细几千行，塞不进去也烧不起。
- **正确性**：让模型自己拿 `installs` 和 `revenue` 算 ROI，它会算错。**派生指标代码算好，模型只读结论**——对应"硬口径走代码、软经验走 LLM"。

细节见《18_上下文工程与成本优化》第六章。

### 8.2 全链路 trace

每一轮都记：`round`、`tool name`、`parameters`、`result`、耗时、token 用量；结束轮记 `reasoning` 和最终动作。

```json
{
  "run_id": "run_0001",
  "rounds": [
    {"round": 1, "type": "tool", "name": "get_metric",
     "parameters": {"obj_id": "crt_002", "days": 8}, "result_digest": "d7_roas=0.037, 7/8 天达标"},
    {"round": 2, "type": "tool", "name": "get_logs",
     "parameters": {"obj_id": "crt_002"}, "result_digest": "无操作记录"},
    {"round": 3, "type": "finish", "reasoning": "单日异常，非素材问题，不删", "action": "keep"}
  ],
  "usage": {"prompt_tokens": 9120, "completion_tokens": 380, "cached_tokens": 5650}
}
```

这张表是排障的唯一依据。没有 trace，"模型昨天删错了一个组"这件事永远查不出原因。

### 8.3 写操作前必须人工确认

**Agent 出建议 → 人拍板 → 才执行。** 这不是过度设计，是"人机协作"的产品定位——真实优化师不会让一个模型直接动预算。

状态机：

```
pending ──approve──→ approved ──→ executed
   │
   └──reject──→ rejected
```

规则：**非 `pending` 状态不能再 approve 或 reject**（幂等 + 防重复执行）。

| 接口 | 作用 |
|---|---|
| `POST /api/agent/propose` | 出建议，落库为 `pending` |
| `POST /api/agent/approve` | 确认，转 `approved` 并执行 |
| `POST /api/agent/reject` | 放弃，转 `rejected` |
| `GET /api/agent/runs` | 历史运行列表 |

配套的 `agent_run_history` 表记录每次运行的完整 trace，审批人能看到"它为什么这么建议"再决定。

### 8.4 `dry_run` 默认开

新工具、新 prompt、新模型上线时，默认 `dry_run=True`：**完整跑一遍决策链路，产出建议和 trace，但不真正执行写操作。**

- 默认关是危险的：一次 prompt 调整就可能让模型开始批量删组。
- 默认开后，先灰度观察几天的建议质量，确认无误再逐个放开写权限。
- 实现上把"决策"和"执行"彻底分开：`propose()` 只产出动作对象，`apply()` 才落库。`dry_run` 只影响后者。

### 8.5 工具调用去重与缓存

同一轮里模型经常重复调同一个工具同一组参数（尤其在多轮循环里"忘了自己查过"）。两层防护：

1. **轮内去重**：以 `(tool_name, canonical_json(args))` 为 key 缓存结果，命中就直接返回缓存，并告诉模型"这是本轮的重复查询"。省一次查库，也省一次 token。
2. **跨轮缓存**：快照类、口径类数据当天不变，可以按 `(tool, args, date)` 缓存。指标类数据要注意时效，别把昨天的结果当今天用。

参数要做**规范化**再当 key：键排序、去掉 `None`、数字统一类型。否则 `{"days":7}` 和 `{"days":"7"}` 会被当成两次不同调用。

### 8.6 日志匹配不要用子串包含

判断"某对象有没有被操作过"，很多人写 `if obj_id in log_text`。这会错：

- `ch_001` 是 `ch_0010` 的子串 → 把别人的操作算到它头上。
- 日志里的备注文本可能恰好包含这个 id，但不是针对它的操作。
- 大小写、前后空格、全角半角不一致 → 漏匹配。

正确做法：

- 日志表里**存结构化的 `obj_id` 字段**，用等值匹配，不做文本搜索。
- 必须做文本匹配时，用**词边界**（正则 `\bch_001\b`）或分隔符切分后精确比较，不要 `in`。
- 匹配完抽样人眼核对 20 条，这类 bug 不会自己暴露，只会悄悄污染决策依据。

### 8.7 在工具层做数据对齐

Agent 天然能"把两条数据放一起推理"，但前提是**喂给它的数据已经对齐**。

之前 `get_top_history`（排行）和 `get_logs`（操作）是两坨割裂的数据，模型只能"粗结合"——它得自己猜哪条日志对应哪一天，经常猜错。

`get_trend_with_ops` 把 `daily_detail`（数仓逐日趋势）和 `operation_log`（操作记录）按**对象 + 时间**在**工具层 join 好**，返回"逐日指标 + 当天操作"的合并视图。

价值：模型不用做 join，只需要做判断。**能由代码确定性完成的关联，不要交给模型猜。**

一个验证过的效果：`ch_001` 聚合口径 91% 达标，却被日志标记"ROI 偏低"删了两次——对齐后模型直接看出这个矛盾，判断是误删、应当恢复。**对齐之后才能发现矛盾并纠错。**

### 8.8 三个真实踩坑

| bug | 现象 | 原因 | 修法 |
|---|---|---|---|
| 快照取样错误 | 快照里看起来全是同一个素材的不同日期 | `head(top_n)` 直接取前 N 行，而每个素材有 8 天数据 | 按维度 ID 聚合，每个维度只占一行 |
| mock 数据 ROI 虚高 | D7 ROI 冲到 533%，IAA 业务不可能 | 造数系数 0.02 太大 | 改成 0.006，头部 160% / 中部 30~80% / 尾部 2%，70% 红线正好能区分 |
| 日志误记 | 出现大量"预算改成 None"的脏日志 | adjust 传 `{"budget": None}` 被当成"改成 None" | 归一化：字段传 `None` = 不改，写入前剔除 |

**结论：LLM 的决策质量取决于 ① 快照聚合正确 ② 数据口径合理 ③ 日志干净，三者缺一不可。** 这三件事全在模型之外——这也是"数据工程能力决定 AI 应用上限"的具体含义。

## 九、什么任务交给模型，什么任务规则引擎就够

### 9.1 两条轴

**能不能模型化，看两点：① 输出是不是"唯一确定的一个数"；② 数据量够不够大。**

```
                    数据量少 ←──────────→ 数据量大
输出单一 ↑          [规则 / 统计]          [✓ 训模型]
输出不唯一 ↓        [LLM + 工具 + 知识库]   [LLM 生成式]
```

| 象限 | 做法 | 例子 |
|---|---|---|
| 输出单一 + 数据多 | 训模型 | 出价、CTR 预估、推荐排序、图像识别 |
| 输出不唯一 + 数据少 | LLM + 工具 + 知识库 | 投放优化决策（本项目） |
| 输出单一 + 数据少 | 规则 / 统计 | 阈值判断、简单打标 |
| 输出不唯一 + 数据多 | LLM 生成式 | 写文案、对话、总结 |

### 9.2 为什么投放优化不适合训神经网络

理由的核心**不是"输入类型杂"，而是"输出不唯一 + 数据少"**：

- **输出不唯一**：同一个广告表现差，正确答案可能是降价、可能是换素材、可能是换渠道。**没有唯一标准答案。** 监督学习要学"唯一的一个数"，学不了"决策"。
- **数据量少**：行业经验、业务突发只有几十到几百条样本，喂不饱神经网络。

"输入类型杂"其实不是障碍——图像识别输入是几百万像素照样训。**真正的死穴是输出不唯一 + 数据少。**

### 9.3 广告实时出价（RTB）是什么模型

出价模型本身也**不是神经网络，而是传统 ML（XGBoost / 逻辑回归）**。

场景：用户打开 App 要展示广告位，平台在**几十毫秒内**完成一次拍卖，几十家广告主同时报价。模型要在这几十毫秒里回答一件事：

> "这个用户值不值得我出 5 块钱买下这次展示？"

- **为什么适合模型化**：输出单一（预估收益 eCPM，一个数）+ 数据海量（每秒百万级请求）。
- **为什么不用神经网络**：要快（几十毫秒）、要便宜（每笔请求都算），深模型的推理成本撑不住。

### 9.4 按数据量选型

| 数据量 | 做法 | 例子 |
|---|---|---|
| 极少（几十~几百条） | **LLM + 工具 + 知识库** | 投放优化中台（`agent.py` + `knowledge_base.py`） |
| 海量（百万+） | **训 / 微调模型**（XGBoost / 神经网络） | 广告出价、CTR、推荐 |
| 中间（万~十万） | **传统 ML**（XGBoost / 逻辑回归） | 财务困境预测 |

**LLM 的两个硬伤，决定它替代不了训练模型**：

1. **贵 + 慢**：每次调 API 要几秒；训练好的 XGBoost 预测一次是微秒级、几乎免费。广告出价每秒几万次，不可能每笔都问 LLM。
2. **不精确**：LLM 是"预测下一个词"，不是"算精确到 0.001 的数"。

### 9.5 一句话结论

> **能不能训模型取决于数据量，要不要用 LLM 取决于任务形态。**
> 数据少用 LLM + 工具 + 知识库；数据多且要实时精确的用训模型；中间地带用传统 ML。

三条红线：

- 别理解成"有 LLM 就够了、永远不用训模型"——大厂实时场景还是得训模型。
- 别把"出价模型"说成神经网络——它是传统 ML，为了快和便宜。
- 别把"输入类型杂"当成不训模型的理由——真正理由是"输出不唯一 + 数据少"。

## 十、可解释性

### 10.1 两种解释，可靠性完全不同

**可解释性 = 客观 trace（代码记录，可靠）+ 主观 reasoning（LLM 当轮顺带写，仅供参考）。**

| | 客观 trace | 主观 reasoning |
|---|---|---|
| 谁产生 | 代码，机械记录 | 模型，当轮生成 |
| 内容 | 调了哪个工具、什么参数、返回什么 | 为什么这么做、为什么不那么做 |
| 可靠性 | 高，不会编 | 中低，可能与真实推理过程不符 |
| 用途 | 排障、审计、复盘 | 给人看的决策说明 |

**关键原则：不能事后单独调一次 LLM 让它"解释刚才为什么这么决策"。** 那时它手里没有当时的上下文，只会根据结论编一个听起来合理的理由——这是幻觉的完美温床，而且是**最难被发现的一种幻觉**，因为它和结论高度自洽。

### 10.2 怎么做

1. **在 `finish` 的结构里加 `reasoning` 字段**，要求写清"为什么做 + 为什么不做"。

```json
{
  "action": "keep",
  "target": "crt_002",
  "reasoning": "聚合 D7 ROAS 3.7% 低于 70% 红线，但逐日看前 7 天有 80% 达标，仅最后一天跌至 12%；日志无相关操作。判断为单日数据异常而非素材衰退，故不删，建议明日复查。",
  "confidence": "medium"
}
```

2. **trace 记录完整证据链**：tool 轮存 `name + parameters + result`，finish 轮存 `reasoning + action`。两者用同一个 `run_id` 串起来。
3. **渲染成看板**：把"结论 + 推理链 + 每轮 trace + 动作"渲染成 HTML（`run_agent_dashboard.py`），审批人一屏看完就能判断该不该批。

### 10.3 易错点

- 把 `reasoning` 当成"可靠的解释"对外承诺 → 它只是当轮的参考，出问题时以 trace 为准。
- `reasoning` 写得太短（"因为 ROI 低"）→ 没有证据链，等于没解释。要求它**引用具体数值和天数**。
- trace 只记工具名不记参数 → 复现不了。参数才是决策的关键输入。

## 面试速查

- **生成模型和决策模型的区别？** → 前者逐 token 自回归采样，后者一次前向对候选打分再 argmax。
- **为什么决策更省？** → 不用逐字生成，只输出一个短 JSON 或一个分数，token 差一个数量级。
- **softmax 在决策里干什么？** → 把 N 个候选的 logit 归一化成概率，取最大项。
- **RLHF 和 DPO 的区别？** → RLHF 先训奖励模型再优化；DPO 直接用偏好对调模型，跳过奖励模型。
- **该不该换独立小决策模型？** → 看候选集能否枚举；有限固定就值得（延迟降 10 倍、成本降 100 倍），开放多变就用大模型。
- **不接 RAG，决策模型会更强吗？** → 不会，只是省了生成；上限由输入信息决定。
- **Function Calling 的本质？** → 模型输出结构化调用意图（函数名 + 参数 JSON），宿主执行，结果回传。
- **原生 tools 和把 JSON 塞 prompt 的区别？** → 前者有 schema 约束、结构化 `tool_calls`、天然配对和并行；后者要自己解析、易出解析失败/字段漂移/幻觉函数。
- **工具结果用什么角色回传？** → `role=tool` + `tool_call_id`，不能用 `role=user` 顶替。
- **为什么不能用 user 角色？** → 丢失"这是证据不是需求"的语义、并行时配对断裂、协议校验报错、注入风险更高。
- **多轮 Function Calling 的核心？** → 循环：调用 → 回传 → 再判断，不是"一次调多个函数"。
- **多轮比一次性强在哪？** → 能按需下钻，而不是喂什么看什么；误删陷阱就是靠这个避开的。
- **`max_rounds` 干什么？** → 防死循环；耗尽后要在禁用工具的前提下降级总结，并标注信息不完整。
- **工具 description 要写什么？** → 触发场景 + 边界 + 与相似工具的区别。
- **parameters 三件套？** → `enum` 锁值域、`required` 锁必填、格式示范对齐约定。
- **工具粒度怎么定？** → 读操作参数化合并，读写分离，写操作单独成工具以便挂确认和权限。
- **语义层解决什么？** → 让模型不猜口径；裸 LLM 写 SQL 约 21%，加语义层可到 90%+。
- **为什么口径不能放 RAG？** → 相似度检索会漏会歪，口径必须精确键值查询。
- **IAA 里同名不同义的典型？** → eCPM / CTR / CVR 在买量和变现侧各有一套口径。
- **MCP 解决什么数学问题？** → 把 N×M 集成降成 N+M。
- **MCP 的四个核心概念？** → Server、Client、Tool、Resource（另有 Prompt 和 Transport）。
- **FastMCP 是什么？** → 官方 mcp 库的高层封装，一个装饰器把函数变成工具；mcp 2.x 里类名改为 `MCPServer`。
- **MCP 和 FastAPI 的分工？** → FastAPI 面向人和服务，MCP 面向 Agent 的工具发现与调用。
- **语义层和 MCP 的关系？** → 语义层是内容（看到什么），MCP 是管道（怎么拿到）。
- **为什么写操作要人工确认？** → 定位是人机协作；状态机 pending → approved/rejected，非 pending 不能再审。
- **`dry_run` 为什么默认开？** → 完整跑决策但不执行写操作，先灰度看建议质量再放开权限。
- **日志匹配为什么不能用 `in`？** → `ch_001` 是 `ch_0010` 的子串，会错配；要用结构化字段等值或词边界匹配。
- **可解释性怎么给？** → 客观 trace（可靠）+ 当轮 reasoning（参考）；不能事后让模型补解释。
- **什么任务该训模型？** → 输出单一 + 数据海量 + 要求毫秒级，如出价、CTR。
- **什么任务规则引擎就够？** → 输出单一 + 数据少，阈值判断这类。
- **出价模型是神经网络吗？** → 不是，是 XGBoost / 逻辑回归，为了快和便宜。
