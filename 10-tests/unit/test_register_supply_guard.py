#!/usr/bin/env python3
"""判据焊死:注册链路断供守护 —— 连续多轮没登记上新市场就告警(2026-08-03)。

为什么改这条:原告警是"数失败了几个"(register_fail > 阈值)。但单次注册失败**会自愈**
—— 失败的市场还在交易,下一轮仍会出现在 firehose 里被重新登记。稳态每轮失败 ~6 个纯属
正常损耗,故"数失败个数"是**错的形状**:阈值定低了是洪水(旧阈值 3 → 76% 轮次必推,
共 1340 条),定高了又抓不住真事故。

真正的事故形态是:**接口挂了/被封 → 全部注册失败 → 新市场再也进不来 → 宇宙悄悄停止
增长**。该问的不是"报了几个错",而是"**成功登记了几个**"—— 与真值断供守护完全同形
([[test_truth_supply_guard]]),都属于"一切正常但产出为 0"的静默失败。

阈值依据(实测 1541 轮,非拍脑袋):
  new_registered 中位 34/轮,为 0 的轮次占 3.8%,**自然出现的最长连零 = 10 轮**。
  取 18 轮(3 小时)= 实测最长自然连零的近 2 倍余量。
  且本守护只在"确实有市场可登记"时进位(见下),故实际连零比实测统计的更难达成。
"""
import sys
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import alerts  # noqa: E402
import cycle_state  # noqa: E402

OBSERVED_MAX_NATURAL_ZERO_RUN = 10  # 实测 1541 轮里自然出现的最长连零


def _recorder(sink: dict):
    def fake_send(msg):
        sink["body"] = msg
        return True
    return fake_send


# ---------- 「没活可干」不等于「干砸了」 ----------

def test_nothing_to_register_is_neutral():
    """本轮没有新市场可登记 = 宇宙里没新东西,不是故障。
    既不进位(否则误报)也不归零(否则掩盖),保持中立。"""
    assert cycle_state.next_zero_streak(5, newly=0, attempted=0) == 5


def test_all_attempts_failed_increments():
    """有市场要登记却一个都没成功 —— 正是接口挂掉的形态,必须进位。"""
    assert cycle_state.next_zero_streak(3, newly=0, attempted=40) == 4


def test_any_success_resets():
    """只要登记上一个就说明链路通,立刻归零。"""
    assert cycle_state.next_zero_streak(17, newly=1, attempted=40) == 0


# ---------- 阈值必须高于实测自然连零 ----------

def test_threshold_above_observed_natural_run():
    """阈值必须高于实测最长自然连零(10 轮),否则正常波动就会误报。"""
    assert alerts.REGISTER_ZERO_CYCLES > OBSERVED_MAX_NATURAL_ZERO_RUN


def test_no_alarm_below_threshold(monkeypatch):
    monkeypatch.setattr(alerts, "_send", lambda m: pytest.fail(f"不该推送: {m}"))
    for streak in range(alerts.REGISTER_ZERO_CYCLES):
        assert alerts.maybe_alert({"register_zero_streak": streak}) is False


def test_alarm_fires_at_threshold(monkeypatch):
    sent = {}
    monkeypatch.setattr(alerts, "_send", _recorder(sent))
    assert alerts.maybe_alert({"register_zero_streak": alerts.REGISTER_ZERO_CYCLES}) is True
    assert "注册" in sent["body"] and "新市场" in sent["body"], "正文要说人话"


# ---------- ⭐防洪 ----------

def test_does_not_flood_during_outage(monkeypatch):
    """断供期间按固定间隔复述,不得每轮推 —— 否则又是第三次告警洪水。"""
    pushes = []
    monkeypatch.setattr(alerts, "_send", lambda m: pushes.append(m) or True)
    n = alerts.REGISTER_ZERO_CYCLES
    for streak in range(1, n * 3 + 1):
        alerts.maybe_alert({"register_zero_streak": streak})
    assert len(pushes) == 3, f"3 个间隔应恰好推 3 条,实推 {len(pushes)} 条"


# ---------- 旧的「数失败个数」告警必须退役 ----------

@pytest.mark.parametrize("rf", [0, 6, 22, 40, 100])
def test_register_fail_count_no_longer_alerts(monkeypatch, rf):
    """失败个数本身不再触发告警(单次失败会自愈,数个数是错的形状)。
    失败仍逐轮进日志/心跳留痕,只是不再打扰人。"""
    monkeypatch.setattr(alerts, "_send", lambda m: pytest.fail(f"不该推送: {m}"))
    assert alerts.maybe_alert({"register_fail": rf, "total_markets_polled": 50}) is False


def test_old_threshold_constant_is_gone():
    """旧常量必须删除,而不是留着不用 —— 留着会让人以为它还在生效。"""
    assert not hasattr(alerts, "REGISTER_FAIL_THRESHOLD")


# ---------- 状态持久化 ----------

def test_streak_survives_across_cycles(monkeypatch, tmp_path):
    """每轮是独立进程,连零计数必须落盘才能跨轮累积。"""
    f = tmp_path / "reg.json"
    cycle_state.write_streak(f, 7)
    assert cycle_state.read_streak(f) == 7


def test_corrupt_state_degrades_safely(tmp_path):
    """状态损坏只该让计数从头开始,不得让采集周期崩。"""
    f = tmp_path / "reg.json"
    f.write_text("{ 坏掉的")
    assert cycle_state.read_streak(f) == 0
