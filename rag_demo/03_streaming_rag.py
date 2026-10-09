"""
RAG优化版：流式输出
大模型一个字一个字地蹦出来，不用等全部生成完
"""

import os
import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# 初始化知识库（和之前一样）
documents = [
    "CPA（单次转化成本）= 总花费 / 总转化数。CPA越高，说明获客成本越高。",
    "CTR（点击率）= 总点击 / 总曝光。CTR越高，说明广告素材越吸引人。",
    "CPC（单次点击成本）= 总花费 / 总点击。CPC越高，说明竞价越激烈。",
    "Facebook广告的优势是用户量大，定向精准，适合做品牌曝光和受众拓展。",
    "Google搜索广告的优势是用户搜索意图明确，转化率相对较高，适合精准获客。",
    "TikTok广告适合做年轻用户群体的品牌种草，视频素材的创意很重要。",
    "如果CPA突然上涨超过历史平均值的50%，就属于异常，需要检查投放设置。",
    "广告素材连续投放超过7天，CTR会明显下降，这就是素材疲劳，需要及时更换。",
    "ROI（投资回报率）=（收入 - 成本）/ 成本。ROI大于1才说明赚钱。",
    "归因窗口是指用户点击广告后，多久内的转化会被记到这个广告上。常见的有7天点击、1天浏览。",
]

embedding_function = embedding_functions.DefaultEmbeddingFunction()
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_or_create_collection(
    name="ad_knowledge_base",
    embedding_function=embedding_function
)

if collection.count() == 0:
    collection.add(documents=documents, ids=[f"doc_{i}" for i in range(len(documents))])


# ============================================================
# 流式输出版本的回答函数
# ============================================================
def rag_answer_stream(question):
    print(f"\n{'='*50}")
    print(f"问题：{question}")
    print(f"{'='*50}")

    # 第1步：检索相关文档（和之前一样）
    print("\n【1/2】检索相关文档...")
    docs = collection.query(query_texts=[question], n_results=3)
    for i, (doc, distance) in enumerate(zip(docs['documents'][0], docs['distances'][0])):
        print(f"  第{i+1}篇（相似度：{1-distance:.4f}）：{doc[:40]}...")

    # 第2步：拼接Prompt
    context = "\n".join([f"- {doc}" for doc in docs['documents'][0]])
    prompt = f"""
你是一个专业的广告投放分析师助手。请根据下面的参考资料回答问题。
只能基于参考资料回答，不要编造。

参考资料：
{context}

用户问题：{question}
"""

    # 第3步：调用大模型，stream=True 开启流式输出
    print("\n【2/2】大模型正在生成回答：")
    print("-" * 50)

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        stream=True  # ！！！关键：开启流式输出
    )

    # 一个字一个字地打印出来
    for chunk in response:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)

    print("\n" + "-" * 50)


if __name__ == "__main__":
    rag_answer_stream("什么是CPA？")
    rag_answer_stream("广告素材投放多久需要更换？")
