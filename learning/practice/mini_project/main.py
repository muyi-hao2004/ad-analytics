# from mini_analytics.loader import load_data
# from practice.my_first_script import result
#
# result = load_data()
# print(result)

# def multiply(a,b):
#     result = {a} * {b}
#     return result
#
# x = multiply(3,4)
# print(x)

import pandas as pd
import numpy as np

# 创建一个有缺失值的DataFrame
df = pd.DataFrame({
    'name': ['小明', '小红', None],
    'age': [20, None, 25],
    'score': [90, 85, None],
})

# 检测缺失值
print(df.isna())

print(df.isna().sum())

print(df.isna().mean()*100)

print(df[df.isna().any(axis=1)])

df.dropna(axis=0, how='any', thresh=None, subset=None, inplace=False)

df.fillna(value=None, method=None, axis=None, inplace=None, limit=None)

#pandas的表格教dataframe 取一列用df['name']
df[['name', 'age']]
df.loc[1]#取一行

df[df['age'] > 21]
df[df['score'] >= 90]
df[df['name'] == '小明']

# 且（&）：age大于20 且 score大于90
df[(df['age'] > 20) & (df['score'] > 90)]

# 或（|）：name是"小明" 或 name是"小红"
df[(df['name'] == '小明') | (df['name'] == '小红')]

# 非（~）：age不等于20
df[~(df['age'] == 20)]

df.loc[0, 'name']
df.iloc[0,0]

df.loc[0, 'score'] = 100

df.duplicated(subset=None, keep='first')
import pandas as pd

df = pd.DataFrame({
    'name': ['小明', '小红', '小明', '小刚', '小红'],
    'age': [20, 21, 20, 22, 21],
    'score': [90, 85, 90, 95, 88],  # 注意：第5行score不一样
})

print("原始数据：")
print(df)
print()

# 例子1：检测完全重复的行（所有列都一样）
print("完全重复的行（keep='first'，第一次出现不算）：")
print(df.duplicated())
print()

# 例子2：只看name和age列是否重复（不管score）
print("只看name和age列是否重复：")
print(df.duplicated(subset=['name', 'age']))
print()

# 例子3：keep='last'，最后一次出现不算重复
print("keep='last'，最后一次出现不算重复：")
print(df.duplicated(keep='last'))
print()

# 例子4：keep=False，所有重复的都算True
print("keep=False，所有重复的都算True：")
print(df.duplicated(keep=False))

df.drop_duplicates(subset=None, keep='first', inplace=False, ignore_index=False)

import pandas as pd

df = pd.DataFrame({
    'name': ['小明', '小红', '小明', '小刚', '小红'],
    'age': [20, 21, 20, 22, 21],
    'score': [90, 85, 90, 95, 88],
})

print("原始数据：")
print(df)
print()

# 例子1：删除完全重复的行（保留第一条）
df1 = df.drop_duplicates()
print("删除完全重复的行（保留第一条）：")
print(df1)
print()

# 例子2：只按name和age列去重（不管score）
df2 = df.drop_duplicates(subset=['name', 'age'])
print("只按name和age列去重：")
print(df2)
print()

# 例子3：保留最后一条
df3 = df.drop_duplicates(subset=['name', 'age'], keep='last')
print("按name和age去重，保留最后一条：")
print(df3)
print()

# 例子4：删除所有重复的（一条都不保留）
df4 = df.drop_duplicates(subset=['name', 'age'], keep=False)
print("按name和age去重，删除所有重复的：")
print(df4)
print()

# 例子5：重置索引
df5 = df.drop_duplicates(ignore_index=True)
print("删除重复行并重置索引：")
print(df5)

pd.to_datetime(arg, errors='raise', format=None, unit=None)

import pandas as pd

# 例子1：转换一列日期
df = pd.DataFrame({
    'date': ['2026-08-01', '2026-08-02', '2026-08-03'],
    'value': [100, 200, 300],
})

print("转换前：")
print(df['date'].dtype)   # object（字符串）

df['date'] = pd.to_datetime(df['date'])

print("转换后：")
print(df['date'].dtype)   # datetime64[ns]（日期类型）
print()

# 转换后就能做日期操作了
print("提取月份：")
print(df['date'].dt.month)
print()
print("提取星期几：")
print(df['date'].dt.dayofweek)
print()

# 例子2：转换失败时用coerce（设为NaT）
dates = pd.Series(['2026-08-01', '无效日期', '2026-08-03'])
result = pd.to_datetime(dates, errors='coerce')
print("errors='coerce'，无效日期变成NaT：")
print(result)
print()

# 例子3：指定格式（更快更准确）
dates2 = pd.Series(['01/08/2026', '02/08/2026', '03/08/2026'])
result2 = pd.to_datetime(dates2, format='%d/%m/%Y')
print("指定格式 %d/%m/%Y：")
print(result2)
print()

# 例子4：时间戳转日期（秒级）
timestamps = pd.Series([1754006400, 1754092800, 1754179200])
result3 = pd.to_datetime(timestamps, unit='s')
print("时间戳（秒）转日期：")
print(result3)

pd.to_numeric(arg, errors='raise', downcast=None)
import pandas as pd
import numpy as np

# 例子1：数字列里混了字符串
s = pd.Series(['100', '200', '无效', '300', None])
print("原始数据：")
print(s)
print(f"类型: {s.dtype}")
print()

# 用coerce，转换失败的变成NaN
result = pd.to_numeric(s, errors='coerce')
print("errors='coerce'，无效值变成NaN：")
print(result)
print(f"类型: {result.dtype}")
print()

# 例子2：errors='ignore'，转换失败的保持原样
result2 = pd.to_numeric(s, errors='ignore')
print("errors='ignore'，无效值保持原样：")
print(result2)
print()

# 例子3：在DataFrame里转换某列
df = pd.DataFrame({
    'cost': ['100.5', '200.3', '无效', '300.0'],
    'clicks': ['10', '20', '30', '40'],
})
print("原始DataFrame：")
print(df)
print()

df['cost'] = pd.to_numeric(df['cost'], errors='coerce')
df['clicks'] = pd.to_numeric(df['clicks'], errors='coerce')
print("转换后：")
print(df)
print(f"cost类型: {df['cost'].dtype}")
print(f"clicks类型: {df['clicks'].dtype}")

df.astype(dtype, errors='raise')
import pandas as pd

df = pd.DataFrame({
    'age': ['20', '21', '22'],      # 字符串数字
    'cost': [100.5, 200.3, 300.0],  # 浮点数
    'channel': ['Facebook', 'Google', 'Facebook'],  # 分类列
    'active': [True, False, True],   # 布尔值
})

print("原始类型：")
print(df.dtypes)
print()

# 例子1：把age列转成整数
df['age'] = df['age'].astype(int)
print("age转成int后：")
print(df['age'].dtype)
print()

# 例子2：把cost列转成整数（会截断小数部分！）
df['cost_int'] = df['cost'].astype(int)
print("cost转成int（截断小数）：")
print(df[['cost', 'cost_int']])
print()

# 例子3：把channel列转成category（省内存）
df['channel'] = df['channel'].astype('category')
print("channel转成category后：")
print(df['channel'].dtype)
print()

# 例子4：用字典一次性转换多列
df2 = df.astype({
    'age': float,
    'cost': str,
    'active': str,
})
print("用字典一次性转换多列后：")
print(df2.dtypes)

import pandas as pd

df = pd.DataFrame({
    'Date': ['2026-08-01', '2026-08-02'],
    'Click Count': [100, 200],
    'Impression': [10000, 20000],
    '花费': [500, 1000],
})

print("原始列名：")
print(df.columns.tolist())
print()

# 例子1：重命名指定列
df1 = df.rename(columns={
    'Date': 'date',
    'Click Count': 'clicks',
    'Impression': 'impressions',
    '花费': 'cost',
})
print("重命名后列名：")
print(df1.columns.tolist())
print()

# 例子2：只重命名部分列
df2 = df.rename(columns={'花费': 'cost'})
print("只重命名'花费'列：")
print(df2.columns.tolist())

import pandas as pd

df = pd.DataFrame({
    ' Date ': ['2026-08-01', '2026-08-02'],
    'Click Count': [100, 200],
    'Ad Cost': [500, 1000],
})

print("原始列名：")
print(df.columns.tolist())
print()

# 一步搞定：去空格 → 转小写 → 空格改下划线
df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')

print("标准化后列名：")
print(df.columns.tolist())

df.sort_values(by, axis=0, ascending=True, inplace=False, na_position='last', ignore_index=False)

import pandas as pd

df = pd.DataFrame({
    'name': ['小明', '小红', '小刚', '小李'],
    'age': [20, 21, 22, 20],
    'score': [90, 85, 95, 88],
})

print("原始数据：")
print(df)
print()

# 例子1：按score升序（从小到大）
df1 = df.sort_values(by='score')
print("按score升序：")
print(df1)
print()

# 例子2：按score降序（从大到小）
df2 = df.sort_values(by='score', ascending=False)
print("按score降序：")
print(df2)
print()

# 例子3：按多列排序（先按age升序，age相同按score降序）
df3 = df.sort_values(by=['age', 'score'], ascending=[True, False])
print("按age升序、score降序：")
print(df3)
print()

# 例子4：排序后重置索引
df4 = df.sort_values(by='score', ascending=False, ignore_index=True)
print("按score降序并重置索引：")
print(df4)

df.reset_index(level=None, drop=False, inplace=False)

df.groupby(by=None, axis=0, as_index=True, sort=True, dropna=True)
import pandas as pd

df = pd.DataFrame({
    'channel': ['Facebook', 'Google', 'Facebook', 'Google', 'TikTok'],
    'date': ['2026-08-01', '2026-08-01', '2026-08-02', '2026-08-02', '2026-08-01'],
    'impressions': [10000, 20000, 15000, 25000, 30000],
    'clicks': [200, 500, 300, 600, 800],
    'cost': [500, 1200, 700, 1500, 1800],
})

print("原始数据：")
print(df)
print()

# 按渠道分组，对所有数值列求和
result = df.groupby('channel').sum()
print("按渠道分组求和：")
print(result)

result = df.groupby('channel', as_index=False).sum()
print("按渠道分组求和（as_index=False）：")
print(result)

# 按渠道分组：曝光、点击、花费求和，同时算有多少天（date列计数）
result = df.groupby('channel', as_index=False).agg({
    'impressions': 'sum',
    'clicks': 'sum',
    'cost': 'sum',
    'date': 'nunique',   # 去重计数，算有多少天
})
print("不同列不同聚合方式：")
print(result)

# 按渠道分组：cost同时求和、求平均、求最大、求最小
result = df.groupby('channel', as_index=False).agg({
    'cost': ['sum', 'mean', 'max', 'min'],
})
print("同一列多种聚合方式：")
print(result)

result = df.groupby('channel', as_index=False).agg(
    total_impressions=('impressions', 'sum'),   # 曝光求和，列名叫total_impressions
    total_clicks=('clicks', 'sum'),             # 点击求和，列名叫total_clicks
    total_cost=('cost', 'sum'),                 # 花费求和，列名叫total_cost
    avg_cpc=('cost', 'mean'),                   # 花费求平均，列名叫avg_cpc
    day_count=('date', 'nunique'),              # 日期去重计数，列名叫day_count
)
print("命名聚合：")
print(result)

# 第一步：按渠道分组求和
grouped = df.groupby('channel', as_index=False).agg({
    'impressions': 'sum',
    'clicks': 'sum',
    'cost': 'sum',
})

# 第二步：计算衍生指标
grouped['ctr'] = grouped['clicks'] / grouped['impressions']
grouped['cpc'] = grouped['cost'] / grouped['clicks']

print("分组后计算CTR、CPC：")
print(grouped)

#np.where(条件, 条件成立时的值, 条件不成立时的值)
import numpy as np

# 如果曝光>0，CTR=点击/曝光；否则CTR=0
grouped['ctr'] = np.where(
    grouped['impressions'] > 0,          # 条件
    grouped['clicks'] / grouped['impressions'],  # 条件成立时
    0                                      # 条件不成立时
)

df['列'].shift(periods=1, freq=None, axis=0)
# 按日期分组
daily = df.groupby('date', as_index=False).agg({
    'cost': 'sum',
    'clicks': 'sum',
})

# 按日期排序（必须排序，否则shift不对！）
daily = daily.sort_values('date').reset_index(drop=True)

# 前一天的花费
daily['cost_prev'] = daily['cost'].shift(1)

# 环比变化率 = (今天 - 昨天) / 昨天
daily['cost_daily_change'] = (daily['cost'] - daily['cost_prev']) / daily['cost_prev']

print("日环比计算：")
print(daily)

# 按日期分组并排序
daily = df.groupby('date', as_index=False)['cost'].sum()
daily = daily.sort_values('date').reset_index(drop=True)

# 7天移动平均（min_periods=1表示前几天也开始算，用现有数据的平均）
daily['cost_ma7'] = daily['cost'].rolling(window=7, min_periods=1).mean()

print("7天移动平均：")
print(daily)

grouped = df.groupby('channel', as_index=False)['cost'].sum()

# 按花费从高到低排名
grouped['rank'] = grouped['cost'].rank(ascending=False, method='min').astype(int)

# 按排名排序
grouped = grouped.sort_values('rank')

print("按花费排名：")
print(grouped)
