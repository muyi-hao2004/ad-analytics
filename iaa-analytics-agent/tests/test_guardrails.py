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
            current={"installs": 21_000, "days": 8})
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
        """边界值：等于阈值应当放行，避免规则写成 >= 还是 > 的歧义。

        注意 days 必须一起给：护栏对删除有两个独立门槛，缺任一个都会
        fail-closed。这正是「加字段要同步所有调用方」的提醒。
        """
        ok, _ = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={"installs": g.MIN_INSTALLS_FOR_DELETE,
                     "days": g.MIN_DAYS_FOR_DELETE})
        assert ok

    def test_安装量少一次被拒(self):
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={"installs": g.MIN_INSTALLS_FOR_DELETE - 1,
                     "days": g.MIN_DAYS_FOR_DELETE})
        assert not ok

    def test_天数够但量不够也被拒(self):
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={"installs": 30, "days": 30})
        assert not ok and "样本" in why

    def test_量够但天数不够被拒(self):
        """H01 的坑：8000 安装完全够，但只跑了 3 天，D7 还没长熟。

        两个门槛是 AND 关系，各自解决一个独立问题：
        量不够是噪声，天数不够是数据未成熟。
        """
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_050", "reason": "ROI 5%"},
            current={"installs": 8000, "days": 3})
        assert not ok
        assert "天" in why and "未成熟" in why

    def test_查不到天数也拒绝(self):
        """只给 installs 不给 days → fail-closed，不能默认当成跑满了。"""
        ok, why = g.validate_action({
            "action": "delete_sub_channel", "target": "ch_003", "reason": "ROI 低"},
            current={"installs": 50000})
        assert not ok
        assert "天数" in why

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


# ============================================================
# 入口一致性：护栏不能有「绕过去的路」
# ============================================================
#
# 这组测试源于一次审计。当时用 grep 找所有调用平台写方法的地方，发现三处：
#   agent._execute_action     → 过了护栏 ✓
#   mcp_server.submit_suggestion → 过了护栏，但组装 current 漏了 installs（误拒）
#   app.py 三个 Web 接口      → 完全没过护栏 ✗
#
# 结论：护栏的层级不看它写得多好，看**还有没有别的路能打到平台**。
# 只加一层而留着旁路，等于没加。

class TestWeb接口也过护栏:
    """app.py 的写接口是人手动触发的，但打的是同一个平台，规则必须一致。"""

    @pytest.fixture(autouse=True)
    def _app(self):
        pytest.importorskip("fastapi", reason="Web 接口测试需要 fastapi")
        import app as appmod
        self._guard = appmod._guard

    def test_删除描述性target被拦(self):
        ok, why = self._guard({
            "action": "delete_sub_channel",
            "target": "我认为应该删除这个子渠道", "reason": "手动"})
        assert not ok
        assert "形态异常" in why

    def test_删除查不到样本量被拦(self):
        ok, why = self._guard({
            "action": "delete_sub_channel", "target": "ch_999999", "reason": "手动"})
        assert not ok
        assert "安装量" in why

    def test_预算超区间被拦(self):
        ok, _ = self._guard({
            "action": "adjust_budget", "target": "US_A_01",
            "params": {"country": "US", "new_budget": 999999}, "reason": "手动"})
        assert not ok

    def test_目标把070写成70被拦(self):
        # LLM 和人都容易犯：0.70 写成 70。差 100 倍且不会报错。
        ok, _ = self._guard({
            "action": "adjust_target", "target": "US_A_01",
            "params": {"country": "US", "new_target": 70}, "reason": "手动"})
        assert not ok


class Test组装逻辑收口:
    """组装「当前状态」只能有一个实现，否则两个调用方迟早漂移。"""

    def test_删除动作要带installs(self):
        import agent
        cur = agent._current_context_for(
            {"action": "delete_sub_channel", "target": "ch_001"})
        assert "installs" in cur, "delete 必须带 installs，否则护栏只能 fail-closed 误拒"

    def test_预算动作要带预算基线(self):
        import agent
        cur = agent._current_context_for({
            "action": "adjust_budget", "target": "US_A_01",
            "params": {"country": "US"}})
        assert "budget" in cur

    def test_未知动作返回空(self):
        import agent
        assert agent._current_context_for({"action": "nope", "target": "x"}) == {}


class Test组装逻辑不漂移:
    """护栏需要的「当前状态」有**两个来源**，必须字段一致。

    生产：agent._current_context_for()  查真实 parquet
    评测：judge.current_from_snapshot()  读场景快照（评测必须与真实库隔离）

    两份实现是必要的（快照数据不能来自库），但**字段集合必须一模一样**。
    今天加 days 门槛时就差点漏掉评测侧：如果那边只返回 installs，
    所有场景的 days 都会是 None，护栏 fail-closed 全部拒绝，
    报表会显示「零误删」——一个看起来最好的假结果。
    """

    @pytest.fixture(autouse=True)
    def _mods(self):
        evaldir = os.path.join(os.path.dirname(__file__), "..", "eval")
        if evaldir not in sys.path:
            sys.path.insert(0, evaldir)
        import judge as jm
        import scenarios
        import agent
        import config
        if not (config.ADS_DIR / "sub_channel_table.parquet").exists():
            pytest.skip("缺 sub_channel_table.parquet，跳过字段一致性检查")
        self.jm, self.sc, self.agent = jm, scenarios, agent

    def _case(self, cid):
        return [c for c in self.sc.build_cases() if c.id == cid][0]

    def test_删除动作两处字段一致(self):
        case = self._case("H01")
        prod = self.agent._current_context_for(
            {"action": "delete_sub_channel", "target": "ch_001"})
        ev = self.jm.current_from_snapshot(
            case, {"action": "delete_sub_channel", "target": "ch_050"})
        assert set(prod) == set(ev), f"生产 {set(prod)} vs 评测 {set(ev)}"
        assert "days" in ev and ev["days"] == 3, "H01 的 days=3 必须能读出来"

    def test_预算动作两处字段一致(self):
        case = self._case("S05")
        prod = self.agent._current_context_for(
            {"action": "adjust_budget", "target": "US_A_01",
             "params": {"country": "US"}})
        cfg = (case.snapshot.get("当前配置") or [{}])[0]
        ev = self.jm.current_from_snapshot(case, {
            "action": "adjust_budget", "target": cfg.get("campaign_id"),
            "params": {"country": (cfg.get("countries") or [{}])[0].get("country")}})
        assert set(prod) == set(ev), f"生产 {set(prod)} vs 评测 {set(ev)}"
