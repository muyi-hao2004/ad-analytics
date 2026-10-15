"""
Agent 决策核心：快照组装 + 多轮 Function Calling + 决策解析

这是"中台"从【可操作】走向【自动优化】的关键一环。

分层结构：
  1. build_snapshot()   —— 把「派生指标 + 当前配置 + 最近日志」压缩成精简 JSON（日报的数据版）
  2. TOOLS_DEFINITION   —— 工具说明书（告诉 LLM 有哪些工具、参数怎么填）
  3. TOOLS_REGISTRY     —— 名字→函数 的查表（你的代码真正执行的地方）
  4. run_agent()        —— 多轮循环：LLM 查数据 → 你执行 → 结果回传 → LLM 再查/给决策

设计原则（为什么这么做）：
  - 不喂 HTML 报表（那是给人看的界面，LLM 读表格 HTML 又慢又易错）
  - 不喂原始事实表（太大、太细，且让 LLM 自己算派生指标容易错）
  - 快照只给「结论」，需要细节让 LLM 通过 get_metric/get_logs 按需查（多轮 Function Calling）
  - LLM 从头到尾不执行任何代码，它只「表达意图」，你的代码查表执行
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).parent))

import config
import dim_scd
import knowledge_base
import semantic_layer
from dotenv import load_dotenv

load_dotenv(config.PROJECT_ROOT / ".env")


# ============================================================
# 1. 快照组装：把数仓结果压缩成 LLM 能读懂的精简 JSON
# ============================================================

def build_snapshot(top_n: int = 20) -> Dict[str, Any]:
    """
    组装给 LLM 的"日报数据版"快照。

    包含三类信息：
      1. 派生指标（ADS 层）：D7 ROI、留存、eCPM、CPI —— 已经是结论，直接看
      2. 当前配置（维度表）：每个系列的策略、目标值、预算 —— LLM 要知道现状
      3. 最近操作日志：最近调了什么 —— 避免重复调整、知道历史

    Args:
        top_n: 每个维度最多取多少行（避免 token 爆炸）

    Returns:
        快照 dict，可直接 json.dumps 喂给 LLM
    """
    snapshot: Dict[str, Any] = {}

    # ---- 1.1 派生指标：素材/子渠道前后端表（判断"删不删"的核心）----
    snapshot["素材表现"] = _load_top("creative_table.parquet", top_n)
    snapshot["子渠道表现"] = _load_top("sub_channel_table.parquet", top_n)

    # ---- 1.2 当前配置：每个系列的策略 + 目标值 + 预算 ----
    snapshot["当前配置"] = _current_config()

    # ---- 1.3 最近操作日志（最近 10 条）----
    snapshot["最近操作"] = dim_scd.list_logs(limit=10)

    # ---- 1.4 业务红线（LLM 判断"要不要优化"的基准）----
    snapshot["业务目标"] = {
        "D7_ROAS_目标": f"{config.D7_ROAS_TARGET:.0%}",
        "说明": "D7 ROI 低于该值说明变现效率不足，需要优化",
    }

    return snapshot


def _load_top(filename: str, top_n: int) -> List[Dict[str, Any]]:
    """
    读 ADS 层 parquet，按「维度ID」聚合后取 Top N（最差的排前面）。

    为什么聚合：ADS 层每个维度 ID 有多个 install_date 的行（比如一个素材有 8 天的数据）。
    如果不聚合直接 head(top_n)，取到的会是同一个 ID 的不同日期，把其他维度全挤掉，
    导致 LLM 只看得到一个素材/子渠道。

    聚合逻辑：
      - 同一维度 ID 的多天数据汇总 spend / installs 等
      - D7 ROI 用「总回收 / 总花费」重新加权，而不是简单平均
      - 按加权 ROI 升序排，最该优化的排最前
    """
    import pandas as pd
    path = config.ADS_DIR / filename
    if not path.exists():
        return []
    df = pd.read_parquet(path)

    # 确定维度 ID 列名（素材表是 creative_id，子渠道表是 sub_channel）
    if "creative_id" in df.columns:
        dim_col = "creative_id"
    elif "sub_channel" in df.columns:
        dim_col = "sub_channel"
    else:
        return df.head(top_n).to_dict(orient="records")

    agg: Dict[str, Any] = {dim_col: "first"}
    sum_cols = ["spend", "clicks", "buy_impressions", "installs"]
    for c in sum_cols:
        if c in df.columns:
            agg[c] = "sum"

    grouped = df.groupby(dim_col, as_index=False).agg(agg)

    # D7 ROI 用「回收/花费」重新加权：回收额 ≈ spend * d7_roi（按天加权求和 / 总花费）
    if "d7_roi" in df.columns and "spend" in df.columns:
        df = df.copy()
        df["_rev"] = df["spend"] * df["d7_roi"]          # 每天回收额
        rev = df.groupby(dim_col)["_rev"].sum()
        spend = df.groupby(dim_col)["spend"].sum()
        grouped["d7_roi"] = grouped[dim_col].map(rev / spend)   # 加权 ROI
    elif "d7_roi" in df.columns:
        grouped["d7_roi"] = df.groupby(dim_col)["d7_roi"].mean()

    # 带一个「近 N 天累计」标记，方便 LLM 理解这是多天汇总
    grouped["days"] = df.groupby(dim_col)["install_date"].nunique().values

    # 按加权 ROI 升序，最差的排前面
    if "d7_roi" in grouped.columns:
        grouped = grouped.sort_values("d7_roi", ascending=True)

    return grouped.head(top_n).to_dict(orient="records")


def _current_config() -> List[Dict[str, Any]]:
    """当前生效的系列配置（策略 + 投放人 + 渠道 + 各国目标值/预算）。"""
    campaigns = dim_scd.list_all_current()
    result = []
    for c in campaigns:
        item = {
            "campaign_id": c["campaign_id"],
            "strategy": c["strategy"],
            "owner": c["owner"],
            "channel": c["channel"],
        }
        # 该系列的国家配置（目标值/预算）
        countries = _list_countries(c["campaign_id"])
        item["countries"] = countries
        result.append(item)
    return result


def _list_countries(campaign_id: str) -> List[Dict[str, Any]]:
    """某系列当前生效的国家配置。"""
    import sqlite3
    conn = sqlite3.connect(dim_scd.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT country, budget, target FROM dim_campaign_country
               WHERE campaign_id=? AND is_current=1""",
            (campaign_id,)
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ============================================================
# 2. 工具定义（说明书）+ 工具注册表（查表执行）
# ============================================================

# ---- 2.1 工具说明书：告诉 LLM「有哪些工具、参数怎么填」 ----
# 这是 Function Calling 的核心。LLM 读了它就知道该输出什么样的 JSON。
# 参数里的 enum 是「约束」，防止 LLM 乱填不存在的值。

TOOLS_DEFINITION = [
    {
        "name": "get_metric",
        "description": "查「单个对象」某指标最近几天的逐日明细（下钻）。"
                       "触发场景：快照里某对象的聚合值异常（如 ROI 低），但需要看它「每天」的变化来定位是不是单日暴跌导致的。"
                       "边界：只看一个对象的趋势，不做跨对象横向对比（对比用 get_top_history）；"
                       "不附带操作日志（看因果用 get_trend_with_ops）。",
        "parameters": {
            "type": "object",
            "properties": {
                "obj_type": {
                    "type": "string",
                    "enum": ["creative", "sub_channel"],
                    "description": "对象类型：creative=素材，sub_channel=子渠道",
                },
                "obj_id": {
                    "type": "string",
                    "description": "对象ID，如 crt_002 或 ch_003",
                },
                "metric": {
                    "type": "string",
                    "enum": ["d7_roi", "d7_retention", "cpi", "spend", "installs"],
                    "description": "要看的指标（d7_roi=D7回报率，d7_retention=D7留存，cpi=安装成本，spend=花费，installs=安装数）",
                },
                "days": {
                    "type": "integer",
                    "description": "看最近几天（默认 8）",
                },
            },
            "required": ["obj_type", "obj_id", "metric"],
        },
    },
    {
        "name": "get_logs",
        "description": "查某个对象的「操作历史」（谁在什么时候删过/调过它）。"
                       "触发场景：决定要删/调某个对象之前，必须先查它最近是否已被操作过，避免重复操作或误删。"
                       "边界：只返回操作记录，不返回指标数据（指标用 get_metric/get_top_history）；"
                       "若要把操作和指标对齐看因果，用 get_trend_with_ops（一次返回两者）。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "关键词（如子渠道ID ch_003、系列ID US_A_01），匹配日志里的 campaign_id/field 等字段",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回几条（默认 10）",
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_top_history",
        "description": "查历史「头部/尾部」对象排行（横向对比）。按维度聚合，返回表现最好和最差的各 top_n 个。"
                       "触发场景：想判断某对象是「一贯很差」还是「只是近期变差」——用它在全量历史里定位它排第几。"
                       "边界：返回的是排行和聚合值，不是单对象逐日明细（逐日看 get_metric）；"
                       "这是精确数值查询，走数仓，不是文本经验（经验用 search_knowledge）。",
        "parameters": {
            "type": "object",
            "properties": {
                "dim": {
                    "type": "string",
                    "enum": ["app", "sub_channel", "creative_id", "campaign", "country"],
                    "description": "聚合维度：app=包，sub_channel=子渠道，creative_id=素材，campaign=系列，country=国家",
                },
                "metric": {
                    "type": "string",
                    "enum": ["d7_roi", "spend", "installs", "cpi"],
                    "description": "排行指标（只支持这 4 个，不含留存）：d7_roi=D7回报率，spend=花费，installs=安装，cpi=安装成本",
                },
                "top_n": {
                    "type": "integer",
                    "description": "头部和尾部各取几个（默认 5）",
                },
            },
            "required": ["dim"],
        },
    },
    {
        "name": "get_trend_with_ops",
        "description": "查某对象的「逐日趋势 + 当天操作日志」合并视图（因果归因专用）。"
                       "触发场景：想判断「某指标的变化是不是某次操作导致的」，它把趋势和操作对齐到同一时间轴。"
                       "边界：这是「趋势 + 操作」二合一，优先于单独查 get_metric + get_logs 两次；"
                       "但若你只需要趋势（不需要看操作），用更轻的 get_metric 即可。",
        "parameters": {
            "type": "object",
            "properties": {
                "obj_type": {
                    "type": "string",
                    "enum": ["sub_channel", "creative"],
                    "description": "对象类型：sub_channel=子渠道，creative=素材",
                },
                "obj_id": {
                    "type": "string",
                    "description": "对象ID，如 ch_003 或 crt_002",
                },
                "metric": {
                    "type": "string",
                    "enum": ["d7_roi", "d7_retention", "cpi", "spend", "installs"],
                    "description": "要看的指标（默认 d7_roi）",
                },
                "days": {
                    "type": "integer",
                    "description": "看最近几天（默认 8）",
                },
            },
            "required": ["obj_type", "obj_id"],
        },
    },
    {
        "name": "delete_sub_channel",
        "description": "删除一个子渠道（标记停用，不可逆）。"
                       "前置条件（必须全部满足才调用）：① 该子渠道 D7 ROI 明显低于 70% 目标；"
                       "② 已用 get_metric 确认是「持续差」而非单日暴跌；③ 已用 get_logs 确认近期未删除过。"
                       "边界：这是危险写操作，宁可多查一轮也不可误删；不确定就 finish 不动手。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "要删除的子渠道ID，如 ch_003。必须是你在前面已经用 get_metric/get_logs 确认过的对象ID，不要传未经验证的对象。"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "adjust_target",
        "description": "调整某系列某国家的目标值（ROAS%，如 0.7 表示 70%）。"
                       "触发场景：当前目标值不合理（过高导致误判、过低导致误删），需要校准目标。"
                       "边界：只改「目标」不改「预算」（改预算用 adjust_budget）；改之前先确认目标确实不合理。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "系列ID，如 US_A_01"},
                "country": {"type": "string", "description": "国家代码，如 US"},
                "new_target": {"type": "number", "description": "新目标值（小数，如 0.7）"},
            },
            "required": ["target", "country", "new_target"],
        },
    },
    {
        "name": "adjust_budget",
        "description": "调整某系列某国家的预算（美元）。"
                       "触发场景：表现好（ROI 达标且量充足）可加预算放大，表现差可降预算控制损失。"
                       "边界：只改「预算」不改「目标」（改目标用 adjust_target）；加预算前确认 ROI 稳定达标。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "系列ID，如 US_A_01"},
                "country": {"type": "string", "description": "国家代码，如 US"},
                "new_budget": {"type": "number", "description": "新预算（美元）"},
            },
            "required": ["target", "country", "new_budget"],
        },
    },
    {
        "name": "search_knowledge",
        "description": "检索优化师「行业经验/方法论」知识库（RAG，文本检索）。"
                       "触发场景：需要参考「这类问题通常怎么处理」的经验时，例如「ROI 低于目标该怎么下钻」「换素材的标准是什么」。"
                       "边界（重要）：它返回的是「文本经验」，不是精确数值；"
                       "能算出来的数字（ROI、排行、趋势）一律用 get_metric/get_top_history，不要用本工具查数值。"
                       "它也不替代数据分析——先查数据形成判断，再可选地用它补充方法论。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要检索的问题，用自然语言描述，如「D7 ROAS 低于 70% 该怎么优化」",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_metric_definition",
        "description": "查「指标的标准口径定义」——返回指标的准确公式、依赖字段、口径备注。"
                       "触发场景（重要，判断口径时必查）：当你需要用某个指标做判断、且不确定它的精确算法时，"
                       "必须先调本工具拿标准定义，而不是按字面猜。例如「D7 ROAS 到底怎么算」「买量 eCPM 和变现 eCPM 有什么区别」。"
                       "边界：它返回的是「口径定义」，不是「数值」；算具体数值仍用 get_metric/get_top_history。"
                       "这是语义层工具，是保证「算对」的基础——尤其要警惕同名不同义（如 eCPM 分买量/变现两套）。",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["d7_roas", "cpi", "buy_ctr", "buy_cvr", "buy_ecpm", "monetization_ecpm", "d7_retention", "rewarded_views_per_user"],
                    "description": "要查口径的指标 key。d7_roas=D7回报率，cpi=安装成本，buy_ctr/buy_cvr/buy_ecpm=买量侧CTR/CVR/eCPM，monetization_ecpm=变现eCPM，d7_retention=D7留存，rewarded_views_per_user=人均观次",
                },
            },
            "required": ["metric"],
        },
    },
]


# ---- 2.2 读工具的真实实现（参数化：一个函数吃一类查询）----
def _tool_get_metric(obj_type: str, obj_id: str, metric: str,
                     days: int = 8) -> Dict[str, Any]:
    """
    查素材/子渠道某指标的每日明细。

    参数化：靠 obj_type 区分查素材还是子渠道，metric 区分查哪个指标，
    一个函数覆盖「对象×指标」的无限组合，而不是为每种组合写一个函数。
    """
    import pandas as pd
    if obj_type == "creative":
        path = config.ADS_DIR / "creative_table.parquet"
        dim_col = "creative_id"
    elif obj_type == "sub_channel":
        path = config.ADS_DIR / "sub_channel_table.parquet"
        dim_col = "sub_channel"
    else:
        return {"error": f"未知对象类型 {obj_type}，只支持 creative/sub_channel"}

    if not path.exists():
        return {"error": "数据表不存在"}

    df = pd.read_parquet(path)
    sub = df[df[dim_col] == obj_id].sort_values("install_date")
    if sub.empty:
        return {"error": f"未找到 {obj_id} 的数据"}

    # 取最近 days 天
    sub = sub.tail(days)
    # 返回「日期 → 指标值」的列表，方便 LLM 看趋势
    series = [{"date": r["install_date"], metric: r.get(metric)}
              for r in sub.to_dict(orient="records")]
    return {"obj_type": obj_type, "obj_id": obj_id, "metric": metric,
            "data": series}


def _tool_get_logs(keyword: str, limit: int = 10) -> Dict[str, Any]:
    """按关键词查操作日志，判断对象是否被操作过。"""
    logs = dim_scd.list_logs(limit=100)
    # 模糊匹配：campaign_id / field / old_value / new_value 任一包含关键词
    matched = [l for l in logs
               if keyword and (keyword in str(l.get("campaign_id") or "")
                               or keyword in str(l.get("field") or "")
                               or keyword in str(l.get("old_value") or "")
                               or keyword in str(l.get("new_value") or ""))]
    return {"keyword": keyword, "count": len(matched),
            "logs": matched[:limit]}


def _tool_get_top_history(dim: str = "sub_channel", metric: str = "d7_roi",
                          top_n: int = 5) -> Dict[str, Any]:
    """
    查历史「头部/尾部」对象排行（按包/子渠道/素材/国家维度聚合）。

    这是「历史头部包分析」的正确实现：精确数值走数仓（不是 RAG）。
    从 ADS 层 daily_detail 聚合出「哪个包/渠道历史表现最好/最差」，
    供 LLM 做趋势判断和横向对比。

    Args:
        dim: 聚合维度（app / sub_channel / creative / campaign / country）
        metric: 排行指标（d7_roi / spend / installs / cpi）
        top_n: 返回头部 N 个 + 尾部 N 个（默认各 5 个）
    """
    import pandas as pd
    path = config.ADS_DIR / "daily_detail.parquet"
    if not path.exists():
        return {"error": "daily_detail 数据不存在"}

    df = pd.read_parquet(path)
    if dim not in df.columns:
        return {"error": f"维度 {dim} 不存在，可选: app/sub_channel/creative_id/campaign/country"}
    if metric not in df.columns:
        return {"error": f"指标 {metric} 不存在，可选: d7_roi/spend/installs/cpi"}

    # 按维度聚合：spend/installs 求和，d7_roi 用「总回收/总花费」加权
    agg = {"spend": "sum", "installs": "sum"}
    agg = {k: v for k, v in agg.items() if k in df.columns}
    grouped = df.groupby(dim, as_index=False).agg(agg)

    if metric == "d7_roi" and "spend" in df.columns and "d7_roi" in df.columns:
        df = df.copy()
        df["_rev"] = df["spend"] * df["d7_roi"]
        rev = df.groupby(dim)["_rev"].sum()
        spend = df.groupby(dim)["spend"].sum()
        grouped["d7_roi"] = grouped[dim].map(rev / spend)
    elif metric in df.columns and metric not in ("spend", "installs"):
        grouped[metric] = df.groupby(dim)[metric].mean()

    # 加天数（该维度出现在多少天）
    grouped["days"] = df.groupby(dim)["install_date"].nunique().values

    # 排序取头部/尾部
    if metric in grouped.columns:
        grouped = grouped.sort_values(metric, ascending=False)
    else:
        grouped = grouped.sort_values("spend", ascending=False)

    top = grouped.head(top_n).to_dict(orient="records")
    bottom = grouped.tail(top_n).to_dict(orient="records")

    return {
        "dim": dim, "metric": metric,
        "头部": top,      # 表现最好的（d7_roi 最高 / spend 最大）
        "尾部": bottom,   # 表现最差的（d7_roi 最低 / spend 最小）
    }


def _tool_get_trend_with_ops(obj_type: str, obj_id: str, metric: str = "d7_roi",
                             days: int = 8) -> Dict[str, Any]:
    """
    把「数仓趋势」和「关系库操作日志」按对象+时间对齐，一次返回。

    这是解决「Agent 如何结合历史趋势 + 操作日志判断因果」的关键工具：
      - 单看趋势（get_metric）不知道变化是不是人为操作导致的
      - 单看日志（get_logs）不知道操作后效果如何
      - 本工具把两者合并：返回「逐日指标 + 每天的操作用在哪个维度、改了什么」，
        Agent 一眼就能看出「某天调了目标值 → 次日 ROI 掉了 → 因果成立」。

    对齐策略（对象粒度不同是难点）：
      - 指标表(daily_detail)按 sub_channel/creative_id 记
      - 日志(operation_log)的 campaign_id+country 记目标值/预算操作，
        delete_sub_channel 的 old_value 里存 ch_xxx，change_creative 存 crt_xxx
      - 因此按 obj_id 匹配日志的 old_value/new_value/campaign_id 等字段，
        并把日志按天聚合到趋势的日期轴上
    """
    import pandas as pd

    # ---- 1. 取该对象的逐日指标趋势 ----
    path = config.ADS_DIR / "daily_detail.parquet"
    if not path.exists():
        return {"error": "daily_detail 数据不存在"}

    df = pd.read_parquet(path)
    if obj_type == "sub_channel":
        dim_col = "sub_channel"
    elif obj_type == "creative":
        dim_col = "creative_id"
    else:
        return {"error": f"未知对象类型 {obj_type}，只支持 sub_channel/creative"}

    if dim_col not in df.columns:
        return {"error": f"指标表无 {dim_col} 列"}
    if metric not in df.columns:
        return {"error": f"指标 {metric} 不存在"}

    sub = df[df[dim_col] == obj_id].sort_values("install_date")
    if sub.empty:
        return {"error": f"未找到 {obj_id} 的数据"}

    # 按天聚合（同一对象可能多 country/campaign，汇总 spend，加权 roi）
    trend_rows = []
    for date, g in sub.groupby("install_date"):
        row = {"date": date, "spend": round(float(g["spend"].sum()), 2)}
        if metric == "d7_roi" and "d7_roi" in g.columns and g["spend"].sum() > 0:
            rev = (g["spend"] * g["d7_roi"]).sum()
            row[metric] = round(float(rev / g["spend"].sum()), 4)
        else:
            row[metric] = round(float(g[metric].sum()), 2)
        trend_rows.append(row)
    trend_rows = trend_rows[-days:]

    # ---- 2. 取该对象的操作日志，并按天对齐 ----
    logs = dim_scd.list_logs(limit=200)
    # 匹配该对象相关的日志：old_value/new_value/campaign_id/field 任一包含 obj_id
    matched = []
    for l in logs:
        blob = " ".join(str(l.get(k) or "") for k in
                        ("campaign_id", "country", "field", "old_value", "new_value"))
        if obj_id in blob:
            matched.append(l)

    # 日志的 op_time 是 "YYYY-MM-DD HH:MM:SS"，取日期部分做 key
    ops_by_date: Dict[str, list] = {}
    for l in matched:
        op_date = str(l.get("op_time") or "")[:10]
        ops_by_date.setdefault(op_date, []).append(
            {"action": l.get("action"), "field": l.get("field"),
             "old": l.get("old_value"), "new": l.get("new_value"),
             "campaign": l.get("campaign_id"), "country": l.get("country")}
        )

    # ---- 3. 合并：每个日期带上「当天操作」 ----
    merged = []
    for row in trend_rows:
        ops = ops_by_date.get(row["date"], [])
        merged.append({**row, "操作": ops})

    return {
        "obj_type": obj_type, "obj_id": obj_id, "metric": metric,
        "趋势+操作": merged,
        "说明": "每个日期节点附带当天的操作记录；把「指标变化」和「操作」对齐到同一时间轴，可判断因果",
    }


def _tool_search_knowledge(query: str, top_k: int = 3) -> Dict[str, Any]:
    """
    检索优化师行业经验知识库（RAG）。

    与精确数值工具（get_metric/get_top_history）分工：
      - 精确数值 → 数仓 + Function Calling
      - 文本经验/方法论 → 这里（RAG 向量检索）
    """
    results = knowledge_base.search_knowledge(query, top_k=top_k)
    if not results:
        return {"query": query, "结果": [], "说明": "知识库为空（可能还没建库，先运行 knowledge_base.build_knowledge()）"}
    return {
        "query": query,
        "经验": [
            {"来源": r["source"], "内容": r["text"], "相关度": r["distance"]}
            for r in results
        ],
        "说明": "以上是按语义相关度排序的行业经验，供决策参考；距离越小越相关",
    }


def _tool_get_metric_definition(metric: str) -> Dict[str, Any]:
    """
    查指标的标准口径定义（语义层工具）。

    作用：让 LLM 在「判断口径」时先查标准答案，而不是对着裸表猜。
    尤其警惕同名不同义（eCPM 分买量/变现两套、CTR 分买量/变现）。
    """
    result = semantic_layer.get_metric_definition(metric)
    if "error" in result:
        return result
    return result


# ---- 2.3 工具注册表：名字 → 函数（加工具只在这里加一行）----
# 写工具（delete/adjust）复用 platform_layer 的 mock 平台实现。

def _build_tool_registry(plat):
    """把「读工具」和「写工具」统一装进一张名字→函数的表。"""
    return {
        "get_metric": _tool_get_metric,
        "get_logs": _tool_get_logs,
        "get_top_history": _tool_get_top_history,
        "get_trend_with_ops": _tool_get_trend_with_ops,
        "search_knowledge": _tool_search_knowledge,
        "get_metric_definition": _tool_get_metric_definition,
        "delete_sub_channel": lambda target, **kw: plat.delete_sub_channel(
            target, operator="agent", reason=kw.get("reason", "Agent 自动优化")),
        "adjust_target": lambda target, country, new_target, **kw: plat.adjust_target(
            target, country, new_target, operator="agent", reason=kw.get("reason", "Agent 自动优化")),
        "adjust_budget": lambda target, country, new_budget, **kw: plat.adjust_budget(
            target, country, new_budget, operator="agent", reason=kw.get("reason", "Agent 自动优化")),
    }


# ============================================================
# 3. LLM 调用（OpenAI 兼容接口，多轮 Function Calling）
# ============================================================

SYSTEM_PROMPT = """你是一个海外 IAA（网赚类）广告投放的资深优化师，负责分析中台数据并给出优化决策。

你的业务背景：
- 纯广告变现的休闲游戏，目标市场美国（T0）
- 核心指标 D7 ROAS 目标 70%（低于 70% 说明变现效率不足）

你的工作方式（多轮，重要）：
1. 先读用户给的「快照」（素材/子渠道的聚合表现 + 当前配置 + 业务目标）
2. 如果快照里的聚合值不足以判断，用 get_metric 下钻查某对象的每日明细，或用 get_logs 查它是否被操作过
3. 当你需要判断「某指标的变化是不是某次操作导致的」（因果归因）时，用 get_trend_with_ops，
   它会把逐日趋势和操作日志对齐到同一时间轴，能看清「操作前 vs 操作后」的指标变化
4. 查清楚后再做决策，避免误删（比如某素材聚合 ROI 低，但可能只是最后一天暴跌，要查明细确认）
5. 只有当所有信息都够了，才调用写操作（delete_sub_channel / adjust_target / adjust_budget）或输出 finish

工具使用优先级（重要）：
- 数据能直接判断 → 只用数据工具（get_metric / get_top_history / get_trend_with_ops），不要多余地查经验
- 数据查清了、但仍不确定「这类问题业界通常怎么处理」→ 此时才用 search_knowledge 检索经验做参考
- 经验是「补充参考」，不能替代数据判断，也不能作为写操作的唯一依据
- 口径判断（重要）：当你需要「用某个指标做判断、但不确定它的精确算法」时，先调 get_metric_definition 拿标准定义。
  尤其警惕「同名不同义」——eCPM 分买量（spend/buy_impressions×1000）和变现（ad_revenue/ad_impressions×1000）两套，
  CTR/CVR 也分买量侧和变现侧。拿不准口径就先查，不要按字面猜。

每一步输出格式（严格 JSON，不要输出其他文字）：
- 想查数据时：
  {"step": "tool", "name": "get_metric", "parameters": {"obj_type":"creative","obj_id":"crt_002","metric":"d7_roi","days":8}}
- 想做决策/结束分析时：
  {"step": "finish", "conclusion": "一句话总结", "reasoning": "你的完整推理链：你看了哪些数据、发现了什么、为什么得出这个结论、为什么不做某些操作", "actions": [{"action":"delete_sub_channel","target":"ch_003","params":{},"reason":"..."}]}

规则：
- 一次只输出一个 step（一个 tool 调用，或一个 finish）
- 不要重复查已经查过的数据
- 没有需要优化的就 finish 且 actions 为空数组
- finish 时必须在 reasoning 里写清楚「为什么这么做」和「为什么不做某些操作」，这是给人看的可解释性说明
"""


def _make_client():
    """构造 OpenAI 兼容 client（读 .env）。"""
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    if not api_key or not base_url:
        raise RuntimeError(
            "未配置 LLM。请在 iaa-analytics-agent/.env 里设置 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL。\n"
            "例如：\n"
            "  LLM_API_KEY=sk-xxx\n"
            "  LLM_BASE_URL=https://api.deepseek.com\n"
            "  LLM_MODEL=deepseek-chat\n"
        )
    from openai import OpenAI
    model = os.getenv("LLM_MODEL", "deepseek-chat")
    # timeout 防止 LLM 请求在网络层挂起（deepseek 偶发慢请求会导致任务卡死）
    return OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=2), model


def _call_once(client, model, messages) -> str:
    """单次调用 LLM，返回文本。"""
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=0.2, timeout=60.0,
    )
    return resp.choices[0].message.content


def run_llm_loop(snapshot: Dict[str, Any], plat,
                 max_rounds: int = 8) -> Dict[str, Any]:
    """
    多轮 Function Calling 主循环：LLM 查数据 → 你执行 → 结果回传 → 直到 finish。

    这是「标准 Function Calling」的完整形态：
      - LLM 输出 tool 调用 → 查表找到函数 → 执行 → 结果 json.dumps 变文字回传
      - LLM 读结果，继续查或输出 finish
      - max_rounds 防止 LLM 无限查下去（死循环）

    Returns:
        {"conclusion": ..., "actions": [...], "trace": [...]}
        trace 记录每一轮 LLM 做了什么（调试用）
    """
    client, model = _make_client()
    registry = _build_tool_registry(plat)

    system_msg = SYSTEM_PROMPT + "\n\n可用工具清单：\n" + json.dumps(
        TOOLS_DEFINITION, ensure_ascii=False, indent=2)

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": "这是中台数据快照：\n" +
         json.dumps(snapshot, ensure_ascii=False, default=str)},
    ]

    trace = []

    for round_no in range(max_rounds):
        llm_text = _call_once(client, model, messages)
        step = parse_llm_step(llm_text)          # 解析 LLM 这一步想干嘛

        if step.get("step") == "finish":
            # LLM 说「分析完了」，返回最终决策
            trace.append({"round": round_no + 1, "step": "finish",
                          "reasoning": step.get("reasoning", ""),
                          "raw": llm_text[:300]})
            return {
                "conclusion": step.get("conclusion", ""),
                "reasoning": step.get("reasoning", ""),
                "actions": step.get("actions", []),
                "trace": trace,
            }

        if step.get("step") == "tool":
            # LLM 想调工具 → 查表执行 → 结果回传
            name = step.get("name")
            params = step.get("parameters", {})
            func = registry.get(name)
            if func is None:
                result = {"error": f"未知工具 {name}"}
            else:
                try:
                    result = func(**params)       # 关键：按 name 找到函数，params 原样传入
                except Exception as e:
                    result = {"error": str(e)}

            # 关键：记录「调了什么 + 参数 + 返回了什么」，这是可解释性的客观证据
            trace.append({"round": round_no + 1, "step": "tool",
                          "name": name, "parameters": params,
                          "result": result})

            # 关键步骤：把执行结果变成文字，append 进对话，回传给 LLM
            messages.append({"role": "assistant", "content": llm_text})
            messages.append({"role": "user", "content":
                f"工具 {name} 返回结果：\n" +
                json.dumps(result, ensure_ascii=False, default=str)})
            continue

        # 既不是 tool 也不是 finish，让 LLM 重新按格式输出
        trace.append({"round": round_no + 1, "step": "unknown",
                      "raw": llm_text[:200]})
        messages.append({"role": "assistant", "content": llm_text})
        messages.append({"role": "user", "content":
            "你的输出不符合格式，请按约定的 JSON 格式重新输出（step 必须是 tool 或 finish）。"})

    return {"conclusion": "达到最大轮数仍未 finish", "reasoning": "",
            "actions": [], "trace": trace}


def parse_llm_step(llm_text: str) -> Dict[str, Any]:
    """解析 LLM 每一步输出的 JSON（容错：剥 ```json``` 包裹、找 {} 边界）。"""
    text = llm_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {"step": "unknown", "raw": llm_text[:200]}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"step": "unknown", "raw": llm_text[:200]}


# ============================================================
# 3. 决策解析：把 LLM 返回的 JSON 解析成结构化动作
# ============================================================

VALID_ACTIONS = {"delete_sub_channel", "adjust_budget", "adjust_target"}


def parse_decision(llm_text: str) -> Dict[str, Any]:
    """
    解析 LLM 返回的 JSON，提取 conclusion + actions。

    做了容错：LLM 可能输出 ```json ... ``` 包裹，或者前后有废话，都要能剥出来。
    """
    text = llm_text.strip()
    # 去掉可能的 markdown 代码块包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    # 找到第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 输出不是合法 JSON: {llm_text[:200]}")
    text = text[start:end + 1]

    data = json.loads(text)
    actions = data.get("actions", [])
    # 过滤非法动作
    valid = [a for a in actions if a.get("action") in VALID_ACTIONS]
    return {
        "conclusion": data.get("conclusion", ""),
        "actions": valid,
    }


# ============================================================
# 4. 主流程：快照 → 多轮 Function Calling → 执行写动作（闭环）
# ============================================================

def run_agent(dry_run: bool = True, max_rounds: int = 8) -> Dict[str, Any]:
    """
    完整跑一遍 Agent：组装快照 → 多轮 LLM（查数据/决策）→ 执行写动作。

    Args:
        dry_run: True 只分析不执行写操作，False 真正调平台改库
        max_rounds: LLM 最多查几轮（防死循环）

    Returns:
        {"conclusion": ..., "actions": [...], "trace": [...], "executed": [...]}
    """
    import platform_layer
    plat = platform_layer.get_platform("mock")

    # 1. 组装快照
    snapshot = build_snapshot()
    print("=" * 60)
    print("快照已组装（喂给 LLM 的初始数据）")
    print("=" * 60)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)[:1500])
    print("...\n")

    # 2. 多轮 Function Calling
    print("[1/2] 多轮 LLM 分析（LLM 会按需调工具查明细）...\n")
    decision = run_llm_loop(snapshot, plat, max_rounds=max_rounds)

    # 打印 trace，展示 LLM 每一轮做了什么（关键：能看到它查了哪些数据）
    print("\n--- 决策过程 trace（可解释性）---")
    for t in decision.get("trace", []):
        step = t.get("step")
        if step == "tool":
            print(f"  第{t['round']}轮 [查数据] {t.get('name')}({json.dumps(t.get('parameters'), ensure_ascii=False)})")
        elif step == "finish":
            print(f"  第{t['round']}轮 [给出决策]")
            reasoning = t.get("reasoning", "")
            if reasoning:
                print(f"    └ 推理链: {reasoning[:200]}")
        else:
            print(f"  第{t['round']}轮 [格式异常] {t.get('raw', '')[:100]}")
    print("--- trace 结束 ---\n")

    print("结论:", decision["conclusion"])
    print("动作数:", len(decision["actions"]))
    if decision.get("reasoning"):
        print("推理链:", decision["reasoning"])

    # 3. 执行写动作（只有写操作才真正落库）
    executed = []
    write_actions = [a for a in decision["actions"]
                     if a.get("action") in VALID_ACTIONS]
    if not dry_run and write_actions:
        print("\n[2/2] 执行写动作...")
        for a in write_actions:
            r = _execute_action(plat, a)
            executed.append(r)
            print(f"  - {a['action']} {a.get('target')}: {r.get('message')}")
    elif dry_run:
        print("\n[2/2] dry_run 模式，跳过执行（仅分析）")

    return {
        "conclusion": decision["conclusion"],
        "reasoning": decision.get("reasoning", ""),
        "actions": decision["actions"],
        "trace": decision.get("trace", []),
        "executed": executed,
    }


def _execute_action(plat, action: Dict[str, Any]) -> Dict[str, Any]:
    """把解析出的写动作转成平台调用。"""
    a_type = action["action"]
    target = action.get("target", "")
    params = action.get("params", {})
    reason = action.get("reason", "Agent 自动优化")

    if a_type == "delete_sub_channel":
        return plat.delete_sub_channel(target, operator="agent", reason=reason)
    if a_type == "adjust_budget":
        return plat.adjust_budget(target, params.get("country", ""),
                                  params.get("new_budget", 0),
                                  operator="agent", reason=reason)
    if a_type == "adjust_target":
        return plat.adjust_target(target, params.get("country", ""),
                                  params.get("new_target", 0),
                                  operator="agent", reason=reason)
    return {"ok": False, "message": f"未知动作 {a_type}"}


# ============================================================
# 5. 人工确认开关：Agent 出建议 → 人确认 → 才执行
# ============================================================

def run_agent_with_approval() -> Dict[str, Any]:
    """
    人机协作模式：Agent 只出「建议」（不执行），存成 pending 待确认单，
    返回给投放人。投放人看过 reasoning 和动作后，再决定 approve/reject。

    Returns:
        {"run_id": ..., "conclusion": ..., "reasoning": ..., "actions": [...], "trace": [...]}
        run_id 是这次建议的 ID，投放人凭它调用 approve/reject。
    """
    import platform_layer
    plat = platform_layer.get_platform("mock")

    # 1. 组装快照 + 多轮 LLM（只分析，不执行写操作）
    snapshot = build_snapshot()
    decision = run_llm_loop(snapshot, plat)

    # 2. 只保留「写动作」作为建议（读动作 get_xxx 不落库）
    write_actions = [a for a in decision.get("actions", [])
                     if a.get("action") in VALID_ACTIONS]

    # 3. 存成 pending 待确认单
    run_id = dim_scd.save_agent_run(
        status="pending",
        conclusion=decision.get("conclusion", ""),
        reasoning=decision.get("reasoning", ""),
        actions=write_actions,
        executed=[],
        trace=decision.get("trace", []),
    )

    return {
        "run_id": run_id,
        "conclusion": decision.get("conclusion", ""),
        "reasoning": decision.get("reasoning", ""),
        "actions": write_actions,
        "trace": decision.get("trace", []),
    }


def approve_agent_run(run_id: int) -> Dict[str, Any]:
    """
    投放人确认一条 pending 建议单，真正执行里面的写动作。

    Args:
        run_id: 待确认建议单的 ID

    Returns:
        {"ok": True, "run_id": ..., "executed": [...]}
    """
    import platform_layer
    run = dim_scd.get_agent_run(run_id)
    if run is None:
        return {"ok": False, "message": f"建议单 {run_id} 不存在"}
    if run["status"] != "pending":
        return {"ok": False, "message": f"建议单 {run_id} 状态为 {run['status']}，只有 pending 才能确认"}

    plat = platform_layer.get_platform("mock")
    executed = []
    for a in run["actions"]:
        r = _execute_action(plat, a)
        r["action"] = a.get("action", "")
        executed.append(r)

    # 更新状态为 executed
    dim_scd.update_agent_run(run_id, status="executed", executed=executed)

    return {"ok": True, "run_id": run_id, "executed": executed}


def reject_agent_run(run_id: int) -> Dict[str, Any]:
    """投放人放弃一条 pending 建议单（不执行）。"""
    run = dim_scd.get_agent_run(run_id)
    if run is None:
        return {"ok": False, "message": f"建议单 {run_id} 不存在"}
    if run["status"] != "pending":
        return {"ok": False, "message": f"建议单 {run_id} 状态为 {run['status']}，只有 pending 才能放弃"}
    dim_scd.update_agent_run(run_id, status="rejected")
    return {"ok": True, "run_id": run_id, "status": "rejected"}


if __name__ == "__main__":
    run_agent(dry_run=True)
