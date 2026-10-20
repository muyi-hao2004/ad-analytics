"""
LLM 调用计量与成本折算。

============================================================
为什么单独一个模块，而不是在 agent.py 里加两行
============================================================
因为「怎么算」这件事本身有争议，需要单独可测、可改。

关键认知：**Agent 的成本不是「单价 × 调用次数」。**

多轮 Function Calling 的输入 token 是**超线性**增长的：
    第 1 轮发送：system + 快照              ≈ 8k token
    第 2 轮发送：上面全部 + 工具调用 + 结果   ≈ 9.5k
    第 3 轮发送：再叠加                      ≈ 11k
    ...
每轮都要把完整历史重发一遍，所以 6 轮下来的**累计输入**
约等于单轮的 4~5 倍，而不是 1 倍。轮数一多，输入成本按三角数上升。

所以行业里区分两个口径：
    - token 单价（cost per token）：价目表上那个数
    - 任务成本（cost per task）：跑完一次分析实际花了多少

两者可以**背离**：单价降了，但如果 Agent 学会「多查几次更保险」，
轮数变多、上下文变长，任务成本反而涨。2026-10 的行业数据里
Gartner 预测到 2028 年单个 agentic workflow 推理成本可能涨 5 倍以上，
就是这个机制——不是单价涨，是调用结构和上下文变复杂。

本模块只做计量，不做预算限制（那是 guardrails 的职责，边界不同：
guardrails 管「能不能做」，这里管「花了多少」）。

============================================================
价格从哪来
============================================================
不硬编码在代码里，走环境变量，因为价格会变且各渠道不同。
默认值是 DeepSeek 公开价的数量级参考，**实际请以账单为准**：
    LLM_INPUT_PRICE_PER_MTOK   输入 USD / 百万 token
    LLM_OUTPUT_PRICE_PER_MTOK  输出 USD / 百万 token
    LLM_USD_CNY                汇率
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

# 默认价：仅作数量级参考，通过 .env 覆盖
DEFAULT_INPUT_PRICE_PER_MTOK = 0.15
DEFAULT_OUTPUT_PRICE_PER_MTOK = 0.60
DEFAULT_USD_CNY = 7.1

# 空累加器。用 dict 而不是 dataclass：它要直接 JSON 序列化进 trace 和数据库
EMPTY: Dict[str, int] = {
    "calls": 0,                 # LLM 调用次数（≈ 轮数）
    "prompt_tokens": 0,         # 累计输入 token
    "completion_tokens": 0,     # 累计输出 token
    "total_tokens": 0,
    "cache_hit_tokens": 0,      # 命中 prompt cache 的输入 token（便宜很多）
}


def _prices() -> Tuple[float, float, float]:
    """读价格配置，非法值回退到默认（不要让配置写错导致整个 Agent 挂掉）。"""
    def _f(key: str, default: float) -> float:
        try:
            v = float(os.getenv(key, "") or default)
            return v if v >= 0 else default
        except (TypeError, ValueError):
            return default

    return (
        _f("LLM_INPUT_PRICE_PER_MTOK", DEFAULT_INPUT_PRICE_PER_MTOK),
        _f("LLM_OUTPUT_PRICE_PER_MTOK", DEFAULT_OUTPUT_PRICE_PER_MTOK),
        _f("LLM_USD_CNY", DEFAULT_USD_CNY),
    )


def extract_usage(resp: Any) -> Dict[str, int]:
    """从一次 LLM 响应里取出 token 用量。

    为什么用 getattr 层层兜底而不是直接 resp.usage：
        1. 部分网关（含本地推理、代理）不返回 usage，字段直接缺失
        2. 测试用的 FakeClient 也不会有 usage
        少统计一次成本可以接受，因为拿不到 usage 就整个异常，不可接受。

    Args:
        resp: OpenAI SDK 的 ChatCompletion 响应对象

    Returns:
        用量 dict；拿不到时返回全 0（calls 记 1，调用本身发生了是真的）
    """
    one = dict(EMPTY)
    one["calls"] = 1

    usage = getattr(resp, "usage", None)
    if usage is None:
        return one

    # OpenAI 标准字段
    one["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
    one["completion_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)

    total = getattr(usage, "total_tokens", None)
    one["total_tokens"] = int(total) if total else one["prompt_tokens"] + one["completion_tokens"]

    # DeepSeek 系特有的缓存字段：拆成 hit / miss，hit 部分单价低一个量级
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if hit is None:
        # 有的网关把明细放在 prompt_tokens_details 里
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            hit = getattr(details, "cached_tokens", None)
    one["cache_hit_tokens"] = int(hit or 0)

    return one


def merge(total: Dict[str, int], one: Dict[str, int]) -> Dict[str, int]:
    """累加两次用量，返回新 dict（不改原对象，避免调用方共享状态）。"""
    out = dict(total)
    for k in EMPTY:
        out[k] = int(out.get(k, 0)) + int(one.get(k, 0))
    return out


def merge_all(items) -> Dict[str, int]:
    """累加一组用量。"""
    total = dict(EMPTY)
    for it in items or []:
        total = merge(total, it)
    return total


def finalize(usage: Dict[str, int], model: str = "") -> Dict[str, Any]:
    """把 token 用量折算成钱，返回可落库 / 可打印的扁平结构。

    Args:
        usage: merge 累加出来的用量
        model: 模型名，仅作记录（当前价格按全局配置，不按模型区分）

    Returns:
        {
          "model", "calls", "prompt_tokens", "completion_tokens", "total_tokens",
          "cache_hit_tokens", "cache_hit_rate",
          "input_per_call",          # 平均每次调用的输入 token —— 上下文膨胀的直接指标
          "cost_usd", "cost_cny"
        }

    Raises:
        无。价格配置非法时回退默认值，不抛异常。
    """
    in_price, out_price, fx = _prices()

    prompt = int(usage.get("prompt_tokens", 0))
    completion = int(usage.get("completion_tokens", 0))
    hit = int(usage.get("cache_hit_tokens", 0))
    calls = int(usage.get("calls", 0))

    # 缓存命中的部分按输入价的 1/10 计（DeepSeek 公开口径的近似）
    # 拿不到 hit 数据时按全价算，属于**高估**——成本估算宁可偏高，不要偏低
    hit = min(hit, prompt)
    miss = prompt - hit
    cost_usd = (miss / 1_000_000) * in_price \
        + (hit / 1_000_000) * in_price * 0.1 \
        + (completion / 1_000_000) * out_price

    return {
        "model": model,
        "calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens", 0)),
        "cache_hit_tokens": hit,
        "cache_hit_rate": round(hit / prompt, 4) if prompt else 0.0,
        "input_per_call": round(prompt / calls, 1) if calls else 0.0,
        "cost_usd": round(cost_usd, 6),
        "cost_cny": round(cost_usd * fx, 6),
    }


def format_line(stat: Dict[str, Any]) -> str:
    """一行摘要，给控制台和日志用。"""
    return (
        f"{stat.get('calls', 0)} 次调用 / "
        f"in {stat.get('prompt_tokens', 0):,} + out {stat.get('completion_tokens', 0):,} tok / "
        f"缓存命中 {stat.get('cache_hit_rate', 0):.1%} / "
        f"${stat.get('cost_usd', 0):.4f} (¥{stat.get('cost_cny', 0):.4f})"
    )
