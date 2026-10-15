# Airflow 自定义镜像
# 基于官方镜像，补装项目 DAG 需要的额外依赖（openai + python-dotenv）。
# pandas / numpy 官方镜像已内置，无需重复安装。
# 国内环境通过镜像加速器拉取（生产/海外环境可换回 apache/airflow:2.10.5-python3.12）
FROM docker.1panel.live/apache/airflow:2.10.5-python3.12

# 官方镜像默认是 airflow 用户，禁止 root 直接 pip install，
# 切到 airflow 用户装包（官方推荐方式）。
USER airflow

# 项目 DAG 依赖（agent.py 用 openai 调 deepseek，dotenv 读 .env）
RUN pip install --no-cache-dir openai python-dotenv
