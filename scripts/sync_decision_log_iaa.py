# -*- coding: utf-8 -*-
"""第 6 步：把中台的决策日志接进数仓（ODS → DWD → ADS）

================================================================================
为什么日志也要入仓
================================================================================
在这之前，「判定结论」这件事有两个数据源：

    指标数值  → 数仓（Hive / 导出的 parquet）
    判定日志  → SQLite（data/dim.db，中台自己的库）

后果不是「不好看」，是**没法 join**。最想问的问题全都答不了：

  - 判了 cut 的那些素材，砍完之后 D7 ROI 有没有回升？
    → 日志在 SQLite、ROI 在 Hive，跨源 join 只能在内存里手工拼
  - 上周一共改判了几次？是滞回在起作用，还是规则在漂？
    → 需要按 data_as_of × verdict 聚合，SQLite 上没有这张汇总
  - 三个月前的定论，当时的指纹和配置版本是什么？
    → dim.db 是本地文件，换台机器就没了

所以这一步把日志变成数仓的一等公民：分层、分区、被门禁管、可回溯。

================================================================================
SQLite 为什么不废弃（这是刻意的双写，不是没收拾干净）
================================================================================
两边的职责完全不同，谁替不了谁：

    SQLite（写入端 / OLTP）        数仓（台账 / OLAP）
    ------------------------       -------------------------
    行级 UPSERT（UNIQUE 约束）      分区整块覆盖
    毫秒级，agent 一天跑多次        秒级，按调度跑
    只存「当前最新结论」            存「历史上每一次改口」
    事务边界清晰                    能 join、能聚合、能进门禁

如果强行让数仓当唯一写入端，就要在 parquet 上做 UPSERT——
parquet 是列式不可变文件，做这件事只能「读全表 → 改一行 → 重写全表」，
每次 agent 跑一轮就重写一遍，代价荒谬。

反过来让 SQLite 当唯一读取端，就回到了「两个数据源没法 join」。
所以正确的分工是：**写在 SQLite，读去数仓，两源对账**。

================================================================================
两源一致性：用对账代替事务
================================================================================
跨 SQLite 和 Hive 不可能有分布式事务。这里承认这一点，然后做两件事：

  1. **同步是全量镜像，不是增量**
     增量（WHERE decided_at > 上次水位）看着省事，但会**漏掉改写**：
     agent 补数时会把某天已有的行 UPSERT 掉（data_as_of 相同、内容更新），
     这条改写的 decided_at 是新的、data_as_of 是旧的，
     按 data_as_of 增量就会整个跳过它 —— 数仓永久停在旧结论上，且不报错。
     源表只有几百行，全量镜像的代价可以忽略，换来的是「不可能漏」。

  2. **水位（watermark）当一致性判据**
     两边的 max(decided_at) 一比就知道谁更新。中台读的时候：
       数仓更新  → 用数仓
       SQLite 更新（agent 刚跑完、还没同步）→ 用 SQLite
     这不是「猜哪边对」，是用一个明确定义的水位决定，没有第三种情况。

  3. **门禁里有 source_parity 断言**
     同步完立刻逐对象比对两源的最新结论。不一致就 fail 掉流水线。
     这条断言就是「事务约束」的替代品：做不到原子，就做到**可发现**。

================================================================================
幂等：两层，缺一层都不完整
================================================================================
  第一层（源）：SQLite 表上的 UNIQUE(obj_type, obj_id, metric, data_as_of)
      → 同一天跑十次 agent，只留一条最终结论

  第二层（仓）：INSERT OVERWRITE + dynamic partition overwrite
      → 同一份源数据同步十次，分区逐字节相同

只做第二层没用（源在变，仓只是忠实复制了变化）；
只做第一层也不够（源稳定，但同步逻辑有随机性，重跑结果仍可能不同）。

产出：
    lab.ods_decision_log       171 行   决策日志原样落地（按 data_as_of 分区）
    lab.ods_operation_log       14 行   人工/Agent 操作日志（按操作日分区）
    lab.dwd_decision_event     171 行   判定事件流，补「相对上次是否改判」
    lab.ads_decision_latest    171 行   每对象最新一条 + 历史统计
    lab.ads_decision_stats       7 行   按日期 × 对象类型 × 结论的分布

用法：
    python scripts/sync_decision_log_iaa.py
    python scripts/sync_decision_log_iaa.py --skip-export   # 只更 Hive，不导 parquet
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIM_DB = PROJECT_ROOT / "iaa-analytics-agent" / "data" / "dim.db"
DB = "lab"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")

# 导出目标和 ADS 其他表同一个目录 —— 中台只认一个目录，多一个就是多一处漂移
ADS_OUT = PROJECT_ROOT / "iaa-analytics-agent" / "data" / "warehouse" / "ads"


# ============================================================
# 一、从 SQLite 抽取
# ============================================================

DECISION_COLS = [
    "log_id", "decided_at", "data_as_of", "obj_type", "obj_id", "metric",
    "value", "verdict", "reason", "days_sustained", "snapshot_hash", "config_version",
]

OPERATION_COLS = [
    "log_id", "op_time", "operator", "action", "app", "channel",
    "campaign_id", "country", "field", "old_value", "new_value", "reason",
]


def _read_sqlite(table: str, cols: List[str]) -> pd.DataFrame:
    """只读方式读 SQLite。

    为什么用 `?mode=ro` 而不是普通 connect：
        同步任务对 dim.db 只有读的权利。用可写模式连上去，万一代码里
        多了一句 CREATE/DELETE，就会改到 agent 正在用的库；
        而且可写连接会加锁，可能让同时在跑的 agent 写日志失败。
        只读连接是**结构上**杜绝这件事，而不是靠"我记得别写"。
    """
    if not DIM_DB.exists():
        raise FileNotFoundError(f"找不到中台的维表库: {DIM_DB}（agent 还没跑过？）")
    conn = sqlite3.connect(f"file:{DIM_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(f"SELECT {', '.join(cols)} FROM {table}", conn)
    finally:
        conn.close()
    return df


def _none(v):
    """pandas 的 NaN → Python None。

    必须显式转：Spark 会把 float('nan') 当成合法 double 写进 parquet，
    而 pandas 读回来 NaN 和 NULL 长得一样，于是"没有值"和"值是 NaN"
    在整条链路上再也分不开。NULL 才是「不知道」。
    """
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if pd.isna(v):
        return None
    return v


def _to_date(s: str) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def load_decisions() -> pd.DataFrame:
    df = _read_sqlite("decision_log", DECISION_COLS)
    if df.empty:
        raise RuntimeError(
            "decision_log 是空的 —— 先让 agent 跑一轮（build_snapshot(persist=True)）。\n"
            "空日志进仓后，滞回会全部退化成「首次判定」，而且不会报任何错。"
        )
    df["data_as_of"] = df["data_as_of"].map(_to_date)
    df["value"] = df["value"].map(_none)
    df["days_sustained"] = df["days_sustained"].map(_none)
    return df


def load_operations() -> pd.DataFrame:
    df = _read_sqlite("operation_log", OPERATION_COLS)
    if df.empty:
        return df
    # 分区键用「操作发生日」。op_time 是 'YYYY-MM-DD HH:MM:SS'，
    # 前 10 位就是日期 —— 不用 pd.to_datetime 是因为它是本地墙钟时间，
    # 转成带时区的 timestamp 会凭空引入一个不存在的时区语义。
    df["dt"] = df["op_time"].map(lambda s: _to_date(s) if s else None)
    df = df[df["dt"].notna()].copy()
    return df


# ============================================================
# 二、建表
# ============================================================

def create_tables(spark) -> None:
    """建 5 张表。

    ODS 用 EXTERNAL：数据和元数据分离，删表不删数据。
        这和前三层的 ODS 契约一致 —— 原始层是"上游给什么就存什么"，
        不应该因为一次 DROP TABLE 就把唯一的落地副本删掉。

    DWD / ADS 用普通表（MANAGED）：都是可重建的派生数据，
        删了能再算出来，没必要保护。
    """
    def ddl(table: str, body: str, extra: str) -> str:
        return f"""
        CREATE TABLE IF NOT EXISTS {DB}.{table} (
            {body}
        )
        {extra}
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/{table}'
        """

    # ---- ODS 1：决策日志 ----
    # 分区键直接用 data_as_of（业务日期），不叫 dt。
    # 为什么：其他四层的 dt 是「数据分区日」，这里的分区含义是
    # 「这条判定看到的是哪天的数据」，两者语义不同，共用一个名字迟早被混用。
    spark.sql(f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.ods_decision_log (
            log_id          bigint,
            decided_at      string,
            obj_type        string,
            obj_id          string,
            metric          string,
            value           double,
            verdict         string,
            reason          string,
            days_sustained  int,
            snapshot_hash   string,
            config_version  string
        )
        PARTITIONED BY (data_as_of date)
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/ods_decision_log'
    """)

    spark.sql(f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {DB}.ods_operation_log (
            log_id      bigint,
            op_time     string,
            operator    string,
            action      string,
            app         string,
            channel     string,
            campaign_id string,
            country     string,
            field       string,
            old_value   string,
            new_value   string,
            reason      string
        )
        PARTITIONED BY (dt date)
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/ods_operation_log'
    """)

    # ---- DWD：判定事件流 ----
    # 和 ODS 的区别就两列：prev_verdict / changed。
    # 为什么值得单开一层：ODS 是"每次判了什么"，DWD 才是"什么时候改了口"。
    # 后者要靠 LAG 窗口算出来，放在 ADS 里算会重复计算 171 次。
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {DB}.dwd_decision_event (
            log_id          bigint,
            decided_at      string,
            data_as_of      date,
            obj_type        string,
            obj_id          string,
            metric          string,
            value           double,
            verdict         string,
            prev_verdict    string,
            prev_value      double,
            prev_data_as_of date,
            changed         boolean,
            reason          string,
            days_sustained  int,
            snapshot_hash   string,
            config_version  string
        )
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/dwd_decision_event'
    """)

    # ---- ADS：每对象最新一条（中台读这张）----
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {DB}.ads_decision_latest (
            obj_type        string,
            obj_id          string,
            metric          string,
            data_as_of      date,
            decided_at      string,
            value           double,
            verdict         string,
            prev_verdict    string,
            changed         boolean,
            reason          string,
            days_sustained  int,
            verdict_streak  int,
            n_judged        int,
            n_changed       int,
            first_data_as_of date,
            snapshot_hash   string,
            config_version  string
        )
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/ads_decision_latest'
    """)

    # ---- ADS：判定分布（看板 / 复盘用）----
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {DB}.ads_decision_stats (
            data_as_of   date,
            obj_type     string,
            metric       string,
            verdict      string,
            n_objects    bigint,
            n_changed    bigint,
            avg_value    double,
            min_value    double,
            max_value    double
        )
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/ads_decision_stats'
    """)


# ============================================================
# 三、装载
# ============================================================

def load_ods(spark, decisions: pd.DataFrame, operations: pd.DataFrame) -> None:
    """ODS：原样落地，按业务日期动态分区覆盖。

    动态分区覆盖（partitionOverwriteMode=dynamic）是这里的关键：
        本次数据里出现哪几个 data_as_of，就只替换哪几个分区，
        其余分区一个字节不动。
    如果用的是静态分区（OVERWRITE 整表），补一天数就会把历史全清掉——
    这个坑在 ODS 层踩过一次，表现是"补完 10-15，10-01~10-14 全没了"。
    """
    from pyspark.sql.types import (
        DateType, DoubleType, IntegerType, LongType, StringType, StructField, StructType,
    )

    d_schema = StructType([
        StructField("log_id", LongType()),
        StructField("decided_at", StringType()),
        StructField("obj_type", StringType()),
        StructField("obj_id", StringType()),
        StructField("metric", StringType()),
        StructField("value", DoubleType()),
        StructField("verdict", StringType()),
        StructField("reason", StringType()),
        StructField("days_sustained", IntegerType()),
        StructField("snapshot_hash", StringType()),
        StructField("config_version", StringType()),
        StructField("data_as_of", DateType()),
    ])
    d_rows = [
        (
            int(r.log_id), str(r.decided_at), r.obj_type, r.obj_id, r.metric,
            _none(r.value), r.verdict, r.reason,
            None if r.days_sustained is None else int(r.days_sustained),
            r.snapshot_hash, r.config_version, r.data_as_of,
        )
        for r in decisions.itertuples(index=False)
    ]
    spark.createDataFrame(d_rows, d_schema).createOrReplaceTempView("_src_decision")
    spark.sql(f"""
        INSERT OVERWRITE TABLE {DB}.ods_decision_log PARTITION (data_as_of)
        SELECT log_id, decided_at, obj_type, obj_id, metric, value, verdict,
               reason, days_sustained, snapshot_hash, config_version, data_as_of
        FROM _src_decision
    """)
    print(f"      ods_decision_log      {len(d_rows):,} 行", flush=True)

    if operations.empty:
        print("      ods_operation_log     0 行（源表为空，跳过）", flush=True)
        return

    o_schema = StructType(
        [StructField("log_id", LongType())]
        + [StructField(c, StringType()) for c in OPERATION_COLS if c != "log_id"]
        + [StructField("dt", DateType())]
    )
    o_rows = [
        (int(r.log_id),) + tuple(_none(getattr(r, c)) for c in OPERATION_COLS if c != "log_id")
        + (r.dt,)
        for r in operations.itertuples(index=False)
    ]
    spark.createDataFrame(o_rows, o_schema).createOrReplaceTempView("_src_operation")
    spark.sql(f"""
        INSERT OVERWRITE TABLE {DB}.ods_operation_log PARTITION (dt)
        SELECT {', '.join(OPERATION_COLS)}, dt FROM _src_operation
    """)
    print(f"      ods_operation_log     {len(o_rows):,} 行", flush=True)


def build_dwd(spark) -> None:
    """DWD：给每一行补上「上一次判的是什么、变没变」。

    changed 的三值问题：
        第一行没有上一次，`verdict <> lag(verdict)` 在 Spark 里是 NULL，
        不是 false。如果直接用它做 sum(CASE WHEN changed THEN 1 END)，
        这一行会被静默跳过 —— 看起来完全正常，只是统计少 1。
        所以显式写成「没有上一次 → false」，让 changed 只有 true/false 两值。

    排序用 data_as_of 而不是 decided_at：
        decided_at 是"什么时候算的"（补数会重排），
        data_as_of 是"算的是哪天的数"（业务上唯一有序）。
        用 decided_at 排序，补一次历史数就会得到一串假的"改判"。
    """
    spark.sql(f"""
        INSERT OVERWRITE TABLE {DB}.dwd_decision_event
        SELECT
            log_id, decided_at, data_as_of, obj_type, obj_id, metric,
            value, verdict,
            lag(verdict)      OVER (PARTITION BY obj_type, obj_id, metric ORDER BY data_as_of) AS prev_verdict,
            lag(value)        OVER (PARTITION BY obj_type, obj_id, metric ORDER BY data_as_of) AS prev_value,
            lag(data_as_of)   OVER (PARTITION BY obj_type, obj_id, metric ORDER BY data_as_of) AS prev_data_as_of,
            CASE WHEN lag(verdict) OVER (PARTITION BY obj_type, obj_id, metric ORDER BY data_as_of) IS NULL
                 THEN false
                 ELSE verdict <> lag(verdict) OVER (PARTITION BY obj_type, obj_id, metric ORDER BY data_as_of)
            END AS changed,
            reason, days_sustained, snapshot_hash, config_version
        FROM {DB}.ods_decision_log
    """)
    n = spark.sql(f"SELECT count(*) c FROM {DB}.dwd_decision_event").collect()[0]["c"]
    print(f"      dwd_decision_event    {n:,} 行", flush=True)


def build_ads(spark) -> None:
    """ADS：最新结论 + 判定分布。

    verdict_streak 是什么：
        「当前这个结论已经连续几天了」。用 gaps-and-islands 算：
        同一个对象内部，行号 减去 「按结论分组后的行号」，相同 verdict 的
        连续段会得到同一个 island 值，段内 count 就是连续天数。

    verdict_streak 与 days_sustained 的**区别（别混）**：
        days_sustained  = 裸阈值分类连续几天 → 判"能不能改判"的依据
        verdict_streak  = 最终结论连续几天     → 判"这个结论有多稳"的依据
    一个对象可以在 days_sustained=5 的同时 verdict_streak=1，
    因为滞回刚放行了一次改判。两个数都有用，但不能互相替代。
    """
    spark.sql(f"""
        INSERT OVERWRITE TABLE {DB}.ads_decision_latest
        WITH isl AS (
            SELECT e.*,
                   row_number() OVER (PARTITION BY obj_type, obj_id, metric ORDER BY data_as_of)
                 - row_number() OVER (PARTITION BY obj_type, obj_id, metric, verdict ORDER BY data_as_of)
                   AS island
            FROM {DB}.dwd_decision_event e
        ),
        strk AS (
            SELECT i.*,
                   count(*) OVER (PARTITION BY obj_type, obj_id, metric, verdict, island) AS verdict_streak
            FROM isl i
        ),
        agg AS (
            SELECT s.*,
                   row_number() OVER (PARTITION BY obj_type, obj_id, metric ORDER BY data_as_of DESC) AS rn,
                   count(*)     OVER (PARTITION BY obj_type, obj_id, metric) AS n_judged,
                   sum(CASE WHEN changed THEN 1 ELSE 0 END)
                                OVER (PARTITION BY obj_type, obj_id, metric) AS n_changed,
                   min(data_as_of) OVER (PARTITION BY obj_type, obj_id, metric) AS first_data_as_of
            FROM strk s
        )
        SELECT obj_type, obj_id, metric, data_as_of, decided_at, value, verdict,
               prev_verdict, changed, reason, days_sustained, verdict_streak,
               n_judged, n_changed, first_data_as_of, snapshot_hash, config_version
        FROM agg
        WHERE rn = 1
    """)
    n = spark.sql(f"SELECT count(*) c FROM {DB}.ads_decision_latest").collect()[0]["c"]
    print(f"      ads_decision_latest   {n:,} 行", flush=True)

    spark.sql(f"""
        INSERT OVERWRITE TABLE {DB}.ads_decision_stats
        SELECT data_as_of, obj_type, metric, verdict,
               count(*) AS n_objects,
               sum(CASE WHEN changed THEN 1 ELSE 0 END) AS n_changed,
               avg(value) AS avg_value,
               min(value) AS min_value,
               max(value) AS max_value
        FROM {DB}.dwd_decision_event
        GROUP BY data_as_of, obj_type, metric, verdict
    """)
    n = spark.sql(f"SELECT count(*) c FROM {DB}.ads_decision_stats").collect()[0]["c"]
    print(f"      ads_decision_stats    {n:,} 行", flush=True)


# ============================================================
# 四、导出 + 验收
# ============================================================

def export_all(spark) -> None:
    """导出成 parquet 给中台读。

    直接复用 build_ads_iaa.export_parquet，而不是在这里重写一遍：
        那个函数处理了两件必须一致的事 —— date 列转字符串（否则
        JSON 序列化会失败）、decimal 转 double（否则和 float 相乘会抛异常）。
        复制一份出来的话，将来改了一处、忘了另一处，
        中台就会在某一张表上莫名其妙地崩。
    """
    from build_ads_iaa import export_parquet

    ADS_OUT.mkdir(parents=True, exist_ok=True)
    for table, out, order_by in [
        # 排序键都是各自唯一粒度，所以排序后行序唯一 → 导出的文件可以做到**逐字节相同**。
        # 不传 order_by 的话，两次导出内容一致但字节不同（分片顺序不固定），
        # 「幂等」就只能证明到「数据对」而证不到「文件一样」。
        ("ods_decision_log", "decision_log", "obj_type, obj_id, metric, data_as_of"),
        ("ads_decision_latest", "decision_latest", "obj_type, obj_id, metric"),
        ("ads_decision_stats", "decision_stats", "data_as_of, obj_type, metric, verdict"),
        ("ods_operation_log", "operation_log", "log_id"),
    ]:
        try:
            n = export_parquet(spark, table, out, order_by=order_by)
            size = (ADS_OUT / f"{out}.parquet").stat().st_size
            print(f"      {out + '.parquet':24s} {n:,} 行  {size / 1024:.0f} KB", flush=True)
        except Exception as e:  # noqa: BLE001
            # 空表（例如 operation_log 没有数据）不该让整个同步失败 ——
            # 上游三张指标表已经导出成功了，这里少一个文件只是"这块日志还没有"
            print(f"      {out + '.parquet':24s} 跳过（{type(e).__name__}: {e}）", flush=True)


def verify(spark, decisions: pd.DataFrame) -> bool:
    """自验收：入仓后必须和源完全对得上。

    只验"没报错"是不够的 —— INSERT OVERWRITE 写了个空分区同样不报错。
    """
    ok = True

    def check(label: str, cond: bool, detail: str) -> None:
        nonlocal ok
        print(f"      [{'PASS' if cond else 'FAIL'}] {label:<44} {detail}", flush=True)
        if not cond:
            ok = False

    n_src = len(decisions)
    n_ods = spark.sql(f"SELECT count(*) c FROM {DB}.ods_decision_log").collect()[0]["c"]
    check("ODS 行数 = SQLite 行数", n_src == n_ods, f"{n_ods:,} vs {n_src:,}")

    n_part = len(spark.sql(f"SHOW PARTITIONS {DB}.ods_decision_log").collect())
    n_days = decisions["data_as_of"].nunique()
    check("ODS 分区数 = 源里不同 data_as_of 个数", n_part == n_days, f"{n_part} 个 vs {n_days} 天")

    n_dwd = spark.sql(f"SELECT count(*) c FROM {DB}.dwd_decision_event").collect()[0]["c"]
    check("DWD 行数 = ODS 行数（事件流不丢不重）", n_dwd == n_ods, f"{n_dwd:,} vs {n_ods:,}")

    dup = spark.sql(f"""
        SELECT count(*) c FROM (
            SELECT obj_type, obj_id, metric, data_as_of, count(*) n
            FROM {DB}.ods_decision_log GROUP BY 1,2,3,4 HAVING count(*) > 1
        )""").collect()[0]["c"]
    check("ODS 粒度唯一（obj × metric × 日期）", dup == 0, f"重复 {dup} 组")

    n_latest = spark.sql(f"SELECT count(*) c FROM {DB}.ads_decision_latest").collect()[0]["c"]
    n_obj = decisions[["obj_type", "obj_id"]].drop_duplicates().shape[0]
    check("ads_decision_latest 每对象一行", n_latest == n_obj, f"{n_latest:,} vs {n_obj:,} 个对象")

    # 最关键的一条：逐对象比对「数仓的最新结论」和「SQLite 的最新结论」。
    # 这条如果过了，说明入仓没有改变任何判定 —— 这是后面所有 join 的前提。
    hive_latest: Dict[Tuple[str, str], Tuple[str, float]] = {}
    for r in spark.sql(f"""
            SELECT obj_type, obj_id, verdict, value FROM {DB}.ads_decision_latest
        """).collect():
        hive_latest[(r["obj_type"], r["obj_id"])] = (r["verdict"], r["value"])

    src_latest = (
        decisions.sort_values(["data_as_of"])
        .groupby(["obj_type", "obj_id"], as_index=False).tail(1)
    )
    mismatch = []
    for r in src_latest.itertuples(index=False):
        key = (r.obj_type, r.obj_id)
        if key not in hive_latest:
            mismatch.append(f"{key} 缺失")
        elif hive_latest[key][0] != r.verdict:
            mismatch.append(f"{key} {hive_latest[key][0]}≠{r.verdict}")
    check("数仓最新结论 = SQLite 最新结论", not mismatch,
          f"{len(src_latest):,} 个对象全对" if not mismatch else f"{len(mismatch)} 处不一致，例如 {mismatch[:3]}")

    # 导出文件行数 = Hive 表行数（防止 coalesce(1) 写出时丢行）
    for out, table in [("decision_log", "ods_decision_log"),
                       ("decision_latest", "ads_decision_latest"),
                       ("decision_stats", "ads_decision_stats")]:
        p = ADS_OUT / f"{out}.parquet"
        if not p.exists():
            check(f"{out}.parquet 存在", False, "文件缺失")
            continue
        n_file = len(pd.read_parquet(p))
        n_hive = spark.sql(f"SELECT count(*) c FROM {DB}.{table}").collect()[0]["c"]
        check(f"{out}.parquet 行数 = Hive 表", n_file == n_hive, f"{n_file:,} vs {n_hive:,}")

    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="把中台决策日志同步进数仓")
    ap.add_argument("--skip-export", action="store_true", help="只更新 Hive，不导出 parquet")
    args = ap.parse_args()

    print("=" * 62)
    print("决策日志入仓：SQLite(dim.db) → ODS → DWD → ADS → parquet")
    print("=" * 62)

    decisions = load_decisions()
    operations = load_operations()
    print(f"\n[源] decision_log {len(decisions):,} 行 / operation_log {len(operations):,} 行")
    print(f"     data_as_of {decisions['data_as_of'].min()} ~ {decisions['data_as_of'].max()}"
          f"（{decisions['data_as_of'].nunique()} 天）")

    from gen_iaa_users import build_session  # noqa: PLC0415  延迟导入：--help 不需要 Spark

    spark = build_session()
    try:
        print("\n[1/4] 建表 ...", flush=True)
        create_tables(spark)

        print("\n[2/4] ODS 原样落地（按 data_as_of 动态分区覆盖）...", flush=True)
        load_ods(spark, decisions, operations)

        print("\n[3/4] DWD 事件流 → ADS 最新结论 + 判定分布 ...", flush=True)
        build_dwd(spark)
        build_ads(spark)

        if args.skip_export:
            print("\n[4/4] 跳过导出", flush=True)
        else:
            print(f"\n[4/4] 导出物化 parquet → {ADS_OUT} ...", flush=True)
            export_all(spark)

        print("\n[验收]", flush=True)
        ok = verify(spark, decisions)
        print(f"\n      {'全部通过' if ok else '存在失败项'}", flush=True)
        return 0 if ok else 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
