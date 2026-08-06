#!/usr/bin/env python3
"""判据焊死:A4 完成标记的一次性种子补录(2026-08-06)。

## 为什么这个脚本值得单独一组判据

它一次性给约 1.5 万个市场打上"已经扫过了"的标记,而**打错方向的代价极不对称**:

- 少标记(该标没标)→ 白扫一遍,浪费几小时接口时间,**数据不丢**
- 多标记(不该标却标了)→ 那个市场**从此再也不会进目标集**,
  它缺的那段结算前成交**永久没人补**

所以本组判据的重心全在一个方向:**证据不足时必须不标**。

## 判定用的是什么

`成交湖里最后一次采集时刻 > 我们首次观测到它已关闭的时刻`。

两个量都只来自**我们自己做过什么**(注册表是 append-only,查得到首次记成 closed 的时刻;
成交表带 ingested_at)。**不用** Gamma 的 `end_date` —— 那是名义结束日期,
市场可能提前关也可能拖后关,拿它当判据是把正确性押在一个不可靠的外部字段上。
"""
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import discovery_service as ds  # noqa: E402
import seed_swept_markers as seed  # noqa: E402
import storage_engine as se  # noqa: E402


def _reg(cid, closed, snapshot_at):
    return {"condition_id": cid, "slug": "s", "title": "t", "event_slug": "e",
            "market_class": "event", "hft_suspect": False,
            "token_id_0": "1", "token_id_1": "2", "outcomes_json": "[]",
            "closed": closed, "resolved_outcome": None,
            "start_date": None, "end_date": None, "snapshot_at": snapshot_at}


def _trade(cid, ingested_at):
    return {"transaction_hash": f"0x{cid}{ingested_at}", "proxy_wallet": "0xw",
            "condition_id": cid, "asset": "1", "outcome_index": 0,
            "outcome_label": "Yes", "side": "BUY", "size": 1.0, "price": 0.5,
            "timestamp": 1700000000, "ingested_at": ingested_at}


def _lake(tmp_path, monkeypatch, regs, trades, swept=()):
    root = tmp_path / "lake"
    (root / "registry").mkdir(parents=True)
    (root / "raw" / "dt=2026-08-01").mkdir(parents=True)
    (root / "swept").mkdir(parents=True)
    monkeypatch.setattr(se, "DATA_ROOT", root)
    monkeypatch.setattr(se, "RAW_DIR", root / "raw")
    monkeypatch.setattr(se, "SWEPT_DIR", root / "swept")
    pq.write_table(pa.Table.from_pylist(regs, schema=ds.MARKETS_SCHEMA),
                   root / "registry" / "r.parquet")
    if trades:
        pq.write_table(pa.Table.from_pylist(trades, schema=se.TRADES_SCHEMA),
                       root / "raw" / "dt=2026-08-01" / "t.parquet")
    if swept:
        pq.write_table(pa.Table.from_pylist(list(swept), schema=se.SWEPT_SCHEMA),
                       root / "swept" / "s.parquet")
    return root


# ---------- 该标记的:证据确凿 ----------

def test_marks_markets_collected_after_we_saw_them_closed(tmp_path, monkeypatch):
    """我们知道它关了之后还采过一遍 ⇒ 那一遍是完整的 ⇒ 可以标记。"""
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100), _reg("0xA", True, 200)],
          trades=[_trade("0xA", 300)])           # 300 > 200
    assert seed.find_already_complete() == ["0xA"]


# ---------- 不该标记的:四种证据不足 ----------

def test_does_not_mark_the_hole_zero_group(tmp_path, monkeypatch):
    """⭐最要紧的一条:最后一次采集在"看到它关闭"**之前** —— 正是洞 0 那批。

    标错这一批 = 24,772 个市场缺的结算前最后一段永久没人补。
    """
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100), _reg("0xA", True, 200)],
          trades=[_trade("0xA", 150)])           # 150 < 200
    assert seed.find_already_complete() == []


def test_does_not_mark_markets_with_no_trades_at_all(tmp_path, monkeypatch):
    """一笔都没采过的,当然不能算"扫过了"。"""
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", True, 200)], trades=[_trade("0xB", 300)])
    assert seed.find_already_complete() == []


def test_does_not_mark_markets_still_open(tmp_path, monkeypatch):
    """还开着的市场根本不在 A4 的义务范围内(它还没关闭,谈不上"关闭后扫一遍")。"""
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100)], trades=[_trade("0xA", 300)])
    assert seed.find_already_complete() == []


def test_a_market_reopened_later_is_judged_by_its_latest_state(tmp_path, monkeypatch):
    """注册表是 append-only,同一市场有多版。判定必须看**最新**那版是否已关闭,
    而"首次记成 closed 的时刻"取最早那次 —— 两个量取不同的版本,故意的。"""
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", True, 100), _reg("0xA", False, 200)],   # 最新是"没关闭"
          trades=[_trade("0xA", 300)])
    assert seed.find_already_complete() == []


def test_collected_at_exactly_the_same_second_is_not_marked(tmp_path, monkeypatch):
    """边界:采集时刻**恰好等于**首次观测到关闭的时刻 ⇒ 证据不足,不标。

    这一秒里到底是先采后关还是先关后采,数据答不上来。
    按不对称原则,答不上来就当没扫过 —— 白扫一次,总好过永久少一段数据。

    ⚠️ 这条是变异测试逼出来的:把 `>` 改成 `>=` 时全套 9 条判据一条不红,
    因为我的样例里压根没有"恰好相等"的情形。**没覆盖到的边界就是没在验。**
    """
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100), _reg("0xA", True, 200)],
          trades=[_trade("0xA", 200)])           # 200 == 200
    assert seed.find_already_complete() == []


def test_threshold_is_the_first_time_we_saw_it_closed_not_the_last(tmp_path, monkeypatch):
    """注册表同一市场会有多条 closed 记录(结算写入会再 append 一版)。
    门槛必须取**首次**看到它关闭的时刻。

    取最后一次会把门槛推后 ⇒ 明明在"知道它关了之后"采过的市场也不标 ⇒ 白扫一大批。
    (方向是安全的,但那是浪费;而且和文档写的判据不符 —— 不一致本身就是隐患。)

    ⚠️ 同样是变异测试逼出来的:min 改 max 时判据全绿,
    因为样例里每个市场只有一条 closed 记录,两者恰好相同。
    """
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 50), _reg("0xA", True, 100),
                _reg("0xA", True, 400)],          # 结算写入又 append 了一版
          trades=[_trade("0xA", 200)])            # 200 > 100(首次)但 < 400(最后一次)
    assert seed.find_already_complete() == ["0xA"]


# ⚠️ 已知的**等价变异**(不是判据缺口,写下来免得下次又去"修"它):
#   把 `join ing` 改成 `left join ing` 行为完全相同 ——
#   左连接后没成交的市场 last_ing 为 NULL,而 `NULL > t_closed` 求值为 NULL,
#   照样被 WHERE 滤掉。判据抓不住它是**对的**,因为它压根没改变行为。


# ---------- 幂等 ----------

def test_already_marked_markets_are_not_marked_again(tmp_path, monkeypatch):
    """复跑必须写 0 条 —— 否则每跑一次标记表就翻一倍。"""
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100), _reg("0xA", True, 200)],
          trades=[_trade("0xA", 300)],
          swept=[{"condition_id": "0xA", "swept_at": 1, "source": "seed",
                  "trades_written": -1}])
    assert seed.find_already_complete() == []


def test_seed_writes_then_becomes_a_no_op(tmp_path, monkeypatch):
    """端到端:第一次写入 N 个,第二次写 0 个。"""
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100), _reg("0xA", True, 200),
                _reg("0xB", False, 100), _reg("0xB", True, 200)],
          trades=[_trade("0xA", 300), _trade("0xB", 300)])
    assert seed.seed() == 2
    assert seed.seed() == 0


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    """试跑不许落盘 —— 这个脚本改的是"以后还扫不扫"这种不可逆的判断。"""
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100), _reg("0xA", True, 200)],
          trades=[_trade("0xA", 300)])
    assert seed.seed(dry_run=True) == 1
    assert not se.has_swept_data(), "试跑却写了标记"


def test_seed_marker_is_distinguishable_from_a_real_sweep(tmp_path, monkeypatch):
    """种子标记必须能和"真扫过一遍"分得开(source='seed',笔数写 -1)。

    以后若发现种子判据定错了,要能把这一批**单独**挑出来重扫 ——
    混成一样就再也分不开了。
    """
    _lake(tmp_path, monkeypatch,
          regs=[_reg("0xA", False, 100), _reg("0xA", True, 200)],
          trades=[_trade("0xA", 300)])
    seed.seed()
    rows = pq.read_table(se.SWEPT_DIR).to_pylist()
    assert [r["source"] for r in rows] == ["seed"]
    assert rows[0]["trades_written"] == -1
