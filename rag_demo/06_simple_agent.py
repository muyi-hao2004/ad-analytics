"""
简单的Agent Demo
大模型自己决定调用哪个工具
"""

import os
from openai import OpenAI
from dotenv import load_dotenv
import json

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# ============================================================
# 第1步：定义两个工具函数
# ============================================================

def query_ad_data(channel: str, metric: str) -> str:
    """
    工具1：查询广告投放数据
    （这里模拟返回数据，实际项目中应该去数据库查）
    """
    print(f"  [工具调用] query_ad_data(channel={channel}, metric={metric})")

    # 模拟数据库里的数据
    mock_data = {
        "Facebook": {"CPA": 45.6, "CTR": 2.3, "CPC": 1.2, "花费": 10000},
        "Google": {"CPA": 38.2, "CTR": 3.1, "CPC": 0.8, "花费": 8000},
        "TikTok": {"CPA": 52.1, "CTR": 1.8, "CPC": 1.5, "花费": 6000},
    }

    if channel in mock_data and metric in mock_data[channel]:
        return f"{channel}今天的{metric}是{mock_data[channel][metric]}"
    else:
        return f"没有找到{channel}的{metric}数据"


def query_knowledge_base(query: str) -> str:
    """
    工具2：查询广告投放知识库
    （这里模拟返回，实际项目中应该调用RAG API）
    """
    print(f"  [工具调用] query_knowledge_base(query={query})")

    # 模拟知识库
    mock_kb = {
        "CPA": "CPA（单次转化成本）= 总花费 / 总转化数。CPA越高，说明获客成本越高。",
        "CTR": "CTR（点击率）= 总点击 / 总曝光。CTR越高，说明广告素材越吸引人。",
        "异常": "如果CPA突然上涨超过历史平均值的50%，就属于异常，需要检查投放设置。",
        "素材": "广告素材连续投放超过7天，CTR会明显下降，这就是素材疲劳，需要及时更换。",
    }

    # 简单匹配关键词
    for key, value in mock_kb.items():
        if key in query:
            return value

    return "知识库中没有找到相关内容"


# ============================================================
# 第2步：告诉大模型有哪些工具可以用
# ============================================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "query_ad_data",
            "description": "查询广告投放的实时数据，比如某个渠道今天的CPA、CTR、CPC、花费等具体数字",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "广告渠道，可选值：Facebook、Google、TikTok"
                    },
                    "metric": {
                        "type": "string",
                        "description": "指标名，可选值：CPA、CTR、CPC、花费"
                    }
                },
                "required": ["channel", "metric"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_base",
            "description": "查询广告投放知识库，比如指标定义、异常原因、操作手册、优化建议等概念性问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的问题"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# ============================================================
# 第3步：Agent的核心逻辑
# ============================================================
def agent_chat(user_question: str):
    print(f"\n{'='*60}")
    print(f"用户问题：{user_question}")
    print(f"{'='*60}")

    # 维护对话历史
    messages = [
        {"role": "system", "content": "你是一个专业的广告投放助手。遇到需要查数据或查知识库的问题，请调用对应的工具。"},
        {"role": "user", "content": user_question}
    ]

    # 循环：大模型可能需要调用多次工具
    max_rounds = 5
    for round_num in range(max_rounds):
        print(f"\n--- 第{round_num+1}轮 ---")

        # 调用大模型
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=tools  # ！！！关键：把工具清单传给大模型
        )

        message = response.choices[0].message

        # 情况1：大模型要调用工具
        if message.tool_calls:
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                print(f"  大模型决定调用工具：{tool_name}")
                print(f"  参数：{tool_args}")

                # 真正执行工具
                if tool_name == "query_ad_data":
                    result = query_ad_data(**tool_args)
                elif tool_name == "query_knowledge_base":
                    result = query_knowledge_base(**tool_args)
                else:
                    result = "未知工具"

                print(f"  工具返回：{result}")

                # 把工具调用和结果加入对话历史
                messages.append(message)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

        # 情况2：大模型直接回答了
        else:
            print(f"\n{'='*60}")
            print("Agent最终回答：")
            print("-" * 60)
            print(message.content)
            print("-" * 60)
            return message.content

    print("\n达到最大轮次，结束")
    return None


# ============================================================
# 测试几个问题
# ============================================================
if __name__ == "__main__":
    # 问题1：查具体数字 → 应该调用query_ad_data
    agent_chat("今天Facebook的CPA多少？")

    # 问题2：查概念 → 应该调用query_knowledge_base
    agent_chat("什么是CPA？")

    # 问题3：查异常原因 → 应该调用query_knowledge_base
    agent_chat("CPA异常上涨怎么办？")
