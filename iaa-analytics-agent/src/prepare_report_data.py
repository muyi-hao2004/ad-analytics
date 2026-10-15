"""
报表演示数据准备脚本

造一份"多国家/多系列/多素材"的手工数据，跑通四层数仓，
产出完整的三张业务表（日常表/素材表/子渠道表），供报表展示。

注意：这是"干净"的演示数据，不埋脏数据。
     带脏数据的造数脚本（生成脏数据）后面单独做。

运行：
    cd iaa-analytics-agent
    venv/Scripts/python.exe src/prepare_report_data.py
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import config
import warehouse


# 模拟的业务规模
APPS = ["money_clicker", "coin_master_lite"]   # 2 个包
COUNTRIES = ["US", "CA", "JP", "GB"]           # 4 个国家
CAMPAIGNS = {                                   # 系列（包 → 系列列表）
    "money_clicker": ["US_A_01", "US_B_01", "JP_A_01"],
    "coin_master_lite": ["CA_A_01", "GB_A_01"],
}
CREATIVES = ["crt_001", "crt_002", "crt_003", "crt_004", "crt_005"]
SUB_CHANNELS = ["ch_001", "ch_002", "ch_003", "ch_004"]
AD_NETWORKS = ["Unity", "MTG", "AdMob"]

# 每个素材的"质量"（决定后端留存/收入好坏，用于演示"换素材"判断）
CREATIVE_QUALITY = {
    "crt_001": 1.0,   # 好
    "crt_002": 0.3,   # 差
    "crt_003": 0.8,   # 较好
    "crt_004": 0.5,   # 一般
    "crt_005": 0.1,   # 极差
}

# 每个子渠道的质量
SUB_CHANNEL_QUALITY = {
    "ch_001": 1.0, "ch_002": 0.7, "ch_003": 0.4, "ch_004": 0.15,
}

DAYS = 8   # 覆盖 D0~D7
START_DATE = "2026-10-01"


def make_fact_rows():
    """造双轴事实表（后端，cohort 口径）。"""
    rows = []
    rng = np.random.default_rng(42)

    for install_offset in range(DAYS):
        install_date = pd.Timestamp(START_DATE) + pd.Timedelta(days=install_offset)
        install_date_str = install_date.strftime("%Y-%m-%d")

        for app in APPS:
            for country in COUNTRIES:
                # 每个包只在部分国家投放
                if app == "money_clicker" and country not in ("US", "JP"):
                    continue
                if app == "coin_master_lite" and country not in ("CA", "GB"):
                    continue

                for campaign in CAMPAIGNS[app]:
                    # 系列只在其名字里标的国家投放
                    if campaign.split("_")[0] != country:
                        continue

                    for creative in CREATIVES:
                        for sub_channel in SUB_CHANNELS:
                            # 每个素材只挂部分子渠道（避免笛卡尔爆炸）
                            if (int(creative[-1]) + int(sub_channel[-1])) % 2 != 0:
                                continue

                            # 基础安装量（随素材质量波动）
                            quality = CREATIVE_QUALITY[creative]
                            base_installs = int(rng.integers(200, 800) * (0.7 + 0.3 * quality))
                            # 花费（买量一次性投入，在 install 当天）
                            spend = round(base_installs * 0.03, 2)

                            # 该 cohort 的每天表现
                            for day in range(DAYS):
                                report_date = install_date + pd.Timedelta(days=day)
                                report_date_str = report_date.strftime("%Y-%m-%d")

                                # 留存衰减（质量越好衰减越慢）
                                sub_quality = SUB_CHANNEL_QUALITY[sub_channel]
                                ret = max(0, (0.5 * quality + 0.5 * sub_quality)) ** (day + 1)
                                active = int(base_installs * ret)
                                if active <= 0:
                                    active = 0

                                ad_impressions = active * 5
                                rewarded_views = int(active * 0.8)
                                # 每活跃用户日均收入（美元）。系数 0.006 配合 CPI=0.03，
                                # 使 D7 ROAS 落在真实网赚 IAA 的合理区间：
                                #   头部素材 ~160%、中部 ~30%~80%、尾部 ~2%
                                # 这样 70% 红线能正好区分「该优化」和「跑得好」的素材。
                                ad_revenue = round(active * 0.006 * quality, 2)

                                rows.append({
                                    "install_date": install_date_str,
                                    "report_date": report_date_str,
                                    "app": app, "country": country,
                                    "campaign": campaign,
                                    "sub_channel": sub_channel,
                                    "creative_id": creative,
                                    "placement": "rewarded",
                                    "ad_network": AD_NETWORKS[day % len(AD_NETWORKS)],
                                    "installs": base_installs,
                                    "active_users": active,
                                    "ad_impressions": ad_impressions,
                                    "rewarded_views": rewarded_views,
                                    "ad_revenue": ad_revenue,
                                    "spend": spend if day == 0 else 0.0,
                                })

    return pd.DataFrame(rows)


def make_acq_rows():
    """造买量日表（前端，单轴 report_date）。"""
    rows = []
    rng = np.random.default_rng(7)

    for day_offset in range(DAYS):
        report_date = pd.Timestamp(START_DATE) + pd.Timedelta(days=day_offset)
        report_date_str = report_date.strftime("%Y-%m-%d")

        for app in APPS:
            for country in COUNTRIES:
                if app == "money_clicker" and country not in ("US", "JP"):
                    continue
                if app == "coin_master_lite" and country not in ("CA", "GB"):
                    continue
                for campaign in CAMPAIGNS[app]:
                    if campaign.split("_")[0] != country:
                        continue
                    for creative in CREATIVES:
                        for sub_channel in SUB_CHANNELS:
                            if (int(creative[-1]) + int(sub_channel[-1])) % 2 != 0:
                                continue
                            quality = CREATIVE_QUALITY[creative]
                            buy_impressions = int(rng.integers(3000, 12000))
                            clicks = int(buy_impressions * rng.uniform(0.05, 0.15))
                            installs = int(clicks * rng.uniform(0.3, 0.6) * (0.7 + 0.3 * quality))
                            spend = round(installs * 0.03, 2)

                            rows.append({
                                "report_date": report_date_str,
                                "app": app, "country": country,
                                "campaign": campaign,
                                "sub_channel": sub_channel,
                                "creative_id": creative,
                                "placement": "rewarded",
                                "ad_network": AD_NETWORKS[day_offset % len(AD_NETWORKS)],
                                "clicks": clicks,
                                "buy_impressions": buy_impressions,
                                "installs": installs,
                                "spend": spend,
                            })

    return pd.DataFrame(rows)


def main():
    config.ensure_dirs()
    print("造演示数据（多国家/多系列/多素材）...")

    fact = make_fact_rows()
    acq = make_acq_rows()
    print(f"  双轴事实表原始数据: {len(fact)} 行")
    print(f"  买量日表原始数据: {len(acq)} 行")

    # 跑四层数仓
    print("\n跑四层数仓...")
    dwd = warehouse.build_dwd(fact)
    acq_dwd = warehouse.build_acquisition(acq)
    dws = warehouse.build_dws(dwd)
    ads = warehouse.build_ads(dws, dwd)
    creative_tbl = warehouse.build_creative_table(acq_dwd, dwd)
    sub_channel_tbl = warehouse.build_sub_channel_table(acq_dwd, dwd)
    daily_detail = warehouse.build_daily_detail(dwd)

    print("\n完成！四张业务表已生成：")
    print(f"  日常表(系列级): {len(ads)} 行")
    print(f"  细粒度日常表: {len(daily_detail)} 行")
    print(f"  素材表: {len(creative_tbl)} 行")
    print(f"  子渠道表: {len(sub_channel_tbl)} 行")


if __name__ == "__main__":
    main()
