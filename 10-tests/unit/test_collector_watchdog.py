#!/usr/bin/env python3
"""判据:看门狗的「持续」必须真的被数出来,且冷却不许被会变的数字冲垮(2026-08-20)。

## 由来(实测,非假想)

2026-08-19 晚 ~ 08-20 03:15,采集器空转约 14 小时(110 轮里 63 轮 firehose 一笔没抓到)。
用户 Telegram 收到约 130 条消息,其中 **118 条一模一样**:

    🟡 最近一轮 firehose 抽风（采样 0 笔），若持续须查接口/IP

两个缺陷叠在一起:

**缺陷一:看门狗没读它手上已有的连续计数。**
心跳里早就有 `trade_flow_outage_streak`(`run_cycle.py` 每轮持久化、
`storage_engine.AUDIT_FIELDS` 白名单里、`alerts.py` 正拿它做分档升级),
而 `collector_watchdog._heartbeat_problems` 只读了 `firehose_fail`,
把同一个 dict 里的 streak 字段**放着没用** ⇒ 那句「若持续须查」
在持续 14 小时之后仍然是 🟡、仍然是同一句话,**永远不会自己变严重**。
形状:「记录事实 vs 使用事实,只接一头」。

**缺陷二(真正的洪水闸门):冷却签名里带了会变的数字。**
签名 = 所有问题行拼接。而其中一行是「采集器有 **N** 条告警发不出去」,
实测 N 取过 1/2/3/7/8/9/20 ⇒ 签名每次都不同 ⇒ 4h 冷却**完全失效**。
实测:看门狗触发 136 次,推出去 63 次(若冷却有效应为每 4h 一条)。

⚠️ 两条必须一起修:只做缺陷一(把轮数写进消息)而不修签名,
**轮数每轮都变 ⇒ 洪水只会更大**。

## 阈值不新定

沿用 `alerts.TRADE_FLOW_OUTAGE_CYCLES`(=6)。它已有实测分布支撑
(08-05~08-17:良性最长 3 轮、事故 56 轮,取良性最大值 ×2);
2026-08-20 独立重算(1 个月 3,218 轮、71 段空转)得 p90=5 / p95=6,支持同一取值。
⛔ 判据一律 import 该常量,不许在这里复制那个 6。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

pytest.importorskip("pyarrow")
import alerts  # noqa: E402
import collector_watchdog as cw  # noqa: E402

WD_SRC = (COLLECTOR_DIR / "collector_watchdog.py").read_text(encoding="utf-8")


def _hb(**kw) -> dict:
    """一条最小心跳。默认全健康,由用例只改自己关心的那个字段。"""
    base = {"firehose_fail": 0, "trade_flow_outage_streak": 0, "alert_queue_depth": 0}
    base.update(kw)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# 缺陷一:「持续」必须被数出来,并按档升级
# ─────────────────────────────────────────────────────────────────────────────
def test_healthy_heartbeat_stays_completely_silent():
    """防洪的一头:稳态必须一句话都不说,否则真信号会被噪音淹掉。"""
    assert cw._heartbeat_problems(_hb()) == []


def test_single_cycle_jitter_is_yellow_not_red():
    """实测 55% 的空转段只有 1 轮(15 分钟)且会自愈 —— 这一档不该吓人。"""
    ps = cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=1))
    assert len(ps) == 1
    assert ps[0].startswith("🟡"), f"单轮抖动不该报红:{ps[0]}"


def test_sustained_outage_ESCALATES_to_red_at_the_measured_threshold():
    """⭐⭐ 本文件最重要的一条:持续到红线就必须变 🔴,不许还是那句 🟡。

    这正是 08-19 那次 14 小时里【从没发生过】的事。
    """
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    below = cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=n - 1))
    at = cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=n))
    assert below and below[0].startswith("🟡"), f"红线之下应为 🟡:{below}"
    assert at and at[0].startswith("🔴"), f"到红线必须升级为 🔴:{at}"


def test_escalated_message_says_HOW_LONG_it_has_been_going():
    """「已持续多久」必须写在消息里 —— 否则看的人还是只能靠数消息条数来判断。"""
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    msg = cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=n))[0]
    assert str(n) in msg, f"消息里没有连续轮数:{msg}"
    # ⭐ reviewer 变异体 B:把 /60 改成 /600,原判据仍全绿 —— 因为只断言了「小时」二字。
    #    换算错了不会被抓到,而这个数字正是用户判断严重程度的依据。
    #    期望值用被检验对象自己的常量算,不在判据里写死。
    want = f"{n * alerts.cycle_minutes() / 60:.1f} 小时"
    assert want in msg, f"时长换算不对,期望包含 {want!r}:{msg}"


def test_the_never_escalating_wording_is_gone():
    """⛔「若持续须查」是一句永远不会自己变严重的话 —— 它正是本次事故的病灶。"""
    for streak in (1, 3, alerts.TRADE_FLOW_OUTAGE_CYCLES, 56):
        for p in cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=streak)):
            assert "若持续须查" not in p, f"streak={streak} 仍在说那句话:{p}"
    assert "若持续须查" not in WD_SRC, "源码里还留着那句措辞"


def test_threshold_is_IMPORTED_from_alerts_not_copied():
    """⛔ 照抄结构而不抽象(CLAUDE.md,已犯 3 次)。红线只允许存在一份。"""
    assert "alerts.TRADE_FLOW_OUTAGE_CYCLES" in WD_SRC, "看门狗没有 import 那条红线"
    assert "TRADE_FLOW_OUTAGE_CYCLES =" not in WD_SRC, "看门狗里又定义了一份红线"


def test_streak_field_is_the_one_already_in_the_heartbeat_schema():
    """接的必须是【已存在】的那个字段,不许新造第五个连零计数器。"""
    import storage_engine as se
    assert "trade_flow_outage_streak" in se.AUDIT_FIELDS
    assert "trade_flow_outage_streak" in WD_SRC


def test_missing_streak_field_degrades_to_yellow_not_crash():
    """老心跳(升级前写的)没有这个字段。不许崩,也不许因此判红。"""
    ps = cw._heartbeat_problems({"firehose_fail": 1})
    assert len(ps) == 1 and ps[0].startswith("🟡")
    # ⭐ reviewer 变异体 A:去掉 max(1,...) 这个下限,原判据仍全绿 ——
    #    因为它只断言了「是 🟡」。而本函数的注释明说要防「已连续 0 轮」这种
    #    自相矛盾的话,却没有任何判据在测那句话。
    assert "已连续 1 轮" in ps[0], f"缺字段时说了自相矛盾的轮数:{ps[0]}"
    assert "0 轮" not in ps[0]


def test_corrupt_streak_field_does_not_swallow_the_other_checks():
    """⛔ CLAUDE.md 铁律:一个附加功能坏掉,不许打断调用方其余职责。

    实测(造真坏输入):'corrupt'→ValueError、[1]/{}→TypeError。
    `_heartbeat_problems` 是 check() 的最后一步 —— 它抛异常会把前面已经攒好的
    timer/心跳新鲜度等问题**整体丢掉**,变成整轮零告警。
    """
    for bad in ("corrupt", [1], {"a": 1}):
        ps = cw._heartbeat_problems(
            {"firehose_fail": 1, "trade_flow_outage_streak": bad, "alert_queue_depth": 3})
        assert any(p.startswith("🔴") and "读不了心跳" in p for p in ps), \
            f"坏字段 {bad!r} 没有出声:{ps}"
        assert any("发不出去" in p for p in ps), \
            f"坏字段 {bad!r} 把队列检查一起吞掉了 —— 正是铁律要防的形状:{ps}"


# ─────────────────────────────────────────────────────────────────────────────
# 缺陷二:冷却签名不许被会变的数字冲垮
# ─────────────────────────────────────────────────────────────────────────────
def test_signature_is_STABLE_when_only_a_count_changes():
    """⭐⭐ 洪水闸门:实测「N 条告警发不出去」的 N 取过 1/2/3/7/8/9/20,
    每变一次签名就变一次 ⇒ 4h 冷却形同虚设 ⇒ 几乎每轮都推。
    """
    a = cw._heartbeat_problems(_hb(alert_queue_depth=1))
    b = cw._heartbeat_problems(_hb(alert_queue_depth=20))
    assert a and b and a != b, "用例前提失效:两组问题正文本应不同"
    assert cw._cooldown_signature(a) == cw._cooldown_signature(b), \
        "只有数字变了,签名却变了 ⇒ 冷却会被冲垮"


def test_signature_is_STABLE_across_streak_growth_within_a_tier():
    """同一档内轮数在涨(3→4→5),不该每轮推一条。"""
    sigs = {cw._cooldown_signature(
        cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=s)))
        for s in range(1, alerts.TRADE_FLOW_OUTAGE_CYCLES)}
    assert len(sigs) == 1, f"同一档内签名有 {len(sigs)} 种,冷却会被冲垮"


def test_signature_CHANGES_when_the_tier_escalates():
    """防洪的另一头:真升级(🟡→🔴)必须立刻推,不能被冷却压住。"""
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    lo = cw._cooldown_signature(
        cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=n - 1)))
    hi = cw._cooldown_signature(
        cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=n)))
    assert lo != hi, "升级到 🔴 却被当成同一个问题压住 ⇒ 真信号发不出来"


def test_signature_changes_when_a_new_problem_kind_appears():
    """多出一类问题必须重新告警,不许被旧签名盖住。"""
    one = cw._heartbeat_problems(_hb(firehose_fail=1, trade_flow_outage_streak=1))
    two = cw._heartbeat_problems(
        _hb(firehose_fail=1, trade_flow_outage_streak=1, alert_queue_depth=3))
    assert cw._cooldown_signature(one) != cw._cooldown_signature(two)


def test_cooldown_actually_suppresses_a_repeat(tmp_path, monkeypatch):
    """把签名接到真冷却上:同签名第二次必须被压住(端到端,不只是纯函数)。"""
    monkeypatch.setattr(cw, "STATE_FILE", tmp_path / ".watchdog_state.json")
    ps = cw._heartbeat_problems(_hb(alert_queue_depth=1))
    sig = cw._cooldown_signature(ps)
    assert cw._cooldown_ok(sig) is True, "第一次必须放行"
    assert cw._cooldown_ok(sig) is False, "同签名第二次必须被冷却压住"


def test_cooldown_uses_the_signature_function_not_a_raw_join():
    """防「写了没接上」:main 必须走 _cooldown_signature,不许自己再拼一份。"""
    assert '"|".join(sorted(problems))' not in WD_SRC, "main 里还留着会被数字冲垮的旧拼法"
    assert "_cooldown_signature(" in WD_SRC
