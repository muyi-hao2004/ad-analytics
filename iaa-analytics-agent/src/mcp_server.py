"""
MCP Server：把 IAA 中台的数据能力封装成标准 MCP 工具，供任意 Agent 调用。

背景（2026 标准）：
  之前 agent.py 里的工具是「手写 Function Calling」，只有 agent.py 自己能用。
  用 MCP（Model Context Protocol）封装后，任何支持 MCP 的 Agent（Claude、GPT、
  以及你自己未来换的其他模型）都能通过标准接口发现并调用这些能力。

  这解决的是 N×M → N+M 的集成问题：每个数据能力写一个 MCP Server，每个模型
  写一个 MCP Client，之后任意组合自动连通。

设计原则：
  - 复用 agent.py 里已有的工具实现（_tool_get_metric 等），不重写逻辑，
    保证「语义层 / 数仓 / 知识库」只有一份代码、一个口径。
  - **写操作不暴露成工具**：AI 只能通过 submit_suggestion 提交建议单，
    由人 approve 后才执行（提议者 ≠ 批准者）。
  - 每个工具都带清晰的 docstring（FastMCP 会把 docstring 转成工具的 description，
    这就是「工具 description 是命门」的落地）。

运行方式（stdio transport，标准 MCP 接入方式）：
  python mcp_server.py

  或用于调试的 in-memory 检查：
  python mcp_server.py --inspect

依赖：mcp>=1.0（当前用 2.x，FastMCP 已更名 MCPServer）
"""

from typing import Dict, Any
from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).parent))

# mcp 2.x 里 FastMCP 已改名为 MCPServer（API 基本兼容，只是类名和 import 路径变了）
from mcp.server.mcpserver import MCPServer

import config
import agent
import platform_layer
import semantic_layer

# 创建 MCP Server 实例。name 会作为服务的标识。
mcp = MCPServer("iaa-analytics-agent")

# 复用 agent.py 的读工具实现（单一数据源，不重写逻辑）
_get_metric = agent._tool_get_metric
_get_logs = agent._tool_get_logs
_get_top_history = agent._tool_get_top_history
_get_trend_with_ops = agent._tool_get_trend_with_ops
_search_knowledge = agent._tool_search_knowledge
_get_metric_definition = agent._tool_get_metric_definition



# ============================================================
# 读工具（精确数值 → 数仓）
# ============================================================

@mcp.tool()
def get_metric(obj_type: str, obj_id: str, metric: str, days: int = 8) -> Dict[str, Any]:
    """查「单个对象」某指标最近几天的逐日明细（下钻）。

    触发场景：快照里某对象聚合值异常（如 ROI 低），要看它「每天」的变化来定位是否单日暴跌。
    obj_type: creative=素材 / sub_channel=子渠道
    metric: d7_roi / d7_retention / cpi / spend / installs
    """
    return _get_metric(obj_type, obj_id, metric, days)


@mcp.tool()
def get_logs(keyword: str, limit: int = 10) -> Dict[str, Any]:
    """查某对象的操作历史（谁在什么时候删过/调过它）。

    触发场景：决定删/调某对象前，先查它是否已被操作过，避免重复操作或误删。
    keyword: 子渠道ID（如 ch_003）或系列ID（如 US_A_01）
    """
    return _get_logs(keyword, limit)


@mcp.tool()
def get_top_history(dim: str = "sub_channel", metric: str = "d7_roi", top_n: int = 5) -> Dict[str, Any]:
    """查历史「头部/尾部」对象排行（横向对比）。

    触发场景：判断某对象是「一贯差」还是「近期才变差」。
    dim: app/sub_channel/creative_id/campaign/country
    metric: d7_roi/spend/installs/cpi（只支持这4个，不含留存）
    """
    return _get_top_history(dim, metric, top_n)


@mcp.tool()
def get_trend_with_ops(obj_type: str, obj_id: str, metric: str = "d7_roi", days: int = 8) -> Dict[str, Any]:
    """查某对象「逐日趋势 + 当天操作日志」合并视图（因果归因专用）。

    触发场景：判断某指标变化是不是某次操作导致的。
    obj_type: sub_channel / creative
    """
    return _get_trend_with_ops(obj_type, obj_id, metric, days)


# ============================================================
# 知识 / 语义工具（文本经验 + 口径定义）
# ============================================================

@mcp.tool()
def search_knowledge(query: str, top_k: int = 3) -> Dict[str, Any]:
    """检索优化师「行业经验/方法论」知识库（RAG，文本检索）。

    触发场景：参考「这类问题通常怎么处理」的经验，如「ROI 低于目标怎么下钻」。
    注意：返回的是文本经验，不是精确数值；算数值用 get_metric/get_top_history。
    """
    return _search_knowledge(query, top_k)


@mcp.tool()
def get_metric_definition(metric: str) -> Dict[str, Any]:
    """查「指标的标准口径定义」——公式、依赖字段、口径备注。

    触发场景：用某指标判断但不确定它的精确算法时，先查标准定义（避免同名不同义）。
    metric: d7_roi/cpi/buy_ctr/buy_cvr/buy_ecpm/monetization_ecpm/d7_retention/rewarded_views_per_user/retention_dn/roi_dn/ecpm_dn/views_dn
    """
    return _get_metric_definition(metric)


# ============================================================
# 写操作：不是「工具」，是「建议单」
# ============================================================
#
# 为什么 MCP 侧也改掉了三个写工具
# ---------------------------------
# MCP 的意义是「任何 AI 客户端都能接进来」。如果这里直接暴露
# delete_sub_channel / adjust_budget / adjust_target，等于给所有接入的
# Agent 开了一个无审批的写接口——它们不在我们的进程里，我们拦不住。
#
# 更根本的一条原则：**提议者和批准者不能是同一个主体。**
# 如果 AI 既能提案又能执行，那「人工确认」就只是个装饰。
#
# 所以这里只暴露一个 submit_suggestion：
#   AI 提交建议 → 落成 pending 建议单 → 人在 Web 界面 approve → 才真正执行
# AI 能看见自己提过的建议及其状态，但碰不到执行动作。

@mcp.tool()
def submit_suggestion(actions: list, reasoning: str = "") -> Dict[str, Any]:
    """提交优化建议单（**不会直接执行**，必须经过人工确认）。

    这是本服务唯一的写入口。想删子渠道、调预算、调目标，都写进 actions 里提交。

    触发场景：你查完数据、形成明确判断后，把建议提交给投放人审核。
    边界：
      - 提交不等于执行。返回 run_id 后，需要人工在系统里 approve 才会生效。
      - 建议会过业务护栏（预算 1~50000、单次变动不超过 3 倍、目标 0.1~3.0、
        一次最多 5 条），不合规的会被拦下并在返回里说明原因。

    actions 每项格式：
      {"action": "delete_sub_channel"|"adjust_budget"|"adjust_target",
       "target": "ch_003" 或 "US_A_01",
       "params": {"country": "US", "new_budget": 40} 或 {"country": "US", "new_target": 0.65},
       "reason": "为什么做这个动作，依据了哪些数据"}

    reasoning: 整体推理链，说明你看了什么数据、为什么得出这个判断。
    """
    import guardrails
    import dim_scd

    actions = actions or []
    if not isinstance(actions, list):
        return {"ok": False, "message": "actions 必须是数组"}

    # 批量预检：数量上限 + 同对象去重
    accepted, batch_rejected = guardrails.check_batch(actions)

    # 逐条业务边界校验：拿当前真实配置算变动幅度
    passed, blocked = [], []
    for a in accepted:
        current = {}
        a_type = a.get("action")
        target = a.get("target") or ""
        country = (a.get("params") or {}).get("country", "")
        if a_type in ("adjust_budget", "adjust_target"):
            current = agent._get_current_cfg(target, country)

        ok, why = guardrails.validate_action(a, current)
        if ok:
            passed.append(a)
        else:
            blocked.append({"action": a_type, "target": target, "reason": why})

    # 一条都不合规就明确拒绝，不落空建议单
    if not passed:
        return {
            "ok": False,
            "message": "没有可提交的建议：全部被护栏拦下或格式不合规",
            "blocked": blocked,
            "batch_rejected": batch_rejected,
        }

    run_id = dim_scd.save_agent_run(
        status="pending",
        conclusion=f"MCP 客户端提交了 {len(passed)} 条建议",
        reasoning=reasoning or "",
        actions=passed,
        executed=[],
        trace=[],
    )

    return {
        "ok": True,
        "run_id": run_id,
        "message": f"建议单 {run_id} 已创建，等待人工确认后才会执行",
        "accepted_count": len(passed),
        "blocked_count": len(blocked) + len(batch_rejected),
        "blocked": blocked + [{"action": None, "target": None, "reason": r.get("reason")}
                              for r in batch_rejected],
        "notice": "注意：你的建议尚未执行。不要向用户声称已经完成操作。",
    }


@mcp.tool()
def list_suggestions(status: str = "pending", limit: int = 10) -> Dict[str, Any]:
    """查看优化建议单列表及其状态（只读）。

    触发场景：想知道自己之前提交的建议有没有被人确认执行。

    status: pending=待确认 / executed=已执行 / rejected=已放弃
    limit: 最多返回几条
    """
    import dim_scd

    rows = dim_scd.list_agent_runs(limit=limit, status=status) \
        if hasattr(dim_scd, "list_agent_runs") else []

    return {
        "status": status,
        "count": len(rows),
        "items": [
            {"run_id": r.get("run_id"), "status": r.get("status"),
             "conclusion": r.get("conclusion"),
             "created_at": str(r.get("created_at", ""))}
            for r in rows
        ],
    }


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    # 支持 --inspect 参数：列出所有工具（不启动 server，方便调试）
    if "--inspect" in sys.argv:
        print("=== MCP Server 已注册的工具 ===")
        for name in ("get_metric", "get_logs", "get_top_history",
                     "get_trend_with_ops", "search_knowledge",
                     "get_metric_definition",
                     "submit_suggestion", "list_suggestions"):
            print(f"  - {name}")
        print()
        print("共 8 个工具：6 个只读查询 + 1 个提交建议（不执行）+ 1 个查建议状态")
        print("写操作不直接暴露——AI 只能提建议，必须经人工 approve。")
        sys.exit(0)

    # 默认：stdio transport 启动 MCP Server
    mcp.run()
