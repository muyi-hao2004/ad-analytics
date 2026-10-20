#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""项目任务入口：一条命令跑通各环节，不需要 make。

为什么不用 Makefile
------------------
Windows 默认没有 make（MSYS/Git Bash 里通常也没有），而这个项目的开发环境就是
Windows。写一个 Makefile 的结果是「README 里有命令、本机跑不了」——和第 2 轮
发现的 venv 路径问题同一类错误：文档写了，但照做会失败。

用 Python 实现的好处：
  1. 跨平台（同一份代码在 Windows / macOS / Linux 行为一致）
  2. 零新依赖（argparse + subprocess 就够）
  3. 用的是当前解释器（sys.executable），不会出现「pip 装到 A、python 跑的是 B」

用法::

    python tasks.py check          # 依赖自检
    python tasks.py init           # 建维度表 + 四层数仓
    python tasks.py test           # 跑单测（不联网、不调 LLM）
    python tasks.py demo           # 跑多轮 Agent 演示（需 LLM 凭据）
    python tasks.py eval           # 跑决策评测（需 LLM 凭据）
    python tasks.py cost           # 看运行成本报表
    python tasks.py all            # check → init → test（不含需要 LLM 的环节）

设计原则
--------
**失败要停**：任何一步返回非 0 就中断，不继续往下跑。
否则会出现「数仓没建成，但测试还是跑了一遍并且通过了」这种假绿灯。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

# 依赖自检清单：(模块名, 是否必需, 缺失时的提示)
DEPS: list[tuple[str, bool, str]] = [
    ("openai", True, "pip install openai"),
    ("yaml", True, "pip install pyyaml"),
    ("pandas", True, "pip install pandas"),
    ("chromadb", False, "仅 RAG 功能需要：pip install chromadb"),
    ("sentence_transformers", False, "仅 RAG 功能需要：pip install sentence-transformers"),
    ("playwright", False, "仅配置抓取需要：pip install playwright && playwright install"),
    ("pytest", False, "跑测试需要：pip install -r requirements-dev.txt"),
]


def _run(cmd: list[str], cwd: Path | None = None, desc: str = "") -> None:
    """执行子命令，失败即中断。

    Args:
        cmd: 命令与参数列表（不经过 shell，避免引号/空格转义问题）
        cwd: 工作目录，默认项目根目录
        desc: 显示给人看的步骤名

    Raises:
        SystemExit: 子进程返回非 0 时，以相同退出码结束，**不吞错误继续跑**
    """
    if desc:
        print(f"\n>>> {desc}")
    print(f"    $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd or ROOT)
    if result.returncode != 0:
        print(f"\n[失败] 上一步退出码 {result.returncode}，已中断。")
        raise SystemExit(result.returncode)


def task_check() -> None:
    """依赖自检：区分「必需」与「可选」，缺必需项直接失败。"""
    import importlib.util

    print(f"解释器: {sys.executable}")
    print(f"版本:   {sys.version.split()[0]}\n")

    missing_required: list[str] = []
    for mod, required, hint in DEPS:
        ok = importlib.util.find_spec(mod) is not None
        tag = "必需" if required else "可选"
        print(f"  {'OK  ' if ok else 'MISS'}  {mod:<24} [{tag}]")
        if not ok:
            if required:
                missing_required.append(mod)
                print(f"        → {hint}")
            else:
                print(f"        → {hint}（不影响 Agent 主流程）")

    if missing_required:
        print(f"\n缺少必需依赖: {', '.join(missing_required)}")
        print("修复: pip install -r requirements.txt")
        raise SystemExit(1)
    print("\n必需依赖齐全。")


def task_init() -> None:
    """建维度表 + 四层数仓。幂等，可以重复跑。"""
    _run([sys.executable, "init_campaign_dims.py"], cwd=SRC, desc="建维度表（含 SCD2）")
    _run([sys.executable, "demo_warehouse.py"], cwd=SRC, desc="建四层数仓")


def task_test() -> None:
    """跑单测。不联网、不调 LLM、不碰真实平台，所以可以放心在 CI 里跑。"""
    _run([sys.executable, "-m", "pytest", "tests/", "-q"], desc="单元测试")


def task_demo() -> None:
    """跑多轮 Agent 演示。会真实调用 LLM，产生少量费用（约 $0.002）。"""
    _run([sys.executable, "demo_multi_round.py"], cwd=SRC, desc="多轮 Agent 演示")


def task_eval(repeat: int = 1, only: str | None = None) -> None:
    """跑决策评测。

    Args:
        repeat: 每个场景重复次数。**建议 3**——单次结果会翻转（实测 S04 有 1/3 误删）。
        only: 只跑指定场景，如 "S02,S04"
    """
    cmd = [sys.executable, "eval/run_eval.py", "--repeat", str(repeat)]
    if only:
        cmd += ["--only", only]
    _run(cmd, desc=f"决策评测（每场景 {repeat} 次）")


def task_cost() -> None:
    """打印运行成本报表。"""
    _run([sys.executable, "cost_report.py"], cwd=SRC, desc="成本报表")


def task_all() -> None:
    """串跑不依赖 LLM 的环节：自检 → 建数仓 → 测试。"""
    task_check()
    task_init()
    task_test()
    print("\n全部通过。需要 LLM 的环节：python tasks.py demo / eval")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IAA 中台任务入口（跨平台，替代 Makefile）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="task", required=True)

    sub.add_parser("check", help="依赖自检")
    sub.add_parser("init", help="建维度表 + 四层数仓")
    sub.add_parser("test", help="跑单测")
    sub.add_parser("demo", help="多轮 Agent 演示（需 LLM）")
    sub.add_parser("cost", help="成本报表")
    sub.add_parser("all", help="check + init + test")

    p_eval = sub.add_parser("eval", help="决策评测（需 LLM）")
    p_eval.add_argument("--repeat", type=int, default=1,
                        help="每场景重复次数，建议 3")
    p_eval.add_argument("--only", type=str, default=None,
                        help="只跑指定场景，如 S02,S04")

    args = parser.parse_args()

    if args.task == "eval":
        task_eval(repeat=args.repeat, only=args.only)
    else:
        {"check": task_check, "init": task_init, "test": task_test,
         "demo": task_demo, "cost": task_cost, "all": task_all}[args.task]()


if __name__ == "__main__":
    main()
