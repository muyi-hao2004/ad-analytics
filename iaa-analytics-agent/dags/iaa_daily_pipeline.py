"""
IAA 广告变现中台 —— 每日数据管道 DAG

这个 DAG 把两个原本手动跑的任务编排成一条有依赖关系的流水线：
  1. update_warehouse：更新数据 + 重跑四层数仓（prepare_report_data.main）
  2. run_agent：数据更新完后，跑 Agent 多轮决策（agent.run_agent）

关键点：`update_warehouse >> run_agent` 这一行，就是 Airflow 的「灵魂」——
它表达了「先更新数据、再跑 Agent」的依赖关系，Airflow 自动保证顺序。

Airflow 相比 asyncio 定时，多出的能力：
  - 任务依赖（DAG 天然表达，而非手动排）
  - 失败重试（retries=3，retry_delay=5分钟）
  - 可视化（Web UI 看每次运行状态、日志）
  - 历史回溯（backfill 补跑历史日期）
"""

from datetime import datetime, timedelta
import os
import sys
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

# 把 src 加进 sys.path，让 DAG 能 import 项目的 src 模块。
# 兼容两种运行环境：
#   1. Docker 容器内：整个项目挂载在 /opt/airflow/iaa-analytics-agent_src，src 在其下
#   2. 本地 Windows：dags 的上一级（iaa-analytics-agent）下就是 src
_SRC_CANDIDATES = [
    Path("/opt/airflow/iaa-analytics-agent/src"),                            # Docker 容器
    Path(__file__).resolve().parent.parent / "src",            # 本地
]
for _cand in _SRC_CANDIDATES:
    if _cand.exists():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

import prepare_report_data  # noqa: E402
import agent  # noqa: E402


# ---- 两个任务的具体实现（Airflow 会调这些函数）----

def _update_warehouse(**context):
    """
    任务1：更新数据 + 重跑四层数仓。
    真实场景这里是「增量更新」，demo 阶段是全量重跑（prepare_report_data.main）。
    """
    print(f"[{context['ds']}] 开始更新数仓数据...")
    prepare_report_data.main()
    print(f"[{context['ds']}] 数仓更新完成，四张业务表已刷新。")


def _run_agent(**context):
    """
    任务2：数据更新完后，跑 Agent 多轮决策（真正执行，非 dry_run）。
    max_rounds 放宽到 15：Agent 逐个渠道下钻趋势+日志时，8 轮不够查完所有渠道再下结论。
    """
    print(f"[{context['ds']}] 开始跑 Agent 决策...")
    result = agent.run_agent(dry_run=False, max_rounds=15)
    print(f"[{context['ds']}] Agent 决策完成。")
    print(f"  结论: {result.get('conclusion', '')}")
    print(f"  执行动作数: {len(result.get('executed', []))}")
    return result


# ---- DAG 定义 ----

default_args = {
    "owner": "iaa-bi",
    "retries": 3,                      # 失败重试 3 次
    "retry_delay": timedelta(minutes=5),  # 每次重试间隔 5 分钟
    "depends_on_past": False,           # 不依赖上一次运行是否成功
}

dag = DAG(
    dag_id="iaa_daily_pipeline",
    default_args=default_args,
    description="IAA 中台每日数据管道：更新数仓 → 跑 Agent 决策",
    schedule="0 8 * * *",              # 每天 08:00 跑（cron 表达式）
    start_date=datetime(2026, 10, 14),
    catchup=False,                      # 不回溯补跑历史日期
    tags=["iaa", "daily"],
)

# ---- 两个 task ----

update_warehouse = PythonOperator(
    task_id="update_warehouse",
    python_callable=_update_warehouse,
    dag=dag,
)

run_agent = PythonOperator(
    task_id="run_agent",
    python_callable=_run_agent,
    dag=dag,
)

# ---- 关键：定义依赖关系 ----
# update_warehouse 成功 → 才跑 run_agent
update_warehouse >> run_agent
