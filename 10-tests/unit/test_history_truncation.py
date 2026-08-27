#!/usr/bin/env python3
"""判据焊死:offset 截断造成的历史空洞必须**留痕、且分清哪一半可修**(2026-08-06)。

## 病(实测)

`poll_market` 分页翻到接口硬顶(`OFFSET_CAP=10000`)时,保留已抓的近端成交并写盘,
日志打一句「更早历史待压频回填」。

但**水位线是 `max(timestamp)`**(见 `storage_engine.all_watermarks`)。近端一写进去,
水位线就跳到最新那笔;下一轮从 offset=0 开始、一撞水位线立刻停 —— **永远不会往回翻**。
而那句「待压频回填」承诺的东西**根本不存在**:`backfill_closed_markets` 只捞
"一笔都没有"的市场(`t.condition_id is null`),有部分数据的它够不着。

实测(日志逐个点名的 377 个市场 vs 数据湖):

| | 撞过硬顶的 377 个 | 对照:没撞过、也有 ≥1000 笔的 2,075 个 |
|---|---|---|
| 序列开头的空白 | **p50 199 天 / p90 327 天 / 最大 380 天** | **p50 1 天 / p90 6 天** |

377 个里 347 个空白 > 30 天。**丢得与结果相关**:专丢成交最密的那批市场的最早那段。

## 这个修复**不是**"别写了"

不写会更坏:市场停在"从没采过",下一轮再翻一遍、再撞顶,永远拿不到任何数据,
还每轮白烧 20 页请求。数据丢失本身是**接口硬约束**(offset 顶 10000),修不掉。

**能修的是"它是静默的"** —— 一个开头缺 199 天的序列,现在长得和"刚开盘的新市场"
一模一样,任何用到早期历史的分析都会悄悄算错。故:

1. **留痕**:截断当场写一条记录进 `truncations/`,把空洞边界(保留到哪笔、原水位线在哪)
   变成可查询的事实。
2. **分清两半** —— 这是本文件最要紧的一条:
   - **冷启动截断**(`wm is None`):首次全量回填就超过 10,000 笔。**接口硬约束,修不掉**。
   - **增量截断**(`wm` 存在却没追上):两次轮询之间攒了 >10,000 笔。
     ⭐**这一半是可修的** —— 它等价于「轮转一圈太久」,正是 `test_poll_rotation.py`
     那条红线(一圈须短于 `OFFSET_CAP / p99.9 成交率 ≈ 11.6 小时`)被踩穿的现场证据。
   合成一个计数 = **把可修的那一半藏在不可修的那一半后面**。

## 顺带纠正一处写在代码里的假信念

`alerts.py` 原文写着 offset 截断是「**稳态自愈事件**(近端已保留 + 下轮压频回填)」,
并据此把稳态截断降级为不告警。**前半句是假的**(不自愈,是永久空洞),
后半句承诺的回填不存在。阈值调在一个错的框架里 = CLAUDE.md 第 4 条
「先问这个量本身该不该被监控」那一条的原样重现。

## 本判据**不能**回答什么

不回答"那 377 个能不能补回来"(接口 offset 硬顶 ⇒ 大概率不能),
也不回答增量截断现在有多频繁(合成计数下无从分辨,拆开之后要攒一周,并入 ⏰08-11 校准)。
"""
import sys
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import collector_core as cc  # noqa: E402
import storage_engine as se  # noqa: E402

TOK0 = "0x" + "a" * 40
TOK1 = "0x" + "b" * 40
CID = "0x" + "c" * 64


def _market():
    return {"condition_id": CID, "token_id_0": TOK0, "token_id_1": TOK1}


def _page(offset, base_ts=2_000_000_000):
    """一整页"永远还有更早的"成交(时间戳随 offset 单调递减 = 越翻越旧)。"""
    return [{"transactionHash": f"0x{offset + i:x}", "proxyWallet": "0xw",
             "asset": TOK0, "outcome": "Yes", "side": "BUY",
             "size": "1", "price": "0.5",
             "timestamp": base_ts - offset - i}
            for i in range(cc.PAGE)]


@pytest.fixture
def endless(monkeypatch):
    """接口永远还有下一页 → 必然翻到 OFFSET_WARN 触发截断。"""
    seen = []

    def fake_get(url, counters=None, **kw):
        off = int(url.split("offset=")[1])
        seen.append(off)
        return _page(off)

    monkeypatch.setattr(cc, "_get", fake_get)
    return seen


@pytest.fixture
def one_page(monkeypatch):
    """接口只有一页 → 正常翻到底,绝不该产生任何截断痕迹。"""
    monkeypatch.setattr(cc, "_get", lambda url, counters=None, **kw: _page(0)[:3])


# ---------- 1. 截断必须留痕,且带得出空洞边界 ----------

def test_cold_truncation_is_recorded_with_the_gap_boundary(endless):
    """首次全量回填撞顶 → 记一条,且**说得出空洞从哪儿开始**。

    只记"发生过截断"不够:分析层要知道这个序列的开头是**人为切口**还是市场真的没成交,
    那要靠"我们保留到的最旧一笔"这个边界。
    """
    counters = cc.new_counters()
    rows = cc.poll_market(_market(), counters, wm=None)
    recs = counters.get("truncations") or []
    assert len(recs) == 1, f"撞顶却没留痕:{recs}"
    r = recs[0]
    assert r["condition_id"] == CID
    assert r["mode"] == "cold"
    assert r["offset_reached"] >= cc.OFFSET_WARN
    assert r["kept_rows"] == len(rows) > 0
    assert r["oldest_kept_ts"] == rows[-1]["timestamp"], \
        "空洞边界必须是**保留到的最旧一笔**(分页是新→旧,故取末尾)"
    assert r["watermark_before"] is None


def test_warm_truncation_is_recorded_separately(endless):
    """有水位线却没追上 = 两次轮询之间攒爆了。空洞在**序列中间**,比冷启动那种更坏。"""
    counters = cc.new_counters()
    cc.poll_market(_market(), counters, wm=1)      # 极旧的水位线,永远追不上
    r = (counters.get("truncations") or [{}])[0]
    assert r.get("mode") == "warm"
    assert r.get("watermark_before") == 1, "原水位线必须记下来 —— 它是空洞的另一端"


# ---------- 2. ⭐两半必须分开数 ----------

def test_cold_and_warm_are_counted_separately(endless):
    """⭐合成一个计数 = 把**可修的那一半**藏在不可修的那一半后面。

    冷启动截断是接口硬约束(修不掉);增量截断是"轮转一圈太久"(可修,
    且是 test_poll_rotation.py 那条红线被踩穿的现场证据)。
    处置完全不同的两件事共用一个数字 = 这个数字对任何一件都没有分辨力。
    """
    cold, warm = cc.new_counters(), cc.new_counters()
    cc.poll_market(_market(), cold, wm=None)
    cc.poll_market(_market(), warm, wm=1)

    assert (cold["offset_overflow_cold_count"], cold["offset_overflow_warm_count"]) == (1, 0)
    assert (warm["offset_overflow_cold_count"], warm["offset_overflow_warm_count"]) == (0, 1)


def test_total_counter_still_sums_both(endless):
    """总数保持原语义:心跳/告警/回填清扫都还在读它,不许悄悄改口径。"""
    for wm in (None, 1):
        c = cc.new_counters()
        cc.poll_market(_market(), c, wm=wm)
        assert c["offset_overflow_count"] == (
            c["offset_overflow_cold_count"] + c["offset_overflow_warm_count"]) == 1


# ---------- 3. 回归:仍然要写盘 ----------

def test_rows_are_still_kept_on_truncation(endless):
    """⭐「别写了」是错的修法,必须钉死不许有人这么改。

    不写 → 市场停在"从没采过" → 下轮再翻一遍再撞顶 → **永远拿不到任何数据**,
    还每轮白烧 20 页请求。那是拿"零数据 + 无限重试"换掉"有数据但有洞"。
    """
    counters = cc.new_counters()
    rows = cc.poll_market(_market(), counters, wm=None)
    assert len(rows) > 0, "截断时把已抓的近端也丢了 —— 换来的是零数据 + 每轮重烧"


def test_pagination_still_stops_at_the_cap(endless):
    """留痕不是放行:该停还得停,不许把 offset 翻过硬顶(接口一律 400)。"""
    counters = cc.new_counters()
    cc.poll_market(_market(), counters, wm=None)
    assert max(endless) <= cc.OFFSET_CAP


# ---------- 4. 稳态必须完全静默(防洪的另一头) ----------

def test_no_truncation_leaves_no_trace(one_page):
    """正常翻到底 → 计数 0、痕迹 0。稳态不静默的告警会把真信号淹掉。"""
    counters = cc.new_counters()
    cc.poll_market(_market(), counters, wm=None)
    assert counters["offset_overflow_count"] == 0
    assert counters["offset_overflow_cold_count"] == 0
    assert counters["offset_overflow_warm_count"] == 0
    assert not counters.get("truncations")


# ---------- 5. 痕迹必须真的落盘(零件合格 ≠ 装上车) ----------

def test_records_reach_the_lake(endless, monkeypatch, tmp_path):
    """`poll_markets` 必须把痕迹冲进数据湖 —— 留在内存里等于没留。"""
    monkeypatch.setattr(se, "TRUNCATIONS_DIR", tmp_path / "truncations")
    monkeypatch.setattr(se, "write_trades", lambda rows, day=None, counts=None: None)
    written = []
    monkeypatch.setattr(se, "write_truncations", lambda recs: written.extend(recs))
    counters = cc.new_counters()
    cc.poll_markets([_market()], counters, wms={})
    assert len(written) == 1 and written[0]["condition_id"] == CID, \
        "poll_markets 没把截断痕迹冲盘 → 重启即失忆"


def test_write_truncations_round_trips(tmp_path, monkeypatch):
    """落盘的字段必须**读得回来**(schema 对不上会静默丢字段)。"""
    import duckdb
    monkeypatch.setattr(se, "TRUNCATIONS_DIR", tmp_path / "truncations")
    dest = se.write_truncations([{
        "condition_id": CID, "detected_at": 1786000000, "mode": "cold",
        "offset_reached": 8000, "kept_rows": 8123,
        "oldest_kept_ts": 1785000000, "watermark_before": None}])
    got = duckdb.sql(f"select * from read_parquet('{dest}')").fetchall()[0]
    assert got[0] == CID and "cold" in got
    assert se.write_truncations([]) is None, "空列表不许落一个空文件(会拖垮 compaction)"


def test_backfill_sweeper_also_flushes(monkeypatch, endless, tmp_path):
    """⭐回填清扫扫的正是"已关闭且没采过"的盘 —— 撞顶概率最高的那一类。

    它直接调 `cc.poll_market`,不走 `poll_markets`;漏掉它 = 痕迹只记一半,
    而漏掉的恰是最可能截断的那一半(又一次"丢得与结果相关")。
    """
    import backfill_closed_markets as bf
    written = []
    monkeypatch.setattr(se, "write_truncations", lambda recs: written.extend(recs))
    monkeypatch.setattr(bf, "collector_is_running", lambda: False)
    bf.backfill_once([{"condition_id": CID, "token_id_0": TOK0, "token_id_1": TOK1}],
                     cursor="", max_markets=1, time_budget_s=60,
                     write=lambda rows: None)
    assert written and written[0]["mode"] == "cold", \
        "回填清扫没冲截断痕迹 —— 而它扫的正是最可能撞顶的那批"


# ---------- 6. 纠正写在代码里的假信念 ----------

def test_no_code_still_promises_a_backfill_that_cannot_exist():
    """⭐「更早历史待压频回填」这句话是假的,而它同时出现在日志和告警注释里。

    假承诺比没有说明更坏:它让读的人**以为有人在管**,于是没人去管
    (这正是 2026-08-03 那次静默 11 天的形状)。
    压频回不了 —— 接口 offset 顶 10000 是硬约束,不是频率问题。
    """
    import inspect
    import alerts

    def _live(src):
        """只留会真被执行/打印的那些行。

        注释里**引用**这句话是为了记住它错在哪(留着有用),
        判据要焊的是它不许再出现在**说给人看的文本**里。
        """
        return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))

    for src, who in ((inspect.getsource(cc.poll_market), "poll_market 的日志"),
                     (inspect.getsource(alerts), "alerts 的告警文案")):
        assert "压频回填" not in _live(src), f"{who} 仍在承诺一个不存在的回填"
    assert "永久取不回" in inspect.getsource(cc.poll_market), \
        "只删掉假承诺不够 —— 得把真相说出来,否则读的人只是少了一条线索"


def test_the_false_self_healing_premise_is_corrected_in_writing():
    """⭐把「已知它错在哪」这件事本身焊住,免得哪天有人照着旧结论又调回去。

    `alerts.py` 曾把 offset 截断写成"稳态**自愈**事件",并据此降级为不告警 ——
    在一个错的框架里调阈值。删掉不够,得留下更正,否则下一个人会重新推出同样的错。
    """
    import inspect
    import alerts
    src = inspect.getsource(alerts)
    head = src[:src.index("OFFSET_OVERFLOW_ALERT_THRESHOLD")]
    assert "自愈" not in head[-600:] or "更正" in head[-1200:], \
        "alerts 仍把 offset 截断说成自愈事件而没有更正说明"
    assert "永久空洞" in head, "更正没写进代码 —— 下一个人会重新推出同一个错误结论"


def test_warm_truncation_is_alerted_but_steady_state_is_silent():
    """增量截断 = 轮转红线被踩穿 + 永久丢数据 ⇒ 必须推;而稳态(0)必须完全静默。

    两头都焊(CLAUDE.md:新增告警必须自带防洪判据)。
    阈值不是拍的 —— 它来自**已经存在的设计红线**(一圈须短于 11.6 小时),
    所以"发生一次就是踩穿"这个判据不需要等实测分布。
    """
    import alerts
    assert alerts.maybe_alert({"offset_overflow_warm_count": 0,
                               "offset_overflow_cold_count": 5,
                               "total_markets_polled": 50}) is False, \
        "冷启动截断是接口硬约束,推它 = 噪音(而噪音会让真信号无处可显)"
    msg = alerts.build_alert({"offset_overflow_warm_count": 1, "total_markets_polled": 50})
    assert msg and "轮转" in msg, f"增量截断没被当异常报出来:{msg!r}"
