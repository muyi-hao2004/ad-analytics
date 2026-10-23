# -*- coding: utf-8 -*-
"""幂等性验证：同一天跑两遍，逐表比对内容指纹

为什么要专门写一个验证脚本
--------------------------
「重跑不出错」和「重跑结果相同」是两件完全不同的事，而后者才是幂等。
肉眼比对表头、看行数一样、看几个 ROI 差不多，都**证明不了**幂等 ——
随机数种子变了、分组顺序变了、浮点累加顺序变了，行数和均值都可能几乎不变，
但明细已经换了。

所以这里用内容指纹（content fingerprint）：
    fingerprint = (行数, sum(hash(所有列)))
`hash()` 是 Spark 的确定性哈希，`sum()` 与行顺序无关。
两个分区的指纹相同 ⇒ 内容逐行相同（哈希碰撞概率可忽略）。

验证三件事
----------
  A. **重跑幂等**：同一天跑两次，第二次结果必须与第一次完全一致
  B. **重跑不改历史**：跑 dt=X 时，其它分区必须一个字节都不变
     （这条针对的是一个真实踩过的坑：用 --days 1 冒烟测试，
      以为验证成功，实际只重写了第 1 天，其余 30 天还是旧数据）
  C. **重跑不改原始值**：如果第一次重跑就和现状不同，说明链路里有非确定性
     （随机种子没固定 / 依赖了当前时间 / groupby 顺序影响了聚合）

用法
----
    python scripts/test_idempotent_iaa.py --dt 2026-10-05
    python scripts/test_idempotent_iaa.py --dt 2026-10-05 --rounds 2

退出码：0 = 三项全部通过；1 = 有不一致
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(PROJECT_ROOT / ".venv-spark" / "Scripts" / "python.exe")

# 被验证的 10 张表：(表名, 是否分区表)
# 分区表只比对本次任务涉及的那个分区，全量表比对整表
TABLES: List[Tuple[str, bool]] = [
    ("ods_iaa_user_daily", True),
    ("ods_iaa_acquisition_daily", True),
    ("dwd_user_daily", True),
    ("dws_cohort_daily", True),
    ("dws_campaign_daily", False),
    ("dws_creative_daily", False),
    ("dws_date_daily", False),
    ("ads_daily_detail", False),
    ("ads_creative_table", False),
    ("ads_sub_channel_table", False),
]

# 用来做「其它分区是否被误改」的对照分区。
# 选最后一天：它离 dt=X 最远，任何「全量重写」的副作用都会在这里暴露。
GUARD_DT = "2026-10-31"


def run_step(script: str, *args: str, timeout: int = 3600) -> None:
    """跑一个流水线脚本，失败就退出。"""
    path = PROJECT_ROOT / "scripts" / script
    cmd = [PYTHON, "-u", str(path), *args]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    t0 = time.time()
    proc = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={
            # 先铺父环境，再覆盖编码相关的键 —— 顺序反了父进程的 cp936 会赢
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "MSYS_NO_PATHCONV": "1",
        },
        cwd=str(PROJECT_ROOT),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(
        ln for ln in out.strip().splitlines()[-25:]
        if not ln.startswith(("WARNING", "WARN ", "SLF4J", "Using", "To adjust", "Setting", "	at "))
    )
    print(tail, flush=True)
    print(f"  → 退出码 {proc.returncode}，用时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"[中止] {script} 失败，幂等验证无意义")


def fingerprint(spark, table: str, dt: Optional[str] = None) -> Tuple[int, int]:
    """算一张表（或一个分区）的内容指纹。

    Args:
        table: 不带库前缀的表名
        dt: 分区日期；None 表示整表

    Returns:
        (行数, sum(hash(所有列)))
    """
    df = spark.table(f"lab.{table}")
    if dt is not None:
        df = df.filter(df.dt == dt)
    cols = [f"`{c}`" for c in df.columns]
    # sum(hash(...)) 与行顺序无关；溢出是可复现的确定性行为，不影响比对
    row = df.selectExpr("count(*) AS n", f"sum(hash({', '.join(cols)})) AS h").collect()[0]
    return int(row["n"]), int(row["h"] or 0)


def refresh_tables(spark) -> None:
    """让 Spark 重新读取表目录下的文件清单。

    为什么必须做这一步：
    流水线的每一步都是**独立进程**（子进程），它们删除并重写 parquet 文件。
    而本脚本的 Spark 会话在整个验证期间一直活着，它第一次读表时会把
    文件清单（InMemoryFileIndex）缓存下来。之后外部进程把文件换掉，
    缓存里还是旧文件名，下一次查询就会报
        FILE_NOT_EXIST: ... part-00000-ae8de99b-....parquet does not exist

    这不是数据坏了，是**元数据缓存过期**。真实生产里同样的问题会出现在
    「Spark 会话常驻 + 外部工具往同一张表写文件」的场景。
    """
    spark.catalog.clearCache()
    for table, _ in TABLES:
        try:
            spark.sql(f"REFRESH TABLE lab.{table}")
        except Exception as e:  # noqa: BLE001
            print(f"    [warn] REFRESH lab.{table} 失败: {type(e).__name__}")


def snapshot(spark, dt: str) -> Dict[str, Tuple[int, int]]:
    """给所有表拍一张指纹快照。"""
    refresh_tables(spark)
    snap = {}
    for table, is_part in TABLES:
        snap[table] = fingerprint(spark, table, dt if is_part else None)
    # 对照组：dt=GUARD_DT 的 ODS 分区（用来验证「单日重建不碰其它分区」）
    snap["__guard__ods_" + GUARD_DT] = fingerprint(spark, "ods_iaa_user_daily", GUARD_DT)
    snap["__guard__dwd_" + GUARD_DT] = fingerprint(spark, "dwd_user_daily", GUARD_DT)
    return snap


def fmt(v: Tuple[int, int]) -> str:
    return f"{v[0]:>10,} 行  h={v[1]:>20,}"


def compare(name: str, a: Dict[str, Tuple[int, int]], b: Dict[str, Tuple[int, int]]) -> int:
    """比对两张快照，返回不一致数量。"""
    print(f"\n--- {name} ---")
    bad = 0
    for k in a:
        if a[k] == b[k]:
            print(f"  一致  {k:32s} {fmt(a[k])}")
        else:
            bad += 1
            print(f"  不一致 {k:32s}")
            print(f"        before: {fmt(a[k])}")
            print(f"        after : {fmt(b[k])}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dt", required=True, help="要验证的业务日期，如 2026-10-05")
    ap.add_argument("--rounds", type=int, default=2, help="重跑次数（默认 2）")
    args = ap.parse_args()

    dt = args.dt
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from gen_iaa_users import build_session  # noqa: PLC0415

    print(f"验证目标：dt={dt}，重跑 {args.rounds} 次")
    print(f"对照分区：{GUARD_DT}（跑 {dt} 时它必须纹丝不动）")

    spark = build_session()
    # 兜底：万一 REFRESH 也没赶上，缺文件时读 0 行而不是整个任务崩掉。
    # 只在本测试里开，不建议在生产查询里开 —— 它会掩盖真正的数据丢失。
    spark.conf.set("spark.sql.files.ignoreMissingFiles", "true")
    try:
        t0 = time.time()
        print("\n[0] 拍摄基线快照 ...")
        base = snapshot(spark, dt)
        print(f"    完成，{(time.time() - t0):.1f}s")

        # ---- 第 1 轮：跑一遍完整流水线 ----
        run_step("gen_iaa_users.py", "--dt", dt)
        run_step("build_dwd_iaa.py", "--dt", dt)
        run_step("gen_iaa_acquisition.py", "--dt", dt)
        run_step("build_dws_iaa.py", "--dt", dt, "--skip-full")
        run_step("build_dws_iaa.py", "--full-only")
        run_step("build_ads_iaa.py")

        print("\n[1] 拍摄第 1 轮快照 ...")
        r1 = snapshot(spark, dt)

        # ---- 第 2 轮：再跑一遍（参数完全相同）----
        for i in range(2, args.rounds + 1):
            print(f"\n{'=' * 62}\n第 {i} 轮重跑（参数与第 1 轮完全相同）\n{'=' * 62}")
            run_step("gen_iaa_users.py", "--dt", dt)
            run_step("build_dwd_iaa.py", "--dt", dt)
            run_step("gen_iaa_acquisition.py", "--dt", dt)
            run_step("build_dws_iaa.py", "--dt", dt, "--skip-full")
            run_step("build_dws_iaa.py", "--full-only")
            run_step("build_ads_iaa.py")
        r2 = snapshot(spark, dt)
    finally:
        spark.stop()

    print(f"\n{'=' * 62}\n结论\n{'=' * 62}")

    # A. 重跑幂等
    bad_a = compare("A. 重跑幂等（第 1 轮 vs 第 2 轮，必须完全一致）", r1, r2)
    # C. 重跑不改原始值
    bad_c = compare("C. 重跑不改原始值（基线 vs 第 1 轮）", base, r1)

    print(f"\n{'=' * 62}")
    if bad_a == 0:
        print("A 通过：同一天跑两遍，10 张表（含 4 个分区）内容逐行相同")
    else:
        print(f"A 失败：{bad_a} 张表重跑后内容变化 —— 这条流水线不幂等")

    if bad_c == 0:
        print("C 通过：重建出来的值与现状完全一致，链路无非确定性")
    else:
        print(f"C 提示：{bad_c} 张表与基线不同。若首次重建就不同，说明原数据"
              f"不是由当前代码产出的（例如造数脚本改过但没全量重跑）")

    ok = bad_a == 0 and bad_c == 0
    print(("全部通过" if ok else "存在不一致，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
