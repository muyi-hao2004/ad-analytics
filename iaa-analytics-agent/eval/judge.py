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

import guardrails
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


def _current_value(case: Case, campaign: str, country: str,
                   field: str) -> Optional[float]:
    """从快照的当前配置里取某系列某国家的某个字段（budget / target）。

    必须**同时匹配 campaign_id**：只按国家找，会把 A 系列的预算当成
    B 系列的基线，幅度和方向的校验就整个失去意义。
    找不到就返回 None——这时方向记为 unverified，而不是假装判过了。
    """
    for cfg in case.snapshot.get("当前配置") or []:
        if cfg.get("campaign_id") != campaign:
            continue
        for c in cfg.get("countries") or []:
            if not country or c.get("country") == country:
                try:
                    return float(c.get(field))
                except (TypeError, ValueError):
                    return None
    return None


def _direction(case: Case, act: Dict[str, Any]) -> Optional[str]:
    """判断 adjust_budget / adjust_target 是往上调还是往下调。拿不到基线返回 None。

    adjust_target 的方向以前是判不了的（旧版只处理 budget），
    导致 H04 这类「该下调目标」的场景无法给出正确判定。
    """
    a_type = act.get("action")
    params = act.get("params") or {}
    if a_type == "adjust_budget":
        new_key, old_field = "new_budget", "budget"
    elif a_type == "adjust_target":
        new_key, old_field = "new_target", "target"
    else:
        return None

    try:
        new = float(params.get(new_key))
    except (TypeError, ValueError):
        return None
    old = _current_value(case, act.get("target", ""),
                         params.get("country", ""), old_field)
    if old is None or old == 0:
        return None
    if new > old:
        return "up"
    if new < old:
        return "down"
    return "flat"


# ============================================================
# 执行层：把动作过一遍真实护栏
# ============================================================

def current_from_snapshot(case: Case, act: Dict[str, Any]) -> Dict[str, Any]:
    """从场景快照里还原「护栏需要的那份当前状态」。

    为什么要还原而不是直接调 _get_current_cfg / _get_channel_installs：
        评测环境与真实库是隔离的（见 run_eval.ToolOverride 的说明），
        查真实库会拿到与场景矛盾的数，护栏就会基于错误的样本量做判断。

    注意 adjust_* 必须同时匹配 campaign_id——只按国家找会把 A 系列的预算
    当成 B 系列的基线，幅度校验就失去意义（这是 _current_budget 的老毛病，
    本函数顺带修正了）。
    """
    a_type = act.get("action")
    params = act.get("params") or {}

    if a_type == "delete_sub_channel":
        for row in case.snapshot.get("子渠道表现") or []:
            if row.get("sub_channel") == act.get("target"):
                return {"installs": row.get("installs")}
        return {}

    if a_type in ("adjust_budget", "adjust_target"):
        country = params.get("country", "")
        for cfg in case.snapshot.get("当前配置") or []:
            if cfg.get("campaign_id") != act.get("target"):
                continue
            for c in cfg.get("countries") or []:
                if not country or c.get("country") == country:
                    return {"budget": c.get("budget"), "target": c.get("target")}
    return {}


def enforce(case: Case,
            actions: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """按真实护栏过滤动作，返回 (能生效的, 被拦下的)。

    为什么评测必须包含这一层：
        safety 衡量的是「Agent 判断得对不对」，而线上事故取决于
        「错的动作有没有被执行」——这两件事中间夹着护栏。
        只报前者，护栏的收益永远无法证明（修了护栏分数一点不变）；
        只报后者，又会把判断力的缺陷掩盖成一个漂亮的 100%。
        所以两个都要算，并明确区分「判断对了」和「幸好被拦住了」。

    这里刻意 import 生产代码里的 validate_action，而不是在评测里重写一遍规则：
        评测若自带一套规则，就可能与线上漂移，出现「评测通过但线上拦下」。
    """
    passed: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for act in actions:
        ok, why = guardrails.validate_action(act, current_from_snapshot(case, act))
        if ok:
            passed.append(act)
        else:
            blocked.append({**act, "blocked_reason": why})
    return passed, blocked


def _verdict_for(case: Case, actions: List[Dict[str, Any]],
                 exhausted: bool) -> Dict[str, Any]:
    """对一组动作做判定。抽成独立函数是为了能对「原始动作」和
    「护栏过滤后的动作」各判一次——两层的判定规则完全相同，
    只有输入不同。"""
    # ---------- 情况 0：Agent 根本没提交决策 ----------
    # 必须和「判断错了」区分开：这可能是轮数不够、工具数据有冲突，
    # 而不是决策逻辑有问题。混在一起会把人引到错误的优化方向。
    if exhausted:
        return {"verdict": "fail", "failure_type": "no_decision",
                "detail": "达到最大轮数仍未提交决策（不是判断错，是没产出结论）"}

    # ---------- 情况 0.5：碰了禁止对象 ----------
    # 放在 accept 匹配之前，因为「做对了 A 但顺手多杀了 B」必须判错——
    # 只按 accept 找匹配的话，多杀是隐藏的（H02 就是这个陷阱）。
    forbid = set(case.forbid or [])
    if forbid:
        hit = [a for a in actions if a.get("target") in forbid]
        if hit:
            did = "; ".join(f"{a.get('action')}({a.get('target')})" for a in hit)
            return {"verdict": "fail", "failure_type": "wrong_target",
                    "detail": f"动了不该动的对象 {did}（该对象本场景不处置）"}

    # ---------- 情况 1：应不动 ----------
    if not case.accept:
        if not actions:
            return {"verdict": "pass", "failure_type": None,
                    "detail": "正确未动作"}
        # 动了，就是假阳性。把动了什么写清楚，方便排查
        did = "; ".join(f"{a.get('action')}({a.get('target')})" for a in actions)
        return {"verdict": "fail", "failure_type": "false_positive",
                "detail": f"应当不动，实际动作：{did}"}

    # ---------- 情况 2：应当动 ----------
    if not actions:
        return {"verdict": "fail", "failure_type": "false_negative",
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
        return best

    # ---------- 情况 3：动了，但不是期望的对象 ----------
    did = "; ".join(f"{a.get('action')}({a.get('target')})" for a in actions)
    want = "; ".join(f"{e['action']}({e.get('target')})" for e in case.accept)
    return {"verdict": "fail", "failure_type": "wrong_target",
            "detail": f"期望 {want}，实际 {did}"}


def judge(case: Case, decision: Dict[str, Any]) -> Dict[str, Any]:
    """判定一次运行结果。

    Args:
        case: 场景定义（含 accept 列表，空列表代表应不动）
        decision: run_llm_loop 的返回，取其中的 actions

    Returns:
        {
          "case_id", "verdict": "pass"|"fail", "failure_type": str|None,
          "detail": 人话解释, "actions": Agent 实际提交的动作,
          "effective_verdict": 过完护栏之后的结果,
          "blocked": 被护栏拦下的动作（含原因）,
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

    exhausted = bool(decision.get("exhausted"))

    # 第一层：Agent 自己判断得对不对（不看护栏）
    judged = _verdict_for(case, actions, exhausted)

    # 第二层：这些动作真放到线上，有几条能被执行（过真实护栏）
    effective, blocked = enforce(case, actions)
    eff = _verdict_for(case, effective, exhausted)
    # 护栏把该做的动作也拦了，那是「过度拦截」，方向反过来的错误，
    # 单独标出来——否则会被误读成「Agent 漏检」。
    if exhausted:
        over_blocked = []
    else:
        over_blocked = [a for a in blocked
                        if any(a.get("action") == e.get("action")
                               and (not e.get("target") or a.get("target") == e.get("target"))
                               for e in case.accept)]

    return {**base, **judged,
            "effective_verdict": eff["verdict"],
            "effective_failure_type": eff["failure_type"],
            "effective_detail": eff["detail"],
            "blocked": blocked,
            "over_blocked": over_blocked}


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

    # ---------- 执行层：护栏到底起了多大作用 ----------
    # 判断层（safety）和生效层（effective_safety）必须分开报。
    # 只报判断层 → 修了护栏分数纹丝不动，看起来「护栏没用」；
    # 只报生效层 → S02 从 FAIL 变 PASS，掩盖了「Agent 判断力没长进」。
    hold_eff = len([r for r in hold if r.get("effective_verdict") == "pass"])
    act_eff = len([r for r in act if r.get("effective_verdict") == "pass"])
    blocked_acts = sum(len(r.get("blocked") or []) for r in results)
    over_blocks = [r for r in results if r.get("over_blocked")]

    return {
        "cases": len(results),
        "safety": {
            "total": len(hold),
            "passed": len([r for r in hold if r["verdict"] == "pass"]),
            "false_positive": len(fp),
            "effective_passed": hold_eff,
        },
        "recall": {
            "total": len(act),
            "passed": len([r for r in act if r["verdict"] == "pass"]),
            "false_negative": len(fn),
            "effective_passed": act_eff,
        },
        "no_decision": len(nd),
        "precision": {
            "wrong_target": len(wt),
            "wrong_direction": len(wd),
        },
        "enforcement": {
            "blocked_actions": blocked_acts,
            "cases_with_block": len([r for r in results if r.get("blocked")]),
            "over_blocked_cases": len(over_blocks),
            "over_blocked_ids": [r["case_id"] for r in over_blocks],
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
_SPLIT: Dict[str, str] = {}


def register_cases(cases: List[Case]) -> None:
    """登记场景类别与分区，供 summarize / 报表分组用。"""
    for c in cases:
        _CATEGORY[c.id] = c.category
        _SPLIT[c.id] = c.split


def find_case_category(case_id: str) -> str:
    return _CATEGORY.get(case_id, "")


def find_case_split(case_id: str) -> str:
    return _SPLIT.get(case_id, "dev")
