"""
FastAPI POST请求示例
运行：uvicorn 03_post_request:app --reload
然后访问：http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI
from pydantic import BaseModel  # 用来定义请求体的格式

app = FastAPI(title="POST请求示例")

# ============================================================
# 第1步：定义请求体的格式（用Pydantic模型）
# ============================================================
class AdData(BaseModel):
    """
    定义一条广告数据的格式
    这个类就是告诉FastAPI：POST请求要传什么字段
    """
    date: str           # 日期，比如 "2026-09-21"
    channel: str        # 渠道，比如 "Facebook"
    campaign_name: str  # 活动名称
    cost: float         # 花费
    impressions: int    # 曝光量
    clicks: int         # 点击量
    conversions: int    # 转化量


# ============================================================
# 第2步：写POST接口
# ============================================================
# 存数据的地方（实际应该存数据库，现在先存在内存里）
ad_database = []


@app.post("/api/ad_data")
def create_ad_data(ad: AdData):
    """
    提交一条新的广告数据
    前端传过来的JSON会自动被FastAPI解析成AdData对象
    """
    # 把传过来的数据转成字典，存起来
    ad_dict = ad.dict()
    ad_database.append(ad_dict)

    return {
        "status": "success",
        "message": "数据保存成功",
        "data": ad_dict
    }


@app.get("/api/ad_data")
def get_all_ad_data():
    """查询所有已保存的广告数据（GET接口）"""
    return {
        "total": len(ad_database),
        "data": ad_database
    }


# ============================================================
# 第3步：再写一个更实用的例子：异常检测提交接口
# ============================================================
class AnomalyCheck(BaseModel):
    """定义异常检测请求的格式"""
    channel: str
    cost: float
    cpa: float


@app.post("/api/check_anomaly")
def check_anomaly(request: AnomalyCheck):
    """
    检测某个渠道的指标是否异常
    比如CPA超过50就算异常
    """
    is_abnormal = request.cpa > 50

    if is_abnormal:
        return {
            "is_abnormal": True,
            "reason": f"{request.channel} 的CPA={request.cpa}，超过阈值50"
        }
    else:
        return {
            "is_abnormal": False,
            "reason": f"{request.channel} 的CPA={request.cpa}，正常"
        }
