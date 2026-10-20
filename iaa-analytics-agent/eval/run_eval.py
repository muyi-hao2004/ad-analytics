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


def _check_stub_consistency(case) -> List[str]:
    """检查假工具返回的数据是否和快照自洽。

    为什么需要这条检查：
        2026-10-20 那天连着踩了两次同一个坑。第一次 S02 的假工具不管查谁都
        返回同一串 0.05，Agent 当场指出「数据串号」；第二次 S03 的假工具
        对健康的 ch_012 也返回 0.11（快照是 0.92），Agent 判定「明细与聚合矛盾」
        然后拒绝动作 —— 报表上表现为「模型退化」，实际是场景在撒谎。

        这类 bug 特别隐蔽：场景不会报错，只会让 Agent 显得很笨。
        更糟的是它会**偷换被测的东西**——本来要测「该动的时候敢不敢动」，
        结果测成了「数据矛盾时会不会停手」。

    检查方法：
        同一场景里若有两个以上对象、且它们的 ROI 差异超过 2 倍，
        就要求假工具对不同 obj_id 返回不同的明细。返回完全一样 = 报警。

    这个判据不看数值是否精确吻合（刻意设计的不一致，如 H03 是对的），
    只看「有没有按对象区分」——误报率低，能抓住真正的问题。
    """
    errs: List[str] = []
    fn = (case.tools or {}).get("get_metric")
    if fn is None:
        return errs

    for key, id_field in (("子渠道表现", "sub_channel"), ("素材表现", "creative_id")):
        rows = case.snapshot.get(key) or []
        rois = [(r.get(id_field), r.get("d7_roi")) for r in rows
                if r.get(id_field) and isinstance(r.get("d7_roi"), (int, float))]
        if len(rois) < 2:
            continue
        lo = min(v for _, v in rois)
        hi = max(v for _, v in rois)
        if lo <= 0 or hi / lo <= 2:
            continue   # 数值接近，返回同一份数据也不会露馅

        try:
            means = {}
            for oid, _ in rois:
                res = fn(obj_type="sub_channel" if key == "子渠道表现" else "creative",
                         obj_id=oid, metric="d7_roi")
                vals = [d.get("d7_roi") for d in (res.get("data") or [])
                        if isinstance(d.get("d7_roi"), (int, float))]
                if vals:
                    means[oid] = round(sum(vals) / len(vals), 6)
        except Exception as e:
            errs.append(f"get_metric 桩调用失败: {e}")
            continue

        if len(means) >= 2 and len(set(means.values())) == 1:
            pairs = " / ".join(f"{k}={v}" for k, v in means.items())
            errs.append(
                f"假工具不区分对象，多个 {id_field} 返回同一份明细（{pairs}）。"
                f"快照里它们的 ROI 相差 {hi / lo:.1f} 倍，Agent 会先发现「数据串位」，"
                f"测的就不是原来的陷阱了")
    return errs


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
        if c.split not in ("dev", "holdout"):
            errs.append(f"split 非法: {c.split}")
        if c.category == "must_hold" and c.accept:
            errs.append("must_hold 场景不应有 accept 规则")
        if c.category == "must_act" and not c.accept:
            errs.append("must_act 场景必须给出 accept 规则")
        if c.split == "holdout" and c.category == "must_hold" and c.forbid:
            errs.append("must_hold 已经是「什么都不许动」，forbid 冗余")
        missing = required - set(c.snapshot)
        if missing:
            errs.append(f"快照缺字段: {missing}")
        for exp in c.accept:
            if exp.get("action") not in valid_actions:
                errs.append(f"accept 里的动作不存在: {exp.get('action')}")
            if exp.get("dir") not in (None, "up", "down"):
                errs.append(f"dir 非法: {exp.get('dir')}")

        # forbid 的对象必须真的出现在快照里，否则这条规则永远触发不了——
        # 写错的 forbid 不会报错，只会静默失效，比没有更糟。
        known_targets = set()
        for row in c.snapshot.get("子渠道表现") or []:
            known_targets.add(row.get("sub_channel"))
        for row in c.snapshot.get("素材表现") or []:
            known_targets.add(row.get("creative_id"))
        for cfg in c.snapshot.get("当前配置") or []:
            known_targets.add(cfg.get("campaign_id"))
        for t in c.forbid:
            if t not in known_targets:
                errs.append(f"forbid 里的 {t!r} 不在快照中，这条规则永远不会触发")

        for name in c.tools:
            if not hasattr(agent, f"_tool_{name}"):
                errs.append(f"假工具名不存在: _tool_{name}")

        errs += _check_stub_consistency(c)

        if errs:
            problems += 1
            print(f"  ✗ {c.id}  {c.intent}")
            for e in errs:
                print(f"      - {e}")
        else:
            mark = {"must_hold": "不动", "must_act": "应动",
                    "known_gap": "限制"}[c.category]
            flag = "" if c.split == "dev" else " [holdout]"
            print(f"  ✓ {c.id}  [{mark}]  {c.intent}{flag}")

    print()
    n_hold = len([c for c in cases if c.split == "holdout"])
    print(f"共 {len(cases)} 个场景（dev {len(cases) - n_hold} / holdout {n_hold}），"
          f"{problems} 个有问题")
    return 0 if problems == 0 else 1


# ============================================================
# 报表
# ============================================================

def print_report(results: List[Dict[str, Any]]) -> None:
    print()
    print("=" * 70)
    print("逐场景结果")
    print("=" * 70)
    for r in results:
        split = judge_mod.find_case_split(r["case_id"])
        tag = "" if split == "dev" else " [holdout]"
        # known_gap 不能显示 PASS：它没被测过，只是在当前架构下不可达。
        # 显示 PASS 会让人误以为这条覆盖到了，是最危险的一种误读。
        if judge_mod.find_case_category(r["case_id"]) == "known_gap":
            flag = "GAP "
        else:
            flag = "PASS" if r["verdict"] == "pass" else "FAIL"

        rate = r.get("pass_rate")
        rate_txt = f"  ({r['attempts']} 次中 {r['passes']} 次)" if rate is not None else ""
        print(f"  {r['case_id']}  {flag}  {r['intent']}{tag}{rate_txt}")

        if flag == "GAP ":
            print(f"        └ 已知架构限制，不计入指标（见 scenarios.py 中该场景 note）")
        elif r["verdict"] != "pass":
            kind = judge_mod.SEVERITY_LABEL.get(r.get("failure_type") or "", "?")
            print(f"        └ [{kind}] {r['detail']}")
        else:
            print(f"        └ {r['detail']}")

        # 判断层失败但生效层通过 = 护栏兜住了。这个信息必须显示，
        # 否则读者会以为这条通过了，而实际上 Agent 的想法是错的。
        blocked = r.get("blocked") or []
        if blocked:
            why = blocked[0].get("blocked_reason", "")
            eff = "生效层 PASS（护栏兜住）" if r.get("effective_verdict") == "pass" else "生效层仍 FAIL"
            print(f"        └ 护栏拦下 {len(blocked)} 个动作 → {eff}")
            print(f"           {why}")


def print_metrics(summary: Dict[str, Any], label: str = "") -> None:
    """打印一组三层指标。

    分成判断层 / 生效层两列：
        判断层 = Agent 自己有没有想错
        生效层 = 这些想法里有多少真的被执行了
    只看判断层，护栏的收益永远显示不出来；只看生效层，
    又会把「幸好被拦住了」误读成「Agent 判断对了」。
    """
    s = summary
    head = f"三层指标 · {label}" if label else "三层指标（不合并成一个总分）"
    print()
    print("=" * 70)
    print(head)
    print("=" * 70)
    if s["scored"] == 0:
        print("  （本组没有计分场景）")
        return

    sa, rc, pc, en = s["safety"], s["recall"], s["precision"], s["enforcement"]
    print(f"  safety     判断 {sa['passed']}/{sa['total']}   "
          f"生效 {sa['effective_passed']}/{sa['total']}   "
          f"← 不该动的时候动没动（代价最高）")
    print(f"  recall     判断 {rc['passed']}/{rc['total']}   "
          f"生效 {rc['effective_passed']}/{rc['total']}   "
          f"← 该动的时候动没动")
    print(f"  precision  对象错 {pc['wrong_target']} 次 / 方向错 {pc['wrong_direction']} 次")
    if en["blocked_actions"]:
        print(f"  护栏拦截   {en['blocked_actions']} 个动作，涉及 {en['cases_with_block']} 个场景"
              f"   ← 判断错了，但没造成后果")
    if en["over_blocked_cases"]:
        print(f"  ！过度拦截 {en['over_blocked_cases']} 个场景 "
              f"({', '.join(en['over_blocked_ids'])})   ← 护栏把正确动作也拦了")
    print(f"  合计 {s['passed']}/{s['scored']} 计分通过  "
          f"｜ ${s['cost_usd']:.4f}（¥{s['cost_usd'] * 7.1:.4f}）"
          f"｜ {s['total_tokens']:,} token")


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 决策能力评测")
    ap.add_argument("--dry-run", action="store_true", help="只校验场景定义，不调 LLM")
    ap.add_argument("--only", default="", help="只跑指定场景，逗号分隔（如 S01,S03）")
    ap.add_argument("--max-rounds", type=int, default=8, help="单个场景最多几轮")
    ap.add_argument("--repeat", type=int, default=1,
                    help="每个场景重复跑几次。LLM 输出有随机性，"
                         "单次结果可能翻转；安全相关的场景建议 --repeat 3")
    ap.add_argument("--json", default="", help="把结果写到指定 JSON 文件")
    ap.add_argument("--split", choices=["all", "dev", "holdout"], default="all",
                    help="只跑某个分区。dev=可调参，holdout=只验证不调参")
    args = ap.parse_args()

    cases = scenarios.build_cases()
    if args.split != "all":
        cases = [c for c in cases if c.split == args.split]
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
    print_report(results)
    print_metrics(summary, "全部")

    # dev / holdout 分开报。
    # 为什么不能只看总数：holdout 的水平本来就该低于 dev——
    # 它是「没见过的新情况」。两个数放一起看，才能判断这次改动是真的
    # 提升了泛化，还是只把 dev 那几条背下来了。
    dev = [r for r in results if judge_mod.find_case_split(r["case_id"]) == "dev"]
    hold = [r for r in results if judge_mod.find_case_split(r["case_id"]) == "holdout"]
    if dev and hold:
        print_metrics(judge_mod.summarize(dev), "dev（可以看着它调参）")
        print_metrics(judge_mod.summarize(hold), "holdout（只验证，不许看着它调参）")

    # 失败按严重度排序，先看最危险的
    fails = [r for r in results if r["verdict"] != "pass"
             and judge_mod.find_case_category(r["case_id"]) != "known_gap"]
    if fails:
        fails.sort(key=lambda r: -judge_mod.SEVERITY.get(r.get("failure_type") or "", 0))
        worst = fails[0]
        print()
        print(f"  最严重的失败：{worst['case_id']} "
              f"[{judge_mod.SEVERITY_LABEL.get(worst.get('failure_type') or '', '?')}]")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary,
                        "summary_dev": judge_mod.summarize(dev) if dev else None,
                        "summary_holdout": judge_mod.summarize(hold) if hold else None,
                        "results": results},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\n结果已写入 {args.json}")

    # 退出码只看判断层。护栏兜住的那些不该算「通过」——
    # CI 里绿灯的含义必须是「Agent 判断对了」，不是「幸好被拦住了」。
    return 0 if summary["passed"] == summary["scored"] else 1


if __name__ == "__main__":
    sys.exit(main())
