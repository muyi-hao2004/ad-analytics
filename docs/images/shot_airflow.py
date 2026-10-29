"""登录 Airflow 并截取关键页面，用于 README / 简历展示。

为什么单独写脚本而不是手截：
  界面一改就得重截一遍。脚本化之后重跑即可，尺寸和缩放始终一致，
  不会出现「这张图是 80% 缩放、那张是 100%」的尴尬。

用法：
  py docs/images/shot_airflow.py            # 截 DAG 列表 + Graph 视图
  py docs/images/shot_airflow.py --run-page # 额外截每次运行的任务状态页
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://localhost:8080"
DAG_ID = "iaa_warehouse_daily"
OUT = Path(__file__).resolve().parent  # docs/images/

# 凭据从环境变量读，不留字面量在仓库里。
# 默认值 admin/admin 是 Airflow 官方 docker-compose 的开发账号，
# 换了部署环境时用 AIRFLOW_USER / AIRFLOW_PASSWORD 覆盖。
AIRFLOW_USER = os.environ.get("AIRFLOW_USER", "admin")
AIRFLOW_PASSWORD = os.environ.get("AIRFLOW_PASSWORD", "admin")


def _login(page: Page) -> None:
    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    if "login" in page.url:
        page.fill("#username", AIRFLOW_USER)
        page.fill("#password", AIRFLOW_PASSWORD)
        page.click("input[type=submit]")
        page.wait_for_load_state("networkidle", timeout=60000)


def _shot_graph(page: Page, out: Path) -> None:
    """截 Graph 视图。

    整页截图会把左侧的甘特面板一起带上，节点被压得很小，缩到 README 宽度后
    文字全成灰块。所以优先截 React Flow 的容器元素，只保留任务图本身。
    """
    page.goto(f"{BASE}/dags/{DAG_ID}/graph", wait_until="networkidle", timeout=90000)
    # React Flow 画布渲染完成前节点是空的，等第一个节点出现
    page.wait_for_selector(".react-flow__node", timeout=60000)
    page.wait_for_timeout(2500)

    canvas = page.query_selector(".react-flow")
    if canvas is not None:
        canvas.screenshot(path=str(out))
    else:
        page.screenshot(path=str(out), full_page=True)
    print("已保存", out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-page", action="store_true",
                    help="额外截一次运行详情页（需要已经有 run）")
    args = ap.parse_args()

    with sync_playwright() as pw:
        # 用系统已装的 Edge，不必再下一份 chromium
        browser = pw.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        _login(page)

        # 1) DAG 列表
        page.goto(f"{BASE}/home", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
        p1 = OUT / "airflow_dags.png"
        page.screenshot(path=str(p1), full_page=True)
        print("已保存", p1)

        # 2) 五层流水线的任务图
        _shot_graph(page, OUT / "airflow_graph.png")

        # 3) 运行详情（每次运行的 task 状态矩阵）
        if args.run_page:
            page.goto(f"{BASE}/dags/{DAG_ID}/runs", wait_until="networkidle",
                      timeout=60000)
            page.wait_for_timeout(1500)
            link = page.query_selector("table a[href*='/grid']")
            if link is not None:
                link.click()
                page.wait_for_load_state("networkidle", timeout=60000)
                page.wait_for_timeout(2500)
                p3 = OUT / "airflow_run.png"
                page.screenshot(path=str(p3), full_page=True)
                print("已保存", p3)
            else:
                print("没有找到 run 记录，跳过 run 页")

        browser.close()


if __name__ == "__main__":
    main()
