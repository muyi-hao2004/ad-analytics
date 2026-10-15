"""
广告投放智能助手 - Agent核心
大模型自己决定调用哪个工具
"""

import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from tools import (
    query_ad_data,
    detect_anomaly,
    query_knowledge_base,
    get_daily_report,
    TOOLS
)

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# 工具名到函数的映射
TOOL_FUNCTIONS = {
    "query_ad_data": query_ad_data,
    "detect_anomaly": detect_anomaly,
    "query_knowledge_base": query_knowledge_base,
    "get_daily_report": get_daily_report,
}


def ad_agent_chat(user_question: str, verbose: bool = True):
    """
    广告投放智能助手
    用户提问，Agent自己决定调用哪个工具
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"用户问题：{user_question}")
        print(f"{'='*60}")

    # 对话历史
    messages = [
        {
            "role": "system",
            "content": """你是一个专业的广告投放智能助手。
你可以调用工具来查询数据、检测异常、查询知识库、生成日报。
请根据用户的问题，选择合适的工具来回答。
如果需要多个工具，可以依次调用。
回答要简洁明了，重点突出。"""
        },
        {"role": "user", "content": user_question}
    ]

    # 循环调用，直到大模型直接回答
    max_rounds = 8
    for round_num in range(max_rounds):
        if verbose:
            print(f"\n--- 第{round_num+1}轮 ---")

        # 调用大模型
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=TOOLS
        )

        message = response.choices[0].message

        # 情况1：大模型要调用工具
        if message.tool_calls:
            # 先把大模型的工具调用请求加入对话历史（只加一次）
            messages.append(message)

            # 循环执行每个工具
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                if verbose:
                    print(f"  大模型决定调用：{tool_name}")
                    print(f"  参数：{tool_args}")

                # 执行工具
                if tool_name in TOOL_FUNCTIONS:
                    try:
                        result = TOOL_FUNCTIONS[tool_name](**tool_args)
                    except Exception as e:
                        result = f"工具执行出错：{str(e)}"
                else:
                    result = f"未知工具：{tool_name}"

                if verbose:
                    print(f"  工具返回：{result[:100]}..." if len(result) > 100 else f"  工具返回：{result}")

                # 把工具结果加入对话历史
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

        # 情况2：大模型直接回答
        else:
            if verbose:
                print(f"\n{'='*60}")
                print("Agent最终回答：")
                print("-" * 60)
                print(message.content)
                print("-" * 60)
            return message.content

    return "达到最大调用轮次，请简化问题"


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    # 测试1：查具体数据
    ad_agent_chat("今天Facebook的CPA多少？")

    # 测试2：查概念
    ad_agent_chat("什么是CPA？")

    # 测试3：异常检测
    ad_agent_chat("Facebook今天投放正常吗？有没有异常？")

    # 测试4：生成日报
    ad_agent_chat("给我一份今天的投放日报")
