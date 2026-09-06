#!/usr/bin/env python3
"""判据:`dt` 目录名必须等于该目录下**每一笔**成交的真实 UTC 成交日(2026-08-27)。

设计单:`docs/DESIGN_DT_PARTITION_FIX_2026-08-27.md`(需求 / 设计 / 设计 review 都在里面)

## 由来(实测,非假想)

`storage_engine.write_trades` 原先这样定分区:

    day = dt.datetime.fromtimestamp(int(rows[0]["timestamp"]), dt.UTC).strftime(...)

**整批按 `rows[0]` 的日期归档。** 后果实测(2026-08-27 全湖):

- 2,961.5 万笔里 **1,017.9 万笔(34.4%)** 的 `dt` 与自己的真实成交日对不上
- 当前 **41 个分区**,按真实成交日应该有 **460 个**
- 最狠的 `dt=2026-08-01` 一个目录装着 89 万笔,真实成交日横跨
  **2025-05-13 ~ 2026-08-01(15 个月)**

⭐ 危害不是"现在算错了" —— 实查:全项目**没有一处按 dt 过滤**,都是 `dt=*` 读全湖,
所以既往结论没被污染。危害是**它是一个上了膛的陷阱**:将来任何人(包括我)顺手写一句
`WHERE dt BETWEEN ...`,就会拿到一个与结果相关的偏样本,而且**不报错、不缺列、
数量看着还差不多,只有结论是错的**。

## 这组判据焊死什么

1. 跨日界的批次必须**按每行自己的日期**分开落盘(核心 bug)
2. `rows[0]` 与其余行不同日时,**不许**整批跟着 `rows[0]` 走(直指根因的形状)
3. 单日批次仍然只写一个文件(不许无谓地把文件炸碎 —— 实测 64.8% 的写入是单日)
4. 显式传 `day=` 时照旧(`test_compaction_sweep` 依赖它把文件塞进指定分区)
5. 单行时间戳坏掉:**跳过该行并出声**,不许一颗老鼠屎打翻整批

## 异常家族按【造真坏输入实测】决定(2026-08-27 实跑)

    10**18  -> OSError          10**19 起 -> OverflowError   ← 量级边界会换族
    -10**15 -> ValueError       -10**17 -> OSError   -10**20 -> OverflowError
    None    -> TypeError        'abc'   -> ValueError  inf -> OverflowError
⇒ `except (OSError, ValueError, TypeError, OverflowError)`。

🔴 **我第一版漏了 `OverflowError`**,是 2026-08-27 code review 判 BLOCKER 抓出来的。
   根源:我"实测"时只探了 `10**18` 和 `2**63-1`,**两个恰好都落在 OSError 那一侧**,
   于是整个 OverflowError 族没被看见,而这个参数化又原样继承了同一个盲区。
   ⇒ 形状同 [[lesson-half-measured-stamped-as-verified]]:
     只量了会落进目标族的值,没量会落到别处的值,就盖了"已实测"的章。

⚠️ `0` 和 `-1` **不抛**(得到 1970 年),它们是"合法但可疑"的值,不归这一条管。
"""
from __future__ import annotations

import datetime as dt
import importlib
import sys
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

pytest.importorskip("pyarrow")

DAY = 86400
# 固定锚点,不用 now():判据不许随时钟漂移(否则某天午夜跑会时红时绿)。
T_2026_08_10_1200Z = int(dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.UTC).timestamp())


@pytest.fixture
def se(tmp_path, monkeypatch):
    """把数据湖隔离到 tmp,import 全新 storage_engine(读 REBIRTH_DATA)。

    ⛔ 绝不碰生产数据湖 —— 项目铁律「测试隔离」。
    """
    monkeypatch.setenv("REBIRTH_DATA", str(tmp_path))
    import storage_engine
    importlib.reload(storage_engine)
    assert str(tmp_path) in str(storage_engine.RAW_DIR), "隔离没生效,拒绝继续"
    return storage_engine


def _row(i: int, ts: int) -> dict:
    """一条最小合法 trade(唯一 tx_hash → 不会被去重误合)。"""
    return {
        "transaction_hash": f"0x{i:064x}", "proxy_wallet": "0xw", "condition_id": "0xc",
        "asset": "1", "outcome_index": 0, "outcome_label": "Yes", "side": "BUY",
        "size": 1.0, "price": 0.5, "timestamp": ts, "ingested_at": ts,
    }


def _day_of(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")


def _partitions(se) -> dict[str, int]:
    """{分区日 -> 该分区的行数}。直接读磁盘,不信任返回值。"""
    import pyarrow.parquet as pq
    out = {}
    for p in sorted(se.RAW_DIR.glob("dt=*")):
        n = sum(pq.read_table(f).num_rows for f in p.glob("*.parquet"))
        if n:
            out[p.name[len("dt="):]] = n
    return out


def _rows_in(se, day: str) -> list[int]:
    """某分区里所有行的 timestamp。"""
    import pyarrow.parquet as pq
    ts = []
    for f in (se.RAW_DIR / f"dt={day}").glob("*.parquet"):
        ts += pq.read_table(f).column("timestamp").to_pylist()
    return ts


# ─────────────────────────────────────────────────────────────────────────────
# 核心:每一行必须落在与自己成交日相符的分区
# ─────────────────────────────────────────────────────────────────────────────
def test_a_batch_spanning_midnight_is_split_by_each_rows_own_date(se):
    """跨日界的一批:必须按每行自己的日期分开,不是整批塞进一个抽屉。

    实测:差 1 天的错位占全湖 10.43%,就是这个形状(正常轮询批次跨了午夜)。
    """
    t0 = T_2026_08_10_1200Z
    rows = [_row(0, t0), _row(1, t0 + DAY)]        # 08-10 与 08-11 各一笔
    se.write_trades(rows)
    assert _partitions(se) == {_day_of(t0): 1, _day_of(t0 + DAY): 1}


def test_the_batch_does_NOT_follow_row_zero(se):
    """⭐直指根因:`rows[0]` 是 08-10,其余 3 行都是 08-11。

    旧代码整批按 `rows[0]` 归档 ⇒ 4 行全进 08-10。
    这条判据就是要让那种写法**红**。
    """
    t0 = T_2026_08_10_1200Z
    rows = [_row(0, t0)] + [_row(i, t0 + DAY) for i in range(1, 4)]
    se.write_trades(rows)
    parts = _partitions(se)
    assert parts == {_day_of(t0): 1, _day_of(t0 + DAY): 3}, \
        f"整批跟着 rows[0] 走了 —— 根因没修 → {parts}"


def test_every_row_lands_in_the_partition_matching_its_own_trade_date(se):
    """更强的一条:5 天散开,逐个分区核对**里面的每一行**都属于这一天。

    ⚠️ 只断言"分区数对"是不够的 —— 行数对而内容错的写法照样能过。
    """
    t0 = T_2026_08_10_1200Z
    rows = [_row(i, t0 + i * DAY) for i in range(5)]
    se.write_trades(rows)
    for i in range(5):
        day = _day_of(t0 + i * DAY)
        for ts in _rows_in(se, day):
            assert _day_of(ts) == day, f"dt={day} 的分区里混进了 {_day_of(ts)} 的成交"


def test_a_backfill_spanning_a_year_lands_in_one_partition_per_day(se):
    """回填的形状:一批横跨 120 天(实测单次写入最大跨度 112 天)。

    实测差 31~180 天的错位占全湖 11.82%,全是这个形状造成的。
    """
    t0 = T_2026_08_10_1200Z - 120 * DAY
    rows = [_row(i, t0 + i * DAY) for i in range(120)]
    se.write_trades(rows)
    parts = _partitions(se)
    assert len(parts) == 120, f"应产生 120 个分区,实得 {len(parts)}"
    assert set(parts.values()) == {1}


# ─────────────────────────────────────────────────────────────────────────────
# 防洪的另一头:不许无谓地把文件炸碎
# ─────────────────────────────────────────────────────────────────────────────
def test_a_single_day_batch_still_writes_exactly_one_file(se):
    """⭐实测 **64.8%** 的真实写入就是单日 —— 这一档必须仍然只写一个文件。

    否则每次写入都多出无用的碎片,而 compaction 的阈值是**每分区** 50 个文件,
    碎片摊薄到多个分区后**永远够不到、永远不会被合并**(设计单 §4.2)。
    """
    t0 = T_2026_08_10_1200Z
    se.write_trades([_row(i, t0 + i) for i in range(20)])   # 同一天内 20 笔
    files = list((se.RAW_DIR / f"dt={_day_of(t0)}").glob("*.parquet"))
    assert len(files) == 1, f"单日批次被拆成了 {len(files)} 个文件"


def test_the_number_of_files_equals_the_number_of_distinct_days(se):
    """文件数 = 批次里不同日期的个数。多一个都是无谓碎片。"""
    t0 = T_2026_08_10_1200Z
    rows = [_row(i, t0 + (i % 3) * DAY) for i in range(30)]   # 3 天 × 10 笔
    se.write_trades(rows)
    total = sum(len(list(p.glob("*.parquet"))) for p in se.RAW_DIR.glob("dt=*"))
    assert total == 3, f"3 个日期应写 3 个文件,实得 {total}"


# ─────────────────────────────────────────────────────────────────────────────
# 既有契约不许被打破
# ─────────────────────────────────────────────────────────────────────────────
def test_an_explicit_day_still_overrides_the_grouping(se):
    """`test_compaction_sweep` 依赖 `day=` 把文件强行塞进指定分区来造小文件。

    ⛔ 这条契约不许被本次改动打破,否则那组判据测的就不是它宣称测的东西了。
    """
    t0 = T_2026_08_10_1200Z
    se.write_trades([_row(0, t0), _row(1, t0 + DAY)], day="2020-01-01")
    assert _partitions(se) == {"2020-01-01": 2}


def test_empty_rows_still_write_nothing(se):
    """空批次不许落空文件(空文件会污染分区、拖垮 compaction)。"""
    se.write_trades([])
    assert list(se.RAW_DIR.glob("dt=*")) == []


def test_the_return_value_names_every_file_actually_written(se):
    """返回值必须与磁盘上真实落下的文件一一对应。

    ⚠️ 生产调用方目前都**不使用**返回值(已实查 collector_core.py:330 /
       backfill_closed_markets.py:228),但契约得是真的 —— 不然它就是句谎话。
    """
    t0 = T_2026_08_10_1200Z
    got = se.write_trades([_row(0, t0), _row(1, t0 + DAY)])
    on_disk = {p.resolve() for p in se.RAW_DIR.glob("dt=*/*.parquet")}
    assert {Path(p).resolve() for p in got} == on_disk


# ─────────────────────────────────────────────────────────────────────────────
# 一行坏数据不许打翻整批
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad,label", [
    (10**18, "OSError(10^18)"),
    (-10**15, "ValueError(-10^15)"),
    (None, "TypeError(None)"),
    ("abc", "TypeError(非数字字符串)"),
    # ⭐2026-08-27 code review 抓出的 BLOCKER:**异常家族在量级边界上会换**。
    #   我原先"实测"只探了 10^18 和 2^63-1,两个**恰好都落在 OSError 那一侧**,
    #   于是漏掉了 OverflowError,而判据的参数化原样继承了同一个盲区。
    #   实测边界:10^18→OSError,但 **10^19 起→OverflowError**;
    #             -10^17→OSError,但 **-10^20→OverflowError**;float('inf')→OverflowError。
    #   教训与 [[lesson-half-measured-stamped-as-verified]] 同形:
    #   只量了会落在目标族里的值,没量会落在别处的值,就盖了"已实测"的章。
    (10**19, "⭐OverflowError(10^19,量级边界)"),
    (10**20, "⭐OverflowError(10^20)"),
    (-10**20, "⭐OverflowError(-10^20)"),
    (float("inf"), "⭐OverflowError(inf)"),
])
def test_one_bad_timestamp_is_skipped_and_the_good_rows_still_land(se, capsys, bad, label):
    """⭐一颗老鼠屎不许打翻整批:坏行跳过,好行照样落盘,而且**要出声**。

    异常家族是**造真坏输入实测**出来的(见模块 docstring),不是想当然。
    出声的消费者是 `collector.log` —— 与同文件里落盘失败那条 🔴 用的是同一条通路。
    """
    t0 = T_2026_08_10_1200Z
    rows = [_row(0, t0), _row(1, bad), _row(2, t0 + DAY)]
    se.write_trades(rows)                              # 不抛 = 第一层要求
    assert _partitions(se) == {_day_of(t0): 1, _day_of(t0 + DAY): 1}, \
        f"{label}:好行没能落盘"
    out = capsys.readouterr()
    assert "timestamp" in (out.out + out.err), f"{label}:坏行被**静默**丢掉了"


def test_a_zero_timestamp_is_not_treated_as_an_error(se):
    """⚠️ 边界:`0` 和 `-1` **不抛异常**(实测得到 1970 年),它们是
    「合法但可疑」的值,不归上面那条管 —— 照常落进 1970 分区,让它**看得见**。

    ⛔ 不许悄悄把它归到"今天" —— 那正是本次要修的那个病(把不属于这天的塞进这天)。
    """
    se.write_trades([_row(0, 0)])
    assert _partitions(se) == {"1970-01-01": 1}


# ─────────────────────────────────────────────────────────────────────────────
# ⭐落盘顺序是**正确性要求**,不是风格(2026-08-27 code review 抓出)
# ─────────────────────────────────────────────────────────────────────────────
def test_a_crash_midway_leaves_a_time_ordered_prefix_never_a_hole(se, monkeypatch):
    """⭐⭐这条焊死的是**永久缺口**,本项目最怕的那个后果。

    机制:市场水位线 = 湖里该市场的 `max(timestamp)`(`all_watermarks`),
    而轮询遇到 `ts <= wm` 就**停止翻页**(`collector_core.py:169`)——
    **比水位线旧的成交永远不会被再抓一次。**

    ⇒ 若 `write_trades` 按**新→旧**落盘,中途被 SIGKILL:新的那几天已落盘、
      水位线被抬到最新,而**更旧的、没写成的那几天全部躺在水位线之下 ⇒ 永久缺口。**
    ⇒ 按**旧→新**落盘则安全:落下的永远是一个**时间前缀**,水位线只会低报,
      下一轮从断点接着补。

    ⛔ 所以 `sorted(groups.items())` 是**不许动的不变量**。
       我原先在注释里写它"只为可读性、不影响正确性" —— **那是句假话**,已改。
    """
    t0 = T_2026_08_10_1200Z
    rows = [_row(i, t0 + i * DAY) for i in range(6)]        # 6 天,每天一笔

    real = se._write_one_partition
    calls = {"n": 0}

    def die_after_two(rs, day):
        if calls["n"] >= 2:
            raise RuntimeError("模拟:落盘到一半进程被杀")
        calls["n"] += 1
        return real(rs, day)

    monkeypatch.setattr(se, "_write_one_partition", die_after_two)
    with pytest.raises(RuntimeError):
        se.write_trades(rows)

    landed = [ts for d in _partitions(se) for ts in _rows_in(se, d)]
    assert landed, "前提不成立:一笔都没落盘"
    missing = [r["timestamp"] for r in rows if r["timestamp"] not in landed]
    assert missing, "前提不成立:全落盘了,没测到中途崩的情形"
    assert max(landed) < min(missing), (
        f"落盘的不是时间前缀 —— 水位线会被抬到 {max(landed)},"
        f"而没写成的 {min(missing)} 躺在它之下 ⇒ **永久缺口**")


# ─────────────────────────────────────────────────────────────────────────────
# 丢弃的行必须进心跳,不能只 print(2026-08-27 code review 抓出)
# ─────────────────────────────────────────────────────────────────────────────
def test_dropped_rows_are_counted_into_the_heartbeat_not_just_printed(se):
    """⛔ 只 print 到 stderr = 「记录事实 vs 使用事实,只接一头」——本项目已犯 5 次。

    同文件的 `compact_day` 早就是对的做法:收 `counts` 字典、bump 具名字段、
    进 `AUDIT_FIELDS` ⇒ 心跳里能被守护/趋势判据消费。这条判据要求一样的待遇。
    """
    t0 = T_2026_08_10_1200Z
    counts: dict = {}
    se.write_trades([_row(0, t0), _row(1, 10**20)], counts=counts)
    assert counts.get("trade_day_unparsable_count") == 1, \
        f"丢弃的行没有进结构化计数,只靠 print 是没有读取者的 → {counts}"


def test_the_dropped_counter_is_in_the_heartbeat_whitelist(se):
    """计数了但没进心跳白名单 = 进程一退就蒸发,事后无从归因,等于没计。"""
    assert "trade_day_unparsable_count" in se.AUDIT_FIELDS
