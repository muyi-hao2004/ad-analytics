"""
广告投放智能助手 - 工具层
所有Agent可以调用的工具都在这里
"""

import pymysql
import pandas as pd
from datetime import datetime, timedelta

# ============================================================
# 数据库连接配置
# ============================================================
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "123456",
    "database": "ad_analytics",
    "charset": "utf8mb4"
}


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)


# ============================================================
# 工具1：查询广告数据
# ============================================================
def query_ad_data(channel: str = None, date: str = None) -> str:
    """
    查询广告投放数据
    参数：
        channel: 广告渠道（Facebook/Google/TikTok/YouTube/Instagram），不传则查所有渠道
        date: 日期，格式YYYY-MM-DD，不传则查最新一天
    """
    print(f"  [工具调用] query_ad_data(channel={channel}, date={date})")

    conn = get_db_connection()

    # 构建SQL
    sql = "SELECT channel, date, cost, impressions, clicks, conversions FROM ad_data WHERE 1=1"
    params = []

    if channel:
        sql += " AND channel = %s"
        params.append(channel)

    if date:
        sql += " AND date = %s"
        params.append(date)
    else:
        # 查最新一天
        sql += " AND date = (SELECT MAX(date) FROM ad_data)"

    sql += " ORDER BY channel"

    df = pd.read_sql(sql, conn, params=params)
    conn.close()

    if df.empty:
        return "没有找到相关数据"

    # 计算指标
    df['CTR'] = (df['clicks'] / df['impressions'] * 100).round(2)
    df['CPC'] = (df['cost'] / df['clicks']).round(2)
    df['CPA'] = (df['cost'] / df['conversions']).round(2)

    # 格式化成字符串
    result_lines = []
    for _, row in df.iterrows():
        result_lines.append(
            f"{row['channel']} ({row['date']}): "
            f"花费={row['cost']:.0f}, "
            f"曝光={row['impressions']:.0f}, "
            f"点击={row['clicks']:.0f}, "
            f"转化={row['conversions']:.0f}, "
            f"CTR={row['CTR']}%, "
            f"CPC={row['CPC']}, "
            f"CPA={row['CPA']}"
        )

    return "\n".join(result_lines)


# ============================================================
# 工具2：异常检测
# ============================================================
def detect_anomaly(channel: str) -> str:
    """
    检测某个渠道的投放是否异常
    规则：CPA超过过去7天平均值的50%，或CTR下降超过30%
    """
    print(f"  [工具调用] detect_anomaly(channel={channel})")

    conn = get_db_connection()

    # 查最新一天的数据
    sql_latest = """
        SELECT * FROM ad_data 
        WHERE channel = %s AND date = (SELECT MAX(date) FROM ad_data)
    """
    df_latest = pd.read_sql(sql_latest, conn, params=[channel])

    if df_latest.empty:
        conn.close()
        return f"没有找到{channel}的数据"

    latest = df_latest.iloc[0]
    latest_cpa = latest['cost'] / latest['conversions'] if latest['conversions'] > 0 else 0
    latest_ctr = latest['clicks'] / latest['impressions'] if latest['impressions'] > 0 else 0

    # 查过去7天的平均值（不含最新一天）
    sql_history = """
        SELECT * FROM ad_data 
        WHERE channel = %s 
        AND date >= (SELECT MAX(date) FROM ad_data) - INTERVAL 7 DAY
        AND date < (SELECT MAX(date) FROM ad_data)
    """
    df_history = pd.read_sql(sql_history, conn, params=[channel])
    conn.close()

    if df_history.empty:
        return f"{channel}历史数据不足，无法判断异常"

    # 计算历史平均
    df_history['CPA'] = df_history['cost'] / df_history['conversions'].replace(0, 1)
    df_history['CTR'] = df_history['clicks'] / df_history['impressions'].replace(0, 1)

    avg_cpa = df_history['CPA'].mean()
    avg_ctr = df_history['CTR'].mean()

    # 判断异常
    anomalies = []

    if avg_cpa > 0 and latest_cpa > avg_cpa * 1.5:
        anomalies.append(
            f"CPA异常：最新CPA={latest_cpa:.2f}，过去7天平均={avg_cpa:.2f}，上涨了{(latest_cpa/avg_cpa-1)*100:.1f}%"
        )

    if avg_ctr > 0 and latest_ctr < avg_ctr * 0.7:
        anomalies.append(
            f"CTR异常：最新CTR={latest_ctr*100:.2f}%，过去7天平均={avg_ctr*100:.2f}%，下降了{(1-latest_ctr/avg_ctr)*100:.1f}%"
        )

    if not anomalies:
        return f"{channel}投放正常，无异常。CPA={latest_cpa:.2f}（历史平均{avg_cpa:.2f}），CTR={latest_ctr*100:.2f}%（历史平均{avg_ctr*100:.2f}%）"
    else:
        return f"{channel}检测到异常：\n" + "\n".join(anomalies)


# ============================================================
# 工具3：查询知识库（简化版，用关键词匹配）
# ============================================================
def query_knowledge_base(query: str) -> str:
    """
    查询广告投放知识库
    （这里用简化版，实际项目中应该调用RAG API）
    """
    print(f"  [工具调用] query_knowledge_base(query={query})")

    knowledge_base = {
        "CPA": "CPA（单次转化成本）= 总花费 / 总转化数。CPA越高，说明获客成本越高。",
        "CTR": "CTR（点击率）= 总点击 / 总曝光。CTR越高，说明广告素材越吸引人。",
        "CPC": "CPC（单次点击成本）= 总花费 / 总点击。CPC越高，说明竞价越激烈。",
        "ROI": "ROI（投资回报率）=（收入 - 成本）/ 成本。ROI大于1才说明赚钱。",
        "异常": "如果CPA突然上涨超过历史平均值的50%，就属于异常，需要检查投放设置。常见原因：1.竞争加剧导致CPC上升；2.素材疲劳导致CTR下降；3.落地页问题导致转化率下降；4.定向人群过窄。",
        "素材": "广告素材连续投放超过7天，CTR会明显下降，这就是素材疲劳，需要及时更换。建议每周更新2-3组新素材测试。",
        "Facebook": "Facebook广告的优势是用户量大，定向精准，适合做品牌曝光和受众拓展。CPA相对较高，但用户质量较好。",
        "Google": "Google搜索广告的优势是用户搜索意图明确，转化率相对较高，适合精准获客。CPA相对较低。",
        "TikTok": "TikTok广告适合做年轻用户群体的品牌种草，视频素材的创意很重要。CTR较高但转化率相对较低。",
    }

    # 简单关键词匹配
    results = []
    for key, value in knowledge_base.items():
        if key.lower() in query.lower():
            results.append(value)

    if results:
        return "\n\n".join(results)
    else:
        return "知识库中没有找到相关内容。建议检查：1.投放设置是否有变化；2.素材是否需要更新；3.落地页是否正常；4.竞争环境是否变化。"


# ============================================================
# 工具4：生成日报
# ============================================================
def get_daily_report(date: str = None) -> str:
    """
    生成广告投放日报
    """
    print(f"  [工具调用] get_daily_report(date={date})")

    conn = get_db_connection()

    if date:
        sql = "SELECT * FROM ad_data WHERE date = %s ORDER BY channel"
        df = pd.read_sql(sql, conn, params=[date])
    else:
        sql = "SELECT * FROM ad_data WHERE date = (SELECT MAX(date) FROM ad_data) ORDER BY channel"
        df = pd.read_sql(sql, conn)

    conn.close()

    if df.empty:
        return "没有数据"

    # 计算汇总
    total_cost = df['cost'].sum()
    total_impressions = df['impressions'].sum()
    total_clicks = df['clicks'].sum()
    total_conversions = df['conversions'].sum()

    overall_ctr = total_clicks / total_impressions * 100 if total_impressions > 0 else 0
    overall_cpc = total_cost / total_clicks if total_clicks > 0 else 0
    overall_cpa = total_cost / total_conversions if total_conversions > 0 else 0

    report = f"""===== 广告投放日报 =====
日期：{df['date'].iloc[0]}

【整体汇总】
总花费：{total_cost:.0f}
总曝光：{total_impressions:.0f}
总点击：{total_clicks:.0f}
总转化：{total_conversions:.0f}
整体CTR：{overall_ctr:.2f}%
整体CPC：{overall_cpc:.2f}
整体CPA：{overall_cpa:.2f}

【分渠道详情】
"""

    for _, row in df.iterrows():
        ctr = row['clicks'] / row['impressions'] * 100 if row['impressions'] > 0 else 0
        cpc = row['cost'] / row['clicks'] if row['clicks'] > 0 else 0
        cpa = row['cost'] / row['conversions'] if row['conversions'] > 0 else 0
        report += f"\n{row['channel']}：花费={row['cost']:.0f}, 转化={row['conversions']:.0f}, CPA={cpa:.2f}, CTR={ctr:.2f}%"

    return report


# ============================================================
# 工具清单（给大模型看的）
# ============================================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_ad_data",
            "description": "查询广告投放的具体数据，包括花费、曝光、点击、转化、CTR、CPC、CPA等。当用户问某个渠道的具体数字、或者要对比数据时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "广告渠道，可选：Facebook、Google、TikTok、YouTube、Instagram。不传则查所有渠道。"
                    },
                    "date": {
                        "type": "string",
                        "description": "查询日期，格式YYYY-MM-DD。不传则查最新一天。"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "detect_anomaly",
            "description": "检测某个渠道的投放是否异常，包括CPA异常上涨、CTR异常下降等。当用户问'是不是异常'、'为什么涨了'、'有没有问题'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "要检测的广告渠道，可选：Facebook、Google、TikTok、YouTube、Instagram"
                    }
                },
                "required": ["channel"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_base",
            "description": "查询广告投放知识库，包括指标定义、异常原因、优化建议、渠道特点等概念性问题。当用户问'什么是'、'为什么'、'怎么办'、'有什么建议'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的问题"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_report",
            "description": "生成完整的广告投放日报，包括整体汇总和分渠道详情。当用户要'日报'、'汇总'、'整体情况'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "日报日期，格式YYYY-MM-DD。不传则用最新一天。"
                    }
                }
            }
        }
    }
]
