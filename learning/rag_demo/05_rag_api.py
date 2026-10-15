"""
RAG API服务
把RAG包装成HTTP接口，别的程序可以通过网络调用
"""

import os
from fastapi import FastAPI
from pydantic import BaseModel
import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 初始化FastAPI
# ============================================================
app = FastAPI(title="广告知识库RAG API")

# 初始化大模型客户端
llm_client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# 初始化知识库
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

embedding_function = embedding_functions.DefaultEmbeddingFunction()
chroma_client = chromadb.PersistentClient(path="./chroma_db")
collection = chroma_client.get_or_create_collection(
    name="ad_knowledge_base_v2",
    embedding_function=embedding_function
)

if collection.count() == 0:
    collection.add(
        documents=documents,
        ids=[f"doc_{i}" for i in range(len(documents))],
        metadatas=[{"source": t} for t in doc_titles]
    )


# ============================================================
# 定义请求和响应的数据格式
# ============================================================
class AskRequest(BaseModel):
    question: str  # 用户的问题


class AskResponse(BaseModel):
    question: str           # 用户的问题
    answer: str             # 大模型生成的回答
    sources: list[str]      # 引用了哪些文档
    chunks_used: int        # 用了几个文档块


# ============================================================
# 核心接口：问问题
# ============================================================
@app.post("/ask", response_model=AskResponse)
def ask_rag(req: AskRequest):
    # 第1步：检索相关文档
    docs = collection.query(query_texts=[req.question], n_results=3)

    context = ""
    sources = []
    for i, (doc, meta) in enumerate(zip(docs['documents'][0], docs['metadatas'][0])):
        context += f"\n【文档{i+1}：{meta['source']}】\n{doc}\n"
        sources.append(meta['source'])

    # 第2步：拼接Prompt
    prompt = f"""
你是一个专业的广告投放分析师助手。请根据下面提供的参考资料，回答用户的问题。
只能基于参考资料回答，不要编造。每句话后面标注来源。

参考资料：
{context}

用户问题：{req.question}
"""

    # 第3步：调用大模型
    response = llm_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}]
    )

    answer = response.choices[0].message.content

    # 返回结果
    return AskResponse(
        question=req.question,
        answer=answer,
        sources=sources,
        chunks_used=len(sources)
    )


# ============================================================
# 健康检查接口
# ============================================================
@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "knowledge_base_size": collection.count()
    }


# 启动命令：uvicorn 05_rag_api:app --reload --port 8002
