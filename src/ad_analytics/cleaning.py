"""
数据清洗模块
对原始广告投放数据进行清洗，处理缺失值、异常值、重复数据等。
"""

import pandas as pd

from .logger import logger


def clean_ad_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗广告投放数据的主函数，按顺序执行所有清洗步骤。

    Args:
        df: 原始数据DataFrame

    Returns:
        清洗后的DataFrame
    """
    raw_count = len(df)
    logger.info(f"开始数据清洗，原始数据: {raw_count} 行")

    # 步骤1：字段名标准化
    df = standardize_columns(df)

    # 步骤2：数据类型转换
    df = convert_types(df)

    # 步骤3：去除完全重复的行
    before = len(df)
    df = remove_duplicates(df)
    logger.info(f"去除重复行: 删除 {before - len(df)} 行")

    # 步骤4：处理缺失值
    before = len(df)
    df = handle_missing_values(df)
    logger.info(f"处理缺失值: 删除 {before - len(df)} 行")

    # 步骤5：处理异常值
    before = len(df)
    df = handle_outliers(df)
    logger.info(f"处理异常值: 删除 {before - len(df)} 行")

    # 步骤6：重置索引
    df = df.reset_index(drop=True)

    removed = raw_count - len(df)
    logger.info(f"数据清洗完成: 原始 {raw_count} 行 → 清洗后 {len(df)} 行，删除 {removed} 行")
    logger.info(f"清洗后数据形状: {df.shape}")

    return df


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    标准化字段名：转小写、空格改下划线、去除前后空格。

    Args:
        df: 原始数据

    Returns:
        字段名标准化后的数据
    """
    df = df.copy()  # 复制一份，不修改原始数据
    df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
    logger.debug(f"字段名标准化完成: {list(df.columns)}")
    return df


def convert_types(df: pd.DataFrame) -> pd.DataFrame:
    """
    转换数据类型：日期列转datetime，数字列转数值类型。

    Args:
        df: 原始数据

    Returns:
        类型转换后的数据
    """
    df = df.copy()

    # 日期列转换
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        logger.debug(f"日期列转换完成，范围: {df['date'].min()} ~ {df['date'].max()}")

    # 数字列转换（转换失败的设为NaN）
    numeric_columns = ['impressions', 'clicks', 'conversions', 'cost']
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 分类列转换（省内存）
    category_columns = ['channel', 'campaign', 'region', 'device']
    for col in category_columns:
        if col in df.columns:
            df[col] = df[col].astype('category')

    return df


def remove_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """
    去除完全重复的行。

    Args:
        df: 原始数据

    Returns:
        去重后的数据
    """
    df = df.copy()
    duplicate_count = df.duplicated().sum()
    if duplicate_count > 0:
        logger.warning(f"发现 {duplicate_count} 条完全重复的行，已删除")
    df = df.drop_duplicates(keep='first')
    return df


def handle_missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """
    处理缺失值：
    - 关键字段（date, channel, campaign）有缺失的行直接删除
    - cost缺失用0填充（广告花费为空通常就是0花费）
    - 其他数值列缺失用0填充

    Args:
        df: 原始数据

    Returns:
        处理缺失值后的数据
    """
    df = df.copy()

    # 关键字段有缺失的行删除（这些字段是维度，缺失的话没法分析）
    key_columns = ['date', 'channel', 'campaign']
    existing_key_columns = [col for col in key_columns if col in df.columns]
    before = len(df)
    df = df.dropna(subset=existing_key_columns)
    deleted = before - len(df)
    if deleted > 0:
        logger.warning(f"关键字段缺失，删除 {deleted} 行")

    # cost缺失用0填充（广告花费为空通常就是没花费）
    if 'cost' in df.columns:
        missing_cost = df['cost'].isna().sum()
        if missing_cost > 0:
            logger.info(f"cost列有 {missing_cost} 条缺失，用0填充")
        df['cost'] = df['cost'].fillna(0)

    # 其他数值列缺失用0填充
    numeric_columns = ['impressions', 'clicks', 'conversions']
    for col in numeric_columns:
        if col in df.columns:
            missing = df[col].isna().sum()
            if missing > 0:
                logger.info(f"{col}列有 {missing} 条缺失，用0填充")
            df[col] = df[col].fillna(0)

    return df


def handle_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """
    处理异常值：
    - 删除曝光量为0的行（没有展示的广告没有分析价值）
    - 删除数值列为负的行（曝光、点击、转化、花费不能为负）
    - 点击量大于曝光量的行删除（不合理）
    - 花费超过100万的行标记为异常（可能是数据错误，这里先保留并警告）

    Args:
        df: 原始数据

    Returns:
        处理异常值后的数据
    """
    df = df.copy()

    # 1. 删除曝光量为0的行
    if 'impressions' in df.columns:
        zero_impressions = (df['impressions'] == 0).sum()
        if zero_impressions > 0:
            logger.warning(f"发现 {zero_impressions} 条零曝光记录，已删除")
        df = df[df['impressions'] > 0]

    # 2. 删除数值列为负的行
    numeric_columns = ['impressions', 'clicks', 'conversions', 'cost']
    for col in numeric_columns:
        if col in df.columns:
            negative_count = (df[col] < 0).sum()
            if negative_count > 0:
                logger.warning(f"发现 {negative_count} 条 {col} 为负的记录，已删除")
            df = df[df[col] >= 0]

    # 3. 删除点击量大于曝光量的行（不合理）
    if 'clicks' in df.columns and 'impressions' in df.columns:
        invalid_ctr = (df['clicks'] > df['impressions']).sum()
        if invalid_ctr > 0:
            logger.warning(f"发现 {invalid_ctr} 条点击量大于曝光量的记录，已删除")
        df = df[df['clicks'] <= df['impressions']]

    # 4. 花费超过100万的行标记警告（可能是真实的大额投放，也可能是错误，这里保留）
    if 'cost' in df.columns:
        huge_cost = (df['cost'] > 1000000).sum()
        if huge_cost > 0:
            logger.warning(f"发现 {huge_cost} 条花费超过100万的记录，请人工确认是否正常")

    return df


def get_cleaning_report(raw_df: pd.DataFrame, clean_df: pd.DataFrame) -> dict:
    """
    生成数据清洗报告，对比清洗前后的数据质量。

    Args:
        raw_df: 清洗前的原始数据
        clean_df: 清洗后的干净数据

    Returns:
        清洗报告字典
    """
    report = {
        'raw_rows': len(raw_df),
        'clean_rows': len(clean_df),
        'removed_rows': len(raw_df) - len(clean_df),
        'removal_rate': round((len(raw_df) - len(clean_df)) / len(raw_df) * 100, 2) if len(raw_df) > 0 else 0,
        'raw_missing_values': raw_df.isna().sum().to_dict(),
        'clean_missing_values': clean_df.isna().sum().to_dict(),
        'raw_duplicates': int(raw_df.duplicated().sum()),
        'clean_duplicates': int(clean_df.duplicated().sum()),
    }

    logger.info(f"数据清洗报告: 原始 {report['raw_rows']} 行 → 清洗后 {report['clean_rows']} 行，"
                f"删除 {report['removed_rows']} 行（{report['removal_rate']}%）")

    return report
