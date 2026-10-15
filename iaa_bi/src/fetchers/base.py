"""
抓取器抽象基类

定义"配置抓取器"的统一接口：所有渠道（Unity/MTG/AdMob）的抓取器，
都必须实现 fetch_campaigns() 方法，返回统一的配置数据结构。

这样上层代码调用时，不需要关心是哪个渠道，只要调 fetch_campaigns() 就行。
这就是「策略模式」：同一件事（抓配置），不同渠道不同实现，但接口统一。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any


class ConfigFetcher(ABC):
    """配置抓取器抽象基类。所有渠道抓取器继承它。"""

    # 渠道名（子类覆盖）
    channel_name: str = ""

    def __init__(self, username: str, password: str):
        """
        Args:
            username: 该渠道的登录账号
            password: 该渠道的登录密码（从 .env 读入，不硬编码）
        """
        self.username = username
        self.password = password

    @abstractmethod
    def fetch_campaigns(self) -> List[Dict[str, Any]]:
        """
        抓取该渠道下的所有广告系列配置。

        Returns:
            配置列表，每个元素是一个系列配置 dict，统一结构：
            {
                "campaign_id": str,       # 系列ID
                "campaign_name": str,     # 系列名
                "strategy": str,          # 策略类型：D7_ROAS / D0_ROAS / CPI
                "owner": str,             # 投放人
                "channel": str,           # 渠道
                "countries": [            # 国家配置（系列×国家）
                    {"country": "US", "budget": 1000.0, "target": 0.70},
                    ...
                ],
                "creatives": [str, ...],  # 已选素材ID列表
            }
        """
        raise NotImplementedError


def validate_config(config: Dict[str, Any]) -> bool:
    """校验一条系列配置结构是否完整（防御式检查）。"""
    required = ["campaign_id", "campaign_name", "strategy", "owner", "channel", "countries"]
    return all(k in config for k in required)
