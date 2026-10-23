"""中台取数层：把「数据从哪来」从业务逻辑里抽出来。

================================================================================
为什么需要这一层
================================================================================
改造前，agent.py 里有 6 处这样的代码：

    df = pd.read_parquet(config.ADS_DIR / "daily_detail.parquet")

这行把「去哪读数据」硬编码在了业务逻辑里。一旦数据源变化（换 Hive 直连、
换 OLAP、换缓存），就要改 6 个地方，每处都要重新理解上下文 ——
而且改漏一处不会报错，只会让某个工具返回旧数据。

这一层把「去哪读」收敛成一个配置项，业务代码只表达「我要什么」。

================================================================================
两种模式，为什么默认不是 Hive
================================================================================
    ADS_MODE=parquet（默认）
        读 data/warehouse/ads/*.parquet —— 数仓算完导出的小结果
        延迟约 5ms（文件几十 KB）

    ADS_MODE=hive
        走 Spark 查 lab.ads_* —— 真正的数仓直连
        延迟约 2s（Spark 自身的查询规划就要 1.8s，跟数据量无关）

默认选 parquet **不是因为省事，是因为快 400 倍**。而且这不是妥协，
是生产环境的常见做法：数仓（Hive/Spark）负责重计算，算完导出成小结果，
在线服务读小结果。真实公司做在线服务也**不会**让 API 直连批处理集群 ——
那会把集群打爆，而且批处理引擎的调度延迟本来就不适合交互式查询。

所谓「接 OLAP」的本质，就是把「导出小结果」这一步换成
「同步到 ClickHouse/Doris 并建索引」。同步链路、延迟、成本都是新的问题。
当前数据量（ADS 1.4 万行）用 parquet 已经完全够，上 OLAP 是过度设计。

将来数据涨到千万级、QPS 到几百，只需要：
    ① 把 _load_from_parquet 换成查 OLAP 的 HTTP 接口
    ② 改 ADS_MODE 的默认值
业务代码（agent.py 1300 行）一行不动。这就是接口隔离的价值。

================================================================================
一致性说明
================================================================================
两种模式读的是**同一批数据**（Hive 表 → 导出 parquet），
所以切换模式不会改变结果，只会改变延迟。这一点必须保证，
否则「切模式验证」就失去意义了。
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

# 这个项目的 src/ 不是包（没有 __init__.py），模块之间是「把 src 塞进 sys.path
# 后扁平导入」的方式（agent.py 顶部也是这么做的）。这里保持一致，
# 否则 `import warehouse_api` 会找不到 config。
sys.path.insert(0, str(Path(__file__).parent))

import config  # noqa: E402

# parquet 文件放在原项目的 ADS 目录，文件名保持不变 ——
# 这样即使不看这个文件，也能明白它替代了什么。
_ADS_DIR: Path = config.ADS_DIR

_MODE = os.getenv("ADS_MODE", "parquet").lower()

# 模块级缓存：ADS 文件只有几十 KB，但一次请求可能连查 3~5 个工具，
# 每次都读盘是浪费。缓存键包含 mtime，导出了新数据会自动失效。
_CACHE: Dict[str, tuple[float, pd.DataFrame]] = {}
_LOCK = threading.Lock()

_SPARK = None


def _read_parquet(name: str) -> pd.DataFrame:
    """读物化文件。带 mtime 缓存，数据重新导出后自动失效。"""
    path = _ADS_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"ADS 物化文件不存在: {path}\n"
            f"请先跑 scripts/build_ads_iaa.py 生成并导出。"
        )
    mtime = path.stat().st_mtime
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and hit[0] == mtime:
            return hit[1].copy()
    df = pd.read_parquet(path)
    with _LOCK:
        _CACHE[name] = (mtime, df)
    return df.copy()


def _read_hive(name: str) -> pd.DataFrame:
    """直连 Hive。SparkSession 一个进程只建一次 —— 它要 8 秒，不能每次重建。"""
    global _SPARK
    if _SPARK is None:
        raise RuntimeError(
            "ADS_MODE=hive 需要先调用 init_spark() 建好 SparkSession。\n"
            "FastAPI 里应在 lifespan 中初始化（见 app.py），"
            "而不是在请求处理中懒加载 —— 那会让第一个请求超时。"
        )
    table = {
        "daily_detail": "lab.ads_daily_detail",
        "creative_table": "lab.ads_creative_table",
        "sub_channel_table": "lab.ads_sub_channel_table",
        "decision_log": "lab.ods_decision_log",
        "decision_latest": "lab.ads_decision_latest",
        "decision_stats": "lab.ads_decision_stats",
        "operation_log": "lab.ods_operation_log",
    }[name]
    return _SPARK.sql(f"SELECT * FROM {table}").toPandas()


def init_spark(spark: Any) -> None:
    """注入外部建好的 SparkSession（由 app.py 的 lifespan 调用）。"""
    global _SPARK, _MODE
    _SPARK = spark
    _MODE = "hive"


def _read(name: str) -> pd.DataFrame:
    return _harden_dtypes(_read_hive(name) if _MODE == "hive" else _read_parquet(name))


# 语义上「可能真的没有值」的列。
# 不含 roi_0..7 / ret_0..7 等原始列：它们参与大量聚合计算，
# 改 dtype 的影响面太大；而汇总别名是 LLM 和工具真正消费的入口，
# 也是历史上唯一出过「NULL 被算成 0」事故的地方。
_NULLABLE_FLOAT = ("d7_roi", "d7_retention", "d7_ecpm", "cpi")


def _harden_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """把可空浮点列转成 pandas 的 nullable dtype（`Float64`）。

    为什么值得改：
        float64 的 NaN 在 sum() / mean() 里会被**静默跳过**，
        一整列全是 NULL 时结果是 0.0 —— 于是「不知道」被算成「零」。
        实测踩过一次：ch_001 的 d7_retention 在 10-25 之后整列 NULL，
        工具返回 0.0，LLM 直接判「用户全流失」。

        Float64 的 pd.NA 会向上传播，`.sum()` 默认仍然 skipna，
        但至少 `astype(float)` / 比较运算会炸，逼着调用方显式处理
        「没有值」和「值为零」这两种情况，而不是让 0.0 冒充。

    注意：这一步**不改变落盘数据**。parquet 里的 NULL 是正确的
    （窗口没走完就是不知道），改成 0 才是错的。
    这里只影响「读进内存之后怎么表示」。
    """
    for c in _NULLABLE_FLOAT:
        if c in df.columns and str(df[c].dtype) != "Float64":
            try:
                df[c] = df[c].astype("Float64")
            except (TypeError, ValueError):
                pass      # 类型异常就保持原样，不让读取因为一个字段失败
    return df


# =============================================================================
# 对外接口：三个表的读法
# =============================================================================
def read_daily_detail(mature_only: bool = False) -> pd.DataFrame:
    """cohort 宽表 D0~D7（原 `daily_detail.parquet`）。

    Args:
        mature_only: 只返回 D7 观察窗口完整的 cohort（`is_mature_d7 = True`）。

            **算任何 D7 类指标（d7_roi / d7_retention / d7_ecpm）时必须传 True。**
            尾部 cohort 只活了 0~6 天，D7 收入还没产生，但报表照样会算出
            一个偏低的 d7_roi —— 实测 ch_001 从 10-24 的 0.73 掉到 10-31 的 0.36，
            看起来像渠道恶化，其实只是窗口不够。
            默认 False 是为了「看最新情况」时不丢数据；
            做决策、算趋势、排行时必须过滤。

    Returns:
        列含 install_date / app / country / campaign / creative_id / sub_channel /
        installs / spend / ret_0..7 / roi_0..7 / ecpm_0..7 / view_0..7 /
        d7_roi / cpi / is_mature_d7 / strategy / dact0..7 / drev0..7 / dview0..7 / dimp0..7
    """
    df = _read("daily_detail")
    if mature_only and "is_mature_d7" in df.columns:
        df = df[df["is_mature_d7"]].copy()
    return df


def read_creative_table() -> pd.DataFrame:
    """素材汇总（原 `creative_table.parquet`）。一行 = 一个素材 × 一个装机日。"""
    return _read("creative_table")


def read_sub_channel_table() -> pd.DataFrame:
    """子渠道汇总（原 `sub_channel_table.parquet`）。一行 = 一个子渠道 × 一个装机日。"""
    return _read("sub_channel_table")


_KNOWN = ("daily_detail", "creative_table", "sub_channel_table",
          "decision_log", "decision_latest", "decision_stats", "operation_log")


def read_by_filename(filename: str) -> pd.DataFrame:
    """按原 parquet 文件名取数。

    存在的意义：`_load_top(filename)` 这种「文件名是参数」的函数，
    签名里传的是 "creative_table.parquet"。有了这个入口，调用方不用改签名，
    内部只换一行就能切到新数据源。

    Args:
        filename: 原文件名，可带或不带 .parquet 后缀

    Raises:
        ValueError: 文件名不在已知三张表里（防止拼错名字静默返回错表）
    """
    name = str(filename).replace(".parquet", "")
    if name not in _KNOWN:
        raise ValueError(
            f"未知的 ADS 表 {filename!r}，只支持 {list(_KNOWN)}。"
            f"拼错名字如果静默返回空表，会变成「查询没数据」而不是「代码有 bug」，很难查。"
        )
    return _read(name)


# =============================================================================
# 护栏专用的两个点查接口
# =============================================================================
# 为什么单独提供而不是让调用方自己 groupby：
#     这两处对「0」和「查不到」的区分是**安全语义**，不是显示问题。
#     _get_channel_installs 返回 0 表示「确实没量」，返回 None 表示「查不到」，
#     护栏对两者的处理完全不同（None 直接 fail-closed 拒绝执行）。
#     如果让每个调用方自己 sum()，迟早有人写成 sum() or 0，
#     那就把「查不到」变成了「没量」，护栏可能因此放行不该放行的删除操作。
def get_channel_installs(sub_channel: str) -> Optional[int]:
    """某子渠道的**累计**装机量。返回 None 表示查不到（不是 0）。"""
    if not sub_channel:
        return None
    try:
        df = read_sub_channel_table()
        if "sub_channel" not in df.columns or "installs" not in df.columns:
            return None
        rows = df[df["sub_channel"] == sub_channel]
        if rows.empty:
            return None
        return int(rows["installs"].sum())
    except Exception:
        return None


def get_channel_observed_days(sub_channel: str) -> Optional[int]:
    """某子渠道覆盖了多少个装机日（实际有数据的天数，不是日历跨度）。

    用 nunique 而不是 max-min+1：中间断投的渠道（停两天又开）用日期差
    会算成 10 天，但实际只有 8 天数据。护栏判断的是「有没有足够的成熟 cohort」，
    看的是实际有数据的天数。
    """
    if not sub_channel:
        return None
    try:
        df = read_sub_channel_table()
        if "sub_channel" not in df.columns or "install_date" not in df.columns:
            return None
        rows = df[df["sub_channel"] == sub_channel]
        if rows.empty:
            return None
        return int(pd.to_datetime(rows["install_date"]).nunique())
    except Exception:
        return None


# =============================================================================
# 日志链路：数仓为主，SQLite 为增量
# =============================================================================
# 为什么这里要处理「两个源」，而上面三张表不用：
#     指标表只有一个源（数仓），日志有两个（数仓 + agent 的写入库）。
#     日志由 agent 在**流水线之后**写，所以数仓里的日志天生比 SQLite 旧一天。
#     如果中台只读数仓，agent 刚写完的结论它看不到 —— 快照里会出现
#     「上次结论」比「本次判定」还早一轮的错位。
#
# 判据只有一条：比 decided_at。
#     数仓更新  → 用数仓（正常情况：DAG 同步过，或 agent 还没跑）
#     SQLite 新 → 用 SQLite（agent 刚跑完、同步任务还没跑）
# 没有第三种情况，也就不存在"猜哪边对"。
#
# 为什么是整块替换而不是逐行 merge：
#     「SQLite 新」时直接从源重算一遍最新结论（171 行，1 毫秒），
#     而不是把新行 patch 进旧表。
#     逐行 merge 的代码一旦有 bug，产出的是**半新半旧**的结论表 ——
#     它看起来完全正常，只是某几个对象的"上次"是旧的。
#     这种错误在报表上无法被发现，代价远大于省下的那点计算。

_DIM_DB = config.DATA_DIR / "dim.db"
_LOG_KEYS = ["obj_type", "obj_id", "metric"]


def _sqlite_log(table: str) -> pd.DataFrame:
    """只读打开 agent 的写入库。连不上就返回空表，由调用方决定降级。"""
    import sqlite3  # noqa: PLC0415

    if not _DIM_DB.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(f"file:{_DIM_DB}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _is_newer(src: pd.DataFrame, wh: pd.DataFrame, col: str) -> bool:
    """源是否比数仓更新。任一边为空时判为「源更新」（宁可读源，也别读空）。"""
    if src is None or src.empty or col not in src.columns:
        return False
    if wh is None or wh.empty or col not in wh.columns:
        return True
    return str(src[col].max()) > str(wh[col].max())


def _tail_run(series: pd.Series) -> int:
    """序列末尾连续相同值的个数 —— verdict_streak 的 pandas 版实现。"""
    vals = list(series)
    if not vals:
        return 0
    n = 1
    for v in reversed(vals[:-1]):
        if v == vals[-1]:
            n += 1
        else:
            break
    return n


def _latest_from_log(df: pd.DataFrame) -> pd.DataFrame:
    """从原始日志重算「每对象最新一条 + 历史统计」，列与 ads_decision_latest 对齐。

    这是 ADS 那套窗口逻辑的 pandas 版本。听起来像重复实现，但两者的信任边界不同：
        Spark 版  → 落盘，被门禁校验、被 join
        pandas 版 → 只在「agent 刚写完、数仓还没同步」的窗口里兜底
    让它失败降级成「读旧一天」，比让它共享 Spark 代码从而把中台拖进 Spark 依赖要划算。
    """
    d = df.copy()
    d["data_as_of"] = pd.to_datetime(d["data_as_of"])
    d = d.sort_values(_LOG_KEYS + ["data_as_of"], kind="mergesort")
    g = d.groupby(_LOG_KEYS, sort=False)

    d["prev_verdict"] = g["verdict"].shift(1)
    d["changed"] = (d["verdict"] != d["prev_verdict"]) & d["prev_verdict"].notna()
    d["n_judged"] = g["verdict"].transform("size")
    d["n_changed"] = g["changed"].transform("sum")
    d["first_data_as_of"] = g["data_as_of"].transform("min")
    d["verdict_streak"] = g["verdict"].transform(_tail_run)

    last = d.groupby(_LOG_KEYS, sort=False).tail(1).copy()
    last["data_as_of"] = last["data_as_of"].dt.strftime("%Y-%m-%d")
    last["first_data_as_of"] = pd.to_datetime(last["first_data_as_of"]).dt.strftime("%Y-%m-%d")

    cols = ["obj_type", "obj_id", "metric", "data_as_of", "decided_at", "value",
            "verdict", "prev_verdict", "changed", "reason", "days_sustained",
            "verdict_streak", "n_judged", "n_changed", "first_data_as_of",
            "snapshot_hash", "config_version"]
    return last[[c for c in cols if c in last.columns]].reset_index(drop=True)


def read_decision_latest() -> pd.DataFrame:
    """每个对象的最新判定（含 last_verdicts 需要的全部字段）。

    中台问「上次判了什么」就来这里。**不要再直接读 dim.db** ——
    直接读 SQLite 会绕过「数仓是台账」这件事，等于又回到两个源各读一半。
    """
    try:
        wh = _read("decision_latest")
    except Exception:
        wh = pd.DataFrame()

    src = _sqlite_log("decision_log")
    if _is_newer(src, wh, "decided_at"):
        out = _latest_from_log(src)
        out.attrs["source"] = "sqlite(delta)"
        out.attrs["note"] = "agent 刚写过，数仓尚未同步"
        return out
    wh.attrs["source"] = "warehouse"
    wh.attrs["note"] = "已同步"
    return wh


def read_decision_log() -> pd.DataFrame:
    """全量判定日志（回溯用：某对象历史上被判过几次、每次什么理由）。"""
    try:
        wh = _read("decision_log")
    except Exception:
        wh = pd.DataFrame()

    src = _sqlite_log("decision_log")
    if _is_newer(src, wh, "decided_at"):
        src = src.copy()
        src.attrs["source"] = "sqlite(delta)"
        return src
    wh.attrs["source"] = "warehouse"
    return wh


def read_decision_stats() -> pd.DataFrame:
    """判定分布（按 data_as_of × 对象类型 × 结论）。

    这张表只来自数仓：它本身是同步时算出来的派生结果，
    "源更新"对它没有意义 —— 要么用已算好的，要么等下次同步。
    """
    return _read("decision_stats")


def read_operation_log(limit: Optional[int] = None) -> pd.DataFrame:
    """操作日志（最近调了什么）。limit=None 取全量，按时间倒序。"""
    try:
        wh = _read("operation_log")
    except Exception:
        wh = pd.DataFrame()

    src = _sqlite_log("operation_log")
    df = src if _is_newer(src, wh, "op_time") else wh
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if "op_time" in df.columns:
        df = df.sort_values("op_time", ascending=False)
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


def refresh() -> None:
    """清缓存。数据重新导出后调用（或等 mtime 自动失效）。"""
    with _LOCK:
        _CACHE.clear()


def current_mode() -> str:
    """当前取数模式，用于健康检查和日志。"""
    return _MODE
