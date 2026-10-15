"""
抓取器工厂

根据渠道名，返回对应的抓取器实例。
上层代码只调 get_fetcher("unity").fetch_campaigns()，
不用关心内部是哪个渠道、怎么实现的。
"""

from typing import Dict
from .base import ConfigFetcher
from .unity import UnityFetcher
import sys
from pathlib import Path

# 让 import 能找到上一级的 config.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CHANNEL_CREDENTIALS  # noqa: E402


# 渠道名 → 抓取器类 的注册表
# 以后新增渠道（比如 MTG、AdMob），就在这里加一行，其余代码不用动
FETCHER_REGISTRY: Dict[str, type] = {
    "unity": UnityFetcher,
    # "mtg": MtgFetcher,       # 以后要接 MTG 时，写 MtgFetcher 并在这里注册
    # "admob": AdmobFetcher,
}


def get_fetcher(channel: str) -> ConfigFetcher:
    """
    根据渠道名返回抓取器实例。

    Args:
        channel: 渠道名，如 "unity"、"mtg"

    Returns:
        对应渠道的抓取器实例（已注入该渠道的账号密码）

    Raises:
        ValueError: 渠道未注册时
    """
    channel = channel.lower().strip()

    if channel not in FETCHER_REGISTRY:
        raise ValueError(f"未注册的渠道: {channel}，已支持: {list(FETCHER_REGISTRY.keys())}")

    # 从配置里取该渠道的账号密码
    cred = CHANNEL_CREDENTIALS.get(channel, {})
    fetcher_cls = FETCHER_REGISTRY[channel]

    return fetcher_cls(
        username=cred.get("username", ""),
        password=cred.get("password", ""),
    )
