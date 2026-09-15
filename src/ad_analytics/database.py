"""
数据库操作模块（简化版，不用ORM，只用函数和SQL）
负责连接MySQL、建库、建表、写入数据、查询数据。
"""

import pymysql
import pandas as pd
from sqlalchemy import create_engine

from .config import settings
from .logger import logger


def get_connection():
    """
    连接 MySQL 数据库，返回连接对象。
    参数和 DataGrip 里填的连接信息一样。
    """
    conn = pymysql.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )
    return conn


def create_database():
    """
    创建数据库（如果不存在）。
    注意：创建数据库时不能指定 database 参数，因为数据库还不存在。
    """
    conn = pymysql.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        charset='utf8mb4',
    )
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS {settings.DB_NAME} "
            f"DEFAULT CHARACTER SET utf8mb4"
        )
        conn.commit()
        logger.info(f"数据库 {settings.DB_NAME} 创建成功（或已存在）")
    finally:
        cursor.close()
        conn.close()


def create_tables():
    """创建所有表（如果不存在）。"""
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # 建 ad_data 表（列名和模拟数据CSV一致）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ad_data (
                id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
                date DATE NOT NULL COMMENT '日期',
                campaign VARCHAR(100) COMMENT '计划名称',
                channel VARCHAR(50) COMMENT '渠道',
                region VARCHAR(50) COMMENT '地区',
                device VARCHAR(20) COMMENT '设备',
                impressions INT DEFAULT 0 COMMENT '曝光量',
                clicks INT DEFAULT 0 COMMENT '点击量',
                conversions INT DEFAULT 0 COMMENT '转化量',
                cost FLOAT DEFAULT 0 COMMENT '花费',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                INDEX idx_date (date),
                INDEX idx_channel (channel),
                INDEX idx_campaign (campaign)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='广告投放数据表'
        """)

        # 建 campaigns 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INT PRIMARY KEY COMMENT '计划ID',
                name VARCHAR(100) NOT NULL COMMENT '计划名称',
                channel VARCHAR(50) NOT NULL COMMENT '渠道',
                owner VARCHAR(50) COMMENT '负责人',
                status VARCHAR(20) DEFAULT 'active' COMMENT '状态',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='计划表'
        """)

        conn.commit()
        logger.info("所有表创建成功（或已存在）")
    finally:
        cursor.close()
        conn.close()


def insert_ad_data(df, if_exists='append'):
    """
    把 DataFrame 批量写入 ad_data 表。

    参数:
        df: 要写入的 DataFrame，列名要和表的列名对应
        if_exists: 表已经存在时怎么办
            - 'fail': 报错
            - 'replace': 删除旧表重建（危险！会删掉原有数据）
            - 'append': 追加（最常用）
    """
    engine = create_engine(settings.database_url)

    df.to_sql(
        name='ad_data',
        con=engine,
        if_exists=if_exists,
        index=False,
        chunksize=1000,
    )

    logger.info(f"成功写入 {len(df)} 条数据到 ad_data 表")


def insert_campaigns(df, if_exists='append'):
    """把 DataFrame 批量写入 campaigns 表。"""
    engine = create_engine(settings.database_url)

    df.to_sql(
        name='campaigns',
        con=engine,
        if_exists=if_exists,
        index=False,
        chunksize=1000,
    )

    logger.info(f"成功写入 {len(df)} 条数据到 campaigns 表")


def query_to_df(sql):
    """
    执行 SQL 查询，返回 DataFrame。

    参数:
        sql: 要执行的 SQL 查询语句（SELECT）

    返回:
        DataFrame: 查询结果
    """
    engine = create_engine(settings.database_url)
    df = pd.read_sql(sql, con=engine)
    return df


def execute_sql(sql):
    """
    执行无返回结果的 SQL（INSERT/UPDATE/DELETE/CREATE等）。

    参数:
        sql: 要执行的 SQL 语句
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        logger.info("SQL执行成功")
    finally:
        cursor.close()
        conn.close()


def init_database():
    """初始化数据库：创建数据库 + 创建所有表。"""
    create_database()
    create_tables()
    logger.info("数据库初始化完成")
