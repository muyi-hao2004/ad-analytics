# -*- coding: utf-8 -*-
"""决策层验收：证明「同一份数据不会得出不同结论」。

为什么需要一个专门的验收脚本
------------------------------
这类机制最容易写成自我安慰：代码里写了死区、写了连续确认，
就以为「抖动被解决了」。但从来没验证过它到底在什么情况下生效、
在什么情况下不该生效 —— 那只叫「加了几个 if」。

下面 7 项，每一项都必须能说出「不通过意味着什么」：

  A. 死区确实吸收抖动       失败 = 阈值附近的正常波动会触发改判
  B. 单日事故不改判         失败 = 一次投放事故就能让渠道被砍
  C. 持续恶化会改判         失败 = 死区把真的问题也盖住了（过度稳定）
  D. 判定是确定性的         失败 = 同输入不同输出，整个机制失去意义
  E. 指纹只对指标敏感       失败 = 改个预算就以为「数据变了」要重新判断
  F. 决策日志同日幂等       失败 = agent 重试一次就多一条重复结论
  G. 历史可回溯             失败 = 说不清「当时为什么这么判」

用法：
    python check_decision_layer.py
退出码：0 = 全通过；1 = 有失败
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import agent          # noqa: E402
import decision       # noqa: E402
import dim_scd        # noqa: E402

PASS, FAIL = "[PASS]", "[FAIL]"
_FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {name:52s} {detail}")
    if not ok:
        _FAILS.append(name)


def non_decreasing_states(verdicts: list[str]) -> bool:
    """判定序列里不允许出现「改判后又改回来」的抖动。"""
    flips = sum(1 for a, b in zip(verdicts, verdicts[1:]) if a != b)
    return flips == 0


def main() -> int:
    print("=" * 78)
    print("决策层验收")
    print("=" * 78)

    # ---------------------------------------------------------------
    # A. 死区吸收抖动：值在 0.50 这个阈值附近来回跳
    # ---------------------------------------------------------------
    print("\n--- A. 死区：阈值附近的正常波动不该改判 ---")
    # 素材阈值 cut=0.50，死区 0.04 → 0.48~0.52 之间应该「保持不动」
    wobble = [("10-20", 0.53), ("10-21", 0.495), ("10-22", 0.505),
              ("10-23", 0.49), ("10-24", 0.51), ("10-25", 0.497)]
    prev = None
    trail = []
    for i in range(1, len(wobble) + 1):
        r = decision.evaluate("creative", wobble[:i], prev)
        prev = r["verdict"]
        trail.append(r["verdict"])
    check("6 天在 0.49~0.53 抖动，结论始终不变", non_decreasing_states(trail),
          f"轨迹={trail}")

    # 反证：关掉机制，同样的序列就会跳
    raw = [decision.classify(v, "creative") for _, v in wobble]
    check("（反证）关掉死区后同一序列会跳变", not non_decreasing_states(raw),
          f"裸阈值轨迹={raw}")

    # ---------------------------------------------------------------
    # B. 单日事故不改判
    # ---------------------------------------------------------------
    print("\n--- B. 连续确认：单日事故不该砍渠道 ---")
    accident = [("10-20", 0.72), ("10-21", 0.28), ("10-22", 0.71)]
    r = decision.evaluate("sub_channel", accident, "strong")
    check("单日掉到 0.28 后恢复，仍维持 strong",
          r["verdict"] == "strong" and not r["changed"],
          f"结论={r['verdict']} 拦截原因={r['blocked_by']}")

    # ---------------------------------------------------------------
    # C. 持续恶化要能改判（否则就是过度稳定）
    # ---------------------------------------------------------------
    print("\n--- C. 持续恶化必须改判（不能稳定到把问题盖住）---")
    decline = [("10-18", 0.70), ("10-19", 0.45), ("10-20", 0.43), ("10-21", 0.41)]
    r = decision.evaluate("sub_channel", decline, "watch")
    check("连续 3 天 <0.50，从 watch 改判到 critical",
          r["changed"] and r["verdict"] == "critical",
          f"结论={r['verdict']} 连续={r['days_sustained']}天")

    # ---------------------------------------------------------------
    # D. 判定确定性：同输入跑 20 次必须同输出
    # ---------------------------------------------------------------
    print("\n--- D. 确定性：同输入 20 次必须同输出 ---")
    outs = {decision.evaluate("sub_channel", decline, "watch")["verdict"] for _ in range(20)}
    check("20 次结果完全一致", len(outs) == 1, f"结果集合={outs}")

    # ---------------------------------------------------------------
    # E. 指纹只对指标敏感
    # ---------------------------------------------------------------
    print("\n--- E. 指纹口径：只该对数据敏感，不该对配置敏感 ---")
    snap = agent.build_snapshot()
    h0 = snap["数据口径"]["快照指纹"]

    mutated = json.loads(json.dumps(snap, ensure_ascii=False, default=str))
    mutated["当前配置"] = [{"campaign_id": "X", "strategy": "改了预算"}]
    mutated["最近操作"] = [{"op_time": "2026-10-23", "action": "adjust_budget"}]
    check("改配置/操作日志后指纹不变", agent.snapshot_hash(mutated) == h0, f"h={h0}")

    mutated2 = json.loads(json.dumps(snap, ensure_ascii=False, default=str))
    if mutated2["子渠道表现"]:
        mutated2["子渠道表现"][0]["d7_roi"] = 0.9999
    check("改一个指标值后指纹立刻变化", agent.snapshot_hash(mutated2) != h0,
          f"{h0} → {agent.snapshot_hash(mutated2)}")

    check("同一份快照重复算两次指纹相同",
          agent.snapshot_hash(snap) == agent.snapshot_hash(snap), f"h={h0}")

    # ---------------------------------------------------------------
    # F. 决策日志同日幂等
    # ---------------------------------------------------------------
    print("\n--- F. 决策日志：同一天写多次只留一条 ---")
    dim_scd.init_dim_tables()
    probe = "__probe__"
    ids = []
    for v in (0.60, 0.58, 0.55):
        ids.append(dim_scd.log_decision("creative", probe, "d7_roi", v, "hold",
                                        "2026-10-23", reason=f"试探{v}",
                                        days_sustained=3, snapshot_hash="deadbeef",
                                        config_version="2026-10-01"))
    hist = dim_scd.decision_history("creative", probe)
    check("写 3 次只产生 1 行", len(hist) == 1, f"行数={len(hist)}")
    check("同键写入返回同一个 log_id", len(set(ids)) == 1, f"ids={ids}")
    check("保留的是最后一次的值", hist and hist[0]["value"] == 0.55,
          f"value={hist[0]['value'] if hist else None}")

    # ---------------------------------------------------------------
    # G. 历史可回溯
    # ---------------------------------------------------------------
    print("\n--- G. 回溯：能说清「当时为什么这么判」 ---")
    row = hist[0] if hist else {}
    need = ("decided_at", "data_as_of", "value", "verdict", "reason",
            "snapshot_hash", "config_version")
    missing = [k for k in need if not row.get(k)]
    check("一条记录含齐 7 个回溯字段", not missing,
          f"缺={missing}" if missing else "时间/截止日/值/结论/理由/指纹/配置版本")

    # 清理探针
    conn = sqlite3.connect(dim_scd.DB_PATH)
    conn.execute("DELETE FROM decision_log WHERE obj_id=?", (probe,))
    conn.commit()
    conn.close()

    # ---------------------------------------------------------------
    # 汇总
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    total = 10
    if _FAILS:
        print(f"结果: {total - len(_FAILS)} 通过 / {len(_FAILS)} 失败")
        for f in _FAILS:
            print(f"  未通过: {f}")
        return 1
    print(f"结果: {total} 项全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
