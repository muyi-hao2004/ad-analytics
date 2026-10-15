"""
IAA 广告变现分析中台 —— 四层数仓（ODS → DWD → DWS → ADS）

每一层只做一件事，数据从"脏原始"逐步变成"干净、聚合、面向业务"：

  ODS（贴源层）：只搬不洗，原始数据原样落地，可回溯
  DWD（明细层）：真清洗 + 标准化，得到干净的双轴事实表
  DWS（汇总层）：聚合 + pivot，长表转宽表（cohort 口径 d0~dn）
  ADS（应用层）：算派生指标，拼成日常表，供中台 + LLM

存储用 Parquet（列式存储，数仓标准格式）。
运行：
    cd iaa_bi
    venv/Scripts/python.exe src/warehouse.py
"""

from __future__ import annotations

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


def _save_parquet(df: pd.DataFrame, path: Path) -> None:
    """存成 Parquet（列式存储）。"""
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

    # 没值的补 0（某 cohort 某天没数据，表示该度量确实是 0）
    for col in result.columns:
        if col.startswith("d"):
            result[col] = result[col].fillna(0)

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
        cum_rev = sum(df[f"drev{i}"] for i in range(n + 1))
        df[f"roi_{n}"] = cum_rev / df["spend_total"].replace(0, float("nan"))

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

    为什么需要这张表（与 DWS_INDEX_COLS 的日常表区分）：
      - build_ads 产出的 daily_report 按"系列"聚合，素材/子渠道被拍平，
        无法在报表里按素材/子渠道筛选、下钻。
      - 这张细粒度表保留了 creative_id + sub_channel，配合策略(strategy)，
        支持报表的全部筛选按钮 + 下钻。

    派生指标只算关键的（D0/D1/D7 留存率 + D7 ROI + CPI + 花费 + 安装），
    避免表格过宽；更细的 D0~D7 全量在系列级 daily_report 里看。

    Args:
        dwd: DWD 层干净的双轴事实表

    Returns:
        细粒度日常表（install_date × 全维度 × 关键指标 + strategy）
    """
    df = dwd.copy()

    # ---- 步骤1：算 day_offset ----
    df["install_date"] = pd.to_datetime(df["install_date"])
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["day_offset"] = (df["report_date"] - df["install_date"]).dt.days
    df["install_date"] = df["install_date"].dt.strftime("%Y-%m-%d")

    # ---- 步骤2：按细粒度维度聚合 ----
    group_cols = DETAIL_INDEX_COLS

    # 安装数（install_date 当天，day_offset=0 的 installs 就是总安装）
    installs = df[df["day_offset"] == 0].groupby(
        group_cols, as_index=False)["installs"].sum()

    # 花费（买量一次性投入，day0 有值；这里取该 cohort 的 spend 总和）
    spend = df.groupby(group_cols, as_index=False)["spend"].sum()

    # D1 活跃（第 1 天留存分子）
    d1_active = df[df["day_offset"] == 1].groupby(
        group_cols, as_index=False)["active_users"].sum()
    d1_active = d1_active.rename(columns={"active_users": "d1_active"})

    # D7 活跃（第 7 天留存分子）
    d7_active = df[df["day_offset"] == 7].groupby(
        group_cols, as_index=False)["active_users"].sum()
    d7_active = d7_active.rename(columns={"active_users": "d7_active"})

    # D0~D7 累计收入（用于 D7 ROI）
    df7 = df[df["day_offset"] <= 7]
    cum_rev = df7.groupby(group_cols, as_index=False)["ad_revenue"].sum()
    cum_rev = cum_rev.rename(columns={"ad_revenue": "cum_revenue"})

    # ---- 步骤3：merge 成一张表 ----
    result = installs
    for part in [spend, d1_active, d7_active, cum_rev]:
        result = result.merge(part, on=group_cols, how="left")

    # ---- 步骤4：算派生指标 ----
    result["d1_retention"] = result["d1_active"] / result["installs"].replace(0, float("nan"))
    result["d7_retention"] = result["d7_active"] / result["installs"].replace(0, float("nan"))
    result["d7_roi"] = result["cum_revenue"] / result["spend"].replace(0, float("nan"))
    result["cpi"] = result["spend"] / result["installs"].replace(0, float("nan"))

    # ---- 步骤5：join 策略（从维度表 dim_campaign 取 strategy）----
    result = _join_strategy(result)

    # 落地 ADS
    out = config.ADS_DIR / "daily_detail.parquet"
    _save_parquet(result, out)
    print(f"[ADS] 已生成细粒度日常表: {out.name}  ({len(result)} 行, "
          f"粒度=日期×包×国家×系列×素材×子渠道 + strategy)")
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
