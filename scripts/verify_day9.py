"""
Day9 验证脚本：报表生成模块
把所有模块串起来，生成完整的HTML日报
"""

import sys
sys.path.insert(0, 'src')

from ad_analytics.data_loader import load_from_csv
from ad_analytics.cleaning import clean_ad_data
from ad_analytics.metrics import (
    calculate_overall_metrics,
    group_by_dimension,
    group_by_date
)
from ad_analytics.anomaly_detection import detect_all_anomalies
from ad_analytics.report import generate_report


def main():
    print("=" * 60)
    print("Day9 报表生成模块测试")
    print("=" * 60)

    # 第1步：读取数据
    print("\n[1/7] 读取模拟数据...")
    df_raw = load_from_csv("data/raw/sample_ad_data.csv")
    print(f"原始数据：{len(df_raw)} 行")

    # 第2步：清洗数据
    print("\n[2/7] 清洗数据...")
    df_clean = clean_ad_data(df_raw)
    print(f"清洗后数据：{len(df_clean)} 行")

    # 第3步：计算整体指标
    print("\n[3/7] 计算整体指标...")
    import pandas as pd
    metrics_dict = calculate_overall_metrics(df_clean)
    metrics_df = pd.DataFrame([metrics_dict])  # 字典转成一行的DataFrame
    print(f"整体花费：{metrics_dict['total_cost']:,.2f}元")
    print(f"整体CTR：{metrics_dict['ctr']:.2%}")

    # 第4步：按渠道汇总
    print("\n[4/7] 按渠道汇总...")
    channel_df = group_by_dimension(df_clean, 'channel')
    print(f"共 {len(channel_df)} 个渠道")

    # 第5步：按日期汇总
    print("\n[5/7] 按日期汇总...")
    daily_df = group_by_date(df_clean)
    print(f"共 {len(daily_df)} 天数据")

    # 第6步：异常检测
    print("\n[6/7] 异常检测...")
    anomalies_df = detect_all_anomalies(df_clean)
    print(f"发现 {len(anomalies_df)} 条异常")

    # 第7步：生成报表
    print("\n[7/7] 生成HTML报表...")
    output_path = "reports/daily_report.html"
    generate_report(
        df=df_clean,
        metrics_df=metrics_df,
        channel_df=channel_df,
        daily_df=daily_df,
        anomalies_df=anomalies_df,
        output_path=output_path,
        title="2026年8月 广告投放日报"
    )

    print(f"\n报表已生成：{output_path}")
    print("请在文件管理器中双击打开，或在浏览器中打开查看")

    print("\n" + "=" * 60)
    print("测试完成！报表生成模块工作正常。")
    print("=" * 60)


if __name__ == "__main__":
    main()
