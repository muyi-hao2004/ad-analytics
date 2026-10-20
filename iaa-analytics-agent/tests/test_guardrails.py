# -*- coding: utf-8 -*-
"""
guardrails 单测

护栏是纯函数，所以测试不需要数据库、不需要 LLM、不需要网络。
**这正是它被设计成纯函数的原因之一**——安全逻辑必须能被快速、确定地验证。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import guardrails as g


# ============================================================
# 单条动作校验
# ============================================================

class TestValidateAction:
    def test_未知动作被拒(self):
        ok, why = g.validate_action({"action": "drop_database", "target": "x", "reason": "r"})
        assert not ok
        assert "未知动作" in why

    def test_缺少reason被拒(self):
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "   "})
        assert not ok
        assert "reason" in why

    def test_缺少target被拒(self):
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "", "reason": "ROI 长期不达标"})
        assert not ok
        assert "target" in why

    def test_幻觉ID形态被拒(self):
        """把一整句话当 ID 传进来，应该被拦。"""
        ok, why = g.validate_action({
            "action": "delete_sub_channel",
            "target": "我认为应该删除这个子渠道因为它表现很差",
            "reason": "ROI 低"})
        assert not ok
        assert "形态异常" in why

    def test_正常删除通过(self):
        ok, _ = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "连续7天 ROI<50%"},
            current={"installs": 21_000})
        assert ok

    # ---------- 删除的样本量门槛 ----------
    # 这几条规则来自评测实测：S02 场景连跑 3 次、3 次都删了一个只有
    # 30 次安装的渠道。而知识库里早就写了「样本不足应观察不应删」——
    # 说明这类约束写在 prompt 里不管用，必须落成确定性代码。

    def test_样本不足时删除被拒(self):
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_009", "reason": "ROI 只有 5%"},
            current={"installs": 30})
        assert not ok
        assert "样本" in why

    def test_查不到安装量时删除被拒(self):
        """查不到 → fail-closed。不能因为读数据失败就放行删除。"""
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={})
        assert not ok
        assert "安装量" in why

    def test_安装量刚好达门槛通过(self):
        """边界值：等于阈值应当放行，避免规则写成 >= 还是 > 的歧义。"""
        ok, _ = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={"installs": g.MIN_INSTALLS_FOR_DELETE})
        assert ok

    def test_安装量少一次被拒(self):
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={"installs": g.MIN_INSTALLS_FOR_DELETE - 1})
        assert not ok
        assert "样本" in why

    def test_安装量是字符串被拒(self):
        """LLM 传 "300" 这种字符串时不能被当成数字放过去。"""
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={"installs": "30000"})
        assert not ok
        assert "有效数字" in why

    # ---------- 预算 ----------

    def test_预算超过上限被拒(self):
        ok, why = g.validate_action(
            {"action": "adjust_budget", "target": "US_A_01", "reason": "放量",
             "params": {"country": "US", "new_budget": 999999}},
            current={"budget": 100.0})
        assert not ok
        assert "超出允许区间" in why

    def test_预算加个零被拒(self):
        """最典型的事故：30 写成 300（幅度 10 倍）。"""
        ok, why = g.validate_action(
            {"action": "adjust_budget", "target": "US_A_01", "reason": "放量",
             "params": {"country": "US", "new_budget": 300}},
            current={"budget": 30.0})
        assert not ok
        assert "幅度" in why

    def test_预算缩太狠被拒(self):
        ok, why = g.validate_action(
            {"action": "adjust_budget", "target": "US_A_01", "reason": "缩量",
             "params": {"country": "US", "new_budget": 5}},
            current={"budget": 30.0})
        assert not ok
        assert "幅度" in why

    def test_预算合理幅度通过(self):
        ok, _ = g.validate_action(
            {"action": "adjust_budget", "target": "US_A_01", "reason": "ROI 稳定达标放量",
             "params": {"country": "US", "new_budget": 45}},
            current={"budget": 30.0})
        assert ok

    def test_无基线时预算调整被拒(self):
        """查不到当前预算 → 无法算幅度 → fail-closed。"""
        ok, why = g.validate_action(
            {"action": "adjust_budget", "target": "UNKNOWN_01", "reason": "放量",
             "params": {"country": "US", "new_budget": 40}},
            current={})
        assert not ok
        assert "无基线" in why

    def test_预算传字符串被拒(self):
        ok, why = g.validate_action(
            {"action": "adjust_budget", "target": "US_A_01", "reason": "放量",
             "params": {"country": "US", "new_budget": "40"}},
            current={"budget": 30.0})
        assert not ok
        assert "不是有效数字" in why

    def test_预算传bool被拒(self):
        """Python 里 True 是 int 的子类，必须显式排除。"""
        ok, _ = g.validate_action(
            {"action": "adjust_budget", "target": "US_A_01", "reason": "放量",
             "params": {"country": "US", "new_budget": True}},
            current={"budget": 30.0})
        assert not ok

    # ---------- 目标 ----------

    def test_目标把07写成70被拒(self):
        """经典错误：ROAS 0.7 写成 70。"""
        ok, why = g.validate_action(
            {"action": "adjust_target", "target": "US_A_01", "reason": "校准目标",
             "params": {"country": "US", "new_target": 70}},
            current={"target": 0.7})
        assert not ok
        assert "超出允许区间" in why

    def test_目标合理值通过(self):
        ok, _ = g.validate_action(
            {"action": "adjust_target", "target": "US_A_01", "reason": "目标定得过高导致误判",
             "params": {"country": "US", "new_target": 0.65}},
            current={"target": 0.7})
        assert ok

    def test_缺少country被拒(self):
        ok, why = g.validate_action(
            {"action": "adjust_budget", "target": "US_A_01", "reason": "放量",
             "params": {"new_budget": 40}},
            current={"budget": 30.0})
        assert not ok
        assert "country" in why


# ============================================================
# 批量预检
# ============================================================

class TestCheckBatch:
    def _act(self, target, budget=40.0, country="US"):
        return {"action": "adjust_budget", "target": target, "reason": "放量",
                "params": {"country": country, "new_budget": budget}}

    def test_超过数量上限整批拒绝(self):
        actions = [self._act(f"C{i}") for i in range(g.MAX_ACTIONS_PER_RUN + 1)]
        accepted, rejected = g.check_batch(actions)
        assert accepted == []
        assert len(rejected) == 1
        assert "超过上限" in rejected[0]["reason"]

    def test_同对象重复只保留最后一条(self):
        a1 = self._act("US_A_01", budget=40.0)
        a2 = self._act("US_A_01", budget=50.0)
        accepted, rejected = g.check_batch([a1, a2])
        assert len(accepted) == 1
        assert accepted[0]["params"]["new_budget"] == 50.0     # 后者覆盖
        assert len(rejected) == 1

    def test_不同国家不算重复(self):
        accepted, rejected = g.check_batch([
            self._act("US_A_01", country="US"),
            self._act("US_A_01", country="JP"),
        ])
        assert len(accepted) == 2
        assert rejected == []

    def test_正常不超量全部通过(self):
        accepted, rejected = g.check_batch([self._act(f"C{i}") for i in range(3)])
        assert len(accepted) == 3
        assert rejected == []

    def test_空列表不报错(self):
        accepted, rejected = g.check_batch([])
        assert accepted == [] and rejected == []
