"""
FastAPI + 缓存示例
用字典模拟Redis缓存，演示缓存的效果
"""

import time
from fastapi import FastAPI

app = FastAPI(title="缓存示例")

# ============================================================
# 用字典模拟Redis缓存
# 真实的Redis是一个单独的服务，这里用内存字典代替
# ============================================================
cache = {}  # key: 缓存的键, value: (数据, 过期时间戳)
CACHE_TTL = 60  # 缓存存活时间：60秒

# 模拟数据库查询（故意很慢，加2秒延迟）
def slow_query_from_db(channel: str):
    """模拟从MySQL查数据，很慢，要2秒"""
    print(f"  [模拟] 正在从MySQL查询 {channel} 的数据...")
    time.sleep(2)  # 故意等2秒，模拟慢查询
    return {
        "channel": channel,
        "cost": 10000,
        "impressions": 500000,
        "clicks": 25000,
        "cpa": 40.0,
        "queried_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }


def get_from_cache_or_db(cache_key: str, query_func):
    """
    先查缓存，没有再查数据库
    这就是缓存的核心逻辑！
    """
    # 1. 先看缓存里有没有
    if cache_key in cache:
        data, expire_time = cache[cache_key]
        # 检查有没有过期
        if time.time() < expire_time:
            print(f"  [缓存] 命中缓存！直接返回")
            return data, True  # True表示命中缓存
        else:
            print(f"  [缓存] 缓存过期了，重新查数据库")
            del cache[cache_key]

    # 2. 缓存没有，查数据库
    print(f"  [缓存] 缓存未命中，查询数据库...")
    data = query_func()

    # 3. 把结果存到缓存里，设置过期时间
    cache[cache_key] = (data, time.time() + CACHE_TTL)
    print(f"  [缓存] 数据已存入缓存，{CACHE_TTL}秒后过期")

    return data, False  # False表示没命中缓存


# ============================================================
# 接口1：没有缓存的版本（每次都查数据库）
# ============================================================
@app.get("/api/no_cache/{channel}")
def get_without_cache(channel: str):
    """没有缓存，每次都查数据库"""
    start_time = time.time()
    result = slow_query_from_db(channel)
    elapsed = time.time() - start_time
    return {
        "cache": "未使用缓存",
        "time_cost": f"{elapsed:.2f}秒",
        "data": result
    }


# ============================================================
# 接口2：有缓存的版本（先查缓存）
# ============================================================
@app.get("/api/with_cache/{channel}")
def get_with_cache(channel: str):
    """有缓存，第一次慢，后面都快"""
    start_time = time.time()

    cache_key = f"channel:{channel}"  # 缓存的键，比如 channel:Facebook
    result, hit_cache = get_from_cache_or_db(cache_key, lambda: slow_query_from_db(channel))

    elapsed = time.time() - start_time
    return {
        "cache": "命中缓存" if hit_cache else "未命中缓存（刚查了数据库）",
        "time_cost": f"{elapsed:.2f}秒",
        "data": result
    }


# ============================================================
# 接口3：查看当前缓存里有什么
# ============================================================
@app.get("/api/cache/status")
def cache_status():
    """查看当前缓存状态"""
    return {
        "total_cached_items": len(cache),
        "cache_keys": list(cache.keys()),
        "ttl_seconds": CACHE_TTL
    }
