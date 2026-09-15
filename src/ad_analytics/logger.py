"""
日志配置模块
统一配置项目的日志格式，同时输出到控制台和文件。
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from .config import settings, PROJECT_ROOT


def setup_logger(name: str = "ad_analytics") -> logging.Logger:
    """
    配置并返回一个logger。

    Args:
        name: logger名称

    Returns:
        配置好的logger实例
    """
    logger = logging.getLogger(name)

    # 避免重复添加handler（多次调用时）
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # 日志格式：时间 - 日志级别 - 模块名 - 消息
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ===== 控制台输出 =====
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    logger.addHandler(console_handler)

    # ===== 文件输出（生产环境才写文件，开发环境只打控制台）=====
    if not settings.is_development:
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(exist_ok=True)

        file_handler = RotatingFileHandler(
            filename=log_dir / "app.log",
            maxBytes=10 * 1024 * 1024,  # 单个文件最大10MB
            backupCount=5,  # 保留5个备份
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

    return logger


# 全局logger，其他模块直接 import logger
logger = setup_logger()
