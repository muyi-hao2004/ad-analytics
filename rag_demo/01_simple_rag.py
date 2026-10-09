"""
第一个RAG Demo
用ChromaDB + 本地Embedding模型，实现一个简单的问答系统
"""

import chromadb
from chromadb.utils import embedding_functions

# ============================================================
# 第1步：准备知识库文档
# 这里我们手动写一些广告投放相关的知识
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

# 给每篇文档一个ID和来源标签
ids = [f"doc_{i}" for i in range(len(documents))]
metadatas = [{"source": f"广告知识_{i+1}"} for i in range(len(documents))]


# ============================================================
# 第2步：初始化ChromaDB和Embedding模型
# ============================================================
print("正在加载Embedding模型...（第一次运行会下载模型，需要等一会儿）")

# 使用ChromaDB内置的默认Embedding模型（all-MiniLM-L6-v2）
# 这个模型很小，本地就能跑，效果也不错
embedding_function = embedding_functions.DefaultEmbeddingFunction()

# 创建ChromaDB客户端（存在本地文件夹里）
client = chromadb.PersistentClient(path="./chroma_db")

# 创建一个collection（类似数据库里的表）
collection = client.get_or_create_collection(
    name="ad_knowledge_base",
    embedding_function=embedding_function
)

print("模型加载完成！")


# ============================================================
# 第3步：把文档存入向量数据库
# ============================================================
print("正在把文档存入向量数据库...")

collection.add(
    documents=documents,
    ids=ids,
    metadatas=metadatas
)

print(f"存入了 {collection.count()} 篇文档到知识库")


# ============================================================
# 第4步：写一个检索函数
# ============================================================
def search_knowledge(query, n_results=3):
    """
    从知识库中检索和问题最相关的n篇文档
    """
    results = collection.query(
        query_texts=[query],  # 用户的问题
        n_results=n_results   # 返回前3个最相关的
    )
    return results


# ============================================================
# 第5步：写一个简单的问答函数（模拟RAG）
# ============================================================
def answer_question(question):
    """
    完整的RAG流程：
    1. 检索相关文档
    2. 把文档和问题拼在一起
    3. （这里我们模拟大模型生成回答）
    """
    print(f"\n===== 问题：{question} =====")

    # 第1步：检索相关文档
    print("\n【第1步】检索相关文档...")
    results = search_knowledge(question, n_results=3)

    for i, (doc, metadata, distance) in enumerate(zip(
        results['documents'][0],
        results['metadatas'][0],
        results['distances'][0]
    )):
        print(f"\n  第{i+1}篇（相似度距离：{distance:.4f}）：")
        print(f"    来源：{metadata['source']}")
        print(f"    内容：{doc}")

    # 第2步：拼接Prompt（这里模拟大模型）
    print("\n【第2步】拼接Prompt，发给大模型...")

    context = "\n".join([f"- {doc}" for doc in results['documents'][0]])
    prompt = f"""
    你是一个广告投放分析师助手。请根据下面提供的参考资料，回答用户的问题。

    参考资料：
    {context}

    用户问题：{question}
    """

    print(f"  发给大模型的Prompt长度：{len(prompt)} 字符")

    # 第3步：模拟大模型回答（这里我们直接返回检索到的文档，
    # 实际项目中这里会调用OpenAI/豆包的API生成自然语言回答）
    print("\n【第3步】大模型生成回答：")
    print("  （实际项目中这里会调用大模型API，现在我们先打印检索到的内容）")

    return results


# ============================================================
# 第6步：测试几个问题
# ============================================================
if __name__ == "__main__":
    # 测试问题1：什么是CPA？
    answer_question("什么是CPA？")

    # 测试问题2：为什么Facebook的成本高了？
    answer_question("Facebook的CPA突然上涨，可能是什么原因？")

    # 测试问题3：怎么判断广告效果好不好？
    answer_question("怎么判断广告效果好不好？")

    print("\n===== RAG Demo 运行完成 =====")
