# -*- coding: utf-8 -*-
"""
把 docs/notes/ 下的全部笔记构建成：
  1) 一个自包含的单文件 HTML（侧边栏导航 + 全文搜索 + 暗色模式 + 打印友好）
  2) 一份 .md 原文件副本

用途：桌面随时查看。笔记更新后重跑本脚本即可。

用法:
    python build_notes_html.py
    python build_notes_html.py --src <笔记目录> --out <输出目录>
"""

import argparse
import datetime
import io
import os
import re
import shutil

import markdown

# 分区分组：(组名, 起始编号, 结束编号)
GROUPS = [
    ("总览", 0, 0),
    ("第一层 · 基础", 1, 3),
    ("第二层 · 数据处理", 4, 8),
    ("第三层 · 数据工程", 9, 12),
    ("第四层 · 后端工程", 13, 13),
    ("第五层 · AI", 14, 18),
    ("第六层 · 业务与职业", 19, 22),
    ("第六层 · 评测与方法论", 24, 25),
    ("附录 · 时效层", 23, 23),
    ("附录 · 面试材料", 26, 31),
]

# 额外收录：不在 docs/notes 下、但需要一起看的文件
# (编号, 绝对路径, 侧边栏说明)
EXTRAS = [
    (
        26,
        r"C:/Users/Mia/WorkBuddy/2026-10-09-11-13-51/interview_prep/港城东莞_DS_AI面试准备手册.md",
        "港城莞 AI 面试：自我介绍、16 题行为题、动机题、风险题、AI 行业题",
    ),
    (
        27,
        r"C:/Users/Mia/WorkBuddy/2026-10-09-11-13-51/interview_prep/CV逐段口述稿.md",
        "CV 九个板块各一段话：教育、两篇论文、毕业论文、项目、两段实习、竞赛、技能",
    ),
    (
        28,
        r"C:/Users/Mia/WorkBuddy/2026-10-09-11-13-51/interview_prep/CV中文精读.md",
        "中文带读整份 CV：三块资产 + 两块证据、逐栏精读、四个结构性弱点",
    ),
    (
        29,
        r"C:/Users/Mia/WorkBuddy/2026-10-09-11-13-51/interview_prep/论文与项目全流程详解.md",
        "两篇论文 + 毕业论文 + 项目：全流程、参数、结果表、追问与答法",
    ),
    (
        30,
        r"C:/Users/Mia/WorkBuddy/2026-10-09-11-13-51/interview_prep/AI面试_简洁中英对照版.md",
        "AI 面试临场稿：12 段各 40 秒左右，逐句中英对照，术语压到 0-2 个",
    ),
    (
        31,
        r"C:/Users/Mia/WorkBuddy/2026-10-09-11-13-51/interview_prep/AI面试_题库作答版.md",
        "按真题库作答：5 分钟 4-5 题、12 道高频题中英对照、13 道一句话映射",
    ),
]

# 各篇一句话说明（侧边栏悬浮提示 / 搜索结果副标题）
BLURBS = {
    0: "整套笔记的入口：结构总览、阅读路径、旧笔记映射表",
    1: "函数与 return、面向对象、装饰器、异常处理、推导式、生成器、with",
    2: "文件系统与权限、进程与日志、管道重定向、vim、ssh、crontab、Shell",
    3: "镜像/容器/仓库、Dockerfile、compose、Git",
    4: "主键外键索引、执行顺序、JOIN、窗口函数三大类、CASE WHEN",
    5: "按题型分类的 SQL 练习与标准答案、易错点清单",
    6: "read_csv 参数、groupby/agg/shift/rolling/rank、缺失值、pandas vs SQL",
    7: "SparkSession、读写、窗口函数、partitionBy、格式对比、错题清单",
    8: "宽窄依赖、Shuffle、数据倾斜五种解法、Streaming vs Flink",
    9: "四层数仓、维度建模、双轴事实表、原子度量、SCD2、拉链表、幂等",
    10: "HDFS/YARN/MapReduce、Hive 是翻译官、分区=目录、分区 vs 分桶",
    11: "行式 vs 列式、格式选型、压缩、OLTP vs OLAP、OLAP 引擎",
    12: "Airflow 全组件、DAG、backfill、Kafka 分区与消费组、Redis",
    13: "库 vs SDK vs API、HTTP、RESTful、FastAPI、asyncio、lifespan",
    14: "万能逼近、反向传播、优化器、过拟合与正则、搭几层怎么定",
    15: "Q/K/V、多头、BERT vs GPT、采样、训练三阶段、KV Cache、MoE、蒸馏",
    16: "完整链路、切分、Embedding 选型、混合检索、相似度阈值、rerank",
    17: "决策 vs 生成、原生 tools、role=tool、工具描述、语义层、MCP",
    18: "Token 成本构成、Prompt Caching、快照设计、省 token 的正确顺序",
    19: "IAA vs IAP、买量 vs 变现、指标全表、ROAS、归因、10 类口径陷阱",
    20: "六步流水线、双轴事实表、四层数仓、Airflow 踩坑、10 个高频追问",
    21: "中美竞争结构、代际反超、蒸馏争议、开源格局、选型方法论",
    22: "三种角色、MLOps、训练 vs 推理、求职话术、港城莞面试 16 题",
    23: "截至 2026-10-19 的模型快照与价格、六桶事实、四条主线、面试答法",
    24: "评测的定义与三处分野、内生 label、dev/holdout 与泄漏、判断层 vs 生效层、过度拦截、检索评测",
    25: "四层知识地图：存储、评测、Agent 工程、训练数据；一条贯穿线是「定义什么算对」",
    26: "1 分钟自我介绍、16 题行为题、4 题动机、5 题风险、AI 行业题、训练日程",
    27: "CV 九个板块各一段话（40-55 秒）：教育、两篇论文、毕业论文、项目、两段实习、竞赛、技能",
    28: "中文带读 CV：三块资产 + 两块证据、逐栏「写了什么/为什么存在/他在验证什么/必答三问」、四个结构性弱点",
    29: "四份材料的全流程详解：ML 论文八步、国赛四问、毕业论文 12 定理、项目四层数仓 + Agent",
    30: "AI 面试临场稿（中英逐句对照）：12 段、每段 26-51 秒、镜头前只背第一句和数字",
    31: "以真题库为准：5 分钟 4-5 题、答题太长会被打断、12 题全文 + 13 题映射 + 听力辨题表",
}


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def collect(src):
    """返回 [{num, file, path, title, md, blurb}]，按编号排序。"""
    docs = {}

    def load(num, path, blurb=None, name=None):
        if not os.path.exists(path):
            print("跳过（文件不存在）: %s" % path)
            return
        text = read_text(path)
        title = name or os.path.basename(path)
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        if num in docs:
            raise RuntimeError(
                "编号冲突：%d 已被 %s 占用，现在又要装 %s。"
                "请调整 EXTRAS 编号或正文篇号，否则会静默覆盖、桌面版缺篇。"
                % (num, docs[num]["file"], os.path.basename(path))
            )
        docs[num] = {
            "num": num,
            "file": name or os.path.basename(path),
            "path": path,
            "title": title,
            "md": text,
            "blurb": blurb if blurb is not None else BLURBS.get(num, ""),
        }

    for name in sorted(os.listdir(src)):
        if not name.lower().endswith(".md"):
            continue
        m = re.match(r"^(\d{2})_", name)
        if not m:
            continue
        load(int(m.group(1)), os.path.join(src, name))

    # 外部文件（面试材料等）：桌面副本加编号前缀，与其余 md 排序一致
    for num, path, blurb in EXTRAS:
        load(num, path, blurb, name="%02d_%s" % (num, os.path.basename(path)))

    return [docs[k] for k in sorted(docs)]


def convert(docs):
    """markdown -> html，处理 id 前缀与 .md 内部链接重写。"""
    file2id = {d["file"]: "doc-%02d" % d["num"] for d in docs}

    for d in docs:
        md = markdown.Markdown(
            extensions=["tables", "fenced_code", "toc", "sane_lists"],
            extension_configs={"toc": {"permalink": False}},
        )
        html = md.convert(d["md"])
        prefix = "doc-%02d" % d["num"]

        # 标题 id 加前缀，避免跨文件重名
        html = re.sub(
            r'<h([1-6]) id="([^"]+)"',
            lambda m: '<h%s id="%s-%s"' % (m.group(1), prefix, m.group(2)),
            html,
        )
        # 标题加可点击锚点
        html = re.sub(
            r'<h([2-4]) id="([^"]+)">(.*?)</h\1>',
            lambda m: '<h%s id="%s">%s<a class="anchor" href="#%s" aria-hidden="true">#</a></h%s>'
            % (m.group(1), m.group(2), m.group(3), m.group(2), m.group(1)),
            html,
            flags=re.S,
        )
        # 内部 .md 链接改成本页锚点
        def fix_link(m):
            target = m.group(1).split("/")[-1]
            if target in file2id:
                return 'href="#%s"' % file2id[target]
            return m.group(0)

        html = re.sub(r'href="([^"]*\.md)"', fix_link, html)
        d["html"] = html
        d["headings"] = md.toc_tokens


CSS = r"""
:root{
  --bg:#ffffff; --panel:#f6f7f9; --panel2:#eef0f3; --text:#1b1f24; --muted:#6b7280;
  --line:#e3e6ea; --accent:#2563eb; --accent-soft:#eaf0fe; --code-bg:#f4f6f8;
  --th-bg:#f0f2f5; --shadow:0 1px 3px rgba(0,0,0,.06);
}
html[data-theme="dark"]{
  --bg:#15181d; --panel:#1b1f26; --panel2:#22272f; --text:#e4e7eb; --muted:#98a1ad;
  --line:#2b313a; --accent:#7aa7ff; --accent-soft:#1e2a3f; --code-bg:#1d222a;
  --th-bg:#232830; --shadow:0 1px 3px rgba(0,0,0,.4);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB","Source Han Sans SC",
    -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  font-size:15.5px; line-height:1.78; -webkit-font-smoothing:antialiased;
}
/* ---------- 顶栏 ---------- */
.topbar{
  position:fixed; top:0; left:0; right:0; height:56px; z-index:60;
  display:flex; align-items:center; gap:12px; padding:0 18px;
  background:var(--bg); border-bottom:1px solid var(--line);
}
.brand{font-weight:650; font-size:15px; letter-spacing:.2px; white-space:nowrap}
.brand span{color:var(--muted); font-weight:400; font-size:12.5px; margin-left:8px}
.search-wrap{position:relative; flex:1; max-width:460px; margin-left:8px}
.search-wrap input{
  width:100%; height:34px; padding:0 34px 0 12px; font-size:13.5px;
  color:var(--text); background:var(--panel); border:1px solid var(--line);
  border-radius:8px; outline:none; font-family:inherit;
}
.search-wrap input:focus{border-color:var(--accent); background:var(--bg)}
.search-wrap kbd{
  position:absolute; right:9px; top:8px; font-size:11px; color:var(--muted);
  border:1px solid var(--line); border-radius:4px; padding:1px 5px; background:var(--bg);
}
.results{
  position:absolute; top:40px; left:0; right:0; max-height:65vh; overflow:auto;
  background:var(--bg); border:1px solid var(--line); border-radius:10px;
  box-shadow:0 10px 34px rgba(0,0,0,.14); display:none; z-index:70;
}
.results.open{display:block}
.results .r{
  display:block; padding:9px 13px; border-bottom:1px solid var(--line);
  text-decoration:none; color:var(--text); font-size:13.5px;
}
.results .r:last-child{border-bottom:0}
.results .r:hover,.results .r.sel{background:var(--accent-soft)}
.results .r b{color:var(--accent); font-weight:650}
.results .r small{display:block; color:var(--muted); font-size:11.5px; margin-top:1px}
.results .empty{padding:14px; color:var(--muted); font-size:13px}
.btn{
  height:32px; padding:0 11px; font-size:13px; font-family:inherit; cursor:pointer;
  color:var(--text); background:var(--panel); border:1px solid var(--line);
  border-radius:7px; white-space:nowrap;
}
.btn:hover{background:var(--panel2)}
#menuBtn{display:none}
/* ---------- 侧边栏 ---------- */
.side{
  position:fixed; top:56px; bottom:0; left:0; width:274px; overflow-y:auto;
  background:var(--panel); border-right:1px solid var(--line); padding:14px 0 40px; z-index:50;
}
.side::-webkit-scrollbar{width:8px}
.side::-webkit-scrollbar-thumb{background:var(--line); border-radius:4px}
.grp{padding:14px 18px 5px; font-size:11px; font-weight:700; letter-spacing:.9px;
  color:var(--muted); text-transform:uppercase}
.side a.item{
  display:flex; gap:9px; align-items:baseline; padding:6px 18px 6px 16px;
  color:var(--text); text-decoration:none; font-size:13.4px; line-height:1.45;
  border-left:2px solid transparent;
}
.side a.item:hover{background:var(--panel2)}
.side a.item.active{background:var(--accent-soft); border-left-color:var(--accent); color:var(--accent); font-weight:600}
.side a.item .n{color:var(--muted); font-size:11.5px; font-variant-numeric:tabular-nums; min-width:17px}
.side a.item.active .n{color:var(--accent)}
/* ---------- 正文 ---------- */
.main{margin-left:274px; padding:56px 0 120px}
.doc{max-width:880px; margin:0 auto; padding:34px 40px 10px; scroll-margin-top:56px}
.doc h1,.doc h2,.doc h3,.doc h4{scroll-margin-top:72px}
.doc+.doc{border-top:1px solid var(--line); margin-top:20px; padding-top:48px}
.doc h1{
  font-size:27px; line-height:1.35; margin:0 0 20px; letter-spacing:-.3px;
  padding-bottom:14px; border-bottom:2px solid var(--line);
}
.doc h2{font-size:20px; margin:36px 0 12px; padding-top:6px; letter-spacing:-.2px}
.doc h3{font-size:16.5px; margin:26px 0 9px}
.doc h4{font-size:15px; margin:20px 0 7px; color:var(--muted)}
.anchor{
  margin-left:8px; color:var(--line); text-decoration:none; font-weight:400;
  opacity:0; transition:opacity .15s; font-size:.85em;
}
.doc h2:hover .anchor,.doc h3:hover .anchor,.doc h4:hover .anchor{opacity:1}
.anchor:hover{color:var(--accent)}
.doc p{margin:11px 0}
.doc a{color:var(--accent); text-decoration:none; border-bottom:1px solid transparent}
.doc a:hover{border-bottom-color:var(--accent)}
.doc strong{font-weight:650}
.doc ul,.doc ol{margin:10px 0; padding-left:24px}
.doc li{margin:5px 0}
.doc li>ul,.doc li>ol{margin:5px 0}
.doc blockquote{
  margin:16px 0; padding:11px 17px; border-left:3px solid var(--accent);
  background:var(--panel); color:var(--text); border-radius:0 7px 7px 0;
}
.doc blockquote p{margin:6px 0}
.doc hr{border:0; border-top:1px solid var(--line); margin:30px 0}
.doc code{
  font-family:"JetBrains Mono","Cascadia Mono",Consolas,"Courier New",monospace;
  font-size:.875em; background:var(--code-bg); padding:1.5px 5px; border-radius:4px;
  border:1px solid var(--line);
}
.doc pre{
  background:var(--code-bg); border:1px solid var(--line); border-radius:9px;
  padding:14px 16px; overflow-x:auto; margin:15px 0; line-height:1.62;
}
.doc pre code{background:none; border:0; padding:0; font-size:12.9px}
.table-wrap{overflow-x:auto; margin:16px 0; border:1px solid var(--line); border-radius:9px}
.doc table{border-collapse:collapse; width:100%; font-size:13.6px; line-height:1.62}
.doc th,.doc td{border-bottom:1px solid var(--line); padding:8px 12px; text-align:left; vertical-align:top}
.doc th{background:var(--th-bg); font-weight:650; white-space:nowrap}
.doc tr:last-child td{border-bottom:0}
.doc tbody tr:hover{background:var(--panel)}
.doc em{color:var(--muted)}
mark.hit{background:#ffe58a; color:#111; padding:0 2px; border-radius:2px}
html[data-theme="dark"] mark.hit{background:#7a5c00; color:#fff}
/* ---------- 其他 ---------- */
#toTop{
  position:fixed; right:22px; bottom:22px; width:40px; height:40px; border-radius:50%;
  background:var(--bg); border:1px solid var(--line); color:var(--muted); cursor:pointer;
  font-size:16px; box-shadow:var(--shadow); display:none; z-index:55;
}
#toTop.show{display:block}
.foot{max-width:880px; margin:40px auto 0; padding:24px 40px; color:var(--muted);
  font-size:12.5px; border-top:1px solid var(--line); text-align:center}
.mask{display:none; position:fixed; inset:56px 0 0; background:rgba(0,0,0,.35); z-index:45}
@media (max-width:980px){
  #menuBtn{display:inline-block}
  .side{transform:translateX(-100%); transition:transform .22s ease; box-shadow:var(--shadow)}
  .side.open{transform:none}
  .main{margin-left:0}
  .mask.show{display:block}
  .doc{padding:24px 18px 10px}
  .search-wrap{max-width:none}
  .brand span{display:none}
}
@media print{
  .topbar,.side,.mask,#toTop{display:none!important}
  .main{margin:0; padding:0}
  .doc{max-width:none; padding:0; page-break-after:always}
  .doc+.doc{margin-top:0}
  a{color:inherit}
}
"""

JS = r"""
(function(){
  var qs=function(s,r){return (r||document).querySelector(s)};
  var qsa=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};

  /* 主题 */
  var saved=localStorage.getItem('notes-theme');
  if(saved) document.documentElement.setAttribute('data-theme',saved);
  qs('#themeBtn').onclick=function(){
    var cur=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
    document.documentElement.setAttribute('data-theme',cur);
    localStorage.setItem('notes-theme',cur);
  };

  /* 移动端抽屉 */
  var side=qs('#side'),mask=qs('#mask');
  qs('#menuBtn').onclick=function(){side.classList.toggle('open');mask.classList.toggle('show')};
  mask.onclick=function(){side.classList.remove('open');mask.classList.remove('show')};
  qsa('.side a.item').forEach(function(a){a.addEventListener('click',function(){
    if(innerWidth<980){side.classList.remove('open');mask.classList.remove('show')}
  })});

  /* 表格包一层，便于横向滚动 */
  qsa('.doc table').forEach(function(t){
    var w=document.createElement('div'); w.className='table-wrap';
    t.parentNode.insertBefore(w,t); w.appendChild(t);
  });

  /* 搜索索引：文档标题 + h2/h3/h4 */
  var idx=[];
  qsa('.doc').forEach(function(doc){
    var dt=qs('h1',doc).textContent.trim(), did=doc.id;
    idx.push({doc:did,title:dt,text:dt,sub:'篇目',anchor:did});
    qsa('h2,h3,h4',doc).forEach(function(h){
      var t=h.textContent.replace(/#$/,'').trim();
      idx.push({doc:did,title:dt,text:t,sub:dt,anchor:h.id});
    });
  });

  var input=qs('#q'),box=qs('#results'),sel=-1,hits=[];
  function render(){
    var v=input.value.trim().toLowerCase();
    if(v.length<1){box.classList.remove('open');box.innerHTML='';hits=[];return}
    hits=idx.filter(function(o){return o.text.toLowerCase().indexOf(v)>-1});
    if(!hits.length){box.innerHTML='<div class="empty">没有匹配的标题，换个关键词试试</div>';box.classList.add('open');return}
    hits=hits.slice(0,40); sel=-1;
    box.innerHTML=hits.map(function(o,i){
      var t=o.text.replace(new RegExp('('+v.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<b>$1</b>');
      return '<a class="r" data-i="'+i+'" href="#'+o.anchor+'"><b style="color:inherit">'+t+
             '</b><small>'+o.sub+'</small></a>';
    }).join('');
    box.classList.add('open');
    qsa('.r',box).forEach(function(a){
      a.onclick=function(){box.classList.remove('open');input.blur()};
    });
  }
  input.addEventListener('input',render);
  input.addEventListener('focus',render);
  input.addEventListener('keydown',function(e){
    var rs=qsa('.r',box);
    if(e.key==='Escape'){box.classList.remove('open');input.blur();return}
    if(!rs.length) return;
    if(e.key==='ArrowDown'){e.preventDefault();sel=Math.min(sel+1,rs.length-1)}
    else if(e.key==='ArrowUp'){e.preventDefault();sel=Math.max(sel-1,0)}
    else if(e.key==='Enter'){
      e.preventDefault();
      if(sel<0) sel=0;
      rs[sel].click(); location.hash=rs[sel].getAttribute('href');
      box.classList.remove('open');
      return;
    } else return;
    rs.forEach(function(r){r.classList.remove('sel')});
    if(rs[sel]){rs[sel].classList.add('sel'); rs[sel].scrollIntoView({block:'nearest'})}
  });
  document.addEventListener('click',function(e){
    if(!e.target.closest('.search-wrap')) box.classList.remove('open');
  });
  document.addEventListener('keydown',function(e){
    if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();input.focus();input.select()}
    if(e.key==='/'&&document.activeElement!==input){e.preventDefault();input.focus()}
  });

  /* 侧边栏高亮当前篇 */
  var links={};
  qsa('.side a.item').forEach(function(a){links[a.getAttribute('href').slice(1)]=a});
  var io=new IntersectionObserver(function(es){
    es.forEach(function(en){
      if(!en.isIntersecting) return;
      var a=links[en.target.id];
      if(!a) return;
      qsa('.side a.item.active').forEach(function(x){x.classList.remove('active')});
      a.classList.add('active');
      /* 只滚动侧边栏自身，避免带动整页跳动 */
      var r=a.getBoundingClientRect(),sr=side.getBoundingClientRect();
      if(r.top<sr.top+48) side.scrollTop-=(sr.top+48-r.top);
      else if(r.bottom>sr.bottom-48) side.scrollTop+=(r.bottom-(sr.bottom-48));
    });
  },{rootMargin:'-56px 0px -70% 0px'});
  qsa('.doc').forEach(function(d){io.observe(d)});

  /* 回到顶部 */
  var top=qs('#toTop');
  addEventListener('scroll',function(){top.classList.toggle('show',scrollY>600)},{passive:true});
  top.onclick=function(){scrollTo({top:0,behavior:'smooth'})};
})();
"""


def build_html(docs, out_path):
    today = datetime.date.today().strftime("%Y-%m-%d")

    nav = []
    total_chars = 0
    for gname, lo, hi in GROUPS:
        group_docs = [d for d in docs if lo <= d["num"] <= hi]
        if not group_docs:
            continue
        nav.append('<div class="grp">%s</div>' % gname)
        for d in group_docs:
            total_chars += len(d["md"])
            nav.append(
                '<a class="item" href="#doc-%02d" title="%s"><span class="n">%02d</span><span>%s</span></a>'
                % (d["num"], (d["blurb"] or d["title"]).replace('"', "&quot;"), d["num"], d["title"])
            )

    body = []
    for d in docs:
        body.append(
            '<section class="doc" id="doc-%02d" data-title="%s">%s</section>'
            % (d["num"], d["title"].replace('"', "&quot;"), d["html"])
        )

    html = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据工程与 AI 笔记</title>
<style>%s</style>
</head>
<body>
<header class="topbar">
  <button class="btn" id="menuBtn">☰</button>
  <div class="brand">数据工程与 AI 笔记<span>%d 篇 · %s</span></div>
  <div class="search-wrap">
    <input id="q" type="search" placeholder="搜索标题或小节（按 / 或 Ctrl+K）" autocomplete="off">
    <kbd>Ctrl K</kbd>
    <div class="results" id="results"></div>
  </div>
  <button class="btn" id="themeBtn">◐ 主题</button>
</header>

<nav class="side" id="side">%s</nav>
<div class="mask" id="mask"></div>

<main class="main">%s
<div class="foot">共 %d 篇 · 约 %s 字 · 构建于 %s<br>笔记源目录：%s</div>
</main>

<button id="toTop" title="回到顶部">↑</button>
<script>%s</script>
</body>
</html>""" % (
        CSS,
        len(docs),
        today,
        "\n".join(nav),
        "\n".join(body),
        len(docs),
        "{:,}".format(total_chars),
        today,
        "ad-analytics/docs/notes",
        JS,
    )

    with io.open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return len(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=r"C:/Users/Mia/ad-analytics/docs/notes")
    ap.add_argument("--out", default=r"C:/Users/Mia/Desktop/数据工程笔记")
    args = ap.parse_args()

    docs = collect(args.src)
    if not docs:
        raise SystemExit("没有找到笔记文件：%s" % args.src)

    os.makedirs(args.out, exist_ok=True)

    # 1) 复制 md 原文件
    for d in docs:
        shutil.copy2(d["path"], os.path.join(args.out, d["file"]))

    # 2) 生成单文件 HTML
    convert(docs)
    out_html = os.path.join(args.out, "数据工程笔记.html")
    size = build_html(docs, out_html)

    print("笔记目录 : %s" % args.out)
    print("篇数     : %d" % len(docs))
    print("HTML     : %s（%.1f MB）" % (out_html, size / 1024 / 1024))
    print("md 副本  : %d 个" % len(docs))


if __name__ == "__main__":
    main()
