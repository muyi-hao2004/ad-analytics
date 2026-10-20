# -*- coding: utf-8 -*-
"""
评测入口：跑 golden set，输出三层指标。

============================================================
怎么用
============================================================
    cd iaa-analytics-agent

    python eval/run_eval.py                 # 跑全部场景（会真实调用 LLM）
    python eval/run_eval.py --dry-run       # 只校验场景定义，不调 LLM、不花钱
    python eval/run_eval.py --only S01,S03  # 只跑指定场景
    python eval/run_eval.py --json out.json # 结果落文件，便于前后版本对比

============================================================
为什么值得花这个钱
============================================================
一次完整评测 = 8 个场景 × 约 $0.002 ≈ **$0.016（一毛多）**。
花一毛多能回答「我的 Agent 在关键陷阱上会不会犯错」，
这是项目里性价比最高的一笔支出。

而且它给了一个**回归基线**：以后改 prompt、换模型、加工具，
跑一遍就知道是变好还是变坏。没有这个基线，所有「我觉得优化了」
都只是感觉。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

EVAL_DIR = Path(__file__).parent.resolve()
SRC_DIR = EVAL_DIR.parent / "src"
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(SRC_DIR))

import judge as judge_mod          # noqa: E402
import scenarios                   # noqa: E402

import agent                       # noqa: E402


# ============================================================
# 假工具注入
# ============================================================

# 全部读工具。评测时必须**逐个处理**，不能只替换场景用到的那几个。
READ_TOOLS = ["get_metric", "get_logs", "get_top_history",
              "get_trend_with_ops", "search_knowledge", "get_metric_definition"]

# 这些工具查的是**静态口径**（metrics.yaml），与具体数据无关，
# 保留真实实现——让 LLM 能正常查口径，不引入不确定性。
STATIC_TOOLS = {"get_metric_definition"}


def _empty_stub(name: str):
    """未提供数据的工具的桩实现：返回空结果，绝不落到真实数据库。

    为什么必须桩掉而不是「不替换」：
        场景只定义了 get_metric 的假数据，但 get_logs / get_top_history
        会去读真实的 dim.db，返回与场景完全矛盾的内容（比如场景说
        ROI 11%，真实库说 87%）。LLM 面对两套冲突数据会反复查询，
        最后耗尽轮数不提交决策——现象是「Agent 很笨」，
        真实原因是**评测环境没隔离干净**。
        同一个场景跑两次结果可能不同，这种评测等于没做。
    """
    def stub(**kwargs):
        return {"data": [], "rows": [], "logs": [],
                "message": f"本次评测未为 {name} 提供数据（评测环境已与真实库隔离）"}
    return stub


class ToolOverride:
    """临时替换 agent 的全部读工具，保证评测与真实数据隔离。

    原理：`_build_tool_registry()` 每次调用时才从模块全局读 `_tool_get_metric`
    这些名字，所以替换模块属性就能改变实际行为——不需要改生产代码。

    做成上下文管理器而不是简单赋值：一个场景跑完必须恢复，
    否则后面的场景会串用前一个场景的假数据，结果全部失真
    （这种错很难发现，因为报表上还是「有结果」的）。
    """

    def __init__(self, tools: Dict[str, Any]):
        self.tools = tools
        self._saved: Dict[str, Any] = {}

    def __enter__(self):
        for name in READ_TOOLS:
            attr = f"_tool_{name}"
            if not hasattr(agent, attr):
                continue
            self._saved[attr] = getattr(agent, attr)
            if name in STATIC_TOOLS:
                continue                      # 静态口径，保留真实实现
            setattr(agent, attr, self.tools.get(name) or _empty_stub(name))
        return self

    def __exit__(self, *exc):
        for attr, fn in self._saved.items():
            setattr(agent, attr, fn)
        return False


# ============================================================
# 单个场景
# ============================================================

def run_case(case: scenarios.Case, max_rounds: int = 8,
             repeat: int = 1) -> Dict[str, Any]:
    """跑一个场景并判定。

    repeat > 1 时跑多次，并**取最严重的那次**作为结论。

    为什么取最严重而不是取多数：
        temperature 不是 0，同一个场景两次跑可能一边 PASS 一边删了渠道。
        「3 次里 2 次没犯错的 Agent」在业务上仍然是不能上线的——
        因为它有 1/3 的概率造成资损。
        评测要回答的是「最坏情况下会怎样」，不是「平均下来还行」。
    """
    attempts: List[Dict[str, Any]] = []
    for _ in range(max(1, repeat)):
        with ToolOverride(case.tools):
            decision = agent.run_llm_loop(case.snapshot, None, max_rounds=max_rounds)
        attempts.append(judge_mod.judge(case, decision))

    worst = max(attempts, key=lambda r: judge_mod.SEVERITY.get(r.get("failure_type") or "", 0))
    passes = len([a for a in attempts if a["verdict"] == "pass"])
    worst["attempts"] = len(attempts)
    worst["passes"] = passes
    worst["pass_rate"] = round(passes / len(attempts), 2)
    # 成本要算全部尝试，不是只算最差那一次
    if len(attempts) > 1:
        worst["usage"] = {
            "cost_usd": round(sum((a.get("usage") or {}).get("cost_usd", 0) for a in attempts), 6),
            "total_tokens": sum((a.get("usage") or {}).get("total_tokens", 0) for a in attempts),
            "calls": sum((a.get("usage") or {}).get("calls", 0) for a in attempts),
        }
    return worst


def dry_run(cases: List[scenarios.Case]) -> int:
    """只校验场景定义，不调 LLM。

    为什么需要这个：场景写错（字段名拼错、accept 写了个不存在的动作）
    会表现为「Agent 表现很差」，而不是「场景有 bug」。
    先跑 dry-run 能把这两类问题分开。
    """
    problems = 0
    valid_actions = {"delete_sub_channel", "adjust_budget", "adjust_target"}
    required = {"素材表现", "子渠道表现", "当前配置", "最近操作", "业务目标"}

    print("=" * 70)
    print("场景校验（dry-run，不调用 LLM）")
    print("=" * 70)
    for c in cases:
        errs = []
        if c.category not in ("must_hold", "must_act", "known_gap"):
            errs.append(f"category 非法: {c.category}")
        if c.category == "must_hold" and c.accept:
            errs.append("must_hold 场景不应有 accept 规则")
        if c.category == "must_act" and not c.accept:
            errs.append("must_act 场景必须给出 accept 规则")
        missing = required - set(c.snapshot)
        if missing:
            errs.append(f"快照缺字段: {missing}")
        for exp in c.accept:
            if exp.get("action") not in valid_actions:
                errs.append(f"accept 里的动作不存在: {exp.get('action')}")
            if exp.get("dir") not in (None, "up", "down"):
                errs.append(f"dir 非法: {exp.get('dir')}")
        for name in c.tools:
            if not hasattr(agent, f"_tool_{name}"):
                errs.append(f"假工具名不存在: _tool_{name}")

        if errs:
            problems += 1
            print(f"  ✗ {c.id}  {c.intent}")
            for e in errs:
                print(f"      - {e}")
        else:
            mark = {"must_hold": "不动", "must_act": "应动",
                    "known_gap": "限制"}[c.category]
            print(f"  ✓ {c.id}  [{mark}]  {c.intent}")

    print()
    print(f"共 {len(cases)} 个场景，{problems} 个有问题")
    return 0 if problems == 0 else 1


# ============================================================
# 报表
# ============================================================

def print_report(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    print()
    print("=" * 70)
    print("逐场景结果")
    print("=" * 70)
    for r in results:
        # known_gap 不能显示 PASS：它没被测过，只是在当前架构下不可达。
        # 显示 PASS 会让人误以为这条覆盖到了，是最危险的一种误读。
        if judge_mod.find_case_category(r["case_id"]) == "known_gap":
            flag = "GAP "
        else:
            flag = "PASS" if r["verdict"] == "pass" else "FAIL"

        rate = r.get("pass_rate")
        rate_txt = f"  ({r['attempts']} 次中 {r['passes']} 次)" if rate is not None else ""
        print(f"  {r['case_id']}  {flag}  {r['intent']}{rate_txt}")

        if flag == "GAP ":
            print(f"        └ 已知架构限制，不计入指标（见 scenarios.py 中该场景 note）")
        elif r["verdict"] != "pass":
            kind = judge_mod.SEVERITY_LABEL.get(r.get("failure_type") or "", "?")
            print(f"        └ [{kind}] {r['detail']}")
        else:
            print(f"        └ {r['detail']}")

    s = summary
    print()
    print("=" * 70)
    print("三层指标（不合并成一个总分）")
    print("=" * 70)
    sa, rc, pc = s["safety"], s["recall"], s["precision"]
    print(f"  safety     {sa['passed']}/{sa['total']}   "
          f"假阳性 {sa['false_positive']} 次   ← 不该动的有没有动（代价最高）")
    print(f"  recall     {rc['passed']}/{rc['total']}   "
          f"漏检 {rc['false_negative']} 次     ← 该动的有没有动")
    print(f"  precision  对象错 {pc['wrong_target']} 次 / "
          f"方向错 {pc['wrong_direction']} 次")
    print()
    print(f"  合计 {s['passed']}/{s['cases']} 通过  "
          f"｜ 成本 ${s['cost_usd']:.4f}（¥{s['cost_usd'] * 7.1:.4f}）  "
          f"｜ {s['total_tokens']:,} token")

    # 失败按严重度排序，先看最危险的
    fails = [r for r in results if r["verdict"] != "pass"]
    if fails:
        fails.sort(key=lambda r: -judge_mod.SEVERITY.get(r.get("failure_type") or "", 0))
        worst = fails[0]
        print()
        print(f"  最严重的失败：{worst['case_id']} "
              f"[{judge_mod.SEVERITY_LABEL.get(worst.get('failure_type') or '', '?')}]")


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 决策能力评测")
    ap.add_argument("--dry-run", action="store_true", help="只校验场景定义，不调 LLM")
    ap.add_argument("--only", default="", help="只跑指定场景，逗号分隔（如 S01,S03）")
    ap.add_argument("--max-rounds", type=int, default=8, help="单个场景最多几轮")
    ap.add_argument("--repeat", type=int, default=1,
                    help="每个场景重复跑几次。LLM 输出有随机性，"
                         "单次结果可能翻转；安全相关的场景建议 --repeat 3")
    ap.add_argument("--json", default="", help="把结果写到指定 JSON 文件")
    args = ap.parse_args()

    cases = scenarios.build_cases()
    if args.only:
        wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c.id.upper() in wanted]
        if not cases:
            print(f"没有匹配的场景：{args.only}")
            return 2

    judge_mod.register_cases(cases)

    if args.dry_run:
        return dry_run(cases)

    print("=" * 70)
    print(f"开始评测：{len(cases)} 个场景（会真实调用 LLM）")
    print("=" * 70)

    results = []
    for i, c in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {c.id} {c.intent} ...", end="", flush=True)
        try:
            r = run_case(c, max_rounds=args.max_rounds, repeat=args.repeat)
        except Exception as e:                     # noqa: BLE001
            # 单个场景崩了不能让整轮评测白跑，也不该算通过
            r = {"case_id": c.id, "intent": c.intent, "verdict": "fail",
                 "failure_type": "error", "actions": [],
                 "detail": f"{type(e).__name__}: {e}", "usage": {}}
        results.append(r)
        if judge_mod.find_case_category(c.id) == "known_gap":
            print(" GAP（不计分）")
        elif args.repeat > 1:
            print(f" {r['passes']}/{r['attempts']}")
        else:
            print(" PASS" if r["verdict"] == "pass" else " FAIL")

    summary = judge_mod.summarize(results)
    print_report(results, summary)

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "results": results},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\n结果已写入 {args.json}")

    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
