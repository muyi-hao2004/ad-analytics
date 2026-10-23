# -*- coding: utf-8 -*-
"""IAA 数仓五层每日流水线（幂等版）

和 `iaa_daily_pipeline.py` 的区别
--------------------------------
  - `iaa_daily_pipeline.py`：数仓刷新 → 跑 Agent 决策，是**应用侧**的串行流程
  - 本文件：ODS → DWD → 买量 → DWS → ADS 五层**数仓侧**流水线，每层后面挂质量门禁；
    末尾再把中台写的决策日志反向同步进数仓（见 task_sync_decision_log）

幂等（Idempotency）到底指什么
----------------------------
一句话：**同一天跑 1 次和跑 10 次，表里的数据完全一样。**

这不是「不出错」的同义词。非幂等的流水线跑一次是对的，重跑就错——
最典型的失败长这样：某天任务超时，人手动补跑一次，
于是那天的花费被算了两遍，ROI 直接减半，而报表不会报任何错。

本 DAG 靠三层机制保证幂等（缺一层都不够）：

  1. **写入用分区覆盖，不用追加**
     `INSERT OVERWRITE ... PARTITION (dt)` + `partitionOverwriteMode=dynamic`
     → 只替换本次涉及的分区，其余分区一个字节不动

  2. **单日重建时，随机数种子与全量跑时一致**
     造数脚本的 uid / 素材都由 gid 派生，gid 按「第几天 × 每天人数」编号。
     所以 `--dt X` 必须从第 0 天开始生成再过滤到 X —— 否则同一个人会被
     重新分配到另一个素材，补一次数就把历史归因改掉了（数据看起来正常，但全错）

  3. **质量门禁：断言从 metrics.yaml 读，不写在代码里**
     重跑是否真的产出了同样的结果，靠断言证明，而不是靠「没报错」

为什么 max_active_runs=1 不能省
--------------------------------
ADS 层和 DWS 的三张全量表是**全量重建**（一次性 INSERT OVERWRITE 整表，
不按分区）。如果两个 ds 的 DAG 并发跑，后提交的那个会把前一个的中间结果覆盖掉，
产出一份「半天的新数据 + 半天的旧数据」。所以必须串行。

真实生产里这一步用 Airflow Pool（capacity=1）更稳妥，
max_active_runs 只限制同一 DAG 的并发，Pool 能跨 DAG 限制。

怎么跑
------
  # 按调度日期跑（默认取逻辑日期 ds）
  airflow dags trigger iaa_warehouse_daily

  # 手动补某一天
  airflow dags trigger iaa_warehouse_daily --conf '{"ds": "2026-10-15"}'

  # 强制重算 ODS（默认已有分区就跳过）
  airflow dags trigger iaa_warehouse_daily --conf '{"ds": "2026-10-15", "redo_ods": true}'
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.exceptions import AirflowException
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator

# ============================================================
# 路径与解释器
# ============================================================
# 同一份 DAG 要能在两种布局下跑：
#   本地 Windows：dags/ 的上两级就是项目根（dags → iaa-analytics-agent → ad-analytics）
#   Docker 容器：DAG 被挂到 /opt/airflow/dags，项目根在 /opt/airflow/ad-analytics
# 用「找得到就用」的候选列表，而不是写死路径 —— 写死的那份一定会在另一种环境里炸。
_ROOT_CANDIDATES = [
    Path(__file__).resolve().parents[2],          # 本地
    Path("/opt/airflow/ad-analytics"),            # 容器
    Path("/opt/airflow"),                         # 容器（项目直接挂载在根下）
]
PROJECT_ROOT = next((p for p in _ROOT_CANDIDATES if (p / "scripts").is_dir()), _ROOT_CANDIDATES[0])
SCRIPTS = PROJECT_ROOT / "scripts"

# 数仓脚本跑在 .venv-spark 里（pyspark + pyyaml），不是 Airflow 自己的 venv。
# 用环境变量覆盖，方便容器里改成 /usr/local/bin/python。
PYTHON = os.environ.get(
    "IAA_PYTHON",
    str(PROJECT_ROOT / ".venv-spark" / "Scripts" / "python.exe"),
)

# ODS 分区目录（不连 Spark 就能判断「上游数据到没到」）
LAB_DB = Path(os.environ.get("IAA_LAB_DB", r"D:\warehouse\lab.db"))


# ============================================================
# 工具函数
# ============================================================


def _ds_of(context) -> str:
    """取本次运行的业务日期。

    优先级：手动传入的 conf.ds > Airflow 的逻辑日期 ds。
    为什么要留 conf 入口：补历史数据时逻辑日期是「今天」，但业务日期是那一天，
    两者必须能分开，否则补数会写到错误的分区。
    """
    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    return conf.get("ds") or context["ds"]


def _run(script: str, *args: str, timeout: int = 3600) -> str:
    """调一个数仓脚本，非零退出就 fail 掉这个 task。

    Args:
        script: scripts/ 下的文件名，如 "build_dwd_iaa.py"
        args: 传给脚本的参数
        timeout: 秒。超时视为失败（防止一个卡死的 task 拖垮整个调度）

    Returns:
        子进程的完整输出（日志会一并打到 Airflow task log）

    Raises:
        AirflowException: 脚本不存在 / 超时 / 退出码非 0
    """
    path = SCRIPTS / script
    if not path.exists():
        raise AirflowException(f"脚本不存在: {path}")

    cmd = [PYTHON, "-u", str(path), *args]

    # PYTHONIOENCODING 必须显式设成 utf-8：
    # Windows 下子进程默认按 cp936 输出，脚本里全是中文日志，
    # 不设会得到一堆乱码，排查时完全看不懂 task log。
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        # 脚本内部有 MSYS_NO_PATHCONV 依赖（Git Bash 会改写 /tmp 这类路径）
        "MSYS_NO_PATHCONV": "1",
    }

    print(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired as e:
        raise AirflowException(f"{script} 超时（>{timeout}s）") from e

    out = (proc.stdout or "") + (proc.stderr or "")
    # 只打尾部：Spark 的 WARN/SLF4J 噪声能刷几千行，全打会把真正的错误淹掉
    tail = "\n".join(out.strip().splitlines()[-60:])
    print(tail)

    if proc.returncode != 0:
        raise AirflowException(f"{script} 退出码 {proc.returncode}，见上方日志")
    return out


def _ods_ready(ds: str) -> bool:
    """ODS 分区是否已落地（不连 Spark 的廉价判断）。

    判据：目录存在，且至少有一个非空的 .parquet 文件。
    Spark 写完后会有 _SUCCESS 标记，但它不是必需的（append 模式不写），
    所以用 parquet 文件体积判断更稳。

    注意这里刻意**不**校验行数：行数校验是质量门禁的职责。
    混在一起会导致「数据不完整 → 重跑 ODS → 又重跑」的死循环。
    """
    part = LAB_DB / "ods_iaa_user_daily" / f"dt={ds}"
    if not part.is_dir():
        return False
    for f in part.glob("*.parquet"):
        if f.stat().st_size > 0:
            return True
    return False


# ============================================================
# 任务实现
# ============================================================


def task_ods(**context):
    """① ODS 落地：只重建 ds 这一个分区。

    幂等短路：分区已经在、且没传 redo_ods，就直接跳过。
    这样日常调度几乎是零成本，只有真正缺数据的那天才跑造数。
    """
    ds = _ds_of(context)
    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    if _ods_ready(ds) and not conf.get("redo_ods"):
        print(f"[ods] dt={ds} 已存在，短路跳过（要强制重算传 redo_ods=true）")
        return "skipped"

    print(f"[ods] 重建 dt={ds} 分区")
    _run("gen_iaa_users.py", "--dt", ds, timeout=3600)
    if not _ods_ready(ds):
        raise AirflowException(f"ODS 写入后仍找不到分区 dt={ds}")
    return "built"


def task_dwd(**context):
    """② DWD 清洗：脏数据规则（空 uid / 负收入 / 重复行）在这里处理。"""
    ds = _ds_of(context)
    print(f"[dwd] 清洗 dt={ds}")
    _run("build_dwd_iaa.py", "--dt", ds, timeout=1800)


def task_acq(**context):
    """③ 买量侧事实表：和 DWD 无依赖，可与 DWD 并行，这里保持串行便于排错。"""
    ds = _ds_of(context)
    print(f"[acq] 构建买量表 dt={ds}")
    _run("gen_iaa_acquisition.py", "--dt", ds, timeout=1800)


def task_dws_cohort(**context):
    """④ DWS cohort 分区：单天重建，跳过三张全量表（由下一个 task 单独负责）。"""
    ds = _ds_of(context)
    print(f"[dws] 构建 cohort 分区 dt={ds}")
    _run("build_dws_iaa.py", "--dt", ds, "--skip-full", timeout=1800)


def task_dws_full(**context):
    """⑤ DWS 三张全量表：campaign / creative / date 聚合。

    这三张是**全量重建**（不分区），所以必须串行、且必须每天重跑 ——
    否则新一天的数据进不了按渠道/素材的汇总表。
    """
    print("[dws] 重建三张全量表")
    _run("build_dws_iaa.py", "--full-only", timeout=2400)


def task_ads(**context):
    """⑥ ADS 查询加速层：三张表全量重写，同时导出 parquet 给中台用。"""
    print("[ads] 重建 ADS 三表 + 导出 parquet")
    _run("build_ads_iaa.py", timeout=1800)


def task_sync_decision_log(**context):
    """⑦ 决策日志入仓：SQLite(dim.db) → ODS → DWD → ADS → parquet。

    为什么放在 ADS 之后而不是最前面：
        它读的是 agent 写的 dim.db，和数仓五层没有任何数据依赖，
        放最后是因为它的**代价最低**（几百行，20 秒）而且失败影响最小：
        指标链路已经跑完了，这一个 task 挂了只需单独重跑它。
        如果放最前面，它挂了会把当天的报表一起堵住 —— 收益和风险不匹配。

    为什么它不是"可选优化"：
        没有这一步，判定日志就只活在 dim.db 这个本地文件里：
        换台机器就没了、没法跟指标 join（「判了 cut 之后 ROI 有没有回升」）、
        也没法进门禁。日志入仓之后，「为什么这么判」才第一次成为可查询的数据。
    """
    print("[log] 决策日志入仓（SQLite → ODS/DWD/ADS → parquet）")
    _run("sync_decision_log_iaa.py", timeout=1800)


def _gate(scope: str, with_dt: bool = True):
    """生成一个「质量门禁」callable（避免为四层写四份几乎一样的函数）。

    Args:
        with_dt: 是否把 --dt 传给门禁。日志链路按 data_as_of / 操作日分区，
                 表里根本没有 dt 列，传了会 UNRESOLVED_COLUMN ——
                 而那是个"看起来像数据问题、其实是参数问题"的错误。
    """

    def _inner(**context):
        ds = _ds_of(context)
        args = ["--scope", scope]
        if with_dt:
            args += ["--dt", ds]
            print(f"[gate:{scope}] 校验 dt={ds}")
        else:
            print(f"[gate:{scope}] 校验（全表，日志量小）")
        _run("quality_gate_iaa.py", *args, timeout=1800)

    return _inner


# ============================================================
# DAG 定义
# ============================================================

default_args = {
    "owner": "iaa-bi",
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "depends_on_past": False,
    # 一个 task 卡死不能拖住整条流水线
    "execution_timeout": timedelta(minutes=60),
}

with DAG(
    dag_id="iaa_warehouse_daily",
    default_args=default_args,
    description="IAA 五层数仓：ODS → DWD → 买量 → DWS → ADS + 决策日志入仓，每层挂质量门禁",
    schedule="0 6 * * *",          # 每天 06:00，早于业务方 09:00 看数
    start_date=datetime(2026, 10, 1),
    catchup=False,                 # 补历史用手动 trigger + conf.ds，不用 catchup
    max_active_runs=1,             # 全量表重建会互相覆盖，必须串行（见文件头说明）
    tags=["iaa", "warehouse", "daily"],
    params={"ds": None, "redo_ods": False},
) as dag:

    start = EmptyOperator(task_id="start")

    ods = PythonOperator(task_id="ods_ingest", python_callable=task_ods)
    gate_ods = PythonOperator(task_id="gate_ods", python_callable=_gate("ods"))

    dwd = PythonOperator(task_id="dwd_clean", python_callable=task_dwd)
    acq = PythonOperator(task_id="acq_fact", python_callable=task_acq)
    gate_dwd = PythonOperator(task_id="gate_dwd", python_callable=_gate("dwd"))

    dws_cohort = PythonOperator(task_id="dws_cohort_dt", python_callable=task_dws_cohort)
    dws_full = PythonOperator(task_id="dws_full_tables", python_callable=task_dws_full)
    gate_dws = PythonOperator(task_id="gate_dws", python_callable=_gate("dws"))

    ads = PythonOperator(task_id="ads_build", python_callable=task_ads)
    gate_ads = PythonOperator(task_id="gate_ads", python_callable=_gate("ads"))

    # 日志链路：同步 → 门禁（不传 dt，见 _gate 的 with_dt）
    sync_log = PythonOperator(task_id="sync_decision_log", python_callable=task_sync_decision_log)
    gate_log = PythonOperator(task_id="gate_decision_log", python_callable=_gate("log", with_dt=False))

    end = EmptyOperator(task_id="end")

    # 依赖关系：ods → gate → (dwd, acq) → gate → dws → gate → ads → gate → log → gate
    #
    # 为什么每层后面都插 gate 而不是最后统一校验：
    # 数据错误会**沿着链路放大**。ODS 缺一天，到 ADS 就是 ROI 整体偏低；
    # 等跑到最后才报错，你已经不知道该查哪一层了。
    # 在出错的那一层立刻停，日志里就是「哪个断言、差多少」。
    #
    # 日志链路的 gate 挂在 sync 后面同理：同步错了（少抄一行、抄错一个 verdict）
    # 不会让任何下游报错，只会让中台的「上次结论」慢慢偏离实际 ——
    # 这种错误只能靠对账断言抓。
    start >> ods >> gate_ods
    gate_ods >> [dwd, acq]
    [dwd, acq] >> gate_dwd
    gate_dwd >> [dws_cohort, dws_full]
    [dws_cohort, dws_full] >> gate_dws
    gate_dws >> ads >> gate_ads >> sync_log >> gate_log >> end
