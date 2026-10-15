"""
配置抓取演示脚本

运行后，会看到浏览器自动打开「假 Unity 后台」→ 遍历系列 → 抓取配置。
这是「配置数据独立接入」的演示：结果数据和配置数据走两条通道。

运行方式：
    cd iaa_bi
    venv/Scripts/python.exe src/fetch_config_demo.py
"""

import sys
from pathlib import Path

# 让 import 能找到 src 下的模块
sys.path.insert(0, str(Path(__file__).parent))

from fetchers.factory import get_fetcher  # noqa: E402


def main():
    print("=" * 60)
    print("配置抓取演示：模拟从 Unity 后台抓取广告系列配置")
    print("=" * 60)
    print("即将打开浏览器，自动抓取系列配置...")
    print()

    # 获取 Unity 渠道的抓取器（账号密码已从 .env 读入）
    fetcher = get_fetcher("unity")
    print(f"渠道: {fetcher.channel_name}")
    print(f"账号: {fetcher.username}")
    print()

    # 抓取配置（会看到浏览器自动操作）
    campaigns = fetcher.fetch_campaigns()

    # 打印抓取结果
    print("\n" + "=" * 60)
    print(f"抓取完成，共 {len(campaigns)} 个系列")
    print("=" * 60)
    for c in campaigns:
        print(f"\n系列: {c['campaign_name']}")
        print(f"  策略: {c['strategy']} | 投放人: {c['owner']} | 渠道: {c['channel']}")
        for country in c["countries"]:
            print(f"    国家 {country['country']}: 预算 ${country['budget']}, 目标值 {country['target']}")
        print(f"  素材: {c['creatives']}")


if __name__ == "__main__":
    main()
