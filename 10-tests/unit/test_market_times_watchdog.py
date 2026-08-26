#!/usr/bin/env python3
"""判据:时间防火墙侧表的「死人开关」(2026-08-26 立)。

## 由来(真实风险,不是假想)

V6 判决版要用 **2026-08-27 00:00 UTC 之后结算**的市场,而那些市场的 `closed_time`
只能由 `polymarket-market-times.timer` 每小时去 Gamma 取回来。**当时错过就永久取不回**
(市场关闭后就掉出轮询队列)。而看门狗 `collector_watchdog` 原先只盯一个 unit
(`TIMER_UNIT`,采集器那条),侧表**完全不在它的视野里** ⇒ 侧表停了没有任何人会喊,
症状要等 7~10 天后跑判决那天才冒出来。

2026-08-26 我自己就为做对照 `stop` 掉过它一次并且忘了恢复,是主动复查才发现的。

## ⭐为什么只查「timer 是不是 active」不够

对着 CLAUDE.md 第一条问句 ——「如果它现在就是坏的,我看到的会有什么不同?」——
有两种**实际存在**的故障下,答案是「没有不同」:

1. **被超时砍掉后卡死**:2026-08-26 21:52 真实发生 —— 当时的哨兵锁靠 `finally` 删除,
   而 SIGTERM 不走 `finally` ⇒ 锁残留 ⇒ **此后每一轮都立刻 return 2、什么都不做**,
   而 timer 仍是 active,实测卡死 4 小时 22 分。
   ⚠️ 该机制已于 2026-08-27 换成**内核持有的 flock**(见 `_acquire_lock` 的 docstring),
      这一条具体成因不会再发生;但本守护挡的是**症状**("跑起来了却没干成活"),
      不是那一个成因,所以照留。
2. **网络坏掉**:2026-08-04 / 08-22 都真发生过隧道被拖垮。每轮照跑,零产出。

⇒ 故本守护判三条:timer 存活 / **真的跑成过**(运行记录的新鲜度)/ **有活真干成了**。

## ⭐「没活可干」与「有活没干成」必须分开(静默失败清单第 6 条)

侧表是**回填**任务:追平之后大多数轮次本来就没活干。
所以「产出为 0」在这里**不是**异常信号,不许拿它当判据 —— 拿它当判据的那一秒起,
追平当天就变成天天误报的噪音源(而噪音会让真信号无处可显)。
有分辨力的量是 **pending>0 而 written==0**:有活,一个都没干成。

为此生产脚本必须在**无活可干那条路径上也留痕**(原先直接 `return 0`,什么都不写)——
即项目犯过 4 次的「记录事实 vs 使用事实,只接一头」。本文件焊死这一条。

## 阈值来历(不是拍的)

侧表 `OnCalendar=*:50` ⇒ 一轮 60 分钟。取 150 分钟 = **2.5 轮**:容 1 轮失手、
抓 2 轮连续失手。与看门狗既有的 `HEARTBEAT_STALE_MIN=40` 配 15 分钟周期
(= 2.67 轮,注释写明"容 1 轮失手")是**同一把尺子**,不是另立标准。

## 异常家族按【造真坏输入实测】决定,不凭想当然

2026-08-26 实跑(见本文件末尾的判据):
  缺文件 → FileNotFoundError ⊂ OSError      | 是目录 → IsADirectoryError ⊂ OSError
  非 UTF-8 → UnicodeDecodeError ⊂ ValueError | JSON 垃圾 → JSONDecodeError ⊂ ValueError
  fromisoformat("nope") → ValueError         | fromisoformat(None) → TypeError
⇒ 读+解析用 `(OSError, ValueError)`;时间戳/取整用 `(TypeError, ValueError)`。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

pytest.importorskip("pyarrow")
import collector_watchdog as cw  # noqa: E402

SIDE_TABLE_UNIT = "polymarket-market-times.timer"


# ─────────────────────────────────────────────────────────────────────────────
# 测试替身:只替掉**真正的外部依赖**(systemctl 进程、磁盘上的文件路径),
# 被测逻辑 `_market_times_problems` 与 `_timer_active` 都跑**生产那一份**。
# ⚠️ 2026-08-26 的教训「判据没走生产路径」一天出现六次 —— 故这里刻意不去
#    monkeypatch `_timer_active` 本身:那样一来"它到底查了哪个 unit"就没人验了。
# ─────────────────────────────────────────────────────────────────────────────
class _FakeCompleted:
    def __init__(self, out: str) -> None:
        self.stdout = out
        self.returncode = 0


def _fake_systemctl(active_units: set[str], seen: list[str] | None = None):
    """按 argv 里真实出现的 unit 名作答 —— 查错了 unit 这里就会露出来。

    ⛔ 不按"第几次调用"作答:2026-08-17 栽过 —— 调用次数一变,红绿全部错位。
    """
    def run(cmd, **kw):
        unit = cmd[-1]
        if seen is not None:
            seen.append(unit)
        return _FakeCompleted("active" if unit in active_units else "inactive")
    return run


def _summary(**kw) -> dict:
    """一份健康的运行记录。默认全健康,由用例只改自己关心的那个字段。"""
    base = {
        "run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pending": 12246,
        "fetched": 1700,
        "written": 1700,
    }
    base.update(kw)
    return base


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """装好一套「全健康」的现场;用例只推倒自己要测的那一根柱子。"""
    path = tmp_path / "last_run_summary.json"
    path.write_text(json.dumps(_summary()), encoding="utf-8")
    monkeypatch.setattr(cw, "MARKET_TIMES_SUMMARY", path)
    seen: list[str] = []
    monkeypatch.setattr(cw.subprocess, "run",
                        _fake_systemctl({SIDE_TABLE_UNIT, cw.TIMER_UNIT}, seen))

    class Rig:
        def __init__(self) -> None:
            self.path = path
            self.seen = seen

        def write(self, **kw) -> None:
            self.path.write_text(json.dumps(_summary(**kw)), encoding="utf-8")

        def stop_timer(self, *, keep: set[str] | None = None) -> None:
            monkeypatch.setattr(cw.subprocess, "run",
                                _fake_systemctl(keep or set(), self.seen))
    return Rig()


# ─────────────────────────────────────────────────────────────────────────────
# 防洪的一头:稳态必须完全静默
# ─────────────────────────────────────────────────────────────────────────────
def test_healthy_side_table_stays_completely_silent(rig):
    """稳态一句话都不许说 —— 噪音会让真信号无处可显(CLAUDE.md 阈值第 4 条)。"""
    assert cw._market_times_problems() == []


def test_no_work_to_do_is_NOT_an_alarm(rig):
    """⭐本判据的核心分辨力:侧表是回填任务,追平后大多数轮次本来就没活干。

    pending=0 且 written=0 是**健康**。把它判成异常 = 追平当天起天天误报。
    """
    rig.write(pending=0, fetched=0, written=0, no_work=True)
    assert cw._market_times_problems() == []


# ─────────────────────────────────────────────────────────────────────────────
# 防洪的另一头:三种真故障各必须推
# ─────────────────────────────────────────────────────────────────────────────
def test_timer_stopped_is_RED(rig):
    """08-26 真发生过:我 stop 掉它做对照,忘了恢复。"""
    rig.stop_timer(keep={cw.TIMER_UNIT})
    problems = cw._market_times_problems()
    assert any(p.startswith("🔴") and SIDE_TABLE_UNIT in p for p in problems), problems


def test_it_asks_about_the_SIDE_TABLE_timer_not_the_collector_timer(rig):
    """行为检查,不是文本检查:真去问了哪个 unit,由被记录的 argv 说了算。

    ⚠️ 2026-08-23 的教训「文本检查冒充行为检查」当天犯了 3 次 ——
       `assert "polymarket-market-times" in 源码` 这种写法在这里是**不合格**的。
    """
    cw._market_times_problems()
    assert SIDE_TABLE_UNIT in rig.seen, f"根本没查侧表 timer,只查了 {rig.seen}"


def test_a_run_that_never_finished_is_RED(rig):
    """锁文件残留 / 每轮崩溃:timer 仍 active,但运行记录不再更新。"""
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=cw.MARKET_TIMES_STALE_MIN + 10)
    rig.write(run_at_utc=old.isoformat())
    problems = cw._market_times_problems()
    assert any(p.startswith("🔴") for p in problems), problems


def test_one_missed_run_is_tolerated(rig):
    """容 1 轮失手:单轮抖动不许报,否则又是一个噪音源。"""
    recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=70)
    rig.write(run_at_utc=recent.isoformat())
    assert cw._market_times_problems() == []


def test_a_timestamp_without_a_timezone_is_read_as_UTC_not_local_time(rig):
    """本机是 JST(UTC+9)。无时区的时间戳若落回本机时区,误差 9 小时 ——
    足以把"刚跑过"读成"停了 9 小时"(反向也一样危险)。

    项目已因 JST/UTC 错位虚构过一个不存在的异常,故这一条单独焊死。
    """
    naive = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()
    rig.write(run_at_utc=naive)
    assert cw._market_times_problems() == [], "无时区时间戳被按本机时区解释了"


def test_stale_threshold_tolerates_exactly_one_missed_hourly_run():
    """阈值来历焊死:60 分钟一轮 ⇒ 必须 >2 轮(容 1 次失手)且 <3 轮(2 轮连失就抓)。

    ⛔ 这条挡的是「事后把阈值调松到刚好不报」。
    """
    assert 2 * 60 < cw.MARKET_TIMES_STALE_MIN < 3 * 60


def test_never_ran_at_all_is_RED(rig):
    """从没跑成过 —— 与「跑过但陈旧」是两回事,都必须喊。"""
    rig.path.unlink()
    problems = cw._market_times_problems()
    assert any(p.startswith("🔴") for p in problems), problems


def test_work_pending_but_nothing_written_is_RED(rig):
    """⭐「有活没干成」:网络坏掉时的形状(08-04 / 08-22 真发生过)。"""
    rig.write(pending=12246, fetched=0, written=0)
    problems = cw._market_times_problems()
    assert any(p.startswith("🔴") for p in problems), problems


def test_permanently_excluded_markets_are_surfaced(rig):
    """⭐review M2:格式不合法的 condition_id 被**永久**排除在 todo 之外 ⇒
    pending 不会因它们变大 ⇒ 前两条判据都判不到,而那批市场从此拿不到 closed_time。

    这就是本次改动通篇在讲的「记录事实 vs 使用事实,只接一头」:
    `invalid_cids` 每轮都被写进记录,原先没有任何人读它。
    """
    rig.write(pending=0, fetched=0, written=0, invalid_cids=37)
    problems = cw._market_times_problems()
    assert any(p.startswith("🔴") and "永久" in p for p in problems), problems


def test_zero_invalid_cids_stays_silent(rig):
    """防洪的另一头:现网实测长期恒为 0,这一档必须一句话都不说。"""
    rig.write(invalid_cids=0)
    assert cw._market_times_problems() == []


# ─────────────────────────────────────────────────────────────────────────────
# 一个附加功能坏掉,不许打断调用方其余职责(CLAUDE.md 异常清单第 5 条)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("payload,label", [
    (b"{not json", "JSON 垃圾"),
    (b"", "空文件"),
    (b"\xff\xfe\x00garbage", "非 UTF-8"),
    (b'"just a string"', "顶层不是对象"),
    (b'{"run_at_utc": null}', "时间戳是 null"),
    (b'{"run_at_utc": "nope"}', "时间戳不可解析"),
    (b'{"run_at_utc": "REPLACED", "pending": [], "written": 1}', "pending 不是数字"),
])
def test_a_corrupt_summary_is_reported_and_does_not_swallow_the_timer_check(
        rig, payload, label):
    """两件事一起焊死:①不许抛出去 ②不许把已经攒到的 timer 问题一起吞掉。

    ⚠️ 2026-08-17 daily_digest 栽过同一形状:一个附加检查抛异常,
       把 check() 前面三项核心检查**整体丢掉** ⇒ 看门狗整轮零告警。
    """
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rig.path.write_bytes(payload.replace(b"REPLACED", now.encode()))
    rig.stop_timer(keep={cw.TIMER_UNIT})          # 同时制造一个真问题
    problems = cw._market_times_problems()        # 不抛 = 第一层要求
    assert any(SIDE_TABLE_UNIT in p for p in problems), \
        f"{label}:坏记录把 timer 停摆这条真问题吞掉了 → {problems}"
    assert len(problems) >= 2, f"{label}:坏记录本身没被报出来 → {problems}"


def test_an_old_summary_without_the_pending_field_is_not_silently_healthy(rig):
    """生产脚本还没升级(旧格式)时不许判健康 —— 那正是"绿着而东西是坏的"。"""
    stale_format = _summary()
    stale_format.pop("pending")
    rig.path.write_text(json.dumps(stale_format), encoding="utf-8")
    assert cw._market_times_problems() != []


# ─────────────────────────────────────────────────────────────────────────────
# 接线:新检查必须真的挂在 check() 上,否则它是个谁也走不到的孤儿
# ─────────────────────────────────────────────────────────────────────────────
def test_check_actually_runs_the_side_table_guard(rig, monkeypatch):
    """行为检查:让侧表出真问题,看 check() 的返回值里有没有它。

    ⚠️ 「函数被调用了」≠「它的返回值控制了流程」——所以这里断言的是
       **问题真的出现在 check() 的输出里**,不是"它被调过一次"。
    """
    monkeypatch.setattr(cw, "_digest_problems", lambda *a, **k: [])
    monkeypatch.setattr(cw, "_latest_heartbeat", lambda: None)
    rig.stop_timer(keep={cw.TIMER_UNIT})
    assert any(SIDE_TABLE_UNIT in p for p in cw.check())


def test_an_unexpected_crash_in_the_guard_does_not_kill_the_collector_checks(
        rig, monkeypatch):
    """⭐兜的是**没预料到的**那一种异常(已知的坏输入家族在别处逐个焊死)。

    2026-08-17 daily_digest 正是这个形状:一个附加检查抛了没预料到的异常,
    把 check() 里前面已经攒好的核心问题**整体丢掉** ⇒ 看门狗整轮零告警,
    比没有看门狗更糟(它主动让人放心)。
    """
    monkeypatch.setattr(cw, "_digest_problems", lambda *a, **k: [])
    monkeypatch.setattr(cw, "_latest_heartbeat", lambda: None)

    def boom(unit):
        # ⚠️ 只让**侧表那一路**炸。第一版这里对所有 unit 都抛,结果炸的是
        #    check() 里采集器自己那次调用 —— 判据测的根本不是它宣称要测的那条路径
        #    (「判据没走生产路径」,2026-08-26 当天第七次)。
        if unit == SIDE_TABLE_UNIT:
            raise RuntimeError("systemctl 换了输出格式")
        return True
    monkeypatch.setattr(cw, "_timer_active", boom)

    problems = cw.check()                       # 不抛 = 第一层要求
    assert any("无任何审计心跳" in p for p in problems), \
        f"侧表守护崩溃把采集器的核心检查一起带走了 → {problems}"
    assert any("侧表守护自己崩了" in p for p in problems), \
        f"崩溃本身被静默吞掉了 → {problems}"


def test_cooldown_signature_survives_changing_counts(rig):
    """防洪:正文里的数字每轮都在变,签名不许跟着变(08-19 冷却被数字冲垮过)。"""
    rig.write(pending=12246, fetched=0, written=0)
    a = cw._cooldown_signature(cw._market_times_problems())
    rig.write(pending=907, fetched=0, written=0)
    b = cw._cooldown_signature(cw._market_times_problems())
    assert a == b, f"数字一变签名就变 ⇒ 4h 冷却失效\n{a}\n{b}"
