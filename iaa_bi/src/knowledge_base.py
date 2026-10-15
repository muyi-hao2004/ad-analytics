"""
知识库（RAG）核心：把「优化师行业经验 / 方法论」这类非结构化文本
建成可检索的向量库，让 Agent 决策时能查参考。

为什么需要 RAG（而非数仓）：
  - 数仓存的是结构化精确数值（ROI、留存、花费），能写 SQL 精确查
  - 行业经验是「文本」，靠语义匹配检索，SQL 查不了 → 用 RAG

设计原则（可插拔）：
  - 嵌入层做成接口，默认用 chromadb 内置 MiniLM（英文模型，中文效果一般但能跑通）
  - 以后网络允许，可换成 bge-m3 等中文嵌入模型，检索质量提升，但调用方代码不用改
  - 分块、检索、top_k 都参数化

分层：
  1. _split_documents()  —— 把 markdown 按标题/段落切成小块（chunk）
  2. build_knowledge()    —— 读 docs 目录 → 切块 → 嵌入 → 写进 ChromaDB（离线建库）
  3. search_knowledge()   —— 检索 top_k 条最相关的经验（在线查询）
"""

from typing import List, Dict, Any
from pathlib import Path

import config

# 知识库文档目录（markdown 文件，可随时扩充）
KNOWLEDGE_DIR = config.PROJECT_ROOT / "data" / "knowledge"
# ChromaDB 落盘目录（持久化，不用每次重建）
CHROMA_DIR = config.PROJECT_ROOT / "data" / "chroma_kb"


def _split_markdown(text: str, max_chunk: int = 400) -> List[str]:
    """
    把一篇 markdown 切成小块（chunk）。

    策略：按「标题 + 段落」切。每个二级标题（##）作为一块的起点，
    内容太长再按句号进一步切。这样每块都自带上下文（标题），检索更准。

    Args:
        text: 原始 markdown 全文
        max_chunk: 每块最大字符数

    Returns:
        chunk 列表
    """
    lines = text.split("\n")
    chunks: List[str] = []
    current_title = ""
    current_body: List[str] = []

    def flush():
        """把当前积累的标题+正文打包成一块。"""
        nonlocal current_title, current_body
        if current_body:
            body = " ".join(current_body).strip()
            if body:
                chunk = f"{current_title}\n{body}".strip()
                # 太长再按句号切
                if len(chunk) > max_chunk:
                    for sub in _split_long(chunk, max_chunk):
                        chunks.append(sub)
                else:
                    chunks.append(chunk)
        current_body = []

    for line in lines:
        line = line.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            # 一级标题：作为新章节，flush 上一块
            flush()
            current_title = line.strip("# ").strip()
        elif line.startswith("## "):
            # 二级标题：作为新块起点
            flush()
            current_title = line.strip("# ").strip()
        else:
            current_body.append(line)

    flush()
    return chunks


def _split_long(text: str, max_chunk: int) -> List[str]:
    """把超长文本按句号切分成多块。"""
    parts = text.split("。")
    result: List[str] = []
    buf = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        candidate = (buf + "。" + p).strip("。") if buf else p
        if len(candidate) > max_chunk and buf:
            result.append(buf)
            buf = p
        else:
            buf = candidate
    if buf:
        result.append(buf)
    return result


def _default_embedding_fn():
    """
    默认嵌入函数：用 chromadb 内置的 sentence-transformers 模型。

    注意：默认 MiniLM 是英文模型，中文语义效果一般。这是「能跑通」的兜底，
    后续可替换为中文模型（如 bge-m3）而不改调用方代码。
    """
    import chromadb.utils.embedding_functions as ef
    return ef.DefaultEmbeddingFunction()


def build_knowledge(embedding_fn=None, collection_name: str = "iaa_optimization") -> Dict[str, Any]:
    """
    离线建库：读 data/knowledge/*.md → 切块 → 嵌入 → 写入 ChromaDB。

    Args:
        embedding_fn: 嵌入函数，None 则用默认（可插拔）
        collection_name: ChromaDB collection 名

    Returns:
        建库结果统计（文档数、块数）
    """
    import chromadb

    if embedding_fn is None:
        embedding_fn = _default_embedding_fn()

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # 重建：先删旧的 collection，保证幂等（重复建库不会累积重复数据）
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
    )

    docs: List[str] = []
    ids: List[str] = []
    metadatas: List[Dict[str, str]] = []

    md_files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    for f in md_files:
        text = f.read_text(encoding="utf-8")
        chunks = _split_markdown(text)
        for i, chunk in enumerate(chunks):
            docs.append(chunk)
            ids.append(f"{f.stem}_{i}")
            metadatas.append({"source": f.name})

    if docs:
        collection.add(documents=docs, ids=ids, metadatas=metadatas)

    return {
        "files": len(md_files),
        "chunks": len(docs),
        "collection": collection_name,
    }


def search_knowledge(query: str, top_k: int = 3,
                     collection_name: str = "iaa_optimization") -> List[Dict[str, Any]]:
    """
    在线检索：根据 query 返回 top_k 条最相关的经验。

    Args:
        query: 查询文本（如「D7 ROAS 低于 70% 该怎么优化」）
        top_k: 返回条数
        collection_name: collection 名

    Returns:
        [{text, source, distance}, ...] 按相关度排序
    """
    import chromadb

    if not CHROMA_DIR.exists():
        return []  # 库还没建，返回空（调用方要能容忍）

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        return []

    results = collection.query(query_texts=[query], n_results=top_k)

    out: List[Dict[str, Any]] = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for i, doc in enumerate(docs):
        out.append({
            "text": doc,
            "source": metas[i].get("source", "") if i < len(metas) else "",
            "distance": round(distances[i], 4) if i < len(distances) else None,
        })
    return out


if __name__ == "__main__":
    # 自检：建库 + 检索一条
    import sys
    sys.path.insert(0, str(Path(__file__).parent))

    print("开始建库...")
    stats = build_knowledge()
    print(f"建库完成: {stats}")

    print("\n检索测试: 'D7 ROAS 低于 70% 怎么优化'")
    results = search_knowledge("D7 ROAS 低于 70% 怎么优化", top_k=3)
    for r in results:
        print(f"  [{r['source']}] (距离 {r['distance']})")
        print(f"    {r['text'][:80]}...")
