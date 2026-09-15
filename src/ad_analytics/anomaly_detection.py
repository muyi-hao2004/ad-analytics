"""
异常检测模块
检测广告投放中的常见异常：花费暴涨、点击率过低、转化率过低、CPA过高、零曝光
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Any

from ad_analytics.logger import logger


def detect_spend_surge(df: pd.DataFrame, threshold: float = 1.5) -> pd.DataFrame:
    """
    检测花费暴涨：当天花费 > 前7天平均花费 × threshold

    Args:
        df: 清洗后的广告数据，需包含 date, channel, campaign, cost 列
        threshold: 暴涨倍数阈值，默认1.5倍

    Returns:
        异常记录DataFrame
    """
    anomalies = []

    # 按渠道和日期分组，计算每天的花费
    daily_spend = df.groupby(['channel', 'date'])['cost'].sum().reset_index()
    daily_spend = daily_spend.sort_values(['channel', 'date'])

    # 对每个渠道单独检测
    for channel in daily_spend['channel'].unique():
        channel_data = daily_spend[daily_spend['channel'] == channel].reset_index(drop=True)

        for i in range(len(channel_data)):
            if i < 7:  # 前7天没有足够的历史数据，跳过
                continue

            # 前7天的平均花费
            history_avg = channel_data.loc[i-7:i-1, 'cost'].mean()
            # 当天花费
            current_spend = channel_data.loc[i, 'cost']
            # 当天日期
            current_date = channel_data.loc[i, 'date']

            # 如果历史平均为0，跳过（避免除零）
            if history_avg == 0:
                continue

            # 检测是否暴涨
            if current_spend > history_avg * threshold:
                anomalies.append({
                    'date': current_date,
                    'channel': channel,
                    'campaign': '全部计划',
                    'anomaly_type': '花费暴涨',
                    'metric_value': round(current_spend, 2),
                    'threshold_value': round(history_avg * threshold, 2),
                    'severity': '高',
                    'description': f"当天花费{current_spend:.2f}元，超过前7天平均{history_avg:.2f}元的{threshold}倍"
                })

    logger.info(f"花费暴涨检测完成，发现{len(anomalies)}条异常")
    return pd.DataFrame(anomalies) if anomalies else pd.DataFrame(columns=[
        'date', 'channel', 'campaign', 'anomaly_type', 'metric_value',
        'threshold_value', 'severity', 'description'
    ])


def detect_low_ctr(df: pd.DataFrame, threshold: float = 0.01) -> pd.DataFrame:
    """
    检测点击率过低：CTR < threshold

    Args:
        df: 清洗后的广告数据，需包含 date, channel, campaign, impressions, clicks 列
        threshold: CTR阈值，默认1%

    Returns:
        异常记录DataFrame
    """
    anomalies = []

    # 按日期、渠道、计划分组，计算CTR
    grouped = df.groupby(['date', 'channel', 'campaign']).agg({
        'impressions': 'sum',
        'clicks': 'sum'
    }).reset_index()

    # 计算CTR（除零保护）
    grouped['ctr'] = np.where(grouped['impressions'] > 0,
                               grouped['clicks'] / grouped['impressions'],
                               0)

    # 筛选CTR过低的记录（曝光量大于100才检测，避免小样本误报）
    low_ctr = grouped[(grouped['ctr'] < threshold) & (grouped['impressions'] > 100)]

    for _, row in low_ctr.iterrows():
        anomalies.append({
            'date': row['date'],
            'channel': row['channel'],
            'campaign': row['campaign'],
            'anomaly_type': '点击率过低',
            'metric_value': round(row['ctr'], 4),
            'threshold_value': threshold,
            'severity': '中',
            'description': f"CTR={row['ctr']:.2%}，低于阈值{threshold:.2%}（曝光{row['impressions']}次，点击{row['clicks']}次）"
        })

    logger.info(f"点击率过低检测完成，发现{len(anomalies)}条异常")
    return pd.DataFrame(anomalies) if anomalies else pd.DataFrame(columns=[
        'date', 'channel', 'campaign', 'anomaly_type', 'metric_value',
        'threshold_value', 'severity', 'description'
    ])


def detect_low_cvr(df: pd.DataFrame, threshold: float = 0.01) -> pd.DataFrame:
    """
    检测转化率过低：CVR < threshold

    Args:
        df: 清洗后的广告数据，需包含 date, channel, campaign, clicks, conversions 列
        threshold: CVR阈值，默认1%

    Returns:
        异常记录DataFrame
    """
    anomalies = []

    # 按日期、渠道、计划分组，计算CVR
    grouped = df.groupby(['date', 'channel', 'campaign']).agg({
        'clicks': 'sum',
        'conversions': 'sum'
    }).reset_index()

    # 计算CVR（除零保护）
    grouped['cvr'] = np.where(grouped['clicks'] > 0,
                               grouped['conversions'] / grouped['clicks'],
                               0)

    # 筛选CVR过低的记录（点击量大于50才检测，避免小样本误报）
    low_cvr = grouped[(grouped['cvr'] < threshold) & (grouped['clicks'] > 50)]

    for _, row in low_cvr.iterrows():
        anomalies.append({
            'date': row['date'],
            'channel': row['channel'],
            'campaign': row['campaign'],
            'anomaly_type': '转化率过低',
            'metric_value': round(row['cvr'], 4),
            'threshold_value': threshold,
            'severity': '中',
            'description': f"CVR={row['cvr']:.2%}，低于阈值{threshold:.2%}（点击{row['clicks']}次，转化{row['conversions']}次）"
        })

    logger.info(f"转化率过低检测完成，发现{len(anomalies)}条异常")
    return pd.DataFrame(anomalies) if anomalies else pd.DataFrame(columns=[
        'date', 'channel', 'campaign', 'anomaly_type', 'metric_value',
        'threshold_value', 'severity', 'description'
    ])


def detect_high_cpa(df: pd.DataFrame, threshold_multiplier: float = 2.0) -> pd.DataFrame:
    """
    检测CPA过高：当天CPA > 所有记录平均CPA × threshold_multiplier

    Args:
        df: 清洗后的广告数据，需包含 date, channel, campaign, cost, conversions 列
        threshold_multiplier: CPA倍数阈值，默认2倍

    Returns:
        异常记录DataFrame
    """
    anomalies = []

    # 按日期、渠道、计划分组，计算CPA
    grouped = df.groupby(['date', 'channel', 'campaign']).agg({
        'cost': 'sum',
        'conversions': 'sum'
    }).reset_index()

    # 计算CPA（除零保护）
    grouped['cpa'] = np.where(grouped['conversions'] > 0,
                               grouped['cost'] / grouped['conversions'],
                               np.inf)

    # 计算所有有转化记录的平均CPA
    valid_cpa = grouped[grouped['conversions'] > 0]['cpa']
    if len(valid_cpa) == 0:
        logger.warning("没有有效的CPA数据，跳过CPA过高检测")
        return pd.DataFrame(columns=[
            'date', 'channel', 'campaign', 'anomaly_type', 'metric_value',
            'threshold_value', 'severity', 'description'
        ])

    avg_cpa = valid_cpa.mean()
    threshold = avg_cpa * threshold_multiplier

    # 筛选CPA过高的记录（有转化才检测）
    high_cpa = grouped[(grouped['cpa'] > threshold) & (grouped['conversions'] > 0)]

    for _, row in high_cpa.iterrows():
        anomalies.append({
            'date': row['date'],
            'channel': row['channel'],
            'campaign': row['campaign'],
            'anomaly_type': 'CPA过高',
            'metric_value': round(row['cpa'], 2),
            'threshold_value': round(threshold, 2),
            'severity': '高',
            'description': f"CPA={row['cpa']:.2f}元，超过平均CPA{avg_cpa:.2f}元的{threshold_multiplier}倍（花费{row['cost']:.2f}元，转化{row['conversions']}次）"
        })

    logger.info(f"CPA过高检测完成，发现{len(anomalies)}条异常")
    return pd.DataFrame(anomalies) if anomalies else pd.DataFrame(columns=[
        'date', 'channel', 'campaign', 'anomaly_type', 'metric_value',
        'threshold_value', 'severity', 'description'
    ])


def detect_zero_impressions(df: pd.DataFrame) -> pd.DataFrame:
    """
    检测零曝光：曝光量 = 0

    Args:
        df: 清洗后的广告数据，需包含 date, channel, campaign, impressions 列

    Returns:
        异常记录DataFrame
    """
    anomalies = []

    # 筛选零曝光的记录
    zero_imp = df[df['impressions'] == 0]

    for _, row in zero_imp.iterrows():
        anomalies.append({
            'date': row['date'],
            'channel': row['channel'],
            'campaign': row['campaign'],
            'anomaly_type': '零曝光',
            'metric_value': 0,
            'threshold_value': 0,
            'severity': '高',
            'description': f"曝光量为0，可能账户被封或计划审核不通过（花费{row['cost']:.2f}元）"
        })

    logger.info(f"零曝光检测完成，发现{len(anomalies)}条异常")
    return pd.DataFrame(anomalies) if anomalies else pd.DataFrame(columns=[
        'date', 'channel', 'campaign', 'anomaly_type', 'metric_value',
        'threshold_value', 'severity', 'description'
    ])


def detect_all_anomalies(df: pd.DataFrame, config: Dict[str, Any] = None) -> pd.DataFrame:
    """
    执行所有异常检测，合并结果

    Args:
        df: 清洗后的广告数据
        config: 检测配置，可自定义各检测的阈值
            - spend_surge_threshold: 花费暴涨倍数，默认1.5
            - low_ctr_threshold: CTR阈值，默认0.01
            - low_cvr_threshold: CVR阈值，默认0.01
            - high_cpa_multiplier: CPA倍数，默认2.0

    Returns:
        所有异常记录合并后的DataFrame
    """
    if config is None:
        config = {}

    # 执行各项检测
    anomaly_list = [
        detect_spend_surge(df, config.get('spend_surge_threshold', 1.5)),
        detect_low_ctr(df, config.get('low_ctr_threshold', 0.01)),
        detect_low_cvr(df, config.get('low_cvr_threshold', 0.01)),
        detect_high_cpa(df, config.get('high_cpa_multiplier', 2.0)),
        detect_zero_impressions(df),
    ]

    # 合并所有异常
    all_anomalies = pd.concat(anomaly_list, ignore_index=True)

    # 按严重程度排序（高>中>低）
    severity_order = {'高': 0, '中': 1, '低': 2}
    all_anomalies['severity_order'] = all_anomalies['severity'].map(severity_order)
    all_anomalies = all_anomalies.sort_values(['severity_order', 'date']).drop(columns=['severity_order'])

    logger.info(f"所有异常检测完成，共发现{len(all_anomalies)}条异常")
    return all_anomalies.reset_index(drop=True)
