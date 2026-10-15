"""
广告投放智能分析平台 - Airflow DAG
把原来的 main.py 流水线改写成 Airflow 任务调度形式
"""

# 第1步：导入需要的模块
from datetime import datetime, timedelta
import os
import sys

# 把项目根目录加到Python路径，这样才能导入我们自己的模块
PROJECT_ROOT = "/home/airflow/ad-analytics"  # 生产环境的路径
# 本地开发时用这个：
# PROJECT_ROOT = "C:/Users/Mia/ad-analytics"
sys.path.insert(0, PROJECT_ROOT)

from airflow import DAG
from airflow.operators.python import PythonOperator

from ad_analytics.data_loader import load_from_csv, validate_data
from ad_analytics.cleaning import clean_ad_data, get_cleaning_report
from ad_analytics.metrics import (
    calculate_basic_metrics,
    calculate_overall_metrics,
    group_by_dimension,
    group_by_date,
)
from ad_analytics.anomaly_detection import detect_all_anomalies
from ad_analytics.database import init_database, insert_ad_data
from ad_analytics.report import generate_report
import pandas as pd


# ============================================================
# 第2步：定义每个任务对应的Python函数
# ============================================================

def task_load_data(**context):
    """任务1：加载原始数据"""
    print("【任务1】开始加载数据...")

    raw_data_path = os.path.join(PROJECT_ROOT, "data", "raw", "sample_ad_data.csv")
    df_raw = load_from_csv(raw_data_path)
    print(f"原始数据: {df_raw.shape[0]} 行, {df_raw.shape[1]} 列")

    # 保存到中间文件，给下一步用
    output_path = os.path.join(PROJECT_ROOT, "data", "interim", "raw.parquet")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df_raw.to_parquet(output_path, index=False)
    print(f"原始数据已保存到: {output_path}")

    return output_path


def task_clean_data(**context):
    """任务2：清洗数据"""
    print("【任务2】开始清洗数据...")

    # 从上一步的文件读取数据
    input_path = os.path.join(PROJECT_ROOT, "data", "interim", "raw.parquet")
    df_raw = pd.read_parquet(input_path)

    # 清洗
    df_clean = clean_ad_data(df_raw)
    print(f"清洗后数据: {df_clean.shape[0]} 行, {df_clean.shape[1]} 列")

    # 保存
    output_path = os.path.join(PROJECT_ROOT, "data", "interim", "clean.parquet")
    df_clean.to_parquet(output_path, index=False)
    print(f"清洗后数据已保存到: {output_path}")

    return output_path


def task_calculate_metrics(**context):
    """任务3：计算指标"""
    print("【任务3】开始计算指标...")

    input_path = os.path.join(PROJECT_ROOT, "data", "interim", "clean.parquet")
    df_clean = pd.read_parquet(input_path)

    # 计算基础指标
    df_with_metrics = calculate_basic_metrics(df_clean)
    print("基础指标计算完成（CTR/CVR/CPC/CPA）")

    # 保存带指标的数据
    output_path = os.path.join(PROJECT_ROOT, "data", "interim", "with_metrics.parquet")
    df_with_metrics.to_parquet(output_path, index=False)
    print(f"带指标数据已保存到: {output_path}")

    return output_path


def task_detect_anomalies(**context):
    """任务4：异常检测"""
    print("【任务4】开始异常检测...")

    input_path = os.path.join(PROJECT_ROOT, "data", "interim", "with_metrics.parquet")
    df_with_metrics = pd.read_parquet(input_path)

    # 检测异常
    df_anomalies = detect_all_anomalies(df_with_metrics)
    print(f"检测到异常: {len(df_anomalies)} 条")

    # 保存
    output_path = os.path.join(PROJECT_ROOT, "data", "interim", "anomalies.parquet")
    df_anomalies.to_parquet(output_path, index=False)
    print(f"异常结果已保存到: {output_path}")

    return output_path


def task_write_to_db(**context):
    """任务5：写入数据库"""
    print("【任务5】开始写入数据库...")

    input_path = os.path.join(PROJECT_ROOT, "data", "interim", "clean.parquet")
    df_clean = pd.read_parquet(input_path)

    try:
        # 初始化数据库
        init_database()
        print("数据库初始化完成")

        # 写入数据
        insert_ad_data(df_clean, if_exists="replace")
        print(f"数据写入完成: {len(df_clean)} 条记录")
    except Exception as e:
        print(f"数据库写入失败: {e}")
        raise  # 失败就报错，Airflow会自动重试


def task_generate_report(**context):
    """任务6：生成日报"""
    print("【任务6】开始生成日报...")

    # 读取需要的数据
    df_with_metrics = pd.read_parquet(
        os.path.join(PROJECT_ROOT, "data", "interim", "with_metrics.parquet")
    )
    df_anomalies = pd.read_parquet(
        os.path.join(PROJECT_ROOT, "data", "interim", "anomalies.parquet")
    )

    # 计算汇总指标
    overall_metrics = calculate_overall_metrics(df_with_metrics)
    df_channel = group_by_dimension(df_with_metrics, "channel")
    df_daily = group_by_date(df_with_metrics)

    # 生成报表
    report_date = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(PROJECT_ROOT, "reports", f"daily_report_{report_date}.html")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    df_overall = pd.DataFrame([overall_metrics])

    generate_report(
        df=df_with_metrics,
        metrics_df=df_overall,
        channel_df=df_channel,
        daily_df=df_daily,
        anomalies_df=df_anomalies,
        output_path=report_path,
        title=f"广告投放日报 - {report_date}",
    )
    print(f"报表已生成: {report_path}")

    return report_path


# ============================================================
# 第3步：定义默认参数
# ============================================================
default_args = {
    'owner': 'zenghao',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,  # 失败重试2次
    'retry_delay': timedelta(minutes=5),  # 等5分钟再重试
}


# ============================================================
# 第4步：创建DAG对象
# ============================================================
with DAG(
    dag_id='ad_analytics_daily_pipeline',
    default_args=default_args,
    description='广告投放智能分析平台 - 每日数据流水线',
    schedule_interval='0 8 * * *',  # 每天早上8点跑
    start_date=datetime(2026, 9, 1),
    catchup=False,  # 不补跑历史任务
    tags=['ad-analytics', 'daily', 'etl'],
) as dag:

    # ============================================================
    # 第5步：定义任务
    # ============================================================

    t_load = PythonOperator(
        task_id='load_data',
        python_callable=task_load_data,
    )

    t_clean = PythonOperator(
        task_id='clean_data',
        python_callable=task_clean_data,
    )

    t_metrics = PythonOperator(
        task_id='calculate_metrics',
        python_callable=task_calculate_metrics,
    )

    t_anomaly = PythonOperator(
        task_id='detect_anomalies',
        python_callable=task_detect_anomalies,
    )

    t_db = PythonOperator(
        task_id='write_to_database',
        python_callable=task_write_to_db,
    )

    t_report = PythonOperator(
        task_id='generate_report',
        python_callable=task_generate_report,
    )

    # ============================================================
    # 第6步：定义任务依赖关系（谁先跑谁后跑）
    # ============================================================
    # 流程：加载 → 清洗 → 计算指标 → 异常检测 → 写数据库 + 生成报表
    # 注意：写数据库和生成报表可以并行跑（用>>表示先后）

    t_load >> t_clean >> t_metrics >> [t_anomaly, t_db] >> t_report
