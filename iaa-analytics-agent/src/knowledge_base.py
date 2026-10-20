"""
知识库（RAG）核心：把「优化师行业经验 / 方法论」这类非结构化文本
建成可检索的向量库，让 Agent 决策时能查参考。

为什么需要 RAG（而非数仓）：
  - 数仓存的是结构化精确数值（ROI、留存、花费），能写 SQL 精确查
  - 行业经验是「文本」，靠语义匹配检索，SQL 查不了 → 用 RAG

设计原则（可插拔）：
  - 嵌入层做成接口，默认用 bge-m3（中文检索最强之一），失败回退 MiniLM 兜底
  - bge-m3 走 hf-mirror 镜像下载（国内直连 HF 不通时），约 2.27GB
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
                    # 注意：这里**不**给切分后的子块补标题。
                    # 曾经试过「每个子块都补上所属小节标题」，直觉上能提升子块的自解释性，
                    # 但实测是负收益（eval/run_retrieval_eval.py）：
                    #   A 不补标题  69 块  hit@1 85.0%  MRR 0.875
                    #   C 补标题    67 块  hit@1 80.0%  MRR 0.850
                    # 原因：同一篇文档的所有子块共享同一标题前缀后，块间嵌入高度相似，
                    # 检索时同一文档的多个块互相霸榜，把别的文档的正确块挤出 top-k。
                    # （典型表现：问「次留比转化率还高正常吗」，top3 全是 04 篇的块）
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


def _split_long(text: str, max_chunk: int, min_chunk: int = 40) -> List[str]:
    """把超长文本按句号切分成多块。

    Args:
        text: 待切分文本
        max_chunk: 每块最大字符数
        min_chunk: 末块最小字符数。低于此值则并入前一块。

    Returns:
        切分后的块列表

    为什么需要 min_chunk（实测出来的问题）
    ------------------------------------
    原实现在最后无条件 `result.append(buf)`，导致尾部常留下 60~80 字的碎块。
    这些碎块有三个坏处：
      1. 语义不完整，检索几乎召不回（「判断方法：任何变现改动都要同时看…」这种）
      2. 没有标题前缀，模型/嵌入都不知道它在讲什么
      3. 反而容易因为短促而误匹配，污染 top-k

    评测脚本 eval/run_retrieval_eval.py 第一次跑时，20 条用例里
    R06（「加了插屏收入涨了但整体更亏」）就是因此漏召回：
    答案所在的那句正好被切成了一个 62 字的孤立块。
    """
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
        # 末块太短就并入前一块，避免留下无上下文的碎片
        if result and len(buf) < min_chunk:
            result[-1] = f"{result[-1]}。{buf}"
        else:
            result.append(buf)
    return result


def bge_m3_embedding_fn():
    """
    bge-m3 中文嵌入函数（推荐，默认）。

    bge-m3（BAAI）是当前最强的中文检索嵌入模型之一，多语言、支持稠密+稀疏+多向量，
    中文语义效果远好于 MiniLM。通过 sentence_transformers 加载。

    加载策略（优先本地，避免每次走网络）：
    1. 若本地已下载（iaa-analytics-agent/models/bge-m3/），直接用本地路径
    2. 否则走 hf-mirror 镜像在线下载（约 2.27GB，国内直连 HF 不通）

    下载方式（任选其一）：
        HF_ENDPOINT=https://hf-mirror.com python -c \\
          "from huggingface_hub import snapshot_download; \\
           snapshot_download('BAAI/bge-m3', local_dir='iaa-analytics-agent/models/bge-m3')"
    """
    import os
    from pathlib import Path

    # 本地模型目录（若有则直接用，最稳）
    local_dir = Path(__file__).parent.parent / "models" / "bge-m3"
    if (local_dir / "config.json").exists():
        model_name = str(local_dir)
    else:
        # 国内直连 HF 不通时走镜像；已能直连则此设置无害
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        model_name = "BAAI/bge-m3"

    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    return SentenceTransformerEmbeddingFunction(
        model_name=model_name,
        normalize_embeddings=True,
    )


def _default_embedding_fn():
    """
    默认嵌入函数：优先 bge-m3，失败则回退到 chromadb 内置 MiniLM（英文兜底）。

    调用方无需关心用哪个，保证「能跑通」。
    """
    try:
        return bge_m3_embedding_fn()
    except Exception as e:
        print(f"[knowledge_base] bge-m3 加载失败，回退 MiniLM 兜底：{e}")
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
                     collection_name: str = "iaa_optimization",
                     hybrid: bool = True) -> List[Dict[str, Any]]:
    """
    在线检索：根据 query 返回 top_k 条最相关的经验。

    Args:
        query: 查询文本（如「D7 ROAS 低于 70% 该怎么优化」）
        top_k: 返回条数
        collection_name: collection 名
        hybrid: 是否启用混合检索（向量 + 关键词融合）。默认开。

    Returns:
        [{text, source, distance, match}, ...] 按相关度排序。
        match 说明这条是靠向量、关键词、还是两者共同命中的（便于排查）。

    为什么要 hybrid（这是评测逼出来的改动）
    ------------------------------------
    纯向量检索在两类 query 上会漏：

      1. **口语化提问 vs 书面化原文**
         query「为什么加了插屏广告收入涨了但整体更亏」
         原文「提高插屏频率让 ARPDAU 涨 10%，同时 D7 留存掉几个点…负收益」
         语义是对的，但「更亏」和「负收益」在嵌入空间里不够近，排不进 top3。

      2. **短术语被长主题淹没**
         query「次留比注册转化率还高正常吗」
         答案在《渠道质量》篇，但《留存基准》篇整篇都在讲留存，
         向量分更高，把答案挤出去了。

    这两类的共同点是「字面有强信号但语义被稀释」，正是关键词检索擅长的。
    反过来，关键词检索不擅长同义改写（「买他的钱」vs「CPI」），那是向量的强项。
    两者互补，用 RRF 融合——不需要调权重，只看排名。

    为什么用 TF-IDF 而不是 BM25
    -----------------------
    BM25 需要中文分词（jieba），多一个依赖；TF-IDF 用字符 n-gram 就能工作，
    对中文尤其合适（「注册转化率」会被拆成「注册」「转化」「化率」等片段，
    不需要知道词边界）。对 60 多个块的规模，两者的排序差异可以忽略。
    """
    hits_all = max(top_k * 3, top_k)

    # ---- 第 1 路：向量检索（语义） ----
    vec_hits = _vector_search(query, hits_all, collection_name)
    if not vec_hits:
        return []

    if not hybrid:
        return [{**h, "match": "vector"} for h in vec_hits[:top_k]]

    # ---- 第 2 路：关键词检索（字面） ----
    kw_hits = _keyword_search(query, hits_all, collection_name)
    if not kw_hits:
        # sklearn 缺失或索引不可用时，优雅退化为纯向量，不抛异常
        return [{**h, "match": "vector"} for h in vec_hits[:top_k]]

    # ---- 融合 ----
    return _rrf_fuse(vec_hits, kw_hits, top_k=top_k)


# ============================================================
# 混合检索的实现细节
# ============================================================

# 关键词索引缓存：collection_name -> (vectorizer, matrix, docs, metas)
# 为什么要缓存：TF-IDF 拟合是 O(块数 × 特征数)，每次检索重算会让 Agent 的工具调用变慢。
# 知识库只在重建时变化，所以缓存到进程退出是安全的。
_KW_INDEX: Dict[str, Any] = {}

# RRF 的平滑常数。60 是原论文（Cormack et al., 2009）的推荐值：
# 它决定了「排名差异」的影响强度，越大越平缓。这个值不需要按场景调。
_RRF_K = 60


# ============================================================
# ChromaDB 连接缓存（性能关键，实测出来的）
# ============================================================
#
# 原来的写法是每次检索都 `chromadb.PersistentClient(...)` + `get_collection(...)`。
# 单个调用看不出问题（一次几百毫秒），但 ChromaDB 在 get_collection 时会为
# collection 恢复它的 embedding function —— bge-m3 是 2.2GB，恢复过程要重新加载模型。
#
# 实测：eval/run_retrieval_eval.py 用逐条调用的方式跑 20 条 query，
# 400 秒还没跑完；改成进程内缓存后秒级完成。
# 对 Agent 更严重：它一次分析会连续调用多次 search_knowledge，
# 每次都重新加载模型意味着一次分析要几十秒纯等待。
#
# 缓存到进程退出是安全的：知识库只在 build_knowledge 时变化，
# 重建后重启进程即可（评测脚本和 Agent 都是短生命周期进程）。
_CLIENT: Any = None
_COLLECTIONS: Dict[str, Any] = {}


def _get_collection(collection_name: str):
    """取（并缓存）collection 对象。

    Args:
        collection_name: collection 名

    Returns:
        chromadb Collection 对象，不可用时返回 None
    """
    global _CLIENT

    if collection_name in _COLLECTIONS:
        return _COLLECTIONS[collection_name]

    import chromadb

    if not CHROMA_DIR.exists():
        return None

    if _CLIENT is None:
        _CLIENT = chromadb.PersistentClient(path=str(CHROMA_DIR))

    try:
        collection = _CLIENT.get_collection(name=collection_name)
    except Exception:
        return None

    _COLLECTIONS[collection_name] = collection
    return collection


def _vector_search(query: str, n: int,
                   collection_name: str) -> List[Dict[str, Any]]:
    """向量检索单路。返回 [{text, source, distance}, ...]。"""
    collection = _get_collection(collection_name)
    if collection is None:
        return []

    results = collection.query(query_texts=[query], n_results=n)

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


def _build_kw_index(collection_name: str):
    """构建（或取缓存）关键词索引。

    Returns:
        (vectorizer, matrix, docs, metas)；不可用时返回 None
    """
    if collection_name in _KW_INDEX:
        return _KW_INDEX[collection_name]

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        # 没有 sklearn 就放弃关键词路，纯向量仍然可用
        _KW_INDEX[collection_name] = None
        return None

    collection = _get_collection(collection_name)
    if collection is None:
        return None

    data = collection.get()
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    if not docs:
        return None

    # char n-gram：中文不需要分词。1-3 元覆盖单字、词片段、短语
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3),
                                 sublinear_tf=True, min_df=1)
    matrix = vectorizer.fit_transform(docs)

    _KW_INDEX[collection_name] = (vectorizer, matrix, docs, metas)
    return _KW_INDEX[collection_name]


def _keyword_search(query: str, n: int,
                    collection_name: str) -> List[Dict[str, Any]]:
    """关键词检索单路。用 TF-IDF 余弦相似度，返回与 _vector_search 同构的结果。"""
    index = _build_kw_index(collection_name)
    if index is None:
        return []

    vectorizer, matrix, docs, metas = index
    q = vectorizer.transform([query])
    # TF-IDF 默认 L2 归一化，点积即余弦相似度
    scores = (matrix @ q.T).toarray().ravel()

    order = scores.argsort()[::-1][:n]
    out: List[Dict[str, Any]] = []
    for i in order:
        if scores[i] <= 0:
            continue                      # 完全不沾边的不要
        out.append({
            "text": docs[i],
            "source": (metas[i].get("source", "") if i < len(metas) else ""),
            "distance": None,             # 关键词路没有距离概念，用 score 更直观
            "kw_score": round(float(scores[i]), 4),
        })
    return out


def _rrf_fuse(vec_hits: List[Dict[str, Any]],
              kw_hits: List[Dict[str, Any]],
              top_k: int) -> List[Dict[str, Any]]:
    """Reciprocal Rank Fusion：按排名融合两路结果。

    公式：score(d) = Σ_i 1 / (k + rank_i(d))

    为什么用 RRF 而不是加权分数
    --------------------------
    向量的 distance 和 TF-IDF 的 score 量纲完全不同（一个是 cosine 距离，
    一个是归一化的 tf-idf 点积），直接加权需要先归一化，而归一化方式又会影响结果。
    RRF 只用排名，天然免疫量纲问题，也不需要为不同知识库调权重。

    Args:
        vec_hits: 向量检索结果（已按相关度排序）
        kw_hits: 关键词检索结果（已按相关度排序）
        top_k: 返回条数

    Returns:
        融合后的结果，带 match 字段标明命中来源
    """
    scores: Dict[str, float] = {}
    store: Dict[str, Dict[str, Any]] = {}
    origin: Dict[str, set] = {}

    for tag, hits in (("vector", vec_hits), ("keyword", kw_hits)):
        for rank, h in enumerate(hits, start=1):
            key = h.get("text", "")[:120]      # 用文本前缀做唯一键（chunk 内容唯一）
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            origin.setdefault(key, set()).add(tag)
            # 保留向量路的 distance（有就带上，方便排查）；文本与来源以先到的为准
            if key not in store or store[key].get("distance") is None:
                store[key] = h

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

    out: List[Dict[str, Any]] = []
    for key, s in ranked:
        h = dict(store[key])
        tags = origin[key]
        h["match"] = "both" if len(tags) == 2 else next(iter(tags))
        h["rrf_score"] = round(s, 5)
        out.append(h)
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
