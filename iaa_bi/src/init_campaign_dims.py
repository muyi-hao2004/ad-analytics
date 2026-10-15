"""
补全维度表 dim_campaign 的系列数据。

报表需要「策略(strategy)」这个筛选维度，而 strategy 存在维度表 dim_campaign 里，
不是 API 推来的结果数据。这里把 5 个系列的 strategy/owner/channel 写进维度表，
供报表 join。

注意：这是"配置数据"通道（优化师在平台后台设置 → 抓取/录入维度表），
与结果数据通道（API 推送 → 四层数仓）分开。用 operator=None 静默写入，不污染操作日志。

运行：
    cd iaa_bi
    venv/Scripts/python.exe src/init_campaign_dims.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import dim_scd


# 5 个系列的配置（与 mock_pages/campaign_detail.html 里的 DATA 对应）
CAMPAIGNS = [
    {"campaign_id": "US_A_01", "campaign_name": "US_A_01",
     "strategy": "D7_ROAS", "owner": "张三", "channel": "unity"},
    {"campaign_id": "US_B_01", "campaign_name": "US_B_01",
     "strategy": "CPI", "owner": "李四", "channel": "unity"},
    {"campaign_id": "JP_A_01", "campaign_name": "JP_A_01",
     "strategy": "D0_ROAS", "owner": "王五", "channel": "mtg"},
    {"campaign_id": "CA_A_01", "campaign_name": "CA_A_01",
     "strategy": "D7_ROAS", "owner": "赵六", "channel": "admob"},
    {"campaign_id": "GB_A_01", "campaign_name": "GB_A_01",
     "strategy": "CPI", "owner": "孙七", "channel": "mtg"},
]


def main():
    dim_scd.init_dim_tables()

    for c in CAMPAIGNS:
        # as_of 用 2026-10-01（报表数据从这天开始），operator=None 静默写入
        dim_scd.upsert_campaign(c, as_of="2026-10-01")

    print("维度表已补全，当前生效的系列：")
    for c in dim_scd.list_all_current():
        print(f"  {c['campaign_id']} | 策略 {c['strategy']} | 投放人 {c['owner']} | 渠道 {c['channel']}")


if __name__ == "__main__":
    main()
