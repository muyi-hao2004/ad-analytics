"""
广告投放数据分析平台 - 主入口
一键运行完整流程：数据加载 → 清洗 → 指标计算 → 异常检测 → 写入数据库 → 生成报表
"""

import os
from datetime import datetime

from ad_analytics.config import settings, PROJECT_ROOT
from ad_analytics.logger import logger
from ad_analytics.data_loader import load_from_csv, validate_data, get_data_summary
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


def main():
    """主函数：运行完整的数据分析流水线"""

    # ============================================================
    # 第1步：初始化
    # ============================================================
    logger.info("=" * 70)
    logger.info("广告投放数据分析平台 - 开始运行")
    logger.info("=" * 70)
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"项目根目录: {PROJECT_ROOT}")
    logger.info(f"环境: {settings.APP_ENV} | 调试模式: {settings.DEBUG}")
    logger.info("")

    # ============================================================
    # 第2步：数据加载
    # ============================================================
    logger.info("【第2步】数据加载")
    logger.info("-" * 70)

    # 原始数据路径
    raw_data_path = os.path.join(PROJECT_ROOT, "data", "raw", "sample_ad_data.csv")
    logger.info(f"读取数据文件: {raw_data_path}")

    # 从CSV读取数据
    df_raw = load_from_csv(raw_data_path)
    logger.info(f"原始数据: {df_raw.shape[0]} 行, {df_raw.shape[1]} 列")

    # 验证数据
    validation = validate_data(df_raw, raise_on_error=False)
    logger.info(f"数据验证: {'通过' if validation['is_valid'] else '有警告'}")
    if validation.get("warnings"):
        for warning in validation["warnings"]:
            logger.warning(f"  - {warning}")

    # 打印数据摘要
    summary = get_data_summary(df_raw)
    logger.info(f"日期范围: {summary['date_range']}")
    logger.info(f"渠道数量: {summary['channels']}")
    logger.info(f"计划数量: {summary['campaigns']}")
    logger.info("")

    # ============================================================
    # 第3步：数据清洗
    # ============================================================
    logger.info("【第3步】数据清洗")
    logger.info("-" * 70)

    # 清洗数据
    df_clean = clean_ad_data(df_raw)
    logger.info(f"清洗后数据: {df_clean.shape[0]} 行, {df_clean.shape[1]} 列")

    # 生成清洗报告
    cleaning_report = get_cleaning_report(df_raw, df_clean)
    logger.info(f"删除行数: {cleaning_report['rows_removed']} "
                f"({cleaning_report['removal_rate']:.2%})")
    logger.info(f"缺失值处理: {cleaning_report['missing_values_filled']} 条")
    logger.info(f"重复值删除: {cleaning_report['duplicates_removed']} 条")
    logger.info(f"零曝光删除: {cleaning_report['zero_impressions_removed']} 条")
    logger.info("")

    # ============================================================
    # 第4步：指标计算
    # ============================================================
    logger.info("【第4步】指标计算")
    logger.info("-" * 70)

    # 4.1 计算基础指标（每行的 CTR/CVR/CPC/CPA）
    df_with_metrics = calculate_basic_metrics(df_clean)
    logger.info("基础指标计算完成（CTR/CVR/CPC/CPA）")

    # 4.2 计算整体指标（返回字典）
    overall_metrics = calculate_overall_metrics(df_with_metrics)
    logger.info(f"整体指标:")
    logger.info(f"  总花费: ¥{overall_metrics['total_cost']:,.2f}")
    logger.info(f"  总曝光: {overall_metrics['total_impressions']:,}")
    logger.info(f"  总点击: {overall_metrics['total_clicks']:,}")
    logger.info(f"  总转化: {overall_metrics['total_conversions']:,}")
    logger.info(f"  整体CTR: {overall_metrics['ctr']:.2%}")
    logger.info(f"  整体CVR: {overall_metrics['cvr']:.2%}")
    logger.info(f"  整体CPC: ¥{overall_metrics['cpc']:.2f}")
    logger.info(f"  整体CPA: ¥{overall_metrics['cpa']:.2f}")

    # 4.3 按渠道汇总
    df_channel = group_by_dimension(df_with_metrics, "channel")
    logger.info(f"渠道汇总: {df_channel.shape[0]} 个渠道")
    for _, row in df_channel.iterrows():
        logger.info(f"  {row['channel']}: 花费¥{row['cost']:,.0f}, "
                    f"CTR {row['ctr']:.2%}, CPA ¥{row['cpa']:.2f}")

    # 4.4 按日期汇总
    df_daily = group_by_date(df_with_metrics)
    logger.info(f"日期汇总: {df_daily.shape[0]} 天")
    logger.info("")

    # ============================================================
    # 第5步：异常检测
    # ============================================================
    logger.info("【第5步】异常检测")
    logger.info("-" * 70)

    # 检测所有异常
    df_anomalies = detect_all_anomalies(df_with_metrics)
    logger.info(f"检测到异常: {len(df_anomalies)} 条")

    # 按异常类型统计
    if len(df_anomalies) > 0:
        anomaly_counts = df_anomalies["anomaly_type"].value_counts()
        for anomaly_type, count in anomaly_counts.items():
            logger.info(f"  {anomaly_type}: {count} 条")

        # 按严重程度统计
        severity_counts = df_anomalies["severity"].value_counts()
        logger.info(f"严重程度:")
        for severity, count in severity_counts.items():
            logger.info(f"  {severity}: {count} 条")

        # 保存异常结果
        anomalies_path = os.path.join(PROJECT_ROOT, "data", "processed", "anomalies.csv")
        os.makedirs(os.path.dirname(anomalies_path), exist_ok=True)
        df_anomalies.to_csv(anomalies_path, index=False, encoding="utf-8-sig")
        logger.info(f"异常结果已保存: {anomalies_path}")
    else:
        logger.info("未检测到异常")
    logger.info("")

    # ============================================================
    # 第6步：写入数据库
    # ============================================================
    logger.info("【第6步】写入数据库")
    logger.info("-" * 70)

    try:
        # 初始化数据库（建库建表）
        init_database()
        logger.info("数据库初始化完成")

        # 写入清洗后的数据（先删除旧数据，再插入新数据，避免重复）
        insert_ad_data(df_clean, if_exists="replace")
        logger.info(f"数据写入完成: {len(df_clean)} 条记录")
        logger.info(f"数据库: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    except Exception as e:
        logger.error(f"数据库写入失败: {e}")
        logger.warning("跳过数据库步骤，继续后续流程")
    logger.info("")

    # ============================================================
    # 第7步：生成报表
    # ============================================================
    logger.info("【第7步】生成报表")
    logger.info("-" * 70)

    # 报表输出路径
    report_date = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(PROJECT_ROOT, "reports", f"daily_report_{report_date}.html")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    # 把整体指标字典转成一行DataFrame（报表模块需要）
    import pandas as pd
    df_overall = pd.DataFrame([overall_metrics])

    # 生成HTML报表
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
    # 第8步：运行总结
    # ============================================================
    logger.info("=" * 70)
    logger.info("运行完成！总结")
    logger.info("=" * 70)
    logger.info(f"原始数据: {len(df_raw)} 行")
    logger.info(f"清洗后数据: {len(df_clean)} 行（删除 {len(df_raw) - len(df_clean)} 行）")
    logger.info(f"总花费: ¥{overall_metrics['total_cost']:,.2f}")
    logger.info(f"整体CTR: {overall_metrics['ctr']:.2%}")
    logger.info(f"整体CPA: ¥{overall_metrics['cpa']:.2f}")
    logger.info(f"异常数量: {len(df_anomalies)} 条")
    logger.info(f"报表文件: {report_path}")
    logger.info("=" * 70)
    logger.info("全流程运行成功！")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
# 配置用户名（用你的GitHub用户名）
git config --global user.name "muyi-hao2004"

# 配置邮箱（用你注册GitHub的邮箱）
git config --global user.email "meinvmuyi214@gmail.com"
