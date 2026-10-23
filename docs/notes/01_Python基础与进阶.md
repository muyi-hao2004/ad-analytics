# Python 基础与进阶

本篇覆盖：
- Python 在数据工程中的定位、函数与 `return`、大括号与传参；
- 面向对象（类 / 继承 / 封装 / 三种方法 / 魔术方法）、接口、装饰器；
- 异常处理（语法、异常类型、捕获方式、`raise`、传递、最佳实践、项目案例）；
- 列表字典与字符串、推导式、`lambda` 与 `sorted`、生成器、`with`、内置函数与类型转换。

每个知识点按「是什么 → 为什么需要 → 怎么用 / 易错点」组织，可直接当面试复习材料。

---

## 一、Python 的定位：指挥语言，不是苦力

### 1.1 本质

Python 是「用代码操控电脑和其他软件」的工具，本身不做重活：不算大数据、不存数据，而是**指挥** MySQL、Spark、Kafka 去干。

```python
import os
os.mkdir("新文件夹")     # 用代码命令操作系统建目录
```

### 1.2 三层操控

| 层级 | 做什么 | 例子 |
|---|---|---|
| 操控操作系统 | 文件、目录、进程 | `os.listdir()` |
| 操控别的软件 | 通过客户端库发指令 | `pymysql.connect()` 操控 MySQL；`spark.read` 操控 Spark |
| 自己写服务器程序 | 对外提供服务 | `FastAPI()` |

### 1.3 为什么这个定位重要

数据工程师写 Python，本质是**用 Python 把各种软件串成一条自动化数据管道**。因此学习重点不在语法炫技，而在：调用库、处理数据、控制流程、组织代码（函数 / 类 / 模块）、处理错误（异常）——这些能力构成了本篇的主线。

---

## 二、函数与 return

### 2.1 函数定义格式

```python
def 函数名(参数1, 参数2, ...):
    """函数说明（可选）"""
    函数体（缩进 4 个空格）
    return 返回值（可选）
```

| 部分 | 说明 |
|---|---|
| `def` | 定义函数的关键字，固定小写 |
| 函数名 | 小写字母 + 下划线，如 `load_data` |
| `(参数)` | 输入，可以没有，多个用逗号隔开 |
| `:` | 必须有，表示下面是函数体 |
| 缩进 | 函数体必须缩进 4 个空格 |
| `return` | 输出结果，可选 |

**为什么用函数**：把一段逻辑命名、封装、复用；调用方只需要知道「传什么、拿什么」，不需要关心中间步骤。

### 2.2 return 是函数的输出

`return` 把结果从函数里递出来，调用方可以用变量接住继续用。

| | `print` | `return` |
|---|---|---|
| 作用 | 在屏幕上显示给人看 | 把结果递出来给程序用 |
| 能存进变量吗 | 不能（存到的是 `None`） | 能 |
| 能继续参与计算吗 | 不能 | 能 |

> 一句话：`print` 是给人看的，`return` 是给程序用的。

### 2.3 return 的五种用法

1. **返回一个值**（最常用）：`return result`
2. **返回多个值**：`return ctr, cvr, cpc`，实际打包成元组，调用方可以 `a, b, c = f()` 解包
3. **不写 return**：函数默认返回 `None`
4. **return 之后的代码不执行**：函数遇到 `return` 立即结束
5. **提前 return（early return）**：先处理异常 / 边界情况并返回，正常逻辑放后面，避免多层嵌套

```python
def calc_ctr(clicks, impressions):
    if impressions == 0:          # 边界情况先返回
        return 0.0
    return clicks / impressions   # 正常逻辑不再缩进
```

### 2.4 大括号 `{}` 的四种用法

普通函数传参**永远不用大括号**。大括号只出现在下面四种场合：

| 用法 | 出现位置 | 例子 |
|---|---|---|
| f-string 占位符 | 字符串前有 `f`，花括号里填变量 | `print(f"你好{name}")` |
| 字典定义 | 键值对集合 | `{"name": "小明", "age": 20}` |
| 集合定义 | 无序不重复元素 | `{1, 2, 3}` |
| `format` 占位符 | 老写法，后面跟 `.format()` | `"你好{}".format(name)` |

### 2.5 传参的三种正确写法

```python
multiply(3, 4)             # 位置参数：按顺序直接写值
multiply(a=3, b=4)         # 关键字参数：用等号
multiply(**params)         # 字典解包：params 是 {"a": 3, "b": 4}
```

**易错点**：`multiply(a: 3, b: 4)` 是错的。冒号是**类型注解**的语法（`def f(a: int)`），传参只能用等号或直接写值。

### 2.6 常见错误清单

| 错误 | 正确写法 | 原因 |
|---|---|---|
| 函数定义后面没加冒号 | `def func():` | 冒号必须有 |
| 函数体没缩进 | 缩进 4 个空格 | Python 靠缩进划分函数体 |
| `result = {a} * {b}` | `result = a * b` | 大括号是集合 / 字典语法，变量直接写 |
| `func(a: 1, b: 2)` | `func(1, 2)` 或 `func(a=1, b=2)` | 传参用等号，不用冒号 |
| 用 `print` 当返回值 | 用 `return` 返回结果 | `print` 只显示，不返回 |

---

## 三、面向对象

### 3.1 面向过程 vs 面向对象

| | 面向过程 | 面向对象 |
|---|---|---|
| 关注点 | 「怎么做」 | 「谁来做」 |
| 组织方式 | 函数 + 顺序执行 | 类 + 对象 |
| 适合 | 短小的线性脚本 | 有状态、需复用、需扩展的系统 |

面向对象的核心动机：**把数据和操作数据的功能打包在一起**，避免「一堆函数 + 一堆散落的变量」在规模变大后失控。

### 3.2 类与对象的最小结构

```python
class Ad:
    def __init__(self, channel, cost):
        self.channel = channel   # 实例属性
        self.cost = cost

    def calc_cpa(self, conversions):
        return self.cost / conversions

ad = Ad("Facebook", 1000)
ad.calc_cpa(10)      # 100.0
```

| 概念 | 含义 |
|---|---|
| 类 Class | 模板 / 设计图，定义一类事物共同的属性和方法 |
| 对象 / 实例 Object / Instance | 按类造出来的具体东西，一个类可以造很多个 |
| 属性 | 对象的数据（变量），写作 `self.属性名`，调用时**不加括号** |
| 方法 | 对象的功能（函数），写作 `def 方法名(self):`，调用时**加括号** |
| `__init__` | 构造方法，创建对象时自动调用，用于设置初始属性 |
| `self` | 对象自己，调用方法时 Python 自动传入，必须是第一个参数 |

### 3.3 继承与重写

```python
class SearchAd(Ad):                          # 子类继承父类
    def __init__(self, channel, cost, keywords):
        super().__init__(channel, cost)      # 调用父类构造
        self.keywords = keywords

    def calc_cpa(self, conversions):         # 重写：同名方法覆盖父类
        return super().calc_cpa(conversions) * 0.9
```

| 机制 | 作用 |
|---|---|
| 继承 `class 子类(父类):` | 子类自动拥有父类的属性和方法，实现代码复用 |
| `super()` | 调用父类的方法 / 构造，`super().__init__(参数)` |
| 重写 Override | 子类定义与父类同名的方法，覆盖父类实现 |

**工程中的典型用法（ORM）**：`AdData(Base)` 继承 SQLAlchemy 的 `Base` 父类，就自动获得「对象 ↔ 数据库表」的映射能力——框架把通用能力放在父类，业务类只声明自己的字段。

### 3.4 封装

封装 = 隐藏内部细节，只对外暴露必要的入口。

| 形式 | 写法 | 效果 |
|---|---|---|
| 私有属性 | `self.__balance`（双下划线开头） | 外部不能直接访问 |
| 私有方法 | `def __validate(self)` | 外部不能直接调用 |

好处：保护数据不被随意篡改、在入口处加校验、隐藏实现、内部改动不影响外部调用方。

### 3.5 类属性 vs 实例属性

| | 类属性 | 实例属性 |
|---|---|---|
| 定义位置 | 类体下方，方法之外 | `__init__` 里 |
| 写法 | `属性名 = 值` | `self.属性名 = 值` |
| 归属 | 所有对象共享一份 | 每个对象各自一份 |
| 典型用途 | 常量、全局配置、计数器 | 每个对象独有的数据 |

### 3.6 三种方法

| 类型 | 装饰器 | 第一个参数 | 能访问什么 | 调用方式 |
|---|---|---|---|---|
| 实例方法 | 无 | `self` | 实例属性 + 类属性 | `obj.method()` |
| 类方法 | `@classmethod` | `cls` | 类属性 | `Cls.method()` |
| 静态方法 | `@staticmethod` | 无 | 都不自动访问 | `Cls.method()` |

- 实例方法：最常用，需要读写对象状态时用。
- 类方法：工厂方法（用不同参数造对象）、修改类属性。
- 静态方法：与类相关但不需要对象状态的工具函数，放类里只是为了归属清晰。

### 3.7 魔术方法

前后双下划线，在特定操作发生时被 Python 自动调用，不用手动调。

| 方法 | 触发时机 | 作用 |
|---|---|---|
| `__init__` | 创建对象时 | 构造函数，初始化属性 |
| `__str__` | `print(obj)` / `str(obj)` | 给人看的字符串表示 |
| `__repr__` | 调试、交互式环境直接输出对象 | 给开发者看的表示 |
| `__len__` | `len(obj)` | 支持长度 |
| `__add__` | `obj1 + obj2` | 支持 `+` 运算符 |
| `__getitem__` | `obj[key]` | 支持下标取值 |

---

## 四、接口

### 4.1 什么是接口

接口是「外部和内部交互的入口」：外部不用管内部怎么实现，通过接口就能操作。面向对象语境下，接口通常指**类对外提供的公开方法**（public methods）。

生活类比：手机的音量键、自动售货机的投币口和按钮、餐厅的菜单——使用者只按约定操作，不需要知道内部电路或后厨流程。

### 4.2 怎么做：三步

1. 把数据设为私有：`self.__balance`
2. 提供公开方法作为接口：`deposit()`、`withdraw()`、`get_balance()`
3. 外部只能通过这些方法操作，不能直接碰私有属性

```python
class Account:
    def __init__(self, balance=0):
        self.__balance = balance          # 私有

    def deposit(self, amount):            # 公开接口
        if amount <= 0:
            raise ValueError("存款金额必须大于 0")
        self.__balance += amount

    def withdraw(self, amount):
        if amount > self.__balance:
            raise ValueError("余额不足")
        self.__balance -= amount

    def get_balance(self):                # 只读入口
        return self.__balance
```

### 4.3 为什么要提供接口

| 理由 | 说明 |
|---|---|
| 保护数据 | 不能随意修改，例如余额不允许被改成负数 |
| 加校验 | 校验逻辑写在接口内部（存款必须 > 0、余额不足不能取） |
| 隐藏实现细节 | 外部不必知道「总余额 = 活期 + 定期 + 理财」怎么算 |
| 方便修改 | 内部实现变了，接口不变，外部代码不用改 |

### 4.4 项目中的接口：`@property`

`Settings` 类的 `database_url` 就是接口：外部写 `settings.database_url` 就能拿到连接字符串，不需要知道内部怎么拼接。

```python
class Settings:
    @property
    def database_url(self):
        return f"mysql+pymysql://{self.user}:{self.pwd}@{self.host}:{self.port}/{self.db}"
```

以后拼接逻辑变了（比如加 SSL 参数），外部代码一行都不用改——这正是接口的价值：**变化被挡在接口后面**。

---

## 五、装饰器

### 5.1 是什么

装饰器在不修改原函数代码的前提下，给函数**包一层**，增加额外功能。

- 本质：一个函数，接收另一个函数作为参数，返回包装后的新函数。
- 类比：手机壳（不改变手机功能，增加防摔、美观）、快递外包装（不改变商品，增加保护和信息）。

### 5.2 `@` 语法糖

```python
@timer
def work():
    ...

# 完全等价于
def work():
    ...
work = timer(work)
```

即：定义函数 → 把函数传给装饰器 → 返回的新函数重新赋给原函数名。此后调用 `work()` 实际调用的是包装后的版本。

### 5.3 常见用途

记录日志、统计耗时、权限校验、缓存、失败重试、类型检查。共同点：这些逻辑与业务无关，但每个函数都需要，抽到装饰器里可以一次编写、到处复用。

### 5.4 自定义装饰器的原理

```python
import time

def timer(func):
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)     # 调用原函数
        end = time.time()
        print(f"执行了{end - start}秒")     # 额外功能
        return result                      # 原样返回结果
    return wrapper

@timer
def slow_function():
    time.sleep(1)

slow_function()      # 打印：执行了1.0...秒
```

三句话拆解：

1. `timer` 接收原函数 `func`，返回新函数 `wrapper`。
2. `wrapper` 内部 = 额外功能 + 调用原函数 + 返回结果。
3. `*args, **kwargs` 接收任意位置 / 关键字参数，保证装饰器对任何函数都通用。

### 5.5 类里最常用的三个装饰器

| 装饰器 | 作用 | 典型场景 |
|---|---|---|
| `@property` | 把方法变成属性，调用时不用加括号 | 无参数、返回计算值的只读属性 |
| `@classmethod` | 类方法，第一个参数 `cls`，通过类名调用 | 工厂方法、修改类属性 |
| `@staticmethod` | 静态方法，无 `self` / `cls` | 与类相关但独立的工具函数 |

`@property` 可配合 `@属性名.setter`，在赋值时加校验，这是封装最优雅的写法：

```python
class Ad:
    def __init__(self, cost):
        self._cost = cost

    @property
    def cost(self):
        return self._cost

    @cost.setter
    def cost(self, value):
        if value < 0:
            raise ValueError("成本不能为负")
        self._cost = value
```

项目实例：`Settings.database_url` 用 `@property`，每次访问时动态拼接连接字符串，而不是在 `__init__` 里算死。

---

## 六、异常处理

### 6.1 什么是异常，为什么需要处理

异常是程序**运行时**出现的意外情况，导致程序无法继续正常执行：

- 读文件但文件不存在
- 连数据库但密码错误
- 调 API 但网络断了
- 把 `"abc"` 转成数字
- 取字典里不存在的 key

| 情况 | 结果 |
|---|---|
| 没有异常处理 | 程序直接崩溃退出，后面的代码全部不执行 |
| 有异常处理 | 按预设方式处理错误，然后继续执行后面的代码 |

类比：异常处理是「应急预案」。遇到堵车（异常）就绕路，而不是把车扔在原地。

### 6.2 基本结构：try / except

```python
try:
    可能出错的代码
except 异常类型:
    出错后的处理代码
```

执行流程：

1. 先执行 `try` 里的代码。
2. 没出错 → 跳过 `except`，继续往下执行。
3. 出错且类型匹配 → 跳到对应 `except` 执行处理代码。
4. 处理完继续执行 `except` 之后的代码。

### 6.3 完整结构：try / except / else / finally

```python
try:
    可能出错的代码
except 异常类型1:
    处理代码1
except 异常类型2:
    处理代码2
else:
    没有出错时才执行的代码
finally:
    收尾代码（关闭文件、关闭数据库连接）
```

| 部分 | 作用 | 什么时候执行 |
|---|---|---|
| `try` | 放可能出错的代码 | 总是先执行 |
| `except` | 出错后的处理 | 出错且类型匹配时（可有多个） |
| `else` | 没出错时的额外操作 | 只有没出错才执行（可选） |
| `finally` | 收尾操作 | 不管出不出错都执行（可选） |

`finally` 最常用的场景是**释放资源**：文件、数据库连接、网络连接，确保不泄漏。

### 6.4 常见异常类型（必须记住）

| 异常类型 | 什么时候发生 | 例子 |
|---|---|---|
| `FileNotFoundError` | 文件不存在 | `open("不存在.txt")` |
| `KeyError` | 字典里找不到 key | `d["不存在的key"]` |
| `IndexError` | 列表 / 字符串索引越界 | `lst[100]`（只有 5 个元素） |
| `TypeError` | 类型错误 | `"abc" + 123` |
| `ValueError` | 值错误（类型对、值不对） | `int("abc")` |
| `ZeroDivisionError` | 除以 0 | `10 / 0` |
| `AttributeError` | 对象没有这个属性 / 方法 | `"abc".not_exist_method()` |
| `ImportError` | 导入模块失败 | `import not_exist_module` |
| `PermissionError` | 权限不足 | 无权限读写文件 |
| `ConnectionError` | 网络连接失败 | API 连不上服务器 |
| `TimeoutError` | 连接超时 | API 太久没响应 |
| `Exception` | 所有异常的父类 | 兜底捕获所有异常 |

### 6.5 怎么看报错信息

报错信息的**最后一行**就是异常类型和错误信息：

```
ZeroDivisionError: division by zero
↑                  ↑
异常类型            错误信息
```

上面的调用栈（Traceback）说明「错在哪一行、经过哪些函数」，最后一行说明「错在什么性质」。

### 6.6 捕获异常的四种写法

**方式 1：捕获指定类型（推荐）**

```python
try:
    num = int(input("请输入数字："))
except ValueError:
    print("输入的不是数字！")
```

只捕获指定异常，其他异常照常抛出，不会掩盖问题。

**方式 2：多个 except 分别处理**

```python
try:
    num = int(input("请输入数字："))
    result = 10 / num
except ValueError:
    print("输入的不是数字！")
except ZeroDivisionError:
    print("不能除以 0！")
```

**方式 3：一个 except 捕获多种（元组）**

```python
try:
    ...
except (ValueError, ZeroDivisionError):
    print("输入有误！")
```

**方式 4：捕获所有异常（`Exception`）**

```python
try:
    可能出错的代码
except Exception as e:
    print(f"出错了：{e}")
```

`Exception` 是所有异常的父类，能捕获几乎所有异常。**谨慎使用**：它会把没预料到的错误一起吞掉，让调试变难。

**获取异常详情**：

```python
try:
    num = int("abc")
except ValueError as e:
    print(f"异常类型：{type(e).__name__}")   # ValueError
    print(f"异常信息：{e}")                 # invalid literal for int() ...
    print(f"异常参数：{e.args}")            # ("invalid literal ...",)
```

### 6.7 主动抛出异常：raise

```python
def divide(a, b):
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

什么时候用 `raise`：

- 检查函数参数是否合法（不合法就抛）
- 检查业务规则（库存不足、余额不够）
- 在 `except` 里处理完后重新抛出，让上层也知道出过错（`raise` 单独一行即重抛）

参数校验示例：

```python
def calculate_bmi(weight, height):
    if weight <= 0:
        raise ValueError("体重必须大于 0")
    if height <= 0:
        raise ValueError("身高必须大于 0")
    return weight / (height * height)
```

### 6.8 异常的传递（调用链）

异常沿调用链向上传递，直到被捕获；一直没人捕获，程序崩溃。

```python
def func3():
    10 / 0            # 这里出错

def func2():
    func3()           # 异常传到这里（没捕获，继续向上）

def func1():
    try:
        func2()       # 异常传到这里被捕获
    except ZeroDivisionError:
        print("捕获到异常：除以0")

func1()
```

传递路径：`func3` 出错 → `func2`（不处理）→ `func1`（捕获处理）。

实际工作中：底层函数（数据库操作、读文件）出错后一层层向上抛，最上层（`main`）统一捕获，记录日志、返回错误信息——这就是「谁最清楚怎么处理，谁捕获」。

### 6.9 必须加异常处理的五个场景

**场景 1：文件读写**

```python
def read_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"文件不存在：{file_path}")
        return None
    except PermissionError:
        print(f"没有权限读取文件：{file_path}")
        return None
    except Exception as e:
        print(f"读取文件出错：{e}")
        return None
```

**场景 2：数据库连接和操作**

```python
def query_data(sql):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        return cursor.fetchall()
    except Exception as e:
        print(f"数据库查询出错：{e}")
        return None
    finally:
        if conn:
            conn.close()          # 不管成不成，都关连接
```

**场景 3：API 调用（requests）**

```python
def call_api(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()        # 状态码非 2xx 时抛 HTTPError
        return response.json()
    except requests.exceptions.Timeout:
        print("请求超时")
        return None
    except requests.exceptions.ConnectionError:
        print("网络连接失败")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"HTTP错误：{e}")
        return None
    except Exception as e:
        print(f"API调用出错：{e}")
        return None
```

**场景 4：用户输入校验**

```python
def get_age():
    while True:
        try:
            age = int(input("请输入年龄："))
            if age < 0 or age > 150:
                raise ValueError("年龄必须在 0-150 之间")
            return age
        except ValueError as e:
            print(f"输入无效：{e}，请重新输入")
```

**场景 5：数据处理（pandas）**

```python
def calculate_metric(df):
    try:
        required_columns = ["impressions", "clicks", "cost"]
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"缺少必要的列：{col}")
        if len(df) == 0:
            raise ValueError("数据为空")
        df["ctr"] = df["clicks"] / df["impressions"]
        return df
    except ValueError as e:
        print(f"数据校验失败：{e}")
        return None
    except Exception as e:
        print(f"计算指标出错：{e}")
        return None
```

### 6.10 最佳实践

**应该做的**

1. **精确捕获**：尽量捕获具体类型，不要一上来就 `except Exception`。
2. **捕获了就要处理**：记录日志、返回默认值、提示用户，三选一。
3. **用 `finally` 释放资源**：文件、数据库连接、网络连接。
4. **记录异常信息**：用 `logging` 记下类型、信息、堆栈，便于排查。
5. **参数校验用 `raise`**：函数入口检查，不合法立即抛。
6. **最外层兜底**：`main` 或最外层加 `except Exception`，防止整个程序崩溃。

**不应该做的**

1. 不要 `except: pass`——等于把错误藏起来。
2. 不要过度使用异常：能用 `if` 判断的就用 `if`。
3. 不要在 `except` 里做复杂操作，只做简单错误处理。
4. 不要忽略异常信息，至少打印或记录。
5. 不要用异常控制正常流程，异常只处理「意外」。

### 6.11 项目中的三层异常处理

| 位置 | 策略 | 代码要点 |
|---|---|---|
| `data_loader.py` 的 `load_from_csv` | 捕获后返回 `None`，让流程继续 | `except FileNotFoundError` 单独处理，其余 `except Exception` 记日志 |
| `database.py` 的 `get_connection` | 记录日志后 `raise` 重抛 | 连接失败必须让上层知道，不能静默返回 `None` |
| `main.py` 的数据库步骤 | 最外层兜底，决定是否继续 | 捕获后 `logger.warning("跳过数据库步骤，继续后续流程")` |

```python
# data_loader.py
def load_from_csv(file_path, ...):
    try:
        df = pd.read_csv(file_path, ...)
        return df
    except FileNotFoundError:
        logger.error(f"文件不存在：{file_path}")
        return None
    except Exception as e:
        logger.error(f"读取CSV文件出错：{e}")
        return None

# database.py
def get_connection():
    try:
        conn = pymysql.connect(...)
        return conn
    except Exception as e:
        logger.error(f"数据库连接失败：{e}")
        raise        # 重抛，交给上层决定

# main.py
try:
    init_database()
    insert_ad_data(df_clean, if_exists="replace")
    logger.info("数据写入完成")
except Exception as e:
    logger.error(f"数据库写入失败：{e}")
    logger.warning("跳过数据库步骤，继续后续流程")
```

这三种策略对应三种语义：**可降级**（返回 None 继续）、**不可降级**（重抛）、**整体兜底**（最外层记录并决策）。

### 6.12 练习

1. `safe_read_file(file_path)`：文件存在返回内容；不存在 / 无权限 / 其他异常分别打印提示并返回 `None`。
2. `safe_divide(a, b)`：`b` 为 0 → 打印「不能除以 0」返回 `None`；参数非数字 → 打印「参数必须是数字」返回 `None`；正常返回 `a / b`。
3. `get_number(prompt)`：输入非数字时提示并重新输入，直到拿到有效数字（`while` + `try/except`）。
4. `safe_calculate_ctr(df)`：缺 `impressions` / `clicks` 列或 df 为空时 `raise ValueError`；计算 `CTR = clicks / impressions`（`impressions` 为 0 时 CTR 记 0）；调用处用 `try/except` 捕获。

---

## 七、列表、字典与字符串

### 7.1 列表取值与切片

```python
nums = ["Facebook", "Google", "TikTok"]

nums[0]      # 'Facebook'   第 1 个（下标从 0 开始）
nums[-1]     # 'TikTok'     最后一个
nums[1:3]    # ['Google', 'TikTok']   第 2 到第 3 个（不含右端）
nums[:3]     # 前 3 个
nums[::-1]   # 反转
```

易错点：切片**左闭右开**；负下标从末尾数；`[::-1]` 的 `-1` 是步长，表示倒序。

### 7.2 列表修改与追加

```python
nums[0] = "Instagram"          # 修改指定位置

ads = []
ads.append({"channel": "Facebook", "cost": 1000})
ads.append({"channel": "Google", "cost": 2000})
```

「列表装字典」是数据工程里最常见的结构：一行一条记录，字段用 key 表示，可直接转 DataFrame。

### 7.3 字典常用方法

```python
d = {"张三": 85, "李四": 92}

d["张三"]           # 85，key 不存在会抛 KeyError
d.get("张三")       # 85，不存在返回 None
d.get("王五", 0)    # 0，不存在返回默认值

d.keys()            # 所有 key
d.values()          # 所有 value
d.items()           # 所有 (key, value) 对
```

易错点：`d["不存在的key"]` 抛 `KeyError`；不确定是否存在时用 `.get()`。

### 7.4 字符串常用方法

```python
s = "  Hello World  "

s.strip()                 # 去两边空格
s.upper()                 # 全大写
s.lower()                 # 全小写
s.title()                 # 每个单词首字母大写

s.split(",")              # 按分隔符拆成列表
",".join(["a", "b"])      # 用分隔符把列表拼成字符串

s.replace("CPA", "ROI")   # 替换
s.startswith("data")      # 判断开头
s.endswith(".csv")        # 判断结尾
```

注意：字符串方法**返回新字符串**，不会原地修改（字符串不可变），必须重新赋值。

---

## 八、推导式

### 8.1 基本格式

```python
[要放进去的东西 for x in 原列表]
```

比「建空列表 + for 循环 + append」更短、更快、意图更集中。

### 8.2 五种用法

```python
# 1. 基本：每个元素变换
[x * 2 for x in range(5)]                       # [0, 2, 4, 6, 8]

# 2. 过滤：if 在 for 后面
[x for x in range(10) if x % 2 == 0]            # [0, 2, 4, 6, 8]

# 3. 分支：if-else 在 for 前面
[x * 2 if x % 2 == 0 else x for x in range(10)] # 偶数乘 2，奇数不变

# 4. 嵌套循环：二维列表拍平
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
[num for row in matrix for num in row]          # [1..9]

# 5. 字典推导式：键值互换
d = {"a": 1, "b": 2, "c": 3}
{value: key for key, value in d.items()}        # {1: 'a', 2: 'b', 3: 'c'}
```

### 8.3 对比与易错点

| 类型 | 格式 |
|---|---|
| 基本 | `[x for x in list]` |
| 过滤 | `[x for x in list if 条件]` |
| 分支 | `[A if 条件 else B for x in list]` |
| 嵌套循环 | `[x for row in matrix for x in row]` |

**核心区别**：`if` 在 `for` **后面**是「筛选，不合格的丢掉」；`if-else` 在 `for` **前面**是「每个元素二选一，数量不变」。写反了语法直接报错。

---

## 九、lambda 与 sorted

### 9.1 lambda 匿名函数

```python
lambda 参数: 返回值
```

```python
def add(x, y):        # 普通函数
    return x + y

add_l = lambda x, y: x + y     # 等价的匿名函数
```

用途：函数只用一次、逻辑只有一行时，省去 `def` 和命名的噪音。最常用的场合是给 `sorted` 传 `key`。

### 9.2 sorted 排序

```python
sorted(可迭代对象, key=函数, reverse=True/False)
```

```python
sorted([3, 1, 2])                          # 升序 [1, 2, 3]
sorted([3, 1, 2], reverse=True)            # 降序 [3, 2, 1]
sorted(words, key=lambda x: len(x))        # 按字符串长度
sorted(data, key=lambda x: x[1])           # 按元组第 2 个元素
sorted(data, key=lambda x: (x[0], x[1]))   # 多条件：先渠道，再花费
```

| 参数 | 作用 |
|---|---|
| `key` | 指定「按什么排序」，接收一个函数，对每个元素取排序依据 |
| `reverse` | `True` 降序，默认 `False` 升序 |

```python
data = [("张三", 85), ("李四", 92), ("王五", 78)]
sorted(data, key=lambda x: x[1])
# [('王五', 78), ('张三', 85), ('李四', 92)]
```

易错点：`sorted()` 返回**新列表**，原对象不变；列表自身的 `list.sort()` 是原地排序，返回 `None`。多条件排序把多个依据打包成元组即可，元组按位置依次比较。

---

## 十、生成器

### 10.1 列表 vs 生成器

```python
nums = [x for x in range(1000000)]   # 列表：一次性把所有元素算出来存进内存
nums = (x for x in range(1000000))   # 生成器：用一个算一个，不占内存
```

| | 列表 | 生成器 |
|---|---|---|
| 内存 | 全部元素常驻内存 | 只保留当前状态 |
| 计算时机 | 立即全部计算 | 取值时才计算（惰性） |
| 可否重复遍历 | 可以 | 只能遍历一次 |

### 10.2 yield

```python
def read_lines(file_path):
    with open(file_path) as f:
        for line in f:
            yield line.strip()      # 暂停并返回一行，下次从这里继续
```

含 `yield` 的函数调用后不会立刻执行函数体，而是返回一个生成器；每次 `next()`（或 `for`）才推进到下一个 `yield`。

### 10.3 什么时候用

- 读大文件（逐行处理，不会一次撑爆内存）
- 处理海量数据
- 数据只需要遍历一次、不需要反复访问

---

## 十一、with 语句

### 11.1 作用

自动做收尾工作：关文件、关连接、释放锁。不管是否出错都会执行，等价于 `try/finally` 的简写，但更简洁、不易漏。

### 11.2 常用场景

```python
# 文件
with open("data.csv") as f:
    content = f.read()
# 离开 with 块，文件自动关闭

# 数据库连接（可嵌套）
with pymysql.connect(...) as conn:
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM ad_data")
```

易错点：`with` 块结束后文件已关闭，不能再 `f.read()`；需要后续处理的数据要在块内取出或转成列表 / DataFrame。

---

## 十二、内置函数与类型转换

### 12.1 常用内置函数

| 函数 | 作用 |
|---|---|
| `len()` | 长度 |
| `sum()` | 求和 |
| `max()` / `min()` | 最大 / 最小 |
| `round(x, n)` | 四舍五入到 n 位小数 |
| `abs(x)` | 绝对值 |
| `range()` | 生成数字序列 |
| `enumerate()` | 带索引遍历 |
| `zip()` | 同时遍历多个列表 |
| `type()` | 查看类型 |
| `isinstance()` | 判断是否为某类型（支持继承关系，比 `type()` 更适合校验） |
| `int()` / `float()` / `str()` | 类型转换 |

### 12.2 数字与字符串互转

```python
int("123")      # 123    字符串 → 整数
float("3.14")   # 3.14   字符串 → 小数
str(123)        # '123'  数字 → 字符串
```

易错点：`int("3.14")` 会抛 `ValueError`，带小数点的字符串要先 `float()` 再转换。

### 12.3 列表与元组互转

```python
tuple([1, 2, 3])   # (1, 2, 3)
list((1, 2, 3))    # [1, 2, 3]
```

### 12.4 字典转列表

```python
list(d.keys())     # key 列表
list(d.values())   # value 列表
list(d.items())    # (key, value) 元组列表
```

### 12.5 列表与字符串互转

```python
"Facebook,Google".split(",")        # ['Facebook', 'Google']
",".join(["Facebook", "Google"])    # 'Facebook,Google'
```

### 12.6 其他

```python
list(set([1, 2, 2, 3]))   # 去重，[1, 2, 3]（顺序不保证）
bool(0)                   # False；bool(非空) 为 True
```

用 `set` 去重最简洁，但会丢失原顺序；需要保序时不能直接用 `set`。

---

## 面试速查

- **Python 在数据工程中的定位？** → 指挥语言 / 胶水，自己不干重活，把 MySQL、Spark、Kafka 串成自动化数据管道。
- **`print` 和 `return` 的区别？** → `print` 只在屏幕显示，`return` 把结果交给程序继续用。
- **函数不写 return 返回什么？** → `None`。
- **`{}` 有哪几种用法？** → f-string 占位符、字典、集合、`format` 占位符；传参不用大括号。
- **`func(a: 1)` 为什么错？** → 冒号是类型注解语法，传参只能 `func(1)` 或 `func(a=1)`。
- **类和对象的关系？** → 类是模板，对象是按模板造出的实例，一个类可造多个对象。
- **`self` 是什么？** → 对象自身，调用方法时由 Python 自动传入，必须是第一个参数。
- **`__init__` 的作用？** → 构造方法，创建对象时自动调用，用来初始化实例属性。
- **继承有什么用？怎么调父类？** → 复用父类属性和方法；`super().__init__(...)` 调父类构造，同名方法即重写。
- **封装怎么实现？** → 双下划线开头的属性 / 方法变私有，对外只留公开方法作为接口。
- **类属性和实例属性的区别？** → 类属性所有对象共享一份，实例属性每个对象独有。
- **三种方法的区别？** → 实例方法收 `self` 能访问实例状态；类方法 `@classmethod` 收 `cls` 只能访问类属性；静态方法 `@staticmethod` 无 self/cls。
- **`__str__` 和 `__repr__` 的区别？** → `__str__` 给人看（print），`__repr__` 给开发者调试看。
- **什么是接口？为什么要？** → 类对外暴露的公开方法；保护数据、加校验、隐藏实现、内部改动不影响外部。
- **`@property` 有什么用？** → 把方法变成属性，访问不加括号；配合 `.setter` 可在赋值时校验。
- **装饰器的本质？** → 接收函数、返回新函数的高阶函数；`@d` 等价于 `f = d(f)`。
- **写装饰器为什么要 `*args, **kwargs`？** → 让 wrapper 能接收任意参数并原样转发给原函数，保证通用。
- **`try/except/else/finally` 各自何时执行？** → try 总先执行；except 出错且类型匹配时；else 只在没出错时；finally 无论如何都执行。
- **`finally` 的典型用途？** → 关闭文件、数据库连接等资源，防止泄漏。
- **`ValueError` 和 `TypeError` 的区别？** → 类型对但值不合法是 `ValueError`（`int("abc")`）；类型本身不对是 `TypeError`（`"abc" + 123`）。
- **`raise` 什么时候用？** → 参数校验、业务规则不满足、在 except 里处理完再重抛给上层。
- **异常没人捕获会怎样？** → 沿调用链一直向上抛，最终程序崩溃并打印 Traceback。
- **为什么不要 `except: pass`？** → 把错误完全隐藏，问题会在更远的地方以更难查的形式爆发。
- **推导式里 if 放在前后有什么区别？** → 在 `for` 后是过滤，在 `for` 前是 if-else 二选一。
- **`sorted` 的 `key` 怎么用？** → 传一个函数，返回排序依据；多条件返回元组。
- **生成器比列表省什么？** → 省内存，惰性求值、用一个算一个，但只能遍历一次。
- **`with` 解决了什么？** → 自动收尾（关文件 / 连接），异常时也能执行，等价于可靠的 `try/finally`。
- **`list(set(...))` 去重有什么副作用？** → 丢失原顺序。
