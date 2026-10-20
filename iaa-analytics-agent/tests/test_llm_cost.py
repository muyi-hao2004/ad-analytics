# -*- coding: utf-8 -*-
"""
llm_cost 的纯函数测试。

这里不碰网络、不碰数据库，只验证「token → 钱」的换算。
之所以单独测：算错了不会报错，只会给出一个看起来合理的错数字，
而这类错数字比没有数字更糟——它会被写进汇报里。
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import llm_cost


class TestExtractUsage:
    def test_标准字段(self):
        resp = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=1000, completion_tokens=200, total_tokens=1200))
        u = llm_cost.extract_usage(resp)
        assert u["prompt_tokens"] == 1000
        assert u["completion_tokens"] == 200
        assert u["total_tokens"] == 1200
        assert u["calls"] == 1

    def test_没有usage也不崩(self):
        """本地推理引擎、部分代理网关不返回 usage。"""
        u = llm_cost.extract_usage(SimpleNamespace(choices=[]))
        assert u["calls"] == 1          # 调用确实发生了，这个要记
        assert u["prompt_tokens"] == 0
        assert u["total_tokens"] == 0

    def test_total缺失时自己加(self):
        resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=300, completion_tokens=50))
        u = llm_cost.extract_usage(resp)
        assert u["total_tokens"] == 350

    def test_deepseek缓存字段(self):
        resp = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
            prompt_cache_hit_tokens=800))
        u = llm_cost.extract_usage(resp)
        assert u["cache_hit_tokens"] == 800

    def test_openai缓存字段在details里(self):
        """OpenAI 把缓存明细放在 prompt_tokens_details.cached_tokens。"""
        resp = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=640)))
        u = llm_cost.extract_usage(resp)
        assert u["cache_hit_tokens"] == 640


class TestMerge:
    def test_累加(self):
        a = {"calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
             "total_tokens": 110, "cache_hit_tokens": 0}
        b = {"calls": 2, "prompt_tokens": 250, "completion_tokens": 20,
             "total_tokens": 270, "cache_hit_tokens": 50}
        out = llm_cost.merge(a, b)
        assert out["calls"] == 3
        assert out["prompt_tokens"] == 350
        assert out["cache_hit_tokens"] == 50
        assert a["calls"] == 1, "不能修改入参（调用方可能还在用）"

    def test_merge_all空列表(self):
        out = llm_cost.merge_all([])
        assert out == llm_cost.EMPTY


class TestFinalize:
    def test_基本换算(self, monkeypatch):
        monkeypatch.setenv("LLM_INPUT_PRICE_PER_MTOK", "1.0")
        monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MTOK", "2.0")
        monkeypatch.setenv("LLM_USD_CNY", "7.0")

        u = {"calls": 1, "prompt_tokens": 1_000_000, "completion_tokens": 500_000,
             "total_tokens": 1_500_000, "cache_hit_tokens": 0}
        st = llm_cost.finalize(u)
        # 1M 输入 × $1 + 0.5M 输出 × $2 = $2
        assert st["cost_usd"] == pytest.approx(2.0)
        assert st["cost_cny"] == pytest.approx(14.0)

    def test_缓存命中打折(self, monkeypatch):
        monkeypatch.setenv("LLM_INPUT_PRICE_PER_MTOK", "1.0")
        monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MTOK", "0.0")

        full = llm_cost.finalize({"calls": 1, "prompt_tokens": 1_000_000,
                                  "completion_tokens": 0, "total_tokens": 0,
                                  "cache_hit_tokens": 0})
        half = llm_cost.finalize({"calls": 1, "prompt_tokens": 1_000_000,
                                  "completion_tokens": 0, "total_tokens": 0,
                                  "cache_hit_tokens": 1_000_000})
        assert full["cost_usd"] == pytest.approx(1.0)
        # 全部命中 → 按 1/10 价
        assert half["cost_usd"] == pytest.approx(0.1)
        assert half["cache_hit_rate"] == 1.0

    def test_价格配置非法时回退默认(self, monkeypatch):
        monkeypatch.setenv("LLM_INPUT_PRICE_PER_MTOK", "abc")
        monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MTOK", "-5")
        st = llm_cost.finalize({"calls": 1, "prompt_tokens": 1000,
                                "completion_tokens": 100, "total_tokens": 1100,
                                "cache_hit_tokens": 0})
        assert st["cost_usd"] > 0, "配置写错也要能算出数，不能返回 NaN 或负值"

    def test_平均每次输入(self):
        st = llm_cost.finalize({"calls": 4, "prompt_tokens": 8000,
                                "completion_tokens": 0, "total_tokens": 8000,
                                "cache_hit_tokens": 0})
        assert st["input_per_call"] == 2000.0

    def test_零调用不除零(self):
        st = llm_cost.finalize(dict(llm_cost.EMPTY))
        assert st["calls"] == 0
        assert st["cost_usd"] == 0
        assert st["input_per_call"] == 0.0

    def test_命中数不会超过输入总数(self):
        """脏数据（hit > prompt）时按 prompt 截断，避免算出负数成本。"""
        st = llm_cost.finalize({"calls": 1, "prompt_tokens": 100,
                                "completion_tokens": 0, "total_tokens": 100,
                                "cache_hit_tokens": 999})
        assert st["cost_usd"] >= 0
        assert st["cache_hit_rate"] == 1.0


class TestFormatLine:
    def test_输出包含关键字段(self):
        line = llm_cost.format_line({"calls": 5, "prompt_tokens": 12345,
                                     "completion_tokens": 678, "cache_hit_rate": 0.42,
                                     "cost_usd": 0.0123, "cost_cny": 0.0873})
        assert "5 次调用" in line
        assert "42.0%" in line
        assert "0.0123" in line
