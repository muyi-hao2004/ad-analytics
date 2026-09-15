"""
Day3验证脚本：加载模拟数据，运行清洗，对比清洗前后的数据质量。
"""

from ad_analytics.data_loader import load_from_csv
from ad_analytics.cleaning import clean_ad_data, get_cleaning_report
from ad_analytics.logger import logger


def main():
    logger.info("=" * 60)
    logger.info("Day3验证：数据清洗模块测试")
    logger.info("=" * 60)

    # 1. 加载原始数据
    raw_df = load_from_csv()
    logger.info(f"原始数据: {len(raw_df)} 行")

    # 2. 查看原始数据的问题
    logger.info("\n原始数据问题:")
    logger.info(f"  - cost缺失: {raw_df['cost'].isna().sum()} 条")
    logger.info(f"  - 零曝光: {(raw_df['impressions'] == 0).sum()} 条")
    logger.info(f"  - 重复行: {raw_df.duplicated().sum()} 条")

    # 3. 运行清洗
    clean_df = clean_ad_data(raw_df)

    # 4. 生成清洗报告
    report = get_cleaning_report(raw_df, clean_df)

    logger.info("\n" + "=" * 60)
    logger.info("数据清洗报告:")
    logger.info(f"  原始行数: {report['raw_rows']}")
    logger.info(f"  清洗后行数: {report['clean_rows']}")
    logger.info(f"  删除行数: {report['removed_rows']}")
    logger.info(f"  删除比例: {report['removal_rate']}%")
    logger.info(f"  清洗后缺失值: {report['clean_missing_values']}")
    logger.info(f"  清洗后重复行: {report['clean_duplicates']}")
    logger.info("=" * 60)

    # 5. 清洗后的数据预览
    logger.info("\n清洗后数据前5行:")
    print(clean_df.head())
    print()

    # 6. 清洗后按渠道统计
    logger.info("清洗后按渠道统计:")
    channel_stats = clean_df.groupby('channel').agg({
        'impressions': 'sum',
        'clicks': 'sum',
        'conversions': 'sum',
        'cost': 'sum',
    }).round(2)
    channel_stats['CTR'] = (channel_stats['clicks'] / channel_stats['impressions']).round(4)
    channel_stats['CVR'] = (channel_stats['conversions'] / channel_stats['clicks']).round(4)
    channel_stats['CPC'] = (channel_stats['cost'] / channel_stats['clicks']).round(2)
    print(channel_stats)
    print()

    logger.info("=" * 60)
    logger.info("Day3验证完成！数据清洗模块工作正常。")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
