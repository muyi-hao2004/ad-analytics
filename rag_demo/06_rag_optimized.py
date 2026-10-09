"""
优化版RAG - 文档切分优化
改进点：
1. 按句子切分，不是硬切字数
2. 相邻块有重叠，避免关键信息在边界被切开
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


# ============================================================
# 第1步：更长的知识库文档（模拟真实情况）
# ============================================================
long_document = """
什么是CPA？CPA全称是单次转化成本，计算公式是总花费除以总转化数。CPA越高，说明获客成本越高，投放效率越低。CPA是衡量广告投放效果最重要的指标之一。

什么是CTR？CTR全称是点击率，计算公式是总点击除以总曝光，再乘以100%。CTR越高，说明广告素材越吸引人，用户越愿意点进来。CTR低的话，通常是素材不行或者定向不准。

什么是CPC？CPC全称是单次点击成本，计算公式是总花费除以总点击。CPC越高，说明竞价越激烈，流量越贵。CPC和行业有关，电商行业的CPC通常比游戏行业高。

Facebook广告的特点是什么？Facebook广告的优势是用户量大，定向精准，可以按年龄、性别、兴趣、行为定向。适合做品牌曝光和受众拓展。但是Facebook的CPA相对较高，因为竞争激烈。

Google搜索广告的特点是什么？Google搜索广告的优势是用户搜索意图明确，转化率相对较高。因为用户是主动搜索的，所以更精准。适合做精准获客和效果广告。

TikTok广告的特点是什么？TikTok广告适合做年轻用户群体的品牌种草。TikTok的用户偏年轻，视频素材的创意很重要。CTR通常比较高，但是转化率相对较低，因为用户是来刷视频的，不是来买东西的。

什么是素材疲劳？素材疲劳是指广告素材连续投放太久，CTR会明显下降的现象。一般来说，素材投放超过7天，CTR就会开始下降。解决素材疲劳的方法是及时更换新素材，或者迭代素材的创意。

什么是ROI？ROI全称是投资回报率，计算公式是（收入减去成本）除以成本。ROI大于1才说明赚钱，ROI小于1就是亏钱。ROI越高，说明投放效率越好。

什么是归因窗口？归因窗口是指用户点击广告后，多久内的转化会被记到这个广告上。常见的归因窗口有7天点击、1天浏览。归因窗口越长，转化数越多，但是CPA会越低。

CPA异常怎么判断？如果CPA突然上涨超过过去7天平均值的50%，就属于异常情况。常见的原因有：竞争加剧导致CPC上升、素材疲劳导致CTR下降、落地页问题导致转化率下降、定向人群过窄。
"""


# ============================================================
# 第2步：优化的文档切分函数
# ============================================================
def split_text(text, chunk_size=200, overlap=50):
    """
    优化的文本切分：
    1. 先按句号切分成句子
    2. 然后把句子拼成块，每块大概chunk_size字
    3. 相邻块有overlap字的重叠
    """
    # 第1步：按句号切分成句子
    sentences = [s.strip() for s in text.split("。") if s.strip()]
    # 把句号加回去
    sentences = [s + "。" for s in sentences]

    # 第2步：把句子拼成块
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        # 如果当前块加上这个句子，超过chunk_size了
        if len(current_chunk) + len(sentence) > chunk_size:
            # 把当前块存起来
            if current_chunk:
                chunks.append(current_chunk.strip())
            # 下一个块，从上一个块的结尾开始（重叠）
            # 取当前块的最后overlap个字，作为下一个块的开头
            overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
            current_chunk = overlap_text + sentence
        else:
            current_chunk += sentence

    # 别忘了最后一块
    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


# ============================================================
# 第3步：切分文档，存入向量数据库
# ============================================================
print("正在切分文档...")
chunks = split_text(long_document, chunk_size=200, overlap=50)
print(f"切分成 {len(chunks)} 个块")
for i, chunk in enumerate(chunks):
    print(f"  块{i+1}（{len(chunk)}字）：{chunk[:60]}...")

# 初始化ChromaDB
print("\n正在加载Embedding模型...")
embedding_function = embedding_functions.DefaultEmbeddingFunction()

chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_or_create_collection(
    name="ad_knowledge_base_optimized",  # 新的collection，不覆盖原来的
    embedding_function=embedding_function
)

# 如果是空的，就存进去
if collection.count() == 0:
    print("正在把文档存入向量数据库...")
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    metadatas = [{"source": f"广告知识_{i+1}"} for i in range(len(chunks))]
    collection.add(documents=chunks, ids=ids, metadatas=metadatas)

print(f"知识库已有 {collection.count()} 篇文档")


# ============================================================
# 第4步：检索 + 生成
# ============================================================
def search_relevant_docs(question, n_results=3):
    results = collection.query(
        query_texts=[question],
        n_results=n_results
    )
    return results


def generate_answer(question, relevant_docs):
    context = "\n".join([
        f"- {doc}（来源：{meta['source']}）"
        for doc, meta in zip(relevant_docs['documents'][0], relevant_docs['metadatas'][0])
    ])

    prompt = f"""
你是一个专业的广告投放分析师助手。请根据下面提供的参考资料，回答用户的问题。

要求：
1. 只能基于参考资料回答，不要编造
2. 如果参考资料里没有相关信息，就说"根据现有资料，我无法回答这个问题"
3. 回答要简洁明了，分点说明

参考资料：
{context}

用户问题：{question}
"""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是一个专业的广告投放分析师助手。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )

    return response.choices[0].message.content


def rag_answer(question):
    print(f"\n{'='*50}")
    print(f"问题：{question}")
    print(f"{'='*50}")

    print("\n【1/3】检索相关文档...")
    docs = search_relevant_docs(question, n_results=3)
    for i, (doc, distance) in enumerate(zip(docs['documents'][0], docs['distances'][0])):
        print(f"  第{i+1}篇（相似度：{1-distance:.4f}）：{doc[:50]}...")

    print("\n【2/3】调用大模型生成回答...")
    answer = generate_answer(question, docs)

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
