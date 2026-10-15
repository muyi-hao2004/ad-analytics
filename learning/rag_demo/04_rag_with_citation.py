"""
RAG优化版2：加引用来源
回答的时候标注每句话来自哪篇文档
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

# 给每篇文档起个更有意义的名字
doc_titles = [
    "指标定义-CPA",
    "指标定义-CTR",
    "指标定义-CPC",
    "渠道特点-Facebook",
    "渠道特点-Google",
    "渠道特点-TikTok",
    "异常判断标准",
    "素材优化建议",
    "指标定义-ROI",
    "术语解释-归因窗口",
]

metadatas = [{"source": title} for title in doc_titles]

embedding_function = embedding_functions.DefaultEmbeddingFunction()
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_or_create_collection(
    name="ad_knowledge_base_v2",  # 换个collection名字，存新的
    embedding_function=embedding_function
)

if collection.count() == 0:
    collection.add(
        documents=documents,
        ids=[f"doc_{i}" for i in range(len(documents))],
        metadatas=metadatas
    )


# ============================================================
# 带引用来源的RAG
# ============================================================
def rag_answer_with_citation(question):
    print(f"\n{'='*50}")
    print(f"问题：{question}")
    print(f"{'='*50}")

    # 第1步：检索相关文档
    print("\n【1/2】检索相关文档...")
    docs = collection.query(query_texts=[question], n_results=3)

    # 把文档和来源拼成一段
    context = ""
    for i, (doc, meta) in enumerate(zip(docs['documents'][0], docs['metadatas'][0])):
        context += f"\n【文档{i+1}：{meta['source']}】\n{doc}\n"
        print(f"  文档{i+1}：{meta['source']}")

    # 第2步：拼接Prompt，要求大模型标注来源
    prompt = f"""
你是一个专业的广告投放分析师助手。请根据下面提供的参考资料，回答用户的问题。

要求：
1. 只能基于参考资料回答，不要编造
2. 如果参考资料里没有相关信息，就说"根据现有资料，我无法回答这个问题"
3. 回答要简洁明了，分点说明
4. 每一句话后面都要标注来源，格式是 [来源：xxx]

参考资料：
{context}

用户问题：{question}
"""

    # 第3步：调用大模型（流式输出）
    print("\n【2/2】大模型回答：")
    print("-" * 50)

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        stream=True
    )

    for chunk in response:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)

    print("\n" + "-" * 50)


if __name__ == "__main__":
    rag_answer_with_citation("什么是CPA？")
    rag_answer_with_citation("Facebook广告有什么特点？")
    rag_answer_with_citation("广告素材多久要换？")
