#!/usr/bin/env python3
"""判据焊死:真值断供守护 —— newly_resolved 连续为 0 要报警(2026-08-03)。

为什么加这条:2026-08-03 发现结算真值采集静默死了 11 天。最讽刺的是同期告警系统推了
1340 条,**没有一条是关于真值断供的** —— 监控只覆盖"接口报错"这类显式失败,对"一切
正常但产出为 0"这类**静默失败**完全是瞎的。这条守护就是补这个盲点。

⚠️ 本守护自身极易变成第三次告警洪水(前两次:offset 截断、Gamma 失败阈值)。故判据里
把**防洪**焊死成硬要求:断供期间不得每轮推送,只能按固定间隔复述。

阈值依据(实测,非拍脑袋):注册表近 14 天每天自然结算 ~2,904 个市场(中位)
→ 折算每轮(144 轮/天)期望 ~20 个。故"连续多轮一个都没有"是强异常。
⚠️ 局限:修复后仅有 6 轮观测且全是清存量轮次,**尚无稳态分布**。阈值取保守值,
并由 settlement_watcher 每轮记录 zero_streak,攒够一周后应回来用真实分布校准。
"""
import sys
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import alerts  # noqa: E402
import settlement_watcher as sw  # noqa: E402


def _recorder(sink: dict):
    """拦截真实发送并记录正文;必须返回 True(maybe_alert 直接把它当返回值)。"""
    def fake_send(msg):
        sink["body"] = msg
        return True
    return fake_send


# ---------- 连零计数:进位 / 归零 / 中立 ----------

def test_streak_increments_when_work_done_but_nothing_resolved():
    """有市场可查却一个都没结算 —— 正是本次故障的形态(checked=80, newly=0 连续 11 天)。"""
    assert sw.next_zero_streak(3, newly_resolved=0, checked=800) == 4


def test_streak_resets_on_any_resolution():
    """只要拿到真值就说明链路通,立刻归零(不许旧账拖成误报)。"""
    assert sw.next_zero_streak(17, newly_resolved=1, checked=800) == 0


def test_streak_unchanged_when_nothing_to_check():
    """pending 为空 = 每个到期市场都已有真值 = 成功,不是失败。
    既不进位(否则误报)也不归零(否则掩盖),保持中立。"""
    assert sw.next_zero_streak(5, newly_resolved=0, checked=0) == 5


# ---------- 告警判定:该响时响 ----------

def test_no_alarm_below_threshold(monkeypatch):
    """未到阈值不得报警 —— 单轮为 0 在稳态下完全正常(结算有簇发和夜间静默)。"""
    sent = {}
    monkeypatch.setattr(alerts, "_send", _recorder(sent))
    for streak in range(alerts.TRUTH_SUPPLY_ZERO_CYCLES):
        assert alerts.maybe_alert({"truth_supply_zero_streak": streak}) is False, \
            f"streak={streak} 未到阈值,不该报警"
    assert not sent


def test_alarm_fires_at_threshold(monkeypatch):
    """到达阈值必须报警 —— 这是这条守护存在的全部理由。"""
    sent = {}
    monkeypatch.setattr(alerts, "_send", _recorder(sent))
    assert alerts.maybe_alert(
        {"truth_supply_zero_streak": alerts.TRUTH_SUPPLY_ZERO_CYCLES}) is True
    assert "真值" in sent["body"], "正文要说人话,直指真值断供"


def test_threshold_grounded_in_measured_rate():
    """阈值须对应足够长的静默窗(≥2 小时),否则簇发间隙就会误报。
    实测每轮期望 ~20 个结算,10 分钟一轮 → 阈值轮数 × 10 分钟即静默时长。"""
    assert alerts.TRUTH_SUPPLY_ZERO_CYCLES * 10 >= 120


# ---------- ⭐防洪:断供期间不得每轮推送 ----------

def test_does_not_flood_during_outage(monkeypatch):
    """断供持续时必须按固定间隔复述,不得每轮推 —— 否则 11 天 = 1584 条,
    正是这次刚修掉的病(旧告警 76% 轮次必推,共 1340 条)。"""
    pushes = []
    monkeypatch.setattr(alerts, "_send", lambda m: pushes.append(m) or True)
    n = alerts.TRUTH_SUPPLY_ZERO_CYCLES
    for streak in range(1, n * 3 + 1):          # 模拟连续断供 3 个间隔
        alerts.maybe_alert({"truth_supply_zero_streak": streak})
    assert len(pushes) == 3, f"3 个间隔应恰好推 3 条,实推 {len(pushes)} 条"


def test_silent_between_repeats(monkeypatch):
    """两次复述之间必须完全静默。"""
    pushes = []
    monkeypatch.setattr(alerts, "_send", lambda m: pushes.append(m) or True)
    n = alerts.TRUTH_SUPPLY_ZERO_CYCLES
    for streak in range(n + 1, n * 2):          # 阈值之后、下个间隔之前
        assert alerts.maybe_alert({"truth_supply_zero_streak": streak}) is False


# ---------- 回归:不得碰坏已修好的行为 ----------

def test_healthy_cycle_stays_silent(monkeypatch):
    """一个真实的健康轮次(取实测值)必须完全静默 —— 含本守护在内。"""
    monkeypatch.setattr(alerts, "_send", lambda m: pytest.fail(f"不该推送: {m}"))
    assert alerts.maybe_alert({
        "truth_supply_zero_streak": 0, "newly_resolved": 50, "settlement_checked": 800,
        "settlement_lookup_fail": 0, "register_fail": 6, "offset_overflow_count": 0,
        "firehose_fail": 0, "total_markets_polled": 50,
    }) is False


def test_absent_streak_key_is_safe(monkeypatch):
    """计数里没有该字段时不得崩(旧调用方 / 降级路径)。"""
    monkeypatch.setattr(alerts, "_send", lambda m: True)
    assert alerts.maybe_alert({"register_fail": 6}) is False


# ---------- 状态持久化 ----------

def test_streak_survives_across_cycles(monkeypatch, tmp_path):
    """每轮是独立进程,连零计数必须落盘才能跨轮累积(否则永远数不到阈值)。"""
    monkeypatch.setattr(sw, "STREAK_FILE", tmp_path / "streak.json")
    sw._save_streak(7)
    assert sw._load_streak() == 7


def test_corrupt_streak_file_degrades_safely(monkeypatch, tmp_path):
    """状态文件损坏只该让计数从头开始,不得让整个采集周期崩。"""
    f = tmp_path / "streak.json"
    f.write_text("{ 坏掉的 json")
    monkeypatch.setattr(sw, "STREAK_FILE", f)
    assert sw._load_streak() == 0
