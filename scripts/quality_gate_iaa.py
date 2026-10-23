# -*- coding: utf-8 -*-
"""IAA 数仓质量门禁（Quality Gate）

为什么要有这个脚本
------------------
流水线「跑完不报错」和「数据是对的」是两件不同的事。
下面这些故障全部**不会抛出异常**，只会安静地把错误数据交给下游：

  - 上游只送了半天数据 → 分区数从 31 掉到 15，表照样建成功
  - 上游 exactly-once 失效 → 同一行出现两次，sum 直接翻倍
  - ADS 的 WHERE 条件写窄了 → 丢掉 3% 直投流量，三张表总额对不上
  - CREATE TABLE IF NOT EXISTS 撞上已存在的旧表 → 加了一列却读不到，静默空操作
  - 成本单位从「元」变成「分」→ ROI 差 100 倍，但除法不报错

所以每个任务后面都要挂一道**主动断言**，而不是等报表出问题再回查。

规则从哪来
----------
不写在代码里，读 `iaa-analytics-agent/src/metrics.yaml` 的 `data_quality` 段。
理由：口径和校验必须同源。如果断言写在代码里、口径写在 YAML 里，
改了一处忘了另一处，就会出现「按新口径算、用旧边界校验」的诡异状态。

用法
----
    python scripts/quality_gate_iaa.py --scope ads
    python scripts/quality_gate_iaa.py --scope all --dt 2026-10-15
    python scripts/quality_gate_iaa.py --scope ods --list

退出码：
    0 = 全部通过
    1 = 有断言失败（DAG 应该 fail，不要放行下游）
    2 = 参数/环境错误
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import yaml

# ---- 路径：本文件在 ad-analytics/scripts/ 下 ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
METRICS_YAML = PROJECT_ROOT / "iaa-analytics-agent" / "src" / "metrics.yaml"

# 各 scope 覆盖哪些表
SCOPES: Dict[str, List[str]] = {
    "ods": ["ods_iaa_user_daily", "ods_iaa_acquisition_daily"],
    "dwd": ["dwd_user_daily"],
    "dws": ["dws_cohort_daily"],
    "ads": ["ads_daily_detail", "ads_creative_table", "ads_sub_channel_table"],
    # 日志链路单独一个 scope：它不按 dt 分区（按 data_as_of / 操作日），
    # 和上面四层的分区语义不同，混在一起会让「--dt 只校验一天」这个优化失效。
    "log": ["ods_decision_log", "ads_decision_latest",
            "ads_decision_stats", "ods_operation_log"],
}
SCOPES["all"] = (SCOPES["ods"] + SCOPES["dwd"] + SCOPES["dws"]
                 + SCOPES["ads"] + SCOPES["log"])

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def load_rules() -> Dict[str, Any]:
    """读 metrics.yaml 里的 data_quality 段。

    Returns:
        {表名: {...}}

    Raises:
        FileNotFoundError: YAML 不存在（口径资产缺失，不能带着跑）
    """
    if not METRICS_YAML.exists():
        raise FileNotFoundError(f"语义层文件不存在: {METRICS_YAML}")
    with open(METRICS_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = data.get("data_quality")
    if not rules:
        raise KeyError("metrics.yaml 缺 data_quality 段")
    return rules


# ============================================================
# 断言实现
# ============================================================
# 每个函数签名统一为 (spark, table, rule, dt) -> (status, message)
# 统一签名才能放进 dispatch 表里按 check 名字查。
# ============================================================


def _q(table: str) -> str:
    """表名加库前缀。lab 是固定库名，和所有 build_* 脚本保持一致。"""
    return f"lab.{table}"


def _count(spark, table: str, where: str | None = None) -> int:
    sql = f"SELECT count(*) c FROM {_q(table)}"
    if where:
        sql += f" WHERE {where}"
    return spark.sql(sql).collect()[0]["c"]


def _dt_where(spark, table: str, dt: str | None) -> str:
    """把 `--dt` 编译成 WHERE 子句，**表里没有 dt 列时自动放弃**。

    为什么要这个判断：
        日志链路的表按 data_as_of / 操作日分区，根本没有 dt 列。
        原来那两处直接拼 `dt = '...'`，只要有人跑
        `--scope all --dt 2026-10-15` 就会抛 UNRESOLVED_COLUMN，
        而错误信息指向的是一列「本该存在」的东西，排查方向会跑偏。

        放弃 dt 过滤不会漏校验 —— 日志表只有几百行，全表扫本来就不花时间。
    """
    if not dt:
        return "1=1"
    if "dt" not in spark.table(_q(table)).columns:
        return "1=1"
    return f"dt = '{dt}'"


def _partitions(spark, table: str) -> List[str]:
    rows = spark.sql(f"SHOW PARTITIONS {_q(table)}").collect()
    return sorted(r[0].split("=", 1)[1] for r in rows if "=" in r[0])


def check_partition_count_between(spark, table, rule, dt) -> Tuple[str, str]:
    n = len(_partitions(spark, table))
    lo, hi = rule["min"], rule["max"]
    ok = lo <= n <= hi
    return (PASS if ok else FAIL), f"{n} 个分区（要求 {lo}~{hi}）"


def check_rows_per_partition_between(spark, table, rule, dt) -> Tuple[str, str]:
    # 传了 --dt 就只看这一个分区：日调度不该为了校验一天而扫全表（3500 万行）
    where = f"WHERE dt = '{dt}'" if dt else ""
    rows = spark.sql(f"SELECT dt, count(*) c FROM {_q(table)} {where} GROUP BY dt").collect()
    if not rows:
        return FAIL, f"分区 dt={dt} 没有数据" if dt else "表里一行都没有"
    counts = [(r["dt"], r["c"]) for r in rows]
    lo, hi = rule["min"], rule["max"]
    bad = [(d, c) for d, c in counts if not (lo <= c <= hi)]
    rng = f"{min(c for _, c in counts):,}~{max(c for _, c in counts):,}"
    scope = f"dt={dt}" if dt else f"{len(counts)} 个分区"
    if bad:
        worst = bad[:3]
        return FAIL, f"越界 {len(bad)} 个（{scope}），实测 {rng}，例如 {worst}"
    return PASS, f"{scope}，单日 {rng}（要求 {lo:,}~{hi:,}）"


def check_rows_per_partition_equal(spark, table, rule, dt) -> Tuple[str, str]:
    where = f"WHERE dt = '{dt}'" if dt else ""
    rows = spark.sql(f"SELECT dt, count(*) c FROM {_q(table)} {where} GROUP BY dt").collect()
    if not rows:
        return FAIL, f"分区 dt={dt} 没有数据" if dt else "表里一行都没有"
    want = rule["value"]
    bad = [(r["dt"], r["c"]) for r in rows if r["c"] != want]
    if bad:
        return FAIL, f"{len(bad)} 个分区的行数 ≠ {want}，例如 {bad[:3]}"
    scope = f"dt={dt}" if dt else f"{len(rows)} 个分区"
    return PASS, f"{scope} 行数 = {want}"


def check_duplicate_rate_between(spark, table, rule, dt) -> Tuple[str, str]:
    """断言重复率落在预期区间。

    为什么要用「区间」而不是「必须为 0」：
    ODS 是原始落地层，契约就是「上游给什么就存什么」，重复行是上游的真实故障。
    对 ODS 要求主键唯一是**错的契约** —— 那会逼着摄入层做去重，
    把「上游有重复」这个事实掩盖掉，等真出问题时就查不出来了。
    所以这里断言的是「重复率符合已知的脏数据特征」，去重交给 DWD。
    """
    keys = rule["keys"]
    key_sql = ", ".join(keys)
    where = f"dt = '{dt}'" if dt else "1=1"
    row = spark.sql(
        f"""SELECT sum(n - 1) AS dup_rows, sum(n) AS total FROM (
                SELECT {key_sql}, count(*) n FROM {_q(table)}
                WHERE {where} AND {" AND ".join(f"`{k}` IS NOT NULL" for k in keys)}
                GROUP BY {key_sql} HAVING count(*) > 1
            )"""
    ).collect()[0]
    total = _count(spark, table, where)
    dup = int(row["dup_rows"] or 0)
    rate = dup / total if total else 0.0
    lo, hi = rule["min"], rule["max"]
    ok = lo <= rate <= hi
    return (PASS if ok else FAIL), f"重复行 {dup:,} / {total:,} = {rate:.3%}（要求 {lo:.1%}~{hi:.1%}）"


def check_no_duplicate_grain(spark, table, rule, dt) -> Tuple[str, str]:
    keys = rule["keys"]
    key_sql = ", ".join(keys)
    where = _dt_where(spark, table, dt)
    dup = spark.sql(
        f"""SELECT count(*) c FROM (
                SELECT {key_sql}, count(*) n FROM {_q(table)}
                WHERE {where} GROUP BY {key_sql} HAVING count(*) > 1
            )"""
    ).collect()[0]["c"]
    if dup:
        return FAIL, f"{dup} 组主键重复（{key_sql}），sum 会翻倍"
    return PASS, f"主键 {key_sql} 无重复"


def check_rows_not_greater_than_ods(spark, table, rule, dt) -> Tuple[str, str]:
    where = f"dt = '{dt}'" if dt else None
    n_dwd = _count(spark, table, where)
    n_ods = _count(spark, "ods_iaa_user_daily", where)
    if n_dwd > n_ods:
        return FAIL, f"DWD {n_dwd:,} > ODS {n_ods:,}，join 可能爆炸"
    return PASS, f"DWD {n_dwd:,} ≤ ODS {n_ods:,}（保留 {n_dwd / n_ods:.1%}）"


def check_d7_retention_between(spark, table, rule, dt) -> Tuple[str, str]:
    # 只取 D7 观察窗口完整的 cohort，否则分母含未成熟用户，留存必然偏低
    max_dt = spark.sql(f"SELECT max(dt) m FROM {_q(table)}").collect()[0]["m"]
    r = spark.sql(
        f"""SELECT sum(CASE WHEN day_offset = 7 THEN active_users ELSE 0 END)
                 / sum(CASE WHEN day_offset = 0 THEN active_users ELSE 0 END) AS rate
            FROM {_q(table)}
            WHERE install_date <= date_sub(to_date('{max_dt}'), 7)"""
    ).collect()[0]["rate"]
    if r is None:
        return SKIP, "窗口内没有完整 cohort"
    lo, hi = rule["min"], rule["max"]
    ok = lo <= float(r) <= hi
    return (PASS if ok else FAIL), f"D7 留存 {float(r):.2%}（要求 {lo:.0%}~{hi:.0%}）"


def check_row_count_equal(spark, table, rule, dt) -> Tuple[str, str]:
    n = _count(spark, table)
    want = rule["value"]
    ok = n == want
    return (PASS if ok else FAIL), f"{n:,} 行（要求 {want:,}）"


def check_column_count_equal(spark, table, rule, dt) -> Tuple[str, str]:
    n = len(spark.table(_q(table)).columns)
    want = rule["value"]
    ok = n == want
    return (PASS if ok else FAIL), f"{n} 列（要求 {want}）"


def check_non_null(spark, table, rule, dt) -> Tuple[str, str]:
    cols = rule["columns"]
    expr = ", ".join(f"sum(CASE WHEN `{c}` IS NULL THEN 1 ELSE 0 END) AS `{c}`" for c in cols)
    row = spark.sql(f"SELECT {expr} FROM {_q(table)}").collect()[0]
    bad = {c: row[c] for c in cols if row[c]}
    if bad:
        return FAIL, f"空值 {bad}"
    return PASS, f"{len(cols)} 个关键列无空值"


def check_d7_roi_between(spark, table, rule, dt) -> Tuple[str, str]:
    # 只看成熟 cohort。花费/安装量门槛按表设：
    #   ads_daily_detail 单行已经是「一天 × 一个维度组合」，spend 只有几百，
    #   照搬素材级的 5000 门槛会把整张表过滤空，得到 max/min = NULL。
    min_spend = rule.get("min_spend", 0)
    min_installs = rule.get("min_installs", 0)
    r = spark.sql(
        f"""SELECT max(d7_roi) hi, min(d7_roi) lo, count(*) n FROM {_q(table)}
            WHERE is_mature_d7 = true
              AND d7_roi IS NOT NULL
              AND spend >= {min_spend}
              AND installs >= {min_installs}"""
    ).collect()[0]
    if not r["n"]:
        return FAIL, (f"过滤后没有样本（min_spend={min_spend}, min_installs={min_installs}），"
                      f"门槛定得比数据还高")
    lo_v, hi_v = float(r["lo"]), float(r["hi"])
    lo, hi = rule["min"], rule["max"]
    ok = lo <= lo_v and hi_v <= hi
    return (PASS if ok else FAIL), f"实测 {lo_v:.4f}~{hi_v:.4f}（{r['n']:,} 行，要求 {lo}~{hi}）"


def check_null_only_when(spark, table, rule, dt) -> Tuple[str, str]:
    """断言「空值只出现在允许为空的条件里」。

    为什么这条比「关键列不许为空」更重要：
        D7 类指标的空值有两种来源，含义完全相反 ——
          ① 观察窗口没走完 → 真不知道 → 空是对的
          ② 计算链断了（join 丢了、除零、字段没赋值）→ 空是 bug
        两者在报表里长得一模一样。如果只在「回传 LLM」那一层把 NaN 洗成
        null，等于把 ② 也一起洗白了；反过来要求「不许为空」，又会把
        ① 逼成 0，那就是把「不知道」当成「很差」—— 会直接导致砍错素材。

        唯一正确的做法是钉住 ① 的边界：空值必须伴随它的成因。
    """
    col = rule["column"]
    cond = rule["condition"]
    bad = spark.sql(
        f"SELECT count(*) c FROM {_q(table)} WHERE `{col}` IS NULL AND NOT ({cond})"
    ).collect()[0]["c"]
    if bad:
        detail = spark.sql(
            f"""SELECT min(install_date) a, max(install_date) b,
                       count(*) c FROM {_q(table)}
                WHERE `{col}` IS NULL AND NOT ({cond})"""
        ).collect()[0]
        return FAIL, (f"{bad:,} 行不该为空却为空（install_date "
                      f"{detail['a']}~{detail['b']}）—— 这不是数据不足，是计算断了")
    total_null = spark.sql(
        f"SELECT count(*) c FROM {_q(table)} WHERE `{col}` IS NULL"
    ).collect()[0]["c"]
    return PASS, f"{col} 的空值全部符合「{cond}」（共 {total_null:,} 行为空）"


def check_sum_consistency_across_tables(spark, table, rule, dt) -> Tuple[str, str]:
    metric = rule["metric"]
    tol = rule.get("tolerance", 0.001)
    sums = {}
    for t in rule["tables"]:
        v = spark.sql(f"SELECT sum({metric}) s FROM {_q(t)}").collect()[0]["s"]
        sums[t] = float(v or 0)
    base = sums[rule["tables"][0]]
    worst_t, worst_gap = None, 0.0
    for t, v in sums.items():
        gap = abs(v - base) / base if base else abs(v)
        if gap > worst_gap:
            worst_t, worst_gap = t, gap
    if worst_gap > tol:
        detail = " / ".join(f"{t}={v:,.2f}" for t, v in sums.items())
        return FAIL, f"{worst_t} 偏差 {worst_gap:.4%} > {tol:.2%}；{detail}"
    return PASS, f"三表 {metric} 对齐（最大偏差 {worst_gap:.4%}）"


def check_group_count_band(spark, table, rule, dt) -> Tuple[str, str]:
    col = rule["column"]
    n = spark.sql(f"SELECT count(DISTINCT {col}) c FROM {_q(table)}").collect()[0]["c"]
    lo, hi = rule["min"], rule["max"]
    ok = lo <= n <= hi
    return (PASS if ok else FAIL), f"{col} 有 {n} 个（要求 {lo}~{hi}）"


def check_group_count_equal(spark, table, rule, dt) -> Tuple[str, str]:
    col = rule["column"]
    n = spark.sql(f"SELECT count(DISTINCT {col}) c FROM {_q(table)}").collect()[0]["c"]
    want = rule["value"]
    ok = n == want
    return (PASS if ok else FAIL), f"{col} 有 {n} 个（要求 {want}）"


# ============================================================
# 日志链路专用断言
# ============================================================
# 指标表的断言在问「这个数合不合理」（ROI 该在 0.2~2.0 之间）。
# 日志表不能这么问 —— verdict 凭什么是 cut 而不是 hold，没有业务区间可言。
# 它错的方式只有两种：**丢行** 和 **改内容**。
# 所以这一组断言全部围绕「和源对得上」，而源是一个 SQLite 文件。
# ============================================================


def _open_sqlite(rel_path: str, table: str, columns: List[str],
                 date_col: str | None = None, upper=None, exact=None):
    """以只读方式从 SQLite 取数（对账用）。

    用 `?mode=ro` 是有意的：门禁是校验方，在结构上就不该有写权限。
    用可写连接去校验，等于把裁判和运动员放在同一侧 ——
    平时没差别，出事时你就是说不清。
    """
    import sqlite3  # noqa: PLC0415  只有日志断言需要，模块级导入没必要

    path = PROJECT_ROOT / rel_path
    if not path.exists():
        raise FileNotFoundError(f"对账源不存在: {path}（agent 还没跑过？）")

    sql = f"SELECT {', '.join(columns)} FROM {table}"
    conds = []
    if date_col and upper is not None:
        # 只对账到数仓已有的最后一天。源里更新的日期交给 watermark_lag_days 管，
        # 两者分工明确：这里查「已同步的部分有没有抄错」，那里查「同步跟没跟上」。
        conds.append(f"{date_col} <= '{upper}'")
    if date_col and exact is not None:
        conds.append(f"{date_col} = '{exact}'")
    if conds:
        sql += " WHERE " + " AND ".join(conds)

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _norm(v):
    """统一数值形态，让 Hive 的 double 和 SQLite 的 REAL 能直接比。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    try:
        return round(float(v), 9)
    except (TypeError, ValueError):
        return str(v)


def check_source_parity(spark, table, rule, dt) -> Tuple[str, str]:
    """数仓 vs SQLite 逐行对账。

    这是「跨源事务」的替代品。
    严格的原子性做不到 —— SQLite 提交成功、Hive 写入失败，这个窗口永远存在。
    但「不一致」可以做到**立刻被发现**：
    只要门禁挂在同步任务后面，「复制了一半」就出不了这条流水线。
    """
    date_col = rule["date_col"]
    keys = rule["keys"]
    cols = rule["columns"]
    sqlite_rel, sqlite_tab = rule["sqlite"], rule["table"]

    hive_max = spark.sql(f"SELECT max({date_col}) m FROM {_q(table)}").collect()[0]["m"]
    if hive_max is None:
        return FAIL, "数仓表为空，无从对账"

    where_dt = f" AND {date_col} = '{dt}'" if dt else ""
    select = ", ".join(keys + cols)
    hive_rows = spark.sql(
        f"SELECT {select} FROM {_q(table)} "
        f"WHERE {date_col} <= '{hive_max}'{where_dt}"
    ).collect()
    src_rows = _open_sqlite(sqlite_rel, sqlite_tab, keys + cols, date_col,
                            upper=str(hive_max), exact=dt)

    nk = len(keys)

    def to_map(rows):
        return {tuple(_norm(r[i]) for i in range(nk)): tuple(_norm(r[i]) for i in range(nk, nk + len(cols)))
                for r in rows}

    hive_map, src_map = to_map(hive_rows), to_map(src_rows)
    if not src_map:
        return FAIL, f"源在 {date_col} <= {hive_max} 范围内没有数据，但对账不该在这里空转"

    missing = [k for k in src_map if k not in hive_map]
    extra = [k for k in hive_map if k not in src_map]
    diff = [k for k in src_map if k in hive_map and src_map[k] != hive_map[k]]

    total = len(src_map)
    if missing or extra or diff:
        parts = []
        if missing:
            parts.append(f"数仓缺 {len(missing)} 行（如 {missing[:2]}）")
        if extra:
            parts.append(f"数仓多 {len(extra)} 行（如 {extra[:2]}）")
        if diff:
            k = diff[0]
            parts.append(f"{len(diff)} 行内容不一致（如 {k}: 源{src_map[k]} vs 仓{hive_map[k]}）")
        return FAIL, "；".join(parts) + " —— 两源已经分叉"
    return PASS, f"{total:,} 行与 SQLite 逐行一致（{cols}），截至 {date_col}={hive_max}"


def check_watermark_lag_days(spark, table, rule, dt) -> Tuple[str, str]:
    """数仓的日志比源落后几天。

    为什么要单独一条：source_parity 只比对「已同步的部分」，
    如果同步任务整体没跑，两边在旧范围上照样完全一致 —— 对账会过，
    但中台读到的是三天前的结论，而且它不知道自己读到的是旧的。
    滞后天数就是专门抓这种「一致地过期」的。
    """
    date_col = rule["date_col"]
    hive_max = spark.sql(f"SELECT max({date_col}) m FROM {_q(table)}").collect()[0]["m"]
    rows = _open_sqlite(rule["sqlite"], rule["table"], [f"max({date_col})"])
    src_max = rows[0][0] if rows else None
    if hive_max is None or src_max is None:
        return FAIL, f"取不到水位（仓={hive_max}, 源={src_max}）"

    from datetime import date  # noqa: PLC0415

    def _d(v):
        return date.fromisoformat(str(v)[:10])

    lag = (_d(hive_max) - _d(src_max)).days
    limit = int(rule.get("max", 1))
    if lag < 0:
        return FAIL, f"数仓({hive_max}) 比源({src_max}) 还新 —— 源被回滚过？这是对账方向反了"
    ok = lag <= limit
    return (PASS if ok else FAIL), f"仓 {hive_max} / 源 {src_max}，落后 {lag} 天（要求 ≤ {limit}）"


def check_row_count_between(spark, table, rule, dt) -> Tuple[str, str]:
    """全表行数区间。用于没有 dt 分区的日志表。"""
    part = rule.get("partition_col")
    where = f"WHERE {part} = '{dt}'" if (dt and part) else ""
    n = spark.sql(f"SELECT count(*) c FROM {_q(table)} {where}").collect()[0]["c"]
    lo, hi = rule["min"], rule["max"]
    ok = lo <= n <= hi
    return (PASS if ok else FAIL), f"{n:,} 行（要求 {lo:,}~{hi:,}）"


def check_verdict_in_enum(spark, table, rule, dt) -> Tuple[str, str]:
    """结论必须落在枚举里。

    这条看着琐碎，但它抓的是最难查的一类 bug：
    verdict 写成 'cutt' 不会抛任何异常，只会让所有 `if verdict == 'cut'`
    的分支悄悄走到 else —— 那批该关停的素材就再也不会被关停。
    """
    col = rule["column"]
    allowed = rule["allowed"]
    in_list = ", ".join(f"'{a}'" for a in allowed)
    bad = spark.sql(
        f"SELECT {col} v, count(*) c FROM {_q(table)} "
        f"WHERE {col} IS NOT NULL AND {col} NOT IN ({in_list}) "
        f"GROUP BY {col} ORDER BY c DESC"
    ).collect()
    if bad:
        detail = ", ".join(f"{r['v']}({r['c']})" for r in bad[:5])
        return FAIL, f"出现枚举外的结论：{detail}"
    return PASS, f"{col} 全部落在 {len(allowed)} 个合法取值内"


def check_latest_covers_all(spark, table, rule, dt) -> Tuple[str, str]:
    """ADS 的「最新结论」必须覆盖 ODS 里的每个对象。

    少一个对象不会有任何报错 —— 那个对象下次判定时「上次结论」为空，
    于是永远走「首次判定」分支，滞回对它静默失效。
    这类静默失效正是质量门禁最该管的东西。
    """
    keys = rule["keys"]
    src = _q(rule["source_table"])
    on = " AND ".join(f"s.{k} = t.{k}" for k in keys)
    n_miss = spark.sql(f"""
        SELECT count(*) c FROM (
            SELECT {', '.join(keys)} FROM {src} GROUP BY {', '.join(keys)}
        ) s LEFT ANTI JOIN {_q(table)} t ON {on}
    """).collect()[0]["c"]
    n_src = spark.sql(
        f"SELECT count(*) c FROM (SELECT {', '.join(keys)} FROM {src} GROUP BY {', '.join(keys)})"
    ).collect()[0]["c"]
    if n_miss:
        return FAIL, f"{n_miss} 个对象在 {table} 里缺失（源共 {n_src:,} 个）—— 滞回对它们静默失效"
    return PASS, f"{n_src:,} 个对象全部覆盖"


def check_sum_equals_table_count(spark, table, rule, dt) -> Tuple[str, str]:
    """分布表的分组计数合计 = 源表行数。

    不等说明有行没进任何分组 —— 最常见的原因是分组列出现 NULL，
    而 NULL 在 GROUP BY 里自成一组的性质经常被人想当然地记错。
    """
    col = rule["column"]
    got = spark.sql(f"SELECT sum({col}) s FROM {_q(table)}").collect()[0]["s"] or 0
    want = spark.sql(f"SELECT count(*) c FROM {_q(rule['source_table'])}").collect()[0]["c"]
    ok = int(got) == int(want)
    return (PASS if ok else FAIL), f"{table}.{col} 合计 {int(got):,} vs 源 {want:,} 行"


DISPATCH: Dict[str, Callable] = {
    "partition_count_between": check_partition_count_between,
    "rows_per_partition_between": check_rows_per_partition_between,
    "rows_per_partition_equal": check_rows_per_partition_equal,
    "no_duplicate_grain": check_no_duplicate_grain,
    "duplicate_rate_between": check_duplicate_rate_between,
    "rows_not_greater_than_ods": check_rows_not_greater_than_ods,
    "d7_retention_between": check_d7_retention_between,
    "row_count_equal": check_row_count_equal,
    "column_count_equal": check_column_count_equal,
    "non_null": check_non_null,
    "null_only_when": check_null_only_when,
    "d7_roi_between": check_d7_roi_between,
    "sum_consistency_across_tables": check_sum_consistency_across_tables,
    "group_count_band": check_group_count_band,
    "group_count_equal": check_group_count_equal,
    # ---- 日志链路 ----
    "row_count_between": check_row_count_between,
    "verdict_in_enum": check_verdict_in_enum,
    "source_parity": check_source_parity,
    "watermark_lag_days": check_watermark_lag_days,
    "latest_covers_all": check_latest_covers_all,
    "sum_equals_table_count": check_sum_equals_table_count,
}


def run_table(spark, table: str, rule: Dict[str, Any], dt: str | None) -> Tuple[int, int]:
    """跑一张表的全部断言。返回 (失败数, 总数)。"""
    print(f"\n  [{table}] 粒度：{rule.get('grain', '-')}")
    fails = 0
    assertions = rule.get("assertions", [])
    for a in assertions:
        check = a["check"]
        fn = DISPATCH.get(check)
        if fn is None:
            print(f"    {SKIP}  {check:32s} 未知断言类型（metrics.yaml 写错了？）")
            continue
        try:
            status, msg = fn(spark, table, a, dt)
        except Exception as e:  # noqa: BLE001
            status, msg = FAIL, f"{type(e).__name__}: {e}"
        if status == FAIL:
            fails += 1
        print(f"    {status}  {check:32s} {msg}")
        if status == FAIL and a.get("note"):
            print(f"          ↳ 这类故障的含义：{a['note']}")
    return fails, len(assertions)


def main() -> int:
    ap = argparse.ArgumentParser(description="IAA 数仓质量门禁")
    ap.add_argument("--scope", default="all", choices=sorted(SCOPES.keys()))
    ap.add_argument("--dt", help="只校验某一天的分区（分区级断言用）")
    ap.add_argument("--list", action="store_true", help="只打印规则，不连 Spark")
    args = ap.parse_args()

    try:
        rules = load_rules()
    except Exception as e:  # noqa: BLE001
        print(f"[环境错误] 读不到质量规则: {e}")
        return 2

    tables = SCOPES[args.scope]
    print(f"质量门禁 scope={args.scope} dt={args.dt or '全局'}")
    print(f"规则来源：{METRICS_YAML}")

    if args.list:
        for t in tables:
            r = rules.get(t)
            if not r:
                print(f"  {t}: 无规则")
                continue
            print(f"  {t}（{r.get('grain', '-')}）")
            for a in r.get("assertions", []):
                print(f"    - {a['check']}")
        return 0

    # Spark 延迟导入：--list 不需要它，装了没配好也能看规则
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from gen_iaa_users import build_session  # noqa: PLC0415

    spark = build_session()
    total_fail = 0
    total = 0
    try:
        for t in tables:
            rule = rules.get(t)
            if not rule:
                print(f"\n  [{t}] 无质量规则，跳过")
                continue
            f, n = run_table(spark, t, rule, args.dt)
            total_fail += f
            total += n
    finally:
        spark.stop()

    print(f"\n{'=' * 62}")
    if total_fail:
        print(f"[门禁失败] {total_fail} / {total} 条断言未通过 —— 不要放行下游")
        return 1
    print(f"[门禁通过] {total} 条断言全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
