#!/usr/bin/env python3
"""判据焊死:Gamma 失败告警按实测稳态重定阈值 + 注册/结算失败拆开判定(2026-08-03)。

背景(实测 1530 轮日志,非推测):
- `register_fail` 稳态分布 p50=6 / p75=9 / p90=13 / p95=15 / p99=22 / max=40
  (每轮最多注册 40 个,故 40 = 100% 失败)。而阈值定在 3 → **76% 的轮次必然触发**。
- `settlement_lookup_fail` 稳态 p50=2 / p99=3 / max=16。
- 旧逻辑把两者**相加**再跟 3 比 → 日志里 1340 条推送,又是洪水告警。

这与 [[test_alerts_truncation]] 修的是同一类病:把稳态事件当异常推。区别在于上次修的是
offset 截断,这次漏网的是 Gamma 失败计数。

两项修复:
1. 阈值抬到实测 p99 之上(不是拍脑袋,是让"正常背景不报警"可验证)。
2. 注册失败与结算失败**拆开**:它们是两条独立链路,相加既掩盖单边真异常,又制造噪音。
   结算失败改为**比率判定**——批量化后每轮检查量从 80 涨到数百,绝对值阈值会失去意义。

铁律:先写判据再改代码(见 CLAUDE.md)。断言 import 被检验对象的常量,不复制数字。
"""
import sys
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import alerts  # noqa: E402


@pytest.fixture
def capture_push(monkeypatch):
    sent = {}

    def fake_send(msg):
        sent["body"] = msg
        return True

    monkeypatch.setattr(alerts, "_send", fake_send)
    return sent


# ---------- 阈值必须高于实测稳态 ----------

# 实测 1530 轮的 register_fail 分位数(写死在判据里,便于日后复核阈值是否仍站得住)
OBSERVED_REGISTER_FAIL_P99 = 22


def test_register_threshold_above_observed_p99():
    """阈值必须高于实测 p99=22,否则正常背景仍会 ~1% 概率误报;定在 3 时是 76%。"""
    assert alerts.REGISTER_FAIL_THRESHOLD > OBSERVED_REGISTER_FAIL_P99


@pytest.mark.parametrize("rf", [0, 3, 6, 9, 13, 15, 22])  # 实测 p50→p99 的背景值域
def test_steady_state_register_fail_does_not_push(capture_push, rf):
    """稳态量级的注册失败不推送 —— 这是本次修复的核心承诺(消除 1340 条噪音)。"""
    pushed = alerts.maybe_alert({"register_fail": rf, "total_markets_polled": 50})
    assert pushed is False, f"register_fail={rf} 在实测稳态内,不该推送"
    assert "body" not in capture_push


def test_systemic_register_failure_still_pushes(capture_push):
    """注册链路系统性失败(超阈值)仍须推送 —— 降噪不等于变聋。"""
    pushed = alerts.maybe_alert(
        {"register_fail": alerts.REGISTER_FAIL_THRESHOLD + 1, "total_markets_polled": 50})
    assert pushed is True
    assert "注册" in capture_push["body"]


# ---------- 注册失败与结算失败必须拆开 ----------

def test_fails_are_not_summed(capture_push):
    """两条独立链路各自在稳态内,相加也不得触发(旧逻辑正是相加 → 每轮必炸)。"""
    pushed = alerts.maybe_alert({
        "register_fail": OBSERVED_REGISTER_FAIL_P99,   # 稳态上沿
        "settlement_lookup_fail": 3,                    # 结算稳态 p99
        "settlement_checked": 800,
        "total_markets_polled": 50,
    })
    assert pushed is False, "两条链路各自正常,不得因相加而告警"


def test_settlement_failure_judged_by_ratio(capture_push):
    """结算失败按比率判定:批量化后每轮检查数百个,绝对值阈值会失去意义。
    少量失败(远低于比率红线)不推。"""
    pushed = alerts.maybe_alert({
        "settlement_lookup_fail": 30, "settlement_checked": 800,
        "total_markets_polled": 50,
    })
    assert pushed is False, "30/800 属正常损耗,不该推"


def test_settlement_systemic_failure_pushes(capture_push):
    """结算链路整体挂掉(大比例查不到)= 真异常,必须推。"""
    pushed = alerts.maybe_alert({
        "settlement_lookup_fail": 700, "settlement_checked": 800,
        "total_markets_polled": 50,
    })
    assert pushed is True
    assert "结算" in capture_push["body"]


def test_small_sample_does_not_trigger_ratio(capture_push):
    """样本极小时比率会剧烈抖动(2/2=100%),须有下限保护,否则又是新噪音源。"""
    pushed = alerts.maybe_alert({
        "settlement_lookup_fail": 2, "settlement_checked": 2,
        "total_markets_polled": 50,
    })
    assert pushed is False, "小样本比率抖动不该告警"


# ---------- 回归:已修好的行为不得被本次改动碰坏 ----------

def test_firehose_fail_unaffected(capture_push):
    """firehose 抽风照旧推(与本次阈值改动无关)。"""
    assert alerts.maybe_alert({"firehose_fail": 1}) is True
    assert "firehose" in capture_push["body"]


def test_truncation_downgrade_unaffected(capture_push):
    """上次修的截断降级不得回退(稳态截断仍不推)。"""
    assert alerts.maybe_alert({"offset_overflow_count": 8}) is False


def test_all_normal_is_silent(capture_push):
    """一个真实的正常轮次(取实测中位数)必须完全静默。"""
    pushed = alerts.maybe_alert({
        "offset_overflow_count": 0, "register_fail": 6, "settlement_lookup_fail": 2,
        "settlement_checked": 800, "firehose_fail": 0, "total_markets_polled": 50,
    })
    assert pushed is False
