import pandas as pd

df = pd.read_csv(
    '../data/raw/sample_ad_data.csv',
    parse_dates=['data'],
    encoding='utf-8',
)

print("数据前5行")
print(df.head())
print()

print("数据基本信息：")
print(df.info)
print()

print(f"数据形状:{df.shape[0]} 行，{df.shape[1]}列")
