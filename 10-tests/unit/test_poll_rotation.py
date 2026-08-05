#!/usr/bin/env python3
"""判据焊死:轮询层「每轮取前 N 个」必须能回答「第 N+1 个何时轮到」(2026-08-06)。

## 由来(实测,非假想)

`run_once` 里有一刀硬截断:`markets = markets[:limit]`(limit=50),
而 `markets` 的顺序来自 firehose = **按最近成交排序**。
即每轮永远只采「最近成交的前 50 个」,冷门盘每轮都被砍。

⚠️ **而计数器对这一刀是瞎的**:`poll_budget_skipped_count` 在 `poll_markets` 里算的是
`len(markets) - polled`,而传进去的 `markets` **已经被截断过了** ——
它只看得见那 50 个里因超时没轮到的。实测心跳里「没轮到 轮询」**每轮都是 0**,
而真相是每轮砍掉约 979 个(pollable 1029 / 实采 50)。

**一个恒为 0 的数,是因为它没在看,不是因为没事** —— 这正是本项目反复发作的那个病。

## 后果实测(2026-08-06,DuckDB 查全量数据湖)

| | 市场数 | 从没采到过一笔成交 |
|---|---|---|
| 进行中(还能轮询) | 20,340 | **13,163(65%)** |
| 已关闭(**再也轮不到**) | 46,816 | **23,563(50%)** |

注册表 67,156 个市场,成交流里只有 30,430 个 —— **一半以上从来没被采过**。

上一份交接写的「有 watermark 兜底不丢数据」**不成立**:watermark 只在"终于轮到了"
之后才起作用。冷门盘永远排在 50 名外,等它关闭就被 `pollable` 过滤掉 → **永久轮不到**。
且丢得**与结果相关**(专丢低频盘),是 CLAUDE.md 铁律 2 点名的致命形态。

## ⭐ 为什么不能纯轮转:实测的成交率分布否掉了第一版设计

单市场每小时成交笔数(2026-08 样本,n=298,697 个市场·小时):
**p50=2 / p90=12 / p99=126 / p99.9=859 / max=3,954**

纯轮转一圈 ~5 小时的话,最热的盘会攒 3954×5 ≈ **19,770 笔 > `OFFSET_CAP` 10,000**
→ 分页截断 → **用一个新的丢数据换掉旧的**。

故正解是**劈成两片**:
- **新鲜片**(`limit - rotate_share`):照旧按最近成交取。热门盘天然总在这一片里,
  于是它们仍每轮被采 —— 这不是巧合:**成交率高 ⇔ 一直很"新鲜"**,两者是同一件事。
- **轮转片**(`rotate_share`):游标轮转 + 回卷,给冷门盘一个**有界**的等待上限。

## 轮转片大小的推导(有实测支撑,不是拍的)

一圈轮数 = `pollable / rotate_share`,一轮 15 分钟。
要求:一圈之内累积成交不得超过 `OFFSET_CAP`(否则截断)。

    rotate_share = 15,pollable ≈ 1029 → 一圈 69 轮 ≈ 17.2 小时
    → 保护得住 10000 / 17.2 ≈ 581 笔/小时以下的市场 = **p99.9 以下全覆盖**

而 581 笔/小时以上的(< 0.1% 市场·小时)由**新鲜片**兜住:那种成交密度意味着它几乎
每分钟都在成交,必然排在最近成交的最前面。

⭐ **这个"必然"是设计假设,不是实测。** 万一不成立,现成的 `offset_overflow_count`
会变非零(它就是量分页截断的)—— 即**假设错了会被看见**,而不是静默。

## 本测试**不能**回答什么

1. **不回答"多久能把 13,163 个补齐"。** 那取决于这些市场未来还出不出现在 firehose 里,
   而我们只有采样。本轮只保证:**出现在 pollable 里的,等待上限是有界的**。
2. **不回答已关闭那 23,563 个能不能补回来。** 那是历史回填,另立项。
3. **不断言 50 这个总额是对的。** 总额受时间闸(170s)约束,是硬限制;
   本判据只管这 50 个名额**怎么分**,不管该不该是 50。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import collector_core as cc  # noqa: E402
import storage_engine as se  # noqa: E402

# --- 实测常量(2026-08-06 真机,DuckDB 查全量数据湖)---
MEASURED_TRADES_PER_HOUR_P99 = 126
MEASURED_TRADES_PER_HOUR_P999 = 859
MEASURED_POLLABLE_PER_CYCLE = 1029
CYCLE_MINUTES = 15


def _m(i: int) -> dict:
    return {"condition_id": f"0x{i:064x}", "token_id_0": "1", "token_id_1": "2"}


def _markets(n: int) -> list[dict]:
    """按 firehose 新鲜度排序的可轮询市场(下标 0 = 最近成交)。

    ⭐**顺序必须与 condition_id 无关**:真实数据里"最近成交"和"ID 大小"毫不相干。
    最初这里写的是 `range(n)`(新鲜度顺序 ≡ cid 顺序),于是"轮转片有没有按 cid 排序"
    这件事在判据里**完全看不出差别** —— 变异测试实测:去掉排序,17 条判据全绿。
    替身不忠实于真实性质,判据就测不到那个性质。用确定性打乱(可复现,非随机)。
    """
    return [_m((i * 7919) % n) for i in range(n)]


def _cids(picked) -> list[str]:
    return [m["condition_id"] for m in picked]


# ============ 判据组 A:被砍掉的必须真的被数到 ============

def test_skipped_count_sees_the_hard_truncation():
    """⭐⭐核心:「本轮砍掉了多少个」必须是**真数**。

    改之前这个数恒为 0,因为它在硬截断**之后**才算 —— 于是最大的一刀完全不可见。
    """
    markets = _markets(1000)
    picked, _, skipped = cc.select_poll_targets(markets, wms={}, limit=50, cursor="")
    assert len(picked) == 50
    assert skipped == 950, f"砍掉 950 个却报 {skipped} —— 计数器对硬截断是瞎的"


def test_nothing_skipped_when_budget_covers_everything():
    """防洪另一头:名额够用时必须报 0,否则这个数天天非零 = 没信号。"""
    markets = _markets(30)
    picked, _, skipped = cc.select_poll_targets(markets, wms={}, limit=50, cursor="")
    assert len(picked) == 30 and skipped == 0


def test_no_limit_means_no_truncation():
    """向后兼容:不设上限时全采,行为不变。"""
    markets = _markets(300)
    picked, _, skipped = cc.select_poll_targets(markets, wms={}, limit=None, cursor="")
    assert len(picked) == 300 and skipped == 0


# ============ 判据组 B:热门盘不能被轮转饿死(否则是用新病换旧病) ============

def test_freshest_markets_are_still_polled_every_round():
    """⭐新鲜片必须存在:实测最热的盘 3954 笔/小时,等一圈就爆 OFFSET_CAP。

    纯轮转会让它们等一圈 → 分页截断 → **用一个新的丢数据换掉旧的**。
    """
    markets = _markets(1000)
    picked, _, _ = cc.select_poll_targets(markets, wms={}, limit=50, cursor="")
    fresh_head = _cids(markets[:cc.poll_fresh_share(50)])
    got = set(_cids(picked))
    assert set(fresh_head) <= got, "最近成交的那一片没被全采 —— 热门盘会攒爆分页上限"


def test_fresh_share_is_the_majority_but_rotation_is_not_zero():
    """两片都不许退化成 0:全给新鲜片 = 饿死原样复活;全给轮转 = 热门盘爆表。"""
    n_rot = cc.POLL_ROTATE_SHARE
    assert 0 < n_rot < 50, f"轮转片 {n_rot} 退化了"
    assert cc.poll_fresh_share(50) > n_rot, "新鲜片应占多数(热门盘的保护)"


# ============ 判据组 C:游标轮转必须真的能转完一圈(不许有人永远轮不到) ============

def test_cursor_advances_and_covers_everyone_within_one_lap():
    """⭐⭐「第 N+1 个何时轮到」必须有答案:一圈之内**每个**市场都要被采到。

    这条是整件事的核心。没有它,前面的计数只是把饿死变得可见,并没有解决饿死。
    """
    total = 200
    markets = _markets(total)
    n_rot = cc.POLL_ROTATE_SHARE
    lap = -(-total // n_rot) + 2          # 一圈所需轮数(向上取整)+ 余量
    seen, cursor = set(), ""
    for _ in range(lap):
        picked, cursor, _ = cc.select_poll_targets(markets, wms={}, limit=50, cursor=cursor)
        seen |= set(_cids(picked))
    missing = set(_cids(markets)) - seen
    assert not missing, f"{len(missing)} 个市场转了一整圈仍没轮到 —— 饿死没被解决"


def test_cursor_wraps_around_at_the_end():
    """走到末尾要回卷,不是停在那里(停住 = 后半段永远轮不到)。

    ⚠️ "末尾"指的是 **cid 排序的末尾**,不是 firehose 顺序的最后一个 ——
    两者无关。写成 `_cids(markets)[-1]` 时游标压根没走到需要回卷的位置,
    这条判据就成了摆设(变异测试实测:去掉回卷,它照样绿)。
    """
    markets = _markets(100)
    last_cid = max(_cids(markets))
    picked, cursor, _ = cc.select_poll_targets(markets, wms={}, limit=50, cursor=last_cid)
    rot = set(_cids(picked)) - set(_cids(markets[:cc.poll_fresh_share(50)]))
    assert rot, "游标停在末尾后轮转片空了 —— 没有回卷"


def test_cursor_survives_a_changing_market_set():
    """游标存的是**排序键**不是下标 —— 可轮询集合每轮都在变,存下标会乱跳。

    (与 settlement_watcher.select_batch 同一条理由,那边已被 08-03 事故验证过。)
    """
    cursor = ""
    seen = set()
    for round_i in range(40):
        # 每轮集合都变:新市场进来、老市场退出
        markets = _markets(150)[round_i % 5:]
        picked, cursor, _ = cc.select_poll_targets(markets, wms={}, limit=50, cursor=cursor)
        seen |= set(_cids(picked))
        assert isinstance(cursor, str), "游标必须是可存进 JSON 的排序键"
    assert len(seen) > 100, f"集合变动下轮转卡住了(40 轮只覆盖 {len(seen)} 个)"


def test_no_duplicates_within_one_round():
    """新鲜片与轮转片重叠时不许重复采(白白浪费本就不够的名额)。"""
    markets = _markets(60)
    picked, _, _ = cc.select_poll_targets(markets, wms={}, limit=50, cursor="")
    cids = _cids(picked)
    assert len(cids) == len(set(cids)), "同一轮里有市场被采了两次"


# ============ 判据组 D:轮转片大小必须由实测分布支撑(不许拍脑袋) ============

def test_rotation_lap_keeps_p999_markets_under_the_offset_cap():
    """⭐把"一圈多久"与"多久会攒爆分页上限"的关系焊死。

    一圈 = pollable / rotate_share 轮,一轮 15 分钟。
    要求:p99.9 的市场在一圈之内累积成交 < OFFSET_CAP,否则轮转本身制造截断。
    调小 rotate_share 或调大 DEFAULT_POLL_LIMIT 而没重算的话,这条会红。
    """
    lap_rounds = MEASURED_POLLABLE_PER_CYCLE / cc.POLL_ROTATE_SHARE
    lap_hours = lap_rounds * CYCLE_MINUTES / 60
    accumulated = MEASURED_TRADES_PER_HOUR_P999 * lap_hours
    assert accumulated < cc.OFFSET_CAP, (
        f"一圈 {lap_hours:.1f} 小时,p99.9 市场会攒 {accumulated:,.0f} 笔 "
        f"> 分页上限 {cc.OFFSET_CAP} → 轮转会制造新的截断")


def test_p99_markets_have_generous_headroom():
    """绝大多数市场(p99 及以下)应有充裕余量,而不是刚好卡线。

    刚好卡线 = 成交量一涨就集体截断,那不是"配好了"是"运气好"。
    """
    lap_hours = (MEASURED_POLLABLE_PER_CYCLE / cc.POLL_ROTATE_SHARE) * CYCLE_MINUTES / 60
    accumulated = MEASURED_TRADES_PER_HOUR_P99 * lap_hours
    assert accumulated < cc.OFFSET_CAP / 4, (
        f"p99 市场一圈攒 {accumulated:,.0f} 笔,余量不足 4 倍")


def test_pure_rotation_would_have_been_wrong():
    """把"为什么不能纯轮转"焊进判据 —— 否则这个设计理由只活在注释里。

    实测最热 3,954 笔/小时;若把全部 50 个名额都给轮转,一圈 ≈ 5.1 小时,
    它会攒 ~20,000 笔 > OFFSET_CAP。这条红了说明实测常量被人改了,叙述已失真。
    """
    MEASURED_MAX_PER_HOUR = 3954
    lap_hours_if_pure = (MEASURED_POLLABLE_PER_CYCLE / 50) * CYCLE_MINUTES / 60
    assert MEASURED_MAX_PER_HOUR * lap_hours_if_pure > cc.OFFSET_CAP


# ============ 判据组 E:可见性(否则修了也不知道有没有好转) ============

def test_never_polled_count_is_reported():
    """「本轮可轮询的里面,有多少从来没采过」—— 这是衡量修复效果的那个量。

    没有它,"65% 从没采过"这件事修没修好,只能靠人手工去查数据湖。
    """
    markets = _markets(10)
    wms = {m["condition_id"]: 123 for m in markets[:4]}   # 只有 4 个有水位线
    counters = cc.new_counters()
    cc.count_never_polled(markets, wms, counters)
    assert counters["poll_never_polled_count"] == 6


def test_poll_markets_accumulates_skipped_instead_of_overwriting():
    """⭐硬名额砍掉的那批已经记在计数里了,`poll_markets` 只能**累加**。

    写成 `=` 就把它抹掉 —— 那正是改之前"没轮到 轮询恒 0"的成因(在截断之后才算)。
    这条补的是一个我差点漏掉的缺口:前面所有判据都只测选择器这个"零件",
    而 `=` 和 `+=` 的差别只在**装上车之后**才看得出来。
    """
    counters = cc.new_counters()
    counters["poll_budget_skipped_count"] = 950      # 选择器已记的硬名额砍掉数
    markets = _markets(3)
    cc.poll_markets(markets, counters, wms={}, time_budget_s=-1)   # 时间闸立刻咬
    assert counters["poll_budget_skipped_count"] == 953, (
        "时间闸跳过的没有累加到硬名额跳过的上面 —— 有一批被抹掉了")


def test_never_polled_is_measured_before_truncation(monkeypatch, tmp_path):
    """⭐分母必须是**截断前**的全部可轮询市场。

    在截断后统计 = 在问"被留下的那 50 个怎么样",而我们要问的恰恰是被砍掉的那些。
    这条走主干 `run_once`,因为对错只体现在**调用位置**上,单测函数本身测不出来。
    """
    pollable = [_m(i) for i in range(200)]
    monkeypatch.setattr(cc, "refresh_and_registry",
                        lambda **kw: (pollable, {"firehose_newest_ts": 1,
                                                 "new_discovered": 0,
                                                 "new_registered": 0,
                                                 "register_fail": 0}))
    monkeypatch.setattr(cc, "poll_market", lambda m, c, wm: [])
    monkeypatch.setattr(cc.se, "duckdb_conn", lambda: type("C", (), {"close": lambda s: None})())
    monkeypatch.setattr(cc.se, "all_watermarks", lambda con: {})   # 全都没采过
    monkeypatch.setattr(cc.se, "write_trades", lambda rows: None)
    monkeypatch.setattr(cc, "REGISTER_STREAK_FILE", tmp_path / "reg.json")
    monkeypatch.setattr(cc, "FIREHOSE_WM_FILE", tmp_path / "wm.json")
    monkeypatch.setattr(cc, "POLL_CURSOR_FILE", tmp_path / "cur.json")
    monkeypatch.setattr(cc.time, "sleep", lambda *_: None)

    counters = cc.run_once(limit=50)
    assert counters["poll_never_polled_count"] == 200, (
        f"报了 {counters['poll_never_polled_count']} —— 分母被截断成了本轮实采的那批")
    assert counters["poll_budget_skipped_count"] == 150, "硬名额砍掉的数没进心跳"


def test_timegate_skips_are_counted_separately_from_quota_skips():
    """⭐两种"没轮到"必须分得开 —— 处置方向不同。

    名额不够 = 结构性(1054 个可轮询 vs 每轮 50 个,要动架构);
    时间闸咬住 = 可变(冷启动全回填 ~11s/个,轮转片大多是从没采过的市场)。

    这条还有一个更实际的用途:**轮转片实际完成了几个**决定"一圈多久",
    而一圈多久正是 `test_rotation_lap_keeps_p999_markets_under_the_offset_cap`
    那条红线的自变量。合成一个数,那条红线就没法用真机数据复核。
    """
    counters = cc.new_counters()
    counters["poll_budget_skipped_count"] = 1000        # 名额不够砍掉的
    cc.poll_markets(_markets(3), counters, wms={}, time_budget_s=-1)
    assert counters["poll_timegate_skipped_count"] == 3, "时间闸砍掉的没单独计"
    assert counters["poll_budget_skipped_count"] == 1003, "总数仍要包含它"


def test_new_counters_reach_the_heartbeat():
    """只加 counter 不进心跳 = 进程一退就蒸发,等于没计。"""
    for k in ("poll_budget_skipped_count", "poll_never_polled_count",
              "poll_timegate_skipped_count"):
        assert k in cc.new_counters(), f"{k} 没进 COUNTER_KEYS"
        assert k in se.AUDIT_FIELDS, f"{k} 不落 parquet = 事后无从复核"


def test_run_once_actually_uses_the_selector():
    """⭐「零件合格 ≠ 装上车」:主干必须真的调用选择器,而不是还在裸切片。

    上一轮(08-05)和上上轮(08-04)都栽过同一个跟头:零件测得很细,
    组装那一下没有任何判据看着。把 `markets[:limit]` 留在原地的话,前面全部判据照样绿。
    """
    import inspect
    src = inspect.getsource(cc.run_once)
    assert "select_poll_targets" in src, "run_once 没用选择器"
    assert "markets[:limit]" not in src, "裸切片还在 —— 轮转没装上车"
