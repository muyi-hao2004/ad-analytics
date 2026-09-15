"""
Day2验证脚本：加载模拟数据，查看数据验证报告和数据摘要。
"""

from ad_analytics.data_loader import load_from_csv, get_data_summary
from ad_analytics.logger import logger


def main():
    logger.info("=" * 60)
    logger.info("Day2验证：数据加载模块测试")
    logger.info("=" * 60)

    # 1. 从CSV加载数据
    df = load_from_csv()

    # 2. 查看数据前5行
    logger.info("\n数据前5行：")
    print(df.head())
    print()

    # 3. 查看数据基本信息
    logger.info("数据基本信息：")
    print(df.info())
    print()

    # 4. 查看数据摘要
    summary = get_data_summary(df)
    logger.info("数据统计摘要：")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print()

    # 5. 按渠道统计
    logger.info("按渠道统计：")
    channel_stats = df.groupby('channel').agg({
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
    logger.info("Day2验证完成！数据加载模块工作正常。")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
