"""
数仓分层编排模块（核心）
把广告投放数据加工流程组织成标准的数仓四层架构：

    ODS(贴源层) → DWD(明细层) → DWS(汇总层) → ADS(应用层)

每一层都落盘为 Parquet 文件，中间产物可回溯、可复用、可对接 Spark。

分层说明：
    ODS  Operational Data Store   原始数据原样保存，不做加工
    DWD  Data Warehouse Detail    清洗后的明细数据，去除脏数据
    DWS  Data Warehouse Summary   按维度轻度汇总 + 计算指标
    ADS  Application Data Service 面向业务的最终结果（异常/报表/入库）
"""

from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd

from .config import PROJECT_ROOT
from .logger import logger

# ============================================================
# 数仓目录结构（所有中间产物都落在 data/warehouse/ 下）
# ============================================================
WAREHOUSE_ROOT = PROJECT_ROOT / "data" / "warehouse"

# 每层的落盘路径
ODS_DIR = WAREHOUSE_ROOT / "ods"
DWD_DIR = WAREHOUSE_ROOT / "dwd"
DWS_DIR = WAREHOUSE_ROOT / "dws"
ADS_DIR = WAREHOUSE_ROOT / "ads"

# 每层产出的标准表名（Parquet 文件名）
ODS_AD_LOG = "ods_ad_log"          # 贴源层：广告原始流水
DWD_AD_FACT = "dwd_ad_fact"        # 明细层：清洗后的广告事实表
DWD_DIM_CAMPAIGN = "dim_campaign"  # 明细层：计划维度表
DWS_CHANNEL_DAILY = "dws_ad_channel_daily"  # 汇总层：渠道×日
DWS_DATE_DAILY = "dws_ad_date_daily"        # 汇总层：日期
ADS_ANOMALY = "ads_anomaly"        # 应用层：异常结果


def _ensure_dirs() -> None:
    """确保数仓各层目录存在。"""
    for d in (ODS_DIR, DWD_DIR, DWS_DIR, ADS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _write_layer(df: pd.DataFrame, layer_dir: Path, table_name: str) -> Path:
    """
    把 DataFrame 写入指定层的 Parquet 文件。

    Args:
        df: 要落盘的数据
        layer_dir: 层目录（ods/dwd/dws/ads）
        table_name: 表名（不含扩展名）

    Returns:
        写入的文件路径
    """
    layer_dir.mkdir(parents=True, exist_ok=True)
    out_path = layer_dir / f"{table_name}.parquet"
    df.to_parquet(out_path, index=False)
    logger.info(f"  已落盘 Parquet: {out_path} ({len(df)} 行)")
    return out_path


# ============================================================
# 第 1 层：ODS（贴源层）
# ============================================================
def build_ods(df: pd.DataFrame) -> pd.DataFrame:
    """
    ODS 层：原始数据原样落盘，不做任何加工。

    设计思想：ODS 是"数据备份"，保证上游数据进来了就永久留存一份，
    后面任何一层算错了都能从这里重新开始，不用重新去拉取。

    Args:
        df: 通过 load_data 加载进来的原始数据

    Returns:
        原样返回 df（不修改）
    """
    logger.info("=" * 60)
    logger.info("[ODS 层] 贴源层：原始数据落盘")
    logger.info("=" * 60)

    _ensure_dirs()

    # 原样落盘，不加工
    _write_layer(df, ODS_DIR, ODS_AD_LOG)

    logger.info(f"[ODS] 完成，原始数据 {len(df)} 行")
    return df


# ============================================================
# 第 2 层：DWD（明细层）
# ============================================================
def build_dwd(
    df: pd.DataFrame,
    clean_func,
    dim_df: Optional[pd.DataFrame] = None,
) -> Dict[str, pd.DataFrame]:
    """
    DWD 层：清洗明细数据 + 建立维度表。

    这一层做两件事：
    1. 事实表：对 ODS 数据做清洗（去重/缺失/类型/异常过滤）
    2. 维度表：从事实表抽取维度（如 campaign 维度表），做"建模"

    Args:
        df: ODS 原始数据
        clean_func: 清洗函数（传入 cleaning.clean_ad_data）
        dim_df: 可选，额外的维度表（如计划信息表），没有则从事实表抽取

    Returns:
        dict，包含 'fact'（事实表）和 'dim_campaign'（维度表）
    """
    logger.info("=" * 60)
    logger.info("[DWD 层] 明细层：清洗 + 维度建模")
    logger.info("=" * 60)

    _ensure_dirs()

    # 1. 清洗事实表
    fact_df = clean_func(df)
    _write_layer(fact_df, DWD_DIR, DWD_AD_FACT)

    # 2. 维度表：从事实表抽取 campaign 维度
    if dim_df is None:
        dim_df = _extract_campaign_dim(fact_df)
    _write_layer(dim_df, DWD_DIR, DWD_DIM_CAMPAIGN)

    logger.info(f"[DWD] 完成，事实表 {len(fact_df)} 行，维度表 {len(dim_df)} 行")
    return {"fact": fact_df, "dim_campaign": dim_df}


def _extract_campaign_dim(fact_df: pd.DataFrame) -> pd.DataFrame:
    """
    从事实表抽取 campaign 维度表（数仓建模里的"维度退化"）。

    把事实表里重复出现的 campaign 维度字段单独抽出来，
    去重后生成维度表，这是维度建模的标准做法。
    """
    if "campaign" not in fact_df.columns:
        return pd.DataFrame(columns=["campaign"])

    # 抽取维度列并去重
    dim_cols = [c for c in ("campaign", "channel", "region", "device") if c in fact_df.columns]
    dim_df = fact_df[dim_cols].drop_duplicates().reset_index(drop=True)
    # 生成维度主键
    dim_df.insert(0, "dim_id", range(1, len(dim_df) + 1))
    return dim_df


# ============================================================
# 第 3 层：DWS（汇总层）
# ============================================================
def build_dws(
    fact_df: pd.DataFrame,
    group_by_dimension_func,
    group_by_date_func,
) -> Dict[str, pd.DataFrame]:
    """
    DWS 层：按维度轻度汇总，并计算指标（CTR/CVR/CPC/CPA）。

    汇总层是"预计算"层：把业务最常查的维度提前算好，
    报表/查询直接读这里，不用每次现算。

    Args:
        fact_df: DWD 清洗后的事实表
        group_by_dimension_func: 按维度分组函数（metrics.group_by_dimension）
        group_by_date_func: 按日期分组函数（metrics.group_by_date）

    Returns:
        dict，包含 'channel_daily' 和 'date_daily'
    """
    logger.info("=" * 60)
    logger.info("[DWS 层] 汇总层：按维度聚合 + 计算指标")
    logger.info("=" * 60)

    _ensure_dirs()

    # 渠道汇总
    channel_df = group_by_dimension_func(fact_df, "channel")
    _write_layer(channel_df, DWS_DIR, DWS_CHANNEL_DAILY)

    # 日期汇总
    date_df = group_by_date_func(fact_df)
    _write_layer(date_df, DWS_DIR, DWS_DATE_DAILY)

    logger.info(f"[DWS] 完成，渠道 {len(channel_df)} 个，日期 {len(date_df)} 天")
    return {"channel_daily": channel_df, "date_daily": date_df}


# ============================================================
# 第 4 层：ADS（应用层）
# ============================================================
def build_ads(
    fact_df: pd.DataFrame,
    detect_anomalies_func,
) -> pd.DataFrame:
    """
    ADS 层：面向业务的最终结果 —— 异常检测结果落盘。

    ADS 是"给业务方看"的层，这里产出的是可直接消费的结论数据。
    报表生成、预警推送、MySQL 入库都从这里取数。

    Args:
        fact_df: DWD 清洗后的事实表
        detect_anomalies_func: 异常检测函数（anomaly_detection.detect_all_anomalies）

    Returns:
        异常结果 DataFrame
    """
    logger.info("=" * 60)
    logger.info("[ADS 层] 应用层：异常检测结果")
    logger.info("=" * 60)

    _ensure_dirs()

    anomalies_df = detect_anomalies_func(fact_df)
    _write_layer(anomalies_df, ADS_DIR, ADS_ANOMALY)

    logger.info(f"[ADS] 完成，异常 {len(anomalies_df)} 条")
    return anomalies_df
