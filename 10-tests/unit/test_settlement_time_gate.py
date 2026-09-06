#!/usr/bin/env python3
"""判据焊死:结算段的时间闸(2026-08-06)。

## 病(实测,非推测)

结算段是四段里**唯一没有时间闸**的:它只靠 `checked=800` 这个**个数**配额约束。
个数配额挡不住延迟退化 —— 每次往返从 1.1s 涨到 10s,同样 800 个就从 18s 涨到 160s。

实测 160 轮心跳的结算段耗时分布:

    p50  25s   p90  57s   p99 389s   max 441s

p50 与 max 差 **17.6 倍**。而慢周期告警线是 540s、systemd 硬杀 900s ——
441s 那一轮已经把整轮推到 438s,离告警线只剩 ~100s,且这个余量不由我们决定。

⚠️ 08-06 给 429 加了重试之后,这一段的最坏耗时**变得更大**(限流从"立即返回"
变成"重试 5 次")—— 那次改动只把副作用写进了
`test_registration_budget.py::test_known_unbounded_segments_are_pinned`,没有修。本文件是来修的。

## 闸值怎么定的(先写判据、后定参数)

两条红线夹出来的:

- **下界**:必须**高于实测 p90(57s)**,否则稳态天天被砍 —— 那不是降级,是把
  正常吞吐当异常处理,真值供给会平白变慢。
- **上界**:四道闸声明值之和 + 实测固定开销必须仍在慢周期告警线(540s)以内,
  否则稳态就会天天告警(防洪失效)。
  540 - (90 + 180 + 170) - (compaction 2 + 注册表读取 3) = **95s**

⇒ 窗口 (57, 95),取 **80s**:比 p90 高 40%,离上界还留 15s。
实测 160 轮里会被砍的约 10 轮(6%),且全部是延迟退化的那几轮 —— 正是想砍的那些。

## ⭐本文件最要紧的一条

**游标只许走过"真的查过"的那些市场。**

时间闸砍掉尾巴之后,如果游标照旧写 `picked[-1]`,被砍的那一段就**再也不会被复查**
—— 一轮砍一次,轮转圈圈都在跳过同一批。这是「静默丢样本」的原样重现,
而且丢得与结果相关(专丢延迟退化时排在后面的那些)。
`test_cursor_advances_only_over_actually_checked_markets` 焊死这一点。

## 本判据**不能**回答什么

不回答"80s 会不会太紧",那要一周的心跳分布(⏰2026-08-11 阈值校准一起做);
也不断言整轮从此有界 —— `poll_market` 的分页循环仍然无界,
见 `test_known_unbounded_segments_are_pinned`。
"""
import sys
from pathlib import Path

import pytest

# 11-collector 未在 conftest 路径里,本测试自带
COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import discovery_service as ds  # noqa: E402
import settlement_watcher as sw  # noqa: E402


# 实测:160 轮心跳的结算段耗时(见模块 docstring)。写死在这里是为了闸值改动时
# 有人能一眼看到它当初是拿什么撑起来的,而不是回去 grep 日志。
MEASURED_P50_S, MEASURED_P90_S, MEASURED_MAX_S = 25, 57, 441

N_MARKETS = 800          # = DEFAULT_MAX_CHECK
SECONDS_PER_CHUNK = 30.0  # 退化态的每批耗时(实测 441s / 8 批 ≈ 55s,取 30 便于算)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _row(i):
    # end_date 全部已过 → 全部进 pending;cid 递增使排序稳定、便于断言游标位置
    return {"condition_id": f"0x{i:064x}", "end_date": "2025-01-01T00:00:00Z",
            "resolved_outcome": None, "slug": f"slug-{i}"}


@pytest.fixture
def sealed(monkeypatch):
    """把结算守望与**生产状态文件**彻底隔开,并交出可控的时钟与批量查询。

    ⚠️ 不封死 `_save_cursor` / `_save_streak` 的话,跑一次单测就会把线上游标写掉
    (08-06 真的这样污染过 `poll_cursor.json`)。测试隔离不是风格问题。
    """
    rows = [_row(i) for i in range(N_MARKETS)]
    state = {"cursor": "", "streak": 0, "clock": _Clock(), "chunks": [], "get_deadlines": []}

    monkeypatch.setattr(sw.time, "monotonic", state["clock"])
    monkeypatch.setattr(sw, "load_registry", lambda: {r["condition_id"]: r for r in rows})
    monkeypatch.setattr(sw, "_load_cursor", lambda: state["cursor"])
    monkeypatch.setattr(sw, "_save_cursor", lambda c: state.update(cursor=c))
    monkeypatch.setattr(sw, "_load_streak", lambda: state["streak"])
    monkeypatch.setattr(sw, "_save_streak", lambda n: state.update(streak=n))
    monkeypatch.setattr(sw, "_atomic_write_parquet",
                        lambda *a, **k: pytest.fail("本组判据不该写 parquet"))

    def fake_lookup(cids, net=None, deadline=None):
        state["chunks"].append(list(cids))
        state["get_deadlines"].append(deadline)
        state["clock"].t += SECONDS_PER_CHUNK
        return {}          # 一个都没查到 → 不会走到 parse_market / 写盘

    monkeypatch.setattr(sw, "_batch_lookup_gamma", fake_lookup)
    state["rows"] = rows
    return state



def _pack_sizes(n=N_MARKETS):
    """当前打包规则下 n 个 cid 切成的各批大小。

    ⚠️ 2026-08-16 起批量由 **URL 字节** 决定(`discovery_service.pack_condition_ids`),
    不再是固定 100 个。故本文件里凡涉及"一批多少个"的算术一律从这里推 ——
    写死数字的话,以后调预算这些判据会红在算术上,而它们要验的是**闸的行为**,
    人会因此去改数字而不是去想闸对不对。
    """
    return [len(b) for b in ds.pack_condition_ids([f"0x{i:064x}" for i in range(n)])]


def _checked_under_gate(n_chunks):
    """闸只放行 n_chunks 批时,应该查过多少个。"""
    return sum(_pack_sizes()[:n_chunks])


def _checked_cids(state):
    return [c for chunk in state["chunks"] for c in chunk]


# ---------- 1. 闸要真的停 ----------

def test_gate_stops_before_starting_a_chunk_past_the_deadline(sealed):
    """预算 80s / 每批 30s → 只做得完 3 批(t=0、30、60 起跑;90 已越线)。

    钟必须在**发起这一批之前**看 —— 查完再看必然超出一整批的耗时。
    """
    out = sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    assert len(sealed["chunks"]) == 3, (
        f"应只做 3 批,实际 {len(sealed['chunks'])} 批 —— 闸没咬住,或钟看在了批次之后")
    assert out["checked"] == _checked_under_gate(3)


def test_no_budget_means_unchanged_behaviour(sealed):
    """默认关闭的回归证明:不传预算 = 老行为(全做完、不砍、游标走到队尾)。

    改共享引擎必须默认关闭 + 回归证明(CLAUDE.md 铁律 4)。
    """
    out = sw.watch_settlements(max_check=N_MARKETS)
    assert len(sealed["chunks"]) == len(_pack_sizes())
    assert out["checked"] == N_MARKETS
    assert out["timegate_skipped"] == 0
    assert sealed["cursor"] == sw._sort_key(sealed["rows"][-1])


# ---------- 2. ⭐游标:被砍掉的那一段必须留在原地等下轮 ----------

def test_cursor_advances_only_over_actually_checked_markets(sealed):
    """⭐被时间闸砍掉的尾巴,下一轮必须**从它开始**接着查,而不是被跳过去。

    照旧写 `picked[-1]` 的话:每轮砍一次 = 每圈都跳过同一批,
    而跳掉的恰恰是"延迟退化时排在后面"的那些 —— 丢得与结果相关。
    """
    sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    checked = _checked_cids(sealed)
    last_checked = next(r for r in sealed["rows"] if r["condition_id"] == checked[-1])
    assert sealed["cursor"] == sw._sort_key(last_checked), \
        "游标必须停在最后一个**真查过**的市场上"
    assert sealed["cursor"] != sw._sort_key(sealed["rows"][-1]), \
        "游标走到了没查过的队尾 → 被砍那段永远轮不到(静默丢样本)"


def test_next_round_resumes_at_the_cut_point(sealed):
    """把两轮串起来跑一遍:第二轮查的必须**紧接**第一轮停下的地方,不许有洞。

    只断言游标值不够 —— 游标对不对,要用"下一轮真的查到了谁"来验。
    """
    sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    first = _checked_cids(sealed)
    sealed["chunks"].clear()
    sealed["clock"].t = 0.0
    sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    second = _checked_cids(sealed)

    all_cids = [r["condition_id"] for r in sealed["rows"]]
    assert second[0] == all_cids[all_cids.index(first[-1]) + 1], \
        f"第二轮应从 {first[-1]} 的下一个接着查,实际从 {second[0]} 开始 —— 中间有洞"
    assert not set(first) & set(second), "两轮重复查了同一批 → 游标没前进"


def test_cursor_unchanged_when_the_gate_cuts_everything(sealed):
    """一个都没查成时,游标必须**原地不动** —— 白白前进 = 整批被跳过。"""
    sealed["cursor"] = "预先存在的游标"
    out = sw.watch_settlements(max_check=N_MARKETS, time_budget_s=0.0)
    assert sealed["chunks"] == []
    assert out["checked"] == 0
    assert sealed["cursor"] == "预先存在的游标"


# ---------- 3. 砍掉多少必须出声 ----------

def test_skipped_is_counted_out_loud(sealed):
    """任何降级/剔除必须出声计数(CLAUDE.md 铁律 3)。静默丢样本是真凶。"""
    out = sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    assert out["timegate_skipped"] == N_MARKETS - out["checked"] == N_MARKETS - _checked_under_gate(3)


def test_checked_reports_reality_not_the_plan(sealed):
    """`checked` 是**实际查过**的个数,不是"本轮打算查"的个数。

    报计划数会让"结算吞吐掉了一半"完全隐形 —— 心跳里的数字纹丝不动。
    """
    out = sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    assert out["checked"] == len(_checked_cids(sealed)) == _checked_under_gate(3)


def test_zero_streak_does_not_advance_when_nothing_was_attempted(sealed):
    """闸把整轮砍光 = "没能问",不是"问了没有" → 连零守护不许进位。

    这两者处置完全不同:前者查网络/闸值,后者查真值链路。
    混在一起会让真值断供告警在网络退化时天天误报,而误报会让真信号无处可显。
    """
    sealed["streak"] = 3
    out = sw.watch_settlements(max_check=N_MARKETS, time_budget_s=0.0)
    assert out["zero_streak"] == 3, "一个都没查成却给连零守护进了位 → 网络一坏就误报"


def test_zero_streak_still_advances_on_a_real_dry_round(sealed):
    """反面:真的查了却一个都没结算 → 必须进位(否则守护等于关掉了)。

    ⭐这条是上一条的"防蒙混":只写上一条的话,把守护整个禁用也能通过。
    """
    sealed["streak"] = 3
    out = sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    assert out["zero_streak"] == 4, "查了 300 个一个没结算,连零守护却没动"


# ---------- 4. 闸要能真的兜住(deadline 必须传进 _get) ----------

def test_deadline_is_threaded_into_batch_lookup(sealed):
    """批次之间看钟还不够:单次 `_get` 重试风暴就有 ~130s,比 80s 的闸还大。

    必须把**同一个绝对 deadline** 传进去,让 `_get` 自己在重试之间和 socket
    超时上都看钟(该能力 08-06 已加,见 discovery_service._get)。
    """
    sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    dls = sealed["get_deadlines"]
    assert all(d is not None for d in dls), "deadline 没传进 _batch_lookup_gamma → 闸能被单次重试打穿"
    assert len(set(dls)) == 1, f"每批各算了一个 deadline({dls})→ 闸会被逐批顺延,等于没有"
    assert dls[0] == pytest.approx(80.0), "deadline 必须是**绝对时刻**(起点+预算),不是剩余量"


def test_batch_lookup_passes_deadline_to_get(monkeypatch):
    """零件级:`_batch_lookup_gamma` 自己得把 deadline 交给 `_get`。

    上一条只验到批量函数的门口 —— "零件合格 ≠ 装上车",08-05 栽过一次。
    """
    seen = []

    def fake_get(url, net=None, deadline=None, **kw):
        seen.append(deadline)
        return []

    monkeypatch.setattr(ds, "_get", fake_get)
    sw._batch_lookup_gamma(["0xabc"], net=None, deadline=1234.5)
    assert seen == [1234.5, 1234.5], f"两遍查询都要带 deadline,实际 {seen}"


def test_gate_bounds_the_segment_when_every_get_is_slow(sealed, monkeypatch):
    """端到端:每批都慢到 130s(重试风暴的量级)时,整段仍在一个可写下来的界内。

    没有闸的时候是 每批 130s × 全部批次 —— 直接越过 systemd 硬杀线。
    """
    monkeypatch.setattr(sw, "_batch_lookup_gamma", lambda cids, net=None, deadline=None:
                        (sealed["chunks"].append(list(cids)),
                         sealed["clock"].__setattr__("t", sealed["clock"].t + 130.0), {})[-1])
    t0 = sealed["clock"].t
    sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    elapsed = sealed["clock"].t - t0
    assert len(sealed["chunks"]) == 1, "越过预算后不该再发起新批次"
    # 上界 = 预算 + 一批的最坏耗时(闸只在批次之间看钟;批内由 _get 的 deadline 兜)
    assert elapsed <= 80.0 + 130.0, f"整段 {elapsed}s 超出可声明的上界"
    assert elapsed < 130.0 * len(_pack_sizes()), "无闸时的量级(全部批次 × 130s)——闸没起作用"


# ---------- 5. ⭐闸砍在"两遍查询之间"时,丢的恰是唯一想要的那一类 ----------

def test_deadline_hit_on_the_closed_pass_must_not_fabricate_a_result(monkeypatch):
    """⭐加了闸之后新出现的一个「与结果相关的丢样本」入口,必须堵死。

    批量查询要走**两遍**(默认 + &closed=true),已结算市场**只在第二遍**才拿得到。
    闸正好砍在两遍之间时,丢掉的**全是已结算的** —— 即唯一想要的那一类。
    (这和 08-03 那次"Gamma 默认只返未关闭"是同一个形状,只是这次是我自己的闸造的。)

    所以这些市场必须表现为**查不到**:调用方计 lookup_fail、它们留在 pending 里
    等轮转回来。绝不能当成"查过了,它没结算" —— 那会把它们从 pending 除名 = 永久丢。
    """
    monkeypatch.setattr(ds, "_get", lambda url, **kw:
                        {"__http__": ds.DEADLINE_HIT} if "closed=true" in url
                        else [{"conditionId": "c-open", "closed": False}])
    got = sw._batch_lookup_gamma(["c-open", "c-closed"], deadline=1.0)
    assert "c-closed" not in got, \
        "撞闸那一遍的市场被当成'查过' → 会被从 pending 除名,而它们全是已结算的"
    assert set(got) == {"c-open"}


def test_half_checked_chunk_is_counted_as_lookup_fail(sealed, monkeypatch):
    """半查的那一批要出声:`lookup_fail` 必须涨,否则"闸砍掉了真值供给"完全隐形。

    这些市场 `resolved_outcome` 仍是 None ⇒ 下一圈轮转还会回来,是**延迟不是丢失**。
    但延迟必须可见 —— 不可见的延迟和丢失在心跳上长得一模一样。
    """
    monkeypatch.setattr(sw, "_batch_lookup_gamma",
                        lambda cids, net=None, deadline=None:
                        (sealed["chunks"].append(list(cids)),
                         sealed["clock"].__setattr__("t", sealed["clock"].t + 30.0), {})[-1])
    out = sw.watch_settlements(max_check=N_MARKETS, time_budget_s=80.0)
    assert out["lookup_fail"] == out["checked"] == _checked_under_gate(3), \
        "一个都没查到却没计 lookup_fail → 真值断供在心跳上看不出来"


# ---------- 6. 闸值本身:必须有实测分布撑着 ----------

def test_gate_value_sits_between_the_two_measured_red_lines():
    """闸值不是拍的,是被两条红线夹出来的。改参数时这条会告诉你越了哪一边。"""
    import alerts
    import run_cycle as rc

    gate = rc.SETTLEMENT_TIME_BUDGET_S
    assert gate > MEASURED_P90_S, (
        f"闸 {gate}s ≤ 实测 p90 {MEASURED_P90_S}s → 稳态天天被砍,"
        "真值供给平白变慢(把正常吞吐当异常处理)")

    MEASURED_FIXED_S = 2 + 3   # compaction + 注册表读取(结算段已不在固定开销里)
    upper = alerts.slow_cycle_threshold_s() - (
        rc.FIREHOSE_TIME_BUDGET_S + rc.REGISTER_TIME_BUDGET_S
        + rc.POLL_TIME_BUDGET_S + MEASURED_FIXED_S)
    assert gate < upper, (
        f"闸 {gate}s ≥ 上界 {upper:.0f}s → 四闸之和越过慢周期告警线,稳态天天告警")


def test_run_cycle_actually_passes_the_gate():
    """⭐参数存在 ≠ 被用上。真机上没传下去的话,上面全部判据都在验一个死参数。"""
    import inspect
    import run_cycle as rc

    src = inspect.getsource(rc.main)
    assert "SETTLEMENT_TIME_BUDGET_S" in src, \
        "run_cycle.main 没把结算闸传给 watch_settlements → 线上仍然无闸"


def test_skipped_count_is_persisted_to_the_heartbeat():
    """砍了多少必须落到 parquet:一周后校准阈值时要的是分布,不是去 grep 日志文本。"""
    import inspect
    import run_cycle as rc
    import storage_engine as se

    assert "settlement_timegate_skipped_count" in se.AUDIT_FIELDS
    assert "settlement_timegate_skipped_count" in inspect.getsource(rc.main), \
        "字段在 AUDIT_FIELDS 里但 run_cycle 没填 → 心跳里永远是 0(比缺字段更坏)"


def test_every_field_run_cycle_computes_reaches_the_heartbeat():
    """⭐两个模块之间那道**没人守的缝**:算了却没落盘。

    `write_audit_heartbeat` 只写 `AUDIT_FIELDS` 里列的字段,
    `counts.get(k, 0)` 对**没列进去的键直接丢弃,一声不吭**。
    于是 run_cycle 里辛苦算出来的量可以完整地消失,而日志照常打印 ——
    「日志里看得见 ≠ 心跳里查得到」,而校准阈值只能用心跳。

    🔴 2026-08-06 加结算时间闸时顺手撞到的实况,丢掉的三个是:

    - `newly_resolved` —— **就是 08-03 那次静默 11 天的那个量**。事故复盘写着
      "它恒为 0 而没人规定它不该是 0";修完之后它**依然没进心跳**,
      想复核"这条链路还活着吗"只能去 grep 日志文本。
    - `settlement_lookup_fail` / `settlement_checked` —— 分子和分母。
      run_cycle 里那行注释白纸黑字写着"失败按比率判定,须带上分母",
      而分母恰恰是被丢掉的三个之一。

    这条判据两个方向都焊:列了没填 = 永远 0(上一条管);填了没列 = 静默消失(本条管)。
    """
    import inspect
    import re
    import run_cycle as rc
    import storage_engine as se

    src = inspect.getsource(rc.main)
    body = src[src.index("merged = {"):src.index("se.write_audit_heartbeat")]
    computed = re.findall(r'^\s+"([a-z_]+)":', body, re.M)
    assert computed, "没解析出 merged 里的字段 —— 本判据已失效,请改写(不是通过)"
    dropped = [k for k in computed if k not in se.AUDIT_FIELDS]
    assert not dropped, (
        f"run_cycle 算了但 AUDIT_FIELDS 没列 → 这些量在心跳里静默消失:{dropped}")
