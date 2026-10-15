from sys import exception

from practice.mini_project.main import result


def calculate_ad_metrics(impressions, clicks, conversions,cost):
    if impressions == 0:
        ctr = 0
    elif not isinstance(impressions, (int, float)) or isinstance(clicks, (int, float)):
        raise ValueError
    else:
        ctr = clicks / impressions

    if clicks == 0:
        cvr = 0
    elif not isinstance(conversions, (int, float)) or isinstance(clicks, (int, float)):
        raise ValueError
    else:
        cvr = conversions / clicks

    if clicks == 0:
        cpc = 0
    elif not isinstance(cost, (int, float)) or isinstance(clicks, (int, float)):
        raise ValueError
    else:
        cpc = cost / clicks

    if conversions == 0:
        cpa = 0
    elif not isinstance(cost, (int, float)) or isinstance(conversions, (int, float)):
        raise ValueError
    else:
        cpa = cost / conversions
    return {
    "ctr": ctr,
    "cvr": cvr,
    "cpc": cpc,
    "cpa": cpa}




def filter_by_channel(df, channel_name):
    df1 = df[df["channel" == channel_name]]
    return df1


def calculate_rates(totals):
    # 从字典里取出总量
    total_impressions = totals["total_impressions"]
    total_clicks = totals["total_clicks"]
    total_conversions = totals["total_conversions"]
    total_cost = totals["total_cost"]

    # 计算比率，分母为0时记为0
    ctr = total_clicks / total_impressions if total_impressions != 0 else 0
    cvr = total_conversions / total_clicks if total_clicks != 0 else 0
    cpa = total_cost / total_conversions if total_conversions != 0 else 0

    # 返回比率字典
    return {
        "ctr": ctr,
        "cvr": cvr,
        "cpa": cpa
    }

def calculate_total(df):
    total_impressions = df["impressions"].sum()
    total_clicks = df["clicks"].sum()
    total_cost = df["cost"].sum()
    total_conversions = df["conversions"].sum()
    return {
        "total_impressions" : total_impressions,
        "total_clicks": total_clicks,
        "total_conversions": total_conversions,
        "total_cost": total_cost
    }

def analyze_channel(df, channel_name):
    try:
        result = filter_by_channel(df, channel_name)
        result1 = calculate_total(result)
        result2 = calculate_rates(result1)
        # 合并所有结果
        return {
            "channel": channel_name,
            "total_impressions": result1["total_impressions"],
            "total_clicks": result1["total_clicks"],
            "total_conversions": result1["total_conversions"],
            "total_cost": result1["total_cost"],
            "ctr": result2["ctr"],
            "cvr": result2["cvr"],
            "cpa": result2["cpa"]
        }

    except Exception as e:
        print(f"出现异常{e},渠道可能不存在")
