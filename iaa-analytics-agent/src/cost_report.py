# -*- coding: utf-8 -*-
"""
Agent 运行成本报表。

============================================================
这个脚本回答三个问题
============================================================
1. 跑一次分析多少钱？（平均 / 最贵）
2. 钱花在哪？（输入 vs 输出，固定开销 vs 历史累积）
3. 优化有没有效果？（前后对比，看 input_per_call 和缓存命中率）

============================================================
为什么值得单独做一个脚本
============================================================
「大概几毛钱」是个经不起追问的回答。真实汇报里会被继续问：
    - 一天跑几次？一个月多少？
    - 换个更便宜的模型能省多少？
    - 为什么这次比上次贵一倍？

有这张表就能直接答。而且 input_per_call（平均每次调用的输入 token）
是**上下文是否膨胀**的直接指标——它涨了说明 Agent 在查更多东西，
或者工具返回的数据太大，这是优化 prompt 和工具返回格式的入口。

============================================================
用法
============================================================
    cd iaa-analytics-agent
    venv/Scripts/python.exe src/cost_report.py
    venv/Scripts/python.exe src/cost_report.py --limit 50
    venv/Scripts/python.exe src/cost_report.py --json      # 给程序消费

环境变量（可选，覆盖默认价）：
    LLM_INPUT_PRICE_PER_MTOK / LLM_OUTPUT_PRICE_PER_MTOK / LLM_USD_CNY
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))

import dim_scd
import llm_cost


def collect(limit: int = 100) -> List[Dict[str, Any]]:
    """取有成本记录的运行。

    过滤掉 usage 为空的记录：MCP 提交建议单这类入口不调 LLM，
    把它们算进去会把平均值拉低到没有意义。
    """
    runs = dim_scd.list_agent_runs(limit=limit)
    out = []
    for r in runs:
        u = r.get("usage") or {}
        if not u or not u.get("calls"):
            continue
        out.append({
            "run_id": r.get("run_id"),
            "run_time": r.get("run_time", ""),
            "status": r.get("status", ""),
            "actions": len(r.get("actions") or []),
            "usage": u,
        })
    return out


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总统计。返回的字段全部可直接打印或序列化。"""
    if not rows:
        return {"runs": 0}

    stats = [r["usage"] for r in rows]

    total_calls = sum(s.get("calls", 0) for s in stats)
    total_prompt = sum(s.get("prompt_tokens", 0) for s in stats)
    total_completion = sum(s.get("completion_tokens", 0) for s in stats)
    total_hit = sum(s.get("cache_hit_tokens", 0) for s in stats)
    total_cost = sum(s.get("cost_usd", 0.0) for s in stats)

    costs = [s.get("cost_usd", 0.0) for s in stats]
    per_call_inputs = [s.get("calls", 1) for s in stats]

    return {
        "runs": len(rows),
        "total_calls": total_calls,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "cache_hit_rate": round(total_hit / total_prompt, 4) if total_prompt else 0.0,
        "total_cost_usd": round(total_cost, 6),
        "total_cost_cny": round(total_cost * llm_cost._prices()[2], 6),
        "avg_cost_usd": round(total_cost / len(stats), 6),
        "max_cost_usd": round(max(costs), 6),
        "min_cost_usd": round(min(costs), 6),
        "avg_rounds": round(sum(per_call_inputs) / len(per_call_inputs), 2),
        "avg_input_per_call": round(total_prompt / total_calls, 1) if total_calls else 0.0,
        "input_share": round(total_prompt / (total_prompt + total_completion), 4)
                       if (total_prompt + total_completion) else 0.0,
    }


def print_report(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("没有找到带成本记录的 Agent 运行。")
        print("先跑一次：venv/Scripts/python.exe src/demo_multi_round.py")
        return

    s = summarize(rows)

    print("=" * 68)
    print("Agent 运行成本报表")
    print("=" * 68)
    print(f"样本            {s['runs']} 次运行 / {s['total_calls']} 次 LLM 调用")
    print(f"平均轮数        {s['avg_rounds']} 轮/次")
    print()
    print(f"单次成本        平均 ${s['avg_cost_usd']:.4f}"
          f"（¥{s['avg_cost_usd'] * llm_cost._prices()[2]:.4f}）"
          f"  最贵 ${s['max_cost_usd']:.4f}"
          f"  最省 ${s['min_cost_usd']:.4f}")
    print(f"累计成本        ${s['total_cost_usd']:.4f}"
          f"（¥{s['total_cost_cny']:.4f}）")
    print()
    print(f"token 结构      输入 {s['total_prompt_tokens']:,}"
          f" / 输出 {s['total_completion_tokens']:,}"
          f"  → 输入占 {s['input_share']:.1%}")
    print(f"缓存命中率      {s['cache_hit_rate']:.1%}")
    print(f"平均每轮输入    {s['avg_input_per_call']:,.0f} token")
    print()

    # 明细
    print("-" * 68)
    print(f"{'run_id':>7} {'时间':<20} {'轮数':>4} {'输入':>9} {'输出':>6} {'成本$':>9}")
    print("-" * 68)
    for r in rows[:20]:
        u = r["usage"]
        print(f"{r['run_id']:>7} {r['run_time']:<20} {u.get('calls', 0):>4} "
              f"{u.get('prompt_tokens', 0):>9,} {u.get('completion_tokens', 0):>6,} "
              f"{u.get('cost_usd', 0):>9.4f}")
    if len(rows) > 20:
        print(f"... 另有 {len(rows) - 20} 条未显示（--limit 调大）")

    print()
    print("=" * 68)
    print("读法")
    print("=" * 68)
    print("  输入占比高（通常 >90%）是正常的：每轮都要重发完整历史。")
    print("  想降本，优先压缩**输入**，方向有三个：")
    print("    1. 缩短系统提示（稳定 → 更容易命中 prompt cache）")
    print("    2. 工具返回值瘦身（只回必要的列，别回整张表）")
    print("    3. 减少轮数（把常用查询合并成一个批量工具）")
    print("  输出 token 通常只占总成本的一小部分，压输出收益有限。")
    print()
    print("  判断是否退化：看「平均每轮输入」。它持续上涨说明上下文在膨胀，")
    print("  常见原因是某次改动让工具返回了更多字段。")


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent 运行成本报表")
    ap.add_argument("--limit", type=int, default=100, help="取最近多少条运行记录")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给程序消费）")
    args = ap.parse_args()

    rows = collect(args.limit)

    if args.json:
        print(json.dumps({"summary": summarize(rows), "runs": rows},
                         ensure_ascii=False, indent=2, default=str))
    else:
        print_report(rows)


if __name__ == "__main__":
    main()
