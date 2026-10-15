"""
IAA 广告变现分析中台 —— 配置模块

统一管理项目的路径、字段定义、业务常量。
所有后续模块（造数、数仓分层、pivot、报表、Agent）都从这里引用，
保证字段名、路径、口径全局统一，改一处全改。

与数据字典 data_dictionary.md 一一对应，两者互为参照。
"""

from pathlib import Path

# ============================================================
# 路径常量
# ============================================================
# 项目根目录（iaa-analytics-agent/）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"                       # 原始仿真数据
WAREHOUSE_DIR = DATA_DIR / "warehouse"           # 数仓四层

# 数仓各层目录
ODS_DIR = WAREHOUSE_DIR / "ods"                  # 贴源层
DWD_DIR = WAREHOUSE_DIR / "dwd"                  # 明细层
DWS_DIR = WAREHOUSE_DIR / "dws"                  # 汇总层
ADS_DIR = WAREHOUSE_DIR / "ads"                  # 应用层

# 报表目录
REPORTS_DIR = PROJECT_ROOT / "reports"


# ============================================================
# 双轴事实表的字段定义（与 data_dictionary.md 对应）
# ============================================================

# 日期轴（双轴，这是 IAA 变现数据建模的核心）
DATE_AXIS = [
    "install_date",   # 安装日期：这批用户哪天安装（cohort 锚点）
    "report_date",    # 回传日期：这笔数据哪天发生
]

# 维度字段（谁、在哪、什么渠道/素材/广告位）
DIMENSION_COLUMNS = [
    "app",            # 游戏包
    "country",        # 国家
    "campaign",       # 投放系列
    "sub_channel",    # 子渠道（归因来源）
    "creative_id",    # 素材ID（last-click 归因）
    "placement",      # 广告位（激励视频/插屏/banner）
    "ad_network",     # 广告平台（Unity/MTG/AdMob 等）
]

# 度量字段（原子数据，只存原始值，派生指标用时算）
# 注意：这是「双轴事实表」的字段，只含"变现侧/cohort 口径"的度量。
# 买量侧字段（spend/clicks/buy_impressions/installs）单独建买量表，不混在这里。
MEASURE_COLUMNS = [
    "installs",       # 安装总数（cohort 分母，也是买量侧 installs 的 cohort 锚点）
    "active_users",   # 当天活跃人数（留存分子）
    "ad_impressions", # 变现侧广告展示次数（变现 eCPM 分母）
    "rewarded_views", # 激励视频观看次数（观次分子）
    "ad_revenue",     # 当天广告收入
    "spend",          # 当天花费（ROI 分母，= 买量侧 spend 的 cohort 摊销）
]

# 买量侧字段（Acquisition，单独建「买量日表」，时间轴只有 report_date）
# 关键：买量是"投放当天的动作"，不是 cohort 概念，时间轴与双轴事实表不同
ACQUISITION_COLUMNS = [
    "clicks",           # 买量侧点击次数（CVR 分母）
    "buy_impressions",  # 买量侧广告展示次数（买量 CTR 分母）
]

# 双轴事实表字段（15 个）
FACT_COLUMNS = DATE_AXIS + DIMENSION_COLUMNS + MEASURE_COLUMNS

# 买量日表字段（单轴：report_date + 维度 + 买量度量）
ACQUISITION_TABLE_COLUMNS = ["report_date"] + DIMENSION_COLUMNS + ACQUISITION_COLUMNS + ["installs", "spend"]

# 全部字段（事实表 + 买量表）
ALL_COLUMNS = FACT_COLUMNS + ACQUISITION_COLUMNS


# ============================================================
# 业务常量（维度取值 + 目标）
# ============================================================

# 广告位类型
PLACEMENTS = [
    "rewarded",   # 激励视频（当前主流）
    "interstitial",  # 插屏
    "banner",     # 横幅
]

# 广告平台（变现端接的广告网络）
AD_NETWORKS = [
    "Unity",
    "MTG",        # Mintegral
    "AdMob",
    "AppLovin",
    "ironSource",
]

# D7 ROAS 目标（业务红线，Agent 判断"要不要优化"的基准）
D7_ROAS_TARGET = 0.70   # 70%


# ============================================================
# 渠道账号配置（各广告平台的登录凭据，从 .env 读取，不硬编码）
# ============================================================
# 注意：真实密码绝不写进代码，放在 iaa-analytics-agent/.env 里，通过环境变量读。
# 不同渠道有不同账号密码，所以按渠道名分组。

import os
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")   # 加载 iaa-analytics-agent/.env（如果存在）

# 渠道 → 登录配置 的映射
CHANNEL_CREDENTIALS = {
    "unity": {
        "username": os.getenv("UNITY_USERNAME", "zhangsan@example.com"),
        "password": os.getenv("UNITY_PASSWORD", ""),
    },
    "mtg": {
        "username": os.getenv("MTG_USERNAME", "lisi@example.com"),
        "password": os.getenv("MTG_PASSWORD", ""),
    },
    "admob": {
        "username": os.getenv("ADMOB_USERNAME", "wangwu@example.com"),
        "password": os.getenv("ADMOB_PASSWORD", ""),
    },
}


# ============================================================
# 投放策略类型（创建系列时选择，固定几种）
# ============================================================
STRATEGY_TYPES = [
    "D7_ROAS",   # 按第 7 天 ROAS 优化（目标值填百分比）
    "D0_ROAS",   # 按首日 ROAS 优化（目标值填百分比）
    "CPI",       # 按安装成本优化（目标值填美元）
]


def ensure_dirs() -> None:
    """确保所有需要的目录都存在（幂等，重复调用无害）。"""
    for d in (RAW_DIR, ODS_DIR, DWD_DIR, DWS_DIR, ADS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    # 直接运行本文件时，打印配置概览，方便自检
    ensure_dirs()
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"字段总数: {len(ALL_COLUMNS)}")
    print(f"  日期轴: {len(DATE_AXIS)} -> {DATE_AXIS}")
    print(f"  维度: {len(DIMENSION_COLUMNS)} -> {DIMENSION_COLUMNS}")
    print(f"  度量: {len(MEASURE_COLUMNS)} -> {MEASURE_COLUMNS}")
    print(f"广告位: {PLACEMENTS}")
    print(f"广告平台: {AD_NETWORKS}")
    print(f"D7 ROAS 目标: {D7_ROAS_TARGET:.0%}")
