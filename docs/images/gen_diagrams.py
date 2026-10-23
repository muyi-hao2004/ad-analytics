# -*- coding: utf-8 -*-
"""生成 README 用的两张 SVG 图。

为什么要用脚本而不是直接手写 SVG
--------------------------------
图里的数字（13,981 行 / 78 列 / 35 条断言 / 4-7 比 8）都是改一次数据就会变的。
写成脚本，改数字只改常量，不用在几百行坐标里找；坐标算错了也能一次性重排。

为什么要用 Edge 无头截图自检
---------------------------
SVG 的坐标是手算的，中文还占两个字符宽 —— 不渲染出来看一遍，极容易出现
「文字出框」「标签压线」「箭头指错框」这类问题，而这类问题在纯文本里看不出来。

用法::

    python docs/images/gen_diagrams.py
    # 可选：渲染成 PNG 肉眼验收
    msedge --headless=new --screenshot=out.png --window-size=1000,716 file:///.../architecture.svg
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).parent

# ---- 配色：白底 + 低饱和描边，保证在 GitHub 亮色主题下不刺眼 ----
INK = "#0f172a"
SUB = "#5b6b82"
LINE = "#94a3b8"

C_SRC = ("#f1f5f9", "#94a3b8")
C_WH = ("#eef4ff", "#4f74e3")
C_GATE = ("#fff5e6", "#e08a00")
C_API = ("#f4f1ff", "#7c5cf0")
C_AGENT = ("#eafaf2", "#0e9f6e")
C_GUARD = ("#fdeeee", "#d94b4b")
C_CHIP = ("#f8fafc", "#cbd5e1")

FONT = ("'Segoe UI','PingFang SC','Microsoft YaHei','Noto Sans SC',"
        "'Hiragino Sans GB',sans-serif")


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rect(x, y, w, h, fill, stroke, r=10, sw=1.6, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def txt(x, y, s, size=12, fill=INK, weight="400", anchor="start"):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">'
            f'{esc(s)}</text>')


def line(x1, y1, x2, y2, color=LINE, sw=1.5, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
            f'stroke-width="{sw}"{d}/>')


def arrow(x1, y1, x2, y2, color=LINE, sw=1.5, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
            f'stroke-width="{sw}"{d} marker-end="url(#arrow)"/>')


def head() -> str:
    return f"""<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
          markerHeight="6" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/>
  </marker>
</defs>"""


# ============================================================
# 图 1：端到端链路
# ============================================================

def architecture() -> str:
    W, H = 1000, 716
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="IAA 广告变现分析中台端到端链路">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>', head()]

    p.append(txt(28, 36, "IAA 广告变现分析中台 · 端到端链路", 18, INK, "700"))
    p.append(txt(28, 58, "合成数据 → 五层数仓 → LLM 快照 → Agent 决策 → 护栏审批 → 评测闭环",
                 11.5, SUB))

    # 三列坐标：左=数据源，中=数仓，右=中台
    c1x, c1w = 28, 158
    c2x, c2w = 222, 386          # 222 ~ 608
    c3x, c3w = 678, 294          # 678 ~ 972，中间留 70px 走线通道
    y0, bh, gap = 88, 76, 38
    rows = [y0 + i * (bh + gap) for i in range(5)]
    wire = c2x + 78              # 数仓列内部的竖向走线

    def box(x, w, y, title, subs, colors, tsize=14):
        fill, stroke = colors
        out = [rect(x, y, w, bh, fill, stroke)]
        out.append(txt(x + 16, y + 29, title, tsize, INK, "600"))
        for i, s in enumerate(subs):
            out.append(txt(x + 16, y + 50 + i * 16, s, 10.5, SUB))
        return "".join(out)

    # ---- 第 1 列：数据来源 ----
    p.append(box(c1x, c1w, rows[0], "合成数据",
                 ["固定种子 · 31 天", "5 渠道 · 1,000 素材"], C_SRC))

    # ---- 第 2 列：五层数仓 ----
    wh = [
        ("ODS 原样落地 · 故意注入 0.5% 重复",
         ["ods_iaa_user_daily / ods_iaa_acquisition_daily"]),
        ("DWD 清洗 · 5 条规则 + 首次点击归因",
         ["dwd_user_daily · 入 − 拦 − 出 必须严格相等"]),
        ("买量事实表 · 与变现侧拆表",
         ["dwd_acquisition_daily · 花费 / 安装"]),
        ("DWS cohort 宽表 D0~D7 + 3 张全量表",
         ["dws_cohort_daily · 长表 pivot 成宽表"]),
        ("ADS 三表 · 78 列 · 物化 parquet",
         ["daily_detail 13,981 · creative 6,231 · sub_channel 341"]),
    ]
    for y, (t, s) in zip(rows, wh):
        p.append(box(c2x, c2w, y, t, s, C_WH))

    # 层间连线 + 门禁徽标：徽标放在层与层之间的空隙里，正好对应「出口检查」
    gates = ["门禁 5 条 · 不过即停", "门禁 3 条 · 不过即停",
             "门禁 2 条 · 不过即停", "门禁 12 条 · 不过即停"]
    for i, label in enumerate(gates):
        top, bot = rows[i] + bh, rows[i + 1]
        p.append(arrow(wire, top, wire, bot - 3))
        gy = (top + bot) // 2 - 11
        p.append(rect(wire + 16, gy, 168, 22, C_GATE[0], C_GATE[1], r=11, sw=1.2))
        p.append(txt(wire + 26, gy + 15, label, 10, "#8a5200", "600"))

    # 数据源 → ODS
    p.append(arrow(c1x + c1w + 2, rows[0] + bh / 2, c2x - 4, rows[0] + bh / 2))

    # ---- 第 3 列：中台 ----
    api = [
        ("warehouse_api · 唯一取数入口",
         ["parquet 5ms / hive 2s", "业务代码不碰文件路径"], C_API),
        ("LLM 快照 build_snapshot",
         ["成熟度过滤 · NaN→null", "上次结论 · 显式「不知道」"], C_API),
        ("Agent 多轮 Function Calling",
         ["6 个只读工具 · 写操作不在工具表里", "判定 → decision_log → 回灌数仓"], C_AGENT),
        ("护栏 + 人工批准",
         ["fail-closed · 提议者 ≠ 批准者", "批准时按当前配置重算（TOCTOU）"], C_GUARD),
        ("平台执行",
         ["预算 / 目标 / 删除"], C_SRC),
    ]
    for y, (t, s, c) in zip(rows, api):
        p.append(box(c3x, c3w, y, t, s, c, 13.5))

    c3wire = c3x + c3w / 2
    for i in range(4):
        p.append(arrow(c3wire, rows[i] + bh, c3wire, rows[i + 1] - 4))

    # ADS → warehouse_api：走两列之间的通道上去，再从 api 底部进入。
    # 直接画一条从左下到右上的斜线会穿过别的框，正交折线更好读。
    chan = c2x + c2w + 28          # 636：两列之间的走线通道
    y_land = rows[1] - 24          # 178：api 与第二位之间的空隙
    p.append(line(c2x + c2w + 2, rows[4] + bh / 2, chan, rows[4] + bh / 2))
    p.append(line(chan, rows[4] + bh / 2, chan, y_land))
    p.append(line(chan, y_land, 745, y_land))
    p.append(arrow(745, y_land, 745, rows[0] + bh + 3))

    # ---- 底部三块证据 ----
    chips = [
        ("3 次快照 2 次比对", "同一天重跑，10 张表逐行相同"),
        ("35 条断言", "22 指标链路 + 13 日志链路，FAIL 即停"),
        ("13 场景 × 3 次", "safety 判断层 4/8 → 生效层 7/8"),
    ]
    cw, chh, gapc = 300, 56, 24
    for i, (big, small) in enumerate(chips):
        x = 28 + i * (cw + gapc)
        p.append(rect(x, 648, cw, chh, C_CHIP[0], C_CHIP[1], r=8, sw=1.2))
        p.append(txt(x + 16, 674, big, 13.5, INK, "700"))
        p.append(txt(x + 16, 693, small, 10, SUB))

    p.append("</svg>")
    return "\n".join(p)


# ============================================================
# 图 2：评测——判断层 vs 生效层
# ============================================================

def eval_chart() -> str:
    W, H = 1000, 372
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="判断层与生效层的 safety 对比">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>', head()]

    p.append(txt(28, 34, "safety 通过率：判断层 vs 生效层", 17, INK, "700"))
    p.append(txt(28, 56, "13 条场景 × --repeat 3 取最坏（不是单次跑的数字）", 11, SUB))

    p.append(rect(28, 70, 12, 12, "#94a3b8", "#94a3b8", r=3, sw=0))
    p.append(txt(46, 80, "判断层：Agent 自己想出来的动作对不对", 10.5, SUB))
    p.append(rect(272, 70, 12, 12, "#0e9f6e", "#0e9f6e", r=3, sw=0))
    p.append(txt(290, 80, "生效层：过完护栏后有多少真会被执行", 10.5, SUB))

    x0, xmax = 252, 890
    groups = [
        ("全量 8 条", "13 条里可判 safety 的", 8, 4, 7),
        ("dev 6 条", "可看着结果调参", 6, 4, 6),
        ("holdout 2 条", "没见过，只验证", 2, 0, 1),
    ]

    top, block = 104, 80
    for gi, (label, note, total, judge, eff) in enumerate(groups):
        gy = top + gi * block
        p.append(txt(28, gy + 26, label, 12.5, INK, "600"))
        p.append(txt(28, gy + 44, note, 9.5, SUB))
        for bi, (val, color, name) in enumerate(
                [(judge, "#94a3b8", "判断层"), (eff, "#0e9f6e", "生效层")]):
            by = gy + 6 + bi * 30
            p.append(rect(x0, by, xmax - x0, 20, "#f1f5f9", "#e2e8f0", r=5, sw=1))
            w = (xmax - x0) * val / total
            if w > 0:
                p.append(rect(x0, by, max(w, 3), 20, color, color, r=5, sw=0))
            p.append(txt(x0 - 12, by + 15, name, 10, SUB, "400", anchor="end"))
            p.append(txt(x0 + w + 10, by + 15, f"{val}/{total}", 11,
                         INK if val else "#b91c1c", "700"))

    p.append(txt(28, 352,
                 "生效层涨、判断层不变 —— 护栏买到的是「不出事」，不是「变聪明」；"
                 "过度拦截 0 次（健康删除照常放行）", 10.5, SUB))
    p.append("</svg>")
    return "\n".join(p)


if __name__ == "__main__":
    for name, fn in [("architecture.svg", architecture),
                     ("eval-layers.svg", eval_chart)]:
        (OUT / name).write_text(fn(), encoding="utf-8")
        print(f"wrote {OUT / name}")
