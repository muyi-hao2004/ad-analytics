"""
知识库初始化脚本：读 data/knowledge/*.md → 建向量库。

用法：
    python src/init_knowledge.py

以后往 data/knowledge/ 里加了新的经验文档，重跑一次即可刷新向量库。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import knowledge_base


def main():
    print("=" * 50)
    print("IAA 优化师经验知识库 初始化")
    print("=" * 50)
    stats = knowledge_base.build_knowledge()
    print(f"\n✅ 建库完成")
    print(f"  文档数: {stats['files']}")
    print(f"  知识块: {stats['chunks']}")
    print(f"  collection: {stats['collection']}")

    # 自检索一条，验证可用
    print("\n自检检索: '换素材的标准是什么'")
    for r in knowledge_base.search_knowledge("换素材的标准是什么", top_k=2):
        print(f"  [{r['source']}] {r['text'][:60]}...")


if __name__ == "__main__":
    main()
