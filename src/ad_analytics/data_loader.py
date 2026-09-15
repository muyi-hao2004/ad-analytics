"""
数据加载模块
支持从CSV文件和API两种方式加载广告投放数据，并进行数据验证。
"""

from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from .config import settings, PROJECT_ROOT
from .logger import logger

# 数据的标准列名和类型
EXPECTED_COLUMNS = {
    'date': 'datetime64[ns]',
    'channel': 'object',
    'campaign': 'object',
    'region': 'object',
    'device': 'object',
    'impressions': 'int64',
    'clicks': 'int64',
    'conversions': 'int64',
    'cost': 'float64',
}

REQUIRED_COLUMNS = ['date', 'channel', 'campaign', 'impressions', 'clicks', 'cost']


class DataValidationError(Exception):
    """数据验证失败时抛出的异常。"""
    pass


def load_from_csv(
    file_path: Optional[str] = None,
    encoding: str = 'utf-8',
) -> pd.DataFrame:
    """
    从CSV文件加载广告投放数据。

    Args:
        file_path: CSV文件路径，默认为 data/raw/sample_ad_data.csv
        encoding: 文件编码

    Returns:
        加载并验证后的DataFrame

    Raises:
        FileNotFoundError: 文件不存在
        DataValidationError: 数据验证失败
    """
    if file_path is None:
        file_path = PROJECT_ROOT / 'data' / 'raw' / 'sample_ad_data.csv'

    file_path = Path(file_path)

    if not file_path.exists():
        logger.error(f"数据文件不存在: {file_path}")
        raise FileNotFoundError(f"数据文件不存在: {file_path}")

    logger.info(f"开始从CSV加载数据: {file_path}")

    df = pd.read_csv(
        file_path,
        parse_dates=['date'],
        encoding=encoding,
    )

    logger.info(f"CSV加载完成，共 {len(df)} 行，{len(df.columns)} 列")

    # 验证数据
    validation_report = validate_data(df)
    logger.info(f"数据验证报告: {validation_report}")

    return df


def load_from_api(
    start_date: str,
    end_date: str,
    api_url: Optional[str] = None,
    api_token: Optional[str] = None,
    timeout: int = 30,
) -> pd.DataFrame:
    """
    从广告平台API加载数据。

    Args:
        start_date: 开始日期，格式 'YYYY-MM-DD'
        end_date: 结束日期，格式 'YYYY-MM-DD'
        api_url: API地址，默认从配置读取
        api_token: API认证token，默认从配置读取
        timeout: 请求超时时间（秒）

    Returns:
        加载并验证后的DataFrame

    Raises:
        requests.exceptions.RequestException: API请求失败
        DataValidationError: 数据验证失败
    """
    if api_url is None:
        api_url = getattr(settings, 'AD_API_URL', 'https://api.example.com/ads/report')
    if api_token is None:
        api_token = getattr(settings, 'AD_API_TOKEN', '')

    logger.info(f"开始从API加载数据: {start_date} ~ {end_date}")

    headers = {}
    if api_token:
        headers['Authorization'] = f'Bearer {api_token}'

    try:
        response = requests.get(
            api_url,
            params={
                'start_date': start_date,
                'end_date': end_date,
                'metrics': 'impressions,clicks,conversions,cost',
                'dimensions': 'date,channel,campaign,region,device',
            },
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        logger.error(f"API请求超时（{timeout}秒）")
        raise
    except requests.exceptions.ConnectionError:
        logger.error("API网络连接失败")
        raise
    except requests.exceptions.HTTPError as e:
        logger.error(f"API HTTP错误: {e}")
        raise
    except ValueError:
        logger.error("API响应JSON解析失败")
        raise

    # API返回的数据通常是列表格式，转为DataFrame
    if isinstance(data, dict) and 'data' in data:
        records = data['data']
    elif isinstance(data, list):
        records = data
    else:
        logger.error(f"API返回数据格式不支持: {type(data)}")
        raise ValueError("API返回数据格式不支持")

    df = pd.DataFrame(records)

    # 类型转换
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    for col in ['impressions', 'clicks', 'conversions']:
        if col in df.columns:
            df[col] = df[col].astype(int)
    if 'cost' in df.columns:
        df['cost'] = df['cost'].astype(float)

    logger.info(f"API加载完成，共 {len(df)} 行")

    # 验证数据
    validation_report = validate_data(df)
    logger.info(f"数据验证报告: {validation_report}")

    return df


def validate_data(df: pd.DataFrame, raise_on_error: bool = False) -> dict:
    """
    验证数据质量，返回验证报告。

    Args:
        df: 待验证的DataFrame
        raise_on_error: 是否在发现严重问题时抛出异常

    Returns:
        验证报告字典

    Raises:
        DataValidationError: 当raise_on_error=True且发现严重问题时
    """
    report = {
        'total_rows': len(df),
        'total_columns': len(df.columns),
        'columns': list(df.columns),
        'missing_values': {},
        'invalid_values': {},
        'duplicate_rows': 0,
        'date_range': None,
        'warnings': [],
    }

    # 1. 检查必填列是否存在
    missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_columns:
        msg = f"缺少必填列: {missing_columns}"
        logger.error(msg)
        report['warnings'].append(msg)
        if raise_on_error:
            raise DataValidationError(msg)

    # 2. 检查缺失值
    for col in REQUIRED_COLUMNS:
        if col in df.columns:
            missing_count = df[col].isna().sum()
            if missing_count > 0:
                report['missing_values'][col] = int(missing_count)
                logger.warning(f"列 '{col}' 有 {missing_count} 条缺失值")

    # 3. 检查数值列的非法值（负值）
    numeric_columns = ['impressions', 'clicks', 'conversions', 'cost']
    for col in numeric_columns:
        if col in df.columns:
            negative_count = (df[col] < 0).sum()
            if negative_count > 0:
                report['invalid_values'][col] = {
                    'negative': int(negative_count),
                }
                logger.warning(f"列 '{col}' 有 {negative_count} 条负值")

    # 4. 检查零曝光记录（可能是异常数据）
    if 'impressions' in df.columns:
        zero_impressions = (df['impressions'] == 0).sum()
        if zero_impressions > 0:
            report['invalid_values']['impressions'] = report['invalid_values'].get('impressions', {})
            report['invalid_values']['impressions']['zero'] = int(zero_impressions)
            logger.warning(f"有 {zero_impressions} 条零曝光记录")

    # 5. 检查重复行
    duplicate_count = df.duplicated().sum()
    report['duplicate_rows'] = int(duplicate_count)
    if duplicate_count > 0:
        logger.warning(f"发现 {duplicate_count} 条重复行")

    # 6. 日期范围
    if 'date' in df.columns and len(df) > 0:
        report['date_range'] = {
            'min': str(df['date'].min().date()),
            'max': str(df['date'].max().date()),
        }

    logger.info(
        f"数据验证完成: {report['total_rows']}行, "
        f"缺失值列数={len(report['missing_values'])}, "
        f"重复行={report['duplicate_rows']}"
    )

    return report


def get_data_summary(df: pd.DataFrame) -> dict:
    """
    获取数据的基本统计摘要。

    Args:
        df: 数据DataFrame

    Returns:
        统计摘要字典
    """
    summary = {
        'total_rows': len(df),
        'date_range': None,
        'channels': [],
        'total_impressions': 0,
        'total_clicks': 0,
        'total_conversions': 0,
        'total_cost': 0.0,
        'overall_ctr': 0.0,
        'overall_cvr': 0.0,
        'overall_cpc': 0.0,
    }

    if len(df) == 0:
        return summary

    if 'date' in df.columns:
        summary['date_range'] = {
            'min': str(df['date'].min().date()),
            'max': str(df['date'].max().date()),
        }

    if 'channel' in df.columns:
        summary['channels'] = sorted(df['channel'].unique().tolist())

    for col, key in [('impressions', 'total_impressions'),
                      ('clicks', 'total_clicks'),
                      ('conversions', 'total_conversions')]:
        if col in df.columns:
            summary[key] = int(df[col].sum())

    if 'cost' in df.columns:
        summary['total_cost'] = round(float(df['cost'].sum()), 2)

    # 整体指标
    if summary['total_impressions'] > 0:
        summary['overall_ctr'] = round(summary['total_clicks'] / summary['total_impressions'], 6)
    if summary['total_clicks'] > 0:
        summary['overall_cvr'] = round(summary['total_conversions'] / summary['total_clicks'], 6)
        summary['overall_cpc'] = round(summary['total_cost'] / summary['total_clicks'], 4)

    return summary
