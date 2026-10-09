# Day11：Python 异常处理详解

## 一、什么是异常？为什么需要异常处理？

### 什么是异常？
异常就是程序运行时出现的"意外情况"或"错误"，导致程序无法继续正常执行。
- 读取文件但文件不存在
- 连接数据库但密码错了
- 调用API但网络断了
- 把字符串"abc"转成数字
- 取字典里不存在的key

### 没有异常处理会怎么样？
程序遇到错误直接崩溃退出，后面的代码都不会执行。

### 有异常处理会怎么样？
程序遇到错误不会崩溃，按照预设的方式处理错误，然后继续执行后面的代码。

> 生活类比：异常处理就像"应急预案"。遇到堵车（异常），绕路走，而不是把车扔在原地。

---

## 二、异常处理的基本语法

### 最基本结构：try / except
```python
try:
    # 可能出错的代码
    可能出错的代码
except 异常类型:
    # 出错了怎么办
    出错后的处理代码
```

执行流程：
1. 先执行 try 里的代码
2. 没有出错 → 跳过 except，继续执行
3. 出错了且类型匹配 → 跳到 except 执行处理代码
4. 处理完后继续执行后面的代码

### 完整结构：try / except / else / finally
```python
try:
    # 可能出错的代码
    可能出错的代码
except 异常类型1:
    # 遇到异常类型1时的处理
    处理代码1
except 异常类型2:
    # 遇到异常类型2时的处理
    处理代码2
else:
    # 没有出错时才执行（可选）
    没有出错时的代码
finally:
    # 不管有没有出错，最后都会执行（可选）
    收尾代码（关闭文件、关闭数据库连接）
```

| 部分 | 作用 | 什么时候执行 |
|---|---|---|
| try | 放可能出错的代码 | 总是先执行 |
| except | 出错后的处理 | 出错时执行（可以有多个） |
| else | 没有出错时的额外操作 | 只有没出错才执行（可选） |
| finally | 收尾操作 | 不管出不出错都执行（可选） |

> finally 最常用场景：关闭资源（文件、数据库连接、网络连接），确保资源不泄漏。

---

## 三、常见的异常类型（必须记住）

| 异常类型 | 什么时候发生 | 例子 |
|---|---|---|
| FileNotFoundError | 文件不存在 | open("不存在的文件.txt") |
| KeyError | 字典里找不到key | dict["不存在的key"] |
| IndexError | 列表/字符串索引越界 | list[100]（列表只有5个元素） |
| TypeError | 类型错误 | "abc" + 123 |
| ValueError | 值错误 | int("abc") |
| ZeroDivisionError | 除以0 | 10 / 0 |
| AttributeError | 对象没有这个属性/方法 | "abc".not_exist_method() |
| ImportError | 导入模块失败 | import not_exist_module |
| PermissionError | 权限不够 | 没有权限读取/写入文件 |
| ConnectionError | 网络连接失败 | API调用连不上服务器 |
| TimeoutError | 连接超时 | API调用太久没响应 |
| Exception | 所有异常的父类 | 兜底用，捕获所有异常 |

### 怎么看报错信息？
报错信息最后一行就是异常类型和错误信息：
```
ZeroDivisionError: division by zero
↑               ↑
异常类型        错误信息
```

---

## 四、捕获异常的几种方式

### 方式1：捕获指定类型（推荐）
```python
try:
    num = int(input("请输入数字："))
except ValueError:
    print("输入的不是数字！")
```
只捕获指定异常，其他异常正常报错。推荐这种方式。

### 方式2：捕获多种异常（多个except）
```python
try:
    num = int(input("请输入数字："))
    result = 10 / num
except ValueError:
    print("输入的不是数字！")
except ZeroDivisionError:
    print("不能除以0！")
```

### 方式3：一个except捕获多种（用元组）
```python
try:
    ...
except (ValueError, ZeroDivisionError):
    print("输入有误！")
```

### 方式4：捕获所有异常（Exception）
```python
try:
    可能出错的代码
except Exception as e:
    print(f"出错了：{e}")
```
Exception是所有异常的父类，能捕获几乎所有异常。
> 谨慎使用！可能会隐藏没预料到的错误，让调试变难。

### 方式5：获取异常详细信息
```python
try:
    num = int("abc")
except ValueError as e:
    print(f"异常类型：{type(e).__name__}")
    print(f"异常信息：{e}")
    print(f"异常参数：{e.args}")
```

---

## 五、主动抛出异常：raise

### 基本用法
```python
def divide(a, b):
    if b == 0:
        raise ValueError("除数不能为0")  # 主动抛出异常
    return a / b

try:
    result = divide(10, 0)
except ValueError as e:
    print(f"出错了：{e}")
```

### 什么时候用raise？
- 检查函数参数是否合法（不合法就raise）
- 检查业务规则（库存不足、余额不够）
- 在except里捕获后处理完再重新抛出（让上层也知道）

### 例子：参数校验
```python
def calculate_bmi(weight, height):
    if weight <= 0:
        raise ValueError("体重必须大于0")
    if height <= 0:
        raise ValueError("身高必须大于0")
    return weight / (height * height)
```

---

## 六、异常的传递（调用链）

异常会沿着调用链向上传递，直到被捕获。如果一直没人捕获，程序崩溃。

```python
def func3():
    10 / 0  # 这里出错

def func2():
    func3()  # 异常传到这里（没捕获）

def func1():
    try:
        func2()  # 异常传到这里被捕获
    except ZeroDivisionError:
        print("捕获到异常：除以0")

func1()
```

异常传递路径：func3出错 → func2（没捕获）→ func1（捕获处理）

> 实际工作中：底层函数（数据库操作）出错，一层层往上抛，直到最上层（main函数）捕获并处理（记录日志、返回错误信息）。

---

## 七、实际应用场景（必须加异常处理的5大场景）

### 场景1：文件读写
```python
def read_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return content
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

### 场景2：数据库连接和操作
```python
def query_data(sql):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        result = cursor.fetchall()
        return result
    except Exception as e:
        print(f"数据库查询出错：{e}")
        return None
    finally:
        if conn:
            conn.close()  # 不管有没有出错，都关闭连接
```

### 场景3：API调用（requests）
```python
def call_api(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # 状态码不是200就抛出异常
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

### 场景4：用户输入校验
```python
def get_age():
    while True:
        try:
            age = int(input("请输入年龄："))
            if age < 0 or age > 150:
                raise ValueError("年龄必须在0-150之间")
            return age
        except ValueError as e:
            print(f"输入无效：{e}，请重新输入")
```

### 场景5：数据处理（pandas）
```python
def calculate_metric(df):
    try:
        # 检查必要的列
        required_columns = ["impressions", "clicks", "cost"]
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"缺少必要的列：{col}")
        if len(df) == 0:
            raise ValueError("数据为空")
        # 计算指标
        df["ctr"] = df["clicks"] / df["impressions"]
        return df
    except ValueError as e:
        print(f"数据校验失败：{e}")
        return None
    except Exception as e:
        print(f"计算指标出错：{e}")
        return None
```

---

## 八、异常处理最佳实践

### ✅ 应该做的
1. **精确捕获异常**：尽量捕获具体类型，不要一上来就except Exception
2. **异常要有处理**：捕获了就要处理（记录日志、返回默认值、提示用户），不要except: pass
3. **用finally释放资源**：文件、数据库连接、网络连接在finally里关闭
4. **记录异常信息**：用logging记录异常（类型、错误信息、堆栈），方便排查
5. **函数参数校验用raise**：函数入口检查参数，不合法就主动raise
6. **最外层兜底**：在main函数或最外层加except Exception兜底，防止程序崩溃

### ❌ 不应该做的
1. **不要用except: pass**（捕获了什么都不做，等于隐藏错误）
2. **不要过度使用异常**：能用if判断的就用if，不要什么都用try/except
3. **不要在except里做复杂操作**：except里只做简单错误处理
4. **不要忽略异常信息**：捕获了至少要打印或记录错误信息
5. **不要用异常控制正常流程**：异常是处理"意外情况"的，不是做正常流程控制

---

## 九、项目里的异常处理例子

### data_loader.py 的 load_from_csv
```python
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
```

### database.py 的 get_connection
```python
def get_connection():
    try:
        conn = pymysql.connect(...)
        return conn
    except Exception as e:
        logger.error(f"数据库连接失败：{e}")
        raise  # 连接失败，重新抛出，让上层处理
```

### main.py 的数据库步骤
```python
try:
    init_database()
    insert_ad_data(df_clean, if_exists="replace")
    logger.info("数据写入完成")
except Exception as e:
    logger.error(f"数据库写入失败：{e}")
    logger.warning("跳过数据库步骤，继续后续流程")
```

---

## 十、练习题

### 练习1：文件读取函数
写一个函数 safe_read_file(file_path)：
- 文件存在 → 返回内容
- 文件不存在 → 打印提示，返回None
- 没有权限 → 打印提示，返回None
- 其他异常 → 打印错误信息，返回None

### 练习2：安全的除法函数
写一个函数 safe_divide(a, b)：
- b是0 → 打印"不能除以0"，返回None
- a或b不是数字 → 打印"参数必须是数字"，返回None
- 正常 → 返回a/b

### 练习3：用户输入数字
写一个函数 get_number(prompt)：
- 输入不是数字 → 提示"输入无效，请重新输入"，重新输入
- 直到输入有效数字，返回这个数字
- 提示：while循环 + try/except

### 练习4：pandas安全计算CTR
写一个函数 safe_calculate_ctr(df)：
- 检查是否有impressions和clicks列，没有就raise ValueError
- 检查df是否为空，为空就raise ValueError
- 计算CTR = clicks / impressions（impressions为0时CTR为0）
- 返回计算后的df
- 调用时用try/except捕获异常

---

## 总结

异常处理是Python工程化的核心技能，实际工作中天天都在用。
- **核心思想**：程序遇到错误不要崩溃，按照预设方式处理，继续运行
- **基本语法**：try/except/else/finally
- **常见异常**：FileNotFoundError、KeyError、ValueError、TypeError、ZeroDivisionError等
- **实际场景**：文件读写、数据库操作、API调用、用户输入、数据处理
- **最佳实践**：精确捕获、有处理、finally释放资源、记录日志、参数校验用raise
