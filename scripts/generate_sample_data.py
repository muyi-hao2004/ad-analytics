"""
生成模拟广告投放数据
生成1000条模拟的广告投放日报数据，用于开发和测试。
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ad_analytics.config import PROJECT_ROOT

# 固定随机种子，保证每次生成的数据一样（方便测试）
random.seed(42)

# ===== 模拟数据的维度 =====
CHANNELS = ['Facebook', 'Google', 'TikTok', 'Instagram', 'YouTube']
CAMPAIGNS = [
    '夏季促销_品牌词', '夏季促销_通用词', '新品发布_兴趣定向',
    '再营销_老客', '拉新_相似受众', '618大促_主推',
    '双11预热_品牌', '双11正式_爆款', '日常投放_长尾词',
]
REGIONS = ['华东', '华南', '华北', '西南', '华中', '东北', '西北']
DEVICES = ['Mobile', 'Desktop', 'Tablet']


def generate_one_record(record_date: date) -> dict:
    """生成一条模拟的广告投放记录。"""
    channel = random.choice(CHANNELS)
    campaign = random.choice(CAMPAIGNS)
    region = random.choice(REGIONS)
    device = random.choice(DEVICES)

    # 基础曝光量：不同渠道量级不同
    base_impressions = {
        'Facebook': 50000, 'Google': 80000, 'TikTok': 120000,
        'Instagram': 40000, 'YouTube': 60000,
    }
    impressions = int(random.gauss(base_impressions[channel], 15000))
    impressions = max(100, impressions)  # 保证至少100曝光

    # 点击率：不同渠道CTR不同（1%-5%）
    ctr_base = {'Facebook': 0.025, 'Google': 0.035, 'TikTok': 0.045,
                'Instagram': 0.02, 'YouTube': 0.015}
    ctr = random.gauss(ctr_base[channel], 0.01)
    ctr = max(0.001, min(0.15, ctr))  # 限制在0.1%-15%之间
    clicks = int(impressions * ctr)

    # 转化率：点击到转化的比例（2%-10%）
    cvr = random.gauss(0.05, 0.02)
    cvr = max(0.005, min(0.2, cvr))
    conversions = int(clicks * cvr)

    # 单次点击成本CPC：不同渠道不同（1-5元）
    cpc_base = {'Facebook': 2.5, 'Google': 3.5, 'TikTok': 1.8,
                'Instagram': 2.8, 'YouTube': 2.0}
    cpc = random.gauss(cpc_base[channel], 0.8)
    cpc = max(0.5, cpc)
    cost = round(clicks * cpc, 2)

    # 故意造一些脏数据（用于测试清洗模块）
    # 5%的概率缺失花费
    if random.random() < 0.05:
        cost = None

    # 3%的概率曝光量为0（异常数据）
    if random.random() < 0.03:
        impressions = 0
        clicks = 0
        conversions = 0
        cost = 0

    return {
        'date': record_date.isoformat(),
        'channel': channel,
        'campaign': campaign,
        'region': region,
        'device': device,
        'impressions': impressions,
        'clicks': clicks,
        'conversions': conversions,
        'cost': cost,
    }


def main():
    """生成30天的模拟数据，每天约30-40条，总共约1000条。"""
    records = []
    start_date = date(2026, 8, 1)

    for day_offset in range(30):  # 30天
        current_date = start_date + timedelta(days=day_offset)
        # 每天生成30-40条记录
        daily_count = random.randint(30, 40)
        for _ in range(daily_count):
            records.append(generate_one_record(current_date))

    df = pd.DataFrame(records)

    # 打乱顺序（模拟真实导出的数据不是按日期排好的）
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)

    # 保存到 data/raw/
    output_dir = PROJECT_ROOT / 'data' / 'raw'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / 'sample_ad_data.csv'
    df.to_csv(output_path, index=False, encoding='utf-8')

    print(f"✅ 模拟数据生成完成！")
    print(f"   文件路径: {output_path}")
    print(f"   总记录数: {len(df)}")
    print(f"   日期范围: {df['date'].min()} ~ {df['date'].max()}")
    print(f"   渠道数: {df['channel'].nunique()}")
    print(f"   计划数: {df['campaign'].nunique()}")
    print(f"   缺失值: cost列缺失 {df['cost'].isna().sum()} 条")
    print(f"   零曝光: {(df['impressions'] == 0).sum()} 条")


if __name__ == '__main__':
    main()
