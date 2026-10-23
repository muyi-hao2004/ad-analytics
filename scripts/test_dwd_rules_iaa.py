"""DWD 清洗规则的故障注入测试。

为什么必须写这个测试（而不是看「生产数据里 R4/R5 命中 0」就说规则没问题）：
    「0 命中」有两种完全相反的解释 ——
        ① 数据里真的没有这类脏数据（规则正确但没被触发）
        ② 规则写错了，永远为 false（看起来也是 0 命中）
    从输出上根本区分不出来。唯一能区分的方法，是**主动塞一条必然违规的数据**，
    看它会不会被拦。拦住了说明规则活着；没拦住说明规则是死的。

    这也是数据工程和普通业务代码测试的最大区别：
    业务代码测「输入 A 得到 B」，清洗规则要测「输入坏的 A 被丢掉，且好的 A 不被误杀」。

断言有三条：
    ① 每条规则至少拦掉 1 行（规则是活的）
    ② 正常样本 1 行都不许拦（零误伤）
    ③ 重复样本只留 1 条（去重精确）

跑法：
    MSYS_NO_PATHCONV=1 ./.venv-spark/Scripts/python.exe -u scripts/test_dwd_rules_iaa.py
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal

_HADOOP_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop"))
if os.path.isdir(_HADOOP_HOME):
    os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
    os.environ["PATH"] = os.path.join(_HADOOP_HOME, "bin") + os.pathsep + os.environ.get("PATH", "")
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

SQL_FILE = os.path.join(os.path.dirname(__file__), "..", "sql", "dwd", "dwd_iaa_user_daily.sql")

SCHEMA = (
    "uid string, install_date date, days_since_install int, app string, country string, "
    "campaign string, sub_channel string, creative_id string, device_os string, "
    "session_cnt int, ad_impr bigint, rewarded_views int, "
    "ad_revenue decimal(12,6), install_cost decimal(12,6), dt string"
)

BASE = dict(
    install_date=date(2026, 10, 15), days_since_install=0, app="money_clicker",
    country="US", campaign="US_A_01", sub_channel="ch_001", creative_id="crt_us_a_01_001",
    device_os="Android", session_cnt=3, ad_impr=10, rewarded_views=2,
    ad_revenue=Decimal("0.500000"), install_cost=Decimal("0.320000"), dt="2026-10-15",
)


def row(uid, **over):
    d = dict(BASE)
    d["uid"] = uid
    d.update(over)
    return tuple(d[k] for k in [
        "uid", "install_date", "days_since_install", "app", "country", "campaign",
        "sub_channel", "creative_id", "device_os", "session_cnt", "ad_impr",
        "rewarded_views", "ad_revenue", "install_cost", "dt",
    ])


# ---- 测试样本 ----
# 前两组是「必须保留」的，后面每条对应一条清洗规则
CLEAN_CASES = [
    row("u_clean_1"),
    # 活跃了但一次广告都没看到：展示 0、观次 0、收入 0 都是合法的
    # （区分「观测到的 0」和「NULL」是本项目的一条硬规则）
    row("u_clean_2", ad_impr=0, rewarded_views=0, ad_revenue=Decimal("0")),
    # 装机当天，成本有值
    row("u_clean_3", install_cost=Decimal("0.410000")),
]

DIRTY_CASES = [
    ("R1 主键为空", row(None)),
    ("R2 活跃日早于装机日", row("u_bad_days", days_since_install=-3)),
    ("R3 收入为负", row("u_bad_rev", ad_revenue=Decimal("-1.200000"))),
    ("R4a 展示为负", row("u_bad_impr", ad_impr=-5)),
    ("R4b 零次打开", row("u_bad_session", session_cnt=0)),
    ("R5a 观次为负", row("u_bad_rv_neg", rewarded_views=-2)),
    ("R5b 观次超过展示", row("u_bad_rv_over", ad_impr=6, rewarded_views=9)),
]

DUP_CASE = row("u_dup")   # 完全相同的两行，期望去重后只剩 1 行


def main() -> int:
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("test-dwd-rules")
        .master("local[2]")
        .config("spark.driver.memory", "1g")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    all_rows = CLEAN_CASES + [r for _, r in DIRTY_CASES] + [DUP_CASE, DUP_CASE]
    df = spark.createDataFrame(all_rows, schema=SCHEMA)
    df.createOrReplaceTempView("_test_src")

    # 直接把生产 SQL 拿来跑，只把表名换成测试数据 ——
    # 测的就是线上那份 SQL，不是另写一份「测试专用 SQL」（后者一定会走偏）
    with open(SQL_FILE, encoding="utf-8") as f:
        sql = f.read().replace("lab.ods_iaa_user_daily", "_test_src")
    out = spark.sql(sql)
    kept = sorted(r["uid"] for r in out.collect())

    print(f"注入 {len(all_rows)} 行（{len(CLEAN_CASES)} 干净 + {len(DIRTY_CASES)} 脏 + 1 重复）")
    print(f"清洗后保留 {len(kept)} 行: {kept}\n")

    ok = True

    # 断言 ①：干净样本零误伤
    for r in CLEAN_CASES:
        uid = r[0]
        if uid not in kept:
            print(f"  [失败] 干净样本 {uid} 被误杀")
            ok = False
    print(f"  [1] 干净样本零误伤: {'OK' if ok else '失败'}（{len(CLEAN_CASES)} 条全保留）")

    # 断言 ②：每条脏规则都被拦住
    missing = []
    for desc, r in DIRTY_CASES:
        uid = r[0]
        if uid is not None and uid in kept:
            missing.append(desc)
    # R1 的 uid 是 NULL，没法按 uid 查，单独验证「输出里没有 NULL uid」
    null_uid = sum(1 for u in kept if u is None)
    if null_uid:
        missing.append("R1 主键为空")
    if missing:
        print(f"  [2] 规则拦截: 失败 —— 这些脏数据漏过去了: {missing}")
        ok = False
    else:
        print(f"  [2] 规则拦截: OK（{len(DIRTY_CASES)} 类脏数据全部拦住，零漏网）")

    # 断言 ③：去重精确
    dup_n = kept.count("u_dup")
    if dup_n != 1:
        print(f"  [3] 去重: 失败 —— u_dup 保留了 {dup_n} 条，期望 1 条")
        ok = False
    else:
        print("  [3] 去重: OK（2 条完全重复只留 1 条）")

    # 断言 ④：总行数精确
    expect = len(CLEAN_CASES) + 1
    if len(kept) != expect:
        print(f"  [4] 行数: 失败 —— 期望 {expect} 行，实际 {len(kept)} 行")
        ok = False
    else:
        print(f"  [4] 行数: OK（{len(all_rows)} 行进 → {expect} 行出，精确）")

    spark.stop()
    print("\n" + ("全部通过。" if ok else "有断言失败，见上。"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
