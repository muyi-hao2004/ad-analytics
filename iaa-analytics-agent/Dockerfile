# Airflow 自定义镜像
#
# 基于官方镜像，补装两类依赖：
#   1. 项目 DAG 需要的 Python 包（openai + python-dotenv）
#   2. 数仓任务需要的 Spark 运行时（JRE + pyspark）
#
# 为什么容器里要装 Spark
# ----------------------
# DAG 的 task 用 subprocess 调 `scripts/build_*.py`，这些脚本都依赖 pyspark。
# 只装 Airflow 不装 Spark 的话，DAG 能加载、能触发，但每个 task 一执行就报
# `ModuleNotFoundError: pyspark`，而且要到 task log 里翻才能发现。
# 装齐之后整条链路在容器内自洽，不依赖宿主机的 Python 环境。
#
# 为什么基础镜像用 ARG 而不是写死加速器地址
# ----------------------------------------
# 之前这里写的是 `FROM docker.1panel.live/apache/airflow:...`，把镜像源硬编码进了
# Dockerfile。结果是加速器一挂（403 Forbidden）镜像就永远构建不出来。
# 现在默认用官方名，需要加速时构建时传参即可，不改文件：
#
#   docker compose build --build-arg AIRFLOW_BASE=docker.1ms.run/apache/airflow:2.10.5-python3.12
ARG AIRFLOW_BASE=apache/airflow:2.10.5-python3.12
FROM ${AIRFLOW_BASE}

# 国内环境用国内源；apt 与 pip 都要换，否则构建会卡在下载上。
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# ---------- 1. Java 运行时（pyspark 的硬依赖）----------
# 官方 airflow 镜像基于 debian bookworm，不自带 JRE。
# 用 headless 版：不需要 GUI，省 100MB 左右。
# pyspark 4.x 要求 Java 17+。
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# ---------- 2. Python 依赖 ----------
# 官方镜像默认是 airflow 用户，禁止 root 直接 pip install，
# 切到 airflow 用户装包（官方推荐方式）。
USER airflow

# 版本与宿主机 .venv-spark 对齐（4.0.3）。
# 两边版本不一致时，同一份 SQL 的 schema 推断行为可能不同，
# 会出现「本地能跑、容器报列类型不匹配」这种最难查的问题。
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} \
    openai python-dotenv pyspark==4.0.3

# 装完自检：JRE 和 pyspark 缺一个，构建就该在这里失败，
# 而不是等到某天任务跑挂了才回头找原因。
RUN java -version 2>&1 | head -1 \
    && python -c "import pyspark, openai, dotenv; print('pyspark', pyspark.__version__)"
