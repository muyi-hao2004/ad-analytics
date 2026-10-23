"""
中台后端服务（FastAPI）

作用：承接前端报表的"写操作"（删除子渠道、调预算、换素材…），
     转发给平台操作层（platform_layer），并写操作日志。

这是"仿真中台"从【纯展示】走向【可操作】的关键一步：
  前端 (HTML)  fetch('/api/delete_sub_channel')
       ↓ POST JSON
  后端 (本文件)  校验 → 调 platform_layer → 返回结果
       ↓
  平台操作层  MockPlatform（现在）/ UnityPlatform（将来）

运行：
    cd iaa-analytics-agent
    venv/Scripts/python.exe -m uvicorn src.app:app --reload --port 8000

然后浏览器打开 http://127.0.0.1:8000 就能看到带"真操作"的中台。
"""

from typing import Optional, List
from pathlib import Path
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

import config
import dim_scd
import guardrails
import platform_layer
import warehouse_api
import agent

from dotenv import load_dotenv
load_dotenv(config.PROJECT_ROOT / ".env")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时启动 Agent 定时任务，关闭时取消。"""
    task = asyncio.create_task(_auto_run_agent())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="IAA 广告变现分析中台", version="1.0", lifespan=lifespan)

# 允许跨域（前端如果单独打开 file:// 或另一个端口，也能调后端）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 请求体模型（Pydantic 自动校验）
# ============================================================

class DeleteSubChannelReq(BaseModel):
    sub_channel_ids: List[str]          # 要删除的子渠道 ID 列表（支持批量）
    operator: str = "agent"             # 操作者：agent 或投放人名字
    app: Optional[str] = None
    channel: Optional[str] = None
    reason: Optional[str] = None


class AdjustBudgetReq(BaseModel):
    campaign_id: str
    country: str
    new_budget: float
    operator: str = "agent"
    reason: Optional[str] = None


class AdjustTargetReq(BaseModel):
    campaign_id: str
    country: str
    new_target: float
    operator: str = "agent"
    reason: Optional[str] = None


# ============================================================
# API 接口
# ============================================================

@app.get("/api/health")
def health():
    return {"ok": True, "service": "IAA BI 中台", "time": dim_scd.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def _guard(action: dict):
    """Web 接口的护栏校验。

    为什么这个文件也需要：这三个接口是**人**在看板上点按钮触发的，
    但它和 Agent 路径打的是同一个平台。之前这里直接调 plat.xxx()，
    完全绕过了 guardrails —— 同一条规则（比如删除要求样本量 >= 100）
    在 Agent 路径生效、在人手动路径不生效，等于没有规则。

    护栏的层级不看它写得多好，看**还有没有别的路能打到平台**。
    """
    ok, why = guardrails.validate_action(
        action, agent._current_context_for(action))
    return ok, why


@app.post("/api/delete_sub_channel")
def api_delete_sub_channel(req: DeleteSubChannelReq):
    """删除（批量）子渠道：先过护栏，再转发给平台层。"""
    plat = platform_layer.get_platform("mock")
    results = []
    for sid in req.sub_channel_ids:
        action = {"action": "delete_sub_channel", "target": sid,
                  "reason": req.reason or f"{req.operator} 手动操作"}
        ok, why = _guard(action)
        if not ok:
            results.append({"sub_channel_id": sid, "ok": False,
                            "message": f"护栏拦截：{why}"})
            continue
        r = plat.delete_sub_channel(
            sid, operator=req.operator, app=req.app,
            channel=req.channel, reason=req.reason
        )
        results.append({"sub_channel_id": sid, **r})

    ok_count = sum(1 for r in results if r["ok"])
    return {
        "ok": True,
        "deleted": ok_count,
        "total": len(results),
        "results": results,
    }


@app.post("/api/adjust_budget")
def api_adjust_budget(req: AdjustBudgetReq):
    action = {"action": "adjust_budget", "target": req.campaign_id,
              "params": {"country": req.country, "new_budget": req.new_budget},
              "reason": req.reason or f"{req.operator} 手动调整"}
    ok, why = _guard(action)
    if not ok:
        raise HTTPException(status_code=400, detail=f"护栏拦截：{why}")

    plat = platform_layer.get_platform("mock")
    r = plat.adjust_budget(req.campaign_id, req.country, req.new_budget,
                           req.operator, req.reason)
    if not r["ok"]:
        raise HTTPException(status_code=400, detail=r["message"])
    return r


@app.post("/api/adjust_target")
def api_adjust_target(req: AdjustTargetReq):
    action = {"action": "adjust_target", "target": req.campaign_id,
              "params": {"country": req.country, "new_target": req.new_target},
              "reason": req.reason or f"{req.operator} 手动调整"}
    ok, why = _guard(action)
    if not ok:
        raise HTTPException(status_code=400, detail=f"护栏拦截：{why}")

    plat = platform_layer.get_platform("mock")
    r = plat.adjust_target(req.campaign_id, req.country, req.new_target,
                           req.operator, req.reason)
    if not r["ok"]:
        raise HTTPException(status_code=400, detail=r["message"])
    return r


@app.get("/api/logs")
def api_logs(limit: int = 50):
    """查操作日志（给前端日志 Tab 用）。

    读路径统一走 `warehouse_api`，和快照、Agent 工具同源 ——
    这样「前端看到的操作记录」和「Agent 判断时看到的」不会是两个答案。
    值里的 NA 统一转 None：pandas 的 NaN 会让 JSON 输出非法的 NaN token。
    """
    try:
        df = warehouse_api.read_operation_log(limit=limit)
        if df is not None and not df.empty:
            return {"logs": [{k: (None if pd.isna(v) else v) for k, v in r.items()}
                             for r in df.to_dict("records")]}
    except Exception:  # noqa: BLE001
        pass
    return {"logs": dim_scd.list_logs(limit=limit)}


@app.get("/api/sub_channels")
def api_sub_channels(active_only: bool = True):
    """查子渠道列表（给前端子渠道筛选/删除用）。"""
    return {"sub_channels": dim_scd.list_sub_channels(active_only=active_only)}


class RunAgentReq(BaseModel):
    dry_run: bool = True   # True=只分析不执行，False=真正执行动作


@app.post("/api/run_agent")
def api_run_agent(req: RunAgentReq):
    """触发 Agent：组装快照 → 多轮 Function Calling →（可选）执行动作。"""
    result = agent.run_agent(dry_run=req.dry_run)
    return {"ok": True, **result}


@app.get("/api/snapshot")
def api_snapshot(top_n: int = 20):
    """查看 Agent 组装的快照（调试用，理解 Agent 到底看了什么）。"""
    return {"snapshot": agent.build_snapshot(top_n=top_n)}


# ============================================================
# 人工确认开关：Agent 出建议 → 投放人确认 → 才执行
# ============================================================

class ApproveReq(BaseModel):
    run_id: int


@app.post("/api/agent/propose")
def api_agent_propose():
    """Agent 出建议（不执行）：返回 run_id + reasoning + 待确认动作。"""
    result = agent.run_agent_with_approval()
    return {"ok": True, **result}


@app.post("/api/agent/approve")
def api_agent_approve(req: ApproveReq):
    """投放人确认执行某条待确认建议单。"""
    result = agent.approve_agent_run(req.run_id)
    return result


@app.post("/api/agent/reject")
def api_agent_reject(req: ApproveReq):
    """投放人放弃某条待确认建议单。"""
    result = agent.reject_agent_run(req.run_id)
    return result


@app.get("/api/agent/runs")
def api_agent_runs(limit: int = 20, status: Optional[str] = None):
    """Agent 运行历史（含 pending/executed/rejected 各状态）。"""
    return {"runs": dim_scd.list_agent_runs(limit=limit, status=status)}


@app.post("/api/agent_analyze")
def api_agent_analyze(top_n: int = 20):
    """
    只分析不执行（等价 dry_run=True），返回完整可解释轨迹：
      - trace: 每一轮「查了什么工具 + 参数 + 返回结果」的客观证据
      - reasoning: LLM 的推理链（为什么这么决定）
    供前端「可解释性面板」渲染，让投放人看懂 Agent 为什么这么决策。
    """
    import platform_layer
    plat = platform_layer.get_platform("mock")
    snapshot = agent.build_snapshot(top_n=top_n)
    decision = agent.run_llm_loop(snapshot, plat)
    return {"ok": True, "snapshot": snapshot, **decision}


# ============================================================
# 定时调度：每天固定时间自动跑一轮 Agent
# ============================================================
# 用 asyncio 后台任务实现，零额外依赖（不装 APScheduler）。
# 每天到点触发 agent.run_agent(dry_run=False)，结果存内存缓存，
# 供 /api/agent_schedule 查询最近一次自动运行的结果。

# 最近一次自动运行的缓存（进程内，重启后清空）
_LAST_AUTO_RUN: dict = {"status": "尚未运行"}

# 每天几点跑（默认 09:00，可用 .env 的 AGENT_RUN_TIME=HH:MM 覆盖）
_AGENT_RUN_TIME = os.getenv("AGENT_RUN_TIME", "09:00")


async def _auto_run_agent():
    """后台任务：每天等到 AGENT_RUN_TIME 触发一次 Agent。"""
    while True:
        now = datetime.now()
        hh, mm = map(int, _AGENT_RUN_TIME.split(":"))
        # 计算距离今天目标时间的秒数；若已过，则等到明天
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()

        await asyncio.sleep(wait_seconds)

        # 到点执行
        _LAST_AUTO_RUN["status"] = "运行中"
        try:
            result = agent.run_agent(dry_run=False)   # 真正执行（非 dry_run）
            _LAST_AUTO_RUN.update({
                "status": "完成",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "conclusion": result.get("conclusion", ""),
                "reasoning": result.get("reasoning", ""),
                "actions": result.get("actions", []),
                "trace": result.get("trace", []),
                "executed": result.get("executed", []),
            })
        except Exception as e:
            _LAST_AUTO_RUN.update({
                "status": "失败",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(e),
            })


@app.get("/api/agent_schedule")
def api_agent_schedule():
    """查定时任务的配置 + 最近一次自动运行结果。"""
    return {
        "run_time": _AGENT_RUN_TIME,
        "enabled": True,
        "last_run": _LAST_AUTO_RUN,
    }


# ============================================================
# 静态报表托管：把 reports/bi_report.html 作为首页
# ============================================================

REPORTS_DIR = Path(__file__).parent.parent / "reports"


@app.get("/")
def index():
    """首页：返回中台报表 HTML。"""
    html = REPORTS_DIR / "bi_report.html"
    if html.exists():
        return FileResponse(html)
    return {"message": "报表尚未生成，请先运行 build_report.py"}


@app.get("/agent_dashboard")
def agent_dashboard():
    """Agent 决策看板（由 run_agent_dashboard.py 生成）。"""
    html = REPORTS_DIR / "agent_dashboard.html"
    if html.exists():
        return FileResponse(html)
    return {"message": "决策看板尚未生成，请先运行 src/run_agent_dashboard.py"}


# 托管 reports 目录下的静态资源（图片等）
app.mount("/static", StaticFiles(directory=str(REPORTS_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
