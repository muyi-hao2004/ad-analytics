"""
Day6 验证脚本：测试 database.py 模块
完整流程：初始化数据库 → 读取数据 → 清洗数据 → 写入数据库 → 查询验证
"""

import sys
from pathlib import Path

# 把 src 目录加到 Python 路径，这样才能 import ad_analytics
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from ad_analytics.data_loader import load_from_csv
from ad_analytics.cleaning import clean_ad_data
from ad_analytics.database import init_database, insert_ad_data, query_to_df, execute_sql


def main():
    print("=" * 60)
    print("Day6 数据库模块测试")
    print("=" * 60)

    # 第1步：初始化数据库（创建数据库 + 建表）
    print("\n[1/5] 初始化数据库...")
    init_database()
    # 因为表结构可能变了，先删除旧表再重建
    execute_sql("DROP TABLE IF EXISTS ad_data")
    execute_sql("DROP TABLE IF EXISTS campaigns")
    from ad_analytics.database import create_tables
    create_tables()

    # 第2步：读取模拟数据
    print("\n[2/5] 读取模拟数据...")
    csv_path = project_root / "data" / "raw" / "sample_ad_data.csv"
    df_raw = load_from_csv(str(csv_path))
    print(f"原始数据：{len(df_raw)} 行，{len(df_raw.columns)} 列")

    # 第3步：清洗数据
    print("\n[3/5] 清洗数据...")
    df_clean = clean_ad_data(df_raw)
    print(f"清洗后数据：{len(df_clean)} 行，{len(df_clean.columns)} 列")
    print(f"列名：{df_clean.columns.tolist()}")

    # 第4步：写入数据库（先清空旧数据，再写入新数据）
    print("\n[4/5] 写入数据库...")
    execute_sql("TRUNCATE TABLE ad_data")  # 清空表，避免重复数据
    insert_ad_data(df_clean, if_exists='append')
    print(f"已写入 {len(df_clean)} 条数据")

    # 第5步：查询验证
    print("\n[5/5] 查询验证...")

    # 查询总记录数
    df_count = query_to_df("SELECT COUNT(*) AS total FROM ad_data")
    print(f"数据库总记录数：{df_count['total'].iloc[0]}")

    # 查询前5条
    print("\n前5条数据：")
    df_head = query_to_df("SELECT * FROM ad_data ORDER BY date LIMIT 5")
    print(df_head.to_string(index=False))

    # 按渠道统计
    print("\n按渠道统计：")
    df_channel = query_to_df("""
        SELECT 
            channel,
            COUNT(*) AS record_count,
            SUM(impressions) AS total_impressions,
            SUM(clicks) AS total_clicks,
            SUM(cost) AS total_cost
        FROM ad_data
        GROUP BY channel
        ORDER BY total_cost DESC
    """)
    print(df_channel.to_string(index=False))

    # 按日期统计
    print("\n按日期统计（前7天）：")
    df_date = query_to_df("""
        SELECT 
            date,
            SUM(cost) AS total_cost,
            SUM(clicks) / SUM(impressions) AS ctr
        FROM ad_data
        GROUP BY date
        ORDER BY date
        LIMIT 7
    """)
    print(df_date.to_string(index=False))

    print("\n" + "=" * 60)
    print("测试完成！数据库模块工作正常。")
    print("=" * 60)


if __name__ == "__main__":
    main()
