"""
MCP Server：把 IAA 中台的 8 个工具封装成标准 MCP 工具，供任意 Agent 调用。

背景（2026 标准）：
  之前 agent.py 里的工具是「手写 Function Calling」，只有 agent.py 自己能用。
  用 MCP（Model Context Protocol）封装后，任何支持 MCP 的 Agent（Claude、GPT、
  以及你自己未来换的其他模型）都能通过标准接口发现并调用这些能力。

  这解决的是 N×M → N+M 的集成问题：每个数据能力写一个 MCP Server，每个模型
  写一个 MCP Client，之后任意组合自动连通。

设计原则：
  - 复用 agent.py 里已有的工具实现（_tool_get_metric 等），不重写逻辑，
    保证「语义层 / 数仓 / 知识库」只有一份代码、一个口径。
  - 写工具（delete/adjust）复用 platform_layer 的 mock 平台。
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

# 写操作复用 mock 平台（与 agent.py 一致）
_plat = platform_layer.get_platform("mock")


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
# 写工具（危险操作，复用 mock 平台）
# ============================================================

@mcp.tool()
def delete_sub_channel(target: str, reason: str = "Agent 自动优化") -> Dict[str, Any]:
    """删除一个子渠道（标记停用，不可逆）。

    前置条件（必须满足才调用）：① D7 ROI 明显低于 70% 目标；② 已确认是「持续差」而非单日暴跌；
    ③ 已确认近期未删除过。宁可多查一轮，不可误删。
    target: 要删除的子渠道ID，如 ch_003
    """
    return _plat.delete_sub_channel(target, operator="agent", reason=reason)


@mcp.tool()
def adjust_target(target: str, country: str, new_target: float, reason: str = "Agent 自动优化") -> Dict[str, Any]:
    """调整某系列某国家的目标值（ROAS%，如 0.7 表示 70%）。

    触发场景：当前目标值不合理（过高误判、过低误删），需要校准。
    target: 系列ID，如 US_A_01；country: 国家代码，如 US；new_target: 新目标值（小数）
    """
    return _plat.adjust_target(target, country, new_target, operator="agent", reason=reason)


@mcp.tool()
def adjust_budget(target: str, country: str, new_budget: float, reason: str = "Agent 自动优化") -> Dict[str, Any]:
    """调整某系列某国家的预算（美元）。

    触发场景：表现好（ROI达标且量足）加预算放大，表现差降预算控制损失。
    target: 系列ID；country: 国家代码；new_budget: 新预算（美元）
    """
    return _plat.adjust_budget(target, country, new_budget, operator="agent", reason=reason)


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    # 支持 --inspect 参数：列出所有工具（不启动 server，方便调试）
    if "--inspect" in sys.argv:
        print("=== MCP Server 已注册的工具 ===")
        for name in ("get_metric", "get_logs", "get_top_history",
                     "get_trend_with_ops", "search_knowledge",
                     "get_metric_definition", "delete_sub_channel",
                     "adjust_target", "adjust_budget"):
            print(f"  - {name}")
        print("\n共 9 个工具（含语义层 get_metric_definition）")
        sys.exit(0)

    # 默认：stdio transport 启动 MCP Server
    mcp.run()
