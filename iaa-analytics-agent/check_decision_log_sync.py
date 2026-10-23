# -*- coding: utf-8 -*-
"""验收：决策日志入仓（SQLite → ODS → DWD → ADS → parquet → 中台）

为什么单独写一份验收，而不是"跑一遍看没报错"
------------------------------------------------
入仓这类工作的失败方式几乎都是**静默**的：

  - 少抄了 3 行 → 表建成功、查询正常，只是中台有 3 个对象永远「首次判定」
  - verdict 抄错一个字母 → 没有任何异常，只是那批素材再也不会被判 cut
  - 导出时 coalesce(1) 丢行 → parquet 少几行，pandas 读回来一点不觉得奇怪
  - 同步任务被跳过 → 两边在旧数据上完全一致，对账照样通过

所以验收必须主动制造「本不该通过」的情形，看断言会不会响。
本文件里唯一的那条负向测试（篡改一行 → 门禁必须 FAIL）就是干这个的：
**一个从来没红过的断言，不能算断言。**

用法
----
    python check_decision_log_sync.py
    python check_decision_log_sync.py --idempotent    # 额外验证重跑幂等（慢一倍）

退出码：0 全过 / 1 有失败
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
# 数仓脚本（gen_iaa_users.build_session / quality_gate_iaa）在 scripts/ 下，
# 不在这个项目里 —— 它们属于数仓侧，和这个应用是两套代码。
sys.path.insert(0, str(ROOT.parent / "scripts"))

_HADOOP_HOME = ROOT.parent / "infra" / "hadoop"
if _HADOOP_HOME.is_dir():
    os.environ.setdefault("HADOOP_HOME", str(_HADOOP_HOME))
    os.environ["PATH"] = str(_HADOOP_HOME / "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable

DIM_DB = ROOT / "data" / "dim.db"
ADS_DIR = ROOT / "data" / "warehouse" / "ads"
PASS, FAIL = "PASS", "FAIL"

LOG_COLS = ["obj_type", "obj_id", "metric", "data_as_of", "verdict", "value"]


def fingerprint(df: pd.DataFrame, cols: list[str]) -> tuple[int, str]:
    """顺序无关的数据指纹：(行数, 哈希)。

    为什么不用文件 md5：
        parquet 的文件字节里含**列级统计索引**，同一份数据两次写出的
        这 24 个字节会不同（实测）。拿文件 md5 判幂等，会得到
        「内容一模一样但 hash 不同」的假失败 —— 然后人就不看这个断言了。

        正确做法是只对**数据本身**取指纹：先按业务键排序（消除行序影响），
        再把每行拼成字符串求哈希。相同数据 → 相同指纹，与控制流无关。
    """
    d = df[cols].copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].round(6)
    d = d.astype("string").fillna("<NULL>").sort_values(cols, kind="mergesort")
    blob = "\n".join("|".join(row) for row in d.itertuples(index=False))
    return len(d), hashlib.md5(blob.encode("utf-8")).hexdigest()[:16]


def sqlite_log() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DIM_DB}?mode=ro", uri=True)
    try:
        return pd.read_sql_query("SELECT * FROM decision_log", conn)
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="决策日志入仓验收")
    ap.add_argument("--idempotent", action="store_true", help="额外跑一次同步并比对指纹")
    args = ap.parse_args()

    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  [{PASS if cond else FAIL}] {label:<48} {detail}")
        if not cond:
            ok = False

    print("=" * 74)
    print("决策日志入仓验收")
    print("=" * 74)

    # ========================================================
    # 0. 源
    # ========================================================
    print("\n[0] 源（SQLite dim.db）")
    src = sqlite_log()
    check("decision_log 非空", len(src) > 0, f"{len(src)} 行")
    n_src, fp_src = fingerprint(src, LOG_COLS)
    check("源指纹", True, f"{n_src} 行 / {fp_src}")

    # ========================================================
    # 1. 数仓与导出文件
    # ========================================================
    from gen_iaa_users import build_session  # noqa: PLC0415

    spark = build_session()
    try:
        print("\n[1] 数仓表 / 导出文件")
        tables = {}
        for t in ("ods_decision_log", "dwd_decision_event", "ads_decision_latest",
                  "ads_decision_stats", "ods_operation_log"):
            try:
                n = spark.sql(f"SELECT count(*) c FROM lab.{t}").collect()[0]["c"]
                tables[t] = n
                check(f"lab.{t} 存在且有数据", n > 0, f"{n:,} 行")
            except Exception as e:  # noqa: BLE001
                check(f"lab.{t} 存在且有数据", False, f"{type(e).__name__}: {str(e)[:60]}")

        # ---- 三重指纹：源 = ODS = 导出文件 ----
        # 三者一致才说明「抄进来没错」且「导出没丢行」。
        hive = spark.sql(
            f"SELECT {', '.join(LOG_COLS)} FROM lab.ods_decision_log"
        ).toPandas()
        n_h, fp_h = fingerprint(hive, LOG_COLS)
        check("ODS 指纹 = 源指纹", (n_h, fp_h) == (n_src, fp_src), f"{n_h} 行 / {fp_h}")

        p = ADS_DIR / "decision_log.parquet"
        if p.exists():
            n_f, fp_f = fingerprint(pd.read_parquet(p), LOG_COLS)
            check("导出 parquet 指纹 = 源指纹", (n_f, fp_f) == (n_src, fp_src), f"{n_f} 行 / {fp_f}")
        else:
            check("导出 parquet 指纹 = 源指纹", False, "decision_log.parquet 不存在")

        # ---- 分区：一个 data_as_of 一个分区 ----
        parts = sorted(r[0].split("=")[1] for r in
                       spark.sql("SHOW PARTITIONS lab.ods_decision_log").collect())
        check("ODS 分区 = 源里的 data_as_of 集合",
              parts == sorted(str(d)[:10] for d in src["data_as_of"].unique()),
              f"{parts}")

        # ---- 每对象一行 ----
        n_latest = tables.get("ads_decision_latest", 0)
        n_obj = src[["obj_type", "obj_id"]].drop_duplicates().shape[0]
        check("ads_decision_latest 每对象一行", n_latest == n_obj, f"{n_latest} vs {n_obj}")

        # ========================================================
        # 2. 门禁：正向（应全过）
        # ========================================================
        print("\n[2] 质量门禁 scope=log（正向）")
        import quality_gate_iaa as gate  # noqa: PLC0415

        rules = gate.load_rules()
        total_fail = 0
        for t in gate.SCOPES["log"]:
            rule = rules.get(t)
            if not rule:
                check(f"{t} 有质量规则", False, "metrics.yaml 里没有这张表")
                continue
            f, n = gate.run_table(spark, t, rule, None)
            total_fail += f
            check(f"{t} 的 {n} 条断言", f == 0, f"{f} 条失败")

        # ========================================================
        # 3. 门禁：负向（篡改必须被抓到）
        # ========================================================
        # 用一张**副本表**做篡改，不碰真表 —— 测试不该有把生产数据改坏的可能。
        # 这是整个验收里最有价值的一项：它在问「这条断言到底会不会响」。
        print("\n[3] 质量门禁负向测试（篡改一行，source_parity 必须 FAIL）")
        probe = "lab.tmp_parity_probe"
        try:
            spark.sql(f"DROP TABLE IF EXISTS {probe}")
            # 从真表**读出后另建**，而不是对副本原地 UPDATE：
            #   Spark 不允许「一边读一边覆盖同一张表」（UNSUPPORTED_OVERWRITE）。
            #   一条 CREATE TABLE AS SELECT 就绕开了，而且真表一个字节都没动。
            victim = spark.sql(
                "SELECT obj_id FROM lab.ods_decision_log WHERE verdict='cut' LIMIT 1"
            ).collect()[0]["obj_id"]
            spark.sql(f"""
                CREATE TABLE {probe} AS
                SELECT log_id, decided_at, obj_type, obj_id, metric, value,
                       CASE WHEN obj_id='{victim}' THEN 'hold' ELSE verdict END AS verdict,
                       reason, days_sustained, snapshot_hash, config_version, data_as_of
                FROM lab.ods_decision_log
            """)
            # 断言函数的 rule 参数是**单条断言**（带 min/max/keys 的那一层），
            # 不是整张表的规则 —— 和 run_table 里 fn(spark, table, a, dt) 传的一致。
            parity_rule = next(a for a in rules["ods_decision_log"]["assertions"]
                               if a["check"] == "source_parity")
            status, msg = gate.check_source_parity(
                spark, "tmp_parity_probe", parity_rule, None)
            check("篡改 verdict 后 source_parity 变红", status == FAIL,
                  msg[:70] if status == FAIL else f"仍然 PASS（{msg[:50]}）")
        except Exception as e:  # noqa: BLE001
            check("篡改 verdict 后 source_parity 变红", False, f"{type(e).__name__}: {e}")
        finally:
            spark.sql(f"DROP TABLE IF EXISTS {probe}")

        # ========================================================
        # 4. 幂等：同一份源同步两次，结果必须一样
        # ========================================================
        print("\n[4] 幂等（同一份源再同步一次）")
        before = (n_h, fp_h)
        if args.idempotent:
            import subprocess  # noqa: PLC0415
            r = subprocess.run(  # noqa: S603
                [sys.executable, str(ROOT.parent / "scripts" / "sync_decision_log_iaa.py")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            check("第二次同步成功", r.returncode == 0, f"退出码 {r.returncode}")
            # 必须刷新：同步是**另一个进程**把分区文件整个换掉的，
            # 而当前这个常驻 Spark 会话还缓存着第一次扫描时的 InMemoryFileIndex
            # （里面记着旧文件名）。不刷新就会报 "file does not exist"，
            # 而文件明明在 —— 这个错以前排查过一次，错在缓存不在数据。
            spark.catalog.clearCache()
            spark.sql("REFRESH TABLE lab.ods_decision_log")
            hive2 = spark.sql(
                f"SELECT {', '.join(LOG_COLS)} FROM lab.ods_decision_log"
            ).toPandas()
            after = fingerprint(hive2, LOG_COLS)
            check("重跑后指纹不变", before == after, f"{before} → {after}")
        else:
            print("  [SKIP] 未传 --idempotent，跳过（要验证请加这个参数）")

        # ========================================================
        # 5. 增量水印：agent 刚写完、数仓还没同步时，中台必须读到新的
        # ========================================================
        print("\n[5] 增量水印（模拟 agent 刚写了一条，数仓尚未同步）")
        import warehouse_api  # noqa: PLC0415

        base = warehouse_api.read_decision_latest()
        check("正常情况下读的是数仓", base.attrs.get("source") == "warehouse",
              str(base.attrs.get("source")))

        probe_id = str(src[src["obj_type"] == "creative"]["obj_id"].iloc[0])
        new_day = (date.fromisoformat(str(src["data_as_of"].max())[:10])
                   + timedelta(days=1)).isoformat()
        conn = sqlite3.connect(DIM_DB)
        try:
            conn.execute(
                """INSERT INTO decision_log
                       (decided_at, data_as_of, obj_type, obj_id, metric,
                        value, verdict, reason, days_sustained, snapshot_hash, config_version)
                   VALUES (datetime('now','localtime'), ?, 'creative', ?, 'd7_roi',
                           0.4321, 'cut', '验收探针', 7, 'probe-hash', '2026-10-01')
                   ON CONFLICT(obj_type, obj_id, metric, data_as_of) DO NOTHING""",
                (new_day, probe_id),
            )
            conn.commit()

            delta = warehouse_api.read_decision_latest()
            check("源更新后切到 SQLite 增量", delta.attrs.get("source") == "sqlite(delta)",
                  str(delta.attrs.get("source")))
            check("增量里能读到新结论",
                  str(delta[delta["obj_id"] == probe_id]["data_as_of"].iloc[0])[:10] == new_day,
                  f"data_as_of={new_day}")

            row = delta[delta["obj_id"] == probe_id].iloc[0]
            check("增量算出了 verdict_streak（pandas 版与 Spark 版同口径）",
                  int(row["verdict_streak"]) >= 1, f"streak={row['verdict_streak']}")
            check("增量算出了 n_judged / n_changed",
                  int(row["n_judged"]) >= 2, f"n_judged={row['n_judged']}")
        finally:
            conn.execute("DELETE FROM decision_log WHERE reason='验收探针'")
            conn.commit()
            conn.close()

        after_clean = warehouse_api.read_decision_latest()
        check("清理探针后回到数仓", after_clean.attrs.get("source") == "warehouse",
              str(after_clean.attrs.get("source")))

        # ========================================================
        # 6. 中台快照：用的是同一份日志
        # ========================================================
        print("\n[6] 中台快照")
        import agent  # noqa: PLC0415

        snap = agent.build_snapshot(top_n=20, persist=False)
        check("快照标出了日志来源", snap["数据口径"].get("判定日志来源") == "数仓（已同步）",
              str(snap["数据口径"].get("判定日志来源")))
        check("快照有「上次结论」", len(snap.get("上次结论") or []) > 0,
              f"{len(snap.get('上次结论') or [])} 条")
        check("快照有「最近操作」", len(snap.get("最近操作") or []) > 0,
              f"{len(snap.get('最近操作') or [])} 条")
        # 上一次判定和本次判定的对象集合必须对得上，否则「上次结论」会缺人
        judged = {r["对象"] for r in (snap["判定建议"].get("渠道") or [])}
        cr = snap["判定建议"].get("素材") or {}
        if isinstance(cr, dict):
            judged |= {r["对象"] for r in (cr.get("最差 10 个") or [])}
        shown = {r["对象"] for r in (snap.get("上次结论") or [])}
        check("「上次结论」覆盖所有展示中的对象", judged <= shown,
              f"缺 {sorted(judged - shown)[:3]}" if judged - shown else f"{len(shown)} 条")
    finally:
        spark.stop()

    print("\n" + "=" * 74)
    print("[全部通过]" if ok else "[存在失败项] —— 不要放行")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
