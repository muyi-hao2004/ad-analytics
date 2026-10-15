"""
第一个 Airflow DAG 示例
这个 DAG 做的事情很简单：打印一句"Hello Airflow"
"""

# 第1步：导入需要的模块
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

# 第2步：定义你要执行的Python函数
def say_hello():
    print("Hello Airflow! 我是第一个任务")
    print("今天也是努力学习的一天！")

def say_world():
    print("Hello World! 我是第二个任务")

# 第3步：定义默认参数
default_args = {
    'owner': 'zenghao',           # 这个DAG的负责人是谁
    'depends_on_past': False,      # 是否依赖上一次的运行结果
    'email': ['test@test.com'],    # 出错时发邮件通知谁
    'email_on_failure': False,     # 失败时要不要发邮件
    'email_on_retry': False,       # 重试时要不要发邮件
    'retries': 1,                  # 失败后重试几次
    'retry_delay': timedelta(minutes=5),  # 重试间隔多久
}

# 第4步：创建DAG对象
with DAG(
    dag_id='hello_airflow_dag',    # DAG的唯一名字，不能重复
    default_args=default_args,      # 用上面定义的默认参数
    description='我的第一个Airflow DAG',  # 描述这个DAG是干嘛的
    schedule_interval='0 8 * * *',  # 定时执行时间（cron表达式），这里是每天早上8点
    start_date=datetime(2026, 9, 20),  # 从哪天开始
    catchup=False,                 # 要不要补跑之前没跑的任务
    tags=['example', 'first_dag'], # 标签，方便在界面上找
) as dag:

    # 第5步：定义任务1
    task_hello = PythonOperator(
        task_id='say_hello_task',  # 任务的唯一ID
        python_callable=say_hello, # 要执行哪个Python函数
    )

    # 第6步：定义任务2
    task_world = PythonOperator(
        task_id='say_world_task',
        python_callable=say_world,
    )

    # 第7步：定义任务之间的依赖关系
    # 意思是：先跑task_hello，跑完再跑task_world
    task_hello >> task_world
