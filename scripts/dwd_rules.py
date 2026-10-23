"""DWD 清洗规则的**唯一定义处**。

为什么规则要单独放在一个文件里，而不是散在 SQL 和脚本里：
    清洗规则有三个使用者 ——
        ① sql/dwd/dwd_ad_event.sql   真正跑生产
        ② scripts/build_dwd.py       逐条统计「各拦掉多少行」
        ③ scripts/test_dwd_rules.py  注入脏数据，验证规则真的抓得住
    如果三处各写一份，改了一处忘了另两处，就会出现「SQL 里少了一条规则，
    但审计报告显示 9 条全部通过」这种致命情况 —— 报告越漂亮越危险。

   所以规则只在这里写一遍，另两处引用它。SQL 那份是文本，无法直接引用，
   就靠 assert_sql_in_sync() 做文本比对：SQL 里少写或改写了任何一条，启动即报错。

一条规则的写法约定：
    expr 是一个 SQL 布尔表达式，**True 表示这一行要保留**。
    写成「保留条件」而不是「丢弃条件」，是为了多条规则可以用 AND 串起来，
    也为了让 expr 读起来就是一句人话：「uid IS NOT NULL」。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    code: str      # R1..R9，必须和 SQL 里的注释编号一致
    title: str     # 一句话说明这条规则在拦什么
    expr: str      # SQL 布尔表达式，True = 保留
    why: str       # 为什么会存在这种脏数据（面试会被问）


RULES: tuple[Rule, ...] = (
    Rule(
        "R1", "uid 必须非空",
        "uid IS NOT NULL",
        "主键缺失。上游埋点漏传 uid、或解析时字段错位都会造成。"
        "这种行无法归因到任何人，留着只会让「去重用户数」虚高。",
    ),
    Rule(
        "R2", "campaign 必须非空",
        "campaign IS NOT NULL",
        "同上。没法归到任何广告计划的花费，无法进任何一张报表。",
    ),
    Rule(
        "R3", "时间戳不能为负",
        "timestamp >= 0",
        "设备时钟错误、时区处理错误（减了 8 小时变成负数）都会造成。"
        "负时间会让「按天分区」直接把数据扔进 1969 年。",
    ),
    Rule(
        "R4", "click 只能取 0 或 1",
        "click IN (0, 1)",
        "布尔字段被写成多值枚举（比如 2 = 疑似点击）时会出现。"
        "不拦掉的话 SUM(click) 会算出一个比曝光数还大的点击数。",
    ),
    Rule(
        "R5", "conversion 只能取 0 或 1",
        "conversion IN (0, 1)",
        "同上。转化数一旦出现 2，CPA 直接腰斩。",
    ),
    Rule(
        "R6", "cost 必须为正数",
        "cost IS NOT NULL AND cost > 0",
        "有两种情况要拦：NULL 会让 SUM 忽略它（金额悄悄变少）；"
        "0 或负数可能是退款冲正，混进来会让 ROI 出现负花费。"
        "注意：这条规则对「曝光不花钱」的计费模式是不适用的，"
        "本数据集 100% 行 cost>0，说明它记的是计费事件不是纯曝光。",
    ),
    Rule(
        "R7", "转化时间不能早于曝光时间",
        "NOT (conversion = 1 AND conversion_timestamp < timestamp)",
        "典型的时区错配或时钟不同步。不拦掉的话「转化延迟」会算出负数，"
        "归因窗口分析全是错的。",
    ),
    Rule(
        "R8", "转化相关的哨兵值必须自洽",
        "NOT ((conversion = 1 AND (click_nb < 1 OR click_pos < 0)) "
        "OR (conversion = 0 AND (click_nb <> -1 OR click_pos <> -1)))",
        "这条规则的第一版完全写错了，值得记住。我原本以为 click_nb 是「累计点击次数」，"
        "写了 click=1 时 click_nb 必须 >= 1，结果一次拦掉 33.65% 的行（5,541,519 行，"
        "而总点击才 5,947,563 行）—— 等于说 93% 的点击都是脏数据，显然荒唐。"
        "实测后发现 click_nb 和 click_pos 都是「转化上下文」字段："
        "有转化时才有值（click_nb 1..174，click_pos 0..173），没转化时一律填 -1。"
        "所以 -1 不是脏数据，是「该字段对这行不适用」的哨兵值。"
        "这条规则真正要防的，是上游把填充条件改坏了 —— 该填 -1 的地方填了 0，"
        "或者有转化的行漏填 —— 那会让「转化前平均点击几次」算出一个假的数。",
    ),
    Rule(
        "R9", "有归因或转化，就必须有点击",
        "NOT ((attribution = 1 OR conversion = 1) AND click = 0)",
        "本数据集是点击归因模型：归因和转化的前提都是发生过点击。"
        "这种行一旦出现，会让「归因转化率」大于「点击转化率」——数学上不可能，"
        "但报表里很常见，因为归因和点击往往来自两套口径的拼接。"
        "（实测：attribution=1 但 click=0 有 0 行；conversion=1 但 click=0 也是 0 行，"
        " 两条都成立，所以合并成一条规则一起守。）",
    ),
)

# 去重规则不写进 RULES，因为它不是「按行判断真假」，而是「按主键分组取第一条」，
# 语义和上面 9 条不一样，混在一起会让审计逻辑变复杂。
#
# 为什么要写两份列名：清洗发生在 ODS 的原始列名上（uid / campaign / timestamp），
# 去见重时已经改过名了（uid / campaign_id / event_ts）。同一个逻辑键在两个阶段
# 有两个叫法，必须显式写出来 —— 只写一份然后靠脑子记，是这类 bug 的固定来源。
DEDUP_KEY_SRC: tuple[str, ...] = ("uid", "campaign", "timestamp")      # ODS 阶段
DEDUP_KEY_DWD: tuple[str, ...] = ("uid", "campaign_id", "event_ts")    # DWD 阶段
DEDUP_ORDER: str = "conversion DESC, click DESC, cost DESC"


def cleaning_where_sql(indent: str = "        ") -> str:
    """把 9 条规则拼成 WHERE 子句（AND 连接）。"""
    sep = f"\n{indent}AND "
    return sep.join(r.expr for r in RULES)


def _norm(text: str) -> str:
    """把连续空白压成一个空格，方便做「文本包含」比对。"""
    return " ".join(text.split())


def assert_sql_in_sync(sql_text: str) -> None:
    """确认 SQL 文件里真的写了这 9 条规则，一条都不能少。

    这个检查看起来多余（毕竟是我自己写的），但它挡的是未来：
    以后改规则时只改了 dwd_rules.py 忘了改 SQL，或者反过来，
    审计报告会显示「9 条规则全部执行」，而生产 SQL 里其实只有 8 条 ——
    这种错误不会报错，只会让数据悄悄多出一批脏行。
    """
    norm_sql = _norm(sql_text)
    missing = [r.code for r in RULES if _norm(r.expr) not in norm_sql]
    if missing:
        raise RuntimeError(
            f"SQL 与 dwd_rules.py 不同步：SQL 里找不到 {missing} 对应的条件。\n"
            f"请把 sql/dwd/dwd_ad_event.sql 的 WHERE 子句和这里对齐，"
            f"否则审计报告会虚报「规则已生效」。"
        )


def audit_counts_sql() -> str:
    """生成一段能在任意源表上跑的审计 SQL（每条规则命中多少行）。

    只用于人工核对和 test_dwd_rules.py 的注入测试。
    """
    parts = []
    for r in RULES:
        # COALESCE(expr, TRUE)：表达式结果为 NULL 时按「保留」处理，
        # 避免把一个 NULL 悄悄算成「该行脏」——NULL 和 false 在 WHERE 里行为不同。
        parts.append(
            f"SUM(CASE WHEN NOT COALESCE(({r.expr}), TRUE) THEN 1 ELSE 0 END) AS {r.code}"
        )
    return ",\n    ".join(parts)
