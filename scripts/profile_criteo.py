"""Criteo 归因数据集的数据探查（低内存版）。

建表之前必须先看数据 —— 这是数据工程最重要的一条纪律：
不要凭记忆写 schema，不要凭想象定分区粒度。

**为什么分两套手段**：
    你机器只有 16 GB，Docker + 其他容器已经占掉一半。
    把 2.47 GB 的 TSV 当 22 列字符串整体扫两遍，JVM 会直接 OOM。
    所以：
      · 字段类型推断 → 用 Python 读前 20 万行（够用，类型不会变）
      · 时间分布 / 漏斗指标 / campaign 倾斜 → Spark 只 select 需要的那几列

用法：
    ./.venv-spark/Scripts/python.exe -u scripts/profile_criteo.py
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter

_HADOOP_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop")
)
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

TSV = "D:/data/criteo/criteo_attribution_dataset.tsv"
SAMPLE_ROWS = 200_000

# 建表时要用的真实类型（来自字段语义，不是猜的）：
#   计数/金额/时间 → 数值；cat*/campaign/uid → 标识符，当字符串或 bigint 存
INT_COLS = {"timestamp", "uid", "campaign", "conversion", "conversion_timestamp",
            "conversion_id", "attribution", "click", "click_pos", "click_nb"}
DOUBLE_COLS = {"cost", "cpo", "time_since_last_click"}


def profile_types() -> list[str]:
    """用前 SAMPLE_ROWS 行推断每列是数值还是字符串。"""
    print("[1/4] 字段类型（前 20 万行）...", flush=True)
    with open(TSV, encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader)
        nonnum = Counter()
        seen = Counter()
        uniq = {c: set() for c in header}
        mn = {c: float("inf") for c in header}
        mx = {c: float("-inf") for c in header}
        for i, row in enumerate(reader):
            if i >= SAMPLE_ROWS:
                break
            for c, v in zip(header, row):
                seen[c] += 1
                try:
                    f = float(v)
                    mn[c] = min(mn[c], f)
                    mx[c] = max(mx[c], f)
                except ValueError:
                    nonnum[c] += 1
                if len(uniq[c]) < 5000:
                    uniq[c].add(v)

    print(f"      {'列名':<24}{'非数值率':>10}{'基数(≤5000)':>13}{'最小':>13}{'最大':>14}  推断类型", flush=True)
    types = []
    for c in header:
        rate = nonnum[c] / max(seen[c], 1)
        if c in INT_COLS:
            t = "bigint"
        elif c in DOUBLE_COLS:
            t = "double"
        elif rate > 0.1:
            t = "string"
        else:
            t = "bigint/double"
        types.append(t)
        flag = ""
        if rate > 0.99:
            flag = "  <- 全空/全非数值"
        print(f"      {c:<24}{rate:>10.2%}{len(uniq[c]):>13}{mn[c]:>13.4g}{mx[c]:>14.4g}  {t}{flag}", flush=True)
    return header


def profile_with_spark() -> None:
    from pyspark.sql import SparkSession, functions as F

    print("\n[2/4] 启动 Spark（local[2] / 2g，只扫需要的列）...", flush=True)
    spark = (
        SparkSession.builder.appName("profile-criteo")
        .master("local[2]")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    base = (
        spark.read.option("sep", "\t")
        .option("header", "true")
        .option("inferSchema", "false")
        .csv(TSV)
        .select(
            F.col("timestamp").cast("long").alias("ts"),
            F.col("campaign").cast("long").alias("campaign"),
            F.col("click").cast("int").alias("click"),
            F.col("conversion").cast("int").alias("conv"),
            F.col("cost").cast("double").alias("cost"),
        )
        .cache()
    )

    print("\n[3/4] 时间范围与按天分布 ...", flush=True)
    tmin, tmax, n = base.agg(
        F.min("ts"), F.max("ts"), F.count("*")
    ).collect()[0]
    span = (tmax - tmin) / 86400
    print(f"      总行数: {n:,}", flush=True)
    print(f"      timestamp: {tmin} ~ {tmax}（相对秒，从 0 开始）", flush=True)
    print(f"      跨度: {span:.2f} 天", flush=True)

    days = (
        base.select(((F.col("ts") - tmin) / 86400).cast("int").alias("day"))
        .groupBy("day").count().orderBy("day").collect()
    )
    counts = [r["count"] for r in days]
    avg = sum(counts) / len(counts)
    print(f"\n      共 {len(days)} 天，日均 {avg:,.0f} 行：", flush=True)
    for r in days:
        bar = "#" * max(1, int(r["count"] / avg * 32))
        print(f"        day{r['day']:<3} {r['count']:>10,}  {bar}", flush=True)
    ratio = max(counts) / min(counts)
    print(f"      最大/最小 = {ratio:.2f} 倍"
          f"{'（均匀）' if ratio < 1.5 else '（不均匀，注意倾斜）'}", flush=True)

    print("\n[4/4] 漏斗指标与 campaign 倾斜 ...", flush=True)
    m = base.agg(
        F.count("*").alias("imp"),
        F.sum("click").alias("clk"),
        F.sum("conv").alias("conv"),
        F.sum("cost").alias("cost"),
    ).collect()[0]
    imp, clk, conv, cost = m["imp"], m["clk"], m["conv"], m["cost"]
    print(f"      impressions = {imp:>16,}   （一行 = 一次展示）", flush=True)
    print(f"      clicks      = {clk:>16,}   CTR = {clk/imp:.4%}", flush=True)
    print(f"      conversions = {conv:>16,}   CVR = {conv/imp:.4%}", flush=True)
    print(f"      cost 合计   = {cost:>16,.2f}", flush=True)
    if conv:
        print(f"      CPA         = {cost/conv:>16,.4f}", flush=True)
    if clk:
        print(f"      CPC         = {cost/clk:>16,.4f}", flush=True)

    camps = (
        base.groupBy("campaign")
        .agg(F.count("*").alias("imp"), F.sum("click").alias("clk"), F.sum("conv").alias("conv"))
        .orderBy(F.col("imp").desc())
        .collect()
    )
    print(f"\n      campaign 数量: {len(camps)}", flush=True)
    for r in camps[:8]:
        print(f"        {r['campaign']:<12} {r['imp']:>12,} ({r['imp']/imp:>6.2%})"
              f"  CTR={r['clk']/max(r['imp'],1):>7.3%}  conv={r['conv']:>6,}", flush=True)
    top = camps[0]["imp"] / imp
    print(f"      最大 campaign 占 {top:.2%}"
          f"{'  <- 倾斜源头：DWS 按 campaign 聚合会有长尾 task' if top > 0.25 else ''}", flush=True)

    spark.stop()

    print("\n===== 建表决策 =====", flush=True)
    print(f"  · 分区粒度：按 dt 分区，共 {len(days)} 个分区，日均 {avg:,.0f} 行", flush=True)
    print("    不用到小时：30×24=720 个分区，每分区才 2 万多行，小文件问题会先于收益出现", flush=True)
    print("  · 事实表粒度：一次展示一行（impression 级）", flush=True)
    print("  · 维度：campaign（渠道）、cat1~cat9（人群/素材属性）", flush=True)
    print("  · 金额只有 cost，没有收入 → 能算 CPA/CPC/CTR/CVR，ROI 算不了（别硬编）", flush=True)


def main() -> int:
    if not os.path.exists(TSV):
        print(f"找不到 {TSV}，先跑 scripts/download_criteo.py")
        return 1
    profile_types()
    profile_with_spark()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
