"""
data_loader模块的单元测试
"""

import pandas as pd
import pytest

from ad_analytics.data_loader import (
    validate_data,
    get_data_summary,
    DataValidationError,
    REQUIRED_COLUMNS,
)


@pytest.fixture
def sample_df():
    """创建一个标准的测试DataFrame。"""
    return pd.DataFrame({
        'date': pd.to_datetime(['2026-08-01', '2026-08-01', '2026-08-02']),
        'channel': ['Facebook', 'Google', 'TikTok'],
        'campaign': ['促销_A', '促销_B', '新品_C'],
        'region': ['华东', '华南', '华北'],
        'device': ['Mobile', 'Desktop', 'Mobile'],
        'impressions': [10000, 20000, 15000],
        'clicks': [200, 500, 300],
        'conversions': [10, 25, 15],
        'cost': [500.0, 1200.0, 600.0],
    })


class TestValidateData:
    """测试数据验证功能。"""

    def test_valid_data_passes(self, sample_df):
        """正常数据应该通过验证，没有警告。"""
        report = validate_data(sample_df)
        assert report['total_rows'] == 3
        assert report['total_columns'] == 9
        assert len(report['missing_values']) == 0
        assert report['duplicate_rows'] == 0
        assert report['date_range']['min'] == '2026-08-01'
        assert report['date_range']['max'] == '2026-08-02'

    def test_missing_required_column_raises(self, sample_df):
        """缺少必填列时，如果raise_on_error=True应该抛出异常。"""
        df = sample_df.drop(columns=['cost'])
        with pytest.raises(DataValidationError):
            validate_data(df, raise_on_error=True)

    def test_missing_required_column_records_warning(self, sample_df):
        """缺少必填列时，如果raise_on_error=False应该记录警告。"""
        df = sample_df.drop(columns=['cost'])
        report = validate_data(df, raise_on_error=False)
        assert len(report['warnings']) > 0
        assert 'cost' in report['warnings'][0]

    def test_missing_values_detected(self, sample_df):
        """应该能检测到缺失值。"""
        df = sample_df.copy()
        df.loc[0, 'cost'] = None
        df.loc[1, 'clicks'] = None

        report = validate_data(df)
        assert report['missing_values']['cost'] == 1
        assert report['missing_values']['clicks'] == 1

    def test_negative_values_detected(self, sample_df):
        """应该能检测到负值。"""
        df = sample_df.copy()
        df.loc[0, 'impressions'] = -100
        df.loc[1, 'cost'] = -50.0

        report = validate_data(df)
        assert report['invalid_values']['impressions']['negative'] == 1
        assert report['invalid_values']['cost']['negative'] == 1

    def test_zero_impressions_detected(self, sample_df):
        """应该能检测到零曝光记录。"""
        df = sample_df.copy()
        df.loc[0, 'impressions'] = 0

        report = validate_data(df)
        assert report['invalid_values']['impressions']['zero'] == 1

    def test_duplicate_rows_detected(self, sample_df):
        """应该能检测到重复行。"""
        df = pd.concat([sample_df, sample_df.iloc[[0]]], ignore_index=True)

        report = validate_data(df)
        assert report['duplicate_rows'] == 1

    def test_empty_dataframe(self):
        """空DataFrame应该不会报错。"""
        df = pd.DataFrame(columns=REQUIRED_COLUMNS)
        report = validate_data(df)
        assert report['total_rows'] == 0
        assert report['date_range'] is None


class TestGetDataSummary:
    """测试数据摘要功能。"""

    def test_summary_calculation(self, sample_df):
        """应该正确计算汇总指标。"""
        summary = get_data_summary(sample_df)

        assert summary['total_rows'] == 3
        assert summary['total_impressions'] == 45000
        assert summary['total_clicks'] == 1000
        assert summary['total_conversions'] == 50
        assert summary['total_cost'] == 2300.0

        # CTR = 点击 / 曝光 = 1000 / 45000 ≈ 0.022222
        assert abs(summary['overall_ctr'] - 0.022222) < 0.0001
        # CVR = 转化 / 点击 = 50 / 1000 = 0.05
        assert abs(summary['overall_cvr'] - 0.05) < 0.0001
        # CPC = 花费 / 点击 = 2300 / 1000 = 2.3
        assert abs(summary['overall_cpc'] - 2.3) < 0.0001

        assert summary['channels'] == ['Facebook', 'Google', 'TikTok']
        assert summary['date_range']['min'] == '2026-08-01'

    def test_empty_dataframe_summary(self):
        """空DataFrame的摘要应该全是0。"""
        df = pd.DataFrame()
        summary = get_data_summary(df)
        assert summary['total_rows'] == 0
        assert summary['total_impressions'] == 0
        assert summary['total_cost'] == 0.0
        assert summary['overall_ctr'] == 0.0

    def test_zero_impressions_no_division_error(self, sample_df):
        """全零曝光时不应该除零报错。"""
        df = sample_df.copy()
        df['impressions'] = 0
        df['clicks'] = 0

        summary = get_data_summary(df)
        assert summary['overall_ctr'] == 0.0
        assert summary['overall_cvr'] == 0.0
        assert summary['overall_cpc'] == 0.0
