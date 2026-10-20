# -*- coding: utf-8 -*-
"""
判定器：把 Agent 的输出和场景的标准答案比对。

============================================================
为什么不做「准确率」一个数
============================================================
因为两类错误的代价**严重不对称**：

    误删一个正常渠道（假阳性）→ 直接损失正在赚钱的流量，且难以恢复
    漏掉一次优化机会（假阴性）→ 少赚一点，下个周期还能发现

如果只看准确率，一个「永远不动」的 Agent 在 must_hold 场景上能拿满分，
在 must_act 上全错，整体准确率看起来还行——但它毫无价值。
反过来一个「见低就删」的 Agent 也能拿到不低的准确率，却是资损级的。

所以这里分成三个指标分别统计，最后**不合并成一个总分**：
    safety     不该动的时候，真的没动吗（假阳性率）
    recall     该动的时候，动了吗（假阴性率）
    precision  动的时候，对象和方向对吗

面试里被问「你怎么评估」，回答「我分三层看，因为两类错误代价不对称」，
和回答「我算了个准确率 82%」，可信度不是一个量级。

============================================================
失败分类
============================================================
    false_positive   不该动却动了        最危险：直接改线上配置
    wrong_direction  动了但方向反了      最危险：该降却加，放大亏损
    wrong_target     动了但对象错了      中等：动了无辜对象
    false_negative   该动却没动          最轻：错失优化机会
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from scenarios import Case


# 失败类型 -> 严重度（数字越大越严重）
SEVERITY = {
    "false_negative": 1,
    "no_decision": 2,        # 没产出结论：可能是轮数/数据问题，不一定是判断错
    "wrong_target": 3,
    "false_positive": 4,
    "wrong_direction": 5,
}

SEVERITY_LABEL = {
    "false_positive": "不该动却动了",
    "wrong_direction": "方向反了",
    "wrong_target": "对象错了",
    "false_negative": "该动没动",
    "no_decision": "未提交决策",
    "error": "运行异常",
}


def _current_budget(case: Case, target: str, country: str = "") -> Optional[float]:
    """从快照的当前配置里找某个对象的现有预算，用于判断调预算的方向。

    找不到就返回 None——这时方向记为 unverified，而不是假装判过了。
    诚实地说「这条没验证到」，比给一个编出来的判定有用。
    """
    for cfg in case.snapshot.get("当前配置") or []:
        for c in cfg.get("countries") or []:
            if not country or c.get("country") == country:
                try:
                    return float(c.get("budget"))
                except (TypeError, ValueError):
                    return None
    return None


def _direction(case: Case, act: Dict[str, Any]) -> Optional[str]:
    """判断一个 adjust_budget 动作是加还是减。拿不到基线返回 None。"""
    if act.get("action") != "adjust_budget":
        return None
    params = act.get("params") or {}
    try:
        new = float(params.get("new_budget"))
    except (TypeError, ValueError):
        return None
    old = _current_budget(case, act.get("target", ""), params.get("country", ""))
    if old is None or old == 0:
        return None
    if new > old:
        return "up"
    if new < old:
        return "down"
    return "flat"


def judge(case: Case, decision: Dict[str, Any]) -> Dict[str, Any]:
    """判定一次运行结果。

    Args:
        case: 场景定义（含 accept 列表，空列表代表应不动）
        decision: run_llm_loop 的返回，取其中的 actions

    Returns:
        {
          "case_id", "verdict": "pass"|"fail", "failure_type": str|None,
          "detail": 人话解释, "actions": Agent 实际提交的动作,
          "usage": 该场景的成本
        }
    """
    raw_actions = decision.get("actions") or []
    actions = [a for a in raw_actions if isinstance(a, dict) and a.get("action")]

    usage = decision.get("usage") or {}
    # reasoning 必须留下来。否则失败时只知道「错了」，不知道「怎么想的」，
    # 就无法判断是 Agent 判断偏了，还是场景设计有缺陷。
    base = {"case_id": case.id, "intent": case.intent,
            "actions": actions, "usage": usage,
            "conclusion": (decision.get("conclusion") or "")[:300],
            "reasoning": (decision.get("reasoning") or "")[:800]}

    # ---------- 情况 0：Agent 根本没提交决策 ----------
    # 必须和「判断错了」区分开：这可能是轮数不够、工具数据有冲突，
    # 而不是决策逻辑有问题。混在一起会把人引到错误的优化方向。
    if decision.get("exhausted"):
        return {**base, "verdict": "fail", "failure_type": "no_decision",
                "detail": "达到最大轮数仍未提交决策（不是判断错，是没产出结论）"}

    # ---------- 情况 1：应不动 ----------
    if not case.accept:
        if not actions:
            return {**base, "verdict": "pass", "failure_type": None,
                    "detail": "正确未动作"}
        # 动了，就是假阳性。把动了什么写清楚，方便排查
        did = "; ".join(f"{a.get('action')}({a.get('target')})" for a in actions)
        return {**base, "verdict": "fail", "failure_type": "false_positive",
                "detail": f"应当不动，实际动作：{did}"}

    # ---------- 情况 2：应当动 ----------
    if not actions:
        return {**base, "verdict": "fail", "failure_type": "false_negative",
                "detail": "应当动作，实际未动作"}

    # 逐个 accept 规则匹配；返回匹配到的最好结果
    best: Optional[Dict[str, Any]] = None
    for exp in case.accept:
        for act in actions:
            if act.get("action") != exp["action"]:
                continue
            if exp.get("target") and act.get("target") != exp["target"]:
                continue

            # 对象和动作类型都对上了。如果规则要求方向，再比一次
            want_dir = exp.get("dir")
            got_dir = _direction(case, act)
            if want_dir and got_dir and got_dir != want_dir:
                cand = {"verdict": "fail", "failure_type": "wrong_direction",
                        "detail": f"应当 {want_dir}，实际 {got_dir}"}
            elif want_dir and got_dir is None:
                cand = {"verdict": "pass", "failure_type": None,
                        "detail": f"动作与对象正确（方向未验证：当前配置里无基线）"}
            else:
                cand = {"verdict": "pass", "failure_type": None,
                        "detail": f"正确命中 {exp['action']}({exp.get('target')})"}

            if best is None or (cand["verdict"] == "pass" and best["verdict"] != "pass"):
                best = cand
            if best["verdict"] == "pass":
                break
        if best and best["verdict"] == "pass":
            break

    if best is not None:
        return {**base, **best}

    # ---------- 情况 3：动了，但不是期望的对象 ----------
    did = "; ".join(f"{a.get('action')}({a.get('target')})" for a in actions)
    want = "; ".join(f"{e['action']}({e.get('target')})" for e in case.accept)
    return {**base, "verdict": "fail", "failure_type": "wrong_target",
            "detail": f"期望 {want}，实际 {did}"}


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总三层指标。

    注意：这里**刻意不输出一个总分**。三层指标的权重是业务决定的，
    不是算法决定的——把它压成一个数会掩盖「safety 100% 但 recall 0%」
    这种关键信息。
    """
    # known_gap 不计入任何指标：它是「当前架构下做不到」，不是「Agent 做错了」。
    # 混进 recall 会把架构欠账算成模型能力问题，优化方向会被带偏。
    scored = [r for r in results if find_case_category(r["case_id"]) != "known_gap"]
    gaps = [r for r in results if find_case_category(r["case_id"]) == "known_gap"]

    hold = [r for r in scored if find_case_category(r["case_id"]) == "must_hold"]
    act = [r for r in scored if find_case_category(r["case_id"]) == "must_act"]

    fp = [r for r in results if r.get("failure_type") == "false_positive"]
    fn = [r for r in results if r.get("failure_type") == "false_negative"]
    nd = [r for r in results if r.get("failure_type") == "no_decision"]
    wd = [r for r in results if r.get("failure_type") == "wrong_direction"]
    wt = [r for r in results if r.get("failure_type") == "wrong_target"]

    total_cost = sum((r.get("usage") or {}).get("cost_usd", 0) for r in results)
    total_tokens = sum((r.get("usage") or {}).get("total_tokens", 0) for r in results)

    return {
        "cases": len(results),
        "safety": {
            "total": len(hold),
            "passed": len([r for r in hold if r["verdict"] == "pass"]),
            "false_positive": len(fp),
        },
        "recall": {
            "total": len(act),
            "passed": len([r for r in act if r["verdict"] == "pass"]),
            "false_negative": len(fn),
        },
        "no_decision": len(nd),
        "precision": {
            "wrong_target": len(wt),
            "wrong_direction": len(wd),
        },
        "passed": len([r for r in scored if r["verdict"] == "pass"]),
        "scored": len(scored),
        "known_gap": {
            "total": len(gaps),
            "ids": [r["case_id"] for r in gaps],
        },
        "cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
    }


# 避免 summarize 依赖全局状态：用模块级注册表，由 runner 填
_CATEGORY: Dict[str, str] = {}


def register_cases(cases: List[Case]) -> None:
    """登记场景类别，供 summarize 分组用。"""
    for c in cases:
        _CATEGORY[c.id] = c.category


def find_case_category(case_id: str) -> str:
    return _CATEGORY.get(case_id, "")
