# -*- coding: utf-8 -*-
"""
评测场景库（golden set）。

============================================================
为什么不需要真实数据集
============================================================
这是最容易被误解的一点，值得写在最前面。

真实投放数据里**没有标准答案**。「这个渠道该不该删」连资深优化师也只能
凭经验判断，历史上没有人给每条数据标注过「正确答案」。也就是说：
真实数据集有数据，但**没有 label**，做不了监督评测。

合成场景反过来——答案是你设计出来的，而且可以刻意控制变量：
    - 同样的聚合 ROI，一个是真垃圾，一个是单日数据未成熟
    - 同样的低 ROI，一个样本量 2 万，一个样本量 30（不可信）
    - 同样的不达标，一个从没调过，一个 6 小时前刚调过

所以评测的关键不是「数据真不真实」，而是**陷阱设计得对不对**。
只要每个场景的「正确做法」是业务上唯一合理的，它就是有效 label。

这也是这个岗位（AI 数据工程 / 训练数据工程）的核心能力：
    **定义什么叫正确样本，并证明这个定义是可执行的。**
而不是「我有一批真实数据」。

============================================================
场景怎么构成
============================================================
每个 Case 三部分：
    snapshot   —— 喂给 LLM 的初始数据（模拟「日报数据版」，和线上同构）
    tools      —— 假工具实现。**决定「不下钻能不能答对」**
    accept     —— 可接受的动作集合（空列表 = 应该不动）

关键设计：**tools 是陷阱的载体。**
只有真的调了 get_metric 下钻，才能看到「前 7 天正常、最后一天暴跌」；
只看 snapshot 里的聚合值，一定会答错。这样才算测到了「多轮取数能力」，
而不是测「LLM 会不会看图说话」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Case:
    """一个评测场景。

    Attributes:
        id: 场景编号，报表里用
        category: "must_hold"（不应动作）/ "must_act"（应当动作）
        intent: 人话说明这个场景考什么
        snapshot: 喂给 LLM 的快照
        tools: 工具名 -> 假实现。工具名不带 `_tool_` 前缀，如 "get_metric"
        accept: 可接受的动作列表。每项 {"action":..., "target":..., "dir": "up"/"down"/None}
                空列表表示「正确做法是不做任何动作」
        trap: 陷阱机制说明（写给人看，也用于报表解释）
        note: 判定依据
    """
    id: str
    category: str
    intent: str
    snapshot: Dict[str, Any]
    tools: Dict[str, Callable] = field(default_factory=dict)
    accept: List[Dict[str, Any]] = field(default_factory=list)
    trap: str = ""
    note: str = ""


# ============================================================
# 公共构造器：让场景定义短一点
# ============================================================

TARGET = {"D7_ROAS_目标": "70%",
          "说明": "D7 ROI 低于该值说明变现效率不足，需要优化"}


def _snap(creatives=None, channels=None, config=None, logs=None) -> Dict[str, Any]:
    return {
        "素材表现": creatives or [],
        "子渠道表现": channels or [],
        "当前配置": config or [],
        "最近操作": logs or [],
        "业务目标": TARGET,
    }


def _row(roi: float, spend: float = 600.0, installs: int = 20000,
         days: int = 8) -> Dict[str, Any]:
    """构造一行 Agg 后的表现数据（字段与 _load_top 输出一致）。"""
    return {"spend": spend, "clicks": int(spend * 100), "buy_impressions": int(spend * 1000),
            "installs": installs, "d7_roi": roi, "days": days}


def _detail(values: List[float], key: str = "d7_roi") -> Dict[str, Any]:
    """构造明细返回（用于 get_metric / get_trend_with_ops 的假实现）。"""
    return {"data": [{"date": f"10-{i + 1:02d}", key: v} for i, v in enumerate(values)]}


def _cfg(campaign: str, country: str, budget: float, target: float,
         strategy: str = "D7_ROAS") -> Dict[str, Any]:
    return {"campaign_id": campaign, "strategy": strategy, "owner": "张三",
            "channel": "unity",
            "countries": [{"country": country, "budget": budget, "target": target}]}


# ============================================================
# 场景定义
# ============================================================

def build_cases() -> List[Case]:
    """返回全部评测场景。

    结构上刻意做成「同一类表面现象，正确答案相反」：
        S01 低聚合 ROI → 不动（数据未成熟）
        S03 低聚合 ROI → 该动（真的差）
    如果 Agent 只会按「ROI 低就删」，它必然在 S01 上失败。
    这就是这批评测的鉴别力来源。
    """
    cases: List[Case] = []

    # --------------------------------------------------------
    # S01 单日异常（数据未成熟）—— 经典误删陷阱
    # --------------------------------------------------------
    def s01_metric(obj_type=None, obj_id=None, metric=None, days=8, **kw):
        if obj_id == "crt_020" and metric == "d7_roi":
            # 前 7 天健康，只有最后一天暴跌。D7 ROI 的窗口是 install_date 起 7 天，
            # 最后一天的 cohort 还没跑满，天然偏低 —— 不是素材变差。
            return {"obj_id": obj_id, "metric": metric,
                    **_detail([0.82, 0.78, 0.85, 0.81, 0.79, 0.83, 0.80, 0.03])}
        return {"obj_id": obj_id, "metric": metric, **_detail([0.80] * 8)}

    cases.append(Case(
        id="S01",
        category="must_hold",
        intent="聚合 ROI 极低，但是单日未成熟数据造成的假象",
        trap="只看聚合值会误删一个健康素材；必须调 get_metric 看明细才能发现",
        note="D7 ROI 需要 install_date 起 7 天窗口，最后一天 cohort 未跑满，偏低属正常",
        snapshot=_snap(creatives=[
            {"creative_id": "crt_020", **_row(0.037, spend=500, installs=10000)},
            {"creative_id": "crt_021", **_row(1.02, spend=600, installs=12000)},
        ]),
        tools={"get_metric": s01_metric},
        accept=[],
    ))

    # --------------------------------------------------------
    # S02 样本量不足 —— 低 ROI 但统计上不可信
    # --------------------------------------------------------
    def s02_metric(obj_type=None, obj_id=None, metric=None, days=8, **kw):
        # 注意：必须按 obj_id 返回**不同但自洽**的数据。
        # 第一版图省事让所有 ID 都返回同一串 0.05，结果 LLM 在 reasoning 里
        # 直接指出「数据串号、与快照矛盾」，进而降低了置信度。
        # 这属于场景 bug——评测数据本身不自洽，测出来的就不是决策能力。
        if obj_id == "ch_009":
            # 只有 4 天、每天都是 0.05：样本窗口都不够，结论不成立
            return {"obj_id": obj_id, "metric": metric, **_detail([0.05] * 4)}
        # ch_010 是健康对照组，8 天稳定达标
        return {"obj_id": obj_id, "metric": metric, **_detail([0.95] * 8)}

    cases.append(Case(
        id="S02",
        category="must_hold",
        intent="ROI 只有 5%，但样本量太小（30 次安装），结论不可信",
        trap="按 ROI 数值排序它最差；不检查 installs/spend 量级就会去动它",
        note="30 installs 的 ROI 波动可达数倍，不足以支撑删除决策；正确做法是等样本或加大投放观察",
        snapshot=_snap(channels=[
            {"sub_channel": "ch_009", **_row(0.05, spend=15.0, installs=30, days=4)},
            {"sub_channel": "ch_010", **_row(0.95, spend=800, installs=26000)},
        ]),
        tools={"get_metric": s02_metric},
        accept=[],
    ))

    # --------------------------------------------------------
    # S03 真的差 —— 该动（与 S01/S02 形成对照）
    # --------------------------------------------------------
    def s03_metric(obj_type=None, obj_id=None, metric=None, days=8, **kw):
        # 连续 8 天都在 0.10~0.13，稳定地差，样本量充足
        return {"obj_id": obj_id, "metric": metric,
                **_detail([0.11, 0.12, 0.10, 0.13, 0.11, 0.12, 0.10, 0.12])}

    cases.append(Case(
        id="S03",
        category="must_act",
        intent="连续 8 天 ROI 稳定在 11% 左右，远低于 70% 目标，样本充足",
        trap="无陷阱，是基础有效性检查：该动的时候必须动",
        note="稳定低效 + 样本充足（2 万安装）→ 删子渠道或降预算都合理",
        snapshot=_snap(channels=[
            {"sub_channel": "ch_011", **_row(0.115, spend=700, installs=21000)},
            {"sub_channel": "ch_012", **_row(0.92, spend=650, installs=22000)},
        ]),
        tools={"get_metric": s03_metric},
        accept=[
            {"action": "delete_sub_channel", "target": "ch_011"},
            {"action": "adjust_budget", "target": "ch_011", "dir": "down"},
        ],
    ))

    # --------------------------------------------------------
    # S04 聚合差，但分国家看有一个健康 —— 不该整体删
    # --------------------------------------------------------
    def s04_metric(obj_type=None, obj_id=None, metric=None, days=8, **kw):
        # 整体 0.35，但美国 0.95、日本 0.05：正确做法是只处理日本
        return {"obj_id": obj_id, "metric": metric,
                "by_country": [{"country": "US", "d7_roi": 0.95, "spend": 600},
                               {"country": "JP", "d7_roi": 0.05, "spend": 600}]}

    cases.append(Case(
        id="S04",
        category="must_hold",
        intent="系列整体 ROI 35%，但拆国家看美国 95% 达标、日本 5% 拖累",
        trap="直接按系列维度删会误杀美国这个赚钱的市场；必须先拆国家",
        note="整体不达标时不能整体处置。本场景测的是「是否会做维度下钻后分层决策」"
             "（是否真的去调日本算加分项，但整体删除一定错）",
        snapshot=_snap(channels=[
            {"sub_channel": "ch_013", **_row(0.35, spend=1200, installs=30000)},
        ]),
        tools={"get_metric": s04_metric},
        accept=[],
    ))

    # --------------------------------------------------------
    # S05 超额达标 —— 该加预算
    # --------------------------------------------------------
    def s05_metric(obj_type=None, obj_id=None, metric=None, days=8, **kw):
        return {"obj_id": obj_id, "metric": metric,
                **_detail([1.35, 1.40, 1.32, 1.38, 1.36, 1.41, 1.33, 1.37])}

    cases.append(Case(
        id="S05",
        category="known_gap",
        intent="ROI 稳定 135% 该加量，但当前架构下这条路径走不通",
        trap="这不是 Agent 判断错，是**动作空间与可观测维度不匹配**",
        note="【已知架构限制】adjust_budget / adjust_target 的对象是「系列+国家」，"
             "而快照只提供「素材/子渠道」维度的表现，两者之间没有映射字段。"
             "Agent 能看到 crt_030 该加量，却无法知道它属于哪个系列、该调哪个 campaign 的预算。"
             "所以这个场景在当前架构下不可达，不计入 recall，单独记为 known_gap。"
             "修法：在快照的子渠道/素材表现里带上 campaign_id（数据里本来就有，"
             "是 _load_top 聚合时丢掉了）。",
        snapshot=_snap(
            creatives=[{"creative_id": "crt_030", **_row(1.37, spend=800, installs=30000)}],
            # 第一版漏了当前配置，Agent 的理由是「缺预算基线，无法确认加量空间」，
            # 判它 false_negative 其实不公平——是场景没给判断所需的信息。
            # 教训：场景必须提供该决策所需的最小充分信息，否则测的是信息缺失
            # 而不是决策能力。
            config=[_cfg("US_A_01", "US", 800, 0.70)],
        ),
        tools={"get_metric": s05_metric},
        # accept 先留空：对象级预算调整在当前架构下不存在，
        # 写一个做不到的标准只会得到无意义的失败。
        accept=[],
    ))

    # --------------------------------------------------------
    # S06 刚调过 —— 不该重复调整
    # --------------------------------------------------------
    def s06_logs(keyword="", limit=10, **kw):
        # 6 小时前刚被降过预算，再降会过度收缩
        return {"logs": [{"time": "2026-10-20 08:12:00", "operator": "张三",
                          "field": "budget", "campaign_id": "US_A_01",
                          "country": "US", "old_value": "1200", "new_value": "800",
                          "reason": "ROI 未达标，先降一档观察"}]}

    cases.append(Case(
        id="S06",
        category="must_hold",
        intent="ROI 仍略低于目标，但 6 小时前刚降过预算，尚未观察到效果",
        trap="不查操作日志就会重复降预算，导致过度收缩",
        note="同一对象调整后应留观察期（这里 6 小时远短于一个归因窗口），"
             "正确做法是不动或只建议观察",
        snapshot=_snap(
            channels=[{"sub_channel": "ch_014", **_row(0.58, spend=800, installs=24000)}],
            config=[_cfg("US_A_01", "US", 800, 0.70)],
        ),
        tools={"get_logs": s06_logs},
        accept=[],
    ))

    # --------------------------------------------------------
    # S07 达标但结构恶化 —— 需要下钻才看得见
    # --------------------------------------------------------
    def s07_metric(obj_type=None, obj_id=None, metric=None, days=8, **kw):
        if metric == "d7_retention":
            # 留存连续下滑，虽然 ROI 还在 0.75（达标）但趋势危险
            return {"obj_id": obj_id, "metric": metric,
                    **_detail([0.42, 0.40, 0.38, 0.35, 0.33, 0.30, 0.28, 0.25],
                              key="d7_retention")}
        return {"obj_id": obj_id, "metric": metric, **_detail([0.75] * 8)}

    cases.append(Case(
        id="S07",
        category="must_act",
        intent="子渠道 ROI 0.75 刚好达标，但 D7 留存 8 天内从 42% 跌到 25%",
        trap="只看 ROI 会认为「达标无需处理」；必须下钻留存才能发现结构恶化",
        note="ROI 达标不代表健康，留存持续下滑预示未来 ROI 会跌。"
             "对象用子渠道而非素材：delete_sub_channel 是当前架构下唯一成立的"
             "对象级动作（见 S05 的架构限制说明）",
        snapshot=_snap(channels=[
            {"sub_channel": "ch_040", **_row(0.75, spend=900, installs=25000)},
        ]),
        tools={"get_metric": s07_metric},
        accept=[
            {"action": "delete_sub_channel", "target": "ch_040"},
        ],
    ))

    # --------------------------------------------------------
    # S08 数据窗口不足 —— 无法判断
    # --------------------------------------------------------
    def s08_metric(obj_type=None, obj_id=None, metric=None, days=8, **kw):
        return {"obj_id": obj_id, "metric": metric, "data": [],
                "message": "该对象仅入库 2 天，无法计算 D7 指标"}

    cases.append(Case(
        id="S08",
        category="must_hold",
        intent="新上的素材只有 2 天数据，D7 指标根本算不出来",
        trap="快照里 days=2 但 ROI 显示 0.10，不看 days 直接处置就会误伤新品",
        note="D7 指标需要 7 天窗口；不满窗口时任何处置都属于过早决策",
        snapshot=_snap(creatives=[
            {"creative_id": "crt_050", **_row(0.10, spend=120, installs=3000, days=2)},
        ]),
        tools={"get_metric": s08_metric},
        accept=[],
    ))

    return cases
