def safe_read_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return content
    except FileNotFoundError:
        print("文件不存在")
        return None
    except PermissionError:
        print("没有权限读取文件")
        return None
    except Exception as e:
        print("读取文件出错")
        return None

def safe_divide(a,b):
    if b == 0:
        print("不能除以0")
        return None
    elif not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        print("参数必须是数字")
        return None
    return a/b
try:
    safe_divide(10, 0)
except ZeroDivisionError:
    print("出现除数是0的错误")

def getnumber(prompt):
    while True:
      num = input(prompt)
      try:
          num2 = int(prompt)
          return num2
      except ValueError:
          print("输入无效，请重新输入")


def safe_calculate_ctr(df):
    # 1. 检查必要的列是否存在
    if "impressions" not in df.columns or "clicks" not in df.columns:
        raise ValueError("缺少必要的列：impressions 或 clicks")

    # 2. 检查数据是否为空
    if len(df) == 0:
        raise ValueError("数据为空")

    # 3. 先对所有行计算 CTR = clicks / impressions
    df["ctr"] = df["clicks"] / df["impressions"]

    # 4. 再把 impressions 为 0 的行的 CTR 替换为 0（避免除以0得到inf）
    df.loc[df["impressions"] == 0, "ctr"] = 0

    # 5. 返回计算后的 df
    return df

