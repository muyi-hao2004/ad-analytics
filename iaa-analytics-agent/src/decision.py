# -*- coding: utf-8 -*-
"""判定器：把「一个数字」变成「一个结论」，并且保证结论不乱跳。

================================================================================
为什么需要单独一层，而不是让 LLM 自己判
================================================================================
原来 LLM 每轮拿到一份快照，自己对着阈值判「该砍 / 该观察」。
数据几乎没动，它也可能换一个说法 —— 因为大模型每次推理都是独立采样的，
你没法用 prompt 要求它「和昨天保持一致」（它根本没有昨天的记忆）。

正确做法是把「判定」从「解释」里拆出来：

    判定层（本文件）：确定性规则，纯函数，输入相同则输出必然相同
    解释层（LLM）：拿到已经判好的结论，负责讲清原因、给出可执行的动作

这样一来：
  - 抖动在规则层就被消灭了，不依赖 LLM 的稳定性
  - 判定结果可单测（不需要 API key，不需要 Spark）
  - 出了争议能复现：同一个值喂进去，永远得到同一个结论

================================================================================
两个机制：死区（band）+ 连续确认（min_consecutive_days）
================================================================================
① 死区：跨边界时不能只看一个点，要留一条带子
       阈值 0.50，值在 0.499 / 0.501 之间来回跳时，裸阈值会天天改判。
       死区宽度 0.04 时，0.48~0.52 之间「保持上次结论，不动」。
       物理类比就是空调恒温器：设 26℃ 但 25℃ 才启动、27℃ 才停，
       否则在 26℃ 上下会疯狂开关。

② 连续确认：单个日子的数据是噪声，必须连续 N 天都指向同一方向才改判
       这个防的是「死区之外但只出现一天」的情况，比如某天投放事故
       导致 ROI 腰斩，第二天恢复 —— 不该据此砍渠道。
       注意：改判（无论变好变坏）都需要确认，不能只对变坏设门槛，
       否则一次侥幸反弹就会让你加预算。

================================================================================
状态的排序
================================================================================
    渠道：strong(3) > watch(2) > weak(1) > critical(0)
    素材：scale(2)  > hold(1)  > cut(0)

rank 只用于比较「谁比谁好」，具体边界值仍然来自 metrics.yaml。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# 各对象类型的状态阶梯：从好到差，附该状态的**下界**（含）。
# 最后一项的 base 是「低于所有下界」时的状态。
#
# 这里是**兜底默认值**，真正生效的数值从 metrics.yaml 的 decision_thresholds 读。
# 留一份默认值是为了让本文件能单独 import 做单测（不依赖 YAML 存在）。
# 但线上口径必须以 YAML 为准 —— 否则又变成「阈值散落在代码里，改一处漏一处」。
DEFAULT_LADDERS: Dict[str, Tuple[List[Tuple[str, float]], str]] = {
    "sub_channel": ([("strong", 0.70), ("watch", 0.62), ("weak", 0.50)], "critical"),
    "creative": ([("scale", 0.90), ("hold", 0.50)], "cut"),
}

_LADDER_CACHE: Optional[Dict[str, Tuple[List[Tuple[str, float]], str]]] = None


def _load_ladders() -> Dict[str, Tuple[List[Tuple[str, float]], str]]:
    """从语义层读阈值，拼成状态阶梯。读不到就用默认值（并保持可测）。"""
    global _LADDER_CACHE
    if _LADDER_CACHE is not None:
        return _LADDER_CACHE
    ladders = dict(DEFAULT_LADDERS)
    try:
        import semantic_layer  # 延迟导入：让 decision.py 能独立 import

        th = semantic_layer.get_thresholds()
        if isinstance(th, dict) and "error" not in th:
            ch, cr = th.get("channel") or {}, th.get("creative") or {}
            if ch.get("strong") and ch.get("watch") and ch.get("critical"):
                ladders["sub_channel"] = (
                    [("strong", float(ch["strong"])),
                     ("watch", float(ch["watch"])),
                     ("weak", float(ch["critical"]))],
                    "critical",
                )
            if cr.get("scale") and cr.get("cut"):
                ladders["creative"] = (
                    [("scale", float(cr["scale"])), ("hold", float(cr["cut"]))],
                    "cut",
                )
    except Exception:  # noqa: BLE001
        pass
    _LADDER_CACHE = ladders
    return ladders

# 中文名，写日志和给 LLM 看时用
VERDICT_CN = {
    "strong": "达标可加预算",
    "watch": "临界观察",
    "weak": "不达标需优化",
    "critical": "严重建议降预算",
    "scale": "建议加预算",
    "hold": "保持观察",
    "cut": "建议关停",
}


def _ladder(obj_type: str) -> Tuple[List[Tuple[str, float]], str]:
    return _load_ladders().get(obj_type, DEFAULT_LADDERS["sub_channel"])


def _ranks(obj_type: str) -> Dict[str, int]:
    bands, base = _ladder(obj_type)
    r = {base: 0}
    for i, (name, _) in enumerate(reversed(bands), start=1):
        r[name] = i
    return r


def classify(value: Optional[float], obj_type: str) -> Optional[str]:
    """裸判定：只按阈值，不考虑历史。value 为 None 时返回 None（不知道，不是差）。"""
    if value is None:
        return None
    bands, base = _ladder(obj_type)
    for name, lo in bands:
        if value >= lo:
            return name
    return base


def _lower_bound(state: str, obj_type: str) -> float:
    bands, base = _ladder(obj_type)
    for name, lo in bands:
        if name == state:
            return lo
    return 0.0  # base 状态没有下界，用 0 代替


def _boundary(a: str, b: str, obj_type: str) -> float:
    """两个状态之间的那条分界线。

    取两者下界的较大值：watch(0.62) 与 weak(0.50) 之间是 0.62，
    critical(0) 与 weak(0.50) 之间是 0.50。
    """
    return max(_lower_bound(a, obj_type), _lower_bound(b, obj_type))


def _consecutive_days(series: Sequence[Tuple[str, Optional[float]]],
                      state: str, obj_type: str) -> int:
    """从最后一天往前数，候选状态连续出现了几天。

    series 是按日期升序的 (date, value)。遇到 None（指标不可观测）
    就停止计数 —— 「不知道」不能算作「满足条件」，也不能算作「不满足」，
    它是链路的断点。
    """
    n = 0
    for _, v in reversed(list(series)):
        if v is None:
            break
        if classify(v, obj_type) == state:
            n += 1
        else:
            break
    return n


def evaluate(obj_type: str, series: Sequence[Tuple[str, Optional[float]]],
             previous: Optional[str] = None,
             band_width: float = 0.04,
             min_days: int = 3,
             enabled: bool = True) -> Dict[str, Any]:
    """给一条时间序列，产出最终判定。

    Args:
        obj_type: sub_channel / creative
        series: 按日期升序的 (date, value)，value 为 None 表示不可观测
        previous: 上次的判定结果（来自 decision_log），没有就传 None
        band_width: 死区总宽度（上下各一半）
        min_days: 改判需要的连续确认天数
        enabled: 关掉就退化成裸阈值（用来对比「加了机制到底有没有用」）

    Returns:
        {
          "value", "candidate", "verdict", "previous", "changed",
          "blocked_by", "days_sustained", "reason"
        }
        blocked_by: None / "dead_band" / "needs_confirm" / "no_data"
    """
    out: Dict[str, Any] = {
        "value": None, "candidate": None, "verdict": None, "previous": previous,
        "changed": False, "blocked_by": None, "days_sustained": 0, "reason": "",
    }
    if not series:
        out["blocked_by"] = "no_data"
        out["reason"] = "没有可用数据，不下结论"
        out["verdict"] = previous
        return out

    last_date, value = series[-1]
    out["value"] = None if value is None else round(float(value), 4)
    out["day"] = last_date

    if value is None:
        out["blocked_by"] = "no_data"
        out["reason"] = f"{last_date} 该指标不可观测（观察窗口未走完），不参与判定"
        out["verdict"] = previous
        return out

    cand = classify(value, obj_type)
    out["candidate"] = cand
    out["days_sustained"] = _consecutive_days(series, cand, obj_type)

    if previous is None:
        # 第一次判定：直接采用，但附上连续天数，让日志能看出这个结论有多稳
        out["verdict"] = cand
        out["reason"] = (
            f"首次判定 {value:.4f} → {VERDICT_CN.get(cand, cand)}"
            f"（该状态已连续 {out['days_sustained']} 天）"
        )
        return out

    if cand == previous:
        out["verdict"] = cand
        out["reason"] = f"{value:.4f} 仍在「{VERDICT_CN.get(cand, cand)}」区间，结论不变"
        return out

    if not enabled:
        out["verdict"] = cand
        out["changed"] = True
        out["reason"] = f"{value:.4f} 跨过阈值，「{VERDICT_CN.get(previous, previous)}」→「{VERDICT_CN.get(cand, cand)}」（未启用迟滞）"
        return out

    # ---- 机制一：死区 ----
    b = _boundary(previous, cand, obj_type)
    half = band_width / 2.0
    if abs(value - b) < half:
        out["verdict"] = previous
        out["blocked_by"] = "dead_band"
        out["reason"] = (
            f"{value:.4f} 落在死区 {b - half:.2f}~{b + half:.2f} 内，"
            f"维持上次结论「{VERDICT_CN.get(previous, previous)}」，避免抖动"
        )
        return out

    # ---- 机制二：连续确认 ----
    if out["days_sustained"] < min_days:
        out["verdict"] = previous
        out["blocked_by"] = "needs_confirm"
        out["reason"] = (
            f"{value:.4f} 指向「{VERDICT_CN.get(cand, cand)}」，但只连续 "
            f"{out['days_sustained']} 天（需 {min_days} 天），暂不改判，维持"
            f"「{VERDICT_CN.get(previous, previous)}」"
        )
        return out

    out["verdict"] = cand
    out["changed"] = True
    out["reason"] = (
        f"{value:.4f} 连续 {out['days_sustained']} 天满足「{VERDICT_CN.get(cand, cand)}」，"
        f"由「{VERDICT_CN.get(previous, previous)}」改判"
    )
    return out


def evaluate_all(obj_type: str, series_by_obj: Dict[str, List[Tuple[str, Optional[float]]]],
                 previous_by_obj: Dict[str, str],
                 band_width: float = 0.04, min_days: int = 3,
                 enabled: bool = True) -> List[Dict[str, Any]]:
    """批量判定，并按「改判的排前面」排序 —— 需要人看的永远是变化的那几个。"""
    rows = []
    for obj_id, series in series_by_obj.items():
        r = evaluate(obj_type, series, previous_by_obj.get(obj_id),
                     band_width=band_width, min_days=min_days, enabled=enabled)
        r["obj_id"] = obj_id
        r["obj_type"] = obj_type
        rows.append(r)
    rows.sort(key=lambda x: (not x["changed"], x["value"] if x["value"] is not None else 9e9))
    return rows


if __name__ == "__main__":
    # 自检：三个场景，直接看输出
    print("=== 场景 1：值在阈值附近抖动（0.499 / 0.501 / 0.498）===")
    prev = None
    for d, v in [("10-20", 0.505), ("10-21", 0.499), ("10-22", 0.501), ("10-23", 0.498)]:
        r = evaluate("creative", [(d, v)], prev)
        print(f"  {d} {v:.3f} → {r['verdict']:8s} blocked={r['blocked_by']} {r['reason']}")
        prev = r["verdict"]

    print("\n=== 场景 2：真实恶化（连续 4 天 0.44）===")
    series = [("10-18", 0.62), ("10-19", 0.58), ("10-20", 0.46),
              ("10-21", 0.44), ("10-22", 0.45), ("10-23", 0.43)]
    print(f"  首次：{evaluate('creative', series[:3], None)['verdict']}")
    print(f"  之后：{evaluate('creative', series, 'hold')['reason']}")

    print("\n=== 场景 3：单日事故后恢复 ===")
    series = [("10-20", 0.70), ("10-21", 0.28), ("10-22", 0.71)]
    print(f"  {evaluate('creative', series, 'hold')['reason']}")
