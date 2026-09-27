# Day21: Python深入知识总结

---

## 一、列表和字典操作

### 列表取元素
```python
nums = ["Facebook", "Google", "TikTok"]

nums[0]      # 第1个
nums[-1]     # 最后一个
nums[1:3]    # 第2到第3个（不包含3）
nums[:3]     # 前3个
nums[::-1]   # 反转
```

### 列表修改元素
```python
nums[0] = "Instagram"  # 修改第1个元素
```

### 列表加字典
```python
ads = []
ads.append({"channel": "Facebook", "cost": 1000})
ads.append({"channel": "Google", "cost": 2000})
```

### 字典常用方法
```python
d = {"张三": 85, "李四": 92}

d["张三"]           # 取值，不存在报错
d.get("张三")       # 取值，不存在返回None
d.get("王五", 0)    # 取值，不存在返回默认值0

d.keys()    # 所有key
d.values()  # 所有value
d.items()   # 所有key-value对
```

---

## 二、列表推导式

### 基本格式
```python
[要放进去的东西 for x in 原列表]
```

### 1. 基本用法
```python
[x * 2 for x in range(5)]
# [0, 2, 4, 6, 8]
```

### 2. 加过滤条件（if在后面）
```python
# 只保留偶数
[x for x in range(10) if x % 2 == 0]
# [0, 2, 4, 6, 8]
```

### 3. if-else分支（if在前面）
```python
# 偶数乘2，奇数不变
[x * 2 if x % 2 == 0 else x for x in range(10)]
```

### 4. 嵌套循环（二维列表拍平）
```python
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
[num for row in matrix for num in row]
# [1, 2, 3, 4, 5, 6, 7, 8, 9]
```

### 5. 字典推导式
```python
# 键值互换
d = {"a": 1, "b": 2, "c": 3}
{value: key for key, value in d.items()}
# {1: 'a', 2: 'b', 3: 'c'}
```

### 对比总结
| 类型 | 格式 |
|------|------|
| 基本 | `[x for x in list]` |
| 过滤 | `[x for x in list if 条件]` |
| 分支 | `[A if 条件 else B for x in list]` |
| 嵌套循环 | `[x for row in matrix for x in row]` |

---

## 三、字符串常用方法

```python
s = "  Hello World  "

s.strip()        # 去两边空格
s.upper()        # 全大写
s.lower()        # 全小写
s.title()        # 首字母大写

s.split(",")     # 按逗号拆分
",".join(list)   # 用逗号拼接

s.replace("CPA", "ROI")  # 替换
s.startswith("data")     # 判断开头
s.endswith(".csv")       # 判断结尾
```

---

## 四、lambda匿名函数

### 格式
```python
lambda 参数: 返回值
```

### 例子
```python
# 普通函数
def add(x, y):
    return x + y

# lambda
lambda x, y: x + y
```

### 最常用场景：sorted的key
```python
data = [("张三", 85), ("李四", 92), ("王五", 78)]

# 按第2个元素（分数）排序
sorted(data, key=lambda x: x[1])
```

---

## 五、sorted排序

### 格式
```python
sorted(可迭代对象, key=函数, reverse=True/False)
```

### 例子
```python
# 数字升序
sorted([3, 1, 2])

# 数字降序
sorted([3, 1, 2], reverse=True)

# 按字符串长度
sorted(words, key=lambda x: len(x))

# 按元组第2个元素
sorted(data, key=lambda x: x[1])

# 多条件排序（先按渠道，再按花费）
sorted(data, key=lambda x: (x[0], x[1]))
```

---

## 六、面向对象

### 基本格式
```python
class Ad:
    def __init__(self, channel, cost):
        self.channel = channel  # 实例变量
        self.cost = cost
    
    def calc_cpa(self, conversions):
        return self.cost / conversions
```

### 创建对象
```python
ad = Ad("Facebook", 1000)
ad.calc_cpa(10)  # 100.0
```

### 继承
```python
class SearchAd(Ad):  # 继承Ad
    def __init__(self, channel, cost, keywords):
        super().__init__(channel, cost)  # 调用父类构造函数
        self.keywords = keywords
```

### 常用魔术方法
| 方法 | 作用 |
|------|------|
| `__init__` | 构造函数 |
| `__str__` | 打印时显示的字符串 |
| `__len__` | 支持len() |
| `__add__` | 支持+运算符 |
| `__getitem__` | 支持[]取值 |

---

## 七、生成器（Generator）

### 列表 vs 生成器
```python
# 列表：一次性存所有数据
nums = [x for x in range(1000000)]

# 生成器：用一个算一个，不占内存
nums = (x for x in range(1000000))
```

### yield关键字
```python
def read_lines(file_path):
    with open(file_path) as f:
        for line in f:
            yield line.strip()  # 暂停，返回一行
```

### 什么时候用生成器？
- 读大文件
- 处理海量数据
- 不需要多次遍历的数据

---

## 八、with语句

### 作用
自动做收尾工作（关文件、关连接、释放锁），不管出不出错都会执行。

### 常用场景
```python
# 打开文件
with open("data.csv") as f:
    f.read()

# 连接数据库
with pymysql.connect(...) as conn:
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM ad_data")
```

---

## 九、常用内置函数

| 函数 | 作用 |
|------|------|
| `len()` | 长度 |
| `sum()` | 求和 |
| `max()` / `min()` | 最大/最小 |
| `round(x, n)` | 四舍五入 |
| `abs(x)` | 绝对值 |
| `range()` | 生成数字序列 |
| `enumerate()` | 带索引遍历 |
| `zip()` | 同时遍历多个列表 |
| `type()` | 看类型 |
| `isinstance()` | 判断类型 |
| `int()` / `float()` / `str()` | 类型转换 |
