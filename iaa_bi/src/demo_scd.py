"""
SCD Type 2 演示脚本

演示"配置变更时保留历史"的完整流程：
  1. 第一次抓配置 → 插入（没有历史）
  2. 再次抓配置（目标值变了）→ 关旧行 + 插新行
  3. 查询"历史某天"的值 → 能查到旧值（不会被新值覆盖）

运行：
    cd iaa_bi
    venv/Scripts/python.exe src/demo_scd.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import dim_scd


def main():
    # 第 0 步：建表
    dim_scd.init_dim_tables()
    print("维度表已创建（dim_campaign + dim_campaign_country）\n")

    # ============================================================
    # 第 1 步：第一次抓配置（10-01）
    # ============================================================
    print("=" * 60)
    print("第 1 次抓取（10-01）：目标值 70%")
    print("=" * 60)
    dim_scd.upsert_campaign({
        "campaign_id": "US_A_01",
        "campaign_name": "US_A_01",
        "strategy": "D7_ROAS",
        "owner": "张三",
        "channel": "unity",
    }, as_of="2026-10-01")
    dim_scd.upsert_campaign_country("US_A_01", {
        "country": "US", "budget": 1000.0, "target": 0.70,
    }, as_of="2026-10-01")
    print("已写入：US_A_01 目标值 70%")

    # 查 10-03 的目标值（应该是 70%）
    r = dim_scd.get_country_as_of("US_A_01", "US", "2026-10-03")
    print(f"查询 10-03 的目标值: {r['target']}  ← 应该是 0.70")
    print()

    # ============================================================
    # 第 2 步：第二次抓配置（10-05），目标值改了 70% → 80%
    # ============================================================
    print("=" * 60)
    print("第 2 次抓取（10-05）：目标值改成 80%")
    print("=" * 60)
    dim_scd.upsert_campaign_country("US_A_01", {
        "country": "US", "budget": 1000.0, "target": 0.80,
    }, as_of="2026-10-05")
    print("已更新：目标值 70% → 80%（触发 SCD 关旧+插新）")
    print()

    # ============================================================
    # 第 3 步：验证历史没有丢
    # ============================================================
    print("=" * 60)
    print("验证历史是否保留")
    print("=" * 60)
    # 查 10-03（改之前）→ 应该是 70%
    r_before = dim_scd.get_country_as_of("US_A_01", "US", "2026-10-03")
    print(f"查询 10-03 的目标值: {r_before['target']}  ← 应该是 0.70（历史）")

    # 查 10-06（改之后）→ 应该是 80%
    r_after = dim_scd.get_country_as_of("US_A_01", "US", "2026-10-06")
    print(f"查询 10-06 的目标值: {r_after['target']}  ← 应该是 0.80（新值）")

    # ============================================================
    # 第 4 步：验证「做法2」——同天多次调整只留最终值
    # ============================================================
    print()
    print("=" * 60)
    print("做法2：同一天（10-08）对目标值调了 3 次")
    print("=" * 60)
    # 早上：80% → 90%
    dim_scd.upsert_campaign_country("US_A_01", {
        "country": "US", "budget": 1000.0, "target": 0.90,
    }, as_of="2026-10-08")
    # 中午：90% → 95%
    dim_scd.upsert_campaign_country("US_A_01", {
        "country": "US", "budget": 1000.0, "target": 0.95,
    }, as_of="2026-10-08")
    # 下午：95% → 88%（最终值）
    dim_scd.upsert_campaign_country("US_A_01", {
        "country": "US", "budget": 1000.0, "target": 0.88,
    }, as_of="2026-10-08")

    # 查 10-08 当天最终值 → 应该只有一条记录，值是 0.88
    conn = dim_scd._connect()
    cur = conn.cursor()
    cur.execute(
        """SELECT COUNT(*) FROM dim_campaign_country
           WHERE campaign_id='US_A_01' AND country='US' AND eff_start_date='2026-10-08'"""
    )
    cnt = cur.fetchone()[0]
    conn.close()
    r_1008 = dim_scd.get_country_as_of("US_A_01", "US", "2026-10-08")
    print(f"10-08 当天记录条数: {cnt}  ← 应该是 1（同天合并，没插成 3 行）")
    print(f"10-08 最终目标值:   {r_1008['target']}  ← 应该是 0.88（只留最后一次）")
    print()

    # ============================================================
    # 第 5 步：查看当前生效的系列
    # ============================================================
    print("=" * 60)
    print("当前生效的系列（is_current=1）")
    print("=" * 60)
    for c in dim_scd.list_all_current():
        print(f"  {c['campaign_id']} | 策略 {c['strategy']} | 投放人 {c['owner']} | 生效自 {c['eff_start_date']}")


    # ============================================================
    # 第 6 步：验证操作日志（记录谁在何时怎么调的）
    # ============================================================
    print()
    print("=" * 60)
    print("操作日志：Agent 和投放人调整策略")
    print("=" * 60)
    # Agent 调整目标值（带原因）
    dim_scd.upsert_campaign_country("US_A_01", {
        "country": "US", "budget": 1000.0, "target": 0.85,
    }, as_of="2026-10-09", operator="agent", app="money_clicker",
       channel="unity", reason="D7 ROAS 连续3天低于70%，建议降低目标值到85%试探")

    # 投放人手动调预算
    dim_scd.upsert_campaign_country("US_A_01", {
        "country": "US", "budget": 1500.0, "target": 0.85,
    }, as_of="2026-10-10", operator="张三", app="money_clicker",
       channel="unity", reason="美国市场周末流量好，加预算")

    print("已写入 2 条调整操作，查询日志：")
    for log in dim_scd.list_logs(limit=20):
        print(f"  [{log['op_time']}] {log['operator']} | {log['action']} | "
              f"{log['field']}: {log['old_value']} -> {log['new_value']} | "
              f"包={log['app']} 渠道={log['channel']} 系列={log['campaign_id']}")
        if log["reason"]:
            print(f"        原因: {log['reason']}")


if __name__ == "__main__":
    main()
