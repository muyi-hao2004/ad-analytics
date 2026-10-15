"""
维度表 SCD Type 2（缓慢变化维度）完整实现

用 SQLite 存储维度表，实现"配置变更时保留历史"：
  - 表结构：加 eff_start_date / eff_end_date / is_current 三个字段
  - 更新逻辑：值变了 → "关旧行 + 插新行"（不覆盖）
  - 查询逻辑：查"某天"的值，取当时生效的那一行

为什么用 SQLite：维度表的"关旧插新"本质是 UPDATE + INSERT，
用数据库表达最自然，也最接近真实生产（真实数仓维度表就在数据库里）。
"""

import sqlite3
from pathlib import Path
from datetime import datetime, date
from typing import List, Dict, Any, Optional

# 维度表数据库路径
DB_PATH = Path(__file__).parent.parent / "data" / "dim.db"


def _connect():
    """建立 SQLite 连接。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # 让查询结果可以用 row["字段名"] 访问
    return conn


def init_dim_tables() -> None:
    """
    建维度表（如果不存在）。

    dim_campaign：系列维度表，含 SCD Type 2 的 3 个时间字段
    dim_campaign_country：系列×国家配置表（预算/目标值），同样含 SCD 字段
    dim_sub_channel：子渠道维度表，含 is_active 状态（删除=置 0，保留历史）
    operation_log：操作审计日志（append-only，记录 Agent/投放人的每次调整）
    """
    conn = _connect()
    try:
        cur = conn.cursor()

        # 系列维度表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dim_campaign (
                campaign_id     TEXT NOT NULL,
                campaign_name   TEXT,
                strategy        TEXT,          -- D7_ROAS / D0_ROAS / CPI
                owner           TEXT,          -- 投放人
                channel         TEXT,          -- 渠道
                eff_start_date  TEXT NOT NULL, -- 生效开始
                eff_end_date    TEXT,          -- 生效结束（NULL=至今）
                is_current      INTEGER,       -- 是否当前生效（1=是）
                PRIMARY KEY (campaign_id, eff_start_date)
            )
        """)

        # 系列×国家配置表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dim_campaign_country (
                campaign_id     TEXT NOT NULL,
                country         TEXT NOT NULL,
                budget          REAL,          -- 预算（美元）
                target          REAL,          -- 目标值（ROAS% 或 CPI$）
                eff_start_date  TEXT NOT NULL,
                eff_end_date    TEXT,
                is_current      INTEGER,
                PRIMARY KEY (campaign_id, country, eff_start_date)
            )
        """)

        # 子渠道维度表（删除=置 is_active=0，不物理删除，保留历史）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dim_sub_channel (
                sub_channel_id  TEXT NOT NULL,
                sub_channel_name TEXT,          -- 归因过来的名字（复杂，用 id 为主键）
                app             TEXT,           -- 所属包
                channel         TEXT,           -- 渠道
                is_active       INTEGER DEFAULT 1,  -- 1=启用 0=已删除
                eff_start_date  TEXT NOT NULL,
                eff_end_date    TEXT,
                PRIMARY KEY (sub_channel_id, eff_start_date)
            )
        """)

        # 操作审计日志表（append-only，永不修改/覆盖）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS operation_log (
                log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                op_time     TEXT NOT NULL,      -- 操作时间 YYYY-MM-DD HH:MM:SS
                operator    TEXT NOT NULL,      -- 操作者：agent 或 投放人名字
                action      TEXT NOT NULL,      -- 动作类型
                app         TEXT,               -- 包名
                channel     TEXT,               -- 渠道
                campaign_id TEXT,               -- 系列ID（可空）
                country     TEXT,               -- 国家（可空）
                field       TEXT,               -- 改的字段（budget/target/...）
                old_value   TEXT,               -- 改前值
                new_value   TEXT,               -- 改后值
                reason      TEXT                -- 调整原因（LLM 结论）
            )
        """)

        # Agent 运行历史表（每次跑一轮 Agent 记录一条，人工确认开关的核心）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_run_history (
                run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_time    TEXT NOT NULL,      -- 运行时间
                status      TEXT NOT NULL,      -- pending=待确认 approved=已确认执行 rejected=已放弃 executed=已执行
                conclusion  TEXT,               -- 结论
                reasoning   TEXT,               -- 推理链（可解释性）
                actions_json TEXT,              -- 建议动作（JSON 数组）
                executed_json TEXT,             -- 实际执行结果（JSON）
                trace_json  TEXT                -- 多轮决策轨迹（JSON）
            )
        """)

        conn.commit()
    finally:
        conn.close()


# 支持的动作类型（约束取值，避免脏数据）
ACTION_TYPES = {
    "create_campaign",      # 新建系列
    "adjust_budget",        # 调整预算
    "adjust_target",        # 调整目标值（ROAS%/CPI$）
    "change_creative",      # 换素材
    "delete_sub_channel",   # 删除子渠道
    "add_country",          # 上新国家
    "pause_campaign",       # 暂停系列
    "resume_campaign",      # 恢复系列
}


def upsert_campaign(campaign: Dict[str, Any], as_of: Optional[str] = None) -> None:
    """
    把一条系列配置写进维度表（SCD Type 2 + 做法2：同天合并）。

    三种情况：
      1. 当天还没有该系列的任何记录 → 插新行（eff_start_date=当天）
      2. 当天已有记录，且就是当前生效行（is_current=1）→ 原地 UPDATE（同天只留最终值）
      3. 当天已有记录，但不是当前生效行 → 关掉更早的 current 行，把"当天"这行拉回 current

    注意：这里只处理"系列级"属性；"系列×国家"的预算/目标值走 upsert_campaign_country。

    Args:
        campaign: 系列配置 dict，含 campaign_id, campaign_name, strategy, owner, channel
        as_of: 生效日期（YYYY-MM-DD），默认今天
    """
    eff_date = as_of or datetime.now().strftime("%Y-%m-%d")
    conn = _connect()
    try:
        cur = conn.cursor()

        # 先看"当天"是否已经有该系列的一条记录（做法2：同天合并的依据）
        cur.execute(
            "SELECT * FROM dim_campaign WHERE campaign_id=? AND eff_start_date=?",
            (campaign["campaign_id"], eff_date)
        )
        same_day = cur.fetchone()

        if same_day is not None:
            # 情况2/3：当天已有一条记录 → 原地更新最终值，不插新行
            cur.execute(
                """UPDATE dim_campaign
                   SET campaign_name=?, strategy=?, owner=?, channel=?, is_current=1
                   WHERE campaign_id=? AND eff_start_date=?""",
                (campaign.get("campaign_name"), campaign.get("strategy"),
                 campaign.get("owner"), campaign.get("channel"),
                 campaign["campaign_id"], eff_date)
            )
            # 如果之前有另一条 current 行（更早的），把它关掉
            cur.execute(
                """UPDATE dim_campaign
                   SET eff_end_date=?, is_current=0
                   WHERE campaign_id=? AND is_current=1 AND eff_start_date<>?""",
                (eff_date, campaign["campaign_id"], eff_date)
            )
        else:
            # 情况1：当天没有记录 → 判断是否要"关旧+插新"
            cur.execute(
                "SELECT * FROM dim_campaign WHERE campaign_id=? AND is_current=1",
                (campaign["campaign_id"],)
            )
            current = cur.fetchone()

            if current is None:
                # 全新系列，第一次出现 → 直接插
                cur.execute(
                    """INSERT INTO dim_campaign
                       (campaign_id, campaign_name, strategy, owner, channel,
                        eff_start_date, eff_end_date, is_current)
                       VALUES (?,?,?,?,?,?, NULL, 1)""",
                    (campaign["campaign_id"], campaign.get("campaign_name"),
                     campaign.get("strategy"), campaign.get("owner"),
                     campaign.get("channel"), eff_date)
                )
            else:
                # 判断"关键属性"是否变化
                changed = (
                    current["campaign_name"] != campaign.get("campaign_name")
                    or current["strategy"] != campaign.get("strategy")
                    or current["owner"] != campaign.get("owner")
                    or current["channel"] != campaign.get("channel")
                )
                if changed:
                    # 值变了 → 关旧 + 插新
                    cur.execute(
                        """UPDATE dim_campaign
                           SET eff_end_date=?, is_current=0
                           WHERE campaign_id=? AND is_current=1""",
                        (eff_date, campaign["campaign_id"])
                    )
                    cur.execute(
                        """INSERT INTO dim_campaign
                           (campaign_id, campaign_name, strategy, owner, channel,
                            eff_start_date, eff_end_date, is_current)
                           VALUES (?,?,?,?,?,?, NULL, 1)""",
                        (campaign["campaign_id"], campaign.get("campaign_name"),
                         campaign.get("strategy"), campaign.get("owner"),
                         campaign.get("channel"), eff_date)
                    )
                # 没变 → 什么都不做

        conn.commit()
    finally:
        conn.close()


def upsert_campaign_country(campaign_id: str, country_cfg: Dict[str, Any],
                            as_of: Optional[str] = None,
                            operator: Optional[str] = None,
                            app: Optional[str] = None,
                            channel: Optional[str] = None,
                            reason: Optional[str] = None) -> None:
    """
    把"系列×国家"的预算/目标值写进维度表（SCD Type 2 + 做法2：同天合并）。

    与 upsert_campaign 同样的三情况逻辑，只是多一个 country 维度。
    当预算或目标值发生变化时，会自动写一条操作日志（需要传入 operator 才有意义）。

    Args:
        campaign_id: 所属系列
        country_cfg: {"country": "US", "budget": 1000.0, "target": 0.70}
        as_of: 生效日期
        operator: 操作者（"agent" 或投放人名字），传了才会记日志
        app: 包名（记日志用）
        channel: 渠道（记日志用）
        reason: 调整原因（记日志用）
    """
    eff_date = as_of or datetime.now().strftime("%Y-%m-%d")
    conn = _connect()
    try:
        cur = conn.cursor()

        # 归一化：budget/target 传 None 表示"不改这个字段"，用当前生效值回填，
        # 避免把 None 误当成"改成空"而触发变化判断、误记日志。
        cur.execute(
            """SELECT budget, target FROM dim_campaign_country
               WHERE campaign_id=? AND country=? AND is_current=1""",
            (campaign_id, country_cfg["country"])
        )
        cur_row = cur.fetchone()
        if cur_row is not None:
            if country_cfg.get("budget") is None:
                country_cfg["budget"] = cur_row["budget"]
            if country_cfg.get("target") is None:
                country_cfg["target"] = cur_row["target"]

        # 先看"当天"是否已有该系列×国家的记录（做法2）
        cur.execute(
            """SELECT * FROM dim_campaign_country
               WHERE campaign_id=? AND country=? AND eff_start_date=?""",
            (campaign_id, country_cfg["country"], eff_date)
        )
        same_day = cur.fetchone()

        if same_day is not None:
            # 同天合并：原地更新最终值
            cur.execute(
                """UPDATE dim_campaign_country
                   SET budget=?, target=?, is_current=1
                   WHERE campaign_id=? AND country=? AND eff_start_date=?""",
                (country_cfg.get("budget"), country_cfg.get("target"),
                 campaign_id, country_cfg["country"], eff_date)
            )
            cur.execute(
                """UPDATE dim_campaign_country
                   SET eff_end_date=?, is_current=0
                   WHERE campaign_id=? AND country=? AND is_current=1 AND eff_start_date<>?""",
                (eff_date, campaign_id, country_cfg["country"], eff_date)
            )
            # 同天值变了也记日志（做法2 下，同天多次调整每次都留痕）
            if same_day["budget"] != country_cfg.get("budget"):
                _log_inline(cur, operator, "adjust_budget", app, channel,
                            campaign_id, country_cfg["country"], "budget",
                            same_day["budget"], country_cfg.get("budget"), reason)
            if same_day["target"] != country_cfg.get("target"):
                _log_inline(cur, operator, "adjust_target", app, channel,
                            campaign_id, country_cfg["country"], "target",
                            same_day["target"], country_cfg.get("target"), reason)
        else:
            cur.execute(
                """SELECT * FROM dim_campaign_country
                   WHERE campaign_id=? AND country=? AND is_current=1""",
                (campaign_id, country_cfg["country"])
            )
            current = cur.fetchone()

            if current is None:
                cur.execute(
                    """INSERT INTO dim_campaign_country
                       (campaign_id, country, budget, target,
                        eff_start_date, eff_end_date, is_current)
                       VALUES (?,?,?,?,?, NULL, 1)""",
                    (campaign_id, country_cfg["country"],
                     country_cfg.get("budget"), country_cfg.get("target"), eff_date)
                )
            else:
                changed = (
                    current["budget"] != country_cfg.get("budget")
                    or current["target"] != country_cfg.get("target")
                )
                if changed:
                    cur.execute(
                        """UPDATE dim_campaign_country
                           SET eff_end_date=?, is_current=0
                           WHERE campaign_id=? AND country=? AND is_current=1""",
                        (eff_date, campaign_id, country_cfg["country"])
                    )
                    cur.execute(
                        """INSERT INTO dim_campaign_country
                           (campaign_id, country, budget, target,
                            eff_start_date, eff_end_date, is_current)
                           VALUES (?,?,?,?,?, NULL, 1)""",
                        (campaign_id, country_cfg["country"],
                         country_cfg.get("budget"), country_cfg.get("target"), eff_date)
                    )
                    # 值变了 → 记日志
                    if current["budget"] != country_cfg.get("budget"):
                        _log_inline(cur, operator, "adjust_budget", app, channel,
                                    campaign_id, country_cfg["country"], "budget",
                                    current["budget"], country_cfg.get("budget"), reason)
                    if current["target"] != country_cfg.get("target"):
                        _log_inline(cur, operator, "adjust_target", app, channel,
                                    campaign_id, country_cfg["country"], "target",
                                    current["target"], country_cfg.get("target"), reason)

        conn.commit()
    finally:
        conn.close()


def get_campaign_as_of(campaign_id: str, as_of: str) -> Optional[Dict[str, Any]]:
    """
    查询"某天"该系列的配置（SCD 的核心查询：取当时生效的那一行）。

    Args:
        campaign_id: 系列ID
        as_of: 查询日期（YYYY-MM-DD）

    Returns:
        当时的配置 dict，找不到返回 None
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM dim_campaign
               WHERE campaign_id=?
                 AND eff_start_date <= ?
                 AND (eff_end_date IS NULL OR eff_end_date > ?)
               ORDER BY eff_start_date DESC
               LIMIT 1""",
            (campaign_id, as_of, as_of)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_country_as_of(campaign_id: str, country: str, as_of: str) -> Optional[Dict[str, Any]]:
    """查询"某天"某系列某国家的预算/目标值。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM dim_campaign_country
               WHERE campaign_id=? AND country=?
                 AND eff_start_date <= ?
                 AND (eff_end_date IS NULL OR eff_end_date > ?)
               ORDER BY eff_start_date DESC
               LIMIT 1""",
            (campaign_id, country, as_of, as_of)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_all_current() -> List[Dict[str, Any]]:
    """列出所有当前生效的系列（调试用）。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM dim_campaign WHERE is_current=1")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _log_inline(cur, operator: Optional[str], action: str,
                app: Optional[str], channel: Optional[str],
                campaign_id: Optional[str], country: Optional[str],
                field: Optional[str], old_value: Any, new_value: Any,
                reason: Optional[str]) -> None:
    """
    在已有事务（同一个 cursor）内写一条操作日志，避免重复开连接。

    供 upsert_campaign_country 在值变化时调用。operator 为空则跳过（表示只是初始化、非人为调整）。
    """
    if operator is None:
        return  # 没有操作者，不记日志（例如造数初始化时静默写入）
    cur.execute(
        """INSERT INTO operation_log
           (op_time, operator, action, app, channel, campaign_id, country,
            field, old_value, new_value, reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            operator, action, app, channel, campaign_id, country,
            field,
            str(old_value) if old_value is not None else None,
            str(new_value) if new_value is not None else None,
            reason,
        )
    )


def log_operation(operator: str, action: str, app: Optional[str] = None,
                  channel: Optional[str] = None, campaign_id: Optional[str] = None,
                  country: Optional[str] = None, field: Optional[str] = None,
                  old_value: Optional[Any] = None, new_value: Optional[Any] = None,
                  reason: Optional[str] = None) -> None:
    """
    写一条操作审计日志（append-only，永不修改）。

    用途：记录 Agent 或投放人的每次策略调整，供中台展示 + LLM 做历史判断。

    Args:
        operator: 操作者（"agent" 或投放人名字，如 "张三"）
        action:  动作类型，必须是 ACTION_TYPES 之一（或自定义字符串）
        app:     包名
        channel: 渠道
        campaign_id / country / field: 定位信息
        old_value / new_value: 改前/改后值（自动转成字符串存）
        reason:  调整原因（LLM 结论）
    """
    if action not in ACTION_TYPES:
        # 不在预设列表里也不报错，但提示一下（允许扩展动作）
        pass

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO operation_log
               (op_time, operator, action, app, channel, campaign_id, country,
                field, old_value, new_value, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                operator, action, app, channel, campaign_id, country,
                field,
                str(old_value) if old_value is not None else None,
                str(new_value) if new_value is not None else None,
                reason,
            )
        )
        conn.commit()
    finally:
        conn.close()


def list_logs(limit: int = 50, operator: Optional[str] = None,
              app: Optional[str] = None, channel: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    查询操作日志（按时间倒序，最新在前）。

    Args:
        limit: 最多返回多少条
        operator / app / channel: 可选过滤条件
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM operation_log"
        conds = []
        params: List[Any] = []
        if operator:
            conds.append("operator=?")
            params.append(operator)
        if app:
            conds.append("app=?")
            params.append(app)
        if channel:
            conds.append("channel=?")
            params.append(channel)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY log_id DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ============================================================
# 子渠道维度表操作（删除/启用，is_active 状态管理）
# ============================================================

def upsert_sub_channel(sub_channel_id: str, sub_channel_name: Optional[str] = None,
                       app: Optional[str] = None, channel: Optional[str] = None,
                       as_of: Optional[str] = None) -> None:
    """
    把一条子渠道写进维度表（首次出现时）。幂等：已存在则不动。

    注意：子渠道的名字是"归因过来的"，可能很复杂，所以主键用 sub_channel_id。
    这里不做 SCD Type 2（子渠道属性基本不变），只记录"是否存在 + 是否启用"。
    """
    eff_date = as_of or datetime.now().strftime("%Y-%m-%d")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT sub_channel_id FROM dim_sub_channel WHERE sub_channel_id=?",
            (sub_channel_id,)
        )
        if cur.fetchone() is None:
            cur.execute(
                """INSERT INTO dim_sub_channel
                   (sub_channel_id, sub_channel_name, app, channel,
                    is_active, eff_start_date, eff_end_date)
                   VALUES (?,?,?,?,1,?, NULL)""",
                (sub_channel_id, sub_channel_name, app, channel, eff_date)
            )
        conn.commit()
    finally:
        conn.close()


def delete_sub_channel(sub_channel_id: str,
                       operator: Optional[str] = None,
                       app: Optional[str] = None,
                       channel: Optional[str] = None,
                       reason: Optional[str] = None) -> bool:
    """
    删除子渠道（置 is_active=0，不物理删除，保留历史可回溯）。

    Returns:
        True 表示成功置为停用；False 表示该子渠道不存在或已是停用状态。
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM dim_sub_channel WHERE sub_channel_id=? ORDER BY eff_start_date DESC LIMIT 1",
            (sub_channel_id,)
        )
        row = cur.fetchone()
        if row is None:
            return False   # 子渠道不存在
        if row["is_active"] == 0:
            return False   # 已经是停用状态，幂等返回 False（无需重复删）

        cur.execute(
            "UPDATE dim_sub_channel SET is_active=0 WHERE sub_channel_id=?",
            (sub_channel_id,)
        )
        # 写操作日志（append-only）
        _log_inline(cur, operator, "delete_sub_channel", app, channel,
                    None, None, "sub_channel", row["sub_channel_id"], None, reason)
        conn.commit()
        return True
    finally:
        conn.close()


def list_sub_channels(active_only: bool = True) -> List[Dict[str, Any]]:
    """列出子渠道。active_only=True 只返回启用中的。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        if active_only:
            cur.execute("SELECT * FROM dim_sub_channel WHERE is_active=1")
        else:
            cur.execute("SELECT * FROM dim_sub_channel")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ============================================================
# Agent 运行历史（人工确认开关的核心）
# ============================================================
# 每次 Agent 跑一轮，先 dry_run 出「建议」，存成 pending 状态，
# 投放人确认后才真正执行写操作。这样「人机协作」落到实处：
# Agent 出建议 → 人确认 → 才落库。

def save_agent_run(status: str, conclusion: str = "", reasoning: str = "",
                   actions: Optional[List[Dict[str, Any]]] = None,
                   executed: Optional[List[Dict[str, Any]]] = None,
                   trace: Optional[List[Dict[str, Any]]] = None) -> int:
    """保存一次 Agent 运行记录，返回 run_id。"""
    import json as _json
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO agent_run_history
               (run_time, status, conclusion, reasoning, actions_json, executed_json, trace_json)
               VALUES (?,?,?,?,?,?,?)""",
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status,
                conclusion,
                reasoning,
                _json.dumps(actions or [], ensure_ascii=False),
                _json.dumps(executed or [], ensure_ascii=False),
                _json.dumps(trace or [], ensure_ascii=False, default=str),
            )
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_agent_run(run_id: int, status: str,
                     executed: Optional[List[Dict[str, Any]]] = None) -> None:
    """更新一次 Agent 运行的状态（pending→approved/executed/rejected）。"""
    import json as _json
    conn = _connect()
    try:
        cur = conn.cursor()
        if executed is not None:
            cur.execute(
                """UPDATE agent_run_history
                   SET status=?, executed_json=? WHERE run_id=?""",
                (status, _json.dumps(executed, ensure_ascii=False), run_id)
            )
        else:
            cur.execute(
                "UPDATE agent_run_history SET status=? WHERE run_id=?",
                (status, run_id)
            )
        conn.commit()
    finally:
        conn.close()


def get_agent_run(run_id: int) -> Optional[Dict[str, Any]]:
    """取一次 Agent 运行记录（含解析后的 actions/executed/trace）。"""
    import json as _json
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent_run_history WHERE run_id=?", (run_id,))
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("actions_json", "executed_json", "trace_json"):
            d[k.replace("_json", "")] = _json.loads(d.get(k) or "[]")
        return d
    finally:
        conn.close()


def list_agent_runs(limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出 Agent 运行历史（最新在前，可按 status 过滤）。"""
    import json as _json
    conn = _connect()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM agent_run_history"
        params: List[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY run_id DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["actions"] = _json.loads(d.get("actions_json") or "[]")
            d["executed"] = _json.loads(d.get("executed_json") or "[]")
            d["trace"] = _json.loads(d.get("trace_json") or "[]")
            result.append(d)
        return result
    finally:
        conn.close()
