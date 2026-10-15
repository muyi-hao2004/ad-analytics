"""
Unity 渠道的配置抓取器（用 Playwright 浏览器自动化）

模拟真实场景：登录 Unity 后台 → 打开系列列表 → 逐个进入系列详情 →
抓取国家配置和素材选择。

demo 里抓的是本地假页面（mock_pages/），真实环境把 URL 换成 Unity 后台地址即可。
"""

from typing import List, Dict, Any
from pathlib import Path
from .base import ConfigFetcher, validate_config


class UnityFetcher(ConfigFetcher):
    """Unity 渠道抓取器。"""

    channel_name = "unity"

    # 假页面的本地路径（真实环境换成 Unity 后台 URL）
    MOCK_LIST_URL = (Path(__file__).parent.parent.parent / "mock_pages" / "campaign_list.html").as_uri()
    MOCK_DETAIL_URL = (Path(__file__).parent.parent.parent / "mock_pages" / "campaign_detail.html").as_uri()

    def fetch_campaigns(self) -> List[Dict[str, Any]]:
        """
        抓取 Unity 后台的所有系列配置。
        用 Playwright 打开浏览器，模拟人工操作流程。
        """
        from playwright.sync_api import sync_playwright

        campaigns = []

        with sync_playwright() as p:
            # 有头模式：你能看到浏览器窗口自动打开、自动操作
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            # 第 1 步：打开"系列列表页"（真实环境这里先走登录流程）
            page.goto(self.MOCK_LIST_URL)

            # 第 2 步：抓取列表里的所有系列（每行的系列名和策略类型）
            # 用 CSS 选择器定位表格的每一行
            rows = page.locator("#campaign-table tr").all()

            # 跳过表头（第一行是 <th>），从第二行开始
            for row in rows[1:]:
                # 抓取这一行的系列名、策略类型、详情链接
                campaign_name = row.locator("td").nth(0).inner_text()
                strategy = row.locator("td").nth(1).inner_text()

                # 第 3 步：点进系列详情页
                row.locator("td a").click()
                page.wait_for_load_state()

                # 第 4 步：抓取详情页的国家配置
                countries = self._parse_countries(page)

                # 第 5 步：抓取已选素材
                creatives = self._parse_creatives(page)

                campaigns.append({
                    "campaign_id": f"{self.channel_name}_{campaign_name}",
                    "campaign_name": campaign_name,
                    "strategy": strategy,
                    "owner": page.locator(".meta").inner_text().split("投放人：")[-1].strip(),
                    "channel": self.channel_name,
                    "countries": countries,
                    "creatives": creatives,
                })

                # 返回列表页，继续下一个系列
                page.go_back()

            browser.close()

        # 校验并返回
        valid = [c for c in campaigns if validate_config(c)]
        return valid

    def _parse_countries(self, page) -> List[Dict[str, Any]]:
        """抓取详情页的「国家 × 预算 × 目标值」配置。"""
        countries = []
        rows = page.locator("#country-config-table tr").all()
        for row in rows[1:]:  # 跳过表头
            country = row.locator("td").nth(0).inner_text()
            budget = row.locator("td").nth(1).inner_text()
            target = row.locator("td").nth(2).inner_text()

            # 目标值转成数值：ROAS 填 "70%" → 0.70；CPI 填 "0.5" → 0.5
            if target.endswith("%"):
                target_value = float(target.rstrip("%")) / 100
            else:
                target_value = float(target)

            countries.append({
                "country": country,
                "budget": float(budget),
                "target": target_value,
            })
        return countries

    def _parse_creatives(self, page) -> List[str]:
        """抓取详情页里「勾选的素材」。"""
        selected = page.locator("input.creative:checked").all()
        return [box.get_attribute("value") for box in selected]
