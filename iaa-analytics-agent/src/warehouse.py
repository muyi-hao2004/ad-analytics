"""
IAA 广告变现分析中台 —— 四层数仓（ODS → DWD → DWS → ADS）

每一层只做一件事，数据从"脏原始"逐步变成"干净、聚合、面向业务"：

  ODS（贴源层）：只搬不洗，原始数据原样落地，可回溯
  DWD（明细层）：真清洗 + 标准化，得到干净的双轴事实表
  DWS（汇总层）：聚合 + pivot，长表转宽表（cohort 口径 d0~dn）
  ADS（应用层）：算派生指标，拼成日常表，供中台 + LLM

存储用 Parquet（列式存储，数仓标准格式）。
运行：
    cd iaa-analytics-agent
    venv/Scripts/python.exe src/warehouse.py
"""

from __future__ import annotations

import os

import pandas as pd
from pathlib import Path
from typing import List, Optional

import config


# ============================================================
# 通用工具
# ============================================================

def _read_csv_any(path: Path) -> pd.DataFrame:
    """读取 CSV（兼容编码/分隔符的健壮读取）。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到数据文件: {path}")
    # 造数脚本统一用 utf-8，这里兜底 gbk 以防万一
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="gbk")


# Spark 五层管道（scripts/build_ads_iaa.py）在 ADS 目录留的标记文件。
# 存在即表示「这个目录当前是 Spark 产出的」，pandas 链路不许覆盖。
_ADS_MARKER = config.ADS_DIR / "_pipeline.json"


def _guard_ads_overwrite(path: Path) -> None:
    """阻止 pandas 四层覆盖 Spark 五层的 ADS 产物。

    为什么需要这道护栏
    ------------------
    仓库里有两代数仓实现，写的是**同一个** ADS 目录：

      - `src/warehouse.py`（pandas 四层）：ADS 的 daily_detail 约 60 列
      - `scripts/build_ads_iaa.py`（Spark 五层）：78 列，多了
        `is_mature_d7` / `d7_roi` / `d7_retention` 等中台必须的列

    混跑的后果不是报错，而是**静默降级**：pandas 版把文件覆盖掉之后，
    `agent.py` 下次查询才会在某个列上 KeyError，而报错位置离真正的原因
    （一次误执行的 `tasks.py init`）隔了几百行代码，极难定位。

    所以这里 fail-fast。确实要重建 pandas 版（例如没装 Spark 想先看结构），
    两个办法任选：删掉标记文件，或设 `ALLOW_PANDAS_OVERWRITE=1`。
    """
    try:
        same_dir = path.resolve().parent == config.ADS_DIR.resolve()
    except OSError:
        return
    if not same_dir or not _ADS_MARKER.exists():
        return
    if os.getenv("ALLOW_PANDAS_OVERWRITE") == "1":
        return
    raise RuntimeError(
        f"拒绝覆盖 {path}\n"
        f"原因：ADS 目录当前由 Spark 五层管道产出（见 {_ADS_MARKER}），"
        "pandas 四层的列更少，覆盖后中台会缺列。\n"
        "如果确实要重建 pandas 版：\n"
        f"  ① rm {_ADS_MARKER}，或\n"
        "  ② 设置环境变量 ALLOW_PANDAS_OVERWRITE=1 后重跑。\n"
        "只想验证 Spark 版数据：别跑 tasks.py init，直接 python tasks.py test。"
    )


def _save_parquet(df: pd.DataFrame, path: Path) -> None:
    """存成 Parquet（列式存储）。"""
    _guard_ads_overwrite(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _load_parquet(path: Path) -> pd.DataFrame:
    """读 Parquet。"""
    return pd.read_parquet(path)


# ============================================================
# ① ODS 层（贴源层）：只搬不洗
# ============================================================

def build_ods(raw_file: Optional[Path] = None) -> pd.DataFrame:
    """
    把原始结果数据搬到 ODS，原样落地，不做任何清洗。

    为什么"只搬不洗"：
      ODS 是"事故现场"，保留最原始的样子，出问题能回溯；
      清洗放在 DWD，这样清洗规则改了，能从 ODS 重跑，不用重新采集。

    Args:
        raw_file: 原始 CSV 路径；不传则默认 data/raw/ 下第一个 CSV

    Returns:
        贴源数据 DataFrame（与原始完全一致）
    """
    if raw_file is None:
        csvs = sorted(config.RAW_DIR.glob("*.csv"))
        if not csvs:
            raise FileNotFoundError(
                f"{config.RAW_DIR} 下没有原始数据。请先运行造数脚本。")
        raw_file = csvs[0]

    raw = _read_csv_any(raw_file)

    # 原样落地到 ODS（什么都不改，包括脏数据）
    out = config.ODS_DIR / f"{raw_file.stem}.parquet"
    _save_parquet(raw, out)
    print(f"[ODS] 已贴源: {out.name}  ({len(raw)} 行, 原样保留)")
    return raw


# ============================================================
# ② DWD 层（明细层）：真清洗 + 标准化
# ============================================================

def build_dwd(raw: pd.DataFrame) -> pd.DataFrame:
    """
    把 ODS 的脏数据清洗成干净的双轴事实表。

    清洗规则（与数据字典对应）：
      1. 去重：完全相同的行只留一条（同一数据被重复推送）
      2. 补空：度量字段 NaN 补 0，维度字段 NaN 填 "unknown"
      3. 修负值：度量字段负值 clip 到 0（收入/展示/花费不可能为负）
      4. 统一时区：日期字符串统一解析（保留日期，去掉时区歧义）
      5. 类型规范：日期转 datetime，度量转数值

    Args:
        raw: ODS 层数据

    Returns:
        清洗后的双轴事实表（干净明细）
    """
    df = raw.copy()

    n_before = len(df)

    # ---- 规则1：去重 ----
    df = df.drop_duplicates()
    n_dup = n_before - len(df)
    if n_dup:
        print(f"[DWD] 去重: 删除 {n_dup} 行完全重复数据")

    # ---- 规则2：补空 ----
    # 度量字段：NaN → 0（没有就是 0，而不是缺失）
    # 只处理事实表的度量字段；买量字段（clicks/buy_impressions）走 build_acquisition
    for col in config.MEASURE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    # 维度字段：NaN → "unknown"（表示未知，不丢整行）
    for col in config.DIMENSION_COLUMNS:
        if col in df.columns:
            df[col] = df[col].fillna("unknown").astype(str)

    # ---- 规则3：修负值 ----
    for col in config.MEASURE_COLUMNS:
        if col in df.columns:
            df[col] = df[col].clip(lower=0)

    # ---- 规则4：日期标准化 ----
    # 双轴日期统一转成标准日期字符串（YYYY-MM-DD），消除时区/格式差异
    # format="mixed" 让 pandas 自动识别多种格式（纯日期 / 带时间 / 带时区）
    for col in config.DATE_AXIS:
        if col in df.columns:
            # 先去掉时区信息（统一按无时区处理，业务上按"目标市场日"口径）
            s = df[col].astype(str).str.replace(r"\s*[+-]\d{2}:\d{2}$", "", regex=True)
            parsed = pd.to_datetime(s, format="mixed", errors="coerce")
            df[col] = parsed.dt.strftime("%Y-%m-%d")
            # 解析失败的日期，填一个明确的占位（而不是 NaN），便于发现数据问题
            df[col] = df[col].fillna("1970-01-01")

    # ---- 规则5：类型规范 ----
    # 度量字段转 int/float（整数型度量用 int，金额类用 float）
    int_measures = ["installs", "active_users", "ad_impressions", "rewarded_views"]
    float_measures = ["ad_revenue", "spend"]
    for col in int_measures:
        if col in df.columns:
            df[col] = df[col].astype(int)
    for col in float_measures:
        if col in df.columns:
            df[col] = df[col].astype(float)

    # 落地 DWD
    out = config.DWD_DIR / "cohort_daily_fact.parquet"
    _save_parquet(df, out)
    print(f"[DWD] 已清洗: {out.name}  ({len(df)} 行, 干净双轴事实表)")
    return df


# ============================================================
# ②.5 买量日表（前端，单轴）：与双轴事实表分开，时间轴不同
# ============================================================

def build_acquisition(raw: pd.DataFrame) -> pd.DataFrame:
    """
    清洗并落地「买量日表」（前端数据，单轴）。

    为什么单独建表：
      买量（spend/clicks/buy_impressions/installs）是"投放当天的动作"，
      时间轴只有 report_date（单轴），没有 cohort 概念。
      而双轴事实表是"某批安装用户第 N 天的表现"（双轴）。
      两者时间轴不同，不能混在一张表，只在查询时用 creative_id/sub_channel 等 join。

    Args:
        raw: 原始数据（含买量侧字段）

    Returns:
        买量日表（report_date + 维度 + 买量度量）
    """
    df = raw.copy()

    # 买量表字段：report_date + 维度 + 买量度量
    acq_cols = config.ACQUISITION_COLUMNS + ["installs", "spend"]
    keep_cols = ["report_date"] + config.DIMENSION_COLUMNS + acq_cols
    # 只保留买量表需要的列（如果原始数据里有）
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].copy()

    # 清洗（与 DWD 同规则，但只针对买量字段）
    df = df.drop_duplicates()
    for col in acq_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).clip(lower=0)
    for col in config.DIMENSION_COLUMNS:
        if col in df.columns:
            df[col] = df[col].fillna("unknown").astype(str)
    # 日期标准化（单轴）
    if "report_date" in df.columns:
        s = df["report_date"].astype(str).str.replace(r"\s*[+-]\d{2}:\d{2}$", "", regex=True)
        df["report_date"] = pd.to_datetime(s, format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")

    # 落地买量日表
    out = config.DWD_DIR / "acquisition_daily.parquet"
    _save_parquet(df, out)
    print(f"[DWD] 已清洗买量日表: {out.name}  ({len(df)} 行, 单轴 report_date)")
    return df


# ============================================================
# ③ DWS 层（汇总层）：聚合 + pivot 宽表
# ============================================================

# 日常表的聚合维度（pivot 的 index，即"一行代表谁"）
# 更细的维度（sub_channel / creative_id）在素材表/子渠道表单独处理
DWS_INDEX_COLS = ["install_date", "app", "country", "campaign"]

# 要横铺成 d0~dn 的原子度量
# 注意：只 pivot 原子值，派生指标（留存率/eCPM/ROI）留到 ADS 现算
DWS_PIVOT_MEASURES = {
    "active_users":     "act",   # 留存（分子）
    "ad_revenue":       "rev",   # 收入
    "rewarded_views":   "view",  # 观次
    "ad_impressions":   "imp",   # 展示
}


def _pivot_measure(df_long: pd.DataFrame, measure: str, tag: str) -> pd.DataFrame:
    """
    把一个度量横铺成宽表（d0~dn 列）。

    三要素：
      index（行）= DWS_INDEX_COLS（install_date+app+country+campaign）
      columns（列）= day_offset（0,1,2... → d0,d1,d2...）
      values（值）= measure

    Args:
        df_long: 已经带 day_offset 字段的长表
        measure: 要横铺的度量名（如 "active_users"）
        tag: 列名前缀（如 "act"），避免多个度量列名冲突

    Returns:
        该度量的宽表（index 列 + d{tag}0, d{tag}1, ... 列）
    """
    df_long = df_long.copy()
    df_long["d"] = "d" + df_long["day_offset"].astype(str)

    wide = df_long.pivot_table(
        index=DWS_INDEX_COLS,
        columns="d",
        values=measure,
        aggfunc="sum",          # 同 cohort 同天可能有多行，求和
    ).reset_index()

    # 列名加 tag 前缀，并排序（d0, d1, d2... 而不是 d10, d1, d2 的字典序）
    rename = {}
    for col in wide.columns:
        if col not in DWS_INDEX_COLS:
            rename[col] = f"d{tag}{col[1:]}"   # "d0" -> "dact0"
    wide = wide.rename(columns=rename)

    # 把 d 开头的列按数字排序
    dim_cols = [c for c in DWS_INDEX_COLS if c in wide.columns]
    d_cols = sorted(
        [c for c in wide.columns if c.startswith("d")],
        key=lambda x: int(x.replace("d", "").replace(tag, "", 1) or 0)
        if x.replace("d", "").replace(tag, "", 1).isdigit() else -1
    )
    return wide[dim_cols + d_cols]


def build_dws(dwd: pd.DataFrame) -> pd.DataFrame:
    """
    把 DWD 的长表，pivot 成 cohort 宽表（DWS）。

    步骤：
      1. 算 day_offset = report_date - install_date（第几天）
      2. 按 DWS_INDEX_COLS 聚合（同 cohort 同天的多维度行求和）
      3. 对每个度量分别 pivot 成宽表
      4. merge 成一张大宽表

    Args:
        dwd: DWD 层干净的双轴事实表

    Returns:
        cohort 宽表（一行一个 install_date×维度，列是 d0~dn 的各度量）
    """
    df = dwd.copy()

    # ---- 步骤1：算 day_offset ----
    df["install_date"] = pd.to_datetime(df["install_date"])
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["day_offset"] = (df["report_date"] - df["install_date"]).dt.days
    # 转回字符串，保持全链路日期类型一致（与 DWD 一致，都是 YYYY-MM-DD 字符串）
    df["install_date"] = df["install_date"].dt.strftime("%Y-%m-%d")

    # ---- 步骤2：按 index 维度聚合 ----
    # 每个 cohort(install_date) 的每个维度组合，在每天的度量求和
    group_cols = DWS_INDEX_COLS + ["day_offset"]
    agg_dict = {m: "sum" for m in DWS_PIVOT_MEASURES}
    df_agg = df.groupby(group_cols, as_index=False).agg(agg_dict)

    # ---- 步骤3：逐个度量 pivot ----
    wide_parts = []
    for measure, tag in DWS_PIVOT_MEASURES.items():
        wide_parts.append(_pivot_measure(df_agg, measure, tag))

    # ---- 步骤4：merge 成一张大宽表 ----
    result = wide_parts[0]
    for part in wide_parts[1:]:
        result = result.merge(part, on=DWS_INDEX_COLS, how="outer")

    # 没值的保留 NaN（表示"未来未发生"，不是 0）
    # 关键：不能用 fillna(0)，否则会把"左上三角的空缺"填成 0，
    # 后续算 ROI 累计、留存率时会把"未发生的天"误当成"收入为 0 的天"。
    # NaN 会在 ADS 层算派生指标时自然传播为 NaN（= 未发生 / 不可得）。

    # 落地 DWS
    out = config.DWS_DIR / "cohort_daily_wide.parquet"
    _save_parquet(result, out)
    print(f"[DWS] 已 pivot: {out.name}  ({len(result)} 行 cohort 宽表, "
          f"{sum(1 for c in result.columns if c.startswith('d'))} 个 d 列)")
    return result


# ============================================================
# ④ ADS 层（应用层）：派生指标 + 业务表
# ============================================================

def build_ads(dws: pd.DataFrame, dwd: pd.DataFrame) -> pd.DataFrame:
    """
    从 DWS 宽表算派生指标，拼成日常表（ADS 主表）。

    派生指标（只算不存，供中台 + LLM）：
      - Dn 留存率  = dact_n / installs
      - Dn eCPM   = drev_n / dimp_n * 1000
      - Dn 人均观次 = dview_n / dact_n
      - Dn ROI    = 累计 drev / 累计 spend（累计口径）

    Args:
        dws: DWS 层 cohort 宽表
        dwd: DWD 层明细（用于取 installs 和 spend 的累计）

    Returns:
        日常表（ADS）
    """
    df = dws.copy()

    # installs 是 cohort 分母，需要从 DWD 取（每个 install_date 的安装总数）
    # DWD 里 installs 在 install_date 当天（day_offset=0）就等于总安装数
    installs_df = dwd[dwd["install_date"] == dwd["report_date"]]
    installs_by_cohort = installs_df.groupby(
        DWS_INDEX_COLS, as_index=False)["installs"].sum()

    # 合并 installs 到宽表
    df = df.merge(installs_by_cohort, on=DWS_INDEX_COLS, how="left")
    df["installs"] = df["installs"].fillna(0)

    # 找出所有 day 数（dact0 里的 0 就是 day 数）
    days = sorted({int(c.replace("dact", "")) for c in df.columns if c.startswith("dact")})

    # ---- 算派生指标（新增列，命名规则：ret_n / ecpm_n / view_n）----
    for n in days:
        # Dn 留存率 = dact_n / installs
        df[f"ret_{n}"] = df[f"dact{n}"] / df["installs"].replace(0, float("nan"))
        # Dn eCPM = drev_n / dimp_n * 1000
        df[f"ecpm_{n}"] = df[f"drev{n}"] / df[f"dimp{n}"].replace(0, float("nan")) * 1000
        # Dn 人均观次 = dview_n / dact_n
        df[f"view_{n}"] = df[f"dview{n}"] / df[f"dact{n}"].replace(0, float("nan"))

    # ---- Dn ROI：累计收入 / 累计花费 ----
    # 累计收入 = drev0 + drev1 + ... + drevn
    # 累计花费 = 该 cohort 的 spend 总和（买量一次性投入，spend 只在 day0 有）
    spend_by_cohort = dwd.groupby(DWS_INDEX_COLS, as_index=False)["spend"].sum()
    df = df.merge(spend_by_cohort, on=DWS_INDEX_COLS, how="left")
    df["spend"] = df["spend"].fillna(0)
    df.rename(columns={"spend": "spend_total"}, inplace=True)

    for n in days:
        # 累计收入 = drev0..drevn 之和
        # 关键：drev_n 是 NaN 表示"第 n 天还未发生"，则 roi_n 也应是 NaN（累计不可得）
        # 用 cumsum 累加，NaN 会自然向后传播
        cum_rev = sum(df[f"drev{i}"].fillna(0) for i in range(n + 1))
        # 但若"当前天 n"是 NaN（未发生），roi_n 应为 NaN
        current_nan = df[f"drev{n}"].isna()
        df[f"roi_{n}"] = cum_rev / df["spend_total"].replace(0, float("nan"))
        df.loc[current_nan, f"roi_{n}"] = float("nan")

    # ---- CPI = spend_total / installs ----
    df["cpi"] = df["spend_total"] / df["installs"].replace(0, float("nan"))

    # 落地 ADS
    out = config.ADS_DIR / "daily_report.parquet"
    _save_parquet(df, out)
    print(f"[ADS] 已生成日常表: {out.name}  ({len(df)} 行, "
          f"{sum(1 for c in df.columns if c.startswith(('ret_','ecpm_','view_','roi_')))} 个派生指标列)")
    return df


# ============================================================
# ④.5 ADS 层：素材/子渠道前后端结合表
# ============================================================

# 后端简略指标只取 D7（决策临界点，对应 D7 ROAS 目标 70%）
D7 = 7


def _backend_brief(dwd: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """
    从双轴事实表算"后端简略指标"（按素材/子渠道 × install_date 聚合到 D7）。

    后端简略指标（判断"这批用户值不值"的关键）：
      - D7 留存率 = 第 7 天活跃 / 安装数
      - D7 累计收入 = d0..d7 的收入之和
      - D7 ROI = D7 累计收入 / 花费
      - D7 eCPM = D7 累计收入 / D7 累计展示 * 1000

    Args:
        dwd: DWD 双轴事实表
        group_col: 按哪个维度分组（"creative_id" 或 "sub_channel"）

    Returns:
        后端简略指标表（group_col + install_date + D7指标）
    """
    df = dwd.copy()
    df["install_date"] = pd.to_datetime(df["install_date"])
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["day_offset"] = (df["report_date"] - df["install_date"]).dt.days

    # 只取 day_offset <= D7 的数据（D7 累计）
    df7 = df[df["day_offset"] <= D7]

    # 转回字符串，保持 install_date 全链路类型一致
    df7["install_date"] = df7["install_date"].dt.strftime("%Y-%m-%d")

    # 按 group_col + install_date 聚合
    group_cols = [group_col, "install_date"]
    agg = df7.groupby(group_cols, as_index=False).agg(
        installs=("installs", "max"),        # 安装数（每天相同，取 max）
        spend=("spend", "sum"),              # 花费
        cum_revenue=("ad_revenue", "sum"),   # D7 累计收入
        cum_impressions=("ad_impressions", "sum"),  # D7 累计展示
        d7_active=("active_users", lambda s: s[df7.loc[s.index, "day_offset"] == D7].sum()
                   if (df7.loc[s.index, "day_offset"] == D7).any() else 0),
    )

    # 派生指标
    agg["d7_retention"] = agg["d7_active"] / agg["installs"].replace(0, float("nan"))
    agg["d7_roi"] = agg["cum_revenue"] / agg["spend"].replace(0, float("nan"))
    agg["d7_ecpm"] = agg["cum_revenue"] / agg["cum_impressions"].replace(0, float("nan")) * 1000

    return agg


def build_creative_table(acq: pd.DataFrame, dwd: pd.DataFrame) -> pd.DataFrame:
    """
    素材前后端结合表（一行 = 一个素材 × 一个安装日）。

    前端（买量日表）+ 后端（D7 简略指标），用 creative_id join。

    关键简化（demo）：
      买量表的 report_date 近似当作 install_date（IAA 点击即装，归因延迟短）。
      真实环境需要归因匹配，这里简化。

    Args:
        acq: 买量日表（build_acquisition 的产出）
        dwd: DWD 双轴事实表

    Returns:
        素材前后端表
    """
    # 前端：按 creative_id + report_date(≈install_date) 聚合买量指标
    acq = acq.copy()
    acq = acq.rename(columns={"report_date": "install_date"})  # demo 简化
    front = acq.groupby(["creative_id", "install_date"], as_index=False).agg(
        spend=("spend", "sum"),
        clicks=("clicks", "sum"),
        buy_impressions=("buy_impressions", "sum"),
        installs=("installs", "sum"),
    )
    # 前端派生指标
    front["ctr"] = front["clicks"] / front["buy_impressions"].replace(0, float("nan"))
    front["cvr"] = front["installs"] / front["clicks"].replace(0, float("nan"))
    front["cpi"] = front["spend"] / front["installs"].replace(0, float("nan"))

    # 后端：D7 简略指标
    back = _backend_brief(dwd, "creative_id")

    # join：creative_id + install_date
    result = front.merge(
        back[["creative_id", "install_date", "d7_retention", "d7_roi", "d7_ecpm"]],
        on=["creative_id", "install_date"], how="left"
    )

    out = config.ADS_DIR / "creative_table.parquet"
    _save_parquet(result, out)
    print(f"[ADS] 已生成素材前后端表: {out.name}  ({len(result)} 行)")
    return result


def build_sub_channel_table(acq: pd.DataFrame, dwd: pd.DataFrame) -> pd.DataFrame:
    """
    子渠道前后端结合表（一行 = 一个子渠道 × 一个安装日）。

    与素材表同理，只是分组键换成 sub_channel。
    """
    acq = acq.copy()
    acq = acq.rename(columns={"report_date": "install_date"})  # demo 简化
    front = acq.groupby(["sub_channel", "install_date"], as_index=False).agg(
        spend=("spend", "sum"),
        clicks=("clicks", "sum"),
        buy_impressions=("buy_impressions", "sum"),
        installs=("installs", "sum"),
    )
    front["ctr"] = front["clicks"] / front["buy_impressions"].replace(0, float("nan"))
    front["cvr"] = front["installs"] / front["clicks"].replace(0, float("nan"))
    front["cpi"] = front["spend"] / front["installs"].replace(0, float("nan"))

    back = _backend_brief(dwd, "sub_channel")

    result = front.merge(
        back[["sub_channel", "install_date", "d7_retention", "d7_roi", "d7_ecpm"]],
        on=["sub_channel", "install_date"], how="left"
    )

    out = config.ADS_DIR / "sub_channel_table.parquet"
    _save_parquet(result, out)
    print(f"[ADS] 已生成子渠道前后端表: {out.name}  ({len(result)} 行)")
    return result


# ============================================================
# ④.6 ADS 层：细粒度日常表（支持素材/子渠道下钻 + 策略筛选）
# ============================================================

# 细粒度日常表的聚合维度（比 DWS_INDEX_COLS 更细，多 creative_id 和 sub_channel）
# 这样报表才能"选系列后看该系列下的素材、按子渠道独立筛"
DETAIL_INDEX_COLS = DWS_INDEX_COLS + ["creative_id", "sub_channel"]


def build_daily_detail(dwd: pd.DataFrame) -> pd.DataFrame:
    """
    生成「细粒度日常表」，粒度到 日期×包×国家×系列×素材×子渠道。

    与 build_ads（系列级 daily_report）的区别：
      - daily_report 按"系列"聚合，素材/子渠道被拍平，只供系列级报表；
      - 这张细粒度表保留 creative_id + sub_channel，支持报表全维度筛选下钻。

    **本表是唯一事实源**：日常表（明细平铺）和 Cohort 矩阵（按天透视成左上三角）
    都读这张表，并用同一套筛选条件实时聚合——这样两者天然联动。

    派生指标全程 D0~D7（与系列级 daily_report 同构，只是分组粒度更细）：
      - Dn 留存率 = active_users(day=n) / installs
      - Dn ROI    = 累计收入(d0..dn) / spend
      - Dn eCPM   = 收入(day=n) / 展示(day=n) * 1000
      - Dn 人均观次 = 观次(day=n) / active_users(day=n)

    Args:
        dwd: DWD 层干净的双轴事实表

    Returns:
        细粒度日常表（install_date × 全维度 × D0~D7 全程指标 + strategy）
    """
    df = dwd.copy()

    # ---- 步骤1：算 day_offset ----
    df["install_date"] = pd.to_datetime(df["install_date"])
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["day_offset"] = (df["report_date"] - df["install_date"]).dt.days
    df["install_date"] = df["install_date"].dt.strftime("%Y-%m-%d")

    group_cols = DETAIL_INDEX_COLS

    # ---- 步骤2：按「细粒度维度 × day_offset」聚合原子度量 ----
    # 同 build_ads 的聚合逻辑，只是 group_cols 更细（多 creative_id/sub_channel）
    agg = df.groupby(group_cols + ["day_offset"], as_index=False).agg(
        active_users=("active_users", "sum"),      # 留存分子
        ad_revenue=("ad_revenue", "sum"),          # 收入
        rewarded_views=("rewarded_views", "sum"),  # 观次
        ad_impressions=("ad_impressions", "sum"),  # 展示
        installs=("installs", "max"),              # 安装数（每天相同，取 max）
        spend=("spend", "max"),                    # 花费（买量一次性投入，取 max）
    )

    # ---- 步骤3：pivot 成宽表（每个度量 × day_offset 横铺成 d0..dn）----
    # 4 个原子度量分别 pivot，再 merge
    pivot_specs = {
        "active_users":     "dact",
        "ad_revenue":       "drev",
        "rewarded_views":   "dview",
        "ad_impressions":   "dimp",
    }
    parts = []
    for measure, tag in pivot_specs.items():
        tmp = agg.copy()
        tmp["d"] = "d" + tmp["day_offset"].astype(str)
        wide = tmp.pivot_table(
            index=group_cols, columns="d", values=measure, aggfunc="sum"
        ).reset_index()
        rename = {c: f"{tag}{c[1:]}" for c in wide.columns if c not in group_cols}
        wide = wide.rename(columns=rename)
        parts.append(wide)

    result = parts[0]
    for part in parts[1:]:
        result = result.merge(part, on=group_cols, how="outer")

    # ---- 步骤4：取 installs / spend（不随 day 变，从 agg 里按维度 max 取）----
    fixed = df.groupby(group_cols, as_index=False).agg(
        installs=("installs", "max"),
        spend=("spend", "max"),
    )
    result = result.merge(fixed, on=group_cols, how="left")

    # ---- 步骤5：算派生指标（D0~Dn 全程，命名 ret_n/roi_n/ecpm_n/view_n）----
    # 找出所有 day 数（dact 前缀列的 0..n）
    day_cols = [c for c in result.columns if c.startswith("dact")]
    days = sorted(int(c.replace("dact", "")) for c in day_cols)

    for n in days:
        # Dn 留存率
        result[f"ret_{n}"] = result[f"dact{n}"] / result["installs"].replace(0, float("nan"))
        # Dn eCPM = 收入 / 展示 * 1000
        result[f"ecpm_{n}"] = (
            result[f"drev{n}"] / result[f"dimp{n}"].replace(0, float("nan")) * 1000)
        # Dn 人均观次 = 观次 / 活跃
        result[f"view_{n}"] = (
            result[f"dview{n}"] / result[f"dact{n}"].replace(0, float("nan")))
        # Dn ROI = 累计收入(d0..dn) / 花费
        cum_rev = sum(result[f"drev{i}"].fillna(0) for i in range(n + 1))
        result[f"roi_{n}"] = cum_rev / result["spend"].replace(0, float("nan"))
        # 当前天未发生 → roi_n 也应是 NaN（左上三角空缺，不当作 0）
        result.loc[result[f"drev{n}"].isna(), f"roi_{n}"] = float("nan")

    # ---- 步骤6：join 策略（从维度表 dim_campaign 取 strategy）----
    result = _join_strategy(result)

    # ---- 步骤7：只保留「明细平铺需要的列」+ 全程指标列 ----
    # 日常表展示用：install_date+维度+关键指标（D1/D7 + CPI 等）
    # Cohort 矩阵用：全程 ret_/roi_/ecpm_/view_ 列
    keep = group_cols + ["installs", "spend"]
    for n in days:
        keep += [f"ret_{n}", f"roi_{n}", f"ecpm_{n}", f"view_{n}"]
    keep += ["strategy"]
    # dact/drev/dview/dimp 原子列也保留（供前端如需重算，也便于对账）
    keep += [f"dact{n}" for n in days] + [f"drev{n}" for n in days] + \
            [f"dview{n}" for n in days] + [f"dimp{n}" for n in days]
    result = result[[c for c in keep if c in result.columns]]

    # 落地 ADS
    out = config.ADS_DIR / "daily_detail.parquet"
    _save_parquet(result, out)
    print(f"[ADS] 已生成细粒度日常表: {out.name}  ({len(result)} 行, "
          f"粒度=日期×包×国家×系列×素材×子渠道 × D0~D{days[-1] if days else 0} 全程指标)")
    return result


def _join_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """
    从维度表 dim_campaign 取 strategy，按 campaign_id join 到报表数据。

    这是"配置数据通道"和"结果数据通道"在查询时合流的一个例子：
      - 结果数据（事实）在数仓里
      - 配置数据（策略类型）在维度表里
      - 查询时用 campaign_id 关联起来
    """
    # 延迟导入，避免循环依赖（dim_scd 不依赖 warehouse，这里单向依赖）
    import dim_scd

    campaigns = dim_scd.list_all_current()
    strategy_map = {c["campaign_id"]: c["strategy"] for c in campaigns}

    df = df.copy()
    df["strategy"] = df["campaign"].map(strategy_map).fillna("unknown")
    return df


# ============================================================
# 运行入口（当前只演示 ODS + DWD，DWS/ADS 后续补充）
# ============================================================

if __name__ == "__main__":
    config.ensure_dirs()
    print("=" * 60)
    print("四层数仓：ODS → DWD（DWS/ADS 待补充）")
    print("=" * 60)

    # 需要有原始数据才能跑。没有就提示先造数。
    csvs = sorted(config.RAW_DIR.glob("*.csv"))
    if not csvs:
        print("⚠️  当前 data/raw/ 下没有原始数据。")
        print("    造数脚本还没做，先搭骨架。可以用 demo 数据试跑。")
        print("    或者先运行 src/demo_warehouse.py 看 ODS→DWD 的清洗效果。")
    else:
        raw = build_ods()
        dwd = build_dwd(raw)
        print("\n清洗后的双轴事实表预览：")
        print(dwd.head().to_string(index=False))
