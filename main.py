"""
广告投放数据分析平台 - 主入口（数仓四层架构版）
一键运行完整流程：ODS → DWD → DWS → ADS

四层架构：
    ODS 贴源层：多种方式导入原始数据（CSV / Parquet / API），原样落盘
    DWD 明细层：清洗数据 + 维度建模
    DWS 汇总层：按维度聚合 + 计算指标
    ADS 应用层：异常检测 + 入库 + 生成报表
"""

import os
from datetime import datetime

from ad_analytics.config import settings, PROJECT_ROOT
from ad_analytics.logger import logger
from ad_analytics.data_loader import load_data, validate_data, get_data_summary
from ad_analytics.cleaning import clean_ad_data, get_cleaning_report
from ad_analytics.metrics import (
    calculate_basic_metrics,
    calculate_overall_metrics,
    group_by_dimension,
    group_by_date,
)
from ad_analytics.anomaly_detection import detect_all_anomalies
from ad_analytics.database import init_database, insert_ad_data
from ad_analytics.report import generate_report
from ad_analytics.warehouse import (
    build_ods,
    build_dwd,
    build_dws,
    build_ads,
    DWD_AD_FACT,
    DWS_CHANNEL_DAILY,
    DWS_DATE_DAILY,
    ADS_ANOMALY,
)


def main():
    """主函数：按数仓四层架构运行完整流水线"""

    # ============================================================
    # 第 0 步：初始化
    # ============================================================
    logger.info("=" * 70)
    logger.info("广告投放数据分析平台 - 数仓四层架构（ODS→DWD→DWS→ADS）")
    logger.info("=" * 70)
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"项目根目录: {PROJECT_ROOT}")
    logger.info(f"环境: {settings.APP_ENV} | 调试模式: {settings.DEBUG}")
    logger.info("")

    # ============================================================
    # 第 1 层：ODS —— 数据接入（支持 CSV / Parquet / API 三种方式）
    # ============================================================
    logger.info("【第1层 ODS】数据接入（多种导入方式）")
    logger.info("-" * 70)

    # 数据源类型：默认 CSV，可通过环境变量 DATA_SOURCE_TYPE 切换
    source_type = os.getenv("DATA_SOURCE_TYPE", "csv").lower()
    raw_data_path = os.path.join(PROJECT_ROOT, "data", "raw", "sample_ad_data.csv")

    if source_type == "csv":
        logger.info(f"导入方式: CSV")
        df_raw = load_data("csv", file_path=raw_data_path)
    elif source_type == "parquet":
        # Parquet 导入：先尝试 data/raw/sample_ad_data.parquet
        parquet_path = os.path.join(PROJECT_ROOT, "data", "raw", "sample_ad_data.parquet")
        logger.info(f"导入方式: Parquet")
        df_raw = load_data("parquet", file_path=parquet_path)
    elif source_type == "api":
        # API 导入：从配置/环境变量读取日期范围
        logger.info(f"导入方式: API")
        start = os.getenv("API_START_DATE", "2024-01-01")
        end = os.getenv("API_END_DATE", "2024-12-31")
        df_raw = load_data("api", start_date=start, end_date=end)
    else:
        raise ValueError(f"不支持的数据源类型: {source_type}，可选 csv/parquet/api")

    logger.info(f"原始数据: {df_raw.shape[0]} 行, {df_raw.shape[1]} 列")

    # 数据验证
    validation = validate_data(df_raw, raise_on_error=False)
    has_issue = (
        validation.get("missing_values")
        or validation.get("invalid_values")
        or validation.get("duplicate_rows", 0) > 0
    )
    logger.info(f"数据验证: {'有警告' if has_issue else '通过'}")
    if validation.get("warnings"):
        for warning in validation["warnings"]:
            logger.warning(f"  - {warning}")

    # 数据摘要
    summary = get_data_summary(df_raw)
    logger.info(f"日期范围: {summary['date_range']}")
    logger.info(f"渠道数量: {summary['channels']}")
    logger.info("")

    # ODS 落盘（原样保存）
    build_ods(df_raw)

    # ============================================================
    # 第 2 层：DWD —— 清洗 + 维度建模
    # ============================================================
    logger.info("【第2层 DWD】数据清洗 + 维度建模")
    logger.info("-" * 70)

    dwd = build_dwd(df_raw, clean_func=clean_ad_data)
    df_clean = dwd["fact"]
    df_dim_campaign = dwd["dim_campaign"]

    # 清洗报告
    cleaning_report = get_cleaning_report(df_raw, df_clean)
    logger.info(f"删除行数: {cleaning_report['removed_rows']} "
                f"({cleaning_report['removal_rate']:.2f}%)")
    logger.info(f"维度表(计划): {len(df_dim_campaign)} 条")
    logger.info("")

    # ============================================================
    # 第 3 层：DWS —— 聚合 + 指标计算
    # ============================================================
    logger.info("【第3层 DWS】维度聚合 + 指标计算")
    logger.info("-" * 70)

    dws = build_dws(
        df_clean,
        group_by_dimension_func=group_by_dimension,
        group_by_date_func=group_by_date,
    )
    df_channel = dws["channel_daily"]
    df_daily = dws["date_daily"]

    # 整体指标
    df_with_metrics = calculate_basic_metrics(df_clean)
    overall_metrics = calculate_overall_metrics(df_with_metrics)
    logger.info(f"整体指标:")
    logger.info(f"  总花费: ¥{overall_metrics['total_cost']:,.2f}")
    logger.info(f"  总曝光: {overall_metrics['total_impressions']:,}")
    logger.info(f"  总点击: {overall_metrics['total_clicks']:,}")
    logger.info(f"  整体CTR: {overall_metrics['ctr']:.2%}")
    logger.info(f"  整体CVR: {overall_metrics['cvr']:.2%}")
    logger.info(f"  整体CPC: ¥{overall_metrics['cpc']:.2f}")
    logger.info(f"  整体CPA: ¥{overall_metrics['cpa']:.2f}")

    logger.info(f"渠道汇总: {df_channel.shape[0]} 个渠道")
    for _, row in df_channel.iterrows():
        logger.info(f"  {row['channel']}: 花费¥{row['cost']:,.0f}, "
                    f"CTR {row['ctr']:.2%}, CPA ¥{row['cpa']:.2f}")
    logger.info("")

    # ============================================================
    # 第 4 层：ADS —— 异常检测 + 入库 + 报表
    # ============================================================
    logger.info("【第4层 ADS】异常检测 + 入库 + 报表")
    logger.info("-" * 70)

    # 4.1 异常检测（落盘到 ADS 层）
    df_anomalies = build_ads(df_clean, detect_anomalies_func=detect_all_anomalies)
    logger.info(f"检测到异常: {len(df_anomalies)} 条")
    if len(df_anomalies) > 0:
        for anomaly_type, count in df_anomalies["anomaly_type"].value_counts().items():
            logger.info(f"  {anomaly_type}: {count} 条")

    # 4.2 写入数据库（分层入库）
    try:
        init_database()
        insert_ad_data(df_clean, if_exists="replace")
        logger.info(f"MySQL 入库完成: {len(df_clean)} 条记录")
        logger.info(f"数据库: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    except Exception as e:
        logger.error(f"数据库写入失败: {e}")
        logger.warning("跳过数据库步骤，继续后续流程")
    logger.info("")

    # 4.3 生成报表
    report_date = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(PROJECT_ROOT, "reports", f"daily_report_{report_date}.html")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    import pandas as pd
    df_overall = pd.DataFrame([overall_metrics])

    generate_report(
        df=df_with_metrics,
        metrics_df=df_overall,
        channel_df=df_channel,
        daily_df=df_daily,
        anomalies_df=df_anomalies,
        output_path=report_path,
        title=f"广告投放日报 - {report_date}",
    )
    logger.info(f"报表已生成: {report_path}")
    logger.info("")

    # ============================================================
    # 运行总结
    # ============================================================
    logger.info("=" * 70)
    logger.info("四层数仓构建完成！")
    logger.info("=" * 70)
    logger.info(f"ODS 贴源层: {len(df_raw)} 行 → data/warehouse/ods/ods_ad_log.parquet")
    logger.info(f"DWD 明细层: {len(df_clean)} 行 → data/warehouse/dwd/dwd_ad_fact.parquet")
    logger.info(f"DWS 汇总层: 渠道 {len(df_channel)} / 日期 {len(df_daily)}")
    logger.info(f"ADS 应用层: 异常 {len(df_anomalies)} 条 → data/warehouse/ads/ads_anomaly.parquet")
    logger.info(f"总花费: ¥{overall_metrics['total_cost']:,.2f}")
    logger.info(f"整体CTR: {overall_metrics['ctr']:.2%}")
    logger.info(f"报表文件: {report_path}")
    logger.info("=" * 70)
    logger.info("全流程运行成功！")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
