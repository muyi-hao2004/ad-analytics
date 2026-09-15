"""
指标计算模块
计算广告投放的核心指标：CTR、CVR、CPC、CPA、ROI、ROAS等。
支持整体计算、按维度分组计算、时间序列计算。
"""

import pandas as pd
import numpy as np

from .logger import logger


def calculate_basic_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算基础指标，给DataFrame添加CTR、CVR、CPC、CPA等列。
    注意：这个函数是对每一行计算指标（比如每行是一个渠道的汇总数据）。

    Args:
        df: 包含impressions, clicks, conversions, cost列的DataFrame

    Returns:
        添加了指标列的DataFrame
    """
    df = df.copy()

    # CTR = 点击 / 曝光（曝光为0时返回0，避免除零错误）
    df['ctr'] = np.where(
        df['impressions'] > 0,
        df['clicks'] / df['impressions'],
        0
    )

    # CVR = 转化 / 点击（点击为0时返回0）
    df['cvr'] = np.where(
        df['clicks'] > 0,
        df['conversions'] / df['clicks'],
        0
    )

    # CPC = 花费 / 点击（点击为0时返回0）
    df['cpc'] = np.where(
        df['clicks'] > 0,
        df['cost'] / df['clicks'],
        0
    )

    # CPA = 花费 / 转化（转化为0时返回0）
    df['cpa'] = np.where(
        df['conversions'] > 0,
        df['cost'] / df['conversions'],
        0
    )

    logger.debug(f"基础指标计算完成，新增列: ctr, cvr, cpc, cpa")
    return df


def calculate_overall_metrics(df: pd.DataFrame) -> dict:
    """
    计算整体汇总指标（把所有数据加总后再算指标）。
    注意：整体指标不是每行指标的平均，而是先加总再相除。

    Args:
        df: 原始数据DataFrame

    Returns:
        整体指标字典
    """
    total_impressions = int(df['impressions'].sum())
    total_clicks = int(df['clicks'].sum())
    total_conversions = int(df['conversions'].sum())
    total_cost = float(df['cost'].sum())

    metrics = {
        'total_impressions': total_impressions,
        'total_clicks': total_clicks,
        'total_conversions': total_conversions,
        'total_cost': round(total_cost, 2),
        'ctr': round(total_clicks / total_impressions, 6) if total_impressions > 0 else 0,
        'cvr': round(total_conversions / total_clicks, 6) if total_clicks > 0 else 0,
        'cpc': round(total_cost / total_clicks, 4) if total_clicks > 0 else 0,
        'cpa': round(total_cost / total_conversions, 4) if total_conversions > 0 else 0,
    }

    logger.info(f"整体指标计算完成: 曝光={total_impressions}, 点击={total_clicks}, "
                f"转化={total_conversions}, 花费={total_cost:.2f}, "
                f"CTR={metrics['ctr']:.2%}, CVR={metrics['cvr']:.2%}, "
                f"CPC={metrics['cpc']:.2f}, CPA={metrics['cpa']:.2f}")

    return metrics


def group_by_dimension(df: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """
    按指定维度分组汇总，并计算指标。

    Args:
        df: 原始数据DataFrame
        dimension: 分组维度，比如 'channel'、'campaign'、'region'、'device'

    Returns:
        按维度分组汇总后的DataFrame（包含指标列）
    """
    if dimension not in df.columns:
        logger.error(f"维度列不存在: {dimension}")
        raise ValueError(f"维度列不存在: {dimension}，可用列: {list(df.columns)}")

    logger.info(f"按维度分组: {dimension}")

    # 分组汇总
    grouped = df.groupby(dimension).agg({
        'impressions': 'sum',
        'clicks': 'sum',
        'conversions': 'sum',
        'cost': 'sum',
    }).reset_index()

    # 计算指标
    grouped = calculate_basic_metrics(grouped)

    # 按花费降序排列（花费高的排前面）
    grouped = grouped.sort_values('cost', ascending=False).reset_index(drop=True)

    logger.info(f"按 {dimension} 分组完成，共 {len(grouped)} 个分组")

    return grouped


def group_by_date(df: pd.DataFrame) -> pd.DataFrame:
    """
    按日期分组汇总，并计算指标。

    Args:
        df: 原始数据DataFrame（必须包含date列）

    Returns:
        按日期分组汇总后的DataFrame（按日期升序排列）
    """
    if 'date' not in df.columns:
        logger.error("缺少date列，无法按日期分组")
        raise ValueError("缺少date列")

    logger.info("按日期分组")

    # 按日期分组汇总
    grouped = df.groupby('date').agg({
        'impressions': 'sum',
        'clicks': 'sum',
        'conversions': 'sum',
        'cost': 'sum',
    }).reset_index()

    # 计算指标
    grouped = calculate_basic_metrics(grouped)

    # 按日期升序排列
    grouped = grouped.sort_values('date').reset_index(drop=True)

    logger.info(f"按日期分组完成，共 {len(grouped)} 天数据")

    return grouped


def calculate_daily_change(df: pd.DataFrame, metric: str = 'cost') -> pd.DataFrame:
    """
    计算日环比（和前一天比的变化率）。

    Args:
        df: 按日期分组后的DataFrame（必须按日期升序排列）
        metric: 要计算环比的指标列名，比如 'cost'、'clicks'、'cpa'

    Returns:
        添加了环比列的DataFrame
    """
    df = df.copy()

    if metric not in df.columns:
        logger.error(f"指标列不存在: {metric}")
        raise ValueError(f"指标列不存在: {metric}")

    # 前一天的值
    df[f'{metric}_prev'] = df[metric].shift(1)

    # 环比变化率 = (今天 - 昨天) / 昨天
    df[f'{metric}_daily_change'] = np.where(
        df[f'{metric}_prev'] > 0,
        (df[metric] - df[f'{metric}_prev']) / df[f'{metric}_prev'],
        None
    )

    # 删除临时列
    df = df.drop(columns=[f'{metric}_prev'])

    logger.debug(f"日环比计算完成: {metric}_daily_change")

    return df


def calculate_moving_average(df: pd.DataFrame, metric: str, window: int = 7) -> pd.DataFrame:
    """
    计算移动平均（用来平滑数据，看趋势）。

    Args:
        df: 按日期分组后的DataFrame
        metric: 要计算移动平均的指标列名
        window: 移动窗口大小，默认7天（周移动平均）

    Returns:
        添加了移动平均列的DataFrame
    """
    df = df.copy()

    if metric not in df.columns:
        logger.error(f"指标列不存在: {metric}")
        raise ValueError(f"指标列不存在: {metric}")

    # 滚动窗口计算均值
    df[f'{metric}_ma{window}'] = df[metric].rolling(window=window, min_periods=1).mean()

    logger.debug(f"移动平均计算完成: {metric}_ma{window} (窗口={window}天)")

    return df


def rank_by_metric(df: pd.DataFrame, metric: str, top_n: int = 10, ascending: bool = False) -> pd.DataFrame:
    """
    按指定指标排名，取Top N。

    Args:
        df: 数据DataFrame
        metric: 排名依据的指标列名
        top_n: 取前N名，默认10
        ascending: 是否升序，默认False（从高到低）

    Returns:
        Top N的DataFrame
    """
    if metric not in df.columns:
        logger.error(f"指标列不存在: {metric}")
        raise ValueError(f"指标列不存在: {metric}")

    # 排序并取前N
    result = df.sort_values(metric, ascending=ascending).head(top_n).reset_index(drop=True)

    # 添加排名列
    result['rank'] = range(1, len(result) + 1)

    logger.info(f"按 {metric} 排名Top{top_n}完成")

    return result
