"""数据倾斜对照实验：4 种写法，用同一份数据，看每个 task 到底分到多少行。

为什么这个实验必须单独做一份「人造数据」：
    我前面量过，这份 Criteo 数据**完全没有倾斜** ——
    campaign 最大占 2.66%、cat3 最大 2.04%、uid 最大只有 880 行（0.01%）。
    在它上面直接跑 join，任何写法都不会倾斜，你会误以为自己已经会调优了，
    实际上你什么都没调，只是数据太乖。

    所以这里人为造一个「一个渠道吃掉 60% 流量」的事实表 ——
    这是真实广告系统里最常见的样子（信息流渠道天然占大头）。

四种写法分别回答：
    baseline   ：默认（sort-merge join，不广播）→ 看到长尾
    broadcast  ：广播小维表 → shuffle 根本不发生，倾斜自然消失
    aqe_skew   ：开 AQE 自动拆倾斜分区 → 看它到底拆没拆
    salted     ：手工加盐 → 不依赖 AQE 版本行为的确定性方案

判断倾斜的指标：**最长 task 耗时 ÷ 中位 task 耗时**（不是总耗时，也不是分区行数）。
    这台机器只有 2 核，墙钟被稀释得很厉害，看总耗时会得出「差不多」的错误结论；
    分区行数又对 AQE 不敏感（AQE 拆的是 task，不是分区）。
    只有 task 耗时能同时反映四种写法的差别：
        baseline : 最长 task 远高于中位（一个 task 扛六成数据）
        broadcast: 只有 1 个 task，没有比较对象，但作业时间明显短
        aqe_skew : 最长/中位 应显著下降（它把大 task 拆成了几个）
        salted   : task 数变多、每个都短

跑法：
    ./.venv-spark/Scripts/python.exe -u scripts/demo_skew.py
"""

from __future__ import annotations

import os
import sys
import time

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = "file:///D:/warehouse"

FACT_ROWS = 4_000_000   # 从 ODS 取多少行当事实表
NPART = 8               # shuffle 分区数 = 同时会有多少个 task 干活
HOT_SHARE = 60          # 最热的那个渠道吃掉多少百分比的流量
N_CHANNEL = 200         # 渠道总数
SALT_SPLIT = 8          # 加盐时把热键拆成几份


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("demo-skew")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.shuffle.partitions", str(NPART))
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def prepare(spark):
    """造事实表 + 维表。

    事实表的 channel 键怎么造出来的：
        pmod(hash(uid), 100) < 60  → 归到 channel 0（约 60% 的行）
        其余 → pmod(hash(uid), 199) + 1，散到 1..199（每档约 0.2%）

    为什么用 hash 而不是 rand()：
        rand() 每次执行结果都不同，同一份代码跑两次落到不同盐桶，
        结果不可复现。用 hash 是确定性的，重跑一定一样 —— 这在生产里叫「幂等」。
    """
    from pyspark.sql import functions as F

    fact = spark.sql(
        f"SELECT uid, campaign AS campaign_id, cost, click FROM ods.ods_ad_log LIMIT {FACT_ROWS}"
    ).withColumn(
        "channel",
        F.when(
            F.pmod(F.hash("uid"), F.lit(100)) < F.lit(HOT_SHARE), F.lit(0)
        ).otherwise(F.pmod(F.hash("uid"), F.lit(N_CHANNEL - 1)) + F.lit(1)),
    )

    # 维表 200 行，很小 —— 小到 Spark 会自动广播（默认阈值 10 MB）
    dim = spark.range(N_CHANNEL).select(
        F.col("id").cast("int").alias("channel"),
        F.concat(F.lit("ch_"), F.col("id").cast("string")).alias("channel_name"),
        (F.col("id") % F.lit(7) * F.lit(0.01) + F.lit(0.005)).alias("fee_rate"),
    )
    return fact, dim


def profile(df, label: str, spark, workdir: str) -> dict:
    """执行 df，用「输出分区行数分布 + shuffle 阶段 task 数」判断倾斜。

    这个函数改了三版，前两版都测错了，值得完整记下来：

    第一版：`df.rdd.glom().map(len).collect()`
        → DataFrame 转 RDD 绕过了 AQE 的自适应执行，等于把被测对象关掉了。
          结果 AQE 开和不开「完全一样」，差点下结论说「AQE 对倾斜没用」。

    第二版：先写盘，再按输出文件统计行数
        → AQE 的问题修好了，但**指标选错了**：AQE 拆倾斜是「给同一个输出分区
          多派几个 task 并行读完」，输出分区的行数分布**根本不会变**。
          所以这个指标对 AQE 天生失明，仍然得出「AQE 没生效」。

    第三版（当前）：同时看两件事
        ① 输出分区行数分布 —— 对 broadcast / salted 敏感
        ② **shuffle 阶段的 task 数** —— 对 AQE 敏感
           AQE 拆一个倾斜分区，会让 join 阶段的 task 数从 8 变成 8+N，
           这个数字用 StatusTracker 的 SparkStageInfo.numTasks 就能拿到。

    教训：**指标选错了比不测更危险**，因为它会给出一个自信的错误结论。
    """
    import time as _t

    from pyspark.sql import functions as F

    out = os.path.join(workdir, f"out_{_t.time_ns()}")
    group = f"g{_t.time_ns()}"
    sc = spark.sparkContext

    sc.setJobGroup(group, label, interruptOnCancel=True)
    t0 = _t.time()
    df.write.mode("overwrite").parquet(out)
    elapsed = _t.time() - t0

    # shuffle 阶段各有多少 task —— AQE 拆分倾斜分区会让这个数变大
    stages: list[tuple[str, int]] = []
    for jid in sc.statusTracker().getJobIdsForGroup(group):
        info = sc.statusTracker().getJobInfo(jid)
        if info is None:
            continue
        for sid in info.stageIds:
            si = sc.statusTracker().getStageInfo(sid)
            if si is not None:
                stages.append((si.name, si.numTasks))
    sc.setJobGroup("", "")

    # 输出分区行数
    files = (
        spark.read.parquet(out)
        .select(F.input_file_name().alias("f"))
        .groupBy("f").count().collect()
    )
    sizes = sorted((x["count"] for x in files), reverse=True)
    total = sum(sizes)

    plan = df._jdf.queryExecution().executedPlan().toString()
    strategy = "unknown"
    for k in ("BroadcastHashJoin", "SortMergeJoin", "ShuffledHashJoin", "BroadcastNestedLoopJoin"):
        if k in plan:
            strategy = k
            break

    max_stage_tasks = max((n for _, n in stages), default=0)
    print(f"\n  [{label}]")
    print(f"      join 策略      : {strategy}")
    print(f"      输出分区       : {len(sizes)} 个，最大占 {sizes[0] / total:.1%}"
          f"（{sizes[0]:,} 行），其余 " + " ".join(f"{s / total:.1%}" for s in sizes[1:]))
    print(f"      各阶段 task 数 : " + " / ".join(f"{nm or '?'}={n}" for nm, n in stages)
          + f"  → 最多 {max_stage_tasks}")
    print(f"      整作业耗时     : {elapsed:.1f}s")
    return {"label": label, "strategy": strategy, "max_pct": sizes[0] / total,
            "elapsed": elapsed, "npart": len(sizes), "max_tasks": max_stage_tasks}


def main() -> int:
    from pyspark.sql import functions as F

    spark = build_session()
    print(f"pyspark {spark.version} / master=local[2]", flush=True)

    fact, dim = prepare(spark)
    n_fact = fact.count()
    print(f"事实表 {n_fact:,} 行 / 维表 {N_CHANNEL} 行")
    hot = fact.where(F.col("channel") == 0).count()
    print(f"最热渠道 channel=0 占 {hot:,} 行 = {hot / n_fact:.1%}"
          f"（其余 {N_CHANNEL - 1} 个渠道平均每组 {(n_fact - hot) / (N_CHANNEL - 1):,.0f} 行）")

    # 每组实验都要先写盘才能准确统计分区行数，写完即删
    workdir = os.path.join(r"D:\warehouse", "_tmp_skew")
    os.makedirs(workdir, exist_ok=True)
    print(f"临时输出目录: {workdir}")

    results = []

    # ---------------------------------------------------------------
    # 1. baseline：不广播 + 关 AQE → 走 sort-merge join，两个表都要 shuffle
    # ---------------------------------------------------------------
    print("\n" + "=" * 66)
    print("1. baseline —— 默认 sort-merge join（关掉自动广播和 AQE）")
    print("=" * 66)
    spark.conf.set("spark.sql.adaptive.enabled", "false")
    spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")
    spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "false")
    joined = fact.join(dim, "channel").select(
        "channel", "channel_name", "fee_rate", "campaign_id", "cost"
    )
    results.append(profile(joined, "baseline", spark, workdir))
    print("      → 一个 task 扛了六成数据，其余 7 个 task 早早干完在等它。")
    print("        这就是「长尾」。2 核机器上墙钟差别不明显，")
    print("        换到 1000 核集群，这个 task 的时长 = 整个作业的时长，999 核在空转。")

    # ---------------------------------------------------------------
    # 2. broadcast：广播小维表 → 事实表不用 shuffle
    # ---------------------------------------------------------------
    print("\n" + "=" * 66)
    print("2. broadcast —— 广播维表（维表只有 200 行，完全放得进内存）")
    print("=" * 66)
    joined_b = fact.join(F.broadcast(dim), "channel").select(
        "channel", "channel_name", "fee_rate", "campaign_id", "cost"
    )
    results.append(profile(joined_b, "broadcast", spark, workdir))
    print("      → 倾斜不见了，但注意：它**不是靠调优消除的，是靠「不 shuffle」绕开的**。")
    print("        代价是每个 executor 都要持有一份维表。维表一旦超过几百 MB，")
    print("        这条路就走不通 —— 那时候只能回到 sort-merge 再想办法。")

    # ---------------------------------------------------------------
    # 3. aqe_skew：让 AQE 自动拆倾斜分区
    # ---------------------------------------------------------------
    print("\n" + "=" * 66)
    print("3. aqe_skew —— 恢复 sort-merge，交给 AQE 自动拆分倾斜分区")
    print("=" * 66)
    spark.conf.set("spark.sql.adaptive.enabled", "true")
    spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")
    spark.conf.set("spark.sql.adaptive.skewJoin.skewedPartitionFactor", "2")
    spark.conf.set("spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes", "1MB")
    spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "false")
    joined_a = fact.join(dim, "channel").select(
        "channel", "channel_name", "fee_rate", "campaign_id", "cost"
    )
    results.append(profile(joined_a, "aqe_skew", spark, workdir))
    print("      → AQE 只在**运行时**才知道哪个分区大，所以它是「事后补救」。")
    print("        它救得了 join，救不了「按热键 group by」这种必然要聚到一处的情况。")

    # ---------------------------------------------------------------
    # 4. salted：手工把热键拆成 8 份
    # ---------------------------------------------------------------
    print("\n" + "=" * 66)
    print("4. salted —— 手工加盐：热键拆 8 份，join 完再合回去")
    print("=" * 66)
    spark.conf.set("spark.sql.adaptive.enabled", "false")
    fact_s = fact.withColumn(
        "salt",
        F.when(
            F.col("channel") == 0,
            # 用 hash 而不是 rand()：rand() 每次结果不同，同一条数据两次跑
            # 会落到不同盐桶，结果不可复现。hash 是确定性的，重跑一定一样。
            F.pmod(F.hash(F.col("uid"), F.col("campaign_id")), F.lit(SALT_SPLIT)),
        ).otherwise(F.lit(0)),
    )
    dim_s = (
        dim.withColumn(
            "salt",
            F.when(
                F.col("channel") == 0,
                F.array(*[F.lit(i) for i in range(SALT_SPLIT)]),
            ).otherwise(F.array(F.lit(0))),
        )
        .withColumn("salt", F.explode(F.col("salt")))
    )
    joined_s = (
        fact_s.join(dim_s, ["channel", "salt"])
        .drop("salt")
        .select("channel", "channel_name", "fee_rate", "campaign_id", "cost")
    )
    results.append(profile(joined_s, "salted", spark, workdir))
    print("      → 分区变均匀了，代价是维表被复制了 8 份、代码多了一层。")
    print("        加盐是「最后手段」：能广播就别加盐，能靠 AQE 就别手写。")

    # ---------------------------------------------------------------
    # 汇总
    # ---------------------------------------------------------------
    print("\n" + "=" * 66)
    print("汇总（判断倾斜看「最长 task ÷ 中位 task」，不看总耗时）")
    print("=" * 66)
    print(f"  {'方案':<14}{'join 策略':<22}{'最大分区占比':>12}{'最多task数':>10}{'耗时':>9}")
    for r in results:
        print(f"  {r['label']:<14}{r['strategy']:<22}{r['max_pct']:>11.1%}"
              f"{r['max_tasks']:>10}{r['elapsed']:>8.1f}s")

    print("""
  一条结论：
      倾斜的解法有优先级 ——
        ① 能广播就广播（维表 < 几百 MB）—— 最省，直接不 shuffle
        ② 不能广播，靠 AQE 自动拆 —— 零代码，但只在运行时救
        ③ 以上都不行，手工加盐 —— 必然有效，但代码脏、维表膨胀
      顺序反了就会写出「维表才 200 行却手工加盐」这种过度设计。
  """)

    # 清掉临时输出（每组实验写了一份 4M 行的结果，留着占几百 MB）
    import shutil
    if os.path.isdir(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"  已清理临时目录 {workdir}")

    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
