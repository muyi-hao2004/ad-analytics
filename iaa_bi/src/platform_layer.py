"""
平台操作抽象层（依赖倒置的核心）

中台上的"操作"（删除子渠道、调预算、换素材…）需要真正改变广告平台的状态。
但"怎么操作"取决于平台有没有 API / 能不能连：

  - 官方 API（Meta/Google Marketing API）：后端直接调 HTTP 接口
  - Playwright 模拟（平台无 API）：浏览器自动化去后台点按钮
  - Mock（demo 阶段无真实平台）：直接改本地 dim.db，假装"调了平台"

这里把"平台操作"抽象成一组接口，用"策略"实现：
  MockPlatform   —— 现在用，改本地维度表 + 写操作日志
  UnityPlatform  —— 将来换，调真 API / Playwright

关键：后端（app.py）只依赖抽象接口，不关心具体是哪个平台。
将来有真平台了，写一个 UnityPlatform 实现同样的接口，后端一行不用改。

这就是"依赖倒置原则"(Dependency Inversion)：高层模块不依赖底层实现，
两者都依赖抽象接口。
"""

from typing import Optional, Dict, Any, List


# ============================================================
# 抽象接口（用 Python 的约定式定义，不强制 ABC，保持简单）
# ============================================================

class BasePlatform:
    """所有平台实现的统一接口。每个方法都对应中台上的一种"写操作"。

    返回值约定：每个操作返回一个 dict：
        {"ok": bool, "message": str}
    ok=True 表示操作成功，message 是人可读的结果描述。
    """

    def delete_sub_channel(self, sub_channel_id: str, operator: str,
                           app: Optional[str] = None, channel: Optional[str] = None,
                           reason: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def adjust_budget(self, campaign_id: str, country: str, new_budget: float,
                      operator: str, reason: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def adjust_target(self, campaign_id: str, country: str, new_target: float,
                      operator: str, reason: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError


# ============================================================
# Mock 平台：demo 阶段的默认实现（改本地 dim.db）
# ============================================================

class MockPlatform(BasePlatform):
    """假平台：不连任何真实广告平台，直接改本地维度表。

    这样"删除子渠道"在 demo 里是【真的落库生效】了——刷新报表子渠道就消失、
    操作日志也记了。只是"调平台"这一步被 mock 掉了。

    将来替换成 UnityPlatform 时，接口签名不变，后端无需改动。
    """

    def __init__(self):
        # 延迟导入，避免循环依赖（dim_scd 和 platform 互相独立）
        import dim_scd
        self._dim = dim_scd

    def delete_sub_channel(self, sub_channel_id: str, operator: str,
                           app: Optional[str] = None, channel: Optional[str] = None,
                           reason: Optional[str] = None) -> Dict[str, Any]:
        ok = self._dim.delete_sub_channel(
            sub_channel_id, operator=operator, app=app,
            channel=channel, reason=reason
        )
        if ok:
            return {"ok": True, "message": f"子渠道 {sub_channel_id} 已删除"}
        return {"ok": False, "message": f"子渠道 {sub_channel_id} 不存在或已删除"}

    def adjust_budget(self, campaign_id: str, country: str, new_budget: float,
                      operator: str, reason: Optional[str] = None) -> Dict[str, Any]:
        # 复用 upsert_campaign_country，传入 operator 让它在值变化时自动记日志
        app = None  # Mock 阶段不强制，真实平台可从 campaign 查
        channel = None
        self._dim.upsert_campaign_country(
            campaign_id, {"country": country, "budget": new_budget,
                          "target": None},
            operator=operator, app=app, channel=channel, reason=reason
        )
        return {"ok": True, "message": f"{campaign_id}/{country} 预算已调整为 ${new_budget}"}

    def adjust_target(self, campaign_id: str, country: str, new_target: float,
                      operator: str, reason: Optional[str] = None) -> Dict[str, Any]:
        self._dim.upsert_campaign_country(
            campaign_id, {"country": country, "budget": None,
                          "target": new_target},
            operator=operator, app=None, channel=None, reason=reason
        )
        return {"ok": True, "message": f"{campaign_id}/{country} 目标值已调整为 {new_target}"}


# ============================================================
# 平台工厂：按名称取平台实例
# ============================================================

_PLATFORM_REGISTRY: Dict[str, type] = {
    "mock": MockPlatform,
    # "unity": UnityPlatform,  # 将来有真平台时取消注释并实现
}


def get_platform(name: str = "mock") -> BasePlatform:
    """按名称返回平台实例（工厂模式）。默认 mock。"""
    cls = _PLATFORM_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知平台: {name}，可用: {list(_PLATFORM_REGISTRY)}")
    return cls()
