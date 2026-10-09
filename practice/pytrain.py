# result = [x for x in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] if x % 2 == 0]
# print(result)
#
# d = {"a": 1, "b": 2, "c": 3}
# new_d = {}
# for key, value in d.items():
#     new_d[value] = key
#
# print(new_d)
#
# new_D = {value: key for key, value in d.items()}
#
# def find_most_frequent(nums):
#     count = {}
#     for num in nums:
#         if num in count:
#             count[num] += 1
#         else:
#             count[num] = 1
#     max_count = 0
#     most_num = None
#     for num, cnt in count.items():
#         if cnt > max_count:
#             max_count = cnt
#             most_num = num
#     return most_num
#
# def timer(func):
#     def wrapper():
#         start = time.time()
#         func()
#         end = time.time()
#         print(f"运行时间{end-start}秒")
#     return wrapper()
#
# result1 = [x*2 for x in [1, 2, 3, 4, 5, 6]]
# print(result)
#
# scores = {"张三": 85, "李四": 92, "王五": 78, "赵六": 95}
# def most_scores(scores):
#     score = 0
#     for key, value in scores.items():
#         if value > score:
#             score = value
#             person = key
#     return person
#
# def find(scores):
#     my_list = []
#     for key, value in scores.items():
#         if value > 90:
#             my_list.append(key)
#     return my_list
#
# def my_avg(list):
#     my_sum = 0
#     for i in list:
#         my_sum += i
#     avg = my_sum/len(list)
#     return avg
#
# data = [("张三", 85), ("李四", 92), ("王五", 78)]
# print(sorted(data, key=lambda x: x[2]))
# print(sorted(data, key=lambda x: len(x)))
#
# scores = {"张三": 85, "李四": 92, "王五": 78}
# items = list(scores.items())
#
# ads = ["Facebook", "Google", "TikTok"]
# for i,ad in enumerate(ads):
#     print(i,ad)
#
# nums = [1, 2, 3, 4, 5, 6]
# result = list(filter(lambda x:x % 2 == 0, nums))
# result1 = list(map(lambda x:x * 2, nums))
#
# s = "Hello World"
# s.upper()
# s.lower()
# s.title()
#
# s.strip()
# s.lstrip()
# s.rstrip()
#
# s = "Facebook,Google,Tiktok"
# s.split(",")
# channels = ["Facebook", "Google", "Tiktok"]
# ",".join(channels)
#
# s = "今天CPA是100"
# s.replace("CPA", "ROI")   # "今天ROI是100"
#
# s = "data_2026.csv"
#
# s.startswith("data")   # True
# s.endswith(".csv")     # True
#
# s = "Facebook广告效果很好"
#
# s.find("广告")   # 8（位置），找不到返回-1
# "广告" in s      # True
#
# d = {"张三": 85, "李四": 92}
#
# d["张三"]          # 85
# d.get("张三")      # 85
# d.get("王五", 0)   # 0（不存在返回默认值，不报错）
# d = {"张三": 85}
#
# d["王五"] = 78    # 添加
# d["张三"] = 90    # 修改
# d1 = {"a": 1, "b": 2}
# d2 = {"c": 3, "d": 4}
#
# # Python 3.9+
# d = d1 | d2  # {'a':1, 'b':2, 'c':3, 'd':4}
#
# # 通用写法
# d = {**d1, **d2}
# lst = [1, 2, 3]
#
# lst.append(4)      # 末尾加一个：[1, 2, 3, 4]
# lst.extend([5, 6])  # 末尾加多个：[1, 2, 3, 4, 5, 6]
# lst.insert(0, 0)   # 指定位置插入：[0, 1, 2, 3, ...]
# lst = [1, 2, 3, 4, 5]
#
# lst.pop()       # 删除最后一个，返回它
# lst.pop(0)      # 删除第0个
# lst.remove(3)   # 删除值为3的元素
# lst = [3, 1, 2]
#
# lst.sort()          # 升序，直接改原列表
# lst.sort(reverse=True)  # 降序
# lst.reverse()      # 反转
#
# s = "data_2026.csv"
#
# s.startswith("data")   # True
# s.endswith(".csv")     # True

# channels = ["  facebook ", "GOOGLE", " tiktok", "Instagram "]
# channels[0] = channels[0].strip().title()
# channels[1] = channels[1].title()
# channels[2] = channels[2].strip().title()
# channels[3] = channels[3].strip().title()
# print(channels)
#
# channels = ["Facebook", "Google", "Facebook", "TikTok", "Google", "Facebook"]
# def count_channel(list):
#     count = {}
#     for i in list:
#         count[i] = count.get(i, 0) + 1
#     return count
#
# ads = [
#     {"channel": "Facebook", "cost": 1000, "conversions": 10},
#     {"channel": "Google", "cost": 2000, "conversions": 20},
#     {"channel": "TikTok", "cost": 500, "conversions": 5},
# ]
# for ad in ads:
#     ad["cpa"] = ad["cost"] / ad["conversions"]
# result = sorted(ads, key=lambda x: x["cpa"], reverse=True)
#
# filename = "ad_data_2026_09_27_final.csv"
# filename.startswith("ad_data")
# filename.endswith(".csv")
# for str in filename.split("_"):
#     print(str)

# raw_data = "Facebook,1000,10;Google,2000,20;TikTok,500,5"
# every_data = raw_data.split(";")
# channel = []
# cost = []
# conversions = []
# for i in every_data:
#     channel.append(i.split(",")[0])
#     cost.append(i.split(",")[1])
#     conversions.append(i.split(",")[2])
# data = []
# data.append({"channel" : channel[0]})
# data[0]["cost"] = cost[0]
# data[0]["conversions"] = conversions[0]
#
# data.append({"channel" : channel[1]})
# data[1]["cost"] = cost[1]
# data[1]["conversions"] = conversions[1]
#
# data.append({"channel" : channel[2]})
# data[2]["cost"] = cost[2]
# data[2]["conversions"] = conversions[2]
# print(data)

nums = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
result = [x * 2 if x % 2 == 0 else x for x in nums]

nums = [1, 2, 2, 3, 3, 3, 4, 4, 4, 4]
count = {}
for i in nums:
    if i in count.keys():
        count[i] += 1
    else:
        count[i] = 1
print(count)

result1 = [(x * 2) for x in nums if x > 5]
print(result1)

ads = {
    "Facebook": {"cost": 1000, "conversions": 10},
    "Google": {"cost": 2000, "conversions": 20},
    "TikTok": {"cost": 500, "conversions": 5},
}
list1 = ads.