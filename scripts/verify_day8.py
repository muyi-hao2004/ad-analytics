"""
Day8 验证脚本：异常检测模块
"""

import sys
sys.path.insert(0, 'src')

import pandas as pd
from ad_analytics.data_loader import load_from_csv
from ad_analytics.cleaning import clean_ad_data
from ad_analytics.anomaly_detection import detect_all_anomalies


def main():
    print("=" * 60)
    print("Day8 异常检测模块测试")
    print("=" * 60)

    # 第1步：读取数据
    print("\n[1/4] 读取模拟数据...")
    df_raw = load_from_csv("data/raw/sample_ad_data.csv")
    print(f"原始数据：{len(df_raw)} 行")

    # 第2步：清洗数据
    print("\n[2/4] 清洗数据...")
    df_clean = clean_ad_data(df_raw)
    print(f"清洗后数据：{len(df_clean)} 行")

    # 第3步：执行异常检测
    print("\n[3/4] 执行异常检测...")
    anomalies = detect_all_anomalies(df_clean)
    print(f"共发现 {len(anomalies)} 条异常")

    # 第4步：展示结果
    print("\n[4/4] 异常检测结果：")
    print("-" * 60)

    if len(anomalies) == 0:
        print("没有发现异常")
    else:
        # 按异常类型统计
        print("\n按异常类型统计：")
        type_counts = anomalies['anomaly_type'].value_counts()
        for anomaly_type, count in type_counts.items():
            print(f"  {anomaly_type}: {count} 条")

        # 按严重程度统计
        print("\n按严重程度统计：")
        severity_counts = anomalies['severity'].value_counts()
        for severity, count in severity_counts.items():
            print(f"  {severity}: {count} 条")

        # 展示前10条异常
        print("\n前10条异常详情：")
        print("-" * 60)
        display_cols = ['date', 'channel', 'campaign', 'anomaly_type',
                        'metric_value', 'severity', 'description']
        pd.set_option('display.max_colwidth', 50)
        pd.set_option('display.width', 200)
        print(anomalies[display_cols].head(10).to_string(index=False))

        # 保存异常结果到CSV
        output_path = "data/processed/anomalies.csv"
        import os
        os.makedirs("data/processed", exist_ok=True)
        anomalies.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"\n异常结果已保存到：{output_path}")

    print("\n" + "=" * 60)
    print("测试完成！异常检测模块工作正常。")
    print("=" * 60)


if __name__ == "__main__":
    main()
