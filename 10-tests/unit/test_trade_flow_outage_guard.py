#!/usr/bin/env python3
"""判据焊死:成交流持续断供 —— 告警不许在持续故障下说「数据不丢」(2026-08-17)。

## 由来(实测,非假想)

2026-08-13 13:25Z → 08-14 03:00Z,**连续 56 轮(14.0 小时)** firehose 一笔都没抓到:
`firehose_trades: 0 / active_cids: 0 / 市场 0 / 新成交 0 / 重试耗尽 1`。
用相邻天同小时做基线对照测算,**永久丢失约 1.4 万笔**(集中在 08-13 13:00–15:00 UTC,
即缺口最老的那两小时 —— 网络恢复后轮询往回翻页只补得回后半段)。

⭐**告警不是没响,是响了 61 次而每次都说了假话。** `alerts.build_alert` 里那条
`firehose_fail > 0` 的 🔴 正文写着:

    「…本轮空转;**数据不丢(下轮自愈回填)**,但接口若持续失败须查 IP/限流」

「数据不丢」在**单轮抖动**下是真的(下一轮翻页就补回来了),在**持续 14 小时**下是假的 ——
超过约 11.6 小时一圈(`OFFSET_CAP=10000` 除以 p99.9 成交率)最活跃的市场就会出永久空洞。
这句安慰话是按单轮那个情形写的,**没人回头审过它在持续情形下还成不成立**。

结果:61 条告警生成,20 条在 458 分钟后补发,**41 条因队列上限被丢弃**;
而那 20 条内容完全相同,读的人无从知道这事已经持续了 14 小时。

## 三件要焊的事

1. **正文不许说假话**:持续 ≥ 阈值时,不许再出现「数据不丢」,且必须给出持续时长。
2. **防洪**:不许每轮都推(61 条=噪音)。按整数倍复述,14 小时推 9 条而不是 61 条。
3. **恢复时给一条总结**:多久、多少轮。不给的话,人只看到告警莫名其妙停了。

## 阈值怎么定的(实测分布,2026-08-05 ~ 08-17)

`firehose_fail > 0` 的连续段全表:

    56 轮 (14.0h)  ← 2026-08-13 那次事故
     3 轮 (0.8h)   ← 08-06 / 08-08 / 08-12,各一次
     1 轮 (0.2h)   ← 08-09 一次、08-14 五次

⇒ **良性最长 3 轮**,事故 56 轮,中间空档极大。取 `N = 6`(= 良性最大值的 2 倍,1.5 小时):
稳态永不触发,而 08-13 那次会在 1.5 小时内升级。

## ⭐为什么判据钉在 `firehose_fail` 而不是 `new_trades == 0`

实测同期有 **5 轮**是「抓到了但没有新成交」(`new_trades=0` 且 `firehose_fail=0`) ——
那是**没活可干**(正常),不是**有活没干成**(异常)。用 `new_trades` 当判据会把这 5 轮
一起报进去,而「区分这两者」正是静默失败清单第 6 条点名要求的。

## 本判据**不能**回答什么

- 不回答「整机断网时怎么把告警送出去」。那需要第二条独立通道,`alerts.py` 里已写明不在范围内。
  本判据只保证**送得出去的时候,说的是真话**。
- 不回答「丢了多少笔」。损失量取决于哪些市场有多热,只能事后用基线对照测算,
  不能在告警里凭空给数字 —— 故正文只说**风险已经成立**,不编造损失量。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import alerts  # noqa: E402
import cycle_state  # noqa: E402


# 实测(2026-08-05 ~ 08-17,心跳 parquet 里 firehose_fail>0 的连续段):
MEASURED_BENIGN_MAX_RUN = 3     # 良性最长:08-06 / 08-08 / 08-12 各一次
MEASURED_OUTAGE_RUN = 56        # 2026-08-13 那次事故,14.0 小时


def _counts(**kw):
    base = {"firehose_fail": 0, "new_trades": 5000, "total_markets_polled": 50}
    base.update(kw)
    return base


# ---------- 判据组 A:阈值有实测分布撑着 ----------

def test_threshold_sits_above_the_measured_benign_maximum():
    """阈值必须高于实测良性最长段,否则稳态会误报(CLAUDE.md F2)。

    余量取 2 倍:良性 3 轮 → 阈值 6 轮。低于这个就是在拿噪音换灵敏度,
    而噪音会让真信号无处可显(08-03 那次推了 1340 条没一条相关)。
    """
    assert alerts.TRADE_FLOW_OUTAGE_CYCLES > MEASURED_BENIGN_MAX_RUN, (
        f"阈值 {alerts.TRADE_FLOW_OUTAGE_CYCLES} 没高过实测良性最长 "
        f"{MEASURED_BENIGN_MAX_RUN} 轮 ⇒ 稳态会误报")
    assert alerts.TRADE_FLOW_OUTAGE_CYCLES >= 2 * MEASURED_BENIGN_MAX_RUN


def test_threshold_would_have_caught_the_real_outage_early():
    """反面:那次真事故必须被抓到,而且要早 —— 损失发生在最前面几小时。"""
    assert alerts.TRADE_FLOW_OUTAGE_CYCLES < MEASURED_OUTAGE_RUN / 4, (
        "阈值太高:等它触发时事故已经过去大半,而永久损失恰恰发生在最前段")


# ---------- 判据组 B:防洪两头(稳态静默 + 真异常必推) ----------

def test_steady_state_is_completely_silent():
    """稳态一个字都不许出:firehose 正常时不推。"""
    assert alerts.build_alert(_counts()) is None


def test_fetched_but_nothing_new_is_not_an_outage():
    """⭐「抓到了但没新成交」是**没活可干**,不许报。

    实测同期有 5 轮是这个形态(`new_trades=0` 且 `firehose_fail=0`)。
    把它算进去 = 把正常当故障 = 静默失败清单第 6 条禁止的混淆。
    """
    body = alerts.build_alert(_counts(new_trades=0, trade_flow_outage_streak=0))
    assert body is None, f"没活可干却报了警:{body}"


def test_a_short_blip_still_reports_but_does_not_escalate():
    """单轮/短暂抽风:保持原行为(仍然推那条 🔴),但**不**升级成持续断供。"""
    body = alerts.build_alert(_counts(firehose_fail=1, new_trades=0,
                                      trade_flow_outage_streak=1))
    assert body and "firehose 抽风" in body
    # ⚠️ 断言要钉在**升级标记**上,不能拿"持续"两个字当判据 ——
    #    短消息本来就有一句"接口若持续失败须查 IP/限流",那是正常措辞不是升级。
    #    (初版判据就栽在这里:抓的是词不是事,红得毫无意义。)
    assert "成交流持续断供" not in body, "才 1 轮就升级,会让人对红灯脱敏"


def test_sustained_outage_escalates_at_the_threshold():
    """连续到阈值必须升级,并说清已经多久。"""
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    body = alerts.build_alert(_counts(firehose_fail=1, new_trades=0,
                                      trade_flow_outage_streak=n))
    assert body and "成交流持续断供" in body
    hours = n * alerts.cycle_minutes() / 60
    assert f"{hours:.1f}" in body, f"正文里没有持续时长,读的人无从判断严重性:{body}"


def test_it_repeats_on_multiples_not_every_cycle():
    """⭐防洪:14 小时那次真事故不许再推 61 条。

    按整数倍复述 —— 阈值 6 时,56 轮只推 9 条。
    """
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    fired = [s for s in range(1, MEASURED_OUTAGE_RUN + 1)
             if "成交流持续断供" in (alerts.build_alert(
                 _counts(firehose_fail=1, new_trades=0, trade_flow_outage_streak=s)) or "")]
    assert fired == [s for s in range(1, MEASURED_OUTAGE_RUN + 1) if s % n == 0]
    assert len(fired) <= MEASURED_OUTAGE_RUN / 4, (
        f"{MEASURED_OUTAGE_RUN} 轮里推了 {len(fired)} 条,还是太吵")


# ---------- 判据组 C:⭐正文不许说假话 ----------

def test_the_no_data_loss_promise_is_dropped_once_sustained():
    """⭐⭐本判据的核心:持续故障下**不许**再说「数据不丢」。

    08-13 那次它说了 61 遍,而当天永久丢了约 1.4 万笔。
    「数据不丢」只在单轮抖动下成立(下轮翻页补得回来);一圈超过 ~11.6 小时,
    最活跃的市场就会出永久空洞 —— 而告警正文对这个区别一无所知。
    """
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    short = alerts.build_alert(_counts(firehose_fail=1, new_trades=0,
                                       trade_flow_outage_streak=1))
    long = alerts.build_alert(_counts(firehose_fail=1, new_trades=0,
                                      trade_flow_outage_streak=n))
    assert "数据不丢" in short, "单轮抖动下这句是真的,不该删"
    assert "数据不丢" not in long, (
        "持续断供了还在说「数据不丢」—— 这正是 08-13 推了 61 遍的那句假话")
    assert "永久" in long, "升级后必须点明永久空洞的风险,否则读的人不会当回事"


def test_it_never_fabricates_a_loss_number():
    """不许在告警里编造"丢了多少笔"。

    损失量取决于哪些市场有多热,只能事后用基线对照测算(08-13 那 1.4 万笔就是这么算的)。
    在告警里编一个数字出来 = 用与实测相同的语气陈述推断,本项目明令禁止。
    正文只该说**风险已经成立**,不该说损失是多少。
    """
    import re
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    body = alerts.build_alert(_counts(firehose_fail=1, new_trades=0,
                                      trade_flow_outage_streak=2 * n))
    assert body and "成交流持续断供" in body
    assert not re.search(r"[\d,.]+\s*万?笔", body), (
        f"正文里出现了具体的成交笔数,那只能是编的:{body}")


# ---------- 判据组 D:恢复要给一条总结 ----------

def test_recovery_emits_one_summary():
    """恢复时推一条总结(多久、多少轮),否则告警只是莫名其妙地停了。"""
    n = alerts.TRADE_FLOW_OUTAGE_CYCLES
    body = alerts.build_alert(_counts(trade_flow_outage_recovered=MEASURED_OUTAGE_RUN))
    assert body and "恢复" in body
    assert str(MEASURED_OUTAGE_RUN) in body
    assert f"{MEASURED_OUTAGE_RUN * alerts.cycle_minutes() / 60:.1f}" in body


def test_recovery_from_a_blip_stays_silent():
    """短暂抽风恢复不推 —— 否则每天几条"已恢复",又是噪音。"""
    assert alerts.build_alert(_counts(trade_flow_outage_recovered=1)) is None
    assert alerts.build_alert(
        _counts(trade_flow_outage_recovered=MEASURED_BENIGN_MAX_RUN)) is None


# ---------- 判据组 E:接线 —— 算了必须有人读(记录/使用两头都接上) ----------

def test_streak_advances_and_resets_with_the_shared_helper():
    """连计用共用底座推进,不许再手写第六份。"""
    s = 0
    for _ in range(4):
        s = cycle_state.next_hit_streak(s, hit=True)
    assert s == 4
    assert cycle_state.next_hit_streak(s, hit=False) == 0


def test_the_streak_reaches_the_heartbeat():
    """⭐算出来的量必须能在心跳里查到(不变量 C1),否则又是个孤儿字段。

    范式照抄已有的四个连计(`slow_cycle_streak` / `rotation_hole_streak` /
    `truth_supply_zero_streak` / `register_zero_streak`):它们由 `run_cycle` 在
    主干上算好塞进 `merged`,**只进 `AUDIT_FIELDS`、不进 `COUNTER_KEYS`**
    (后者是 `collector_core` 自己那批计数器的初始化清单)。
    ⚠️ 本条最初写成"也要进 COUNTER_KEYS",是我照错了范式 —— 那会逼出第六种写法。
    """
    import storage_engine as se
    assert "trade_flow_outage_streak" in se.AUDIT_FIELDS, (
        "连计没进心跳 schema ⇒ 落不了盘 ⇒ 日后没法回头查这次断供有多久")
    assert "trade_flow_outage_recovered" in se.AUDIT_FIELDS, (
        "恢复总结的依据也要留痕,否则'当时到底断了多久'事后无从复核")


def test_run_cycle_actually_computes_it():
    """⭐零件合格 ≠ 装上车:主干必须真的算这个连计并交给 alerts。"""
    import run_cycle
    src = Path(run_cycle.__file__).read_text(encoding="utf-8")
    assert "TRADE_FLOW_STREAK_FILE" in src and "trade_flow_outage_streak" in src, (
        "run_cycle 没有计算并传递这个连计 —— 判据组 A~D 全是空转")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
