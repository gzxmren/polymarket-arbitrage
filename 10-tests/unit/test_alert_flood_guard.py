#!/usr/bin/env python3
"""告警防洪判据 —— 对应 docs/DESIGN_ALERT_FLOOD_2026-08-28.md

焊两头(CLAUDE.md 静默失败第 7 条):
- **稳态完全静默**:健康日一条都不许推(用现网真实心跳)
- **真异常必推**:故障第一轮必须推,且持续故障下不许饿死

⭐ 本文件的红线**不是拍脑袋定的**,是用 08-19 / 08-20 / 08-24 / 08-25 四天的
**真实心跳**跑出来的。fixture 冻结自生产 audit 分区,冻结时已验证它能逐字复现
生产日志里的推送轮数(08-20:59=59;08-24/25:0=0;08-19:68 vs 日志 69,
差的 1 条属回填链路 link="backfill",不在 build_alert 职责内)。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "11-collector"))

import alerts  # noqa: E402

FIXTURE = PROJECT_ROOT / "10-tests" / "fixtures" / "heartbeats_alert_flood_2026-08.json"

# 冻结基线:现行代码在这四天各出多少轮正文。改动**不许**动这四个数。
MEASURED_UNTHROTTLED = {
    "2026-08-19": 68,   # 退化日(间歇,连计取模全程失效)
    "2026-08-20": 59,   # 退化日
    "2026-08-24": 0,    # 健康日
    "2026-08-25": 0,    # 健康日
}
# 实测最长的一次推送通道中断(2026-08-14,采集器日志原文"约 458 分钟")。
# 红线要问的是"这段时间里产生的消息会不会把队列挤爆",故窗口必须取这个实测值。
MEASURED_WORST_OUTAGE_S = 458 * 60

# ⚠️ 留痕:本文件第一版的红线是「退化日全天消息数 ≤ 8」。那个 8 **是我拍的,没有推导**,
# 实测跑出 13/15 才发现。此刻最容易做的事是把 8 改成 16 让它变绿 —— 那正是
# CLAUDE.md「结果出来之后再定标准,人必然会挑一个刚好能通过的标准」说的自欺。
# 改法是换成**需求本身的单位**:队列 PENDING_MAX 格,在最长中断窗内不许被挤爆。
# 红线常量直接从被检验对象 import(alerts.PENDING_MAX),不在判据里另抄一份。


@pytest.fixture(scope="module")
def heartbeats() -> dict[str, list[dict]]:
    return json.loads(FIXTURE.read_text())


def _step_s() -> float:
    return alerts.cycle_minutes() * 60


def _replay(rows: list[dict], throttled: bool) -> list[float]:
    """按 15 分钟一轮重放,返回**每条外发消息的时刻**(不是触发条数)。"""
    state: dict = {}
    sent: list[float] = []
    for i, row in enumerate(rows):
        now = i * _step_s()
        pairs = alerts.build_alert_keyed(row)
        lines = [t for _, t in pairs]
        if throttled:
            lines, state, _ = alerts.throttle(pairs, state, now)
        if lines:
            sent.append(now)
    return sent


def _worst_window(sent: list[float]) -> int:
    """最长通道中断窗内最多积压几条 —— 这就是队列会不会被挤爆的那个数。"""
    return max((sum(1 for t in sent if 0 <= t - s < MEASURED_WORST_OUTAGE_S)
                for s in sent), default=0)


# ---------- fixture 自身的完整性(防止判据静默变松) ----------

def test_fixture_covers_every_field_build_alert_reads():
    """fixture 缺字段 ⇒ counts.get 全取 0 ⇒ 触发不了 ⇒ 判据变绿而系统是坏的。

    ⚠️ 两个坑都踩过,都写在这:
    ① 正则必须同时吃单双引号 —— f-string 里写的是 counts.get('x')。
       第一版只匹配双引号,24 个字段只认出 11 个。
    ② 切片必须覆盖 build_alert_keyed **和** compose_body —— 拆函数之后
       total_markets_polled / new_trades / newly_resolved 三个搬进了后者,
       只切前一半会认出 21 个,而少掉的正好是"本轮摘要"那三个。
    """
    src = (PROJECT_ROOT / "11-collector" / "alerts.py").read_text()
    body = src[src.index("def build_alert_keyed("):src.index("def maybe_alert_backfill(")]
    keys = set(re.findall(r"""counts\.get\(\s*['"]([^'"]+)['"]""", body))
    assert len(keys) >= 24, f"字段数骤减({len(keys)})=正则又瞎了"
    row = json.loads(FIXTURE.read_text())["2026-08-19"][0]
    missing = sorted(keys - set(row))
    assert not missing, f"fixture 缺 {missing} —— 须重新冻结,否则这些触发永远不会被重放到"


def test_fixture_reproduces_the_measured_production_baseline(heartbeats):
    """判据的地基:重放必须复现生产日志里的真实轮数,否则它测的是别的东西。"""
    for day, expected in MEASURED_UNTHROTTLED.items():
        assert len(_replay(heartbeats[day], throttled=False)) == expected, \
            f"{day} 基线漂了 —— fixture 或 build_alert 变了,红线须重新测算"


# ---------- R1:build_alert 必须保持纯函数 ----------

def test_build_alert_is_still_pure_when_called_repeatedly():
    """现有 18+ 条判据在循环里反复调它(test_trade_flow_outage_guard 一处就 12 次)。

    冷却状态若落进 build_alert,第二次起返回值就变 ⇒ 那些判据变成空壳
    (而且是**变绿**,不是变红)。
    """
    counts = {"settlement_lookup_fail": 170, "settlement_checked": 170}
    first = alerts.build_alert(counts)
    assert first is not None
    for _ in range(12):
        assert alerts.build_alert(counts) == first, "build_alert 有副作用了"


def test_build_alert_is_compose_of_keyed():
    """正文 = 壳(标题+本轮摘要) + 各触发行。壳必须逐字不变(既有判据验的是它)。"""
    counts = {"settlement_lookup_fail": 170, "settlement_checked": 170,
              "rotation_hole_streak": alerts.ROTATION_HOLE_CYCLES}
    pairs = alerts.build_alert_keyed(counts)
    assert len(pairs) == 2
    body = alerts.build_alert(counts)
    assert body == alerts.compose_body([t for _, t in pairs], counts)
    for _, text in pairs:
        assert text in body
    assert alerts.build_alert_keyed({}) == []
    assert alerts.build_alert({}) is None
    assert alerts.compose_body([], counts) is None


# ---------- §2.3:key 必须与正文里的数字无关 ----------

def test_keys_do_not_change_when_numbers_change():
    """看门狗那套"抹掉正文数字得签名"已坏两次(08-19 数字位数、08-26 千分位逗号)。

    本设计的 key 写死在产生处 ⇒ 结构上不可能被数字冲垮。这条就是验那个结构性质。
    """
    small = alerts.build_alert_keyed({"settlement_lookup_fail": 21, "settlement_checked": 22})
    huge = alerts.build_alert_keyed({"settlement_lookup_fail": 1234567,
                                     "settlement_checked": 1234568})
    assert [k for k, _ in small] == [k for k, _ in huge] != []
    for key, _ in small + huge:
        assert not re.search(r"\d", key), f"key {key!r} 里有数字,又会被数字冲垮"


# ---------- 真异常必推 / 窗内压住 ----------

def test_first_occurrence_always_passes():
    pairs = [("settlement_lookup_fail", "⚠️ 出事了")]
    lines, _, _n = alerts.throttle(pairs, {}, now=0.0)
    assert lines == ["⚠️ 出事了"]


def test_repeat_within_window_is_suppressed():
    pairs = [("settlement_lookup_fail", "⚠️ 出事了")]
    lines, st, _n = alerts.throttle(pairs, {}, now=0.0)
    assert lines
    for i in range(1, 12):
        lines, st, _n = alerts.throttle(pairs, st, now=i * _step_s())
        assert lines == [], f"第 {i} 轮不该再推"


def test_passes_again_right_after_the_window():
    pairs = [("settlement_lookup_fail", "⚠️ 出事了")]
    _, st, _n = alerts.throttle(pairs, {}, now=0.0)
    lines, _, _n = alerts.throttle(pairs, st, now=alerts.throttle_cooldown_s())
    assert lines, "冷却窗一过必须能再推(否则是饿死)"


# ---------- R2:被压住的次数不许蒸发 ----------

def test_suppressed_count_rides_on_next_message_of_any_key():
    a = [("settlement_lookup_fail", "⚠️ A")]
    b = [("rotation_hole", "🔴 B")]
    _, st, _n = alerts.throttle(a, {}, now=0.0)
    for i in range(1, 6):                      # A 又发生 5 次,全被压住
        _, st, _n = alerts.throttle(a, st, now=i * _step_s())
    lines, st, _n = alerts.throttle(b, st, now=6 * _step_s())   # 换一个 key 推出去
    tail = "\n".join(lines)
    assert "🔴 B" in tail
    assert "5" in tail and "settlement_lookup_fail" in tail, \
        f"被压住的 5 次没有随任何一条消息出声:{tail!r}"


def test_suppressed_is_not_lost_when_the_fault_disappears():
    """故障自己好了 ⇒ 该 key 不再触发 ⇒ 若只挂在自己身上,那几次永远没人知道。"""
    a = [("settlement_lookup_fail", "⚠️ A")]
    _, st, _n = alerts.throttle(a, {}, now=0.0)
    for i in range(1, 4):
        _, st, _n = alerts.throttle(a, st, now=i * _step_s())
    for i in range(4, 8):                      # 故障消失,啥也不触发
        lines, st, _n = alerts.throttle([], st, now=i * _step_s())
        assert lines == [], "没有触发时不该凭空造一条消息"
    assert st.get("suppressed", {}).get("settlement_lookup_fail") == 3, \
        "欠着的计数被静默清零了"
    lines, st, _n = alerts.throttle([("slow_cycle", "⚠️ 别的事")], st, now=8 * _step_s())
    assert "3" in "\n".join(lines), "下一条消息必须把欠的账结清"


# ---------- R5:与现有 % N == 0 叠加不许饿死 ----------

@pytest.mark.parametrize("key,modulo,field", [
    ("rotation_hole", alerts.ROTATION_HOLE_CYCLES, "rotation_hole_streak"),
    ("slow_cycle", alerts.SLOW_CYCLE_ALERT_CYCLES, "slow_cycle_streak"),
    ("trade_flow_outage", alerts.TRADE_FLOW_OUTAGE_CYCLES, "trade_flow_outage_streak"),
    ("truth_supply_zero", alerts.TRUTH_SUPPLY_ZERO_CYCLES, "truth_supply_zero_streak"),
])
def test_existing_modulo_guards_do_not_starve_under_cooldown(key, modulo, field):
    """两层节流叠加,若触发时刻恒落在冷却窗内 ⇒ 永远发不出来(防洪自己制造静默失败)。

    冷却 12 轮能被 3/4/6 整除是关键;有人把它改成 5 或 7,这条必须变红。
    """
    state: dict = {}
    sent = 0
    for cycle in range(1, 97):                  # 连续故障 24 小时
        counts = {field: cycle, "firehose_fail": 1 if field.startswith("trade_flow") else 0}
        pairs = alerts.build_alert_keyed(counts)
        lines, state, _n = alerts.throttle(pairs, state, now=cycle * _step_s())
        sent += 1 if lines else 0
    assert sent >= 2, f"{key} 在 24 小时持续故障下只推了 {sent} 条 = 饿死"


# ---------- 状态按链路隔离(R3) ----------

def test_throttle_state_path_is_per_link():
    assert alerts._throttle_path("cycle") != alerts._throttle_path("watchdog")


# ---------- 升级路径不许被压住(R6) ----------

def test_escalation_to_a_different_key_is_not_suppressed():
    """firehose「抽风」冷却期内升级成「持续断供」,必须照推(两者是不同 key)。"""
    blip = alerts.build_alert_keyed({"firehose_fail": 1, "trade_flow_outage_streak": 1})
    sustained = alerts.build_alert_keyed(
        {"firehose_fail": 1, "trade_flow_outage_streak": alerts.TRADE_FLOW_OUTAGE_CYCLES})
    assert [k for k, _ in blip] != [k for k, _ in sustained] != []
    _, st, _n = alerts.throttle(blip, {}, now=0.0)
    lines, _, _n = alerts.throttle(sustained, st, now=_step_s())
    assert lines, "升级被自己的前一档压住了"


# ---------- 两头都焊死:稳态静默 + 退化日不洪水 ----------

@pytest.mark.parametrize("day", ["2026-08-24", "2026-08-25"])
def test_steady_state_is_completely_silent(day, heartbeats):
    assert _replay(heartbeats[day], throttled=True) == []


@pytest.mark.parametrize("day", ["2026-08-19", "2026-08-20"])
def test_degraded_day_no_longer_overflows_the_queue(day, heartbeats):
    """两头都验,而且**先验判据本身有分辨力**:

    现状(不节流)必须**越线** —— 若它也不越线,这条判据就跟着永远绿,
    什么都没在验证。它越线这件事本身也解释了 08-14 那 41 条被丢弃的告警。
    """
    before = _worst_window(_replay(heartbeats[day], throttled=False))
    after = _worst_window(_replay(heartbeats[day], throttled=True))
    assert before >= alerts.PENDING_MAX, \
        f"{day} 现状只积压 {before} 条,没越过 {alerts.PENDING_MAX} 格 —— 判据失去分辨力"
    assert after < alerts.PENDING_MAX, \
        f"{day} 节流后 458 分钟窗内仍积压 {after} 条,会挤爆 {alerts.PENDING_MAX} 格队列"
    assert len(_replay(heartbeats[day], throttled=True)) >= 1, \
        f"{day} 一条都不推 = 压过头了,真异常没人知道"


# ---------- 生产路径:必须验 maybe_alert_with_counts 真的接了节流 ----------
# ⚠️ 上面那批判据全在直接调 `throttle`。那验的是零件,不是装配 ——
# 把 maybe_alert_with_counts 里的节流整段删掉,它们**一条都不会红**。
# 这就是 [[lesson-tests-that-dont-run-production-path]] 的形状,故补这一组:
# 只替换网络(`_send`)与状态目录,其余 build_alert_keyed → throttle → 落盘 →
# compose_body → dispatch 全程走真代码。

class _Channel:
    def __init__(self, up=True):
        self.up, self.sent = up, []

    def __call__(self, msg, max_retries=2):
        self.sent.append(msg)
        return self.up


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """把状态/队列目录指到 tmp(铁律:判据绝不许往生产状态目录写)。"""
    monkeypatch.setattr(alerts, "_QUEUE_DIR", tmp_path)
    ch = _Channel()
    monkeypatch.setattr(alerts, "_send", ch)
    return ch, tmp_path


FAULT = {"settlement_lookup_fail": 170, "settlement_checked": 170, "total_markets_polled": 50}


def test_production_entry_sends_the_first_occurrence(wired):
    ch, _ = wired
    r = alerts.maybe_alert_with_counts(FAULT)
    assert r["sent_fresh"] is True
    assert len(ch.sent) == 1 and "结算守望查询失败" in ch.sent[0]


def test_production_entry_suppresses_the_repeat(wired):
    """同一个故障连推两轮 —— 第二轮必须一个字都不发出去。"""
    ch, _ = wired
    alerts.maybe_alert_with_counts(FAULT)
    r = alerts.maybe_alert_with_counts(FAULT)
    assert len(ch.sent) == 1, f"节流没接上,发了 {len(ch.sent)} 次"
    assert r["sent_fresh"] is False
    assert r["throttle_suppressed"] == 1


def test_production_entry_persists_state_across_processes(wired):
    """每轮是独立进程,退出即失忆 —— 冷却不落盘等于没有冷却。"""
    ch, tmp = wired
    alerts.maybe_alert_with_counts(FAULT)
    saved = json.loads((tmp / "alert_throttle_cycle.json").read_text())
    assert "settlement_lookup_fail" in saved["throttle"]["last"]


def test_cooldown_is_consumed_even_when_the_channel_is_down(wired):
    """⭐这条是整件事的立论点:通道断了才是队列会被挤爆的时刻。

    若把冷却改成"送达才起算",通道一断冷却就全失效 —— 而那恰恰是最需要它的时候,
    正是 2026-08-14 丢掉 41 条告警的机制。
    """
    ch, _ = wired
    ch.up = False
    for _ in range(6):
        alerts.maybe_alert_with_counts(FAULT)
    depth = alerts.queue_stats("cycle")["queue_depth"]
    assert depth == 1, f"通道断着的 6 轮里排了 {depth} 条,冷却没起作用"


def test_production_entry_passes_again_after_the_window(wired):
    """把落盘的时刻改老,模拟冷却窗过去(比猴补时钟更接近真实:状态就是从盘上读的)。"""
    ch, tmp = wired
    alerts.maybe_alert_with_counts(FAULT)
    f = tmp / "alert_throttle_cycle.json"
    st = json.loads(f.read_text())
    st["throttle"]["last"]["settlement_lookup_fail"] -= alerts.throttle_cooldown_s() + 1
    f.write_text(json.dumps(st))
    alerts.maybe_alert_with_counts(FAULT)
    assert len(ch.sent) == 2, "冷却窗过了还不推 = 饿死"


def test_legacy_maybe_alert_is_deliberately_not_throttled(wired):
    """约 15 条老判据在循环里调 maybe_alert 并断言 True —— 它必须保持不节流。

    这不是疏漏,是有意的;生产路径不走它(test_alert_queue.py 有判据焊住)。
    """
    ch, _ = wired
    assert alerts.maybe_alert(FAULT) is True
    assert alerts.maybe_alert(FAULT) is True
    assert len(ch.sent) == 2


# ---------- 被压住的次数必须有**读取者**(记录事实/使用事实要两头都接) ----------
# ⚠️ 这组是自查抓出来的:第一版我让 maybe_alert_with_counts 返回 throttle_suppressed
# 就收工了,而**没有任何人读它** —— 正是本项目犯过 4 次的「只接一头」。
# 压住一条告警是一次**降级**,铁律要求出声计数;只在 Telegram 正文里带尾巴不够,
# 因为那条消息本身可能正卡在发不出去的队列里。

def test_suppressed_count_is_per_cycle_not_cumulative():
    """日报按天求和 ⇒ 若返回"还欠着几条",同一条会被重复计进多轮。"""
    pairs = [("settlement_lookup_fail", "⚠️ A")]
    _, st, n0 = alerts.throttle(pairs, {}, now=0.0)
    assert n0 == 0, "第一次是放行,不该算被压住"
    for i in range(1, 5):
        _, st, n = alerts.throttle(pairs, st, now=i * _step_s())
        assert n == 1, f"第 {i} 轮压住 1 条,却报了 {n}(口径成了累计)"


def test_suppressed_count_reaches_the_daily_digest():
    """整条接线:throttle → maybe_alert_with_counts → 心跳 schema → 日报正文。

    断的是哪一环都会红:字段没进 AUDIT_FIELDS / 没进 CONSUMED_FIELDS / 日报没印出来。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "11-collector"))
    import daily_digest as dd
    import storage_engine as se

    assert "alert_throttle_suppressed" in se.AUDIT_FIELDS, "字段没进心跳 schema"
    assert "alert_throttle_suppressed" in dd.CONSUMED_FIELDS, "日报没声明读它 ⇒ 仍是孤儿"

    rows = [{"alert_throttle_suppressed": 3}] + [{"alert_throttle_suppressed": 0}] * 95
    body = dd.render(rows)
    assert "3" in body and "压住" in body, f"日报没把它印出来:\n{body}"

    quiet = dd.render([{"alert_throttle_suppressed": 0}] * 96)
    assert "压住" not in quiet, "稳态下不该出现这一行(防洪自己也得防洪)"


def test_run_cycle_actually_writes_the_field():
    """结构检查:心跳字段必须有**写入者**,而写入发生在 run_cycle 的主干里。

    光有 schema(storage_engine)和读者(daily_digest)不够 —— 三者缺任何一环,
    日报都会永远读到空,而且**不报错**。上面两条判据盖住了 schema 与读者,
    这条盖住写入者。

    ⚠️ 为什么退而用源码断言:真正的行为断言要跑一整轮采集(网络 + 15 分钟),
    代价与收益不成比例。同形状的既有判据:test_trade_flow_outage_guard.py
    ::test_run_cycle_actually_computes_it。
    ⚠️ 它的**弱点如实写明**:改个变量名就会漏过去。故断言用的是完整的赋值语句
    (含右侧取值),不是只找字段名 —— 字段名单独出现在 schema 和日报里也算数,
    那样就废了。
    """
    src = (PROJECT_ROOT / "11-collector" / "run_cycle.py").read_text()
    assert 'merged["alert_throttle_suppressed"] = ar["throttle_suppressed"]' in src, \
        "run_cycle 没把它写进心跳 ⇒ 日报永远读到空"


def test_production_entry_reports_per_cycle_not_cumulative(wired):
    """⭐变异 M8 抓出来的洞:上一条只验了 `throttle` 的口径,没验**生产入口用哪个**。

    把 maybe_alert_with_counts 里改回 `sum(state["suppressed"].values())`(累计口径),
    上一条判据照样全绿 —— 而日报按天求和,同一条会被重复计进多轮。
    """
    _, _ = wired
    alerts.maybe_alert_with_counts(FAULT)                 # 第 1 轮放行
    seen = [alerts.maybe_alert_with_counts(FAULT)["throttle_suppressed"] for _ in range(3)]
    assert seen == [1, 1, 1], f"生产入口报的是累计口径:{seen}(应为逐轮 1)"


def test_expired_entries_are_pruned_so_steady_state_leaves_no_file(wired):
    """稳态不留垃圾文件 —— 本文件既有惯例(队列送达即 unlink)。

    过期条目扔掉是**语义等价**的(过期 = 必然放行),但能让故障结束后状态自己清干净。
    """
    ch, tmp = wired
    f = tmp / "alert_throttle_cycle.json"
    alerts.maybe_alert_with_counts(FAULT)
    assert f.exists(), "冷却期内必须落盘,否则下一轮(独立进程)就忘了"

    st = json.loads(f.read_text())
    st["throttle"]["last"]["settlement_lookup_fail"] -= alerts.throttle_cooldown_s() + 1
    f.write_text(json.dumps(st))
    alerts.maybe_alert_with_counts({"total_markets_polled": 50})    # 故障没了
    assert not f.exists(), "故障早就结束,状态文件还赖着"


def test_pruning_does_not_change_throttle_decisions():
    """等价性:剪枝前后,同一串输入的放行序列必须逐字相同(否则就不是"语义等价")。"""
    pairs = [("settlement_lookup_fail", "⚠️ A")]
    st, seen = {}, []
    for i in range(40):
        lines, st, _ = alerts.throttle(pairs, st, now=i * _step_s())
        seen.append(bool(lines))
    period = alerts.THROTTLE_COOLDOWN_CYCLES
    assert seen == [i % period == 0 for i in range(40)], f"放行节奏变了:{seen}"


# ---------- 坏输入(2026-08-28 code review 判 Block 的两条) ----------
# ⚠️ review 的原话对症:上面 32 条覆盖了"逻辑对不对",没覆盖"输入脏了会怎样",
# 而后者正是 CLAUDE.md 异常清单反复强调、且要求**造真的坏输入跑一遍**的那一类。

@pytest.mark.parametrize("bad", [
    "garbage",                              # state 整个不是 dict
    {"last": "oops", "suppressed": {}},     # last 是字符串
    {"last": [1, 2], "suppressed": {}},     # last 是列表
    {"last": {}, "suppressed": "oops"},     # suppressed 是字符串
    {"last": {"k": "not-a-number"}},        # 时间戳不是数
    {"suppressed": {"k": "not-an-int"}},    # 计数不是数
])
def test_malformed_state_degrades_to_pushing_not_crashing(bad):
    """设计单 §2.7 承诺"状态损坏会退化成立刻推一条,方向是安全的"。

    ⭐ 那句话原先是**假话**:`cycle_state.read_state` 只挡"读不出来",
    挡不住"语法合法但形状不对"。实测四种形状分属三个异常家族全崩。
    这条判据就是把那句承诺变成真的 —— 注释里承诺的保护必须实测接得住。
    """
    lines, st, _ = alerts.throttle([("k", "⚠️ x")], bad, now=0.0)
    assert lines == ["⚠️ x"], "坏状态下应当放行(安全方向),而不是崩或静默压住"
    assert isinstance(st["last"], dict) and isinstance(st["suppressed"], dict)


def test_a_corrupt_state_file_does_not_take_down_the_whole_cycle(wired):
    """放大效应:run_cycle.main 零 try/except,且心跳写在告警**之后**。

    告警这一步崩掉 ⇒ 整轮心跳不写 ⇒ 看门狗报"采集器死了",
    而轮询/结算其实全都成功了 —— 一次"记账最后一步摔了"被误读成"整轮没跑"。
    """
    ch, tmp = wired
    (tmp / "alert_throttle_cycle.json").write_text('{"throttle": {"last": "oops"}}')
    r = alerts.maybe_alert_with_counts(FAULT)      # 不许抛
    assert r["sent_fresh"] is True, "坏状态下应当照常推,而不是被吞掉"


def test_clock_jumping_forward_then_back_does_not_wedge_a_key():
    """RTC 读到错误的未来 → NTP 纠正回来 ⇒ now - prev 恒为负。

    实测(修之前):now 跳到 10,000,000 再回到 1,000 ⇒ 该 key 静默 **2,780 小时**。
    本项目别处已因"NTP 会把时钟拨得忽前忽后"改用 monotonic,说明这是真实风险。
    """
    pairs = [("k", "⚠️ x")]
    _, st, _ = alerts.throttle(pairs, {}, now=10_000_000.0)
    lines, st, _ = alerts.throttle(pairs, st, now=1000.0)
    assert lines, "时钟被纠正回来之后该 key 被卡死了"
    assert st["last"]["k"] == 1000.0, "不可信的时间戳应当被当前时刻覆盖"


class _Dying:
    """模拟"正在发送时被 SIGTERM 杀掉":消息既没发出去,也还没进队列文件。"""

    def __call__(self, msg, max_retries=2):
        raise SystemExit("SIGTERM during _send")


def test_cooldown_is_not_consumed_when_the_process_dies_mid_send(tmp_path, monkeypatch):
    """⭐ 冷却必须在 dispatch **之后**才落盘。

    `dispatch` 里 `items.append` 只在内存,只有 `_send` 返回 False 才落队列文件。
    进程若在 `_send`(最坏阻塞 30s)期间被杀,这条消息**既没发出也没进队列**。
    此时若冷却已经提前记下,同一个 key 会被压住最长 3 小时 ——
    而这恰好打在 `offset_overflow_warm`(唯一被证明与永久数据丢失挂钩的那条)上,
    它改动前是每轮无条件推、丢一次下轮自愈。放在 dispatch 之后,自愈保持不变。
    """
    monkeypatch.setattr(alerts, "_QUEUE_DIR", tmp_path)
    monkeypatch.setattr(alerts, "_send", _Dying())
    with pytest.raises(SystemExit):
        alerts.maybe_alert_with_counts(FAULT)
    assert not (tmp_path / "alert_throttle_cycle.json").exists(), \
        "进程被杀时冷却却已记下 ⇒ 那条丢掉的告警会被压住 3 小时"

    monkeypatch.setattr(alerts, "_send", _Channel())     # 下一轮:通道好了
    assert alerts.maybe_alert_with_counts(FAULT)["sent_fresh"] is True, "自愈没了"


def test_no_production_script_uses_the_unthrottled_entry():
    """结构检查:未节流的 `maybe_alert` 只许判据用,生产脚本一律走 `maybe_alert_with_counts`。

    既有判据(test_alert_queue.py)只查 `run_cycle.main` 一个函数,而本项目
    独立脚本很多(watchdog / daily_digest / backfill 各自直连 alerts)。
    将来有人"照抄"一段 `alerts.maybe_alert(counts)` 到新脚本里,不会撞上任何判据。
    故这条扫全目录。
    """
    offenders = []
    for p in sorted((PROJECT_ROOT / "11-collector").glob("*.py")):
        if p.name == "alerts.py":          # 自身的 __main__ 自测允许
            continue
        if "alerts.maybe_alert(" in p.read_text():
            offenders.append(p.name)
    assert not offenders, f"这些生产脚本用了未节流的入口:{offenders}"
