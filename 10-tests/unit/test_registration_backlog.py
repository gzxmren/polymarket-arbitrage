#!/usr/bin/env python3
"""判据焊死:注册预算跳过的市场必须**留下名单**,不许靠"它再成交一次"来回来(2026-08-05)。

## 由来(code review 挖出,非假想)

`register_new_markets` 有预算闸(个数 + 时间),超了就停 —— 这是对的,是降级不是故障。
错的是**跳过的 cid 没有任何地方持久化**:

1. `refresh_and_registry` 的候选集 `new` 完全来自本轮 firehose 窗口(`active - registry`)
2. `run_once` 只要采到东西就**推进 watermark**(collector_core.py,「采空不许推进」那段)
3. 下一轮 `active` 来自**新窗口** → 上一轮被跳过的 cid 不在里面

⇒ 它**只有再成交一次**才会重新出现。否则永久消失。

## 为什么这是致命而不是"延迟高一点"

因为**丢得与结果相关**(CLAUDE.md 铁律 2 / 静默失败清单第 3 条):

- `items` 按 firehose 新鲜度排序 → **低频盘天然排在后面,先被砍**
- 低频盘又恰好**最不可能**在接下来一个窗口内再成交一次

两头指向同一批人。丢样本本身不致命(随机缺失 = 噪声),**丢得与结果相关才致命** ——
采到的成交流会系统性偏向高频市场,而任何基于它的结论都结构性作废。

对照:`settlement_watcher` 对同一个「每轮取前 N 个」问题解对了(真游标 + 回卷),
注册层和轮询层没有。本判据只管**注册层**;轮询层是同一形态,另立项。

## 为什么不能照抄 settlement_watcher 的游标

那边的 `pending` 是**持久的全量列表**(注册表里所有未结算市场),游标存排序键即可。
这边的候选集是**易逝的**:watermark 一推进,那个 stub(slug/title)就再也拿不到了。
所以必须持久化**积压本身**,不是游标。

## 判据设计:为什么排序键是 (attempts, first_seen, cid)

- `attempts` 在前 → **反复失败的自动沉底**。否则一旦队头堆满"永远注册不上"的东西
  (预算 100/轮,队头 100 个死号),新市场**永远轮不到** —— 那是我亲手造出一个更糟的饿死
- `first_seen` 次之 → 同等尝试次数下**先来先服务**,这是"不饿死"的正面保证
- `cid` 兜底 → 全序,结果可复现(不依赖 dict 遍历顺序)

⭐**被预算跳过 ≠ 尝试失败**:跳过的 `attempts` 不许 +1(它压根没发出去查询)。
否则"忙了几轮"会被当成"这东西有问题"而沉底 —— 那正是要修的病换个形式复发。

## 本判据**不能**回答什么

- 不回答 `MAX_ATTEMPTS` / `MAX_BACKLOG` 该定多少(要实测分布,并入 ⏰2026-08-11 阈值校准)。
  这里只断言:**存在上限、超了要出声**。
- 不回答"低频市场是否真的更难被采到"(要知道全宇宙真实成交流,用采样估计采样漏了什么
  是循环论证)。但本次落地的 `pending_registration_*` 计数给了它第一个**可实测的抓手**。
- 不回答轮询层的同类饿死(`poll_markets` 每轮取前 N 个),那是另一件事。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import registration_backlog as rb  # noqa: E402


def _stub(i):
    return {"slug": f"m-{i}", "title": f"Market {i}"}


def _fresh(n, start=0):
    return {f"0x{i:064x}": _stub(i) for i in range(start, start + n)}


def _cid(i):
    return f"0x{i:064x}"


# ---------- 判据组 A:积压持久化(核心 —— 修的就是这个) ----------

def test_skipped_cid_survives_into_backlog():
    """⭐核心:被预算跳过的 cid 必须留在积压里,不是消失。"""
    now = 1_000
    bl = rb.merge({}, _fresh(5), now)
    ordered = rb.order(bl)
    attempted = list(ordered)[:2]          # 预算只够 2 个
    bl = rb.settle(bl, attempted=attempted, registered=attempted, now=now, counts={})
    left = set(bl)
    assert left == {_cid(i) for i in (2, 3, 4)}, f"被跳过的没留下:{left}"


def test_backlog_cid_returns_even_if_it_never_trades_again():
    """⭐这条正对准 bug 本体:下一轮该 cid **不在 firehose 里**,也必须进候选集。

    旧行为下它只有"再成交一次"才回得来 —— 而最可能不再成交的,正是最该被登记的冷门盘。
    """
    now = 1_000
    bl = rb.merge({}, _fresh(3), now)
    bl = rb.settle(bl, attempted=[_cid(0)], registered=[_cid(0)], now=now, counts={})
    # 下一轮 firehose 里只有一个**全新**市场,老的两个再没成交
    bl2 = rb.merge(bl, {_cid(99): _stub(99)}, now + 900)
    cands = rb.order(bl2)
    assert _cid(1) in cands and _cid(2) in cands, "上轮被跳过的没回到候选集 → 仍然是永久丢"
    assert cands[_cid(1)]["slug"] == "m-1", "stub 内容没被持久化 → 回来了也注册不了"


def test_registered_cid_is_removed():
    """注册成功必须移出积压,否则积压只涨不消。"""
    bl = rb.merge({}, _fresh(3), 1_000)
    bl = rb.settle(bl, attempted=list(_fresh(3)), registered=[_cid(0), _cid(1)],
                   now=1_000, counts={})
    assert set(bl) == {_cid(2)}


def test_merge_keeps_original_first_seen():
    """同一个 cid 再次出现在 firehose 里,不许刷新 first_seen —— 否则它永远排在队尾。

    这是「先来先服务」会被悄悄架空的方式:高频盘每轮都重新出现,若每次都刷新时间戳,
    冷门盘反而被它们不断插队。
    """
    bl = rb.merge({}, {_cid(1): _stub(1)}, 1_000)
    bl = rb.merge(bl, {_cid(1): _stub(1)}, 5_000)
    assert bl[_cid(1)]["first_seen"] == 1_000


def test_merge_keeps_attempts():
    """重新出现不许把 attempts 清零 —— 否则死号永远沉不下去,反复占队头。"""
    bl = rb.merge({}, {_cid(1): _stub(1)}, 1_000)
    bl = rb.settle(bl, attempted=[_cid(1)], registered=[], now=1_000, counts={})
    bl = rb.merge(bl, {_cid(1): _stub(1)}, 2_000)
    assert bl[_cid(1)]["attempts"] == 1


# ---------- 判据组 B:公平性 / 可执行的「不饿死」证明 ----------

def test_waiting_beats_brand_new():
    """等过一轮的排在本轮新发现的前面(同为 attempts=0 时按 first_seen)。"""
    bl = rb.merge({}, _fresh(2), 1_000)              # 老的两个
    bl = rb.merge(bl, {_cid(50): _stub(50)}, 2_000)  # 新来的一个
    assert list(rb.order(bl))[0] == _cid(0)
    assert list(rb.order(bl))[-1] == _cid(50), "新来的插到了等待者前面 → 冷门盘会被持续插队"


def test_repeated_failures_sink_below_fresh():
    """⭐反复失败的必须沉到新市场**下面**。

    否则队头一旦堆满"永远注册不上"的东西,预算全喂给它们,新市场永远轮不到 ——
    那是我用一个更糟的饿死替换掉原来的饿死。
    """
    bl = rb.merge({}, {_cid(1): _stub(1)}, 1_000)
    for _ in range(3):
        bl = rb.settle(bl, attempted=[_cid(1)], registered=[], now=1_000, counts={})
        bl = rb.merge(bl, {}, 1_000)
    bl = rb.merge(bl, {_cid(9): _stub(9)}, 9_000)   # 后来的新市场
    assert list(rb.order(bl))[0] == _cid(9), "老失败户仍占着队头 → 新市场被堵死"


def test_skipped_is_not_counted_as_a_failed_attempt():
    """⭐被预算跳过 ≠ 尝试失败。没发出去的查询不许算它头上。

    否则"系统忙了几轮"会被当成"这东西有问题"而沉底 —— 要修的病换个形式复发。
    """
    bl = rb.merge({}, _fresh(3), 1_000)
    bl = rb.settle(bl, attempted=[_cid(0)], registered=[], now=1_000, counts={})
    assert bl[_cid(0)]["attempts"] == 1, "真尝试过的没记上"
    assert bl[_cid(1)]["attempts"] == 0, "没轮到的被记成失败了"
    assert bl[_cid(2)]["attempts"] == 0


def test_nobody_starves_under_sustained_overload():
    """⭐⭐可执行的「不饿死」证明:涌入长期大于预算,每个市场仍必须在有限轮内被试到。

    这条才是本次改动的**验收核心**。前面那些是机制,这条是**结果**。
    模拟:每轮涌入 30 个新市场,预算只有 10 个/轮,跑 40 轮。
    断言:第 1 轮出现的那批,全部在有限轮内被尝试过 —— 且不依赖它们是否再次成交。
    """
    BUDGET, INFLOW, ROUNDS = 10, 30, 40
    bl, now = {}, 1_000
    first_batch = set(_fresh(INFLOW))
    ever_attempted, rounds_to_cover = set(), None

    for r in range(ROUNDS):
        # 第 1 轮之后,老市场**再也不出现在 firehose 里**(最恶劣、也最真实的情形)
        fresh = _fresh(INFLOW, start=r * INFLOW)
        bl = rb.merge(bl, fresh, now)
        attempted = list(rb.order(bl))[:BUDGET]
        ever_attempted |= set(attempted)
        # 全部注册成功 —— 把"失败重试"这个变量排除掉,单测饿死这一件事
        bl = rb.settle(bl, attempted=attempted, registered=attempted, now=now, counts={})
        now += 900
        if rounds_to_cover is None and first_batch <= ever_attempted:
            rounds_to_cover = r + 1

    assert rounds_to_cover is not None, (
        f"跑满 {ROUNDS} 轮,第一批 {INFLOW} 个里仍有 "
        f"{len(first_batch - ever_attempted)} 个从没被尝试过 → 饿死没修好")
    # 30 个 / 每轮 10 个 = 至少 3 轮;放宽到 5 轮以内,松了就抓不住"轻微插队"
    assert rounds_to_cover <= 5, f"第一批花了 {rounds_to_cover} 轮才轮完,存在插队"


def test_order_is_deterministic():
    """同样的积压必须给出同样的顺序 —— 否则"谁先谁后"随 dict 遍历顺序漂,不可复现。"""
    bl = rb.merge({}, _fresh(20), 1_000)
    assert list(rb.order(bl)) == list(rb.order(dict(reversed(list(bl.items())))))


# ---------- 判据组 C:出声计数(静默失败清单第 3 条) ----------

def test_backlog_size_is_counted():
    counts = {}
    bl = rb.merge({}, _fresh(7), 1_000)
    rb.settle(bl, attempted=[_cid(0)], registered=[_cid(0)], now=1_000, counts=counts)
    assert counts.get("pending_registration_count") == 6


def test_oldest_age_is_counted():
    """最老条目的年龄 —— 「积压在涨」和「积压卡住了」是两种不同的病,只看条数分不出来。"""
    counts = {}
    bl = rb.merge({}, _fresh(2), 1_000)
    rb.settle(bl, attempted=[], registered=[], now=1_000 + 3600, counts=counts)
    assert counts.get("pending_registration_oldest_age_s") == 3600


def test_oldest_age_is_zero_when_empty():
    """空积压的"最老年龄"必须是 0,不许是 None/上一轮残值 —— 否则告警会看着一个假数字。"""
    counts = {}
    rb.settle({}, attempted=[], registered=[], now=9_999, counts=counts)
    assert counts.get("pending_registration_oldest_age_s") == 0
    assert counts.get("pending_registration_count") == 0


def test_gauges_do_not_accumulate_but_the_counter_does():
    """⭐「积压条数」「最老年龄」是**仪表读数**(当前是多少),不是计数器(发生了几次)。

    仪表若写成累加,哪天 settle 在一轮里被调用两次,数字就凭空翻倍 ——
    而它看起来**完全正常**(仍是个合理的正整数),没有任何东西会变红。
    对照:`dropped_count` 是真计数器,一轮里丢两批就该累计。
    """
    counts = {}
    bl = rb.merge({}, _fresh(4), 1_000)
    rb.settle(bl, attempted=[], registered=[], now=2_000, counts=counts)
    rb.settle(bl, attempted=[], registered=[], now=2_000, counts=counts)
    assert counts["pending_registration_count"] == 4, "仪表被累加成了 8"
    assert counts["pending_registration_oldest_age_s"] == 1_000, "年龄被累加了"

    c2 = {}
    over = rb.merge({}, _fresh(1), 1_000)
    for _ in range(rb.MAX_ATTEMPTS + 1):
        over = rb.settle(over, attempted=[_cid(0)], registered=[], now=1_000, counts=c2)
    d1 = c2["pending_registration_dropped_count"]
    over2 = rb.merge({}, _fresh(1, start=77), 1_000)
    for _ in range(rb.MAX_ATTEMPTS + 1):
        over2 = rb.settle(over2, attempted=[_cid(77)], registered=[], now=1_000, counts=c2)
    assert c2["pending_registration_dropped_count"] == d1 + 1, "真计数器反而没累加"


def test_dropping_over_max_attempts_is_loud():
    """⭐超尝试上限而丢弃,必须出声。静默丢弃 = 用新的静默失败替换旧的静默失败。"""
    counts = {}
    bl = rb.merge({}, {_cid(1): _stub(1)}, 1_000)
    for _ in range(rb.MAX_ATTEMPTS + 2):
        bl = rb.settle(bl, attempted=[_cid(1)], registered=[], now=1_000, counts=counts)
    assert _cid(1) not in bl, "死号永远丢不掉 → 积压里堆着永远注册不上的东西"
    assert counts.get("pending_registration_dropped_count") == 1


def test_drop_is_also_loud_in_the_log_but_silent_in_steady_state(capsys):
    """丢弃要在**日志**里也出声(人每天看的是日志,不是 parquet);但稳态必须完全静默。

    新增任何"出声"都得两头焊死(CLAUDE.md 第 7 条):真异常必推 + 稳态零噪音 ——
    降噪不是体验优化,噪音会让真信号无处可显。
    """
    bl = rb.merge({}, _fresh(3), 1_000)
    rb.settle(bl, attempted=[_cid(0)], registered=[_cid(0)], now=1_000, counts={})
    assert capsys.readouterr().out == "", "稳态(没丢东西)也在打日志 → 噪音"

    dead = rb.merge({}, {_cid(1): _stub(1)}, 1_000)
    for _ in range(rb.MAX_ATTEMPTS + 1):
        dead = rb.settle(dead, attempted=[_cid(1)], registered=[], now=1_000, counts={})
    assert "注册积压" in capsys.readouterr().out, "真丢了东西却一声不吭"


def test_counters_reach_the_heartbeat():
    """进程一退就蒸发的计数 = 没有计数。一周后校准阈值时只能 grep 日志文本。"""
    import collector_core as cc
    import storage_engine as se
    for k in ("pending_registration_count", "pending_registration_dropped_count",
              "pending_registration_oldest_age_s"):
        assert k in cc.new_counters(), f"{k} 没进 COUNTER_KEYS → 漏计无人知"
        assert k in se.AUDIT_FIELDS, f"{k} 没进 AUDIT_FIELDS → 不落 parquet"


# ---------- 判据组 D:健壮性 ----------

def test_backlog_has_a_hard_cap_and_says_so():
    """积压必须有硬上限(磁盘/内存),超了要出声丢 —— 但丢的是**沉底的**,不是队头的。"""
    counts = {}
    bl = rb.merge({}, _fresh(rb.MAX_BACKLOG + 50), 1_000)
    bl = rb.settle(bl, attempted=[], registered=[], now=1_000, counts=counts)
    assert len(bl) == rb.MAX_BACKLOG
    assert counts.get("pending_registration_dropped_count") == 50
    kept = rb.order(bl)
    assert _cid(0) in kept, "丢的是队头(等最久的)→ 正好丢掉最该救的那批"


def test_corrupt_file_degrades_to_empty(tmp_path):
    """文件损坏降级为空,不许崩 —— 与 cycle_state 现有约定一致(状态丢失只影响节奏)。"""
    p = tmp_path / "backlog.json"
    p.write_text("{not json")
    assert rb.load(p) == {}


def test_missing_file_is_empty(tmp_path):
    assert rb.load(tmp_path / "nope.json") == {}


def test_roundtrip_survives_a_process_restart(tmp_path):
    """⭐落盘再读回必须一模一样 —— 这是"跨周期"三个字的全部含义。

    每轮是独立进程:内存里的积压不落盘 = 等于没有积压 = bug 原样存在。
    """
    p = tmp_path / "backlog.json"
    bl = rb.merge({}, _fresh(3), 1_000)
    bl = rb.settle(bl, attempted=[_cid(0)], registered=[], now=1_000, counts={})
    rb.save(p, bl)
    assert rb.load(p) == bl


def test_save_is_atomic(tmp_path):
    """原子落位:不许被下一轮读到半截文件(临时文件 + os.replace)。"""
    p = tmp_path / "backlog.json"
    rb.save(p, rb.merge({}, _fresh(3), 1_000))
    assert json.loads(p.read_text())          # 可解析
    assert not list(tmp_path.glob("*.tmp")), "留下了临时文件 → 不是原子写"


def test_garbage_entries_are_dropped_on_load(tmp_path):
    """外部写坏的单条记录不许污染整份积压(缺字段/类型错)。"""
    p = tmp_path / "backlog.json"
    p.write_text(json.dumps({
        _cid(1): {"slug": "ok", "title": "t", "first_seen": 1, "attempts": 0},
        _cid(2): "not-a-dict",
        _cid(3): {"slug": "x"},                       # 缺 first_seen/attempts
    }))
    assert set(rb.load(p)) == {_cid(1)}


# ---------- 判据组 E:回归 —— 预算够用时行为不变 ----------

def test_no_behaviour_change_when_budget_is_ample():
    """预算充足(没人被跳过)时,积压恒为空 —— 本次改动对健康稳态**零影响**。

    这条是"我没有顺手改坏正常路径"的可核查证明(现网实测 skipped 恒为 0,
    即当前就跑在这条路径上)。
    """
    counts = {}
    bl = rb.merge({}, _fresh(20), 1_000)
    allc = list(rb.order(bl))
    bl = rb.settle(bl, attempted=allc, registered=allc, now=1_000, counts=counts)
    assert bl == {}
    assert counts["pending_registration_count"] == 0
    assert counts["pending_registration_dropped_count"] == 0


# ---------- 判据组 F:主干真的用上了(⭐否则前面全是"零件合格但没装上车") ----------

def _wire(monkeypatch, ds, registry: dict, firehose):
    """把 refresh_and_registry 的外部依赖换成假的,只留积压逻辑真跑。"""
    monkeypatch.setattr(ds, "sample_firehose", lambda *a, **k: firehose())
    monkeypatch.setattr(ds, "load_registry", lambda *a, **k: dict(registry))
    monkeypatch.setattr(ds, "newest_trade_ts", lambda *a, **k: 1)
    monkeypatch.setattr(ds, "_atomic_write_parquet", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **k: {"slug": slug})
    monkeypatch.setattr(ds.time, "sleep", lambda *_: None)

    def fake_parse(m):
        # slug "m-7" → cid 7,好让"注册成功的那个"能对回 registry
        i = int(m["slug"].split("-")[1])
        registry[_cid(i)] = {"condition_id": _cid(i), "closed": False,
                             "market_class": "event"}
        return {"condition_id": _cid(i)}

    monkeypatch.setattr(ds, "parse_market", fake_parse)


def test_refresh_and_registry_persists_the_backlog(tmp_path, monkeypatch):
    """⭐⭐端到端:主干必须**真的落盘**积压,不是只有模块自己会存。

    这条焊死的是「零件都合格,但没装到车上」—— 上一轮就栽过一模一样的跟头
    (run_once 重构后残留 NameError,212 条单测全绿、真机跑完一整轮才炸,
     因为**没有任何一条测试调用过主干**)。
    把 refresh_and_registry 里的 `rb.save(...)` 删掉,前面 20 多条判据**照样全绿**,
    而 bug 原封不动 —— 只有这条会红。
    """
    import discovery_service as ds
    p = tmp_path / "backlog.json"
    registry = {}
    trades = [{"conditionId": _cid(i), "slug": f"m-{i}", "title": f"M{i}"} for i in range(5)]
    _wire(monkeypatch, ds, registry, lambda: trades)

    ds.refresh_and_registry(max_new=2, net={}, backlog_file=p)

    assert p.exists(), "积压根本没落盘 → 下一个周期(新进程)读不到 = bug 原样存在"
    saved = rb.load(p)
    assert len(saved) == 3, f"应剩 3 个没轮到的,实际 {sorted(saved)}"


def test_skipped_market_gets_registered_next_round_without_trading_again(tmp_path, monkeypatch):
    """⭐⭐⭐**这条就是 bug 本体的验收**:被跳过的市场,下一轮不再成交也必须能登记上。

    第 1 轮:发现 5 个,预算只有 2 个 → 3 个被跳过
    第 2 轮:firehose **完全空**(那 3 个再也没成交过)
    旧行为:它们永远不会被登记 —— 而这正是低频盘的典型命运
    新行为:从积压里取出来,登记上
    """
    import discovery_service as ds
    p = tmp_path / "backlog.json"
    registry = {}
    trades = [{"conditionId": _cid(i), "slug": f"m-{i}", "title": f"M{i}"} for i in range(5)]
    rounds = iter([trades, []])          # 第 2 轮 firehose 空
    _wire(monkeypatch, ds, registry, lambda: next(rounds))

    ds.refresh_and_registry(max_new=2, net={}, backlog_file=p)
    after_r1 = set(registry)
    assert len(after_r1) == 2, "第 1 轮预算闸没生效,这条测不到饿死"

    net2: dict = {}
    ds.refresh_and_registry(max_new=2, net=net2, backlog_file=p)
    newly = set(registry) - after_r1
    assert newly, "第 2 轮一个都没登记上 → 被跳过的市场仍然只能靠'再成交一次'回来"
    assert net2["pending_registration_count"] == 1, "积压没在消,可能是每轮在处理同一批"


def test_backlog_counters_reach_run_once(monkeypatch):
    """三个新计数必须真的从发现层传到心跳,不是只在 registration_backlog 内部转一圈。"""
    import collector_core as cc
    counts = cc.new_counters()

    def fake_refresh(*a, net=None, **k):
        net.update({"pending_registration_count": 7,
                    "pending_registration_dropped_count": 2,
                    "pending_registration_oldest_age_s": 1800})
        return [], {"firehose_newest_ts": 0}

    def fake_poll(markets, counters, wms, **k):
        counters["new_trades"] = 0
        return 0

    monkeypatch.setattr(cc, "refresh_and_registry", fake_refresh)
    monkeypatch.setattr(cc, "poll_markets", fake_poll)
    monkeypatch.setattr(cc.se, "all_watermarks", lambda *a, **k: {})
    monkeypatch.setattr(cc.se, "duckdb_conn", lambda *a, **k: _NullCon())
    monkeypatch.setattr(cc.cycle_state, "read_state", lambda *a, **k: None)
    monkeypatch.setattr(cc.cycle_state, "write_state", lambda *a, **k: None)
    out = cc.run_once(limit=1, sample=1, max_new=1)
    assert out["pending_registration_count"] == 7
    assert out["pending_registration_dropped_count"] == 2
    assert out["pending_registration_oldest_age_s"] == 1800


class _NullCon:
    def close(self):
        pass


def test_parlays_never_enter_the_backlog():
    """串关在上游(extract_active)就被剔了,不许从别的口子漏进积压来占名额。"""
    import discovery_service as ds
    parlay = "0x0379047733ac11e756e1408b9c4380ca020000000000000000000000000000"
    active = ds.extract_active([{"conditionId": parlay, "slug": None, "title": "A AND B"}])
    assert parlay not in rb.merge({}, active, 1_000)
