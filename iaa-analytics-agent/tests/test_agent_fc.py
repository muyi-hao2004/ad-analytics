# -*- coding: utf-8 -*-
"""
agent 的 Function Calling 循环 + 读写分离测试

这一组测试**不连真实 LLM**（用 fake client 按脚本返回预设响应），
也不连数据库（monkeypatch 掉工具注册表）。

为什么要专门测这两件事：
  1. 读写分离是权限边界，不是「最好满足的风格要求」。它一旦回退，
     Agent 就能在人看不到的地方改投放配置——这类 bug 上线后是资损级别的。
  2. 原生 tool_calls 的对话格式有硬性要求（assistant.tool_calls 必须成对出现
     role=tool 消息，且 tool_call_id 要对得上）。写错格式在真实调用里表现为
     "400 Bad Request"，而单测能在不花钱的情况下抓住它。
"""

import copy
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agent
import guardrails


# ============================================================
# Fake OpenAI client
# ============================================================

def fake_tool_call(call_id, name, args: dict):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def fake_message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [], role="assistant")


class FakeClient:
    """按预设脚本逐次返回 message，并记录每次请求的 kwargs。

    注意 requests 里存的是**深拷贝**：主循环传进来的 messages 是同一个 list，
    后续轮次会继续 append。若只存引用，回看第 1 次请求时会看到第 3 轮的追加内容，
    断言就会莫名其妙地失败（这个坑在第一次跑测试时就踩到了）。
    """

    def __init__(self, script):
        self._script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        snapshot = dict(kwargs)
        if "messages" in snapshot:
            snapshot["messages"] = copy.deepcopy(snapshot["messages"])
        self.requests.append(snapshot)
        if not self._script:
            raise AssertionError("FakeClient 脚本已用尽，但代码还在调用 LLM（可能存在死循环）")
        msg = self._script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def no_io(monkeypatch):
    """把工具注册表换成纯内存的假实现，避免测试碰数据库和向量库。"""
    calls = []

    def fake_get_metric(**kwargs):
        calls.append(("get_metric", kwargs))
        return {"rows": [{"date": "2026-10-01", "d7_roi": 0.42}]}

    def fake_search_knowledge(**kwargs):
        calls.append(("search_knowledge", kwargs))
        return {"docs": ["ROI 低于目标时先下钻看是否单日暴跌"]}

    monkeypatch.setattr(agent, "_build_tool_registry", lambda plat=None: {
        "get_metric": fake_get_metric,
        "search_knowledge": fake_search_knowledge,
    })
    return calls


def run_with(client, monkeypatch, snapshot=None):
    monkeypatch.setattr(agent, "_make_client", lambda: (client, "fake-model"))
    return agent.run_llm_loop(snapshot or {"date": "2026-10-19"}, None, max_rounds=6)


# ============================================================
# 读写分离：结构层面
# ============================================================

class TestReadWriteSeparation:
    def test_注册表里没有写工具(self):
        reg = agent._build_tool_registry()
        for w in guardrails.VALID_ACTIONS:
            assert w not in reg, f"写操作 {w} 不应该出现在 LLM 可调用的工具表里"

    def test_工具清单里没有写工具(self):
        names = {t["name"] for t in agent.TOOLS_DEFINITION}
        for w in guardrails.VALID_ACTIONS:
            assert w not in names, f"写操作 {w} 不应该出现在 tools schema 里"

    def test_工具清单含终态工具(self):
        names = {t["name"] for t in agent.TOOLS_DEFINITION}
        assert "submit_decision" in names

    def test_openai_tools_格式正确(self):
        """原生 tools 参数要求 {type:function, function:{...}}。"""
        for t in agent._openai_tools():
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "parameters" in t["function"]


# ============================================================
# 读写分离：行为层面（LLM 硬要调写工具）
# ============================================================

class TestWriteToolRejected:
    def test_LLM调写工具不会执行(self, monkeypatch, no_io):
        """模拟 LLM 编造一个 delete_sub_channel 调用，必须被拒且不落到平台。"""
        client = FakeClient([
            fake_message(tool_calls=[
                fake_tool_call("call_1", "delete_sub_channel",
                               {"target": "ch_003", "reason": "ROI 太低"})]),
            fake_message(tool_calls=[
                fake_tool_call("call_2", "submit_decision", {
                    "conclusion": "无动作", "reasoning": "只是试探", "actions": []})]),
        ])
        out = run_with(client, monkeypatch)

        tool_step = [t for t in out["trace"] if t.get("step") == "tool"][0]
        assert "error" in tool_step["result"]
        assert "不在分析阶段的可调用清单" in tool_step["result"]["error"]
        assert out["actions"] == []


# ============================================================
# 原生 Function Calling 循环
# ============================================================

class TestFunctionCallingLoop:
    def test_首轮就提交决策(self, monkeypatch, no_io):
        client = FakeClient([
            fake_message(tool_calls=[
                fake_tool_call("call_1", "submit_decision", {
                    "conclusion": "整体健康，无需调整",
                    "reasoning": "快照里各子渠道 ROI 均在 70% 以上，不动",
                    "actions": []})]),
        ])
        out = run_with(client, monkeypatch)
        assert out["conclusion"] == "整体健康，无需调整"
        assert out["actions"] == []
        assert out["trace"][0]["step"] == "finish"
        assert out["trace"][0]["source"] == "submit_decision"

    def test_先查数据再提交(self, monkeypatch, no_io):
        client = FakeClient([
            fake_message(tool_calls=[
                fake_tool_call("call_1", "get_metric", {
                    "obj_type": "sub_channel", "obj_id": "ch_003", "metric": "d7_roi"})]),
            fake_message(tool_calls=[
                fake_tool_call("call_2", "submit_decision", {
                    "conclusion": "ch_003 持续低 ROI，建议删除",
                    "reasoning": "下钻看到连续多天 ROI 低于 50%，不是单日暴跌",
                    "actions": [{"action": "delete_sub_channel", "target": "ch_003",
                                 "params": {}, "reason": "连续7天 ROI<50%"}]})]),
        ])
        out = run_with(client, monkeypatch)

        assert len(no_io) == 1 and no_io[0][0] == "get_metric"
        assert len(out["actions"]) == 1
        assert out["actions"][0]["action"] == "delete_sub_channel"
        steps = [t["step"] for t in out["trace"]]
        assert steps == ["tool", "finish"]

    def test_请求里带了tools参数(self, monkeypatch, no_io):
        """证明走的是原生 tools，而不是把 schema 拼进 prompt。"""
        client = FakeClient([
            fake_message(tool_calls=[
                fake_tool_call("call_1", "submit_decision",
                               {"conclusion": "ok", "reasoning": "ok", "actions": []})]),
        ])
        run_with(client, monkeypatch)

        req = client.requests[0]
        assert "tools" in req and len(req["tools"]) > 0
        assert req["tool_choice"] == "auto"
        # system prompt 里不应该再塞工具清单 JSON
        system_content = req["messages"][0]["content"]
        assert '"parameters"' not in system_content

    def test_assistant的tool_calls被回填进历史(self, monkeypatch, no_io):
        """格式要求：assistant 带 tool_calls 的消息必须原样进历史，否则下一轮 400。"""
        client = FakeClient([
            fake_message(tool_calls=[
                fake_tool_call("call_1", "get_metric", {
                    "obj_type": "creative", "obj_id": "crt_002", "metric": "d7_roi"})]),
            fake_message(tool_calls=[
                fake_tool_call("call_2", "submit_decision",
                               {"conclusion": "ok", "reasoning": "ok", "actions": []})]),
        ])
        run_with(client, monkeypatch)

        second_msgs = client.requests[1]["messages"]
        assistant_msgs = [m for m in second_msgs if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["tool_calls"][0]["id"] == "call_1"
        # 对应的 tool 回执要在，且 id 对得上
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_1"

    def test_一次返回多个tool_call逐个执行(self, monkeypatch, no_io):
        client = FakeClient([
            fake_message(tool_calls=[
                fake_tool_call("call_1", "get_metric", {
                    "obj_type": "creative", "obj_id": "crt_002", "metric": "d7_roi"}),
                fake_tool_call("call_2", "search_knowledge", {"query": "ROI 低怎么下钻"}),
            ]),
            fake_message(tool_calls=[
                fake_tool_call("call_3", "submit_decision",
                               {"conclusion": "ok", "reasoning": "ok", "actions": []})]),
        ])
        out = run_with(client, monkeypatch)
        names = [t["name"] for t in out["trace"] if t.get("step") == "tool"]
        assert names == ["get_metric", "search_knowledge"]
        # 两条 tool 回执都在
        tool_msgs = [m for m in client.requests[1]["messages"] if m.get("role") == "tool"]
        assert {m["tool_call_id"] for m in tool_msgs} == {"call_1", "call_2"}

    def test_达到最大轮数退出(self, monkeypatch, no_io):
        """模型一直不提交决策，不能无限跑。"""
        loop_msg = fake_message(tool_calls=[
            fake_tool_call("c", "get_metric", {
                "obj_type": "creative", "obj_id": "crt_002", "metric": "d7_roi"})])
        client = FakeClient([loop_msg] * 3)
        monkeypatch.setattr(agent, "_make_client", lambda: (client, "fake-model"))
        out = agent.run_llm_loop({}, None, max_rounds=3)
        assert "最大轮数" in out["conclusion"]

    def test_文本兜底(self, monkeypatch, no_io):
        """某些网关不返回 tool_calls，而是把 JSON 写在 content 里。"""
        client = FakeClient([
            fake_message(content=json.dumps({
                "step": "finish",
                "conclusion": "兜底路径",
                "reasoning": "网关没返回 tool_calls",
                "actions": []}, ensure_ascii=False)),
        ])
        out = run_with(client, monkeypatch)
        assert out["conclusion"] == "兜底路径"
        assert out["trace"][0]["source"] == "text-fallback"
