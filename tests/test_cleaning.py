"""
cleaning模块的单元测试
"""

import pandas as pd
import pytest

from ad_analytics.cleaning import (
    clean_ad_data,
    standardize_columns,
    convert_types,
    remove_duplicates,
    handle_missing_values,
    handle_outliers,
    get_cleaning_report,
)


@pytest.fixture
def dirty_df():
    """创建一份包含各种脏数据的测试DataFrame。"""
    return pd.DataFrame({
        'Date': ['2026-08-01', '2026-08-01', '2026-08-02', None, '2026-08-03', '2026-08-04'],
        'Channel': ['Facebook', 'Facebook', 'Google', 'TikTok', 'TikTok', 'YouTube'],
        'Campaign': ['促销A', '促销A', '促销B', '促销C', '促销D', '促销E'],
        'Impressions': [10000, 10000, 20000, 5000, 0, 8000],
        'Clicks': [200, 200, 500, 100, 0, 150],
        'Conversions': [10, 10, 25, 5, 0, 8],
        'Cost': [500.0, 500.0, None, 200.0, 0.0, 400.0],
    })


@pytest.fixture
def clean_df():
    """创建一份干净的测试DataFrame。"""
    return pd.DataFrame({
        'date': pd.to_datetime(['2026-08-01', '2026-08-02', '2026-08-03']),
        'channel': ['Facebook', 'Google', 'TikTok'],
        'campaign': ['促销A', '促销B', '促销C'],
        'impressions': [10000, 20000, 15000],
        'clicks': [200, 500, 300],
        'conversions': [10, 25, 15],
        'cost': [500.0, 1200.0, 600.0],
    })


class TestStandardizeColumns:
    """测试字段名标准化。"""

    def test_column_names_lowercase(self):
        """字段名应该转小写。"""
        df = pd.DataFrame({'Date': [1], 'Channel': [2]})
        result = standardize_columns(df)
        assert list(result.columns) == ['date', 'channel']

    def test_spaces_replaced_with_underscore(self):
        """空格应该替换为下划线。"""
        df = pd.DataFrame({'Ad Cost': [1], 'Click Count': [2]})
        result = standardize_columns(df)
        assert list(result.columns) == ['ad_cost', 'click_count']

    def test_original_df_not_modified(self):
        """不应该修改原始DataFrame。"""
        df = pd.DataFrame({'Date': [1]})
        original_columns = list(df.columns)
        standardize_columns(df)
        assert list(df.columns) == original_columns


class TestConvertTypes:
    """测试数据类型转换。"""

    def test_date_column_converted(self):
        """日期列应该转换为datetime类型。"""
        df = pd.DataFrame({'date': ['2026-08-01', '2026-08-02']})
        result = convert_types(df)
        assert pd.api.types.is_datetime64_any_dtype(result['date'])

    def test_numeric_columns_converted(self):
        """数字列应该转换为数值类型。"""
        df = pd.DataFrame({
            'impressions': ['10000', '20000'],
            'clicks': ['200', '500'],
            'cost': ['500.0', '1200.0'],
        })
        result = convert_types(df)
        assert pd.api.types.is_numeric_dtype(result['impressions'])
        assert pd.api.types.is_numeric_dtype(result['clicks'])
        assert pd.api.types.is_numeric_dtype(result['cost'])

    def test_invalid_numeric_become_nan(self):
        """无法转换的数值应该变成NaN。"""
        df = pd.DataFrame({'cost': ['500.0', 'invalid', '1200.0']})
        result = convert_types(df)
        assert pd.isna(result['cost'].iloc[1])


class TestRemoveDuplicates:
    """测试去重。"""

    def test_duplicates_removed(self):
        """完全重复的行应该被删除。"""
        df = pd.DataFrame({
            'a': [1, 1, 2],
            'b': ['x', 'x', 'y'],
        })
        result = remove_duplicates(df)
        assert len(result) == 2

    def test_no_duplicates_unchanged(self):
        """没有重复的行应该保持不变。"""
        df = pd.DataFrame({'a': [1, 2, 3]})
        result = remove_duplicates(df)
        assert len(result) == 3

    def test_keep_first(self):
        """应该保留第一条重复记录。"""
        df = pd.DataFrame({'a': [1, 1, 2], 'idx': [0, 1, 2]})
        result = remove_duplicates(df)
        assert result.iloc[0]['idx'] == 0


class TestHandleMissingValues:
    """测试缺失值处理。"""

    def test_key_column_missing_rows_removed(self):
        """关键字段（date, channel, campaign）缺失的行应该被删除。"""
        df = pd.DataFrame({
            'date': ['2026-08-01', None, '2026-08-03'],
            'channel': ['Facebook', 'Google', 'TikTok'],
            'campaign': ['A', 'B', 'C'],
            'cost': [500, 600, 700],
        })
        result = handle_missing_values(df)
        assert len(result) == 2

    def test_cost_missing_filled_with_zero(self):
        """cost缺失应该用0填充。"""
        df = pd.DataFrame({
            'date': ['2026-08-01', '2026-08-02'],
            'channel': ['Facebook', 'Google'],
            'campaign': ['A', 'B'],
            'cost': [500.0, None],
        })
        result = handle_missing_values(df)
        assert result['cost'].iloc[1] == 0

    def test_other_numeric_missing_filled_with_zero(self):
        """其他数值列缺失也应该用0填充。"""
        df = pd.DataFrame({
            'date': ['2026-08-01'],
            'channel': ['Facebook'],
            'campaign': ['A'],
            'impressions': [None],
            'clicks': [None],
            'conversions': [None],
            'cost': [500.0],
        })
        result = handle_missing_values(df)
        assert result['impressions'].iloc[0] == 0
        assert result['clicks'].iloc[0] == 0
        assert result['conversions'].iloc[0] == 0


class TestHandleOutliers:
    """测试异常值处理。"""

    def test_zero_impressions_removed(self):
        """零曝光的行应该被删除。"""
        df = pd.DataFrame({
            'impressions': [10000, 0, 20000],
            'clicks': [200, 0, 500],
            'conversions': [10, 0, 25],
            'cost': [500, 0, 1200],
        })
        result = handle_outliers(df)
        assert len(result) == 2

    def test_negative_values_removed(self):
        """数值为负的行应该被删除。"""
        df = pd.DataFrame({
            'impressions': [10000, -5, 20000],
            'clicks': [200, 10, 500],
            'conversions': [10, 1, 25],
            'cost': [500, 100, 1200],
        })
        result = handle_outliers(df)
        assert len(result) == 2

    def test_clicks_greater_than_impressions_removed(self):
        """点击量大于曝光量的行应该被删除。"""
        df = pd.DataFrame({
            'impressions': [10000, 500, 20000],
            'clicks': [200, 1000, 500],  # 第二行点击>曝光
            'conversions': [10, 50, 25],
            'cost': [500, 100, 1200],
        })
        result = handle_outliers(df)
        assert len(result) == 2

    def test_huge_cost_warned_but_kept(self):
        """花费超大的行应该警告但保留。"""
        df = pd.DataFrame({
            'impressions': [10000, 20000],
            'clicks': [200, 500],
            'conversions': [10, 25],
            'cost': [500, 2000000],  # 200万，超过100万
        })
        result = handle_outliers(df)
        assert len(result) == 2  # 保留，不删除


class TestCleanAdData:
    """测试完整的清洗流程。"""

    def test_dirty_data_cleaned(self, dirty_df):
        """脏数据应该被正确清洗。"""
        result = clean_ad_data(dirty_df)

        # 原始6行：1行重复、1行date缺失、1行零曝光，应该剩下3行
        assert len(result) == 3

        # 不应该有缺失值
        assert result.isna().sum().sum() == 0

        # 不应该有零曝光
        assert (result['impressions'] == 0).sum() == 0

        # 不应该有重复
        assert result.duplicated().sum() == 0

        # 字段名应该是小写
        assert 'date' in result.columns
        assert 'channel' in result.columns

    def test_clean_data_unchanged(self, clean_df):
        """干净的数据清洗后应该基本不变。"""
        result = clean_ad_data(clean_df)
        assert len(result) == len(clean_df)


class TestGetCleaningReport:
    """测试清洗报告。"""

    def test_report_generated(self, dirty_df):
        """应该生成正确的清洗报告。"""
        clean = clean_ad_data(dirty_df)
        report = get_cleaning_report(dirty_df, clean)

        assert report['raw_rows'] == 6
        assert report['clean_rows'] == 3
        assert report['removed_rows'] == 3
        assert report['removal_rate'] == 50.0
        assert report['clean_duplicates'] == 0
