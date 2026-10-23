"""数仓实验环境自检。

只依赖标准库，任何 Python 3.9+ 都能直接运行：

    python scripts/check_env.py

它不修改任何东西，只做诊断：告诉你哪一项通过了、哪一项会卡住你、以及怎么修。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass

OK = "OK  "
FAIL = "FAIL"
WARN = "WARN"


@dataclass
class Check:
    """一项检查的结果。"""

    name: str
    status: str
    detail: str
    fix: str = ""


def _run(cmd: list[str]) -> tuple[int, str]:
    """执行命令并合并 stdout/stderr。Windows 上 java -version 输出到 stderr。"""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def check_python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        return Check("Python 版本", FAIL, ver, "需要 3.10+，建议 3.11 ~ 3.13")
    if v >= (3, 14):
        return Check(
            "Python 版本",
            WARN,
            f"{ver}（过新）",
            "PySpark 4.x 官方支持到 3.13，3.14 可能装不上或运行异常。"
            "建议单独建一个 3.13 的 venv 给本项目用",
        )
    return Check("Python 版本", OK, ver)


def check_java() -> Check:
    code, out = _run(["java", "-version"])
    if code != 0:
        return Check("Java", FAIL, "未找到", "安装 JDK 17（Spark 4 要求 17+）")
    m = re.search(r'version "?(\d+)', out)
    if not m:
        return Check("Java", WARN, "已安装但版本无法识别", "确认 java -version 输出")
    major = int(m.group(1))
    if major < 17:
        return Check("Java", FAIL, f"JDK {major}", "Spark 4 需要 JDK 17+")
    return Check("Java", OK, f"JDK {major}")


def _ver_tuple(v: str) -> tuple[int, ...]:
    """把 '4.0.3' 解析成 (4, 0, 3)，容忍后缀如 4.0.3.dev1。"""
    parts = []
    for p in v.split(".")[:3]:
        num = re.match(r"\d+", p)
        parts.append(int(num.group()) if num else 0)
    return tuple(parts)


def check_pyspark() -> Check:
    try:
        import pyspark  # noqa: F401
    except ImportError:
        return Check(
            "pyspark",
            FAIL,
            "未安装",
            "pip install pyspark==4.0.3（装在 3.11~3.13 的 venv 里，不要装在 3.14）",
        )

    ver = pyspark.__version__
    # SPARK-53759：Windows 上只能用 simple-worker（没有 os.fork），
    # 该路径漏了 flush()；Python 3.12+ 改了 GC 顺序后数据丢失，JVM 收到 EOFException。
    # 已在 4.0.3 / 4.1.2 / 3.5.9 修复。
    if sys.platform == "win32" and sys.version_info >= (3, 12) and _ver_tuple(ver) < (4, 0, 3):
        return Check(
            "pyspark",
            FAIL,
            f"{ver}（会必崩）",
            "SPARK-53759：Windows + Python 3.12+ 上此版本 createDataFrame 必定崩。"
            "升级到 pyspark==4.0.3 以上",
        )
    return Check("pyspark", OK, ver)


def check_winutils() -> Check:
    """Windows 上 Spark 需要 winutils.exe，否则 Shell 静态初始化就抛异常。"""
    if sys.platform != "win32":
        return Check("winutils", OK, "非 Windows，不需要")

    home = os.environ.get("HADOOP_HOME")
    local = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "infra", "hadoop")
    )
    exe = os.path.join(home or local, "bin", "winutils.exe")
    if os.path.exists(exe):
        return Check("winutils", OK, f"{exe}")
    return Check(
        "winutils",
        FAIL,
        "未找到",
        "需要 infra/hadoop/bin/winutils.exe；缺失时 Spark 会报 "
        "HADOOP_HOME and hadoop.home.dir are unset",
    )


def check_docker() -> Check:
    code, out = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    if code != 0:
        return Check(
            "Docker daemon",
            FAIL,
            "未运行",
            "打开 Docker Desktop，等左下角显示 Engine running 再重跑本脚本",
        )
    return Check("Docker daemon", OK, f"engine {out.strip()}")


def _running_lab_containers() -> set[str]:
    """返回正在运行的 lab-* 容器名集合。没装 docker 或没启动时返回空集。"""
    code, out = _run(["docker", "ps", "--filter", "name=lab-", "--format", "{{.Names}}"])
    if code != 0:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def check_ports() -> Check:
    """端口检查要看「谁占着」：本项目的容器占着是正常，别的进程占着才是问题。

    注意不能用 bind 来测（Windows 上对 0.0.0.0 已监听的端口，
    bind 到 127.0.0.1 仍可能成功），必须用 connect_ex 判断有没有人在听。
    """
    running = _running_lab_containers()
    owners = {9083: "lab-metastore", 5433: "lab-postgres"}
    listening, ours, foreign = [], [], []

    for port, container in owners.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                listening.append(str(port))
                (ours if container in running else foreign).append(str(port))

    if foreign:
        return Check(
            "端口 9083 / 5433",
            FAIL,
            f"被其他进程占用: {', '.join(foreign)}",
            "先停掉占用方，或改 docker-compose.yml 的端口映射（记得同步脚本里的常量）",
        )
    if ours:
        return Check(
            "端口 9083 / 5433",
            OK,
            f"已由本项目容器监听: {', '.join(ours)}",
        )
    return Check("端口 9083 / 5433", OK, "空闲（容器还没起）")


def check_disk() -> Check:
    target = "D:/" if os.path.exists("D:/") else "C:/"
    total, used, free = shutil.disk_usage(target)
    free_gb = free / 1024**3
    detail = f"{target} 剩余 {free_gb:.0f} GB"
    if free_gb < 20:
        return Check("磁盘", FAIL, detail, "至少留 20 GB：数据 625MB + Parquet 中间结果 + Docker 镜像")
    return Check("磁盘", OK, detail)


def check_memory() -> Check:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_gb = stat.ullTotalPhys / 1024**3
        avail_gb = stat.ullAvailPhys / 1024**3
    except Exception:
        return Check("内存", WARN, "无法读取", "手动确认可用内存在 4 GB 以上")

    detail = f"总 {total_gb:.1f} GB / 可用 {avail_gb:.1f} GB"

    # 阈值分两档：容器在跑时 Docker Desktop 自己会吃掉 4-5 GB，
    # 宿主机「可用」看起来就很少，这时候 2 GB 就够 Spark driver（2g）+ worker 用。
    running = _running_lab_containers()
    if running:
        if avail_gb < 2:
            return Check(
                "内存",
                FAIL,
                detail,
                "容器在跑已占约 2 GB。Spark driver 要 2g，先关掉浏览器等占内存的程序",
            )
        if avail_gb < 3:
            return Check(
                "内存",
                WARN,
                detail,
                "勉强够跑 local[2]，但窗口留太小。想把 DWD 那步跑稳，建议关掉一部分程序",
            )
        return Check("内存", OK, detail)

    if avail_gb < 4:
        return Check(
            "内存",
            FAIL,
            detail,
            "关掉浏览器等占内存的程序。Spark driver 建议 2g，容器合计约 2g",
        )
    return Check("内存", OK, detail)


def main() -> int:
    print("=" * 62)
    print(" 数仓实验环境自检")
    print("=" * 62)
    print()

    checks = [
        check_python(),
        check_java(),
        check_pyspark(),
        check_winutils(),
        check_docker(),
        check_ports(),
        check_disk(),
        check_memory(),
    ]

    for c in checks:
        print(f"[{c.status}] {c.name:<16} {c.detail}")
        if c.fix and c.status in (FAIL, WARN):
            print(f"       -> {c.fix}")

    print()
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]

    if fails:
        print(f"还有 {len(fails)} 项不通过，先修这些再继续。")
        return 1
    if warns:
        print(f"全部通过，但有 {len(warns)} 项需要注意（不一定阻塞）。")
        return 0

    print("全部通过。下一步：")
    print("  cd infra && docker compose up -d --build")
    print("  docker compose logs -f metastore   # 看到 Started Hive Metastore 就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
