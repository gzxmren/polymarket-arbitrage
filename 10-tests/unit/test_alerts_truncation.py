#!/usr/bin/env python3
"""判据焊死:offset 截断告警降级(2026-07-23 用户明令)。

背景:offset 截断是巨盘历史回填触上限的**稳态自愈事件**(近端已保留 + 下轮压频回填),
实测心跳分布中位 1 / p90 3 / 最大 8(首日 82 条),68% 周期 >0。原逻辑 `ov>0` 就推
Telegram → 每轮洪水告警。降级后:截断只进汇总日志 + 心跳 parquet,**只有尖峰(≥阈值)
才当真异常推送**;firehose 抽风 / Gamma 失败等真异常保持原样推送。

铁律:先写判据再改代码(见 CLAUDE.md)。这些断言直接 import 被检验对象自己的阈值常量,
不复制粘贴数字,以保证"红线没放水"可验证。
"""
import sys
from pathlib import Path

import pytest

# 11-collector 未在 conftest 路径里,本测试自带
COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import alerts  # noqa: E402


@pytest.fixture
def capture_push(monkeypatch):
    """拦截真实发送,记录是否推送 + 推送正文,避免打真 Telegram。"""
    sent = {}

    def fake_send(msg):
        sent["body"] = msg
        return True

    monkeypatch.setattr(alerts, "_send", fake_send)
    return sent


# ---------- 稳态截断:不再推送 ----------

@pytest.mark.parametrize("ov", [1, 2, 3, 8])  # 实测心跳里的正常背景值域
def test_steady_state_truncation_does_not_push(capture_push, ov):
    """稳态量级的 offset 截断,单独出现时不推 Telegram(降级为汇总日志)。"""
    pushed = alerts.maybe_alert({"offset_overflow_count": ov, "total_markets_polled": 50})
    assert pushed is False, f"offset_overflow={ov} 属稳态自愈,不该推送"
    assert "body" not in capture_push


def test_threshold_is_above_observed_steady_state():
    """阈值必须高于实测稳态峰值(首日最大 8),否则等于没降级。"""
    assert alerts.OFFSET_OVERFLOW_ALERT_THRESHOLD > 8


# ---------- 真异常:仍然推送 ----------

def test_offset_spike_pushes(capture_push):
    """offset 截断尖峰(≥阈值)= 轮询系统性追不上,是真异常,推送。"""
    pushed = alerts.maybe_alert(
        {"offset_overflow_count": alerts.OFFSET_OVERFLOW_ALERT_THRESHOLD,
         "total_markets_polled": 50})
    assert pushed is True
    assert "截断" in capture_push["body"]


def test_firehose_fail_still_pushes(capture_push):
    """firehose 抽风是真异常,与截断降级无关,照旧推送。"""
    pushed = alerts.maybe_alert({"firehose_fail": 1, "offset_overflow_count": 2})
    assert pushed is True
    assert "firehose" in capture_push["body"]


def test_register_outage_still_pushes(capture_push):
    """注册链路真异常仍照旧推送(与截断降级无关)。

    ⚠️ 本用例 2026-08-03 改写:原断言是"register_fail 个数超阈值就推"。该判定已**退役** ——
    单次注册失败会自愈(市场还在交易,下轮重登),数个数是错的形状。现改为断供守护
    (连续多轮零登记),理由与实测依据见 test_register_supply_guard.py。
    保留本用例是为了守住"真异常必须推"这一头,不让降噪变成变聋。"""
    pushed = alerts.maybe_alert(
        {"register_zero_streak": alerts.REGISTER_ZERO_CYCLES, "offset_overflow_count": 1})
    assert pushed is True
    assert "注册" in capture_push["body"]


def test_steady_truncation_does_not_ride_along_on_real_alert(capture_push):
    """真异常触发推送时,稳态截断不该搭车塞进正文(否则又变噪音)。"""
    pushed = alerts.maybe_alert({"firehose_fail": 1, "offset_overflow_count": 2})
    assert pushed is True
    assert "截断" not in capture_push["body"], "稳态截断不该出现在告警正文里"


def test_nothing_when_all_normal(capture_push):
    """全正常(无抽风、截断在稳态、Gamma 正常)→ 不推。"""
    pushed = alerts.maybe_alert(
        {"offset_overflow_count": 3, "register_fail": 1, "firehose_fail": 0,
         "total_markets_polled": 50})
    assert pushed is False
