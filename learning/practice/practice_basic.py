from itertools import product

from practice.my_first_script import result

name = 'yangzenghao'
age = 22
print(f"我叫{name}，今年{age}岁")

numebers = [1, 2, 3, 4, 5]
a = 0
for i in numebers:
    print(i)
    a = i+a

print(a)

def calculate_bmi(weight, height):
    BMI = weight*height
    return BMI
A = calculate_bmi(60, 1.7)
B = calculate_bmi(80, 1.8)

print(A)
print(f"{B:.2f}")
print(f"{B:.2%}")
print(f"{B:<10}")
print(f"{B:=^20}")

cost = 12345.6789
ctr = 0.028456
cvr = 0.01234
roi = 2.3456
growth = -0.08765

print(f"{cost:,.2f}")
print(f"{ctr:.2%}")
print(f"{roi:.1f}")
print(f"{growth:+.1%}")

raw_name = "  Summer_Sale_2026  "
result1 = raw_name.strip()
result2 = raw_name.replace("_", "")
result3 = raw_name.lower()
result4 = raw_name.split("_")

student = {
    "name": "yangzenghao",
    "age": 22,
    "score": 100,
    "gender": "man"
}

print(student["name"])
print(student.get("name"))
del student["age"]
score = student.pop("score")

# 列表套字典（最常用！一条记录是一个字典，多条记录放在列表里）
students = [
    {"name": "小明", "score": 85},
    {"name": "小红", "score": 92},
    {"name": "小刚", "score": 78}
]

print(students[0]["name"])   # 小明（先取列表第0个，再取字典的name）

product = {
    "name": "iphone 15",
    "price": 5999,
    "stock": 100
}
print(product["name"])
product["color"] = "黑色"
for key, value in product.items():
    print(f"{key}: {value}")

students = [
    {"name": "小明", "score": 85},
    {"name": "小红", "score": 92},
    {"name": "小刚", "score": 78}
]
total_score = 0
for i in students:
    print(f"{i['name']}: {i['score']}分")
    total_score += i['score']
avg = total_score/len(students)

def analyze_students(students):
    total = len(students)
    total_score = 0
    pass_count = 0
    best_score = 0

    for student in students:
        score = student["score"]
        total_score += score
        if score >= 60:
            pass_count += 1
        if score > best_score:
            best_score = score
            best_name = studen["name"]
    fail_count = total - pass_count
    avg_score = round(total_score/total, 1)
    return {
        "total": total,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "avg_score": avg_score,
        "best_student": best_name
    }