# day26 Docker 容器化入门

> 定位：数据工程/MLOps 岗的基础设施技能，模型部署与环境一致性的地基。

---

## 一、Docker 解决什么问题

**一句话**：把"程序 + 依赖"打包成一个标准箱子，在任何机器上原样跑起来。

**痛点**："在我电脑上能跑，你电脑上就报错"——根因是环境不一致（Python 版本、缺包、路径）。Docker 就是解决这个。

---

## 二、三个核心概念（面试必问）

| 概念 | 类比 | 关键点 |
|---|---|---|
| 镜像 Image | 箱子的"设计图纸" | 只读、不可变，是模板 |
| 容器 Container | 按图纸造出的"活箱子" | 真正在跑的实例 |
| Dockerfile | "说明书" | 告诉 Docker 怎么造镜像 |

**关系链**：
```
Dockerfile（说明书）--docker build--> 镜像（图纸）--docker run--> 容器（活箱子）
```

---

## 三、Docker vs 虚拟机（超高频面试题）

| | 虚拟机 VM | Docker 容器 |
|---|---|---|
| 隔离层级 | 隔离整个 OS（重） | 隔离"应用+依赖"，共享宿主内核（轻） |
| 大小/启动 | GB 级 / 分钟级 | MB 级 / 秒级 |

**一句话**：虚拟机"隔离到 OS 层"，容器"隔离到进程层"。

---

## 四、Dockerfile 长什么样（Python 项目示例）

```dockerfile
FROM python:3.12              # 基于官方镜像（地基）
WORKDIR /app                  # 工作目录
COPY requirements.txt .       # 先拷贝依赖清单
RUN pip install -r requirements.txt   # 装依赖（层会被缓存）
COPY . .                      # 再拷贝代码
CMD ["python", "main.py"]     # 启动时跑什么
```

**优化技巧**：`COPY requirements.txt` 和 `COPY .` 分开写，利用层缓存加速构建（改代码不用重装依赖）。

---

## 五、Docker Compose：编排多容器

Docker 管"单个容器"，**Compose 管"一群容器怎么配合"**（一个 yaml + 一条命令拉起多个服务）。

你项目的 `docker-compose.yaml` 跑 Airflow，就是因为它需要 webserver + scheduler + Postgres 多个容器一起。

---

## 六、常用命令

```bash
docker build -t myapp .        # 造镜像
docker run -p 8000:8000 myapp  # 跑容器（端口映射）
docker ps / docker images      # 看容器/镜像
docker exec -it xxx bash       # 进容器调试
docker-compose up -d           # 一次启动编排好的所有容器
```

---

## 七、为什么数据工程/MLOps 必须会 Docker

1. 模型部署：训练好的模型打包成镜像再上线；
2. 环境一致：算法团队/你/线上用同一镜像；
3. 你项目里 Airflow 已经在用 Docker。

---

## 八、一句总结

> Docker = "程序+依赖"打包成标准箱子，到处跑一致。Dockerfile→镜像→容器是一条链；比虚拟机轻（只隔离进程、共享内核）；Compose 管多容器协作。对数据工程/MLOps 岗，它是模型部署和环境一致性的地基。
