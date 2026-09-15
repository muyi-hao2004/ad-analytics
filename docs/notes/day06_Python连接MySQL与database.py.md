# Day6：Python 连接 MySQL 与 database.py

## 一、database.py 的作用

连接 MySQL 数据库 → 建库建表 → 把清洗好的数据写入 → 查询数据做分析。
和在 DataGrip 里操作一样，只不过用 Python 代码自动完成。

## 二、完整数据流

```
原始数据（CSV/API）→ Python读取+清洗 → 写入MySQL数据库 → 需要时SQL查询 → pandas分析/报表
```

数据库是数据的"家"，清洗好的数据存进去，需要时再查出来，不是每次都重新读CSV。

## 三、为什么要把数据写入数据库

1. **持久化**：数据长期保存，新数据自动追加，历史数据不丢失
2. **查询快**：有索引优化，几百万条数据也能秒查（pandas读大文件会卡）
3. **多人共享**：多个系统/同事可以同时连数据库查询，不用传来传去
4. **复杂查询**：多表JOIN、窗口函数、子查询，SQL比pandas更直观更快

## 四、简化版 database.py（不用ORM，只用函数+SQL）

### 1. get_connection() — 连接数据库

```python
import pymysql
from .config import settings

def get_connection():
    conn = pymysql.connect(
        host=settings.DB_HOST,        # 主机地址 localhost
        port=settings.DB_PORT,        # 端口 3306
        user=settings.DB_USER,        # 用户名 root
        password=settings.DB_PASSWORD, # 密码
        database=settings.DB_NAME,    # 数据库名 ad_analytics
        charset='utf8mb4',            # 字符集，支持中文
        cursorclass=pymysql.cursors.DictCursor,  # 查询结果返回字典格式
    )
    return conn
```

- 参数和 DataGrip 里填的连接信息一模一样
- 用完一定要关闭连接：`conn.close()`
- 游标 cursor 用来执行SQL：`cursor.execute(sql)`，`cursor.fetchall()` 获取结果

### 2. create_database() — 创建数据库

- 创建数据库时不能指定 `database` 参数（因为数据库还不存在）
- SQL：`CREATE DATABASE IF NOT EXISTS 库名 DEFAULT CHARACTER SET utf8mb4`
- `IF NOT EXISTS`：已存在就不创建，不报错
- 修改操作需要 `conn.commit()` 提交
- 用 `try...finally` 保证连接一定关闭

### 3. create_tables() — 建表

SQL：`CREATE TABLE IF NOT EXISTS 表名 (列定义) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`

**列定义格式：** `列名 类型 约束 COMMENT '注释'`

**MySQL 常用数据类型：**

| 类型 | 说明 | 例子 |
|---|---|---|
| INT | 整数 | id, clicks |
| BIGINT | 大整数 | 超大数量 |
| FLOAT/DOUBLE | 浮点数 | cost, ctr |
| VARCHAR(长度) | 字符串 | name, channel |
| TEXT | 长文本 | 备注 |
| DATE | 日期 | date |
| DATETIME | 日期时间 | created_at |
| BOOLEAN | 布尔值 | is_active |

**常用约束：**

| 约束 | 说明 |
|---|---|
| PRIMARY KEY | 主键，唯一标识一行 |
| AUTO_INCREMENT | 自增，插入时自动+1 |
| NOT NULL | 不允许为空 |
| DEFAULT 值 | 默认值 |
| UNIQUE | 值唯一 |

**索引：** `INDEX 索引名 (列名)`，经常查询/筛选/JOIN的列要建索引（date、channel、campaign_id）

### 4. insert_ad_data(df) — 把DataFrame写入数据库

用 pandas 的 `df.to_sql()` 方法：

```python
df.to_sql(
    name='ad_data',        # 表名
    con=engine,             # SQLAlchemy引擎（pandas需要这个，不能直接用pymysql连接）
    if_exists='append',     # 表存在时怎么办
    index=False,            # 不把DataFrame索引写入数据库
    chunksize=1000,         # 每批写入1000行，防止内存溢出
)
```

**if_exists 三个选项：**
- `'fail'`：表存在就报错
- `'replace'`：删除旧表重建（危险，会删掉原有数据）
- `'append'`：追加（最常用，新数据加到原有数据后面）

### 5. query_to_df(sql) — 查询数据返回DataFrame

用 pandas 的 `pd.read_sql(sql, con=engine)`：

```python
df = pd.read_sql("SELECT * FROM ad_data WHERE cost > 1000", con=engine)
```

- 在 DataGrip 里写的 SQL 直接复制过来就能用
- 查询结果直接变成 DataFrame，可以用 pandas 继续分析

## 五、关键注意事项

1. **用完一定要关闭连接**：`cursor.close()` + `conn.close()`，用 `try...finally` 保证
2. **修改操作要 commit**：`CREATE`、`INSERT`、`UPDATE`、`DELETE` 需要 `conn.commit()`，`SELECT` 不需要
3. **pandas to_sql/read_sql 需要 SQLAlchemy 引擎**，不能直接用 pymysql 连接
4. **密码不写死在代码里**，存在 `.env` 文件，通过 `config.py` 读取
5. **建表加 IF NOT EXISTS**，重复运行不会报错
6. **写入用 append 模式**，不要用 replace（会删掉原有数据）

## 六、本机 MySQL 信息

- 版本：MySQL 8.0.46
- 主机：localhost
- 端口：3306
- 用户名：root
- 密码：123456（已写入 .env）
- 项目数据库：ad_analytics
- 练习数据库：sql_practice

## 下次待做

1. 安装 pymysql：`pip install pymysql`
2. 把简化版 database.py 写到项目里
3. 运行测试：创建数据库 → 建表 → 把模拟数据写进去 → 查询出来验证
4. 把 data_loader + cleaning + database 串起来，跑通完整流程
