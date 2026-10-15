"""
ODS → DWD 清洗演示（教学用）

用一份手工造的脏数据，演示 DWD 层的 5 条清洗规则怎么把脏数据洗干净：
  1. 去重（完全相同的行）
  2. 补空（NaN → 0 / unknown）
  3. 修负值（clip 到 0）
  4. 日期标准化
  5. 类型规范

运行：
    cd iaa_bi
    venv/Scripts/python.exe src/demo_warehouse.py
"""

import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import config
import warehouse


def make_dirty_data() -> pd.DataFrame:
    """手工造一份带各种脏数据的原始数据（模拟造数脚本埋的脏数据）。"""
    rows = [
        # 正常行
        {"install_date": "2026-10-01", "report_date": "2026-10-01",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 1000, "active_users": 420, "ad_impressions": 5000,
         "rewarded_views": 800, "ad_revenue": 20.5, "spend": 30.0},
        # 重复行（和上面一模一样，应被去重删掉）
        {"install_date": "2026-10-01", "report_date": "2026-10-01",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 1000, "active_users": 420, "ad_impressions": 5000,
         "rewarded_views": 800, "ad_revenue": 20.5, "spend": 30.0},
        # 空值行（度量字段缺失，应为 0）
        {"install_date": "2026-10-01", "report_date": "2026-10-01",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_002", "creative_id": "crt_002",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": None, "active_users": None, "ad_impressions": None,
         "rewarded_views": None, "ad_revenue": None, "spend": None},
        # 负值行（收入/花费为负，应 clip 到 0）
        {"install_date": "2026-10-01", "report_date": "2026-10-01",
         "app": "money_clicker", "country": "US", "campaign": "US_B_01",
         "sub_channel": "ch_003", "creative_id": "crt_003",
         "placement": "interstitial", "ad_network": "MTG",
         "installs": 500, "active_users": 200, "ad_impressions": 3000,
         "rewarded_views": 300, "ad_revenue": -5.0, "spend": -10.0},
        # 日期脏行（带时区/时间戳的字符串）
        {"install_date": "2026-10-02T00:00:00+08:00", "report_date": "2026-10-02 15:30:00",
         "app": "money_clicker", "country": "JP", "campaign": "US_C_01",
         "sub_channel": "ch_004", "creative_id": "crt_004",
         "placement": "banner", "ad_network": "AdMob",
         "installs": 800, "active_users": 300, "ad_impressions": 4000,
         "rewarded_views": 500, "ad_revenue": 15.0, "spend": 25.0},
        # 字段类型脏行（数字写成字符串）
        {"install_date": "2026-10-02", "report_date": "2026-10-02",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_005",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": "900", "active_users": "350", "ad_impressions": "4500",
         "rewarded_views": "600", "ad_revenue": "18.0", "spend": "28.0"},
    ]
    return pd.DataFrame(rows)


def main():
    config.ensure_dirs()

    print("=" * 60)
    print("原始脏数据（共 6 行，含各种脏数据）")
    print("=" * 60)
    dirty = make_dirty_data()
    print(dirty.to_string(index=False))
    print(f"\n共 {len(dirty)} 行\n")

    # 先写一份 CSV 到 raw，模拟"造数脚本产出的原始数据"
    raw_file = config.RAW_DIR / "demo_dirty.csv"
    dirty.to_csv(raw_file, index=False)
    print(f"已把脏数据写到 {raw_file}\n")

    # 走 ODS → DWD
    print("=" * 60)
    print("ODS（贴源层）：原样落地")
    print("=" * 60)
    ods = warehouse.build_ods(raw_file)
    print(f"ODS 行数: {len(ods)}（原样保留，含脏数据）\n")

    print("=" * 60)
    print("DWD（明细层）：清洗")
    print("=" * 60)
    dwd = warehouse.build_dwd(ods)

    print("\n" + "=" * 60)
    print("清洗后的双轴事实表（干净数据）")
    print("=" * 60)
    print(dwd.to_string(index=False))
    print(f"\n最终 {len(dwd)} 行（原来 6 行 → 去重删 1 行 = 5 行）")

    # 验证清洗效果
    print("\n" + "=" * 60)
    print("清洗效果核对")
    print("=" * 60)
    print(f"① 去重: {len(ods)} 行 → {len(dwd)} 行（删了 {len(ods)-len(dwd)} 行重复）")
    print(f"② 补空: ch_002 那行度量字段应为 0 → {dwd[dwd['sub_channel']=='ch_002'][['installs','ad_revenue']].values.tolist()}")
    print(f"③ 修负值: US_B_01 那行 spend 应为 0 → {dwd[dwd['campaign']=='US_B_01']['spend'].values.tolist()}")
    print(f"④ 日期: install_date 应统一 YYYY-MM-DD → {sorted(dwd['install_date'].dropna().astype(str).unique())}")
    print(f"⑤ 类型: installs 应为 int, ad_revenue 应为 float → {dwd['installs'].dtype}, {dwd['ad_revenue'].dtype}")

    # ============================================================
    # DWS 演示：长表 → 宽表（pivot）
    # ============================================================
    print("\n\n" + "=" * 60)
    print("DWS（汇总层）：长表 pivot 成 cohort 宽表")
    print("=" * 60)
    # 上面那份脏数据只有 2 天、day_offset 太少，这里造一份更丰富的干净数据演示 pivot
    print("（用一份更丰富的数据演示，覆盖 install_date=10-01/10-02/10-03，各持续 3 天）")

    rich = pd.DataFrame([
        # install_date=10-01 的 cohort，report_date 覆盖 10-01~10-03
        {"install_date": "2026-10-01", "report_date": "2026-10-01",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 1000, "active_users": 1000, "ad_impressions": 5000,
         "rewarded_views": 800, "ad_revenue": 20.0, "spend": 30.0},
        {"install_date": "2026-10-01", "report_date": "2026-10-02",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 1000, "active_users": 420, "ad_impressions": 3000,
         "rewarded_views": 500, "ad_revenue": 15.0, "spend": 0.0},
        {"install_date": "2026-10-01", "report_date": "2026-10-03",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 1000, "active_users": 300, "ad_impressions": 2000,
         "rewarded_views": 350, "ad_revenue": 10.0, "spend": 0.0},
        # install_date=10-02 的 cohort
        {"install_date": "2026-10-02", "report_date": "2026-10-02",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 800, "active_users": 800, "ad_impressions": 4000,
         "rewarded_views": 600, "ad_revenue": 18.0, "spend": 28.0},
        {"install_date": "2026-10-02", "report_date": "2026-10-03",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 800, "active_users": 360, "ad_impressions": 2500,
         "rewarded_views": 400, "ad_revenue": 12.0, "spend": 0.0},
        # install_date=10-03 的 cohort（只有当天，day0）
        {"install_date": "2026-10-03", "report_date": "2026-10-03",
         "app": "money_clicker", "country": "US", "campaign": "US_A_01",
         "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "installs": 900, "active_users": 900, "ad_impressions": 4500,
         "rewarded_views": 700, "ad_revenue": 16.0, "spend": 26.0},
    ])

    print("\nDWD 长表（每个 cohort 每天一行，注意 install_date 相同的多行）：")
    print(rich[["install_date", "report_date", "active_users", "ad_revenue"]].to_string(index=False))

    dwd_rich = warehouse.build_dwd(rich)
    print("\nDWS 宽表（每个 install_date 一行，d0/d1/d2 横铺）：")
    dws = warehouse.build_dws(dwd_rich)
    # 只展示关键列，避免太宽
    show_cols = ["install_date", "app", "country", "campaign"] + \
                [c for c in dws.columns if c.startswith("d")]
    print(dws[show_cols].to_string(index=False))

    print("\n解读：")
    print("  10-01 这行的 dact0=1000, dact1=420, dact2=300  → 表示这批用户安装当天活跃1000，第1天剩420，第2天剩300")
    print("  10-03 这行只有 dact0=900（因为才装了1天，d1/d2 还没数据，是 0）")

    # ============================================================
    # ADS 演示：派生指标 + 日常表
    # ============================================================
    print("\n\n" + "=" * 60)
    print("ADS（应用层）：算派生指标，拼成日常表")
    print("=" * 60)
    ads = warehouse.build_ads(dws, dwd_rich)

    print("\n日常表（派生指标列）：")
    # 展示维度 + installs + spend + 派生指标
    show_cols = ["install_date", "app", "country", "campaign", "installs",
                 "spend_total", "cpi"] + \
                [c for c in ads.columns if c.startswith(("ret_", "ecpm_", "view_", "roi_"))]
    print(ads[show_cols].round(3).to_string(index=False))

    print("\n解读（以 10-01 为例）：")
    row = ads[ads["install_date"] == "2026-10-01"].iloc[0]
    print(f"  D1 留存率 ret_1 = {row['ret_1']:.3f}  → 第1天还剩 42% 的用户")
    print(f"  D0 eCPM ecpm_0 = {row['ecpm_0']:.2f}  → 每千次展示赚 {row['ecpm_0']:.2f} 美元")
    print(f"  D2 ROI roi_2 = {row['roi_2']:.3f}  → 到第2天累计收入/花费 = {(row['drev0']+row['drev1']+row['drev2'])/row['spend_total']:.3f}")
    print(f"  CPI = {row['cpi']:.3f}  → 每装一个用户花 {row['cpi']:.3f} 美元")

    # ============================================================
    # 买量日表演示：前端单轴数据，与双轴事实表分开
    # ============================================================
    print("\n\n" + "=" * 60)
    print("买量日表（前端，单轴 report_date）—— 与双轴事实表分开")
    print("=" * 60)
    print("（买量是'投放当天的动作'，没有 cohort 概念，时间轴只有 report_date）")

    # 造一份带买量字段的原始数据
    acq_raw = pd.DataFrame([
        {"report_date": "2026-10-01", "app": "money_clicker", "country": "US",
         "campaign": "US_A_01", "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "clicks": 2000, "buy_impressions": 20000, "installs": 1000, "spend": 30.0},
        {"report_date": "2026-10-01", "app": "money_clicker", "country": "US",
         "campaign": "US_A_01", "sub_channel": "ch_002", "creative_id": "crt_002",
         "placement": "rewarded", "ad_network": "Unity",
         "clicks": 500, "buy_impressions": 5000, "installs": 200, "spend": 8.0},
        {"report_date": "2026-10-02", "app": "money_clicker", "country": "US",
         "campaign": "US_A_01", "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "clicks": 1800, "buy_impressions": 18000, "installs": 800, "spend": 28.0},
    ])

    acq = warehouse.build_acquisition(acq_raw)
    print("\n买量日表（单轴，只有 report_date，没有 install_date）：")
    print(acq.to_string(index=False))

    print("\n解读：")
    print("  买量表没有 install_date（没有 cohort 概念），只有 report_date")
    print("  前端指标：买量 CTR = clicks / buy_impressions，CVR = installs / clicks")
    print("  crt_001 的 CTR = 2000/20000 = 10%，CVR = 1000/2000 = 50%")
    print("  这些买量数据要跟后端结合时，用 creative_id / sub_channel_id 做 join")

    # ============================================================
    # 素材前后端表：前端 + 后端 join
    # ============================================================
    print("\n\n" + "=" * 60)
    print("素材前后端结合表：前端花费 + 后端 D7 表现")
    print("=" * 60)
    print("（crt_001 表现好，crt_002 表现差，演示'判定换素材'）")

    # 构造配套的事实表数据：crt_001 用户留存好、crt_002 用户留存差
    # 这里只演示 join 机制，D7 需要 8 天数据，用 day_offset 0~7 模拟
    fact_rows = []
    import itertools
    for creative, (base_install, rev_mult, ret_decay) in {
        "crt_001": (1000, 1.0, 0.7),   # 表现好
        "crt_002": (200, 0.3, 0.5),    # 表现差
    }.items():
        sub_ch = "ch_001" if creative == "crt_001" else "ch_002"
        for day in range(8):  # day 0~7
            active = int(base_install * (ret_decay ** day))
            fact_rows.append({
                "install_date": "2026-10-01", "report_date": f"2026-10-{day+1:02d}",
                "app": "money_clicker", "country": "US", "campaign": "US_A_01",
                "sub_channel": sub_ch, "creative_id": creative,
                "placement": "rewarded", "ad_network": "Unity",
                "installs": base_install, "active_users": active,
                "ad_impressions": active * 5, "rewarded_views": int(active * 0.8),
                "ad_revenue": round(active * 0.02 * rev_mult, 2),
                "spend": 30.0 if day == 0 else 0.0,
            })

    fact_df = pd.DataFrame(fact_rows)
    dwd_full = warehouse.build_dwd(fact_df)

    # 前端买量表（crt_001 和 crt_002 各花多少钱）
    acq_full = pd.DataFrame([
        {"report_date": "2026-10-01", "app": "money_clicker", "country": "US",
         "campaign": "US_A_01", "sub_channel": "ch_001", "creative_id": "crt_001",
         "placement": "rewarded", "ad_network": "Unity",
         "clicks": 2000, "buy_impressions": 20000, "installs": 1000, "spend": 30.0},
        {"report_date": "2026-10-01", "app": "money_clicker", "country": "US",
         "campaign": "US_A_01", "sub_channel": "ch_002", "creative_id": "crt_002",
         "placement": "rewarded", "ad_network": "Unity",
         "clicks": 800, "buy_impressions": 8000, "installs": 200, "spend": 30.0},
    ])
    acq_full = warehouse.build_acquisition(acq_full)

    creative_tbl = warehouse.build_creative_table(acq_full, dwd_full)
    print("\n素材前后端表（前端 + 后端 D7）：")
    show = ["creative_id", "install_date", "spend", "ctr", "cvr", "cpi",
            "d7_retention", "d7_roi", "d7_ecpm"]
    print(creative_tbl[show].round(3).to_string(index=False))

    print("\n判定：")
    print("  crt_001：D7 ROI 高（用户留存好），前端花费 30 值回票价 → 保留")
    print("  crt_002：D7 ROI 低（用户留存差），前端也花 30 → 该换素材")
    print("  这就是素材前后端表的价值：前端'花了多少' + 后端'赚回多少'一起看")


if __name__ == "__main__":
    main()
