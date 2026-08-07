#!/usr/bin/env python3
"""判据焊死:告警发不出去时不许丢,通道恢复后必须补发(2026-08-07)。

## 由来(实测,非假想)

2026-08-07 08:00 一次约 15 分钟的断网:

1. 采集器**正确检测到** firehose 采样 0 笔,**正确生成了**告警
2. Telegram 发不出去(DNS 挂了)
3. `maybe_alert` 返回 False,`run_cycle` 里 `if ...: print("已推送告警")` 于是不打印
4. ⇒ 日志上看起来"今天 0 条告警",**与真正的风平浪静一模一样**

实测历史累计:采集器 23 次 + 看门狗 2 次 = **25 条告警生成了但从没送到**,
而全仓库**没有任何代码读这个失败**(`_send` 只往 stderr 打一行就吞掉)。

形状归类:「记录事实与使用事实只接一头」第 6 次;
成因是「告警的送达通道与被告警的故障同命」—— 网络类事故会同时打掉两者,
而网络类事故恰恰是最常见的一类。

## ⚠️ 本判据**不能**解决的事(写在前面,不许含糊)

**"告警通道坏了"这件事,没法用告警去通知。** 要真解决得有第二条独立通道
(短信/邮件/本地弹窗),不在本次范围。

本次能做到的是两件较弱但真实的事:
1. 通道恢复的那一刻,把积压**补发**出来,并写明"曾中断多久、期间积压几条" ——
   让人**事后知道自己曾被蒙在鼓里多久**。
2. 队列深度进心跳 ⇒ 看门狗读得到。这条在"网络没坏、但 Telegram 令牌失效 /
   接口变更 / 被限流"这类故障下是**真管用**的(那时看门狗自己的通道还活着)。

## 关键约束:每轮最多一次网络调用

补发绝不能逐条重试:20 条 × 单条最坏 30s = 10 分钟,足以撑爆整个周期预算
(硬杀线 900s)。故队列**拼成一条**发出去 —— 成功清空,失败留着下轮再来。
⇒ 队列**取代**了重试,不是叠加在重试之上。
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import alerts  # noqa: E402


@pytest.fixture
def q(tmp_path, monkeypatch):
    """把队列目录指到 tmp —— 判据绝不许往生产状态目录写东西(项目铁律)。"""
    monkeypatch.setattr(alerts, "_QUEUE_DIR", tmp_path)
    return tmp_path


class _Channel:
    """可控的发送通道:记录每次调用的正文,并按 `up` 决定成功还是失败。"""

    def __init__(self, up=True):
        self.up = up
        self.sent = []

    def __call__(self, msg, max_retries=2):
        self.sent.append(msg)
        return self.up

    @property
    def calls(self):
        return len(self.sent)


@pytest.fixture
def ch(monkeypatch):
    c = _Channel()
    monkeypatch.setattr(alerts, "_send", c)
    return c


# ============ 组 A:发不出去不许丢 ============

def test_a_failed_alert_is_kept_not_dropped(q, ch):
    ch.up = False
    r = alerts.dispatch("🔴 出事了", link="cycle")
    assert r["delivered"] is False
    assert r["queue_depth"] == 1


def test_the_queue_survives_process_exit(q, ch):
    """每轮是独立进程,退出即失忆 —— 不落盘等于没排队。"""
    ch.up = False
    alerts.dispatch("🔴 出事了", link="cycle")
    assert json.loads((q / "pending_alerts_cycle.json").read_text())["pending"]


def test_recovery_resends_the_backlog(q, ch):
    ch.up = False
    alerts.dispatch("🔴 第一条", link="cycle")
    alerts.dispatch("🔴 第二条", link="cycle")
    ch.up = True
    r = alerts.dispatch(None, link="cycle")          # 本轮没有新告警
    assert r["delivered"] is True and r["queue_depth"] == 0
    assert "第一条" in ch.sent[-1] and "第二条" in ch.sent[-1]


def test_the_resend_says_how_long_we_were_in_the_dark(q, ch):
    """⭐补发必须写明"曾中断多久、积压几条" —— 否则人无从知道自己被蒙了多久。"""
    ch.up = False
    alerts.dispatch("🔴 出事了", link="cycle")
    alerts.dispatch("🔴 又出事了", link="cycle")
    ch.up = True
    alerts.dispatch(None, link="cycle")
    body = ch.sent[-1]
    assert "补发" in body and "2" in body


def test_a_quiet_round_still_drains_the_backlog(q, ch):
    """⭐本轮没有新告警**也要**尝试补发。

    否则一旦风平浪静,积压就永远排不出去 —— 而"风平浪静"恰恰是故障结束后的常态。
    """
    ch.up = False
    alerts.dispatch("🔴 出事了", link="cycle")
    ch.up = True
    before = ch.calls
    r = alerts.dispatch(None, link="cycle")
    assert ch.calls == before + 1 and r["delivered"] is True


# ============ 组 B:每轮最多一次网络调用(防止补发撑爆周期) ============

def test_one_network_call_per_round_no_matter_how_deep(q, ch):
    """⭐20 条积压也只发一次。逐条重试 = 20×30s = 10 分钟 = 撑爆 900s 硬杀线。"""
    ch.up = False
    for i in range(alerts.PENDING_MAX):
        alerts.dispatch(f"🔴 第 {i} 条", link="cycle")
    assert ch.calls == alerts.PENDING_MAX          # 每轮各一次,不是每轮 N 次
    ch.up = True
    before = ch.calls
    alerts.dispatch(None, link="cycle")
    assert ch.calls == before + 1


def test_steady_state_makes_no_network_call_at_all(q, ch):
    """没有告警、也没有积压 ⇒ 一次都不发(防洪的另一头)。"""
    r = alerts.dispatch(None, link="cycle")
    assert ch.calls == 0 and r == {"delivered": True, "queue_depth": 0,
                                   "dropped": 0, "oldest_age_s": 0}


def test_no_file_is_written_when_there_is_nothing_to_queue(q, ch):
    """稳态不留垃圾文件 —— 也是判据不污染生产目录的前提。"""
    alerts.dispatch(None, link="cycle")
    alerts.dispatch("🔴 出事了", link="cycle")       # 发得出去 ⇒ 不入队
    assert list(q.iterdir()) == []


# ============ 组 C:队列有上限,丢弃必须出声 ============

def test_the_queue_is_capped(q, ch):
    ch.up = False
    for i in range(alerts.PENDING_MAX + 5):
        r = alerts.dispatch(f"🔴 第 {i} 条", link="cycle")
    assert r["queue_depth"] == alerts.PENDING_MAX


def test_dropping_the_oldest_is_counted_out_loud(q, ch):
    """静默丢样本是本项目的真凶 —— 丢了必须数出来,并在补发时告诉人。"""
    ch.up = False
    for i in range(alerts.PENDING_MAX + 5):
        alerts.dispatch(f"🔴 第 {i} 条", link="cycle")
    ch.up = True
    r = alerts.dispatch(None, link="cycle")
    assert r["dropped"] == 5
    assert "5" in ch.sent[-1] and "丢弃" in ch.sent[-1]


def test_newest_is_kept_when_capped(q, ch):
    """丢最老的:告警是状态描述,新的更能反映现状。"""
    ch.up = False
    for i in range(alerts.PENDING_MAX + 3):
        alerts.dispatch(f"🔴 第 {i} 条", link="cycle")
    ch.up = True
    alerts.dispatch(None, link="cycle")
    body = ch.sent[-1]
    assert "第 0 条" not in body and f"第 {alerts.PENDING_MAX + 2} 条" in body


# ============ 组 D:稳态的消息内容一个字都不许变 ============

def test_a_lone_alert_is_sent_verbatim(q, ch):
    """没有积压时,发出去的正文必须与从前**逐字相同**。

    否则这次改动会悄悄改掉所有既有告警的样子,而那些告警的内容判据
    (test_alerts_*.py 等)验的正是正文 —— 包装它们等于拿判据迁就新代码。
    """
    alerts.dispatch("🔴 原样的正文", link="cycle")
    assert ch.sent == ["🔴 原样的正文"]


# ============ 组 E:三条链路各自独立,不许互相踩 ============

def test_each_link_has_its_own_queue(q, ch):
    """采集器(:00/:15/:30/:45)与看门狗(:00/:30)会**同时**跑 ——
    共用一个队列文件会在读-改-写之间丢条目。故一条链路一个文件。
    """
    ch.up = False
    alerts.dispatch("🔴 采集器的", link="cycle")
    alerts.dispatch("🔴 看门狗的", link="watchdog")
    ch.up = True
    r = alerts.dispatch(None, link="cycle")
    assert "采集器的" in ch.sent[-1] and "看门狗的" not in ch.sent[-1]
    assert alerts.queue_stats("watchdog")["queue_depth"] == 1


def test_a_corrupt_queue_file_degrades_to_empty(q, ch):
    """状态文件损坏只该影响节奏,不该让采集器崩(项目既有惯例)。"""
    (q / "pending_alerts_cycle.json").write_text("{ 这不是 json")
    assert alerts.queue_stats("cycle")["queue_depth"] == 0
    assert alerts.dispatch("🔴 出事了", link="cycle")["delivered"] is True


# ============ 组 F:队列深度必须有人读 ============

def test_queue_depth_reaches_the_heartbeat():
    """⭐不接消费者的话,这次改动本身就是"记录了没人读"的第 7 次。"""
    import storage_engine as se
    assert "alert_queue_depth" in se.AUDIT_FIELDS
    assert "alert_dropped_count" in se.AUDIT_FIELDS


def test_the_watchdog_actually_surfaces_a_stuck_queue(monkeypatch):
    """⭐验的是**接线**不是措辞:喂一条"队列积压"的新鲜心跳,`check()` 必须报出来。

    早先这条判据写的是"源码里出现 alert_queue_depth 这个词" —— 那种写法
    只要有人把调用删掉留个注释就照样绿,等于在验自己的替身(08-06 刚栽过一次)。
    """
    import time as _t

    import collector_watchdog as wd
    monkeypatch.setattr(wd, "_timer_active", lambda: True)
    monkeypatch.setattr(wd, "_latest_heartbeat",
                        lambda: {"ts": _t.time(), "alert_queue_depth": 3})
    assert any("发不出去" in p for p in wd.check())


def test_watchdog_flags_a_stuck_queue():
    import collector_watchdog as wd
    problems = wd._heartbeat_problems({"ts": 0, "alert_queue_depth": 3})
    assert any("告警" in p for p in problems)


def test_watchdog_silent_when_queue_is_empty():
    """防洪另一头:队列空 ⇒ 不出声。"""
    import collector_watchdog as wd
    assert wd._heartbeat_problems({"ts": 0, "alert_queue_depth": 0}) == []
