# -*- coding: utf-8 -*-
"""检索质量评测：recall@k / MRR / hit@1。

用法::

    python eval/run_retrieval_eval.py                 # 跑全部用例
    python eval/run_retrieval_eval.py -k 5            # 看 top5
    python eval/run_retrieval_eval.py -v              # 打印每条用例的 top3
    python eval/run_retrieval_eval.py --show-miss     # 只打印失败的

为什么要有这个脚本
----------------
RAG 的效果不能靠「翻两条结果看看」判断。这个脚本把它变成可复现的数字：

  hit@1     top1 就对 —— 用户（或 Agent）只看第一条时的准确率
  recall@k  top-k 里有 —— 上下文窗口塞 k 条时能否覆盖
  MRR       正确结果排名的倒数均值 —— 排名整体靠不靠前

不调用 LLM、不花钱、秒级完成，可以每次改知识库/改分块参数后重跑。

为什么单独写批量检索而不用 knowledge_base.search_knowledge
-------------------------------------------------------
`search_knowledge` 每次调用都会新建 PersistentClient + get_collection，
ChromaDB 恢复 collection 时要实例化 embedding function（bge-m3 约 2.2GB）。
单条调用 20 次 = 反复加载模型，要几分钟。
这里改成一次 client、一次 collection、一次批量 query_texts，
结果与生产路径一致（同一 collection、同一嵌入函数），只是少了重复初始化。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "src"))

import knowledge_base  # noqa: E402
import retrieval_cases  # noqa: E402

COLLECTION = "iaa_optimization"


def _search_batch(queries: List[str], top_k: int,
                  mode: str = "vector") -> List[List[Dict[str, Any]]]:
    """批量检索，返回每条 query 的 top_k 结果。

    Args:
        queries: 查询列表
        top_k: 每条返回几条
        mode: "vector" 只走向量；"hybrid" 走 knowledge_base.search_knowledge
              （向量 + 关键词 RRF 融合）

    Returns:
        与 queries 等长的列表，每项是 [{text, source, distance}, ...]

    Raises:
        SystemExit: 向量库不存在或 collection 缺失时退出（而不是返回空导致全 0 分）
    """
    if mode == "hybrid":
        # 直接走生产路径：评测的必须是 Agent 实际用的那条链路，
        # 否则「评测通过」不代表线上表现（这类错很难发现）。
        # 关键词索引在 knowledge_base 内部缓存，第一次之后很快。
        return [knowledge_base.search_knowledge(q, top_k=top_k) for q in queries]

    import chromadb

    if not knowledge_base.CHROMA_DIR.exists():
        print(f"向量库不存在：{knowledge_base.CHROMA_DIR}")
        print("先建库：python src/init_knowledge.py")
        raise SystemExit(1)

    client = chromadb.PersistentClient(path=str(knowledge_base.CHROMA_DIR))
    try:
        collection = client.get_collection(name=COLLECTION)
    except Exception as exc:
        print(f"collection '{COLLECTION}' 不可用：{exc}")
        print("先建库：python src/init_knowledge.py")
        raise SystemExit(1)

    results = collection.query(query_texts=queries, n_results=top_k)

    out: List[List[Dict[str, Any]]] = []
    docs_all = results.get("documents") or []
    metas_all = results.get("metadatas") or []
    dists_all = results.get("distances") or []

    for i in range(len(queries)):
        hits = []
        docs = docs_all[i] if i < len(docs_all) else []
        metas = metas_all[i] if i < len(metas_all) else []
        dists = dists_all[i] if i < len(dists_all) else []
        for j, doc in enumerate(docs):
            hits.append({
                "text": doc,
                "source": (metas[j].get("source", "") if j < len(metas) else ""),
                "distance": round(dists[j], 4) if j < len(dists) else None,
            })
        out.append(hits)
    return out


def _is_correct(case: retrieval_cases.RetrievalCase, hit: Dict[str, Any]) -> bool:
    """判断一条命中是否算「答对了」。

    两级判定（与 retrieval_cases 的注释对应）：
      1. 文档必须对（source 以 expected 开头）
      2. 若给了 must_contain，chunk 文本里至少要出现其中一个词
         —— 防止「命中了对的文档，但是里面无关的一段」

    must_contain 为空时只判文档，适用于「整篇都在讲这件事」的用例。
    """
    if not case.expected:
        return False
    if not hit.get("source", "").startswith(case.expected):
        return False
    if not case.must_contain:
        return True
    text = hit.get("text", "")
    return any(kw in text for kw in case.must_contain)


def evaluate(top_k: int, verbose: bool, show_miss: bool,
             mode: str = "vector") -> int:
    """跑全部用例并打印指标。

    Args:
        top_k: 检索深度
        verbose: 是否打印每条用例的 top3
        show_miss: 是否只打印失败用例
        mode: vector（纯向量）或 hybrid（向量+关键词）

    Returns:
        进程退出码（0 = 全部 hit@k 通过，1 = 有失败）
    """
    cases = retrieval_cases.build_cases()
    hits_all = _search_batch([c.query for c in cases], top_k, mode=mode)

    rows: List[Dict[str, Any]] = []
    for case, hits in zip(cases, hits_all):
        rank = None                      # 第一个正确结果的排名（1-based）
        matched_text = ""
        for idx, hit in enumerate(hits, start=1):
            if _is_correct(case, hit):
                rank = idx
                matched_text = hit.get("text", "")[:60].replace("\n", " ")
                break
        rows.append({
            "case": case,
            "hits": hits,
            "rank": rank,
            "hit1": rank == 1,
            "hitk": rank is not None,
            "rr": (1.0 / rank) if rank else 0.0,
            "matched": matched_text,
        })

    # ---------- 汇总 ----------
    n = len(rows)
    hit1 = sum(r["hit1"] for r in rows)
    hitk = sum(r["hitk"] for r in rows)
    mrr = sum(r["rr"] for r in rows) / n if n else 0.0

    print("=" * 74)
    print(f"检索评测  ·  {n} 条用例  ·  top_k={top_k}  ·  mode={mode}  ·  {COLLECTION}")
    print("=" * 74)
    print(f"  hit@1      {hit1}/{n}  ({hit1 / n:.1%})")
    print(f"  recall@{top_k}    {hitk}/{n}  ({hitk / n:.1%})")
    print(f"  MRR        {mrr:.3f}")
    print()

    # ---------- 分难度 ----------
    print("  按难度：")
    for diff in ("easy", "medium", "hard", "negative"):
        sub = [r for r in rows if r["case"].difficulty == diff]
        if not sub:
            continue
        k = sum(r["hitk"] for r in sub)
        m = sum(r["rr"] for r in sub) / len(sub)
        print(f"    {diff:<9} recall {k}/{len(sub)}   MRR {m:.3f}")
    print()

    # ---------- 明细 ----------
    print("-" * 74)
    for r in rows:
        case = r["case"]
        if show_miss and r["hitk"]:
            continue
        if r["hit1"]:
            mark = "OK "
        elif r["hitk"]:
            mark = f"@{r['rank']} "
        else:
            mark = "MISS"

        print(f"  [{mark:>4}] {case.id}  {case.query[:38]}")
        if verbose or not r["hitk"]:
            if r["hitk"]:
                print(f"         命中 #{r['rank']}: {r['matched']}")
            else:
                print(f"         期望: {case.expected}  关键词: {case.must_contain}")
                for i, h in enumerate(r["hits"][:3], start=1):
                    src = h["source"][:26]
                    txt = h["text"][:48].replace("\n", " ")
                    print(f"         实际#{i} [{src}] {txt}")

    print("-" * 74)
    miss = [r for r in rows if not r["hitk"]]
    if miss:
        print(f"\n未命中 {len(miss)} 条：{', '.join(r['case'].id for r in miss)}")
        print("排查方向：① 该知识点是否真的写进了知识库（先确认文档里有）")
        print("          ② 分块是否被切碎导致语义不完整（看失败用例的 chunk 长度）")
        print("          ③ query 与文档用词差异过大（考虑在文档里补同义表述）")
    else:
        print(f"\n全部 {n} 条在 top{top_k} 内命中。")

    return 0 if not miss else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="RAG 检索质量评测")
    ap.add_argument("-k", "--top-k", type=int, default=3, help="检索深度，默认 3")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每条用例的命中详情")
    ap.add_argument("--show-miss", action="store_true", help="只打印未命中/非 top1 的用例")
    ap.add_argument("--mode", choices=["vector", "hybrid"], default="hybrid",
                    help="检索方式：vector=纯向量，hybrid=向量+关键词 RRF（默认，与生产一致）")
    args = ap.parse_args()

    raise SystemExit(evaluate(args.top_k, args.verbose, args.show_miss, args.mode))


if __name__ == "__main__":
    main()
