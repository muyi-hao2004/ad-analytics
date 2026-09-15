"""
配置管理模块
统一管理项目的所有配置，从环境变量和.env文件读取。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（src/ad_analytics/config.py → 往上三级是项目根目录）
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# 加载.env文件（如果存在）
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    """应用配置类，所有配置都从这里读取。"""

    # ===== 应用配置 =====
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ===== 数据库配置（MySQL）=====
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_NAME: str = os.getenv("DB_NAME", "ad_analytics")
    DB_USER: str = os.getenv("DB_USER", "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

    # ===== 大模型配置（第4个月会用到）=====
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen-plus")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

    @property
    def database_url(self) -> str:
        """构造数据库连接URL（MySQL + pymysql）。"""
        return f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"

    @property
    def is_development(self) -> bool:
        """是否是开发环境。"""
        return self.APP_ENV == "development"

    def __repr__(self) -> str:
        """打印配置时隐藏敏感信息。"""
        return (
            f"Settings(env={self.APP_ENV}, debug={self.DEBUG}, "
            f"db={self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME})"
        )


# 全局单例，其他模块直接 import settings
settings = Settings()
