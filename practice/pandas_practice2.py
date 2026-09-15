import pandas as pd
import numpy as np

# 练习数据1：学生成绩表（故意加了缺失值和重复行，方便练习清洗）
scores_data = {
    "name": ["小明", "小红", "小刚", "小丽", "小强", "小美", "小华", "小芳", "小军", "小燕", "小明"],  # 最后一个小明是重复行
    "class": ["一班", "一班", "一班", "二班", "二班", "二班", "三班", "三班", "三班", "三班", "一班"],
    "math": [85, 92, 78, 88, np.nan, 95, 82, 90, 70, 87, 85],  # 小强的数学是缺失值
    "english": [78, 85, np.nan, 82, 75, 88, 79, 92, 68, 84, 78],  # 小刚的英语是缺失值
    "chinese": [88, 90, 82, np.nan, 79, 93, 80, 88, 72, 86, 88],  # 小丽的语文是缺失值
}

df_scores = pd.DataFrame(scores_data)
print("=== 成绩表（原始，有缺失值和重复行）===")
print(df_scores)
print(f"\n形状：{df_scores.shape}")

# 练习数据2：学生信息表（用来练习 merge 合并）
info_data = {
    "name": ["小明", "小红", "小刚", "小丽", "小强", "小美", "小华", "小芳", "小军", "小燕"],
    "student_id": ["2024001", "2024002", "2024003", "2024004", "2024005", "2024006", "2024007", "2024008", "2024009", "2024010"],
    "major": ["计算机", "数学", "物理", "计算机", "数学", "英语", "物理", "计算机", "数学", "英语"],
    "enroll_date": ["2024-09-01", "2024-09-01", "2024-09-02", "2024-09-01", "2024-09-03",
                    "2024-09-01", "2024-09-02", "2024-09-01", "2024-09-04", "2024-09-01"],
}

df_info = pd.DataFrame(info_data)
print("\n=== 学生信息表 ===")
print(df_info)

print(df_scores.isna().sum())

df_scores["math"] = df_scores["math"].fillna(df_scores["math"].mean())
df_scores.dropna(subset=["english"], inplace=True)
df_scores[df_scores.duplicated()]
df_scores.drop_duplicates(inplace=True)
df_info["enroll_date"] = pd.to_datetime(df_info["enroll_date"])

df1 = df_scores.groupby("class").agg(
    班级总人数 = ("name", "count"),
    数学及格人数 = ("math", lambda x: (x >= 60).sum()),
)
print(df1)
df1["及格率"] = df1["数学及格人数"]/df1["班级总人数"].round(2)
print(df1)

print(df_scores.sort_values(by="math", ascending=False).groupby("class").first())

def get_level(total):
    if total >= 270:
        return "学霸"
    elif total >= 240:
        return "优秀"
    elif total >= 210:
        return "良好"
    else:
        return "加油"

df_scores["total"] = df_scores["math"] + df_scores["english"] + df_scores["chinese"]
df_scores["level"] = df_scores["total"].apply(get_level)

print(df_scores)

df2 = pd.merge(df_scores, df_info, on="name", how="outer")
print(df2[df2["major"] == "计算机"])
print(df2.sort_values(by="math", ascending=False))
print(df2[["name", "class", "major", "math", "english", "chinese"]])
