"""
跑一轮 Agent，并直接把可解释性结果渲染成「决策看板」HTML。

与 build_report.py（静态报表）不同，这里：
  1. 先跑 agent.run_agent()（多轮 Function Calling，dry_run 可选）
  2. 把「结论 + 推理链 + 每轮 trace（查了什么/返回了什么）+ 动作」渲染成 HTML
  3. 输出到 reports/agent_dashboard.html

这样「跑完直接生成看板」就成立：一条命令，从数据到决策到可视化全链路。

运行：
    cd iaa_bi
    python src/run_agent_dashboard.py            # dry_run 模式（只分析）
    python src/run_agent_dashboard.py --execute  # 真正执行写操作
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
import agent


# ============================================================
# 渲染辅助
# ============================================================

def _tool_name_cn(name: str) -> str:
    return {
        "get_metric": "查指标明细",
        "get_logs": "查操作历史",
        "delete_sub_channel": "删除子渠道",
        "adjust_target": "调目标值",
        "adjust_budget": "调预算",
    }.get(name, name)


def _action_name_cn(action: str) -> str:
    return {
        "delete_sub_channel": "删除子渠道",
        "adjust_target": "调目标值",
        "adjust_budget": "调预算",
    }.get(action, action)


def _pretty_params(params: dict) -> str:
    """把工具参数变成可读的一行。"""
    if not params:
        return ""
    parts = []
    label = {"obj_type": "对象", "obj_id": "ID", "metric": "指标",
             "days": "天数", "keyword": "关键词", "target": "系列",
             "country": "国家", "new_target": "新目标", "new_budget": "新预算"}
    for k, v in params.items():
        parts.append(f"{label.get(k, k)}={v}")
    return " · ".join(parts)


def _summary_result(result: dict) -> str:
    """把工具返回结果压缩成一句可读摘要（避免把整个 JSON 倒进看板）。"""
    if not result:
        return "—"
    if "error" in result:
        return f"⚠️ {result['error']}"
    if "data" in result and isinstance(result["data"], list):
        # get_metric 返回的每日序列
        arr = result["data"]
        if arr and isinstance(arr[0], dict):
            # 找到数值键（去掉 date）
            keys = [k for k in arr[0].keys() if k != "date"]
            metric = keys[0] if keys else ""
            vals = [str(x.get(metric)) for x in arr]
            return f"{metric}: {', '.join(vals)}"
    if "count" in result and "logs" in result:
        n = result["count"]
        return f"匹配到 {n} 条历史记录"
    if "ok" in result:
        return result.get("message", "操作成功")
    # 兜底：截断 JSON
    s = json.dumps(result, ensure_ascii=False, default=str)
    return s[:200]


def _render_trace_step(t: dict) -> str:
    """渲染 trace 里的一轮，返回 HTML 片段。"""
    step = t.get("step")
    if step == "tool":
        name = t.get("name", "")
        params = t.get("parameters", {})
        result = t.get("result", {})
        return f"""
        <div class="step">
          <div class="step-head">
            <span class="round">第 {t['round']} 轮</span>
            <span class="badge tool">调工具</span>
            <span class="tool-name">{_tool_name_cn(name)}</span>
          </div>
          <div class="step-body">
            <div class="kv"><span class="k">参数</span><span class="v mono">{_pretty_params(params) or '（无）'}</span></div>
            <div class="kv"><span class="k">返回</span><span class="v mono">{_summary_result(result)}</span></div>
          </div>
        </div>"""
    elif step == "finish":
        reasoning = t.get("reasoning", "")
        return f"""
        <div class="step">
          <div class="step-head">
            <span class="round">第 {t['round']} 轮</span>
            <span class="badge finish">给决策</span>
            <span class="tool-name">分析完成</span>
          </div>
        </div>"""
    else:
        raw = t.get("raw", "")
        return f"""
        <div class="step">
          <div class="step-head">
            <span class="round">第 {t['round']} 轮</span>
            <span class="badge warn">格式异常</span>
          </div>
          <div class="step-body"><div class="kv"><span class="v mono">{raw}</span></div></div>
        </div>"""


def _render_actions(actions: list, executed: list) -> str:
    """渲染最终动作清单。"""
    if not actions:
        return '<div class="empty">本轮无写操作（无需优化）</div>'
    executed_msgs = {e.get("action"): e.get("message", "") for e in executed if "action" in e}
    rows = []
    for a in actions:
        a_type = a.get("action", "")
        target = a.get("target", "") or a.get("params", {}).get("target", "")
        reason = a.get("reason", "")
        mark = "✅ 已执行" if a_type in executed_msgs else "⏳ 建议（未执行）"
        rows.append(f"""
        <div class="action-row">
          <div class="action-title">
            <span class="badge action">{_action_name_cn(a_type)}</span>
            <span class="mono">{target}</span>
            <span class="exec-mark">{mark}</span>
          </div>
          <div class="action-reason">{reason}</div>
        </div>""")
    return "".join(rows)


def build_dashboard(result: dict, dry_run: bool) -> str:
    """把 run_agent 的结果渲染成完整 HTML。"""
    conclusion = result.get("conclusion", "")
    reasoning = result.get("reasoning", "")
    trace = result.get("trace", [])
    actions = result.get("actions", [])
    executed = result.get("executed", [])

    # 把 reasoning 的编号列表（1) 2) 3)）拆成段落
    reasoning_html = reasoning.replace("\n", "<br>") if reasoning else "（无推理链）"

    trace_html = "".join(_render_trace_step(t) for t in trace)
    actions_html = _render_actions(actions, executed)

    mode_label = "dry_run · 仅分析未执行" if dry_run else "已执行写操作"
    mode_cls = "mode-dry" if dry_run else "mode-exec"
    round_count = len(trace)

    return _HTML_TEMPLATE.replace("__MODE__", mode_label) \
        .replace("__MODE_CLS__", mode_cls) \
        .replace("__ROUNDS__", str(round_count)) \
        .replace("__CONCLUSION__", conclusion) \
        .replace("__REASONING__", reasoning_html) \
        .replace("__TRACE__", trace_html) \
        .replace("__ACTIONS__", actions_html)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent 决策看板</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
         background: #f5f6f8; color: #1f1f1f; padding: 24px; line-height: 1.6; }
  .wrap { max-width: 960px; margin: 0 auto; }
  .header { background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .header h1 { font-size: 22px; font-weight: 700; }
  .header .sub { font-size: 13px; color: #888; margin-top: 6px; }
  .mode-tag { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px;
              font-weight: 600; margin-left: 8px; vertical-align: middle; }
  .mode-dry { background: #fff7e6; color: #d48806; }
  .mode-exec { background: #f6ffed; color: #389e0d; }

  .card { background: #fff; border-radius: 12px; padding: 22px; margin-bottom: 16px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .card h2 { font-size: 15px; font-weight: 700; margin-bottom: 14px; padding-left: 10px;
             border-left: 4px solid #2c5f8a; }

  .conclusion { font-size: 15px; font-weight: 600; color: #1f1f1f; }
  .reasoning { font-size: 13px; color: #444; background: #fafbfc; border-radius: 8px;
               padding: 14px 16px; }

  .step { border: 1px solid #eef0f2; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
  .step-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .round { font-size: 12px; color: #888; font-weight: 600; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; }
  .badge.tool { background: #eef4fb; color: #2c5f8a; }
  .badge.finish { background: #f6ffed; color: #389e0d; }
  .badge.warn { background: #fff1f0; color: #cf1322; }
  .badge.action { background: #fff7e6; color: #d48806; }
  .tool-name { font-size: 14px; font-weight: 600; }
  .step-body { margin-left: 4px; }
  .kv { display: flex; gap: 10px; margin-bottom: 4px; font-size: 13px; }
  .kv .k { color: #888; min-width: 40px; flex-shrink: 0; }
  .kv .v { color: #333; }
  .mono { font-family: 'SF Mono', Consolas, monospace; font-size: 12.5px; }

  .action-row { border-left: 3px solid #d48806; padding: 10px 14px; margin-bottom: 10px;
                background: #fafbfc; border-radius: 0 8px 8px 0; }
  .action-title { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .exec-mark { font-size: 12px; color: #888; }
  .action-reason { font-size: 13px; color: #555; }

  .empty { color: #999; text-align: center; padding: 20px; font-size: 13px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>Agent 决策看板<span class="mode-tag __MODE_CLS__">__MODE__</span></h1>
    <div class="sub">海外网赚类 IAA · 多轮 Function Calling Agent · 共 __ROUNDS__ 轮 · 可解释性轨迹</div>
  </div>

  <div class="card">
    <h2>结论</h2>
    <div class="conclusion">__CONCLUSION__</div>
  </div>

  <div class="card">
    <h2>推理链（为什么这么决策）</h2>
    <div class="reasoning">__REASONING__</div>
  </div>

  <div class="card">
    <h2>决策轨迹（每轮查了什么 / 返回了什么）</h2>
    __TRACE__
  </div>

  <div class="card">
    <h2>最终动作</h2>
    __ACTIONS__
  </div>
</div>
</body>
</html>"""


def main():
    import argparse
    parser = argparse.ArgumentParser(description="跑 Agent 并生成决策看板")
    parser.add_argument("--execute", action="store_true", help="真正执行写操作（默认 dry_run）")
    args = parser.parse_args()

    dry_run = not args.execute
    config.ensure_dirs()

    print("=" * 60)
    print("跑一轮 Agent（多轮 Function Calling）...")
    print("=" * 60)
    result = agent.run_agent(dry_run=dry_run)

    html = build_dashboard(result, dry_run)
    out = config.REPORTS_DIR / "agent_dashboard.html"
    out.write_text(html, encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"决策看板已生成: {out}")
    print(f"  结论: {result['conclusion'][:60]}")
    print(f"  动作数: {len(result['actions'])} | 轨迹轮数: {len(result['trace'])}")
    print("  用浏览器打开该文件，或通过 FastAPI 访问 /agent_dashboard 查看")


if __name__ == "__main__":
    main()
