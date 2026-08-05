#!/usr/bin/env python3
"""判据焊死:firehose 必须覆盖到上一轮,盖不住必须出声(2026-08-04)。

## 由来(实测,非假想)

`sample_firehose(limit=5000)` 取最新 5000 笔全局成交作为"本轮活跃市场"的来源。
实测这 5000 笔只覆盖 **3.8 分钟**(13:25:47Z ~ 13:29:32Z)。

而采集间隔是 **15 分钟** → 发现层的**时间覆盖率只有 25%**,
其余 11.2 分钟里只在别处成交过的市场,本轮根本看不见。

⚠️ **这是我 2026-08-04 自己改出来的副作用**:那次为了"周期别被 systemd 杀"把间隔
从 10 分钟放宽到 15 分钟,覆盖率随之从 38% 掉到 25%,而当时**完全没意识到**
`OnCalendar` 同时是发现层的采样率。教训:
**一个参数同时是两件事的旋钮时,只会有一件被想到。**

## 这件事有多严重(诚实版,不许夸大)

**晚发现 ≠ 丢数据**:市场一旦注册,`poll_market` 首轮 `wm is None` 会从 offset=0
翻到底、补全历史。实测「注册前已累积成交笔数」p50=8 / p90=42 / max=8000,
**没有一个超过 `OFFSET_CAP=10000`** → 补得回来。

所以真损失只有一类:**一生中从未被任何一个采样窗撞上的市场**。
本判据焊的就是"别让这类存在,存在了也要出声",而不是"覆盖率越高越好"。

## 判据形状:为什么是"覆盖到上一轮",不是"把 limit 调大"

调大 limit 是**在错的框架内调参**(CLAUDE.md 阈值第 1 条):
成交量有日内节律、有大事件尖峰,任何写死的条数都会在某些时段盖不住。
正确的量是「**上一轮采到哪儿了**」——按 watermark 翻页到接上为止,
这样成交清淡时自动少翻(省时间),爆量时自动多翻(不留缺口),
翻不完则**出声**(而不是悄悄留个洞)。

## 本测试**不能**回答什么

不回答「有多少市场一生从未被撞上」。要量它得知道全宇宙的真实成交流,
而我们手上只有采样 —— **用采样去估计采样漏了什么,是循环论证**。
真要量得走另一条路(如按 Gamma 全量市场列表对账),那是独立的一件事。
本轮只保证:缺口可见、可计数、且稳态下为零。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import discovery_service as ds  # noqa: E402

# --- 实测常量(2026-08-04 真机)---
MEASURED_TRADES = 5000
MEASURED_WINDOW_MIN = 3.8          # 5000 笔覆盖 3.8 分钟
MEASURED_TRADES_PER_MIN = MEASURED_TRADES / MEASURED_WINDOW_MIN   # ≈ 1316(峰时段)
# 接口硬顶:offset > 10000 返回 HTTP 400
# {"error":"max historical trades offset of 10000 exceeded"} —— 故最多拿到 11000 笔
API_MAX_TRADES = 11000


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ds.time, "sleep", lambda *_: None)


def _page(newest_ts, n=1000, step=1):
    """一页 n 笔,时间戳从 newest_ts 递减(与真实接口一致:offset=0 是最新)。"""
    return [{"conditionId": f"0x{i:064x}", "slug": f"s{i}", "title": "t",
             "timestamp": newest_ts - i * step} for i in range(n)]


def _fake_api(pages, monkeypatch, net_key=None):
    """按 offset 顺序发页;发完返回空(= 确认翻到底)。"""
    calls = []

    def fake_get(url, tries=5, net=None):
        off = int(url.split("offset=")[1].split("&")[0])
        calls.append(off)
        idx = off // 1000
        return pages[idx] if idx < len(pages) else []

    monkeypatch.setattr(ds, "_get", fake_get)
    return calls


# ---------- 判据 1:覆盖到上一轮就停(不浪费时间) ----------

def test_stops_once_gap_is_covered(monkeypatch):
    """接上上一轮的 watermark 就该停 —— 清淡时段不该白翻 20 页。"""
    now = 1_000_000
    pages = [_page(now - i * 1000, n=1000) for i in range(10)]
    calls = _fake_api(pages, monkeypatch)
    net = {}
    # 上一轮最新成交在 1500 秒之前 → 翻到第 2 页(覆盖 0~2000s)即可接上
    ds.sample_firehose(limit=25000, since_ts=now - 1500, net=net)
    assert len(calls) <= 3, f"覆盖到 watermark 后仍在翻页(翻了 {len(calls)} 页)"
    assert net.get("firehose_gap_uncovered_count") == 0


def test_reports_the_covered_window(monkeypatch):
    """必须报出「本轮实际覆盖了多长时间」—— 这是 25% 覆盖率那个盲区的直接解药。"""
    now = 1_000_000
    pages = [_page(now - i * 1000, n=1000) for i in range(10)]
    _fake_api(pages, monkeypatch)
    net = {}
    ds.sample_firehose(limit=25000, since_ts=now - 1500, net=net)
    assert net.get("firehose_window_seconds", 0) > 0, "覆盖时长没被报出来"


# ---------- 判据 2:盖不住必须出声 ----------

def test_uncovered_gap_is_counted(monkeypatch):
    """⭐核心:翻到上限还没接上 = 这段时间的市场本轮看不见 → **必须出声**。

    旧代码这里是彻底沉默的:limit 翻完就 return,缺口无人知晓。
    """
    now = 1_000_000
    # 每页只往回 100 秒;limit=3000 只能翻 3 页 = 300 秒,而缺口要 5000 秒
    pages = [_page(now - i * 100, n=1000, step=1) for i in range(10)]
    _fake_api(pages, monkeypatch)
    net = {}
    ds.sample_firehose(limit=3000, since_ts=now - 5000, net=net)
    assert net.get("firehose_gap_uncovered_count") == 1, "留了缺口却一声不吭"


def test_no_gap_no_noise(monkeypatch):
    """防洪另一头:盖住了就必须**完全静默**,否则这个计数天天非零 = 没信号。"""
    now = 1_000_000
    pages = [_page(now - i * 1000, n=1000) for i in range(10)]
    _fake_api(pages, monkeypatch)
    net = {}
    ds.sample_firehose(limit=25000, since_ts=now - 500, net=net)
    assert net.get("firehose_gap_uncovered_count") == 0


def test_truncation_and_gap_are_different_counters(monkeypatch):
    """「网络断了翻不动」与「翻到上限还没接上」是两回事,处置不同,不许混为一谈。

    前者查隧道,后者加预算/降间隔。混在一个计数里 = 告警指错方向。
    """
    now = 1_000_000

    def fake_get(url, tries=5, net=None):
        return {"__http__": ds.RETRY_EXHAUSTED}

    monkeypatch.setattr(ds, "_get", fake_get)
    net = {}
    ds.sample_firehose(limit=25000, since_ts=now - 5000, net=net)
    assert net.get("firehose_truncated_count") == 1, "网络断了没计截断"


# ---------- 判据 3:时间闸(延迟退化时降级而不是崩) ----------

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_time_budget_stops_paging(monkeypatch):
    """代理慢一倍时,宁可少采一段并出声,也不能把整轮拖到被杀。"""
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)
    now = 1_000_000

    def slow_get(url, tries=5, net=None):
        clock.t += 10.0
        off = int(url.split("offset=")[1].split("&")[0])
        return _page(now - off // 10, n=1000, step=1)

    monkeypatch.setattr(ds, "_get", slow_get)
    net = {}
    ds.sample_firehose(limit=25000, since_ts=now - 10**6,
                       time_budget_s=50, net=net)
    assert net.get("firehose_gap_uncovered_count") == 1, "被时间闸截断却没出声"


# ---------- 判据 4:向后兼容 ----------

def test_without_since_ts_behaves_like_before(monkeypatch):
    """不传 watermark 时(冷启动/手工试跑)行为不变:翻满 limit 或翻到空。"""
    now = 1_000_000
    pages = [_page(now - i * 1000, n=1000) for i in range(3)]
    calls = _fake_api(pages, monkeypatch)
    out = ds.sample_firehose(limit=5000, net={})
    assert len(out) == 3000
    assert calls == [0, 1000, 2000, 3000]


# ---------- 判据 5:计数活到心跳 ----------

def test_coverage_counters_reach_the_heartbeat():
    import collector_core as cc
    import storage_engine as se
    for k in ("firehose_gap_uncovered_count", "firehose_window_seconds"):
        assert k in cc.new_counters(), f"{k} 没进 COUNTER_KEYS"
        assert k in se.AUDIT_FIELDS, f"{k} 不落 parquet = 等于没计"


# ---------- 判据 6:配额必须够得着实测成交率 ----------

def test_sample_limit_does_not_exceed_the_api_ceiling():
    """⭐接口硬顶:offset > 10000 一律 HTTP 400。

        {"error":"max historical trades offset of 10000 exceeded"}

    (2026-08-04 实测:offset 10000 正常返回,11000/12000/13000/14000 全 400。)
    配额设得比硬顶大 = 每轮白撞几个 400、白等几秒,还会把 4xx 计数刷出假信号。
    """
    import run_cycle as rc
    assert rc.DEFAULT_SAMPLE <= API_MAX_TRADES, (
        f"采样上限 {rc.DEFAULT_SAMPLE} > 接口硬顶 {API_MAX_TRADES} → 必然撞 400")


def test_api_ceiling_is_not_mistaken_for_end_of_data(monkeypatch):
    """⭐⭐最要命的一条:撞到 offset 硬顶(400)**不是**「翻到底」。

    `_get` 对 4xx 返回 `{"__http__": 400}`,而 `not isinstance(d, list)` 会把它
    和"确认空"归成一类 → `covered=True` → 缺口计数变 0 → **有洞而心跳全绿**。
    与 08-03 结算断供、08-04 网络零计数同形,是同一个病的第三次发作。

    配额设对时(`DEFAULT_SAMPLE=11000`)本来碰不到硬顶,故这条护的是**配置调过头**的情形
    —— 那正是没人会再回来验证的时刻。用 limit=13000 复现。
    """
    now = 1_000_000

    def ceiling_get(url, tries=5, net=None):
        off = int(url.split("offset=")[1].split("&")[0])
        if off > 10000:
            return {"__http__": 400}
        return _page(now - off // 10, n=1000, step=1)

    monkeypatch.setattr(ds, "_get", ceiling_get)
    net = {}
    ds.sample_firehose(limit=13000, since_ts=now - 10**6, net=net)
    assert net.get("firehose_gap_uncovered_count") == 1, "撞硬顶被当成了翻到底 → 缺口静默"
    assert net.get("firehose_offset_ceiling_count") == 1, "硬顶要与其它停法分开计(处置不同)"


def test_gap_is_reported_in_seconds_not_just_a_flag(monkeypatch):
    """缺口要报**多长**,不是只报"有"。

    只有布尔量时无法回答"这周比上周严重了吗" —— 而阈值校准要的正是分布。
    """
    now = 1_000_000
    pages = [_page(now - i * 100, n=1000, step=1) for i in range(10)]
    _fake_api(pages, monkeypatch)
    net = {}
    ds.sample_firehose(limit=3000, since_ts=now - 5000, net=net)
    assert net.get("firehose_gap_seconds", 0) > 0, "没报缺口时长"


def test_known_structural_gap_is_written_down_and_reconciled():
    """⭐硬约束必须**写下来并与配置对账**,不许某天悄悄变大而无人发现。

    接口只够回溯 ~8.4 分钟(11000 笔 ÷ 实测峰值 1316 笔/分钟),而采集间隔 15 分钟
    → **每轮结构性漏掉 ~6.6 分钟**,这不是 bug,是接口能力的上限。

    但它必须是**明写的已知量**:任何人改间隔或改采样配额时,这条会红,
    逼他重新面对"漏多久"这个问题,而不是让缺口悄悄从 6.6 分钟涨到 20 分钟。
    """
    import alerts
    import run_cycle as rc
    reach_min = API_MAX_TRADES / MEASURED_TRADES_PER_MIN
    gap = alerts.cycle_minutes() - reach_min
    assert abs(gap - rc.KNOWN_FIREHOSE_GAP_MIN) < 0.5, (
        f"实际结构缺口 {gap:.1f} 分钟,而 run_cycle.KNOWN_FIREHOSE_GAP_MIN "
        f"写的是 {rc.KNOWN_FIREHOSE_GAP_MIN} —— 改了间隔/配额却没更新这个已知量")


def test_measured_coverage_of_old_setting_was_the_bug():
    """把"当初错在哪"焊进判据:旧的 5000 条上限在 15 分钟间隔下只盖 25%。

    这条不是装饰 —— 它让任何人把 limit 改回 5000 时,能立刻看到这是已知的坏配置。
    """
    old_coverage = MEASURED_WINDOW_MIN / 15
    assert old_coverage < 0.30, "实测常量被改动了,本文件的由来叙述已失真"
