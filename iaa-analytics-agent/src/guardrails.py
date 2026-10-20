# -*- coding: utf-8 -*-
"""
写操作护栏（Guardrails）

为什么需要这一层
----------------
Agent 的写操作是**不可逆**的：删子渠道、改预算、改目标。
而 LLM 会犯错——不是偶尔，是结构性的：

  - 幻觉出库里不存在的 ID（ch_999）
  - 数量级写错（30 → 30000，少写一个小数点）
  - 同一件事重复做（同一个渠道删两次）
  - 一次给 20 个动作，超出人的审阅能力

所以「LLM 决定做什么」和「平台真的执行」之间，必须夹一层**不依赖 LLM 的
确定性校验**。这层的三个特征缺一不可：

  1. 纯函数       —— 同样的输入永远同样的输出，不查网络、不依赖模型输出格式
  2. 可单测       —— 不需要起 LLM 就能验证每条边界
  3. 失败即拒绝   —— 校验不确定时默认拦下（fail-closed），不是默认放行

三层防线怎么分工
----------------
  ┌─ 第 1 层：结构校验 ──── 动作类型合法？必填参数在？类型对？
  ├─ 第 2 层：业务边界 ──── 本模块。幅度、绝对值、批量、去重
  └─ 第 3 层：人工确认 ──── pending → approve，人在回路上

第 1 层由 tool schema 承担（LLM 侧），第 3 层由 approval 流程承担，
本模块只管第 2 层。

一个容易被忽略的点：approve 时必须**重新校验**
----------------------------------------------
建议单可能在生成后隔了很久才被人点「确认」。这期间配置可能已经被人改过，
或者另一个 Agent 已经执行了相反的动作。所以：

  生成时校验 ≠ 执行时校验

执行前必须拿**当前的真实配置**再过一遍护栏。这是典型的 TOCTOU
（time-of-check to time-of-use）问题——检查的时候有效，用的时候已经失效。
"""

from typing import Any, Dict, List, Tuple
import re

# ============================================================
# 业务硬边界：要改策略就改这里，不要散落在业务代码里
# ============================================================

# 合法对象 ID 的形态：ch_003 / crt_002 / US_A_01 这类
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

BUDGET_MIN = 1.0                 # 单系列单国家日预算下限（USD）
BUDGET_MAX = 50_000.0            # 上限（USD）
BUDGET_MAX_RATIO = 3.0           # 单次调整：新值最多是原值的 3 倍
BUDGET_MIN_RATIO = 1.0 / 3.0     # 单次调整：新值最少是原值的 1/3

TARGET_MIN = 0.1                 # ROAS 目标下限（10%）
TARGET_MAX = 3.0                 # 上限（300%）

MAX_ACTIONS_PER_RUN = 5          # 一次决策最多能带几个写动作

# 删除子渠道前要求的最小累计安装量。
#
# 为什么删除要单独设门槛，而调预算不用：
#   调预算是**可逆**的——调错了下一轮调回来，代价是一天的预算。
#   删渠道是**不可逆**的——is_active 置 0 之后，历史归因断裂、学习期清零，
#   恢复也要重新积累。所以它要求的最低证据等级必须高于调预算。
#
# 为什么是 100：
#   ROI 是个**比率**，分母越小越不稳。30 次安装里差 1 个转化，
#   ROI 就能从 0.05 跳到 0.15。100 次安装约合 3~5 个付费事件，
#   才刚够看趋势；低于这个量级，任何结论都是噪声。
MIN_INSTALLS_FOR_DELETE = 100

VALID_ACTIONS = ("delete_sub_channel", "adjust_budget", "adjust_target")


def _is_number(x: Any) -> bool:
    """排除 bool（Python 里 True 是 int 的子类）和 NaN/Inf。"""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return x == x and x not in (float("inf"), float("-inf"))


def _describe(action: Dict[str, Any]) -> str:
    """给动作生成一句人可读的说明，用在拒绝原因里。"""
    a_type = action.get("action", "?")
    target = action.get("target", "?")
    params = action.get("params") or {}
    if a_type == "adjust_budget":
        return f"调整 {target}/{params.get('country', '?')} 预算 → {params.get('new_budget')}"
    if a_type == "adjust_target":
        return f"调整 {target}/{params.get('country', '?')} 目标 → {params.get('new_target')}"
    if a_type == "delete_sub_channel":
        return f"删除子渠道 {target}"
    return f"{a_type} {target}"


def validate_action(action: Dict[str, Any],
                    current: Dict[str, Any] = None) -> Tuple[bool, str]:
    """
    校验单个写动作。

    Args:
        action:  LLM 给出的动作 {"action","target","params","reason"}
        current: 该对象的**当前真实配置**，用于算变动幅度。
                 {"budget": 30.0, "target": 0.7}；查不到就传 None。
                 注意：取不到原值时对本模块采取更严的策略（见 adjust_budget）。

    Returns:
        (ok, reason) —— ok=False 时 reason 是给人看的拒绝原因

    设计取向：**fail-closed**。任何一项不确定就拒绝，让 Agent 重新决策，
    代价是多一轮；误执行的代价是不可逆的资损。
    """
    if not isinstance(action, dict):
        return False, "动作不是字典"

    a_type = action.get("action")
    if a_type not in VALID_ACTIONS:
        return False, f"未知动作类型 {a_type!r}，可用: {VALID_ACTIONS}"

    target = action.get("target")
    if not target or not isinstance(target, str):
        return False, f"{a_type}: 缺少 target"

    # 所有写操作都要求写理由——这是事后审计的唯一线索
    if not (action.get("reason") or "").strip():
        return False, f"{a_type}: 缺少 reason，写操作必须写明理由"

    params = action.get("params") or {}

    # ---------- 删子渠道：形态 + 样本量 ----------
    if a_type == "delete_sub_channel":
        # 形态检查：ID 只能是 ASCII 字母/数字/下划线/连字符，长度合理。
        # 这条规则来自一次真实测试——最初只查了「长度 ≤64 且无空白」，
        # 结果一句 18 字的中文（"我认为应该删除这个子渠道"）能通过，
        # 因为中文既没有空格也不超长。LLM 幻觉出的「描述性 target」正是这个形态。
        if len(target) > 64 or not _ID_RE.match(target):
            return False, (f"delete_sub_channel: target={target!r} 形态异常，"
                           f"疑似把描述当成了 ID（合法 ID 如 ch_003）")

        # 样本量门槛。
        # 这条规则的来由值得记一笔：评测里 S02 场景连续跑 3 次、3 次都删了
        # 一个只有 30 次安装的渠道（0/3）。而知识库里 08 篇早就写了
        # 「样本不足时正确动作是观察不是删」——也就是说，**这条约束
        # 写在 prompt 和知识库里都不管用**，LLM 该错还是错。
        # 所以它必须落到确定性代码里：判断可以错，执行不能被放行。
        inst = (current or {}).get("installs")
        if inst is None:
            return False, (f"delete_sub_channel: 查不到 {target} 的累计安装量，"
                           f"没有样本量就无法判断它的 ROI 是否可信（fail-closed）")
        if not _is_number(inst):
            return False, f"delete_sub_channel: installs={inst!r} 不是有效数字"
        if inst < MIN_INSTALLS_FOR_DELETE:
            return False, (
                f"delete_sub_channel: {target} 累计安装仅 {int(inst)} 次，"
                f"低于删除所需的最小样本 {MIN_INSTALLS_FOR_DELETE} 次。"
                f"样本不足时 ROI 波动可达数倍，正确动作是继续观察或加量验证，不是删除")
        return True, ""

    # ---------- 改预算 ----------
    if a_type == "adjust_budget":
        country = params.get("country")
        if not country:
            return False, "adjust_budget: 缺少 country"
        nb = params.get("new_budget")
        if not _is_number(nb):
            return False, f"adjust_budget: new_budget={nb!r} 不是有效数字"
        if nb < BUDGET_MIN or nb > BUDGET_MAX:
            return False, (f"adjust_budget: 新预算 {nb} 超出允许区间 "
                           f"[{BUDGET_MIN}, {BUDGET_MAX}]")

        cur = (current or {}).get("budget")
        if _is_number(cur) and cur > 0:
            ratio = nb / cur
            if ratio > BUDGET_MAX_RATIO or ratio < BUDGET_MIN_RATIO:
                return False, (f"adjust_budget: {target}/{country} 预算从 {cur} 变为 {nb}，"
                               f"幅度 {ratio:.2f}x 超出单次允许区间 "
                               f"[{BUDGET_MIN_RATIO:.2f}x, {BUDGET_MAX_RATIO:.1f}x]")
        # 取不到原值时不放行——没有基线就无法判断这一步迈多大，宁可拒绝
        elif cur is None:
            return False, (f"adjust_budget: 查不到 {target}/{country} 的当前预算，"
                           f"无基线无法校验变动幅度")
        return True, ""

    # ---------- 改目标 ----------
    if a_type == "adjust_target":
        country = params.get("country")
        if not country:
            return False, "adjust_target: 缺少 country"
        nt = params.get("new_target")
        if not _is_number(nt):
            return False, f"adjust_target: new_target={nt!r} 不是有效数字"
        if nt < TARGET_MIN or nt > TARGET_MAX:
            return False, (f"adjust_target: 新目标 {nt} 超出允许区间 "
                           f"[{TARGET_MIN}, {TARGET_MAX}]")
        cur = (current or {}).get("target")
        if _is_number(cur) and cur > 0:
            ratio = nt / cur
            if ratio > BUDGET_MAX_RATIO or ratio < BUDGET_MIN_RATIO:
                return False, (f"adjust_target: {target}/{country} 目标从 {cur} 变为 {nt}，"
                               f"幅度 {ratio:.2f}x 超出单次允许区间")
        return True, ""

    return False, "未覆盖的动作类型"


def check_batch(actions: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """
    批量预检：数量上限 + 同对象去重。

    去重规则：同一个 (action, target, country) 只保留**最后**一条——
    后面覆盖前面，符合「LLM 中途改主意」的直觉，也避免对同一对象反复写。

    Returns:
        (accepted, rejected)
          accepted: 通过形态检查的动作（业务边界仍要在执行时另校验）
          rejected: [{"action": ..., "reason": ...}]
    """
    rejected: List[Dict] = []
    actions = actions or []

    if len(actions) > MAX_ACTIONS_PER_RUN:
        rejected.append({
            "action": None,
            "reason": f"一次决策带 {len(actions)} 个动作，超过上限 {MAX_ACTIONS_PER_RUN}，"
                      f"整批拒绝（超量审批会让人失去逐条核对的能力）",
        })
        return [], rejected

    # 保留最后一次出现的同对象动作
    seen: Dict[Tuple, Dict] = {}
    order: List[Tuple] = []
    for a in actions:
        if not isinstance(a, dict):
            rejected.append({"action": a, "reason": "动作不是字典"})
            continue
        params = a.get("params") or {}
        key = (a.get("action"), a.get("target"), params.get("country"))
        if key not in seen:
            order.append(key)
        else:
            rejected.append({
                "action": a,
                "reason": f"与后续动作针对同一对象重复（{_describe(a)}），已用后者覆盖",
            })
        seen[key] = a

    return [seen[k] for k in order], rejected
