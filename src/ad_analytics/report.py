"""
报表生成模块
生成HTML格式的广告投放日报，包含核心指标、渠道汇总、每日趋势、异常列表
"""

import os
from datetime import datetime
from typing import Dict, Any, List

import pandas as pd
from jinja2 import Template

from ad_analytics.logger import logger


# HTML模板（用Jinja2语法）
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            background-color: #f5f7fa;
            color: #333;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .header h1 {
            font-size: 28px;
            margin-bottom: 10px;
        }
        .header .subtitle {
            font-size: 14px;
            opacity: 0.9;
        }
        .section {
            background: white;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        .section h2 {
            font-size: 20px;
            margin-bottom: 15px;
            color: #333;
            border-left: 4px solid #667eea;
            padding-left: 10px;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
        }
        .metric-card {
            background: #f8f9ff;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }
        .metric-card .label {
            font-size: 13px;
            color: #888;
            margin-bottom: 8px;
        }
        .metric-card .value {
            font-size: 24px;
            font-weight: bold;
            color: #333;
        }
        .metric-card .value.highlight {
            color: #667eea;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }
        th {
            background-color: #f8f9ff;
            font-weight: 600;
            color: #555;
        }
        tr:hover {
            background-color: #fafbff;
        }
        .severity-high {
            color: #e74c3c;
            font-weight: bold;
        }
        .severity-medium {
            color: #f39c12;
            font-weight: bold;
        }
        .severity-low {
            color: #27ae60;
            font-weight: bold;
        }
        .anomaly-description {
            color: #666;
            font-size: 13px;
        }
        .footer {
            text-align: center;
            color: #999;
            font-size: 12px;
            padding: 20px;
        }
        .empty-state {
            text-align: center;
            color: #999;
            padding: 30px;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="header">
            <h1>{{ title }}</h1>
            <div class="subtitle">生成时间：{{ generated_at }} | 数据周期：{{ date_range }}</div>
        </div>

        <!-- 核心指标 -->
        <div class="section">
            <h2>核心指标</h2>
            <div class="metrics-grid">
                {% for metric in core_metrics %}
                <div class="metric-card">
                    <div class="label">{{ metric.label }}</div>
                    <div class="value {% if metric.highlight %}highlight{% endif %}">{{ metric.value }}</div>
                </div>
                {% endfor %}
            </div>
        </div>

        <!-- 渠道汇总 -->
        <div class="section">
            <h2>渠道汇总</h2>
            {% if channel_summary %}
            <table>
                <thead>
                    <tr>
                        <th>渠道</th>
                        <th>花费</th>
                        <th>曝光</th>
                        <th>点击</th>
                        <th>转化</th>
                        <th>CTR</th>
                        <th>CVR</th>
                        <th>CPA</th>
                    </tr>
                </thead>
                <tbody>
                    {% for row in channel_summary %}
                    <tr>
                        <td>{{ row.channel }}</td>
                        <td>{{ row.cost }}</td>
                        <td>{{ row.impressions }}</td>
                        <td>{{ row.clicks }}</td>
                        <td>{{ row.conversions }}</td>
                        <td>{{ row.ctr }}</td>
                        <td>{{ row.cvr }}</td>
                        <td>{{ row.cpa }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="empty-state">暂无渠道数据</div>
            {% endif %}
        </div>

        <!-- 每日趋势 -->
        <div class="section">
            <h2>每日趋势</h2>
            {% if daily_trend %}
            <table>
                <thead>
                    <tr>
                        <th>日期</th>
                        <th>花费</th>
                        <th>曝光</th>
                        <th>点击</th>
                        <th>转化</th>
                        <th>CTR</th>
                        <th>CVR</th>
                    </tr>
                </thead>
                <tbody>
                    {% for row in daily_trend %}
                    <tr>
                        <td>{{ row.date }}</td>
                        <td>{{ row.cost }}</td>
                        <td>{{ row.impressions }}</td>
                        <td>{{ row.clicks }}</td>
                        <td>{{ row.conversions }}</td>
                        <td>{{ row.ctr }}</td>
                        <td>{{ row.cvr }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="empty-state">暂无每日趋势数据</div>
            {% endif %}
        </div>

        <!-- 异常列表 -->
        <div class="section">
            <h2>异常提醒（共 {{ anomaly_count }} 条）</h2>
            {% if anomalies %}
            <table>
                <thead>
                    <tr>
                        <th>日期</th>
                        <th>渠道</th>
                        <th>计划</th>
                        <th>异常类型</th>
                        <th>严重程度</th>
                        <th>详情</th>
                    </tr>
                </thead>
                <tbody>
                    {% for row in anomalies %}
                    <tr>
                        <td>{{ row.date }}</td>
                        <td>{{ row.channel }}</td>
                        <td>{{ row.campaign }}</td>
                        <td>{{ row.anomaly_type }}</td>
                        <td class="severity-{{ row.severity_class }}">{{ row.severity }}</td>
                        <td class="anomaly-description">{{ row.description }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="empty-state">太棒了！没有检测到异常</div>
            {% endif %}
        </div>

        <!-- 页脚 -->
        <div class="footer">
            本报告由 ad-analytics 系统自动生成 | © 2026
        </div>
    </div>
</body>
</html>
"""


def generate_report(
    df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    channel_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    anomalies_df: pd.DataFrame,
    output_path: str,
    title: str = "广告投放日报"
) -> str:
    """
    生成HTML格式的广告投放日报

    Args:
        df: 清洗后的完整数据
        metrics_df: 整体指标（calculate_overall_metrics的结果）
        channel_df: 按渠道汇总的指标（group_by_dimension的结果）
        daily_df: 按日期汇总的指标（group_by_date的结果）
        anomalies_df: 异常检测结果（detect_all_anomalies的结果）
        output_path: 输出HTML文件路径
        title: 报表标题

    Returns:
        生成的HTML文件路径
    """
    logger.info(f"开始生成报表：{title}")

    # 1. 准备核心指标数据
    core_metrics = _prepare_core_metrics(metrics_df)

    # 2. 准备渠道汇总数据
    channel_summary = _prepare_channel_summary(channel_df)

    # 3. 准备每日趋势数据
    daily_trend = _prepare_daily_trend(daily_df)

    # 4. 准备异常列表数据
    anomalies, anomaly_count = _prepare_anomalies(anomalies_df)

    # 5. 计算日期范围
    date_range = _get_date_range(df)

    # 6. 准备模板数据
    template_data = {
        "title": title,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date_range": date_range,
        "core_metrics": core_metrics,
        "channel_summary": channel_summary,
        "daily_trend": daily_trend,
        "anomalies": anomalies,
        "anomaly_count": anomaly_count,
    }

    # 7. 渲染模板
    template = Template(HTML_TEMPLATE)
    html_content = template.render(**template_data)

    # 8. 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 9. 写入文件
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info(f"报表生成成功：{output_path}")
    return output_path


def _prepare_core_metrics(metrics_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """准备核心指标数据（转成模板需要的格式）"""
    if metrics_df.empty:
        return []

    row = metrics_df.iloc[0]
    metrics = [
        {"label": "总花费", "value": f"{row['total_cost']:,.2f}元", "highlight": True},
        {"label": "总曝光", "value": f"{row['total_impressions']:,.0f}", "highlight": False},
        {"label": "总点击", "value": f"{row['total_clicks']:,.0f}", "highlight": False},
        {"label": "总转化", "value": f"{row['total_conversions']:,.0f}", "highlight": False},
        {"label": "整体CTR", "value": f"{row['ctr']:.2%}", "highlight": False},
        {"label": "整体CVR", "value": f"{row['cvr']:.2%}", "highlight": False},
        {"label": "整体CPC", "value": f"{row['cpc']:.2f}元", "highlight": False},
        {"label": "整体CPA", "value": f"{row['cpa']:.2f}元", "highlight": True},
    ]
    return metrics


def _prepare_channel_summary(channel_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """准备渠道汇总数据（转成模板需要的格式）"""
    if channel_df.empty:
        return []

    result = []
    for _, row in channel_df.iterrows():
        result.append({
            "channel": row.get("channel", "未知"),
            "cost": f"{row['cost']:,.2f}",
            "impressions": f"{row['impressions']:,.0f}",
            "clicks": f"{row['clicks']:,.0f}",
            "conversions": f"{row['conversions']:,.0f}",
            "ctr": f"{row['ctr']:.2%}",
            "cvr": f"{row['cvr']:.2%}",
            "cpa": f"{row['cpa']:.2f}",
        })
    return result


def _prepare_daily_trend(daily_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """准备每日趋势数据（转成模板需要的格式）"""
    if daily_df.empty:
        return []

    result = []
    for _, row in daily_df.iterrows():
        # 日期可能是datetime或字符串，统一转成字符串
        date_val = row.get("date", "")
        if hasattr(date_val, "strftime"):
            date_str = date_val.strftime("%Y-%m-%d")
        else:
            date_str = str(date_val)[:10]

        result.append({
            "date": date_str,
            "cost": f"{row['cost']:,.2f}",
            "impressions": f"{row['impressions']:,.0f}",
            "clicks": f"{row['clicks']:,.0f}",
            "conversions": f"{row['conversions']:,.0f}",
            "ctr": f"{row['ctr']:.2%}",
            "cvr": f"{row['cvr']:.2%}",
        })
    return result


def _prepare_anomalies(anomalies_df: pd.DataFrame):
    """准备异常列表数据（转成模板需要的格式）"""
    if anomalies_df.empty:
        return [], 0

    result = []
    for _, row in anomalies_df.iterrows():
        # 日期格式化
        date_val = row.get("date", "")
        if hasattr(date_val, "strftime"):
            date_str = date_val.strftime("%Y-%m-%d")
        else:
            date_str = str(date_val)[:10]

        # 严重程度转成CSS类名
        severity = row.get("severity", "中")
        severity_class = {"高": "high", "中": "medium", "低": "low"}.get(severity, "medium")

        result.append({
            "date": date_str,
            "channel": row.get("channel", "未知"),
            "campaign": row.get("campaign", "未知"),
            "anomaly_type": row.get("anomaly_type", "未知"),
            "severity": severity,
            "severity_class": severity_class,
            "description": row.get("description", ""),
        })

    return result, len(result)


def _get_date_range(df: pd.DataFrame) -> str:
    """获取数据的日期范围"""
    if df.empty or "date" not in df.columns:
        return "未知"

    min_date = df["date"].min()
    max_date = df["date"].max()

    if hasattr(min_date, "strftime"):
        min_str = min_date.strftime("%Y-%m-%d")
        max_str = max_date.strftime("%Y-%m-%d")
    else:
        min_str = str(min_date)[:10]
        max_str = str(max_date)[:10]

    return f"{min_str} 至 {max_str}"
