import pandas as pd
import numpy as np

# 练习数据：学生成绩表（10个学生）
data = {
    "name": ["小明", "小红", "小刚", "小丽", "小强", "小美", "小华", "小芳", "小军", "小燕"],
    "class": ["一班", "一班", "一班", "二班", "二班", "二班", "三班", "三班", "三班", "三班"],
    "gender": ["男", "女", "男", "女", "男", "女", "男", "女", "男", "女"],
    "math": [85, 92, 78, 88, 76, 95, 82, 90, 70, 87],
    "english": [78, 85, 90, 82, 75, 88, 79, 92, 68, 84],
    "chinese": [88, 90, 82, 85, 79, 93, 80, 88, 72, 86],
}

df = pd.DataFrame(data)
print(df)

# df1 = df.head(5)
# print(df1)
# df1 = df.tail(5)
# print(df1)
#
# df1 = df.shape
# print(df1)
#
# df1 = df.describe()
# print(df1)
#
# print(df["class"].value_counts())
#
# print(df["name"])
# df[["name", "math"]]   # 取 name 和 math 两列，返回的是 DataFrame（二维）
#
# print(df["math"].mean())
#
# print(df.iloc[0:2])
#
# print(df[df["math"]>80])
#
# print(df[(df["math"]>90) | (df["chinese"]>90)])
#
# print(df.sort_values(by="math"))
#
# df["total"] = df["math"] + df["english"] + df["chinese"]
#
# df["grade"] = np.where(df["average"] >= 90, "a",
#                       df["average"]>= 80, "b",
#                        df["average"]>= 70, "c", "d")
#
# # 定义一个函数，根据分数返回等级
# def get_grade(score):
#     if score >= 90:
#         return "优秀"
#     elif score >= 80:
#         return "良好"
#     elif score >= 70:
#         return "中等"
#     else:
#         return "及格"
#
# # 把这个函数应用到 average 列的每一行
# df["grade"] = df["average"].apply(get_grade)
#
# df.groupby("class")["math"].mean()
#
# df.groupby("class").agg(
#     人数=("name", "count"),
#     数学平均分=("math", "mean"),
# )
#
# df.isna()
# df.isna().sum()
# df["math"].isna().sum()
#
# df.dropna()             # 删除任何包含缺失值的行
# df.dropna(subset=["math"])  # 只删除math列有缺失值的行
# df.dropna(how="all")    # 只删除整行都是缺失值的行
#
# df.fillna(0)
# df["math"].fillna(df["math"].mean())
# df.fillna({"math": 0, "english": 60})

# pd.merge(df1, df2, on="student_id", how="inner")
# pd.concat([df1, df2], axis=0)


print(df.head())
print(df.shape)
print(df.dtypes)
print(df.describe())

