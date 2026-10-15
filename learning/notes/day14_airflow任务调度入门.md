# Day14：Airflow 任务调度入门

## 一、Airflow 是什么？

Apache Airflow 是一个**开源的任务调度平台**，简单说就是：
> **Airflow = 定时闹钟 + 流水线管理 + 失败告警**

### 解决什么问题？
- 每天手动跑数据任务太麻烦 → 定时自动跑
- 步骤多了不知道哪步出错 → 可视化监控
- 失败了没人知道 → 自动重试 + 邮件告警
- 步骤之间有依赖 → 自动按顺序跑

---

## 二、核心概念

| 概念 | 一句话理解 |
|---|---|
| **DAG** | 任务流程图（有向无环图），一个数据流水线一个 DAG |
| **Task** | 流程图里的每一步 |
| **Operator** | Task 具体干什么（PythonOperator 跑 Python 函数） |
| `>>` | 任务依赖（A 跑完跑 B） |
| **Scheduler** | 定时调度器，到点触发任务 |
| **Web UI** | 网页监控界面，看任务状态、日志、手动触发 |

---

## 三、第一个 DAG 的结构

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'zenghao',
    'retries': 2,                          # 失败重试2次
    'retry_delay': timedelta(minutes=5),   # 重试间隔5分钟
}

def my_function(**context):
    print("Hello Airflow!")

with DAG(
    dag_id='my_first_dag',
    default_args=default_args,
    schedule_interval='0 9 * * *',   # 每天早上9点（cron）
    start_date=datetime(2026, 9, 1),
    catchup=False,                    # 不补跑历史（重要！）
    tags=['example'],
) as dag:

    task1 = PythonOperator(
        task_id='my_task',
        python_callable=my_function,
    )
```

---

## 四、schedule_interval（定时规则）

| cron 表达式 | 意思 |
|---|---|
| `0 9 * * *` | 每天早上9点 |
| `0 */2 * * *` | 每2小时 |
| `0 9 * * 1` | 每周一早上9点 |
| `@daily` | 每天凌晨0点 |
| `None` | 不自动跑，手动触发 |

> cron 格式：分 时 日 月 周

---

## 五、进阶概念

### 1. Variables（变量）
- 存在 Airflow 数据库里的全局配置
- 存什么：数据库密码、文件路径、邮件地址、开关
- 代码里读取：`Variable.get("mysql_password")`
- 好处：改配置不用改代码

### 2. Connections（连接）
- 专门存外部系统连接信息（MySQL、API、S3）
- 比 Variables 更结构化（host、port、user、password）
- 用 Hook 读取：`MySqlHook(mysql_conn_id='conn_id')`
- 好处：密码不明文写在代码里，多个 DAG 共用

### 3. Sensors（传感器）
- 一直等某个条件满足了才往下跑
- 常用：FileSensor（等文件出现）、SqlSensor（等 SQL 有结果）
- 参数：poke_interval（多久检查一次）、timeout（最多等多久）
- 场景：等当天的数据文件到了再处理

### 4. TaskGroup（任务组）
- 把相关任务打包成一组，Web UI 上可折叠展开
- 场景：DAG 任务多了，按"数据准备/指标计算/输出"分组

### 5. BranchPythonOperator（分支）
- 根据条件选择走哪个分支
- 场景：数据量大跑全量计算，数据量小跑轻量版

### 6. XCom（任务间传数据）
- 上游 Task 传小数据给下游 Task
- 发送：`context['task_instance'].xcom_push(key='k', value=v)`
- 接收：`context['task_instance'].xcom_pull(task_ids='task_id', key='k')`
- 注意：只传小数据（数字、字符串），不要传 DataFrame

### 7. Context（任务上下文）
- PythonOperator 函数加 `**context` 就能自动拿到
- 常用：
  - `ds`：执行日期字符串（如 `'2026-09-18'`）
  - `ts`：执行时间戳
  - `task_instance`：用来操作 XCom
- 用途：用 ds 拼文件路径，每天处理不同日期的数据

### 8. Pools（池）
- 限制同时运行的任务数量，防止资源占满
- 学习阶段不用管，默认配置够

---

## 六、任务依赖写法

```python
# 顺序执行：A → B → C
task1 >> task2 >> task3

# 并行执行：A 跑完后 B 和 C 同时跑
task1 >> [task2, task3]

# 汇合：B 和 C 都跑完才跑 D
[task2, task3] >> task4
```

---

## 七、你的项目接入 Airflow 后的流程

```
[FileSensor] 等数据文件到了
    ↓
[数据清洗] clean_data
    ↓
[指标计算] calculate_metrics
    ↓
[异常检测] detect_anomalies
    ↓
[写数据库] write_to_db（用 Connection 存 MySQL 信息）
    ↓
[生成报表] generate_report（用 Variable 存邮箱）
    ↓
[发邮件] send_report_email
```

用到的概念：DAG + Task + PythonOperator + Sensor + Connection + Variable + Context + retries

---

## 八、安装方式

- **推荐用 Docker Compose**（Windows 上最省事）
- 需要先装 Docker Desktop
- 下载官方 docker-compose.yaml，一条 `docker compose up -d` 启动
- Web UI：http://localhost:8080（默认 admin/admin）
