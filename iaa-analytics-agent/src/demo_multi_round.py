"""
多轮 Function Calling 演示：展示「多轮」如何比「一次性」决策更准。

核心场景（误删陷阱）：
  - 快照里 crt_002 的「聚合 D7 ROI」只有 3.7%，看起来像垃圾素材，应该删
  - 但真实明细是：前 7 天 ROI 稳定在 80% 上下，只有最后一天暴跌到 3%
  - 一次性 Agent 只会看到 3.7%，误判「删」→ 误杀
  - 多轮 Agent 会调 get_metric 下钻，发现是「单日异常」→ 不删，正确

这就是为什么要升级多轮：让 LLM 拥有「按需取数」的能力，
而不是「你喂什么它就只能看什么」。

运行：
    cd iaa-analytics-agent
    D:/python/python.exe src/demo_multi_round.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agent
import dim_scd
import llm_cost
import platform_layer


def make_trap_snapshot():
    """构造一个「看起来该删、实则不该删」的快照。"""
    return {
        "素材表现": [
            {"creative_id": "crt_002", "spend": 500, "installs": 10000,
             "d7_roi": 0.037, "days": 8},   # 聚合值很低，诱导误删
            {"creative_id": "crt_001", "spend": 600, "installs": 12000,
             "d7_roi": 1.02, "days": 8},
        ],
        "子渠道表现": [],
        "当前配置": [],
        "最近操作": [],
        "业务目标": {"D7_ROAS_目标": "70%",
                     "说明": "D7 ROI 低于该值说明变现效率不足"},
    }


def fake_get_metric(obj_type, obj_id, metric, days=8):
    """模拟 get_metric：查 crt_002 时返回「前7天高、最后一天暴跌」的明细。"""
    if obj_id == "crt_002" and metric == "d7_roi":
        return {
            "obj_type": obj_type, "obj_id": obj_id, "metric": metric,
            "data": [
                {"date": "10-01", "d7_roi": 0.82},
                {"date": "10-02", "d7_roi": 0.78},
                {"date": "10-03", "d7_roi": 0.85},
                {"date": "10-04", "d7_roi": 0.81},
                {"date": "10-05", "d7_roi": 0.79},
                {"date": "10-06", "d7_roi": 0.83},
                {"date": "10-07", "d7_roi": 0.80},
                {"date": "10-08", "d7_roi": 0.03},   # 最后一天暴跌
            ],
        }
    return agent._tool_get_metric(obj_type, obj_id, metric, days)


def main():
    print("=" * 60)
    print("多轮 Function Calling 演示：误删陷阱")
    print("=" * 60)
    print("快照里 crt_002 聚合 ROI = 3.7%（看似该删）")
    print("真实明细 = 前7天80%达标，最后一天暴跌（其实不该删）\n")

    # 让 get_metric 返回陷阱数据
    agent._tool_get_metric = fake_get_metric

    plat = platform_layer.get_platform("mock")
    snapshot = make_trap_snapshot()

    decision = agent.run_llm_loop(snapshot, plat)

    print("\n--- LLM 决策过程 ---")
    for t in decision["trace"]:
        step = t.get("step")
        if step == "tool":
            args = json.dumps(t.get("parameters"), ensure_ascii=False)
            print(f"  第{t['round']}轮 [查数据] {t.get('name')}({args})")
        elif step == "finish":
            print(f"  第{t['round']}轮 [给出决策] {t.get('reasoning', '')[:80]}")
        else:
            print(f"  第{t['round']}轮 [{step}]")
    print("--- 结束 ---\n")

    print("结论:", decision["conclusion"])
    print("动作:", json.dumps(decision["actions"], ensure_ascii=False))

    # 这个场景的正确答案是**不做任何动作**（Roi 低是单日未成熟数据造成的假象），
    # 所以对错可以用代码判定，不需要人肉看。这是它作为 eval case 的基础。
    passed_case = not decision["actions"]

    if passed_case:
        print("\n✅ 多轮 Agent 正确判断：下钻发现是单日异常，未误删 crt_002")
        print("   （若用一次性 Agent，只会看到 3.7% 就误删了）")
    else:
        print("\n❌ Agent 误删了 crt_002（多轮下钻未生效）")

    # ---- 成本 ----
    usage = decision.get("usage", {})
    print("\n本次成本:", llm_cost.format_line(usage))

    # ---- 落库 ----
    # 为什么要存：不存的话，真实运行的 token 数据随手就丢了，
    # cost_report 永远只有空表，只能拿模拟数说话。
    # status 用 demo 而不是 pending——它不是待确认的建议单，是实验记录。
    run_id = dim_scd.save_agent_run(
        status="demo",
        conclusion=("PASS " if passed_case else "FAIL ") + str(decision["conclusion"]),
        reasoning=decision.get("reasoning", ""),
        actions=decision.get("actions", []),
        executed=[],
        trace=decision.get("trace", []),
        usage=usage,
    )
    print(f"已记录 run_id={run_id}（status=demo，看成本：src/cost_report.py）")


if __name__ == "__main__":
    main()
