# -*- coding: utf-8 -*-
"""检索评测用例：query → 期望命中的文档。

为什么要评测检索质量
-------------------
RAG 的"效果好不好"不能靠翻两三条结果目测。要回答的是：
  - 用户问一个问题，正确的那段知识**排在第几**？
  - 换个问法（口语化 / 术语化）还找得到吗？
  - 问一个知识库里没有的东西，会不会硬凑一段无关内容？

这三件事都可以用固定 query 集合 + 命中排名来量化，不需要 LLM，不需要人打分。

判定口径
-------
每个用例给一个 `expected`（期望命中的文档前缀，如 "03_"）和可选的
`must_contain`（该 chunk 里必须出现的词，用来确认命中的是**正确的那一段**
而不是同一文档里无关的一段）。

为什么分 `expected` 和 `must_contain` 两级：
  只判文档会太松——命中 09 篇但命中的是"引用规则"那一节，
  而问题问的是"网赚类有没有基准"，那仍然是错的。
  只看关键词又太严——同一段话换个说法就判失败。
  两级合起来才既容忍表述差异，又能识别"命中了但答非所问"。

diff 难度分布
------------
`difficulty` 字段用来观察不同类型的检索失败：
  - easy:   query 里的词基本来自文档原文（考嵌入能不能对齐）
  - medium: 换说法，术语与原文不同（考语义匹配）
  - hard:   需要跨段落综合，或答案分散在多个文档（考 chunk 策略）
  - negative: 知识库里没有答案，正确行为是**低分/不命中**（考会不会硬凑）
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class RetrievalCase:
    """一条检索评测用例。

    Attributes:
        id: 用例编号，如 R01
        query: 检索输入（模拟 Agent 或用户的真实问法）
        expected: 期望命中的文档名前缀，如 "03_"。negative 用例为 None
        must_contain: 命中 chunk 里应出现的关键词（任一命中即可），用于确认段落相关性
        difficulty: easy / medium / hard / negative
        note: 这条用例想测什么
    """
    id: str
    query: str
    expected: str | None
    must_contain: List[str] = field(default_factory=list)
    difficulty: str = "easy"
    note: str = ""


def build_cases() -> List[RetrievalCase]:
    """构造全部用例。新增知识文档后应同步补充对应 query。"""
    return [
        # ---------- easy：query 词基本来自原文 ----------
        RetrievalCase(
            "R01", "D7 ROAS 低于目标要不要直接删除渠道", "01_",
            ["下钻", "删除"], "easy",
            "原有经验文档，测最基础的召回"),
        RetrievalCase(
            "R02", "换素材的标准是什么", "01_",
            ["素材", "换"], "easy",
            "原文小标题就是「素材优化」"),
        RetrievalCase(
            "R03", "ARPDAU 的公式", "03_",
            ["ARPDAU", "eCPM"], "easy",
            "公式类问题，术语明确"),
        RetrievalCase(
            "R04", "CTIT 是什么意思", "08_",
            ["CTIT", "点击"], "easy",
            "缩写词，嵌入容易对齐"),

        # ---------- medium：换说法，考语义匹配 ----------
        RetrievalCase(
            "R05", "一个用户带来的广告收入还不够买他的钱，是哪里出了问题", "03_",
            ["LTV", "CPI", "ROAS"], "medium",
            "完全口语化描述 LTV < CPI，不含任何指标名"),
        RetrievalCase(
            "R06", "为什么加了插屏广告收入涨了但整体更亏", "03_",
            ["留存", "ARPDAU"], "medium",
            "描述「用留存换日收入」，需要理解 ARPDAU 与 LTV 的区别"),
        RetrievalCase(
            "R07", "次留比注册转化率还高，正常吗", "08_",
            ["留存", "转化率"], "medium",
            "口语+隐含判断，原文表述是「D1 留存 > 注册转化率」"),
        RetrievalCase(
            "R08", "刚开的智能出价活动三天没效果能不能马上调", "06_",
            ["学习期", "天"], "medium",
            "把「Target ROAS 学习期 15-20 天」换成「三天没效果」"),
        RetrievalCase(
            "R09", "超休闲游戏 D1 留存一般多少算正常", "04_",
            ["超休闲", "D1", "28"], "medium",
            "基准查询，需命中分品类表"),

        # ---------- hard：跨段落 / 需要综合 ----------
        RetrievalCase(
            "R10", "纯广告变现的游戏在高价市场买量能跑通吗", "03_",
            ["Tier1", "LTV", "CPI"], "hard",
            "需要综合「LTV vs CPI 4-10 倍差距」那段算术推演"),
        RetrievalCase(
            "R11", "ironSource 和 LevelPlay 是什么关系", "05_",
            ["LevelPlay", "ironSource"], "hard",
            "平台名词关系，chunk 里可能分散在开头几段"),
        RetrievalCase(
            "R12", "ROAS 目标是不是设得越高越好", "06_",
            ["目标", "量"], "hard",
            "原文结论是「设太高会限制量级」，问法是反向的"),
        RetrievalCase(
            "R13", "两个广告平台的 ROAS 目标调整节奏该怎么定", "05_",
            ["两周", "10%"], "hard",
            "需要同时用 05（每两周）和 06（每周2次±10%），命中任一即算对"),

        # ---------- negative：知识库没有答案，考会不会硬凑 ----------
        RetrievalCase(
            "R14", "网赚类看广告提现游戏的留存基准是多少", "09_",
            ["没有找到", "基准", "网赚"], "negative",
            "库内明确写了「没有可靠公开基准」，正确行为是命中这段说明"
            "而不是拿超休闲的数据硬答"),
        RetrievalCase(
            "R15", "印度 CPI 0.01 美元可行吗", "09_",
            ["印度", "0.01", "反驳"], "negative",
            "库内记录了这个存疑个案及从业者反驳"),
        RetrievalCase(
            "R16", "D7 ROAS 目标到底该设 70% 还是 150%", "09_",
            ["目标", "口径"], "negative",
            "库内把三个概念（优化器目标/回本线/实际值）分开解释了"),

        # ---------- 边界：容易串到别的文档 ----------
        RetrievalCase(
            "R17", "美国激励视频 eCPM 大概多少", "07_",
            ["eCPM", "激励"], "medium",
            "07 和 03 都提到 eCPM，应命中 07 的基准表"),
        RetrievalCase(
            "R18", "第四季度收入会不会变好", "07_",
            ["Q4", "季"], "medium",
            "问法完全没有「eCPM」字样"),
        RetrievalCase(
            "R19", "怎么判断一个渠道是刷量的", "08_",
            ["信号", "留存", "设备"], "medium",
            "综合类问题，08 是专篇"),
        RetrievalCase(
            "R20", "留存掉得很快是不是意味着产品不行", "04_",
            ["超休闲", "正常"], "hard",
            "原文强调「超休闲陡降是常态」，与直觉相反"),
    ]


if __name__ == "__main__":
    cases = build_cases()
    print(f"共 {len(cases)} 条检索用例\n")
    from collections import Counter
    for k, v in Counter(c.difficulty for c in cases).items():
        print(f"  {k:<9} {v}")
