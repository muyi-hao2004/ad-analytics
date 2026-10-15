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
        metric: 指标 key，如 d7_roas / cpi / buy_ctr / monetization_ecpm

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


if __name__ == "__main__":
    # 自检：打印全部指标概览 + 单个指标完整定义
    print("=== 业务背景 ===")
    print(get_business_context())
    print("\n=== 全部指标概览 ===")
    for m in list_metrics():
        print(f"  [{m['track']}] {m['metric']:22s} {m['name']}")
    print("\n=== d7_roas 完整定义 ===")
    print(get_metric_definition("d7_roas"))
