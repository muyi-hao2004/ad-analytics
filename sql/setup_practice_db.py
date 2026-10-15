"""重建 SQL 练习库 sql_practice：正确字符集导入 + 建维度表
数据源：data/raw/sample_ad_data.csv
产出：
  - ads 表（事实表）：1041 行，日期/渠道/活动/地区/设备 + 4个指标
  - campaigns 表（维度表）：9 个活动，含 owner/status/budget
用途：练多表 JOIN、GROUP BY 聚合、CASE WHEN、窗口函数
"""
import csv
import pymysql

CONN = dict(host="127.0.0.1", user="root", password="123456",
            charset="utf8mb4", database="sql_practice")

# 1. 读 CSV
rows = []
with open("data/raw/sample_ad_data.csv", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

# 2. 连库（不指定 database，先重建库）
conn = pymysql.connect(host="127.0.0.1", user="root", password="123456",
                       charset="utf8mb4")
cur = conn.cursor()

# 重建数据库（清掉旧表和外键）
cur.execute("DROP DATABASE IF EXISTS sql_practice")
cur.execute("CREATE DATABASE sql_practice DEFAULT CHARSET utf8mb4")
cur.execute("USE sql_practice")

# 3. 建事实表 ads
cur.execute("DROP TABLE IF EXISTS ads")
cur.execute("""
CREATE TABLE ads (
  id INT AUTO_INCREMENT PRIMARY KEY,
  date DATE NOT NULL,
  channel VARCHAR(50),
  campaign VARCHAR(100),
  region VARCHAR(50),
  device VARCHAR(20),
  impressions INT DEFAULT 0,
  clicks INT DEFAULT 0,
  conversions INT DEFAULT 0,
  cost FLOAT DEFAULT 0,
  INDEX idx_channel(channel),
  INDEX idx_campaign(campaign)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""")

# 4. 导入事实表
def to_int(x):
    return int(x) if x not in ("", None) else 0

def to_float(x):
    return float(x) if x not in ("", None) else 0.0

for r in rows:
    cur.execute(
        "INSERT INTO ads(date,channel,campaign,region,device,impressions,clicks,conversions,cost) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (r["date"], r["channel"], r["campaign"], r["region"], r["device"],
         to_int(r["impressions"]), to_int(r["clicks"]), to_int(r["conversions"]), to_float(r["cost"]))
    )

# 5. 建维度表 campaigns
cur.execute("""
CREATE TABLE campaigns (
  id INT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  channel VARCHAR(50),
  owner VARCHAR(50),
  status VARCHAR(20) DEFAULT 'active',
  budget FLOAT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""")

# 9 个活动，覆盖事实表里所有 campaign
campaigns = [
    (1, "双11预热_品牌", "YouTube", "张三", "active", 500000),
    (2, "日常投放_长尾词", "Google", "李四", "active", 800000),
    (3, "双11正式_爆款", "Google", "李四", "paused", 1200000),
    (4, "618大促_冲量", "YouTube", "王五", "active", 600000),
    (5, "夏季清仓_品牌词", "Facebook", "赵六", "active", 300000),
    (6, "夏季清仓_通用词", "Facebook", "赵六", "paused", 400000),
    (7, "运营活动_拉新", "Instagram", "张三", "active", 200000),
    (8, "商品推广_兴趣定向", "Instagram", "王五", "active", 350000),
    (9, "日常投放_爆款词", "TikTok", "李四", "active", 700000),
]
for c in campaigns:
    cur.execute(
        "INSERT INTO campaigns(id,name,channel,owner,status,budget) VALUES(%s,%s,%s,%s,%s,%s)",
        c
    )

conn.commit()

# 6. 验证
cur.execute("SELECT COUNT(*) FROM ads")
print("ads 行数:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM campaigns")
print("campaigns 行数:", cur.fetchone()[0])
cur.execute("SELECT name, channel, owner, status FROM campaigns ORDER BY id")
print("\ncampaigns 维度表内容：")
for row in cur.fetchall():
    print(" ", row)

cur.close()
conn.close()
print("\n✅ 数据库 sql_practice 重建完成，中文正常")
