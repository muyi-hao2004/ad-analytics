"""
Agent 决策核心：快照组装 + 多轮 Function Calling + 决策解析

这是"中台"从【可操作】走向【自动优化】的关键一环。

分层结构：
  1. build_snapshot()   —— 把「派生指标 + 当前配置 + 最近日志」压缩成精简 JSON（日报的数据版）
  2. READ_TOOLS         —— 读工具说明书（原生 tools 参数传给模型）
     SUBMIT_DECISION_TOOL —— 终态工具：LLM 唯一能"表达写意图"的出口
  3. _build_tool_registry —— 名字→函数 的查表（你的代码真正执行的地方，只含读工具）
  4. run_llm_loop()     —— 多轮循环：LLM 调工具 → 你执行 → 结果回传 → LLM 再查/提交决策

设计原则（为什么这么做）：
  - 不喂 HTML 报表（那是给人看的界面，LLM 读表格 HTML 又慢又易错）
  - 不喂原始事实表（太大、太细，且让 LLM 自己算派生指标容易错）
  - 快照只给「结论」，需要细节让 LLM 通过 get_metric/get_logs 按需查（多轮 Function Calling）
  - LLM 从头到尾不执行任何代码，它只「表达意图」，你的代码查表执行
  - **读写分离**：LLM 在分析阶段只有读权限，写动作只能通过 submit_decision 提交建议单，
    由人 approve 后才执行，且执行前还要过 guardrails（见 2.0）
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
import hashlib
import json
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import config
import decision
import dim_scd
import guardrails
import knowledge_base
import llm_cost
import semantic_layer
import warehouse_api
from dotenv import load_dotenv

load_dotenv(config.PROJECT_ROOT / ".env")


# ============================================================
# 1. 快照组装：把数仓结果压缩成 LLM 能读懂的精简 JSON
# ============================================================

def snapshot_hash(snapshot: Dict[str, Any]) -> str:
    """给「数据部分」算一个 8 位指纹。

    刻意**只算指标，不算配置和操作日志**：
        如果把「当前配置」「最近操作」也算进去，那么改一次预算就会让指纹变，
        系统会以为「数据更新了，可以重新判断」，于是 LLM 又改一次口 ——
        这跟防抖动的目标正好相反。指纹的边界必须是「人划定的数据范围」，
        不是「整个 dict」。

    指纹的用途只有一个：比较两次跑，看到的是不是同一份数据。
    它不含任何内容，所以不能用来回溯（回溯靠 decision_log 里的值和理由）。
    """
    core = {
        "口径截止": (snapshot.get("数据口径") or {}).get("D7口径截止"),
        "素材总览": snapshot.get("素材总览"),
        "素材表现": snapshot.get("素材表现"),
        "子渠道表现": snapshot.get("子渠道表现"),
    }
    # 先清洗再算指纹，顺序不能反。
    # 踩过的坑：build_snapshot 里先算指纹、后 sanitize，结果「快照里存的指纹」
    # 和「拿快照重算的指纹」不相等 —— 因为前者依赖 np.float64 之类内存类型
    # 被 default=str 写成的字符串，后者是纯 Python float。
    # 指纹的意义就是「可重算校验」，算完就不可复算等于没算。
    # sanitize 本身是幂等的，所以对已清洗的快照再算一次，结果完全相同。
    core = sanitize_for_llm(core)
    raw = json.dumps(core, sort_keys=True, ensure_ascii=False, default=str)
    canonical = json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()[:8]


def _series_by_obj(obj_type: str, days: int = 14) -> Dict[str, List[Dict[str, Any]]]:
    """取每个对象的逐日 D7 ROI 序列，供判定器使用。

    为什么要从 daily_detail 聚合而不是直接读汇总表：
        汇总表（creative_table）是「对象 × 装机日」的粒度，没有成熟度标记，
        尾部 cohort 的 D7 还没产生，会把序列尾巴拉低 —— 判定器据此会误判恶化。

    加权口径：Σ(花费 × ROI) / Σ花费，等价于 Σ回收 / Σ花费。
        用 sum(min_count=1) 而不是 sum()：全部为 NaN 时 sum() 返回 0，
        会把「不知道」变成「ROI=0」，这是一个能把渠道直接判死刑的 bug。
    """
    df = warehouse_api.read_daily_detail(mature_only=True)
    dim = "sub_channel" if obj_type == "sub_channel" else "creative_id"
    if dim not in df.columns or "d7_roi" not in df.columns:
        return {}

    sub = df[df[dim].notna()].copy()
    if sub.empty:
        return {}

    sub["_rev"] = sub["spend"] * sub["d7_roi"]
    g = (sub.groupby([dim, "install_date"])
            .agg(spend=("spend", "sum"),
                 rev=("_rev", lambda s: s.sum(min_count=1)),
                 n=("d7_roi", "count"))
            .reset_index())

    out: Dict[str, List[Dict[str, Any]]] = {}
    for oid, grp in g.groupby(dim):
        grp = grp.sort_values("install_date").tail(days)
        series = []
        for r in grp.to_dict(orient="records"):
            spend = float(r["spend"] or 0)
            rev = r["rev"]
            # rev 是 NaN 表示这一天该对象的所有行都没有 D7 值 → 不可观测，记 None
            value = None if (rev is None or (isinstance(rev, float) and math.isnan(rev)) or spend <= 0) \
                else float(rev) / spend
            series.append({"day": str(r["install_date"])[:10], "value": value,
                           "spend": spend, "rev": 0.0 if value is None else float(rev)})
        out[str(oid)] = series
    return out


def _prev_verdicts() -> Dict[str, Dict[str, Any]]:
    """每个对象上一次的判定，按 obj_id 索引。

    **唯一入口**：判定层（喂 previous）和快照（展示「上次结论」）必须用同一份数据。
    如果一处读数仓、一处读 SQLite，就会出现「判定用的是 A 结论，
    展示给 LLM 的是 B 结论」——LLM 会据此解释出一套和规则层不一致的说法，
    而且两边单看都正常，只有把两个输出摆在一起才发现对不上。

    数据源：warehouse_api.read_decision_latest()（数仓为主，agent 刚写完时用 SQLite 增量）。
    数仓不可用（还没导出过 / 表没建）时退回 SQLite —— 宁可读旧，不能读不到：
    读不到会让所有对象变成「首次判定」，滞回当天全线失效。
    """
    try:
        df = warehouse_api.read_decision_latest()
        if df is not None and not df.empty and "metric" in df.columns:
            df = df[df["metric"] == "d7_roi"]
            out: Dict[str, Dict[str, Any]] = {}
            for r in df.to_dict("records"):
                out[str(r["obj_id"])] = {
                    "obj_type": r.get("obj_type"),
                    "verdict": r.get("verdict"),
                    "value": r.get("value"),
                    "data_as_of": str(r.get("data_as_of") or "")[:10],
                    "reason": r.get("reason"),
                    "snapshot_hash": r.get("snapshot_hash"),
                    "days_sustained": r.get("days_sustained"),
                    "verdict_streak": r.get("verdict_streak"),
                    "_source": df.attrs.get("source", "warehouse"),
                }
            if out:
                return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 数仓日志读取失败，退回 SQLite: {type(e).__name__}: {e}")
    return dim_scd.last_verdicts(metric="d7_roi")


def _log_source_label() -> str:
    """判定日志这次是从哪读的。写进快照，让人复盘时能对上账。"""
    try:
        src = warehouse_api.read_decision_latest().attrs.get("source", "warehouse")
    except Exception:  # noqa: BLE001
        return "SQLite（数仓不可用，已降级）"
    return {
        "warehouse": "数仓（已同步）",
        "sqlite(delta)": "SQLite 增量（agent 刚写过，数仓尚未同步）",
    }.get(str(src), str(src))


def _judgements() -> tuple[Dict[str, Any], List[tuple]]:
    """跑判定层，产出结构化的「建议动作」。

    这是本快照最重要的一块：LLM 不再自己拿数字对着阈值猜，
    而是拿到一个**已经判好的结论**，它只负责解释和落地。
    判定是确定性的（同输入必同输出），所以同一个渠道不会今天说观察、
    明天说关停 —— 抖动在规则层就被吃掉了，不依赖模型的稳定性。

    Returns:
        (给 LLM 看的展示块, 全量判定行)

        为什么要把「展示」和「全量」分开：
            素材有 160 个，全塞进快照会吃掉几千 token；但**写进决策日志的必须是全量**。
            如果只记「改判的 10 个」，那其余 150 个下次跑的时候
            上次结论是空的，永远判不出「改判」——滞回机制直接失效。
            展示可以裁剪，状态必须完整。
    """
    cfg = semantic_layer.get_hysteresis()
    enabled = bool(cfg.get("enabled", True))
    band = float(cfg.get("band_width", 0.04))
    min_days = int(cfg.get("min_consecutive_days", 3))

    prev = _prev_verdicts()
    out: Dict[str, Any] = {}
    all_rows: List[tuple] = []   # 全量判定行，供写日志用（不受展示裁剪影响）

    # 素材的花费门槛：低于这个值的素材样本量不足，ROI 波动主要是噪声。
    # 不套这个门槛，「建议关停」会多出一批只花了几百块的素材 ——
    # 那不是结论，那是随机数。（实测：套门槛 47 个 vs 不套 65 个）
    cr_th = (semantic_layer.get_thresholds("creative") or {})
    min_spend = float(cr_th.get("min_spend", 0) or 0)

    for obj_type, key in (("sub_channel", "渠道"), ("creative", "素材")):
        series = _series_by_obj(obj_type)
        if not series:
            out[key] = {"状态": "无数据"}
            continue

        skipped = 0
        if obj_type == "creative" and min_spend > 0:
            kept = {}
            for oid, s in series.items():
                if sum(x["spend"] for x in s) >= min_spend:
                    kept[oid] = s
                else:
                    skipped += 1
            series = kept

        previous = {k: v["verdict"] for k, v in prev.items()
                    if v.get("obj_type") == obj_type}
        rows = decision.evaluate_all(obj_type, {k: [(x["day"], x["value"]) for x in v]
                                                for k, v in series.items()},
                                     previous, band_width=band, min_days=min_days,
                                     enabled=enabled)

        brief = [{"对象": r["obj_id"],
                  "当日ROI": r["value"],          # 判定用的就是当日值，不是累计值
                  "建议": r["verdict"],
                  "上次": r["previous"], "本次改判": r["changed"],
                  "被拦原因": r["blocked_by"], "连续天数": r["days_sustained"]}
                 for r in rows]
        all_rows.extend((obj_type, b) for b in brief)

        if obj_type == "sub_channel":
            out[key] = brief
        else:
            # 素材有 200 个，全列会吃掉几千 token，且绝大多数没变化。
            # 只给「改判的」和「最差 10 个」，其余用统计概括。
            changed = [b for b in brief if b["本次改判"]]
            out[key] = {
                "统计": {
                    "纳入判定": len(brief),
                    f"已剔除花费不足{int(min_spend)}的素材": skipped,
                    "建议关停": sum(1 for b in brief if b["建议"] == "cut"),
                    "建议加预算": sum(1 for b in brief if b["建议"] == "scale"),
                    "本次改判": len(changed),
                },
                "本次改判": changed[:10],
                "最差 10 个": [b for b in brief if b["建议"] == "cut"][:10],
            }

    out["规则"] = (
        f"判定口径：以当日 D7 ROI 为准（不是 31 天累计值，累计值见「子渠道表现」），"
        f"跨阈值需越过 ±{band / 2:.2f} 的死区，且连续 {min_days} 天落在同一区间才允许改判。"
        "以上结论由 decision.py 按 metrics.yaml 阈值确定性产出。"
        "你的职责是解释原因、安排落地顺序，**不要用自己算的数推翻它**；"
        "若你认为规则本身有问题，明确写出来交给人判断，不要静默改判。"
    )
    return out, all_rows


def _blank(v) -> bool:
    """值是不是「等于没填」。

    pandas 的 NA 有不止一种形态（None / float('nan') / pd.NA / pd.NaT），
    只判 `is None` 会漏掉后三种，于是快照里出现 "old_value": null 这种
    看起来有值、其实是 NaN 的字段 —— LLM 会以为「这个字段存在但为空」，
    从而认为操作记录不完整。判空必须一次判干净。
    """
    if v is None:
        return True
    try:
        return bool(v != v)          # NaN / NaT 自身不相等
    except (TypeError, ValueError):
        return False


def _op_records(limit: int = 10) -> List[Dict[str, Any]]:
    """操作日志 → 一批干净的 dict（NA 一律变 None，可以直接进 JSON）。

    这是操作日志的**唯一读取出口**：快照的「最近操作」、LLM 的 get_logs 工具、
    趋势叠加的操作标记，全部走这里。
    以前这三处各写一遍 `dim_scd.list_logs()`，于是日志一入仓就出现
    「快照读数仓、工具读 SQLite」的分裂 —— 同一时刻两个答案。
    """
    try:
        df = warehouse_api.read_operation_log(limit=limit)
        if df is not None and not df.empty:
            return [{k: (None if _blank(v) else v) for k, v in r.items()}
                    for r in df.to_dict("records")]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 数仓操作日志读取失败，退回 SQLite: {type(e).__name__}: {e}")
    return dim_scd.list_logs(limit=limit)


def _recent_operations(limit: int = 10) -> List[Dict[str, Any]]:
    """最近的操作日志（快照用）。逻辑同 `_op_records`，保留这个名字是为了调用点可读。"""
    return _op_records(limit)


def _last_verdicts_block(snap_hash: str,
                         only: Optional[set] = None) -> List[Dict[str, Any]]:
    """上次的判定结论 + 数据变没变。

    这是解决「同一渠道今天说观察、明天说关停」的另一半：
    光有确定性判定还不够，LLM 还需要知道「我上次说了什么」。
    它没有记忆，但可以读。

    Args:
        only: {(obj_type, obj_id)} 白名单。日志里有 170 多条（素材全量），
            但快照只展示十几个对象 —— 全塞进去会白烧几千 token。
            **日志写全量、展示只给相关的**，这是两者的分工。
    """
    try:
        rows = _prev_verdicts()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for obj_id, r in rows.items():
        if only is not None and (r.get("obj_type"), obj_id) not in only:
            continue
        out.append({
            "对象": obj_id,
            "上次结论": r.get("verdict"),
            "当时值": r.get("value"),
            "判定日期": (r.get("data_as_of") or "")[:10],
            "数据已变": (r.get("snapshot_hash") or "") != snap_hash,
            # 理由截断：它是给人看的线索，不是证据。完整理由留在
            # decision_log 里，需要复盘时查库，不必占快照 token。
            "当时理由": (r.get("reason") or "")[:40],
        })
    return out


def build_snapshot(top_n: int = 20, persist: bool = False) -> Dict[str, Any]:
    """
    组装给 LLM 的"日报数据版"快照。

    包含四类信息：
      0. 数据口径 —— 截止到哪天、D7 能用到哪天、判读阈值多少（先看这个）
      1. 派生指标（ADS 层）：D7 ROI、留存、eCPM、CPI —— 已经是结论，直接看
      2. 当前配置（维度表）：每个系列的策略、目标值、预算 —— LLM 要知道现状
      3. 最近操作日志：最近调了什么 —— 避免重复调整、知道历史

    口径顺序是有意的：口径放最前，LLM 会先建立「这些数字是怎么来的」的
    上下文，再去看具体数值。反过来的话，它经常抓到一个数就下结论。

    Args:
        top_n: 每个维度最多取多少行（避免 token 爆炸）

    Returns:
        快照 dict，可直接 json.dumps 喂给 LLM
    """
    snapshot: Dict[str, Any] = {}

    # ---- 0. 数据口径与判读阈值（必须先给）----
    snapshot["数据口径"] = _data_context()

    # ---- 1.1 派生指标：素材/子渠道（判断"删不删"的核心）----
    # 先给总览再给明细：明细只列最差的 20 个，单看会把「个别素材差」
    # 误读成「全线崩」，总览用来说清这只是尾部还是整体。
    snapshot["素材总览"] = _creative_overview()
    snapshot["素材表现"] = _load_top("creative_table.parquet", top_n)
    snapshot["子渠道表现"] = _load_top("sub_channel_table.parquet", top_n)

    # ---- 1.15 指纹：必须在指标块都建好之后算 ----
    # 顺序不能反：指纹的定义就是「这几个指标块的内容」，早算会漏块。
    snap_hash = snapshot_hash(snapshot)
    snapshot["数据口径"]["快照指纹"] = snap_hash
    snapshot["数据口径"]["配置读取于"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    cfg_ver = dim_scd.config_version()
    if cfg_ver:
        snapshot["数据口径"]["配置生效日"] = cfg_ver
        snapshot["数据口径"]["配置与指标的关系"] = (
            "指标截止日与配置生效日是两个独立时间轴。若指标截止日 < 配置生效日，"
            "说明当前看到的数字还没反映配置改动，别把效果归因于新配置。"
        )
    # 把日志的来源也标出来：判定日志现在有两个源（数仓为主 / SQLite 增量），
    # 不写清楚的话，复盘时会分不清「那天看到的是已同步的结论还是刚写的结论」。
    snapshot["数据口径"]["判定日志来源"] = _log_source_label()

    # ---- 1.2 判定建议：确定性的结论，LLM 只负责解释 ----
    judgements, judge_rows = _judgements()
    snapshot["判定建议"] = judgements

    # ---- 1.3 当前配置：每个系列的策略 + 目标值 + 预算 ----
    snapshot["当前配置"] = _current_config()

    # ---- 1.4 最近操作日志（最近 10 条）----
    # 只投影有用的字段：原表 12 列里有 4 列常年为 null（app/channel/campaign_id/country），
    # 全量塞进去白占约 40% 的体积，还会让 LLM 以为「这些字段有值只是没填」。
    snapshot["最近操作"] = [
        {k: v for k, v in {kk: r.get(kk) for kk in
                           ("op_time", "operator", "action", "field",
                            "old_value", "new_value", "reason")}.items()
         if not _blank(v)}
        for r in _recent_operations(10)
    ]

    # ---- 1.5 上次结论（解决「每天从零判断」）----
    # 白名单与「判定建议」展示的对象保持一致：日志全量，展示只给这次要看的
    shown = {("sub_channel", r["对象"]) for r in (judgements.get("渠道") or [])}
    _cr = judgements.get("素材")
    if isinstance(_cr, dict):
        for r in (_cr.get("本次改判") or []) + (_cr.get("最差 10 个") or []):
            shown.add(("creative", r["对象"]))
    last = _last_verdicts_block(snap_hash, only=shown)
    if last:
        snapshot["上次结论"] = last
        snapshot["上次结论说明"] = (
            "「数据已变=false」表示本次看到的指标与上次判定时完全相同，"
            "应沿用上次结论并说明原因，不要重新判断出不同结果。"
            "「数据已变=true」才是允许改判的前提（还要满足连续确认天数）。"
        )

    # ---- 1.6 业务红线（LLM 判断"要不要优化"的基准）----
    snapshot["业务目标"] = {
        "D7_ROAS_目标": f"{config.D7_ROAS_TARGET:.0%}",
        "说明": "D7 ROI 低于该值说明变现效率不足，需要优化",
    }

    result = sanitize_for_llm(snapshot)

    if persist:
        _persist_decisions(judge_rows, snap_hash, cfg_ver,
                           (snapshot.get("数据口径") or {}).get("D7口径截止") or "")

    return result


def _persist_decisions(rows: List[tuple], snap_hash: str,
                       cfg_ver: Optional[str], as_of: str) -> None:
    """把本次判定写进 decision_log。

    为什么单独一个函数而不是塞进 build_snapshot 的主流程：
        快照会被反复构建（调试、界面展示、单测），如果构建即写库，
        调试一次就污染一次决策历史。写库必须是**显式**的动作，
        由 run_agent 在真正跑完一轮之后调用。

    为什么必须写全量（不只是快照里展示的那几条）：
        滞回和「改判判定」都依赖「上次的结论」。只记一部分对象，
        其余对象下次就是「首次判定」，永远看不出变化 —— 机制形同虚设。

    幂等性由表上的 UNIQUE(obj_type, obj_id, metric, data_as_of) 保证：
    同一天跑十次也只会留一条最终结论，不需要调用方去重。
    """
    written = 0
    for obj_type, item in rows:
        oid = item.get("对象")
        if not oid:
            continue
        try:
            dim_scd.log_decision(
                obj_type=obj_type, obj_id=oid, metric="d7_roi",
                value=item.get("当日ROI"), verdict=item.get("建议") or "unknown",
                data_as_of=as_of, reason=_reason_of(item),
                days_sustained=item.get("连续天数"),
                snapshot_hash=snap_hash, config_version=cfg_ver,
            )
            written += 1
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 决策日志写入失败 {obj_type}/{oid}: {type(e).__name__}: {e}")
    print(f"[decision] 写入决策 {written} 条（data_as_of={as_of}, 指纹={snap_hash}）")


def _reason_of(item: Dict[str, Any]) -> str:
    """把判定结果拼成一句可回溯的理由。回溯靠这句，不靠指纹。"""
    parts = [f"当日ROI {item.get('当日ROI')}"]
    if item.get("上次") and item.get("建议") != item.get("上次"):
        parts.append(f"{item.get('上次')} → {item.get('建议')}")
    else:
        parts.append(f"维持 {item.get('建议')}")
    if item.get("连续天数"):
        parts.append(f"连续 {item.get('连续天数')} 天")
    if item.get("被拦原因") == "dead_band":
        parts.append("本应改判但落在死区，暂不动")
    return "；".join(parts)


def _data_context() -> Dict[str, Any]:
    """快照的元信息：数据截止在哪、D7 口径能用到哪天、判读阈值是多少。

    为什么必须给 LLM 这块：
        LLM 最容易犯的错是「拿着一个数字直接下结论」，不问这个数字怎么算的。
        没有这块信息，它会做两件错事：

          1. 把 10-31 的 D7 ROI（0.36）和 10-24 的（0.73）直接比，
             得出「渠道急剧恶化，建议停投」—— 其实只是窗口没走完。
          2. 拿 0.65 去比「感觉合理」的线，而不是比语义层定义的目标，
             于是同一个渠道在不同回答里被判成不同结果。

        给了这块，它才能自己判断：这条数据够不够新、该拿哪个阈值比。
    """
    ctx: Dict[str, Any] = {}

    try:
        df = warehouse_api.read_daily_detail()
        first = str(df["install_date"].min())[:10]
        last = str(df["install_date"].max())[:10]
        ctx["数据范围"] = f"{first} ~ {last}"

        if "is_mature_d7" in df.columns:
            mature = df[df["is_mature_d7"]]
            if not mature.empty:
                mature_last = str(mature["install_date"].max())[:10]
                ctx["D7口径截止"] = mature_last
                ctx["成熟度说明"] = (
                    f"D7 类指标（d7_roi / d7_retention / d7_ecpm）只统计"
                    f"「装机日 + 7 天 ≤ {last}」的 cohort，因此只有 {mature_last} "
                    f"及之前的装机日可用。之后的 D7 值是窗口未走完的中间值，"
                    f"系统性偏低，**不能用来判断对象变好还是变差**。"
                )
    except Exception as e:
        ctx["数据范围加载失败"] = f"{type(e).__name__}: {e}"

    th = semantic_layer.get_thresholds()
    if isinstance(th, dict) and "error" not in th:
        ch = th.get("channel") or {}
        cr = th.get("creative") or {}
        # 阈值在 YAML 里是「分界点」（watch 和 weak 恰好同为 0.62），
        # 直接摊给 LLM 会出现两个同样的数字挂在不同的判读名下 —— 它会困惑。
        # 这里转成「区间 → 结论」，因为 LLM 需要的是「0.65 算好还是不好」，
        # 不是一堆分界点自己去推。
        if ch:
            ctx["渠道判读（D7 ROI）"] = [
                {"区间": f"≥ {ch.get('strong')}", "判定": "达标，可加预算"},
                {"区间": f"{ch.get('watch')} ~ {ch.get('strong')}", "判定": "临界，观察不动作"},
                {"区间": f"{ch.get('critical')} ~ {ch.get('weak')}", "判定": "不达标，需优化"},
                {"区间": f"< {ch.get('critical')}", "判定": "严重，建议降预算"},
            ]
        if cr:
            min_spend = cr.get("min_spend")
            ctx[f"素材判读（D7 ROI，且累计花费 > {min_spend}）"] = [
                {"区间": f"≥ {cr.get('scale')}", "判定": "建议加预算"},
                {"区间": f"{cr.get('cut')} ~ {cr.get('scale')}", "判定": "保持观察"},
                {"区间": f"< {cr.get('cut')}", "判定": "建议关停"},
            ]
            ctx["样本不足不下结论"] = (
                f"累计花费 ≤ {min_spend} 的素材样本量不够，"
                f"ROI 波动主要是噪声，不要据此建议关停或加预算。"
            )
        ctx["阈值来源"] = "语义层 metrics.yaml decision_thresholds，改阈值只改那里，不要改代码"

    return ctx


def _creative_overview() -> Dict[str, Any]:
    """素材整体分布摘要：有多少个、分位数、各档位各多少个。

    为什么必须有这块：
        snapshot["素材表现"] 只返回**最差的 20 个**（设计如此 —— 优化要看
        最该动的）。但 LLM 单独看那 20 行，看到的 ROI 全在 0.31~0.45，
        很容易得出「所有素材都不达标，建议全部关停」—— 而它根本没看到
        还有 ROI 1.24 的素材。

        有了总览，它才能回答「这是个别素材的问题，还是整体在恶化」，
        而这是完全不同量级的决策：换 3 个素材 vs 停一个 campaign。

        另外「样本不足未纳入」也要显式说：花费太低的素材 ROI 波动纯属噪声，
        如果不说，LLM 会从 200 个里挑出几个高 ROI 的低花费素材建议加预算。
    """
    dim_col = "creative_id"
    try:
        df = warehouse_api.read_daily_detail(mature_only=True)
    except (FileNotFoundError, ValueError):
        return {}

    if dim_col not in df.columns:
        return {}
    df = df[df[dim_col].notna()]
    if df.empty:
        return {}

    spend = df.groupby(dim_col)["spend"].sum()
    roi = ((df["spend"] * df["d7_roi"]).groupby(df[dim_col]).sum()
           / spend.where(spend > 0)).dropna()
    if roi.empty:
        return {}

    cr = semantic_layer.get_thresholds("creative")
    if not isinstance(cr, dict) or "error" in cr:
        cr = {}
    min_spend = cr.get("min_spend", 5000)
    cut, scale = cr.get("cut"), cr.get("scale")

    eligible = roi[spend.reindex(roi.index) > min_spend]
    if eligible.empty:
        return {}

    out: Dict[str, Any] = {
        "纳入统计": int(len(eligible)),
        "样本不足未纳入": int(len(roi) - len(eligible)),
        "样本门槛_累计花费": min_spend,
        "P10": round(float(eligible.quantile(0.10)), 4),
        "P50": round(float(eligible.quantile(0.50)), 4),
        "P90": round(float(eligible.quantile(0.90)), 4),
        "最低": round(float(eligible.min()), 4),
        "最高": round(float(eligible.max()), 4),
    }
    if cut is not None:
        out[f"低于关停线（{cut}）的个数"] = int((eligible < cut).sum())
    if scale is not None:
        out[f"高于加预算线（{scale}）的个数"] = int((eligible >= scale).sum())
    out["说明"] = ("素材表现只列出最差的 20 个，不代表全部；"
                   "判断是「个别素材差」还是「整体恶化」请看这块。")
    return out


# ============================================================
# 给 LLM 的数据整形：精度 + 空值
# ============================================================
# 为什么需要这一层，而不是直接把 DataFrame 丢过去：
#
#   1. 精度是浮点噪声，不是业务信息
#      pandas 读出来是 0.28853533219904687，LLM 会「引用」这个数字，
#      让回答显得很精确 —— 但后 12 位全是 IEEE754 尾巴。
#      6k 行的快照里，每个数字多省 10 个字符就是几万 token。
#
#   2. NaN 不是合法 JSON
#      json.dumps 默认 allow_nan=True，会把 NaN 写成裸的 NaN token。
#      OpenAI 兼容接口收到会 400，而且报错信息通常只说「非法 JSON」，
#      不会告诉你是哪个字段 —— 排查成本远高于在这里挡掉。
#      业务上 NaN 的含义也很明确：分母为 0（比如当天停投、花费为 0），
#      显式输出 null 让 LLM 知道「这里没数据」，比让它自己猜好。
#
#   3. 类型不一致
#      numpy 标量不继承 Python int，json.dumps 会走 default=str，
#      于是 44993 变成 "44993" 字符串，LLM 没法做数值比较。

_ROI_HINTS = ("roi", "rate", "ctr", "cvr", "retention", "ratio", "lift", "share")
_MONEY_HINTS = ("spend", "revenue", "cost", "cpi", "ecpm", "budget", "price", "arpu")


def _clean_scalar(key: str, v: Any) -> Any:
    """单个值：NaN/inf/NA → None，浮点按语义截断精度，numpy 标量转原生类型。"""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v
    if isinstance(v, int) and not isinstance(v, bool):
        return v

    # pandas 的 pd.NA / NaT：float() 会抛 TypeError，但它们语义上就是「空」。
    # 不特判的话会原样漏到 json.dumps 里变成非法 JSON —— 而这一步正是
    # 用来兜住这种漏网的。用类名判断而不是 import pandas，避免在热路径引入依赖。
    if v.__class__.__name__ in ("NAType", "NaTType"):
        return None

    try:
        f = float(v)
    except (TypeError, ValueError):
        return v                       # date / Decimal / 其他交给调用方或 default=str

    if math.isnan(f) or math.isinf(f):
        return None

    k = (key or "").lower()
    if any(h in k for h in _ROI_HINTS):
        return round(f, 4)
    if any(h in k for h in _MONEY_HINTS):
        return round(f, 2)
    return int(f) if f.is_integer() else round(f, 2)


def sanitize_for_llm(obj: Any, key: str = "") -> Any:
    """递归清洗任意结构，保证 json.dumps 能产出合法 JSON。

    用于所有回传给 LLM 的快照和工具结果。dict 的 key 会作为精度提示
    （roi_* 保留 4 位，spend/cost 类保留 2 位，计数类还原成整数）。
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_llm(v, k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_llm(v, key) for v in obj]
    return _clean_scalar(key, obj)


def _round_records(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """记录列表的精度截断 + NaN 清洗（sanitize_for_llm 的列表版）。"""
    return [sanitize_for_llm(r) for r in rows]


def _to_llm_content(obj: Any) -> str:
    """把任意结构序列化成**合法 JSON 字符串**，用于回传 LLM。

    用 allow_nan=False 而不是默认值，是为了让「漏网的 NaN」立刻暴露成异常，
    而不是悄悄生成 `NaN` 这种非 JSON token —— 后者会被上游以
    「invalid JSON」笼统拒绝，排查时根本不知道是哪个字段。

    但这里不把异常抛给调用方：一个字段序列化失败不该让整轮分析崩掉。
    降级成一条带错误信息的记录，LLM 至少能看到「这里有问题」，
    而 trace 里也留着原始对象可供定位。
    """
    clean = sanitize_for_llm(obj)
    try:
        return json.dumps(clean, ensure_ascii=False, allow_nan=False, default=str)
    except (ValueError, TypeError) as e:
        return json.dumps({
            "_serialize_error": f"{type(e).__name__}: {e}",
            "_preview": str(clean)[:500],
        }, ensure_ascii=False)


_DIM_BY_FILENAME = {
    "creative_table.parquet": "creative_id",
    "sub_channel_table.parquet": "sub_channel",
    "creative_table": "creative_id",
    "sub_channel_table": "sub_channel",
}


def _load_top(filename: str, top_n: int) -> List[Dict[str, Any]]:
    """
    从 ADS 明细层聚合出「最差的 Top N」，供快照使用。

    为什么不再读 creative_table.parquet / sub_channel_table.parquet：
        这两张是 DWS 全量表，聚合了全部 31 天，**没有 is_mature_d7 列**。
        但最后 7 天装机的用户只观察到 D0，D7 收入还没产生，分母（成本）
        却是全额 —— 每个对象的 D7 ROI 都被系统性稀释。

        实测（31 天 / 200 个素材 / 花费 > 5000）：
            全量口径 0.2885 ~ 1.1401
            成熟口径 0.3131 ~ 1.2413
            100% 的素材被低估，均值 -0.0529

        后果不是小数误差，是决策错误：
            按 cut 阈值 0.50 判断，全量口径判「该砍」65 个，
            成熟口径只有 47 个 —— **18 个素材会被误砍**；
            按 scale 阈值 0.90，「该加预算」漏掉 4 个。

        所以快照必须和工具调用共用同一口径：read_daily_detail(mature_only=True)。

    为什么聚合：ADS 明细层每个维度 ID 有多个 install_date 的行。
    不聚合直接 head(top_n) 会取到同一个 ID 的不同日期，把其他维度全挤掉，
    导致 LLM 只看得到一个素材。

    Args:
        filename: 兼容旧调用点，仅用于确定维度列
        top_n:    最多返回多少行（避免 token 爆炸）

    Returns:
        按加权 D7 ROI 升序的记录，最该优化的排最前
    """
    import pandas as pd

    dim_col = _DIM_BY_FILENAME.get(filename)
    if dim_col is None:
        return []

    try:
        df = warehouse_api.read_daily_detail(mature_only=True)
    except (FileNotFoundError, ValueError):
        return []

    if dim_col not in df.columns or "d7_roi" not in df.columns:
        return []

    df = df[df[dim_col].notna()]
    if df.empty:
        return []

    # 向量化聚合。不用 apply：13k 行 × 6k 组，apply 会慢一个量级
    spend = df.groupby(dim_col)["spend"].sum()
    grouped = pd.DataFrame({
        "spend": spend,
        "installs": df.groupby(dim_col)["installs"].sum(),
        # 加权 ROI = 总回收 / 总花费，不是每日 ROI 的简单平均
        "d7_roi": (df["spend"] * df["d7_roi"]).groupby(df[dim_col]).sum() / spend.where(spend > 0),
        # 只有 D7 已成熟的 cohort 才计入，这个天数是「有效观测天数」
        "mature_days": df.groupby(dim_col)["install_date"].nunique(),
    })
    if "clicks" in df.columns:
        grouped["clicks"] = df.groupby(dim_col)["clicks"].sum()

    grouped = grouped.reset_index()
    grouped = grouped.sort_values("d7_roi", ascending=True, na_position="last")

    return _round_records(grouped.head(top_n).to_dict(orient="records"))


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


def _get_channel_installs(sub_channel: str) -> Optional[int]:
    """某子渠道的**累计**安装量，给护栏判断样本是否足够。

    为什么用累计而不是单日：
        判断「这个 ROI 可不可信」看的是这个渠道一共观察了多少次安装，
        单日安装量只反映当天投了多少钱。看单日会把「今天投得少」
        误判成「样本不足」。

    为什么返回 Optional 而不是 0：
        两者语义完全不同——0 是「确实没量」，None 是「查不到/数据缺失」。
        护栏对这两种情况的处理也不同（None 直接 fail-closed 拒绝）。
        这里如果偷懒返回 0，就必须在护栏里区分「真 0」和「查不到」，做不到。
    """
    if not sub_channel:
        return None
    # 取数细节收口到 warehouse_api。这里保留「None 表示查不到」的语义 ——
    # 绝不能吞掉异常返回 0，那等于告诉护栏「这个渠道没有量」，反而可能放行删除。
    return warehouse_api.get_channel_installs(sub_channel)


def _get_channel_observed_days(sub_channel: str) -> Optional[int]:
    """某子渠道覆盖了多少个 install_date（也就是「跑了多少天」）。

    为什么按 install_date 去重计数，而不是用 max-min+1：
        中间断投的渠道（停了两天又开）用日期差会算成 10 天，但实际只有 8 天数据。
        护栏要判断的是「有没有足够的成熟 cohort」，看的是**实际有数据的天数**，
        不是日历跨度。所以用 nunique。

    为什么返回 Optional：
        和 _get_channel_installs 同理——None 是「查不到」，0 是「确实一天没跑」，
        护栏对前者要 fail-closed 拒绝，对后者要给「数据未成熟」的明确理由。
    """
    if not sub_channel:
        return None
    # 和 _get_channel_installs 一样，取数收口到 warehouse_api，语义保持不变。
    return warehouse_api.get_channel_observed_days(sub_channel)


# ============================================================
# 2. 工具定义（说明书）+ 工具注册表（查表执行）
# ============================================================

# ---- 2.1 工具说明书：告诉 LLM「有哪些工具、参数怎么填」 ----
# 这是 Function Calling 的核心：用原生 tools 参数传给模型，模型直接返回
# 结构化的 tool_calls，不需要我们在 prompt 里教它「输出什么 JSON 格式」。
# 参数里的 enum 是「约束」，防止 LLM 乱填不存在的值。
#
# ⚠️ 这一份里**只有读工具**。写操作（删/调预算/调目标）故意不在这里，
#    原因见下面 2.0 的说明。这是本项目最重要的一条架构约束。

# ---- 2.0 读写分离：为什么写操作不能是「工具」 ----
#
# 反面做法（已修正）：把 delete_sub_channel / adjust_budget / adjust_target
# 也注册进工具注册表。后果是 LLM 在**分析循环内部**就能直接触发写操作——
# 它查完数据顺手调一下 delete_sub_channel，平台就真的改了，人根本没机会看。
# 那样的话 approval 流程（pending → 人工确认）形同虚设，只是写在函数名上的装饰。
#
# 正确做法：LLM 在分析阶段**只有读权限**。它想做什么写操作，只能通过
# submit_decision 提交一份「建议单」，这份建议单落库为 pending，
# 由人 approve 之后才由 _execute_action 执行（且执行前再过一遍护栏）。
#
# 一句话：**权限边界要靠代码结构保证，不能靠 prompt 里写"不要直接删"。**

READ_TOOLS = [
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
                    "enum": ["d7_roi", "cpi", "buy_ctr", "buy_cvr", "buy_ecpm", "monetization_ecpm", "d7_retention", "rewarded_views_per_user", "retention_dn", "roi_dn", "ecpm_dn", "views_dn"],
                    "description": "要查口径的指标 key。d7_roi=D7回报率，cpi=安装成本，buy_ctr/buy_cvr/buy_ecpm=买量侧CTR/CVR/eCPM，monetization_ecpm=变现eCPM，d7_retention=D7留存，rewarded_views_per_user=人均观次，retention_dn/roi_dn/ecpm_dn/views_dn=全程指标(第N天)",
                },
            },
            "required": ["metric"],
        },
    },
]


# ---- 2.1b 终态工具：LLM 唯一的「表达写意图」出口 ----
#
# 这是一个**工具**，但它不执行任何写操作，只是把决策结构化地交出来。
# 调用它 = 「我分析完了，这是我的结论和我想做的动作」，然后循环结束，
# 动作落库为 pending 建议单，等人 approve。
#
# 为什么用工具而不是「让模型输出一段 JSON 文本」：
#   1. 参数受 schema 约束，模型没法漏字段或类型写错
#   2. 我们能拿到完整的 arguments 字符串，解析失败率远低于从自由文本里抠 {}
#   3. 决策结构（conclusion / reasoning / actions）有明确的必填项，可校验

SUBMIT_DECISION_TOOL = {
    "name": "submit_decision",
    "description": "分析完成后，用本工具提交最终决策。这是你唯一的收尾方式——"
                   "没有要优化的动作时也要调用它，actions 传空数组。"
                   "注意：调用本工具**不会**执行任何写操作，只是把建议交给人工确认。",
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {
                "type": "string",
                "description": "一句话总结你的判断（<=60字）",
            },
            "reasoning": {
                "type": "string",
                "description": "完整推理链：你看了哪些数据、发现了什么、"
                               "为什么得出这个结论、以及**为什么不做某些操作**。"
                               "这是给人看的可解释性说明，不要省略。",
            },
            "actions": {
                "type": "array",
                "description": f"建议执行的写动作，最多 {guardrails.MAX_ACTIONS_PER_RUN} 个。"
                               "没有就传空数组。每个动作都必须写 reason。",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(guardrails.VALID_ACTIONS),
                            "description": "delete_sub_channel=删子渠道；"
                                           "adjust_budget=调预算；adjust_target=调目标",
                        },
                        "target": {
                            "type": "string",
                            "description": "目标对象 ID：删渠道填子渠道ID（ch_003）；"
                                           "调预算/调目标填系列ID（US_A_01）",
                        },
                        "params": {
                            "type": "object",
                            "description": "参数。adjust_budget 需要 country + new_budget；"
                                           "adjust_target 需要 country + new_target；"
                                           "delete_sub_channel 不需要参数",
                            "properties": {
                                "country": {"type": "string", "description": "国家代码，如 US"},
                                "new_budget": {"type": "number",
                                               "description": f"新预算（USD），"
                                                              f"区间 {guardrails.BUDGET_MIN}~"
                                                              f"{guardrails.BUDGET_MAX}，"
                                                              f"单次变动不超过 "
                                                              f"{guardrails.BUDGET_MAX_RATIO}x"},
                                "new_target": {"type": "number",
                                               "description": f"新 ROAS 目标（小数），"
                                                              f"区间 {guardrails.TARGET_MIN}~"
                                                              f"{guardrails.TARGET_MAX}"},
                            },
                        },
                        "reason": {
                            "type": "string",
                            "description": "为什么做这个动作，以及依据了哪些数据",
                        },
                    },
                    "required": ["action", "target", "reason"],
                },
            },
        },
        "required": ["conclusion", "reasoning", "actions"],
    },
}

# 传给模型的完整工具清单（原生 tools 参数用，需要包一层 type=function）
TOOLS_DEFINITION = READ_TOOLS + [SUBMIT_DECISION_TOOL]


def _openai_tools() -> List[Dict[str, Any]]:
    """把内部工具定义转成 OpenAI tools 参数格式。"""
    return [{"type": "function", "function": t} for t in TOOLS_DEFINITION]


# ---- 2.2 读工具的真实实现（参数化：一个函数吃一类查询）----
def _tool_get_metric(obj_type: str, obj_id: str, metric: str,
                     days: int = 8) -> Dict[str, Any]:
    """
    查素材/子渠道某指标的每日明细。

    参数化：靠 obj_type 区分查素材还是子渠道，metric 区分查哪个指标，
    一个函数覆盖「对象×指标」的无限组合，而不是为每种组合写一个函数。

    口径处理（和 get_trend_with_ops 不同，这是故意的）：
        get_trend_with_ops 直接**剔除**不成熟 cohort —— 它要判趋势和因果，
        混进窗口不完整的数据必然得出错误结论。

        本工具**保留**全部日期，但给每行打 is_mature 标记，并在顶层补
        mature_hint。理由：LLM 调它通常是想「看最新发生了什么」，
        直接删掉最近 7 天等于没回答。让它看到真实数字 + 明确知道
        「这段还没成熟」，比让它以为渠道崩了要好。

        早期版本读的是 creative_table / sub_channel_table（DWS 全量表，
        没有 is_mature_d7 列），连标记都做不了 —— 现在统一走明细层。
    """
    if obj_type == "creative":
        dim_col = "creative_id"
    elif obj_type == "sub_channel":
        dim_col = "sub_channel"
    else:
        return {"error": f"未知对象类型 {obj_type}，只支持 creative/sub_channel"}

    try:
        df = warehouse_api.read_daily_detail()
    except (FileNotFoundError, ValueError):
        return {"error": "数据表不存在"}

    if dim_col not in df.columns:
        return {"error": f"数据表缺少维度列 {dim_col}"}

    sub = df[df[dim_col] == obj_id]
    if sub.empty:
        return {"error": f"未找到 {obj_id} 的数据",
                "hint": "确认 ID 拼写；可用 get_top_history 看当前有哪些 ID"}

    if metric not in sub.columns:
        measurable = [c for c in sub.columns
                      if c in ("d7_roi", "cpi", "spend", "installs",
                               "d7_retention", "d7_ecpm")
                      or c.startswith(("roi_", "ret_", "ecpm_"))]
        return {"error": f"指标 {metric} 不存在", "available": measurable}

    # daily_detail 的粒度是「装机日 × app × country × campaign × 素材 × 渠道」，
    # 比「对象 × 日」更细 —— 同一个渠道同一天会有多行。
    # 不聚合直接 tail(days) 会返回同一天的若干行，LLM 会当成不同日期的数据，
    # 看到「ROI 从 0.35 跳到 0.48」这种根本不存在的波动。
    #
    # 聚合口径：
    #   · 求和类（spend / installs）→ 直接相加
    #   · 比率类（d7_roi / roi_n / ret_n / ecpm_n）→ 按花费加权
    #     加权 ROI = Σ(spend × roi) / Σspend，正好等于 Σ回收 / Σ花费
    #   · 其余 → 算术平均
    #
    # NULL 必须保持 NULL，不能变成 0 —— 这是 ADS 建表时特意区分过的语义：
    #   d7_retention 在「观察窗口没走完」时是 NULL（不知道），
    #   只有「窗口走完但 D7 真的没记录」才是 0（用户全流失）。
    #   pandas 的 sum() 会跳过 NaN，如果不显式判断，一天全是 NULL 会被算成
    #   0.0/总花费 = 0.0，于是 LLM 看到「D7 留存归零，用户全跑了」——
    #   而真相只是「还没到能看的时候」。
    _SUM_COLS = {"spend", "installs", "clicks", "buy_impressions",
                 "impressions", "conversions", "views"}

    series = []
    for d, g in sub.groupby("install_date"):
        row: Dict[str, Any] = {"date": str(d)[:10]}
        valid = g[metric].notna()

        if valid.sum() == 0:
            row[metric] = None                        # 整天无法计算 → 保持未知
        elif metric in _SUM_COLS:
            row[metric] = float(g[metric].sum())
        else:
            # 只用有效行参与加权，否则 NULL 行的花费会稀释分子
            w = g[valid]
            w_spend = float(w["spend"].sum()) if "spend" in w.columns else 0.0
            if w_spend > 0:
                row[metric] = float((w[metric] * w["spend"]).sum() / w_spend)
            else:
                row[metric] = float(w[metric].mean())

        if "is_mature_d7" in g.columns:
            # 同一天的多行 maturity 一定相同（只由装机日决定），all() 只是防御
            row["is_mature"] = bool(g["is_mature_d7"].all())
        series.append(row)

    series = series[-days:]
    out: Dict[str, Any] = {"obj_type": obj_type, "obj_id": obj_id,
                           "metric": metric, "data": series}

    n_immature = sum(1 for x in series if x.get("is_mature") is False)
    if n_immature:
        dates = [x["date"] for x in series if x.get("is_mature") is False]
        out["mature_hint"] = (
            f"最后 {n_immature} 天（{dates[0]} ~ {dates[-1]}）is_mature=false："
            f"这些 cohort 的 D7 观察窗口还没走完，D7 类指标"
            f"（d7_roi / d7_retention / d7_ecpm）会系统性偏低，"
            f"不能据此判断对象变差。判趋势请用 get_trend_with_ops（它已剔除不完整 cohort）。"
        )
    return sanitize_for_llm(out)


def _tool_get_logs(keyword: str, limit: int = 10) -> Dict[str, Any]:
    """按关键词查操作日志，判断对象是否被操作过。"""
    logs = _op_records(100)
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

    # 排行是要拿来做决策的（该砍哪个渠道、该给谁加预算），必须用成熟的 cohort。
    # 用全量口径时 ch_008 算出来 0.829，用成熟口径是 0.873 —— 而快照里
    # 用的是后者。两个数字同时出现在一次对话里，LLM 会不知道该信哪个。
    try:
        df = warehouse_api.read_daily_detail(mature_only=True)
    except (FileNotFoundError, ValueError):
        return {"error": "daily_detail 数据不存在"}

    if dim not in df.columns:
        return {"error": f"维度 {dim} 不存在，可选: app/sub_channel/creative_id/campaign/country"}
    if metric not in df.columns:
        return {"error": f"指标 {metric} 不存在，可选: d7_roi/spend/installs/cpi"}

    spend = df.groupby(dim)["spend"].sum()
    installs = df.groupby(dim)["installs"].sum()
    grouped = pd.DataFrame({"spend": spend, "installs": installs})

    # 比率指标必须用「总量相除」，不能对每日值取平均 ——
    # 一个花费 10 万的渠道和一个花费 1000 的渠道，简单平均会让后者权重一样大。
    if metric == "d7_roi":
        grouped["d7_roi"] = ((df["spend"] * df["d7_roi"]).groupby(df[dim]).sum()
                             / spend.where(spend > 0))
    elif metric == "cpi":
        grouped["cpi"] = spend / installs.where(installs > 0)
    elif metric not in ("spend", "installs"):
        grouped[metric] = df.groupby(dim)[metric].mean()

    # 有效观测天数（只用成熟 cohort，所以是「可比较的天数」而非自然天数）
    grouped["mature_days"] = df.groupby(dim)["install_date"].nunique()

    grouped = grouped.reset_index()
    sort_col = metric if metric in grouped.columns else "spend"
    grouped = grouped.sort_values(sort_col, ascending=False, na_position="last")

    top = _round_records(grouped.head(top_n).to_dict(orient="records"))
    bottom = _round_records(grouped.tail(top_n).to_dict(orient="records"))

    return {
        "dim": dim, "metric": metric,
        "口径": "仅统计 D7 观察窗口完整的 cohort",
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
    # 这里必须用 mature_only=True：本工具的用途是「判断趋势 + 因果关系」，
    # 而尾部 cohort 的 D7 数据不完整（只活了 0~6 天），算出来的 d7_roi 天然偏低。
    # 实测 ch_001 从 10-24 的 0.73 掉到 10-31 的 0.36 —— 看着像渠道崩了，
    # 其实只是观察窗口不够。用这种数据做因果判断，会让 LLM 得出完全错误的结论。
    #
    # 代价是最近 7 天的 cohort 不参与趋势。想看最新明细用 _tool_get_metric（不过滤）。
    # 这个分工是有意的：**看最新用 get_metric，判趋势用 get_trend_with_ops**。
    try:
        df = warehouse_api.read_daily_detail(mature_only=True)
    except FileNotFoundError:
        return {"error": "daily_detail 数据不存在"}

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
    logs = _op_records(200)
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

def _build_tool_registry(plat=None) -> Dict[str, Any]:
    """
    把「读工具」装进名字→函数的表。

    ⚠️ 这里**故意不含写工具**。写操作不在 LLM 可调用的范围内，
    只能通过 submit_decision 提交建议、人工 approve 后由 _execute_action 执行。

    保留 plat 参数只是为了兼容旧调用点；读工具不需要平台实例。
    """
    return {
        "get_metric": _tool_get_metric,
        "get_logs": _tool_get_logs,
        "get_top_history": _tool_get_top_history,
        "get_trend_with_ops": _tool_get_trend_with_ops,
        "search_knowledge": _tool_search_knowledge,
        "get_metric_definition": _tool_get_metric_definition,
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
5. 只有当所有信息都够了，才调用 submit_decision 提交你的判断和建议动作

关于权限（重要）：
- 你在分析阶段**只能读**。没有任何工具能让你直接改动投放配置。
- 你想做的写操作（删子渠道 / 调预算 / 调目标），只能写进 submit_decision 的 actions 里，
  作为「建议」提交。这些建议会交给人审核，人确认后才执行。
- 所以不要假装你已经做了操作，也不要说「已删除」——你只是提出了建议。

工具使用优先级（重要）：
- 数据能直接判断 → 只用数据工具（get_metric / get_top_history / get_trend_with_ops），不要多余地查经验
- 数据查清了、但仍不确定「这类问题业界通常怎么处理」→ 此时才用 search_knowledge 检索经验做参考
- 经验是「补充参考」，不能替代数据判断，也不能作为写操作的唯一依据
- 口径判断（重要）：当你需要「用某个指标做判断、但不确定它的精确算法」时，先调 get_metric_definition 拿标准定义。
  尤其警惕「同名不同义」——eCPM 分买量（spend/buy_impressions×1000）和变现（ad_revenue/ad_impressions×1000）两套，
  CTR/CVR 也分买量侧和变现侧。拿不准口径就先查，不要按字面猜。

收尾方式：
- 分析完必须调用 submit_decision，填清楚 conclusion（一句话结论）、reasoning（完整推理链）、
  actions（建议动作，没有就传空数组）。
- 不要用普通文字回复代替 submit_decision，那样你的结论不会被记录。
- reasoning 里要写清「为什么这么做」以及「为什么不做某些操作」，这是给人看的可解释性说明。

写动作的约束（submit_decision 里的 actions 会被系统校验，不合规会被拒绝）：
- 一次最多 5 个动作
- adjust_budget 的新预算必须在 1 ~ 50000 之间，且单次变动不超过原值的 3 倍
- adjust_target 的新目标必须在 0.1 ~ 3.0 之间
- 每个动作都必须写 reason
- 拿不准要不要动的时候就不动。宁可 actions 为空，也不要为了"有产出"硬凑动作
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


def _call_once(client, model, messages, tools=None):
    """单次调用 LLM，返回 (message 对象, token 用量)。

    和旧版的区别：旧版只取 message.content（字符串），靠 prompt 教模型「输出什么 JSON」；
    现在把 tools 直接传给模型，模型返回结构化的 tool_calls，我们不需要再解析自由文本。

    这带来三个实际收益：
      1. 工具清单不再写进 system prompt，每轮省下几千 input token
         （而且 system prompt 短而稳定，更容易命中 provider 的 prompt cache）
      2. 参数由 schema 约束，模型不会漏字段、不会把数字写成字符串
      3. 可以一次返回多个 tool_calls，读操作天然可并行

    为什么多返回 usage：
        响应对象上本来就有 resp.usage，但旧代码 `return resp.choices[0].message`
        把它直接扔了——于是整个项目跑了几十次也没留下任何成本数据。
        拿到 usage 之后，一次运行花多少钱才是可回答的问题，
        而不是「大概几毛钱吧」。
    """
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "timeout": 60.0,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message, llm_cost.extract_usage(resp)


def run_llm_loop(snapshot: Dict[str, Any], plat=None,
                 max_rounds: int = 8) -> Dict[str, Any]:
    """
    多轮原生 Function Calling 主循环。

    流程：
        LLM → tool_calls（可能一次多个）→ 逐个执行 → 结果以 role=tool 回传 → 继续
        LLM → submit_decision → 循环结束，返回决策

    读写分离（关键）：
        这里能执行的只有 registry 里的 6 个读工具。LLM 就算编造一个
        delete_sub_channel 的调用，也会因为 registry 里没有而返回 error，
        **不会落到平台上**。写操作只能走 submit_decision → pending → approve。

    兜底：
        少数模型/网关不返回 tool_calls，而是把 JSON 写在 content 里。
        这时走 parse_llm_step 解析文本，保证不因为 provider 差异而整体失败。

    Args:
        snapshot:   快照 JSON
        plat:       保留参数，兼容旧调用点（读工具不需要平台实例）
        max_rounds: 最多几轮，防死循环

    Returns:
        {
          "conclusion": ..., "reasoning": ..., "actions": [...], "trace": [...],
          "usage": {model, calls, prompt_tokens, completion_tokens,
                    cache_hit_rate, input_per_call, cost_usd, cost_cny}
        }

        其中 usage 是**一次完整分析**的累计成本。注意它不能按
        「单次调用单价 × 轮数」估算：每轮都要重发完整历史，
        累计输入 token 随轮数呈三角数增长（见 llm_cost 模块注释）。
    """
    client, model = _make_client()
    registry = _build_tool_registry()
    tools = _openai_tools()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "这是中台数据快照：\n" + _to_llm_content(snapshot)},
    ]

    trace: List[Dict[str, Any]] = []
    decision: Optional[Dict[str, Any]] = None
    usage = dict(llm_cost.EMPTY)     # 累计 token 用量，最后折算成一次任务成本

    for round_no in range(1, max_rounds + 1):
        msg, one_usage = _call_once(client, model, messages, tools=tools)
        usage = llm_cost.merge(usage, one_usage)
        tool_calls = getattr(msg, "tool_calls", None) or []

        # ---------- 情况 A：模型给出了工具调用 ----------
        if tool_calls:
            # 先把 assistant 的 tool_calls 原样放回历史，否则下一轮格式不合法
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments or "{}"}}
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                # ---- 终态工具：不执行任何写操作，只记录决策 ----
                if name == "submit_decision":
                    decision = {
                        "conclusion": args.get("conclusion", ""),
                        "reasoning": args.get("reasoning", ""),
                        "actions": args.get("actions") or [],
                    }
                    trace.append({
                        "round": round_no,
                        "step": "finish",              # 保留旧字段值，兼容前端/trace 打印
                        "source": "submit_decision",
                        "reasoning": decision["reasoning"][:300],
                        "actions_count": len(decision["actions"]),
                    })
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": json.dumps(
                            {"ok": True,
                             "message": "决策已记录为待确认建议单，等待人工审核后执行"},
                            ensure_ascii=False),
                    })
                    continue

                # ---- 读工具：查表执行 ----
                func = registry.get(name)
                if func is None:
                    result = {"error": f"工具 {name} 不在分析阶段的可调用清单里"
                                       f"（写操作只能通过 submit_decision 提交建议）"}
                else:
                    try:
                        result = func(**args)       # 关键：按 name 找到函数，args 原样传入
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}

                # 记录「调了什么 + 参数 + 返回了什么」，这是可解释性的客观证据
                trace.append({"round": round_no, "step": "tool", "name": name,
                              "parameters": args, "result": result})

                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": _to_llm_content(result),
                })

            if decision is not None:
                return {**decision, "trace": trace,
                        "usage": llm_cost.finalize(usage, model)}
            continue

        # ---------- 情况 B：模型没调工具，直接回了文本 ----------
        text = msg.content or ""
        fallback = parse_llm_step(text)
        if fallback.get("step") == "finish":
            trace.append({"round": round_no, "step": "finish",
                          "source": "text-fallback",
                          "reasoning": (fallback.get("reasoning") or "")[:300]})
            return {
                "conclusion": fallback.get("conclusion", ""),
                "reasoning": fallback.get("reasoning", ""),
                "actions": fallback.get("actions") or [],
                "trace": trace,
                "usage": llm_cost.finalize(usage, model),
            }

        trace.append({"round": round_no, "step": "no_tool_call",
                      "raw": text[:200]})
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content":
            "请调用 submit_decision 提交最终决策（没有要做的动作就传空 actions）。"})

    return {"conclusion": "达到最大轮数仍未提交决策", "reasoning": "",
            "actions": [], "trace": trace,
            "usage": llm_cost.finalize(usage, model),
            "exhausted": True}


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
    # persist=True：真正跑一轮时才写决策日志。
    # 调试/展示调用不要传，否则调一次就污染一次决策历史。
    snapshot = build_snapshot(persist=True)
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
    print("--- trace 结束 ---")
    # 成本在这里就打印，而不是等整个流程结束：
    # 如果后面执行动作出错崩了，至少还能看到这次分析已经花了多少
    print("本次分析成本:", llm_cost.format_line(decision.get("usage", {})))
    print()

    print("结论:", decision["conclusion"])
    print("动作数:", len(decision["actions"]))
    if decision.get("reasoning"):
        print("推理链:", decision["reasoning"])

    # 3. 执行写动作（只有写操作才真正落库）
    executed = []
    write_actions = [a for a in decision["actions"]
                     if a.get("action") in VALID_ACTIONS]

    # 批量预检：数量上限 + 同对象去重（第 2 层防线的入口）
    write_actions, batch_rejected = guardrails.check_batch(write_actions)
    if batch_rejected:
        print("\n[护栏] 批量预检拦下 " + str(len(batch_rejected)) + " 条：")
        for r in batch_rejected:
            print("  - " + str(r.get("reason")))

    if not dry_run and write_actions:
        print("\n[2/2] 执行写动作...")
        for a in write_actions:
            r = _execute_action(plat, a)      # 单条执行时再过一遍业务边界
            r.setdefault("action", a.get("action", ""))   # 看板靠它匹配「哪条已执行」
            executed.append(r)
            flag = "已拦截" if r.get("blocked_by") else "执行"
            print(f"  - [{flag}] {a['action']} {a.get('target')}: {r.get('message')}")
    elif dry_run:
        print("\n[2/2] dry_run 模式，跳过执行（仅分析）")

    return {
        "conclusion": decision["conclusion"],
        "reasoning": decision.get("reasoning", ""),
        "actions": decision["actions"],
        "batch_rejected": batch_rejected,
        "trace": decision.get("trace", []),
        "executed": executed,
        "usage": decision.get("usage", {}),
    }


def _get_current_cfg(campaign_id: str, country: str) -> Dict[str, Any]:
    """
    取某系列某国家的**当前真实配置**，给护栏算变动幅度用。

    为什么执行时要现查而不是用建议单里记的值：
        建议单可能是几分钟甚至几小时前生成的，期间人可能已经手动改过，
        或者另一个 Agent 已经执行了相反的动作。用旧值当基线会导致「幅度校验」失效。
    """
    if not campaign_id or not country:
        return {}
    try:
        for c in _list_countries(campaign_id):
            if c.get("country") == country:
                return {"budget": c.get("budget"), "target": c.get("target")}
    except Exception:
        # 查不到就返回空，护栏会按「无基线」处理（adjust_budget 会拒绝）
        return {}
    return {}


def _current_context_for(action: Dict[str, Any]) -> Dict[str, Any]:
    """为护栏组装「该对象的当前真实状态」。

    **必须收口在这一个函数里，不要让每个调用方自己拼。**

    为什么：mcp_server 的 submit_suggestion 原本只处理 adjust_budget/adjust_target，
    漏掉了 delete 需要的 installs。结果是同一个动作（删 ch_001，39833 次安装，
    完全合法）在执行层放行、在 MCP 侧被 fail-closed 拒绝，理由还是
    「查不到安装量」——而安装量明明查得到，只是没人去查。
    这种「同一个守卫函数、两个调用方、各自拼参」的写法迟早会漂移，
    所以把组装逻辑和校验逻辑分开：校验是纯函数，组装只有一个实现。

    新增动作类型时只需要改这里，两处入口自动一致。
    """
    a_type = action.get("action") or ""
    target = action.get("target") or ""
    params = action.get("params") or {}

    if a_type in ("adjust_budget", "adjust_target"):
        return _get_current_cfg(target, params.get("country", ""))
    if a_type == "delete_sub_channel":
        # 删除要看两个独立维度：样本量（装了多少次）+ 观察时长（跑了多少天）。
        # 查不到任一个都返回 None，护栏据此 fail-closed 拒绝——
        # 不能因为「读数据失败」就放行删除。
        return {"installs": _get_channel_installs(target),
                "days": _get_channel_observed_days(target)}
    return {}


def _execute_action(plat, action: Dict[str, Any]) -> Dict[str, Any]:
    """
    把解析出的写动作转成平台调用。

    **执行前必须先过 guardrails**，这是最后一道路障：
        LLM 的判断 → 人工确认 → 护栏校验 → 平台执行

    注意顺序：护栏在人工确认之后、平台调用之前。这样即使人点了确认，
    一个明显错误的动作（预算加个零、目标写成 70 而不是 0.7）仍然会被拦下。
    """
    a_type = action.get("action", "")
    target = action.get("target", "")
    params = action.get("params") or {}
    reason = action.get("reason") or "Agent 自动优化"

    # ---- 第 2 层防线：业务边界校验（拿当前真实状态算幅度/样本量）----
    current = _current_context_for(action)

    ok, why = guardrails.validate_action(action, current)
    if not ok:
        return {"ok": False, "blocked_by": "guardrails",
                "action": a_type, "target": target,
                "message": f"护栏拦截：{why}"}

    # ---- 通过校验，才真正打到平台 ----
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
        {"run_id": ..., "conclusion": ..., "reasoning": ..., "actions": [...],
         "trace": [...], "usage": {...}}
        run_id 是这次建议的 ID，投放人凭它调用 approve/reject。
        usage 是这次分析的 token 消耗与折算成本，已同时落库（agent_run_history.usage_json），
        可以直接回答「跑一轮多少钱」。
    """
    import platform_layer
    plat = platform_layer.get_platform("mock")

    # 1. 组装快照 + 多轮 LLM（只分析，不执行写操作）
    # persist=True：真正跑一轮时才写决策日志。
    # 调试/展示调用不要传，否则调一次就污染一次决策历史。
    snapshot = build_snapshot(persist=True)
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
        usage=decision.get("usage"),
    )

    return {
        "run_id": run_id,
        "conclusion": decision.get("conclusion", ""),
        "reasoning": decision.get("reasoning", ""),
        "actions": write_actions,
        "trace": decision.get("trace", []),
        "usage": decision.get("usage", {}),
    }


def approve_agent_run(run_id: int) -> Dict[str, Any]:
    """
    投放人确认一条 pending 建议单，真正执行里面的写动作。

    为什么确认之后还要重新校验（TOCTOU）：
        建议单从生成到被人点「确认」之间可能隔了很久。这期间配置可能已经变了
        ——别人手动调过预算、或者另一张建议单已经执行了相反的动作。
        如果直接按生成时的判断执行，就会出现「基于过期信息做决策」。

        所以这里的顺序是：**先拿当前真实配置重跑一遍护栏，通过的才执行**。
        护栏拦下的动作会带着原因记进 executed，人能直接看到「为什么没生效」。

    Args:
        run_id: 待确认建议单的 ID

    Returns:
        {"ok": True, "run_id": ..., "executed": [...], "blocked": [...]}
    """
    import platform_layer
    run = dim_scd.get_agent_run(run_id)
    if run is None:
        return {"ok": False, "message": f"建议单 {run_id} 不存在"}
    if run["status"] != "pending":
        return {"ok": False, "message": f"建议单 {run_id} 状态为 {run['status']}，只有 pending 才能确认"}

    # 批量预检：数量上限 + 同对象去重（生成时可能没过，这里再兜一次）
    actions, batch_rejected = guardrails.check_batch(run["actions"])

    plat = platform_layer.get_platform("mock")
    executed = []
    blocked = []
    for a in actions:
        r = _execute_action(plat, a)          # 内部会拿当前配置重算幅度
        r["action"] = a.get("action", "")
        executed.append(r)
        if r.get("blocked_by"):
            blocked.append({"action": a.get("action"), "target": a.get("target"),
                            "reason": r.get("message")})

    blocked.extend([{"action": r.get("action", {}).get("action") if isinstance(r.get("action"), dict) else None,
                     "target": None, "reason": r.get("reason")}
                    for r in batch_rejected])

    # 更新状态：全部被拦也算 processed，避免建议单永久卡在 pending
    dim_scd.update_agent_run(run_id, status="executed", executed=executed)

    return {"ok": True, "run_id": run_id, "executed": executed,
            "blocked": blocked, "blocked_count": len(blocked)}


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
