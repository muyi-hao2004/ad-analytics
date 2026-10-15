"""
完整的RAG Demo
检索 + 拼接Prompt + 调用DeepSeek大模型生成回答
"""

import os
import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI
from dotenv import load_dotenv

# ============================================================
# 第0步：加载环境变量（API Key不写死在代码里）
# ============================================================
load_dotenv()  # 从.env文件读取环境变量

# 初始化DeepSeek客户端（兼容OpenAI接口，只改base_url）
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"  # DeepSeek的API地址
)


# ============================================================
# 第1步：准备知识库文档
# ============================================================
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

ids = [f"doc_{i}" for i in range(len(documents))]
metadatas = [{"source": f"广告知识_{i+1}"} for i in range(len(documents))]


# ============================================================
# 第2步：初始化ChromaDB
# ============================================================
print("正在加载Embedding模型...")
embedding_function = embedding_functions.DefaultEmbeddingFunction()

chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_or_create_collection(
    name="ad_knowledge_base",
    embedding_function=embedding_function
)

# 如果是空的，就把文档存进去
if collection.count() == 0:
    print("正在把文档存入向量数据库...")
    collection.add(documents=documents, ids=ids, metadatas=metadatas)

print(f"知识库已有 {collection.count()} 篇文档")


# ============================================================
# 第3步：检索相关文档
# ============================================================
def search_relevant_docs(question, n_results=3):
    """从知识库检索和问题最相关的n篇文档"""
    results = collection.query(
        query_texts=[question],
        n_results=n_results
    )
    return results


# ============================================================
# 第4步：调用大模型生成回答
# ============================================================
def generate_answer(question, relevant_docs):
    """
    把检索到的文档和问题拼起来，发给大模型生成回答
    """
    # 把检索到的文档拼成一段文字
    context = "\n".join([
        f"- {doc}（来源：{meta['source']}）"
        for doc, meta in zip(relevant_docs['documents'][0], relevant_docs['metadatas'][0])
    ])

    # 拼接Prompt
    prompt = f"""
你是一个专业的广告投放分析师助手。请根据下面提供的参考资料，回答用户的问题。

要求：
1. 只能基于参考资料回答，不要编造
2. 如果参考资料里没有相关信息，就说"根据现有资料，我无法回答这个问题"
3. 回答要简洁明了，分点说明
4. 重要数据可以标注来源

参考资料：
{context}

用户问题：{question}
"""

    print(f"发给大模型的Prompt长度：{len(prompt)} 字符")

    # 调用DeepSeek API
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是一个专业的广告投放分析师助手。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7  # 0=严谨，1=有创意
    )

    return response.choices[0].message.content


# ============================================================
# 第5步：完整的RAG流程
# ============================================================
def rag_answer(question):
    """完整的RAG流程：检索 → 拼接 → 生成"""
    print(f"\n{'='*50}")
    print(f"问题：{question}")
    print(f"{'='*50}")

    # 第1步：检索相关文档
    print("\n【1/3】检索相关文档...")
    docs = search_relevant_docs(question, n_results=3)
    for i, (doc, distance) in enumerate(zip(docs['documents'][0], docs['distances'][0])):
        print(f"  第{i+1}篇（相似度：{1-distance:.4f}）：{doc[:50]}...")

    # 第2步：拼接Prompt，调用大模型
    print("\n【2/3】调用大模型生成回答...")
    answer = generate_answer(question, docs)

    # 第3步：返回回答
    print("\n【3/3】最终回答：")
    print("-" * 50)
    print(answer)
    print("-" * 50)

    return answer


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    # 测试几个问题
    rag_answer("什么是CPA？")
    rag_answer("Facebook的CPA突然上涨，可能是什么原因？")
    rag_answer("广告素材投放多久需要更换？")
