"""
语义层加载器（Semantic Layer）

读 metrics.yaml 里的指标口径定义，提供给 Agent / 调用方「先查口径、再算数」。
这是 2026 数仓 Agent 的主流做法：把业务口径声明式存起来，让模型不再对着裸表猜。

与 agent.py 的关系：
  - agent.py 的 get_metric_definition 工具调这里，让 LLM 决策前能拿到指标的
    标准定义 / 公式 / 依赖字段 / 口径备注。
  - 也可以被 MCP Server 直接封装成工具，供任意 Agent 调用。

设计原则：
  - 只读不写：口径是业务资产，这里只提供查询，不提供修改
  - 容错：YAML 缺失或格式错误时，返回明确的 error，而不是让调用方崩掉
"""

from typing import Dict, Any, List, Optional
from pathlib import Path
import yaml


_METRICS_PATH = Path(__file__).parent / "metrics.yaml"

# 模块级缓存：YAML 只读一次，避免每次查询都重新 IO + parse
_CACHE: Optional[Dict[str, Any]] = None


def load_metrics() -> Dict[str, Any]:
    """加载整个 metrics.yaml（带缓存）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _METRICS_PATH.exists():
        raise FileNotFoundError(f"语义层文件不存在: {_METRICS_PATH}")
    with open(_METRICS_PATH, "r", encoding="utf-8") as f:
        _CACHE = yaml.safe_load(f)
    return _CACHE


def get_metric_definition(metric: str) -> Dict[str, Any]:
    """
    查单个指标的完整口径定义。

    Args:
        metric: 指标 key，如 d7_roi / cpi / buy_ctr / monetization_ecpm / retention_dn

    Returns:
        {"metric": ..., "definition": {...}} 或 {"error": ...}
    """
    try:
        data = load_metrics()
    except Exception as e:
        return {"error": f"语义层加载失败: {e}"}

    metrics = data.get("metrics", {})
    if metric not in metrics:
        available = list(metrics.keys())
        return {
            "error": f"指标 {metric} 不存在",
            "available_metrics": available,
        }
    return {"metric": metric, **metrics[metric]}


def list_metrics() -> List[Dict[str, Any]]:
    """列出全部指标（key + 名称 + 一句话定义），供 Agent 概览。"""
    try:
        data = load_metrics()
    except Exception as e:
        return [{"error": f"语义层加载失败: {e}"}]
    return [
        {
            "metric": k,
            "name": v.get("name", ""),
            "definition": v.get("definition", ""),
            "track": v.get("track", ""),
        }
        for k, v in data.get("metrics", {}).items()
    ]


def get_business_context() -> Dict[str, Any]:
    """返回业务背景（目标市场、D7 ROAS 目标线等），供 Agent 判断基准。"""
    try:
        return load_metrics().get("business", {})
    except Exception as e:
        return {"error": f"语义层加载失败: {e}"}


# ============================================================
# 以下三组接口是 v2 新增：让「阈值」和「质量规则」也只有一处定义
# ============================================================
# 为什么重要：如果 agent.py 里写 0.70、guardrails.py 里写 0.68、
# 报表里写 0.72，三个数字就会各自漂移，而且没人说得清哪个才是对的口径。
# 抽到这里之后，「改阈值」变成一次 YAML 修改 + 一次 diff review，
# 而不是在三个文件里 grep 魔法数字。
# ============================================================


def get_thresholds(kind: Optional[str] = None) -> Dict[str, Any]:
    """返回决策阈值（Agent 判「砍 / 保 / 加」的分界线）。

    Args:
        kind: "channel" / "creative" / "cpi"，None 表示返回全部。

    Returns:
        阈值字典。kind 指定的键不存在时返回 {"error": ...}。
    """
    try:
        data = load_metrics().get("decision_thresholds", {})
    except Exception as e:
        return {"error": f"语义层加载失败: {e}"}

    if kind is None:
        return data
    if kind not in data:
        return {"error": f"阈值类型 {kind} 不存在", "available": list(data.keys())}
    return data[kind]


def get_hysteresis() -> Dict[str, Any]:
    """返回判定稳定机制的参数（死区宽度、连续确认天数）。

    为什么和 get_thresholds 分开：阈值回答「多差算差」，
    这组参数回答「结论要多稳定才允许改口」。两者会被不同的模块消费
    （decision.py 读后者），混在一起会让调用方以为都能直接用。
    """
    try:
        th = load_metrics().get("decision_thresholds", {})
    except Exception as e:
        return {"error": f"语义层加载失败: {e}"}
    h = th.get("hysteresis")
    if not h:
        # 缺配置不等于禁用：给一套保守默认值，让判定仍然稳定。
        # 返回 error 会让调用方退化成裸阈值，那才是真正危险的降级。
        return {"enabled": True, "band_width": 0.04,
                "min_consecutive_days": 3, "source": "内置默认（YAML 未配置）"}
    return dict(h)


def get_quality_rules(table: Optional[str] = None) -> Dict[str, Any]:
    """返回数据质量断言规则（DAG 质量门禁读它来决定「什么算坏数据」）。

    Args:
        table: 表名（如 ads_daily_detail）。None 表示返回全部表。

    Returns:
        {表名: {"grain": ..., "assertions": [...]}} 或 {"error": ...}。
    """
    try:
        data = load_metrics().get("data_quality", {})
    except Exception as e:
        return {"error": f"语义层加载失败: {e}"}

    if table is None:
        return data
    if table not in data:
        return {"error": f"表 {table} 没有质量规则", "available": list(data.keys())}
    return data[table]


def get_maturity_rule() -> Dict[str, Any]:
    """返回 cohort 成熟度规则。

    这条规则决定了「哪些 D7 指标可以拿来下结论」。用错它的代价是：
    把「数据还没长出来」误读成「渠道变差了」，进而做出停投的错误决策。

    Returns:
        成熟度规则字典；缺失时返回保守默认值（day_window=7），
        而不是返回空 —— 宁可多过滤，不能少过滤。
    """
    try:
        rule = load_metrics().get("maturity", {})
    except Exception as e:
        return {"error": f"语义层加载失败: {e}", "day_window": 7}

    if not rule:
        return {"day_window": 7, "field": "is_mature_d7", "_warning": "metrics.yaml 缺 maturity 段，用默认值"}
    return rule


if __name__ == "__main__":
    # 自检：打印全部指标概览 + 单个指标完整定义
    print("=== 业务背景 ===")
    print(get_business_context())
    print("\n=== 全部指标概览 ===")
    for m in list_metrics():
        print(f"  [{m['track']}] {m['metric']:22s} {m['name']}")
    print("\n=== d7_roi 完整定义 ===")
    print(get_metric_definition("d7_roi"))
    print("\n=== 决策阈值（channel）===")
    print(get_thresholds("channel"))
    print("\n=== 成熟度规则 ===")
    print({k: v for k, v in get_maturity_rule().items() if k != "measured_effect"})
    print("\n=== 质量规则覆盖的表 ===")
    for tbl, rule in get_quality_rules().items():
        n = len(rule.get("assertions", []))
        print(f"  {tbl:28s} {n} 条断言")
