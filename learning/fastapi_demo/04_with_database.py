"""
把数据库查询集成到FastAPI的完整例子
"""

from fastapi import FastAPI, HTTPException
import mysql.connector
from mysql.connector import Error

app = FastAPI(title="广告数据API - 连接MySQL版本")

# ============================================================
# 数据库连接配置
# ============================================================
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "123456",
    "database": "ad_analytics"
}


def get_db_connection():
    """连接数据库，返回连接对象"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        # 数据库连接失败，抛出500错误
        raise HTTPException(status_code=500, detail=f"数据库连接失败: {str(e)}")


# ============================================================
# 接口1：查询所有渠道的汇总指标
# ============================================================
@app.get("/api/channel_summary")
def get_channel_summary(date: str = None):
    """
    查询各渠道的汇总指标
    - date: 可选，不传就查所有日期
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)  # dictionary=True让结果返回字典，不是元组

    try:
        # 写SQL
        sql = """
            SELECT
                channel,
                SUM(cost) as total_cost,
                SUM(impressions) as total_impressions,
                SUM(clicks) as total_clicks,
                SUM(conversions) as total_conversions,
                ROUND(SUM(clicks) / SUM(impressions) * 100, 2) as ctr,
                ROUND(SUM(cost) / SUM(conversions), 2) as cpa
            FROM ad_data
            WHERE 1=1
        """
        params = []

        if date:
            sql += " AND date = %s"
            params.append(date)

        sql += " GROUP BY channel ORDER BY total_cost DESC"

        # 执行查询
        cursor.execute(sql, params)
        results = cursor.fetchall()

        return {
            "total_channels": len(results),
            "data": results
        }

    finally:
        cursor.close()
        conn.close()


# ============================================================
# 接口2：查询单个渠道的详细数据
# ============================================================
@app.get("/api/channel/{channel_name}")
def get_channel_detail(channel_name: str):
    """
    查询某个渠道的详细数据
    如果渠道不存在，返回404错误
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        sql = """
            SELECT *
            FROM ad_data
            WHERE channel = %s
            ORDER BY date DESC
            LIMIT 100
        """
        cursor.execute(sql, (channel_name,))
        results = cursor.fetchall()

        # 如果查不到数据，抛出404错误
        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"渠道 {channel_name} 不存在，或者没有数据"
            )

        return {
            "channel": channel_name,
            "total_records": len(results),
            "data": results
        }

    finally:
        cursor.close()
        conn.close()


# ============================================================
# 接口3：异常检测接口（POST）
# ============================================================
from pydantic import BaseModel

class AnomalyRequest(BaseModel):
    channel: str
    date: str

@app.post("/api/detect_anomaly")
def detect_anomaly(request: AnomalyRequest):
    """
    检测某个渠道某天是否异常
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 查这个渠道的历史CPA平均值
        cursor.execute("""
            SELECT AVG(cpa) as avg_cpa
            FROM (
                SELECT DATE(date) as dt, SUM(cost)/SUM(conversions) as cpa
                FROM ad_data
                WHERE channel = %s
                GROUP BY DATE(date)
            ) t
        """, (request.channel,))
        history = cursor.fetchone()
        avg_cpa = history["avg_cpa"]

        # 查当天的CPA
        cursor.execute("""
            SELECT SUM(cost)/SUM(conversions) as today_cpa
            FROM ad_data
            WHERE channel = %s AND date = %s
        """, (request.channel, request.date))
        today = cursor.fetchone()

        if not today or today["today_cpa"] is None:
            raise HTTPException(
                status_code=404,
                detail=f"{request.date} 没有 {request.channel} 的数据"
            )

        today_cpa = today["today_cpa"]

        # 判断是否异常：超过历史平均值的50%
        threshold = avg_cpa * 1.5
        is_abnormal = today_cpa > threshold

        return {
            "channel": request.channel,
            "date": request.date,
            "today_cpa": round(today_cpa, 2),
            "avg_historical_cpa": round(avg_cpa, 2),
            "threshold": round(threshold, 2),
            "is_abnormal": is_abnormal,
            "message": f"今天CPA={today_cpa:.2f}，历史平均={avg_cpa:.2f}，" +
                      ("⚠️ 异常偏高！" if is_abnormal else "✅ 正常")
        }

    finally:
        cursor.close()
        conn.close()
