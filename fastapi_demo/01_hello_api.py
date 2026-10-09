"""
第一个FastAPI示例
运行方式：uvicorn 01_hello_api:app --reload
然后浏览器打开：http://127.0.0.1:8000
"""

# 第1步：导入FastAPI
from fastapi import FastAPI

# 第2步：创建app对象（就是你的API服务）
app = FastAPI(title="我的第一个API")

# 第3步：定义接口
# @app.get("/") 意思是：当有人访问根路径"/"的时候，执行下面这个函数
@app.get("/")
def read_root():
    return {"message": "你好，欢迎来到我的API！"}

# 再定义一个接口：访问 /hello/名字 的时候，返回打招呼
@app.get("/hello/{name}")
def say_hello(name: str):
    return {"message": f"你好，{name}！欢迎学习FastAPI"}

# 再定义一个接口：返回广告数据
@app.get("/api/metrics")
def get_metrics():
    # 这里实际是从数据库查数据，现在先写死
    return {
        "date": "2026-09-21",
        "total_cost": 12500.50,
        "total_impressions": 1250000,
        "total_clicks": 62500,
        "ctr": 0.05,
        "cpa": 20.0
    }
