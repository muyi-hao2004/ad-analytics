# -*- coding: utf-8 -*-
"""
生成杨增浩的求职简历 Word 文档
定位：数据工程 / 数据开发（偏工程），AI 应用（数仓 + RAG + Agent）作为项目亮点
"""
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

doc = Document()

# ---------- 全局字体 ----------
style = doc.styles['Normal']
style.font.name = 'Calibri'
style.font.size = Pt(10.5)
style.element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')

# 页面边距
for section in doc.sections:
    section.top_margin = Cm(1.6)
    section.bottom_margin = Cm(1.6)
    section.left_margin = Cm(1.8)
    section.right_margin = Cm(1.8)

# ---------- 工具函数 ----------
def set_run(run, size=10.5, bold=False, color=None, name_east='微软雅黑'):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = 'Calibri'
    run._element.rPr.rFonts.set(qn('w:eastAsia'), name_east)
    if color:
        run.font.color.rgb = RGBColor(*color)

def add_heading(text, size=12, color=(0x1F, 0x3A, 0x5F)):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    set_run(r, size=size, bold=True, color=color)
    # 底部横线
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '8')
    bottom.set(qn('w:space'), '2')
    bottom.set(qn('w:color'), '1F3A5F')
    pBdr.append(bottom)
    pPr.append(pBdr)
    return p

def add_body(text, size=10.5, bold=False, color=None, indent=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.15
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    set_run(r, size=size, bold=bold, color=color)
    return p

def add_bullet(text, size=10.5):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.left_indent = Cm(0.5)
    r = p.add_run('· ' + text)
    set_run(r, size=size)
    return p

def add_item_line(left, right, left_bold=True):
    """一行左标题右时间，如 '项目名称        2025.03 - 2026.06'"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.15
    r1 = p.add_run(left)
    set_run(r1, size=10.5, bold=left_bold)
    # 用 tab 右对齐时间
    r2 = p.add_run('\t' + right)
    set_run(r2, size=10.5, color=(0x60, 0x60, 0x60))
    return p

# ============================================================
# 姓名 + 联系方式
# ============================================================
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(2)
r = p.add_run('杨增浩')
set_run(r, size=20, bold=True, color=(0x1F, 0x3A, 0x5F))

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(4)
r = p.add_run('+86 187-1949-8784  |  muyi433298@qq.com  |  深圳市')
set_run(r, size=9.5, color=(0x40, 0x40, 0x40))

# 求职意向
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(2)
r = p.add_run('求职意向：数据工程 / 数据开发')
set_run(r, size=10, bold=True)

# ============================================================
# 教育经历
# ============================================================
add_heading('教育经历')
add_item_line('深圳大学  数学与应用数学  本科', '2022.09 - 2026.07')
add_bullet('主修课程：数学分析、高等代数、概率论与数理统计、运筹学、数值分析、数学建模、Python、Matlab')
add_bullet('英语六级')

# ============================================================
# 实习经历
# ============================================================
add_heading('实习经历')

add_item_line('深圳哇哇鱼网络科技  海外广告投放数据分析（IAA / IAP）', '2026.06 - 2026.09')
add_bullet('参与海外休闲游戏 IAA（应用内广告）与 IAP（应用内购买 / 订阅）变现数据分析，负责广告投放及收入数据的整理、指标核算与归因分析，熟悉 CPA / CTR / CPC / ROI / ARPU 等指标口径')
add_bullet('对投放渠道、素材、广告位等维度进行日常数据统计与效果归因，辅助定位投放 ROI 波动与变现效率变化的原因')
add_bullet('参与公司广告投放 Agent 的测试与效果评估，基于投放数据反馈 Agent 决策的准确性问题，理解「数据分析 + 大模型智能投放」的落地难点（如效果不稳定、需人工兜底）')

add_item_line('金舟软件（江下信息科技）  数据助理（实习）', '2026.04 - 2026.05')
add_bullet('公司为办公效率类工具软件厂商（录屏、PDF、文件压缩、数据恢复等产品）。使用 Python 对软件使用日志进行清洗与规范化（去重、缺失值处理、字段格式统一），并配合 SQL 完成按产品、版本、时间维度的基础统计与汇总，整理成日报表供团队参考')

# ============================================================
# 项目经历（核心）
# ============================================================
add_heading('项目经历')

add_item_line('广告投放数据分析系统（个人项目，GitHub：muyi-hao2004/ad-analytics）', '2026.03 至今')
add_bullet('面向海外 IAA 广告变现场景，构建从数据接入、数仓分层到报表与 AI 分析决策的完整数据分析系统')
add_bullet('使用 Pandas / PySpark 完成数据清洗与聚合，设计 ODS/DWD/DWS/ADS 四层数仓，落地双轴事实表（install_date / report_date）建模，沉淀原子度量列支撑 ROI、留存、eCPM 等派生指标按需计算')
add_bullet('实现 D0-D7 全程 cohort 指标计算与动态 GROUP BY 聚合，用 MySQL 存储明细与汇总数据，编写 SQL 完成窗口函数、多表 JOIN 等查询')
add_bullet('使用 FastAPI 将指标计算、异常检测等能力封装为接口，配合语义层（metrics.yaml）统一指标口径')
add_bullet('基于 DeepSeek + ChromaDB 构建广告领域 RAG 知识库问答，用 Function Calling 实现多轮对话 Agent，通过 MCP 将 9 个数据工具（读指标 / 查日志 / 改预算等）封装为标准工具服务；配合快照（snapshot）压缩数仓结果供 LLM 决策，理解语义层与 RAG 的「硬口径 / 软经验」分工')
add_bullet('使用 Git 版本管理，了解 Airflow 任务调度与 Docker 容器化的基本用法')

# ============================================================
# 科研与竞赛
# ============================================================
add_heading('科研与竞赛')

add_item_line('论文《人工智能驱动的金融数据分析与企业财务风险预警研究》（独立完成）', '2026.03 - 2026.06')
add_bullet('基于 UCI 波兰公司破产数据集（7,027 样本 / 64 财务比率特征），对比逻辑回归、随机森林、XGBoost 等模型，结合 SMOTE 处理类别不平衡，并用 SHAP 解释模型，XGBoost 取得 AUC 0.9727，论文已被 CSEIT 2026 接收')

add_item_line('论文《基于分裂复零因子分解法的牛顿-莱布尼茨公式》', '2024.11')
add_bullet('数学方向研究，已发表 CPCI 论文')

add_item_line('全国大学生数学建模竞赛  广东省二等奖（建模手）', '2024.09 - 2024.12')
add_bullet('针对生产决策问题，完成数据预处理与参数标定，实现动态规划状态转移方程与遗传算法求解器，设计多组对照实验量化各因素对利润的影响')

# ============================================================
# 技能
# ============================================================
add_heading('技能')
add_bullet('编程语言与数据处理：Python、SQL、Pandas、MySQL、NumPy、PySpark')
add_bullet('数据工程体系：四层数仓建模（ODS/DWD/DWS/ADS）、ETL、Airflow、FastAPI；Hive / HDFS、Kafka / Flink 流计算等大数据组件')
add_bullet('机器学习与大模型应用：分类 / 回归 / 聚类等经典算法、特征工程、RAG（ChromaDB）、LLM API、Function Calling / Agent、MCP、Prompt 工程')
add_bullet('其他：Git / GitHub、Docker；英语六级、计算机二级')

doc.save('C:/Users/Mia/ad-analytics/杨增浩_简历_数据工程.docx')
print('已生成：C:/Users/Mia/ad-analytics/杨增浩_简历_数据工程.docx')
