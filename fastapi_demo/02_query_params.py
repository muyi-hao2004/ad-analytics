"""
FastAPI 查询参数示例
运行：uvicorn 02_query_params:app --reload
然后访问：
  http://127.0.0.1:8000/docs  （交互式文档）
"""

from fastapi import FastAPI

app = FastAPI(title="查询参数示例")

# 模拟从数据库查出来的数据（实际应该从MySQL查）
fake_data = [
    {"date": "2026-09-20", "channel": "Facebook", "cost": 2500, "clicks": 12500, "cpa": 20.0},
    {"date": "2026-09-20", "channel": "Google", "cost": 3000, "clicks": 18000, "cpa": 16.67},
    {"date": "2026-09-20", "channel": "TikTok", "cost": 2000, "clicks": 15000, "cpa": 13.33},
    {"date": "2026-09-21", "channel": "Facebook", "cost": 2800, "clicks": 14000, "cpa": 20.0},
    {"date": "2026-09-21", "channel": "Google", "cost": 3200, "clicks": 19200, "cpa": 16.67},
    {"date": "2026-09-21", "channel": "TikTok", "cost": 2200, "clicks": 16500, "cpa": 13.33},
]


# 第1个接口：带一个可选查询参数
@app.get("/api/channel_metrics")
def get_channel_metrics(channel: str = None, date: str = None):
    """
    查询渠道指标
    - channel: 可选，不传就返回所有渠道
    - date: 可选，不传就返回所有日期
    """
    result = fake_data

    # 如果传了channel，就按channel过滤
    if channel:
        result = [row for row in result if row["channel"] == channel]

    # 如果传了date，就按date过滤
    if date:
        result = [row for row in result if row["date"] == date]

    return {
        "total": len(result),
        "data": result
    }


# 第2个接口：带一个必须传的查询参数
@app.get("/api/cpa_ranking")
def get_cpa_ranking(date: str, top_n: int = 3):
    """
    查询指定日期的CPA排名
    - date: 必须传，查哪天的
    - top_n: 可选，默认取前3名
    """
    # 先过滤出指定日期的数据
    daily_data = [row for row in fake_data if row["date"] == date]

    # 按CPA升序排序（CPA越低越好）
    sorted_data = sorted(daily_data, key=lambda x: x["cpa"])

    # 取前top_n个
    top = sorted_data[:top_n]

    return {
        "date": date,
        "ranking": top
    }


# 第3个接口：数字范围校验
@app.get("/api/expensive_channels")
def get_expensive_channels(min_cost: float = 0, max_cost: float = 10000):
    """
    查询花费在指定范围内的渠道
    """
    result = [
        row for row in fake_data
        if min_cost <= row["cost"] <= max_cost
    ]
    return {"data": result}
