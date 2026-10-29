"""第 5 步：ADS 层 —— 中台真正要读的三张小表。

为什么要这一层（而不是让中台直接查 DWS）：
    DWS 是「可以被任意切片的原子量」，一张表 22 万行，查询要现算比率。
    中台要的是「已经算好、直接显示」的结果：某个素材的 D7 ROI 是多少。
    这两个需求不一样：
        DWS  → 给分析用，任何维度都能重新聚合
        ADS  → 给展示用，固定几个切面，值已经算好

三层的关系是「越来越窄、越来越小」：
    DWD 3,568 万行 → DWS 22 万行 → ADS 1.3 万行

关于「ADS 为什么可以存比率」：
    DWD / DWS 不许存比率，因为比率要被再聚合（avg(ctr) 是错的）。
    ADS 是**终点**，它的输出直接进图表和 LLM 的上下文，不会再有 SUM/AVG 上卷。
    所以这里存 ctr / cvr / cpi / d7_roi 是对的 —— 前提是它们从不被二次聚合。
    这个边界必须写清楚，否则有人会从 ads 表 sum(ctr)，那是另一场灾难。

产出（都进 Hive，中台通过 warehouse_api 读）：
    lab.ads_daily_detail        13,981 行   cohort 宽表 D0~D7（对齐原 daily_detail）
    lab.ads_creative_table         200 行   素材汇总（对齐原 creative_table）
    lab.ads_sub_channel_table       11 行   子渠道汇总（对齐原 sub_channel_table）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime

import pandas as pd

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable

METASTORE_URI = "thrift://127.0.0.1:9083"
WAREHOUSE = os.environ.get("IAA_WAREHOUSE", "file:///D:/warehouse")
DB = "lab"

# 中台（iaa-analytics-agent）原来的 ADS 目录。导出到这里，
# agent.py 里那 6 处 pd.read_parquet 路径完全不用改就能读到新数据。
ADS_OUT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "iaa-analytics-agent", "data", "warehouse", "ads")
)

COHORT = "lab.dws_cohort_daily"
MAX_DT = "2026-10-31"

# 天数轴：中台原来只展示 D0~D7（8 天），保持一致
N_DAYS = 8

# campaign → 优化策略。原项目存在 dim.db 的 dim_campaign 里，
# 这里用 CASE 直接映射（5 个 campaign 固定不变，不值得为它建维表 join）。
STRATEGY_CASE = """
    CASE campaign
        WHEN 'US_A_01' THEN 'D7_ROAS'
        WHEN 'US_B_01' THEN 'CPI'
        WHEN 'JP_A_01' THEN 'D0_ROAS'
        WHEN 'CA_A_01' THEN 'D7_ROAS'
        WHEN 'GB_A_01' THEN 'CPI'
        ELSE 'UNKNOWN'
    END
"""

# 各指标列的 DDL 类型
DETAIL_COLS: list[tuple[str, str]] = (
    [("install_date", "date"), ("app", "string"), ("country", "string"),
     ("campaign", "string"), ("creative_id", "string"), ("sub_channel", "string"),
     ("installs", "bigint"), ("spend", "decimal(18,6)")]
    + [(f"ret_{n}", "double") for n in range(N_DAYS)]
    + [(f"roi_{n}", "double") for n in range(N_DAYS)]
    + [(f"ecpm_{n}", "double") for n in range(N_DAYS)]
    + [(f"view_{n}", "double") for n in range(N_DAYS)]
    # 这四个是**汇总口径别名**，补的是原项目的一个缺陷：
    #   _tool_get_top_history / _tool_get_trend_with_ops / _tool_get_metric
    #   读的是 daily_detail，但那张表只有 roi_0..roi_7 / ret_0..ret_7，
    #   没有 d7_roi / d7_retention / d7_ecpm / cpi，
    #   于是这些工具传 metric="d7_roi" 时永远返回「指标不存在」。
    #   而 _load_top 读的是 creative_table（有这几列），所以从外面看一切正常 ——
    #   典型的「两个工具读不同的表，却共用一套指标命名」。
    #
    #   补全这四列之后，明细层可以自包含地回答「这个对象的 D7 指标是多少」，
    #   中台所有工具都能统一走 daily_detail，不再需要按指标去挑表。
    #   这也是 2026-10-23 那次改动的直接动因：_tool_get_metric 原先读
    #   sub_channel_table（有 d7_retention 但**没有 is_mature_d7**），
    #   改成读明细层后立刻暴露了 d7_retention 缺失。
    + [("d7_roi", "double"), ("cpi", "double"),
       ("d7_retention", "double"), ("d7_ecpm", "double")]
    # 成熟度标记：这批 cohort 的 D7 观察窗口是否已经完整。
    # 为什么必须有这一列：
    #     10-25 之后装机的用户，到 10-31 只活了 0~6 天，D7 收入还没产生。
    #     但报表照样会算出一个 d7_roi —— 分子只有 D0~D6 的收入，分母却是全部成本，
    #     所以数值天然偏低。
    #     实测 ch_001 的 D7 ROI 从 10-24 的 0.73 掉到 10-31 的 0.36，
    #     看起来像「渠道急剧恶化」，其实只是观察窗口不够。
    #     任何算 D7 类指标的查询都必须先过滤 is_mature_d7 = true，
    #     否则「越新的数据看起来越差」这个假象会误导所有决策。
    + [("is_mature_d7", "boolean")]
    + [("strategy", "string")]
    + [(f"dact{n}", "bigint") for n in range(N_DAYS)]
    + [(f"drev{n}", "decimal(18,6)") for n in range(N_DAYS)]
    + [(f"dview{n}", "bigint") for n in range(N_DAYS)]
    + [(f"dimp{n}", "bigint") for n in range(N_DAYS)]
)

AGG_COLS: list[tuple[str, str]] = [
    ("dim_id", "string"),
    ("install_date", "date"),
    ("spend", "decimal(18,6)"),
    ("clicks", "bigint"),
    ("buy_impressions", "bigint"),
    ("installs", "bigint"),
    ("ctr", "double"),
    ("cvr", "double"),
    ("cpi", "double"),
    ("d7_retention", "double"),
    ("d7_roi", "double"),
    ("d7_ecpm", "double"),
]


def build_session():
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("build-ads-iaa")
        .master("local[2]")
        .config("spark.sql.warehouse.dir", WAREHOUSE)
        .config("spark.hadoop.hive.metastore.uris", METASTORE_URI)
        .config("spark.sql.catalogImplementation", "hive")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.shuffle.partitions", "8")
        .enableHiveSupport()
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def create_tables(spark) -> None:
    def ddl(table: str, cols: list[tuple[str, str]]) -> str:
        body = ",\n            ".join(f"`{c}` {t}" for c, t in cols)
        return f"""
        CREATE TABLE IF NOT EXISTS {DB}.{table} (
            {body}
        )
        STORED AS PARQUET
        LOCATION '{WAREHOUSE}/{DB}.db/{table}'
        """

    def ensure(table: str, cols: list[tuple[str, str]]) -> None:
        """建表，但如果已存在的表结构和新定义不一致，先 DROP 再建。

        为什么必须显式处理：
            `CREATE TABLE IF NOT EXISTS` 对已存在的表是**静默空操作**。
            上一轮 ADS 是 73 列，这一轮加了 d7_roi / cpi 变成 75 列，
            如果只是再跑一遍建表语句，旧表原封不动，而 INSERT 是按位置写的 ——
            结果就是 dt 的数据写进 instal_date、spend 写进 ctr，零报错。
            这个坑在 DWD 层踩过一次（首次点击算成 13 万亿），
            代价是排查了很久。同样的坑不踩第二次。

        ADS 是终点表（下游只有读），DROP 重建是安全的。
        """
        if spark.catalog.tableExists(f"{DB}.{table}"):
            actual = [f.name for f in spark.table(f"{DB}.{table}").schema.fields]
            expected = [c for c, _ in cols]
            if actual != expected:
                print(f"      {table} 结构变化 {len(actual)} → {len(expected)} 列，重建", flush=True)
                spark.sql(f"DROP TABLE IF EXISTS {DB}.{table}")
        spark.sql(ddl(table, cols))

    ensure("ads_daily_detail", DETAIL_COLS)
    ensure("ads_creative_table", AGG_COLS)
    ensure("ads_sub_channel_table", AGG_COLS)


def sql_daily_detail() -> str:
    """cohort 宽表：把 DWS 的长表（day_offset 一行）压成 D0~D7 横铺。

    每一列都对应 metrics.yaml 里一条口径：
        dact{n} / drev{n} / dimp{n} / dview{n}  → 原子量，原样横铺
        ret_{n}  = dact_n / installs                  （retention_dn）
        roi_{n}  = sum(drev_0..n) / spend             （roi_dn，累计口径）
        ecpm_{n} = drev_n / dimp_n * 1000             （ecpm_dn，单日口径）
        view_{n} = dview_n / dact_n                   （views_dn）

    注意 roi 是**累计**、ecpm 是**单日** —— 这两个口径不一样，
    metrics.yaml 里写得很清楚（roi_dn 说「累计口径」、ecpm_dn 说「单日口径」）。
    混了会得到「D7 的 eCPM 是 0.5 倍」这种诡异结论。
    """
    dact = ",\n        ".join(
        f"max(CASE WHEN day_offset = {n} THEN active_users END) AS dact{n}"
        for n in range(N_DAYS))
    drev = ",\n        ".join(
        f"max(CASE WHEN day_offset = {n} THEN ad_revenue END) AS drev{n}"
        for n in range(N_DAYS))
    dimp = ",\n        ".join(
        f"max(CASE WHEN day_offset = {n} THEN ad_impressions END) AS dimp{n}"
        for n in range(N_DAYS))
    dview = ",\n        ".join(
        f"max(CASE WHEN day_offset = {n} THEN rewarded_views END) AS dview{n}"
        for n in range(N_DAYS))

    # 累计收入：roi_n 的分子
    cum = ",\n        ".join(
        "sum(CASE WHEN day_offset <= {n} THEN ad_revenue ELSE 0 END) AS cum_rev{n}".format(n=n)
        for n in range(N_DAYS))

    # 比率列。统一加 NULLIF 保护：分母为 0 时返回 NULL 而不是抛异常或返回 Infinity，
    # 因为 NULL 表示「无法计算」，Infinity 会污染后续的 avg() 和图表。
    ratios = ",\n        ".join(
        f"CASE WHEN installs > 0 THEN dact{n} / installs END AS ret_{n}" for n in range(N_DAYS)
    ) + ",\n        " + ",\n        ".join(
        f"CASE WHEN spend > 0 THEN cum_rev{n} / spend END AS roi_{n}" for n in range(N_DAYS)
    ) + ",\n        " + ",\n        ".join(
        f"CASE WHEN dimp{n} > 0 THEN drev{n} / dimp{n} * 1000 END AS ecpm_{n}" for n in range(N_DAYS)
    ) + ",\n        " + ",\n        ".join(
        f"CASE WHEN dact{n} > 0 THEN dview{n} / dact{n} END AS view_{n}" for n in range(N_DAYS)
    )

    return f"""
    WITH base AS (
        SELECT
            install_date, app, country, campaign, creative_id, sub_channel,
            max(installs) AS installs,
            max(spend)    AS spend,
            {dact},
            {drev},
            {dimp},
            {dview},
            {cum}
        FROM {COHORT}
        GROUP BY install_date, app, country, campaign, creative_id, sub_channel
    )
    SELECT
        install_date, app, country, campaign, creative_id, sub_channel,
        installs, spend,
        {ratios},
        -- 汇总口径别名（见 DETAIL_COLS 里的说明）
        CASE WHEN spend > 0 THEN cum_rev7 / spend END          AS d7_roi,
        CASE WHEN installs > 0 THEN spend / installs END       AS cpi,
        -- d7_retention 的 NULL 语义必须区分三种情况（和 sql_dim_summary 保持一致）：
        --   观测窗口没走完        → NULL（不知道，不能说成 0）
        --   窗口走完但 D7 没记录  → 0（用户真的全流失了）
        --   正常                  → dact7 / installs
        CASE WHEN installs > 0 AND date_add(install_date, {N_DAYS - 1}) <= to_date('{MAX_DT}')
             THEN coalesce(dact7, 0) / installs END            AS d7_retention,
        CASE WHEN dimp7 > 0 THEN drev7 / dimp7 * 1000 END      AS d7_ecpm,
        date_add(install_date, {N_DAYS - 1}) <= to_date('{MAX_DT}')  AS is_mature_d7,
        {STRATEGY_CASE} AS strategy,
        {", ".join(f"dact{n}" for n in range(N_DAYS))},
        {", ".join(f"drev{n}" for n in range(N_DAYS))},
        {", ".join(f"dview{n}" for n in range(N_DAYS))},
        {", ".join(f"dimp{n}" for n in range(N_DAYS))}
    FROM base
    """


def sql_dim_summary(dim_col: str, extra_cols: str = "") -> str:
    """素材 / 子渠道的汇总表（一行 = 一个对象 × 一个装机日）。

    d7_retention 的 NULL 处理是本表最微妙的一处：
        cohort 表里「没有行」不等于「0 个用户」。
        如果某批用户到 D7 全流失了，D7 那天**没有任何记录**，
            sum(CASE WHEN day_offset=7 THEN active_users END) 返回 NULL
        但语义上它应该是 0/installs = 0%。

        反过来，如果这批用户装机太晚（10-28 装，观察不到 D7），
        也返回 NULL，但语义是「无法观测」。

        这两种 NULL 必须分开，否则报表会把「还没来得及看」说成「留存为 0」。
        所以这里用窗口边界判断：date_add(install_date, 7) <= '{MAX_DT}' 才算能观测。
        能观测 + 没数据 = 0；观测不了 = NULL。
    """
    return f"""
    WITH base AS (
        SELECT
            {dim_col} AS dim_id,
            {extra_cols}
            install_date,
            sum(installs) AS installs,
            sum(spend)    AS spend,
            sum(CASE WHEN day_offset <= 6 THEN ad_revenue ELSE 0 END)  AS rev7,
            sum(CASE WHEN day_offset = 7 THEN active_users END)        AS users7,
            sum(CASE WHEN day_offset = 7 THEN ad_revenue ELSE 0 END)   AS rev_d7,
            sum(CASE WHEN day_offset = 7 THEN ad_impressions ELSE 0 END) AS impr_d7
        FROM {COHORT}
        -- 这里**不能**加 WHERE {dim_col} IS NOT NULL。
        -- 约 3% 的流量是渠道直投（没有素材 ID），如果把它们过滤掉，
        -- 素材表的 installs/spend 合计会比 cohort 表少 3%，
        -- 而且「三个表总额对不上」会变成一个长期的、没人说得清的口径问题。
        -- 保留 NULL 组：它在 Hive 里合法，pandas groupby 默认 dropna=True
        -- 会自动把它排除出排行榜，两边都不受影响。
        GROUP BY {dim_col}, {extra_cols} install_date
    )
    SELECT
        dim_id,
        install_date,
        spend,
        coalesce(clicks, 0)          AS clicks,
        coalesce(buy_impressions, 0) AS buy_impressions,
        installs,
        CASE WHEN coalesce(buy_impressions, 0) > 0
             THEN clicks / buy_impressions END            AS ctr,
        CASE WHEN coalesce(clicks, 0) > 0
             THEN installs / clicks END                   AS cvr,
        CASE WHEN installs > 0 THEN spend / installs END  AS cpi,
        CASE WHEN installs > 0 AND date_add(install_date, {N_DAYS - 1}) <= to_date('{MAX_DT}')
             THEN coalesce(users7, 0) / installs END      AS d7_retention,
        CASE WHEN spend > 0 THEN rev7 / spend END         AS d7_roi,
        CASE WHEN impr_d7 > 0 THEN rev_d7 / impr_d7 * 1000 END AS d7_ecpm
    FROM base b
    """


def export_parquet(spark, table: str, out_name: str, rename: dict[str, str] | None = None,
                   order_by: str | None = None) -> int:
    """把 ADS 表导出成单文件 parquet，供中台直接读。

    为什么导出而不是让中台查 Hive：
        Spark 查一张小表要 1.8 秒（几乎全是查询规划开销），
        读一个几十 KB 的 parquet 要 5 毫秒 —— 差 360 倍。
        中台一次请求要连查 3~5 个工具，直连 Spark 会让用户等 10 秒。

        真正公司做在线服务也不会让 API 直连批处理集群，那会把集群打爆。
        标准做法就是：数仓算完 → 导出小结果 → 在线服务读小结果。
        所谓「接 OLAP」，只是把「导出 parquet」换成「同步进 ClickHouse」。

    为什么要 cast：
        · date 列导出后 pandas 读成 datetime64，JSON 序列化会失败 →
          转成字符串 '2026-10-01'（原项目就是这个格式）
        · decimal 列 pandas 读成 object(Decimal)，一旦和 float 相乘就抛异常 →
          转成 double
        这两条不做，中台一跑就崩，而且报错信息指向业务逻辑，很难查。

    order_by 为什么存在（默认 None，即不排序）：
        不加排序导出的行顺序是**不确定的** —— 表里的数据经过 shuffle 后
        分片顺序不固定，coalesce(1) 按分片顺序拼接，于是同样一份数据
        两次导出的**字节不同、内容相同**。
        实测过：内容逐字段相等，md5 却不相等。
        对于「幂等 = 重跑得到相同结果」这个断言来说，这是个尴尬的灰色地带：
        说它幂等吧，文件 hash 对不上；说它不幂等吧，数据又没错。

        所以给需要严格幂等证明的表传 order_by（排序键必须是唯一粒度，
        否则同键行之间的相对顺序仍然不确定）。
        默认不排序：ADS 三张表 1.3 万行，加排序要多一次全表 sort，
        而它们的幂等性已经由 test_idempotent_iaa.py 用**顺序无关指纹**证明了。
    """
    df = spark.table(f"{DB}.{table}")
    sel = []
    for f in df.schema.fields:
        t = f.dataType.typeName()
        new = (rename or {}).get(f.name, f.name)
        if t == "date":
            sel.append(f"cast(`{f.name}` as string) as `{new}`")
        elif t.startswith("decimal"):
            sel.append(f"cast(`{f.name}` as double) as `{new}`")
        elif new != f.name:
            sel.append(f"`{f.name}` as `{new}`")
        else:
            sel.append(f"`{f.name}`")

    tmp = os.path.join(ADS_OUT, f"_tmp_{out_name}")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    sql = f"SELECT {', '.join(sel)} FROM {DB}.{table}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    spark.sql(sql).coalesce(1).write.mode("overwrite").parquet(tmp)

    part = next(f for f in os.listdir(tmp) if f.startswith("part-"))
    final = os.path.join(ADS_OUT, f"{out_name}.parquet")
    if os.path.exists(final):
        os.remove(final)
    shutil.move(os.path.join(tmp, part), final)
    shutil.rmtree(tmp)

    pdf = pd.read_parquet(final)
    return len(pdf)


def main() -> int:
    spark = build_session()
    try:
        print("[1/5] 建表 ...", flush=True)
        create_tables(spark)

        print("\n[2/5] ads_daily_detail（cohort D0~D7 横铺）...", flush=True)
        t0 = time.time()
        spark.sql(f"INSERT OVERWRITE TABLE {DB}.ads_daily_detail\n{sql_daily_detail()}")
        n = spark.sql(f"SELECT count(*) c FROM {DB}.ads_daily_detail").collect()[0]["c"]
        print(f"      {n:,} 行  {time.time()-t0:.1f}s", flush=True)

        print("\n[3/5] ads_creative_table（素材汇总）...", flush=True)
        t0 = time.time()
        # 买量侧的 clicks / buy_impressions 按 (dt, creative_id) 聚合后左连
        sql = sql_dim_summary("creative_id")
        sql = sql.replace(
            "FROM base b",
            f"""FROM base b
            LEFT JOIN (
                SELECT dt, creative_id AS ac_dim, sum(clicks) AS clicks,
                       sum(buy_impressions) AS buy_impressions
                FROM {DB}.ods_iaa_acquisition_daily
                GROUP BY dt, creative_id
            ) a ON b.install_date = to_date(a.dt) AND b.dim_id = a.ac_dim""",
        )
        spark.sql(f"INSERT OVERWRITE TABLE {DB}.ads_creative_table\n{sql}")
        n = spark.sql(f"SELECT count(*) c FROM {DB}.ads_creative_table").collect()[0]["c"]
        print(f"      {n:,} 行  {time.time()-t0:.1f}s", flush=True)

        print("\n[4/5] ads_sub_channel_table（子渠道汇总）...", flush=True)
        t0 = time.time()
        sql = sql_dim_summary("sub_channel")
        sql = sql.replace(
            "FROM base b",
            f"""FROM base b
            LEFT JOIN (
                SELECT dt, sub_channel AS ac_dim, sum(clicks) AS clicks,
                       sum(buy_impressions) AS buy_impressions
                FROM {DB}.ods_iaa_acquisition_daily
                GROUP BY dt, sub_channel
            ) a ON b.install_date = to_date(a.dt) AND b.dim_id = a.ac_dim""",
        )
        spark.sql(f"INSERT OVERWRITE TABLE {DB}.ads_sub_channel_table\n{sql}")
        n = spark.sql(f"SELECT count(*) c FROM {DB}.ads_sub_channel_table").collect()[0]["c"]
        print(f"      {n:,} 行  {time.time()-t0:.1f}s", flush=True)

        print(f"\n[5/5] 导出物化 parquet → {ADS_OUT} ...", flush=True)
        os.makedirs(ADS_OUT, exist_ok=True)
        for table, out, rn in [
            ("ads_daily_detail", "daily_detail", None),
            ("ads_creative_table", "creative_table", {"dim_id": "creative_id"}),
            ("ads_sub_channel_table", "sub_channel_table", {"dim_id": "sub_channel"}),
        ]:
            n = export_parquet(spark, table, out, rn)
            size = os.path.getsize(os.path.join(ADS_OUT, f"{out}.parquet"))
            print(f"      {out}.parquet  {n:,} 行  {size/1024:.0f} KB", flush=True)

        # 标记「这个 ADS 目录是 Spark 版产出的」。
        # 仓库里 src/warehouse.py 还有一代 pandas 四层实现，写的是同一个目录 ——
        # 它读到这个标记会拒绝覆盖（见 services/../warehouse.py 的 _guard_ads_overwrite），
        # 否则一次误跑的 tasks.py init 会把 78 列换成 60 列，
        # 报错要到 agent 查数时才出现，查起来极费时间。
        marker = os.path.join(ADS_OUT, "_pipeline.json")
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pipeline": "spark",
                    "producer": "scripts/build_ads_iaa.py",
                    "built_at": datetime.now().isoformat(timespec="seconds"),
                    "database": DB,
                    "tables": ["daily_detail", "creative_table", "sub_channel_table"],
                    "columns": {
                        "daily_detail": len(
                            spark.sql(f"SELECT * FROM {DB}.ads_daily_detail LIMIT 0").columns
                        ),
                    },
                    "note": "pandas 四层（src/warehouse.py）读到本文件会拒绝覆盖 ADS",
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
            f.write("\n")
        print(f"      已写标记 {marker}", flush=True)

        # ---- 验收 ----
        print("\n[验收]", flush=True)
        ok = True

        def check(label: str, cond: bool, detail: str) -> None:
            nonlocal ok
            print(f"      [{'PASS' if cond else 'FAIL'}] {label:<42} {detail}", flush=True)
            if not cond:
                ok = False

        r = spark.sql(f"SELECT count(*) c FROM {DB}.ads_daily_detail").collect()[0]["c"]
        check("ads_daily_detail 行数 = cohort 组合数（13,981）", r == 13981, f"{r:,} 行")

        bad = spark.sql(
            f"SELECT count(*) c FROM {DB}.ads_daily_detail WHERE ret_0 IS NOT NULL AND (ret_0 < 0 OR ret_0 > 1)"
        ).collect()[0]["c"]
        check("ret_0 落在 [0,1]", bad == 0, f"越界 {bad} 行")

        bad = spark.sql(
            f"SELECT count(*) c FROM {DB}.ads_daily_detail WHERE roi_7 IS NOT NULL AND roi_7 < 0"
        ).collect()[0]["c"]
        check("roi_7 非负", bad == 0, f"负数 {bad} 行")

        r = spark.sql(f"""
            SELECT sum(installs) a, (SELECT sum(installs) FROM {DB}.ads_creative_table) b,
                   (SELECT sum(installs) FROM {DB}.ads_sub_channel_table) c
            FROM {DB}.ads_daily_detail
        """).collect()[0]
        check("三张 ADS 表 installs 合计一致", r["a"] == r["b"] == r["c"],
              f"{r['a']:,} / {r['b']:,} / {r['c']:,}")

        # 导出文件与 Hive 表逐项对齐（防止 coalesce(1) 写出时丢行）
        for out, table in [("daily_detail", "ads_daily_detail"),
                           ("creative_table", "ads_creative_table"),
                           ("sub_channel_table", "ads_sub_channel_table")]:
            pdf = pd.read_parquet(os.path.join(ADS_OUT, f"{out}.parquet"))
            hive_n = spark.sql(f"SELECT count(*) c FROM {DB}.{table}").collect()[0]["c"]
            check(f"{out}.parquet 行数 = Hive 表", len(pdf) == hive_n,
                  f"{len(pdf):,} vs {hive_n:,}")

        pdf = pd.read_parquet(os.path.join(ADS_OUT, "daily_detail.parquet"))
        # 注意：pandas 3.0 把字符串列的 dtype 显示成 'str'（StringDtype），
        # 不再是 2.x 的 object。断言不能写死 'object'，否则换个 pandas 版本就误报。
        # 真正要保证的不是 dtype 名字，而是「这个值能进 JSON」——
        # 因为它是 LLM 上下文里的日期，转不成 JSON 整个工具调用就废了。
        import json
        try:
            json.dumps(str(pdf["install_date"].iloc[0]))
            jsonable = "datetime" not in str(pdf["install_date"].dtype).lower()
        except (TypeError, ValueError):
            jsonable = False
        check("导出文件 install_date 可 JSON 序列化（不是 datetime64）",
              jsonable, f"dtype={pdf['install_date'].dtype}")
        check("导出文件 spend 为浮点（可与数值运算）",
              str(pdf["spend"].dtype).startswith("float"), f"dtype={pdf['spend'].dtype}")

        r = spark.sql(f"""
            SELECT count(*) c FROM {DB}.ads_creative_table WHERE d7_retention IS NOT NULL
        """).collect()[0]["c"]
        r2 = spark.sql(f"SELECT count(*) c FROM {DB}.ads_creative_table").collect()[0]["c"]
        print(f"      [INFO] 素材表 {r2:,} 行中 {r:,} 行有 d7_retention（其余窗口不足，为 NULL）", flush=True)

        print(f"\n      {'全部通过' if ok else '存在失败项'}", flush=True)
        return 0 if ok else 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
