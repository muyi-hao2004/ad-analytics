"""中台接入验收：确认 agent.py 的 6 个取数出口都能从新数仓读到数据。

这是改造后的**回归测试**，不是一次性脚本：
    agent.py 里原来有 6 处 pd.read_parquet，现在全部走 warehouse_api。
    任何一处改坏了，这里会立刻报出来 —— 而不是等到 LLM 调用工具时才发现
    「某个工具静默返回空列表」。

跑法：
    cd iaa-analytics-agent
    ../venv/Scripts/python.exe check_warehouse_integration.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import agent  # noqa: E402
import warehouse_api  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, fn, *args, **kwargs):
    global PASS, FAIL
    try:
        result = fn(*args, **kwargs)
        # 判定「有数据」而不是「没抛异常」：
        # 工具函数查不到数据时通常返回 [] 或 {"error": ...}，那也算失败 ——
        # 因为中台最怕的不是报错，是静默返回空。
        if isinstance(result, list):
            ok = len(result) > 0
            detail = f"{len(result)} 条"
        elif isinstance(result, dict):
            ok = "error" not in result
            detail = "error: " + str(result.get("error"))[:40] if not ok else f"keys={len(result)}"
        elif result is None:
            ok, detail = False, "返回 None（查不到）"
        else:
            ok, detail = True, repr(result)[:40]
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<46} {detail}")
        if ok:
            PASS += 1
        else:
            FAIL += 1
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {label:<46} 异常: {exc}")
        traceback.print_exc(limit=2)
        FAIL += 1
        return None


print(f"取数模式: {warehouse_api.current_mode()}")
print("=" * 78)

print("\n--- 1. _load_top（快照里的 Top N 最差对象）---")
top_c = check("_load_top('creative_table.parquet', 5)", agent._load_top, "creative_table.parquet", 5)
top_s = check("_load_top('sub_channel_table.parquet', 3)", agent._load_top, "sub_channel_table.parquet", 3)
if top_c:
    print(f"         最差素材: {top_c[0].get('creative_id')}  d7_roi={top_c[0].get('d7_roi')}")

print("\n--- 2. 护栏用的两个点查 ---")
ch = check("_get_channel_installs('ch_001')", agent._get_channel_installs, "ch_001")
days = check("_get_channel_observed_days('ch_001')", agent._get_channel_observed_days, "ch_001")
# 负例：不存在的渠道必须返回 None（不是 0）——护栏靠这个 fail-closed
neg = agent._get_channel_installs("ch_not_exist")
print(f"  [{'PASS' if neg is None else 'FAIL'}] 不存在渠道返回 None 而非 0"
      f"                          {neg!r}")
PASS, FAIL = (PASS + 1, FAIL) if neg is None else (PASS, FAIL + 1)

print("\n--- 3. _tool_get_metric（对象 × 指标 的逐日明细）---")
m = check("creative / crt_gb_a_01_026 / d7_roi", agent._tool_get_metric,
          "creative", "crt_gb_a_01_026", "d7_roi", 8)
if m and m.get("data"):
    print(f"         最近一天: {m['data'][-1]}")
m2 = check("sub_channel / ch_001 / d7_retention", agent._tool_get_metric,
           "sub_channel", "ch_001", "d7_retention", 8)
if m2 and m2.get("data"):
    print(f"         最近一天: {m2['data'][-1]}")

print("\n--- 4. _tool_get_top_history（历史头部/尾部）---")
th = check("dim=sub_channel metric=d7_roi top_n=3", agent._tool_get_top_history,
           "sub_channel", "d7_roi", 3)
if th and th.get("头部"):
    print(f"         头部: {[(x.get('sub_channel'), round(float(x.get('d7_roi', 0)), 3)) for x in th['头部'][:3]]}")
if th and th.get("尾部"):
    print(f"         尾部: {[(x.get('sub_channel'), round(float(x.get('d7_roi', 0)), 3)) for x in th['尾部'][:3]]}")

print("\n--- 5. _tool_get_trend_with_ops（趋势 + 操作日志对齐）---")
tw = check("sub_channel / ch_001 / d7_roi", agent._tool_get_trend_with_ops,
           "sub_channel", "ch_001", "d7_roi", 8)
if tw and tw.get("trend"):
    print(f"         趋势 {len(tw['trend'])} 天，最后一天: {tw['trend'][-1]}")

print("\n" + "=" * 78)
print(f"结果: {PASS} 通过 / {FAIL} 失败")
print("=" * 78)
sys.exit(0 if FAIL == 0 else 1)
