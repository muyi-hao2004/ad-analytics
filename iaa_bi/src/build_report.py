"""
生成中台 HTML 报表

读取 ADS 层的业务表 + 操作日志，生成一个 HTML 单页：
  - 四个 Tab（日常表/素材表/子渠道表/操作日志）
  - 筛选改为「下拉勾选面板」：每个维度一个下拉框，点开勾选多个，含全选/清除
  - 日常表全维度筛选（时间/包/国家/系列/策略/素材/子渠道），素材随系列联动
  - 默认 = 当天日期的全部数据
  - 子渠道表支持批量删除（写操作日志）
  - 当天未过完的数据标星号
  - 红涨绿跌（ROI 达标标红，不达标标绿）

运行：
    cd iaa_bi
    venv/Scripts/python.exe src/build_report.py
"""

import sys
import json
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import config
import dim_scd


# ============================================================
# 读取 ADS 数据 + 操作日志
# ============================================================

def _load(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def load_all():
    # 日常表用"细粒度"版本（粒度到素材/子渠道，支持全维度筛选下钻）
    daily = _load(config.ADS_DIR / "daily_detail.parquet")
    creative = _load(config.ADS_DIR / "creative_table.parquet")
    sub_channel = _load(config.ADS_DIR / "sub_channel_table.parquet")
    return daily, creative, sub_channel


def load_logs() -> list:
    """读取操作日志（append-only 审计表），最新在前。"""
    return dim_scd.list_logs(limit=200)


# ============================================================
# 数据处理：整理成前端要的格式
# ============================================================

def prep_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """日常表：细粒度（含素材/子渠道/策略），整理成展示用。"""
    dims = ["install_date", "app", "country", "campaign", "strategy",
            "creative_id", "sub_channel"]
    key_cols = ["installs", "spend", "cpi", "d1_retention", "d7_retention", "d7_roi"]
    cols = [c for c in dims + key_cols if c in daily.columns]
    df = daily[cols].copy()
    for c in ["d1_retention", "d7_retention", "d7_roi"]:
        if c in df.columns:
            df[c] = df[c] * 100
    pct_cols = [c for c in ["d1_retention", "d7_retention", "d7_roi"] if c in df.columns]
    money_cols = [c for c in ["spend", "cpi"] if c in df.columns]
    df[pct_cols] = df[pct_cols].round(1)
    df[money_cols] = df[money_cols].round(2)
    return df


def prep_creative(creative: pd.DataFrame) -> pd.DataFrame:
    """素材表：前端 + 后端 D7。"""
    cols = ["creative_id", "install_date", "spend", "ctr", "cvr", "cpi",
            "d7_retention", "d7_roi", "d7_ecpm"]
    cols = [c for c in cols if c in creative.columns]
    df = creative[cols].copy()
    for c in ["ctr", "cvr", "d7_retention", "d7_roi"]:
        if c in df.columns:
            df[c] = df[c] * 100
    pct_cols = [c for c in ["ctr", "cvr", "d7_retention", "d7_roi"] if c in df.columns]
    money_cols = [c for c in ["cpi", "spend", "d7_ecpm"] if c in df.columns]
    df[pct_cols] = df[pct_cols].round(1)
    df[money_cols] = df[money_cols].round(2)
    return df


def prep_sub_channel(sub_channel: pd.DataFrame) -> pd.DataFrame:
    """子渠道表：同素材表结构。"""
    cols = ["sub_channel", "install_date", "spend", "ctr", "cvr", "cpi",
            "d7_retention", "d7_roi", "d7_ecpm"]
    cols = [c for c in cols if c in sub_channel.columns]
    df = sub_channel[cols].copy()
    for c in ["ctr", "cvr", "d7_retention", "d7_roi"]:
        if c in df.columns:
            df[c] = df[c] * 100
    pct_cols = [c for c in ["ctr", "cvr", "d7_retention", "d7_roi"] if c in df.columns]
    money_cols = [c for c in ["cpi", "spend", "d7_ecpm"] if c in df.columns]
    df[pct_cols] = df[pct_cols].round(1)
    df[money_cols] = df[money_cols].round(2)
    return df


# ============================================================
# 生成 HTML
# ============================================================

def _df_to_json(df: pd.DataFrame) -> list:
    return json.loads(df.astype(object).where(pd.notna(df), None).to_json(orient="records"))


def build_html(daily, creative, sub_channel, logs, today_str: str) -> str:
    daily_json = _df_to_json(daily)
    creative_json = _df_to_json(creative)
    sub_json = _df_to_json(sub_channel)
    logs_json = logs

    # 各表的筛选维度
    daily_filters = {
        "install_date": sorted(daily["install_date"].unique().tolist()),
        "app": sorted(daily["app"].unique().tolist()),
        "country": sorted(daily["country"].unique().tolist()),
        "campaign": sorted(daily["campaign"].unique().tolist()),
        "strategy": sorted(daily["strategy"].unique().tolist()),
        "creative_id": sorted(daily["creative_id"].unique().tolist()),
        "sub_channel": sorted(daily["sub_channel"].unique().tolist()),
    }
    creative_filters = {
        "creative_id": sorted(creative["creative_id"].unique().tolist()),
        "install_date": sorted(creative["install_date"].unique().tolist()),
    }
    sub_filters = {
        "sub_channel": sorted(sub_channel["sub_channel"].unique().tolist()),
        "install_date": sorted(sub_channel["install_date"].unique().tolist()),
    }

    # 用普通字符串 + 占位符注入（避免 JS 的 {} 与 f-string 冲突）
    html = _HTML_TEMPLATE
    html = html.replace("__TODAY__", today_str)
    html = html.replace("__DAILY_JSON__", json.dumps(daily_json, ensure_ascii=False))
    html = html.replace("__CREATIVE_JSON__", json.dumps(creative_json, ensure_ascii=False))
    html = html.replace("__SUB_JSON__", json.dumps(sub_json, ensure_ascii=False))
    html = html.replace("__LOGS_JSON__", json.dumps(logs_json, ensure_ascii=False))
    html = html.replace("__DAILY_FILTERS__", json.dumps(daily_filters, ensure_ascii=False))
    html = html.replace("__CREATIVE_FILTERS__", json.dumps(creative_filters, ensure_ascii=False))
    html = html.replace("__SUB_FILTERS__", json.dumps(sub_filters, ensure_ascii=False))
    # 后端地址：由 FastAPI 托管时同源为空；直接打开 file:// 时指向本地 8000 端口
    html = html.replace("__API_BASE__", "")
    return html


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IAA 广告变现分析中台</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
         background: #f5f6f8; color: #1f1f1f; padding: 20px; }
  .header { background: #fff; border-radius: 10px; padding: 20px 24px; margin-bottom: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .header h1 { font-size: 20px; font-weight: 700; }
  .header .sub { font-size: 13px; color: #888; margin-top: 4px; }
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
  .tab { padding: 9px 20px; border-radius: 8px; cursor: pointer; font-size: 14px;
         background: #fff; color: #555; border: 1px solid #e0e0e0; font-weight: 500; }
  .tab.active { background: #2c5f8a; color: #fff; border-color: #2c5f8a; }
  .panel { display: none; }
  .panel.active { display: block; }
  .filters { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }

  /* 下拉勾选面板 */
  .dropdown { position: relative; display: inline-block; }
  .dropdown-toggle { min-width: 120px; padding: 7px 30px 7px 12px; border: 1px solid #ddd;
                     border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px;
                     text-align: left; position: relative; }
  .dropdown-toggle .arrow { position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
                            font-size: 10px; color: #888; }
  .dropdown-toggle .tag { color: #2c5f8a; font-weight: 600; }
  .dropdown-menu { display: none; position: absolute; top: calc(100% + 4px); left: 0; z-index: 100;
                   background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
                   box-shadow: 0 4px 16px rgba(0,0,0,0.12); min-width: 180px; max-height: 280px;
                   overflow: auto; padding: 6px; }
  .dropdown.open .dropdown-menu { display: block; }
  .dropdown-menu .opt { display: flex; align-items: center; gap: 8px; padding: 6px 8px;
                        border-radius: 5px; cursor: pointer; font-size: 13px; }
  .dropdown-menu .opt:hover { background: #f5f7fa; }
  .dropdown-menu .opt input { cursor: pointer; }
  .dropdown-menu .dd-actions { display: flex; gap: 8px; padding: 6px 8px 2px;
                               border-top: 1px solid #f0f0f0; margin-top: 4px; }
  .dd-actions button { flex: 1; padding: 4px 0; border: 1px solid #ddd; border-radius: 5px;
                       background: #fafafa; cursor: pointer; font-size: 12px; }
  .dd-actions button:hover { background: #eef2f6; }

  .table-wrap { background: #fff; border-radius: 10px; overflow: auto;
                 box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { padding: 10px 12px; text-align: right; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }
  th { background: #fafbfc; font-weight: 600; color: #555; position: sticky; top: 0; }
  th:first-child, td:first-child { text-align: left; }
  tr:hover td { background: #f8fafc; }
  .star { color: #e6a23c; font-weight: 700; }
  .good { color: #c0392b; font-weight: 600; }
  .bad { color: #27ae60; font-weight: 600; }
  .muted { color: #bbb; }
  .count { font-size: 12px; color: #888; margin-bottom: 8px; }
  .toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .toolbar .count { margin-bottom: 0; }
  .del-btn { padding: 7px 16px; background: #c0392b; color: #fff; border: none;
             border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; }
  .del-btn:hover { background: #a93226; }
  input[type=checkbox] { cursor: pointer; width: 15px; height: 15px; }

  /* 操作日志 */
  .log-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
               background: #eef2f6; color: #555; }
  .log-op-agent { color: #2c5f8a; font-weight: 600; }
  .log-op-human { color: #8e44ad; font-weight: 600; }
  .log-reason { color: #888; font-size: 12px; max-width: 300px; white-space: normal; }
</style>
</head>
<body>

<div class="header">
  <h1>IAA 广告变现分析中台</h1>
  <div class="sub">海外网赚类 IAA · 美国 T0 市场 · D7 ROAS 目标 70% · 数据截至 __TODAY__</div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('daily')">日常表</div>
  <div class="tab" onclick="switchTab('creative')">素材表（前后端）</div>
  <div class="tab" onclick="switchTab('subchannel')">子渠道表（前后端）</div>
  <div class="tab" onclick="switchTab('logs')">操作日志</div>
</div>

<div class="panel active" id="panel-daily">
  <div class="filters" id="daily-filters"></div>
  <div class="count" id="daily-count"></div>
  <div class="table-wrap"><table id="daily-table"></table></div>
</div>

<div class="panel" id="panel-creative">
  <div class="filters" id="creative-filters"></div>
  <div class="count" id="creative-count"></div>
  <div class="table-wrap"><table id="creative-table"></table></div>
</div>

<div class="panel" id="panel-subchannel">
  <div class="filters" id="subchannel-filters"></div>
  <div class="toolbar">
    <button class="del-btn" onclick="batchDeleteSubChannel()">批量删除选中子渠道</button>
    <span class="count" id="subchannel-count"></span>
  </div>
  <div class="table-wrap"><table id="subchannel-table"></table></div>
</div>

<div class="panel" id="panel-logs">
  <div class="count" id="logs-count"></div>
  <div class="table-wrap"><table id="logs-table"></table></div>
</div>

<script>
const TODAY = "__TODAY__";
const DATA = {
  daily: __DAILY_JSON__,
  creative: __CREATIVE_JSON__,
  subchannel: __SUB_JSON__,
};
let LOGS = __LOGS_JSON__;
const API_BASE = "__API_BASE__";  // 后端地址（空字符串=同源，即由 FastAPI 托管时）
const FILTERS = {
  daily: __DAILY_FILTERS__,
  creative: __CREATIVE_FILTERS__,
  subchannel: __SUB_FILTERS__,
};

// 各表的维度中文名 + 顺序
const DIM_LABELS = {
  daily: [
    ['install_date', '时间'], ['app', '包'], ['country', '国家'],
    ['campaign', '系列'], ['strategy', '策略'],
    ['creative_id', '素材'], ['sub_channel', '子渠道'],
  ],
  creative: [['creative_id', '素材'], ['install_date', '安装日期']],
  subchannel: [['sub_channel', '子渠道'], ['install_date', '安装日期']],
};

// 选中状态：空 Set = 全选
const selected = {
  daily: { install_date: new Set(), app: new Set(), country: new Set(),
           campaign: new Set(), strategy: new Set(), creative_id: new Set(),
           sub_channel: new Set() },
  creative: { creative_id: new Set(), install_date: new Set() },
  subchannel: { sub_channel: new Set(), install_date: new Set() },
};

// 已删除的子渠道（前端标记）
const deletedSubChannels = new Set();

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  const map = { daily: 0, creative: 1, subchannel: 2, logs: 3 };
  document.querySelectorAll('.tab')[map[name]].classList.add('active');
}

// ============================================================
// 下拉勾选面板
// ============================================================
function buildDropdowns() {
  for (const [table, dims] of Object.entries(DIM_LABELS)) {
    const container = document.getElementById(table + '-filters');
    if (!container) continue;
    container.innerHTML = '';
    dims.forEach(([dim, label]) => {
      const values = FILTERS[table][dim] || [];
      const dd = document.createElement('div');
      dd.className = 'dropdown';
      dd.id = table + '-dd-' + dim;
      dd.innerHTML =
        '<button class="dropdown-toggle" onclick="toggleDropdown(\'' + table + '\',\'' + dim + '\')">' +
          '<span class="label-tag">' + label + ': </span>' +
          '<span class="tag" id="' + table + '-tag-' + dim + '">全部</span>' +
          '<span class="arrow">▼</span>' +
        '</button>' +
        '<div class="dropdown-menu" id="' + table + '-menu-' + dim + '">' +
          values.map(v =>
            '<label class="opt"><input type="checkbox" value="' + v + '" onchange="onOptionChange(\'' +
            table + '\',\'' + dim + '\',this)">' + v + '</label>'
          ).join('') +
          '<div class="dd-actions">' +
            '<button onclick="selectAll(\'' + table + '\',\'' + dim + '\')">全选</button>' +
            '<button onclick="clearAll(\'' + table + '\',\'' + dim + '\')">清除</button>' +
          '</div>' +
        '</div>';
      container.appendChild(dd);
    });
  }
  // 默认：日常表时间选「今天」
  if (FILTERS.daily && FILTERS.daily.install_date) {
    const dates = FILTERS.daily.install_date;
    const today = dates.includes(TODAY) ? TODAY : dates[dates.length - 1];
    selected.daily.install_date.add(today);
  }
  syncTag('daily', 'install_date');
}

function toggleDropdown(table, dim) {
  const dd = document.getElementById(table + '-dd-' + dim);
  dd.classList.toggle('open');
}

// 点击面板外关闭
document.addEventListener('click', function(e) {
  if (!e.target.closest('.dropdown')) {
    document.querySelectorAll('.dropdown.open').forEach(d => d.classList.remove('open'));
  }
});

function onOptionChange(table, dim, checkbox) {
  const s = selected[table][dim];
  if (checkbox.checked) s.add(checkbox.value);
  else s.delete(checkbox.value);
  syncTag(table, dim);
  if (table === 'daily' && dim === 'campaign') syncCreativeOptions();
  renderTable(table);
}

function selectAll(table, dim) {
  const s = selected[table][dim];
  s.clear();  // 空 = 全选
  document.querySelectorAll('#' + table + '-menu-' + dim + ' input').forEach(cb => cb.checked = false);
  syncTag(table, dim);
  if (table === 'daily' && dim === 'campaign') syncCreativeOptions();
  renderTable(table);
}

function clearAll(table, dim) {
  const s = selected[table][dim];
  s.clear();
  // 清除 = 不选任何值，但空 Set 语义是"全选"，所以这里用一个哨兵值标记"无"
  // 用特殊约定：加一个不可能出现的哨兵 "__none__"
  s.add('__none__');
  document.querySelectorAll('#' + table + '-menu-' + dim + ' input').forEach(cb => cb.checked = false);
  syncTag(table, dim);
  if (table === 'daily' && dim === 'campaign') syncCreativeOptions();
  renderTable(table);
}

function syncTag(table, dim) {
  const s = selected[table][dim];
  const tag = document.getElementById(table + '-tag-' + dim);
  if (!tag) return;
  const total = (FILTERS[table][dim] || []).length;
  if (s.size === 0) {
    tag.textContent = '全部';
  } else if (s.has('__none__')) {
    tag.textContent = '无';
  } else if (s.size >= total) {
    tag.textContent = '全部';
  } else {
    tag.textContent = '已选 ' + s.size + ' 项';
  }
}

// 素材选项随系列联动
function syncCreativeOptions() {
  const campSel = selected.daily.campaign;
  const allCreatives = FILTERS.daily.creative_id;
  const selCreatives = selected.daily.creative_id;

  let allowed = new Set(allCreatives);
  if (campSel.size > 0 && !campSel.has('__none__')) {
    allowed = new Set();
    DATA.daily.forEach(row => {
      if (campSel.has(row.campaign)) allowed.add(row.creative_id);
    });
  }

  const menu = document.getElementById('daily-menu-creative_id');
  if (!menu) return;
  const boxes = menu.querySelectorAll('input');
  boxes.forEach(cb => {
    const show = allowed.has(cb.value);
    cb.closest('.opt').style.display = show ? 'flex' : 'none';
    if (!show && cb.checked) { cb.checked = false; selCreatives.delete(cb.value); }
  });
  syncTag('daily', 'creative_id');
}

function isSelected(table, dim, value) {
  const s = selected[table][dim];
  if (s.size === 0) return true;           // 空 = 全选
  if (s.has('__none__')) return false;     // 哨兵 = 不选任何
  return s.has(value);
}

function isToday(dateStr) {
  return dateStr === TODAY;
}

// ============================================================
// 渲染表格
// ============================================================
function renderTable(table) {
  let data = DATA[table];
  const dims = Object.keys(selected[table]);

  if (table === 'subchannel') {
    data = data.filter(row => !deletedSubChannels.has(row.sub_channel));
  }

  const filtered = data.filter(row => {
    for (const dim of dims) {
      if (!isSelected(table, dim, row[dim])) return false;
    }
    return true;
  });

  const tableEl = document.getElementById(table + '-table');
  const countEl = document.getElementById(table + '-count');
  countEl.textContent = '共 ' + filtered.length + ' 行';

  if (filtered.length === 0) {
    tableEl.innerHTML = '<tr><td style="text-align:center;color:#999;padding:20px">暂无数据</td></tr>';
    return;
  }

  const cols = Object.keys(filtered[0]);
  let html = '<thead><tr>';
  if (table === 'subchannel') {
    html += '<th style="width:40px"><input type="checkbox" onchange="toggleAll(this)"></th>';
  }
  cols.forEach(c => html += '<th>' + formatHeader(c) + '</th>');
  html += '</tr></thead><tbody>';

  filtered.forEach(row => {
    html += '<tr>';
    if (table === 'subchannel') {
      html += '<td><input type="checkbox" class="row-check" value="' + row.sub_channel + '"></td>';
    }
    cols.forEach(c => {
      html += '<td>' + formatCell(c, row[c], row) + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody>';
  tableEl.innerHTML = html;
}

function toggleAll(master) {
  document.querySelectorAll('#subchannel-table .row-check').forEach(cb => {
    cb.checked = master.checked;
  });
}

function batchDeleteSubChannel() {
  const checks = Array.from(document.querySelectorAll('#subchannel-table .row-check:checked'));
  if (checks.length === 0) {
    alert('请先勾选要删除的子渠道');
    return;
  }
  const names = checks.map(cb => cb.value);
  if (!confirm('确定删除以下 ' + names.length + ' 个子渠道？\n' + names.join(', '))) return;

  // 真正调后端 API（删除动作会落到 dim.db + 写 operation_log）
  fetch(API_BASE + '/api/delete_sub_channel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sub_channel_ids: names,
      operator: 'agent',
      reason: '优化师/Agent 在中台批量删除子渠道',
    }),
  })
    .then(res => res.json())
    .then(result => {
      // 后端成功 → 更新本地缓存（前端立即反映，不用刷新页面）
      names.forEach(n => deletedSubChannels.add(n));
      renderTable('subchannel');
      // 刷新日志 Tab
      refreshLogs();
      const ok = result.deleted || 0;
      alert('成功删除 ' + ok + '/' + names.length + ' 个子渠道（已写入操作日志）');
    })
    .catch(err => {
      alert('删除失败：' + err.message + '\n请确认后端已启动（uvicorn src.app:app）');
    });
}

// 从后端重新拉取操作日志，刷新日志 Tab
function refreshLogs() {
  fetch(API_BASE + '/api/logs?limit=50')
    .then(res => res.json())
    .then(data => {
      LOGS = data.logs || [];
      renderLogs();
    })
    .catch(err => console.log('刷新日志失败:', err));
}

// ============================================================
// 操作日志表
// ============================================================
function renderLogs() {
  const tableEl = document.getElementById('logs-table');
  const countEl = document.getElementById('logs-count');
  countEl.textContent = '共 ' + LOGS.length + ' 条操作记录';

  if (LOGS.length === 0) {
    tableEl.innerHTML = '<tr><td style="text-align:center;color:#999;padding:20px">暂无操作记录</td></tr>';
    return;
  }

  let html = '<thead><tr>';
  ['时间', '操作者', '动作', '包名', '渠道', '系列', '国家', '字段', '改前', '改后', '原因'].forEach(h => {
    html += '<th>' + h + '</th>';
  });
  html += '</tr></thead><tbody>';

  LOGS.forEach(log => {
    html += '<tr>';
    html += '<td>' + (log.op_time || '') + '</td>';
    html += '<td>' + formatOperator(log.operator) + '</td>';
    html += '<td>' + formatAction(log.action) + '</td>';
    html += '<td>' + (log.app || '—') + '</td>';
    html += '<td>' + (log.channel || '—') + '</td>';
    html += '<td>' + (log.campaign_id || '—') + '</td>';
    html += '<td>' + (log.country || '—') + '</td>';
    html += '<td>' + (log.field || '—') + '</td>';
    html += '<td>' + (log.old_value !== null && log.old_value !== undefined ? log.old_value : '—') + '</td>';
    html += '<td>' + (log.new_value !== null && log.new_value !== undefined ? log.new_value : '—') + '</td>';
    html += '<td class="log-reason">' + (log.reason || '—') + '</td>';
    html += '</tr>';
  });
  html += '</tbody>';
  tableEl.innerHTML = html;
}

function formatOperator(op) {
  if (op === 'agent') return '<span class="log-op-agent">Agent</span>';
  if (op === '系统' || op === null) return '<span class="muted">—</span>';
  return '<span class="log-op-human">' + op + '</span>';
}

function formatAction(action) {
  const map = {
    create_campaign: '新建系列', adjust_budget: '调预算', adjust_target: '调目标值',
    change_creative: '换素材', delete_sub_channel: '删子渠道', add_country: '上新国家',
    pause_campaign: '暂停系列', resume_campaign: '恢复系列',
  };
  return map[action] || action;
}

// ============================================================
// 表头/单元格格式化
// ============================================================
function formatHeader(col) {
  const map = {
    install_date: '安装日', app: '包', country: '国家', campaign: '系列',
    strategy: '策略', creative_id: '素材ID', sub_channel: '子渠道', installs: '安装',
    spend_total: '花费', spend: '花费', cpi: 'CPI',
    d1_retention: 'D1留存%', d7_retention: 'D7留存%',
    ctr: 'CTR%', cvr: 'CVR%',
    d7_roi: 'D7 ROI%', d7_ecpm: 'D7 eCPM',
  };
  if (map[col]) return map[col];
  const m = col.match(/^(ret|roi)_(\d+)$/);
  if (m) return 'D' + m[2] + (m[1] === 'ret' ? '留存%' : ' ROI%');
  return col;
}

function formatCell(col, val, row) {
  if (val === null || val === undefined) return '<span class="muted">—</span>';
  if (col === 'install_date') {
    const star = isToday(val) ? ' <span class="star">*</span>' : '';
    return val + star;
  }
  if (col === 'd7_roi') {
    const cls = val >= 70 ? 'good' : 'bad';
    return '<span class="' + cls + '">' + val + '%</span>';
  }
  if (col === 'd1_retention' || col === 'd7_retention') {
    return val + '%';
  }
  return val;
}

// ============================================================
// 初始化
// ============================================================
buildDropdowns();
renderTable('daily');
renderTable('creative');
renderTable('subchannel');
renderLogs();
</script>
</body>
</html>"""


def main():
    config.ensure_dirs()
    dim_scd.init_dim_tables()
    daily, creative, sub_channel = load_all()
    logs = load_logs()

    daily = prep_daily(daily)
    creative = prep_creative(creative)
    sub_channel = prep_sub_channel(sub_channel)

    today_str = "2026-10-08"

    html = build_html(daily, creative, sub_channel, logs, today_str)

    out = config.REPORTS_DIR / "bi_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"报表已生成: {out}")
    print(f"  日常表 {len(daily)} 行 | 素材表 {len(creative)} 行 | 子渠道表 {len(sub_channel)} 行 | 操作日志 {len(logs)} 条")


if __name__ == "__main__":
    main()
