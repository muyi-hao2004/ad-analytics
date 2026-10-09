class Student:
    def __init__(self, name, score):
        self.name = name
        self.score = score

    def get_grade(self):
        if self.score > 90:
            rank_number = "A"
        elif self.score > 80:
            rank_number = "B"
        else:
            rank_number = "C"
        return rank_number

    def is_pass(self):
        if self.score >= 60:
            return "及格"
        else:
            return "不及格"


class Ad:
    def __init__(self, channel, cost, conversions):
        self.channel = channel
        self.cost = cost
        self.conversions = conversions

    def calc_cpa(self):
        return self.cost / self.conversions

class SearchAd(Ad):
    def __init__(self, channel, cost, conversions, keywords):
        super().__init__(channel, cost, conversions)
        self.keywords = keywords

    def get_keywords(self):
        return self.keywords


class Ad:
    def __init__(self, channel, cost, conversions):
        self.channel = channel
        self.cost = cost
        self.conversions = conversions
        self.__cpa = cost / conversions  # 私有属性，外面不能直接改

    def get_cpa(self):
        return self.__cpa


ad = Ad("Facebook", 1000, 10)

# 外面可以看
print(ad.get_cpa())  # 100.0

# 外面不能直接改
# ad.__cpa = 200  # ❌ 报错，私有属性不能直接访问

class Ad:
    def __init__(self, channel, cost, conversions):
        self.channel = channel
        self.cost = cost
        self.conversions = conversions

    # 定义打印的时候显示什么
    def __str__(self):
        return f"Ad(渠道={self.channel}, 花费={self.cost}, CPA={self.cost / self.conversions})"


ad = Ad("Facebook", 1000, 10)
print(ad)
# 没有__str__的时候：<__main__.Ad object at 0x7f9b1c0b5d30>
# 有__str__的时候：Ad(渠道=Facebook, 花费=1000, CPA=100.0)
