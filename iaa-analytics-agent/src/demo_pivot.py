"""
pivot + merge 演示脚本（教学用）

用几行极小的人工数据，演示双轴事实表（长表）怎么：
  1. pivot 成"留存宽表"、"收入宽表"
  2. 再 merge（join）成一张"日常表"（宽表）

目的：看懂"长表 → 宽表 → 拼成日常表"的完整机制，不追求数据真实。
"""

import pandas as pd


# ============================================================
# 第 0 步：造一份"双轴事实表"（长表，每个 cohort 的每天是一行）
# ============================================================
# 字段：install_date, day(第几天), app, active_users, ad_revenue
# 为了演示清楚，只保留最少的字段

long_table = pd.DataFrame([
    # install_date, day, app, active_users, ad_revenue
    {"install_date": "2026-10-01", "day": 0, "app": "A", "active_users": 1000, "ad_revenue": 20.0},
    {"install_date": "2026-10-01", "day": 1, "app": "A", "active_users": 420,  "ad_revenue": 15.0},
    {"install_date": "2026-10-01", "day": 2, "app": "A", "active_users": 300,  "ad_revenue": 10.0},
    {"install_date": "2026-10-02", "day": 0, "app": "A", "active_users": 800,  "ad_revenue": 18.0},
    {"install_date": "2026-10-02", "day": 1, "app": "A", "active_users": 360,  "ad_revenue": 12.0},
    {"install_date": "2026-10-02", "day": 2, "app": "A", "active_users": 250,  "ad_revenue": 8.0},
])

print("=" * 60)
print("第 0 步：双轴事实表（长表）—— 每个 cohort 的每天是一行")
print("=" * 60)
print(long_table.to_string(index=False))
print()


# ============================================================
# 第 1 步：pivot 成"留存宽表"（行=install_date，列=d0/d1/d2）
# ============================================================
# pivot 三要素：
#   index（行）= install_date + app
#   columns（列）= day（0,1,2 → 会变成 d0,d1,d2）
#   values（值）= active_users

# 先给 day 加个 "d" 前缀，让列名好看（0 → "d0"）
long_table["day_label"] = "d" + long_table["day"].astype(str)

retention_wide = long_table.pivot_table(
    index=["install_date", "app"],   # 行：每个安装日期+包 一行
    columns="day_label",             # 列：d0, d1, d2
    values="active_users",           # 值：填活跃人数
).reset_index()

print("=" * 60)
print("第 1 步：pivot 成「留存宽表」（active_users 按 day 横铺）")
print("=" * 60)
print(retention_wide.to_string(index=False))
print()


# ============================================================
# 第 2 步：pivot 成"收入宽表"（同样三要素，值换成 ad_revenue）
# ============================================================
revenue_wide = long_table.pivot_table(
    index=["install_date", "app"],
    columns="day_label",
    values="ad_revenue",
).reset_index()

# 给收入列加个后缀 _rev，避免和留存列名 d0/d1/d2 冲突
revenue_wide.columns = [
    col if col in ("install_date", "app") else f"{col}_rev"
    for col in revenue_wide.columns
]

print("=" * 60)
print("第 2 步：pivot 成「收入宽表」（ad_revenue 按 day 横铺，列名加 _rev）")
print("=" * 60)
print(revenue_wide.to_string(index=False))
print()


# ============================================================
# 第 3 步：merge（join）—— 两张宽表按 install_date+app 拼成日常表
# ============================================================
daily_table = pd.merge(
    retention_wide,          # 左表：留存宽表
    revenue_wide,            # 右表：收入宽表
    on=["install_date", "app"],  # 共同的 key（这是"拼"的依据）
    how="left",              # 左连接（保留左表所有行）
)

print("=" * 60)
print("第 3 步：merge 成「日常表」（留存 + 收入 按 install_date+app 拼一起）")
print("=" * 60)
print(daily_table.to_string(index=False))
print()


# ============================================================
# 第 4 步：算派生指标（只存原子，派生用时算）
# ============================================================
# 比如 D0 留存率 = d0_active / installs（这里没有 installs，演示用 d0_active 当分母示意）
# 实际项目里，留存率 = active_users / installs，这里只演示"派生指标是算出来的"

print("=" * 60)
print("第 4 步：说明「派生指标用时算」—— 列都是原始值拼出来，指标是除出来的")
print("=" * 60)
print("例如：D1 留存率 = d1_active / installs（安装数）")
print("      D1 eCPM = d1_rev / (d1 广告展示) * 1000")
print("      这些不存表，要用时现算")
