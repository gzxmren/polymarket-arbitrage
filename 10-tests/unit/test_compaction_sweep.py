#!/usr/bin/env python3
"""判据焊死:compaction 从"只扫昨天一次"改成"扫任何文件数超阈值的分区"(2026-07-23 用户明令)。

背景(盲区):分区按**成交事件日期**分,巨盘历史回填(近端 8000 笔常跨多日)会把成交写进
**旧日期分区**;原 compaction 每天只合并"昨天"一次 → 被回填污染的旧分区小文件永久累积、
再不合并。实证 07-22 分区 00:05 已合并成 1 个大文件,之后又冒出 2545 个小文件。

新判据:`compact_due_partitions(min_files)` 扫全部 dt= 分区,把文件数 > min_files 的都收进来
(含被回填污染的旧分区),文件数 ≤ min_files 的不动(避免无谓重写)。

铁律:先写判据再改代码(见 CLAUDE.md)。测试用 REBIRTH_DATA 隔离到 tmp,绝不碰生产数据湖。
"""
import importlib
import sys
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

pytest.importorskip("duckdb")  # 去重校验用到 DuckDB;缺依赖则跳过而非误报失败


@pytest.fixture
def se(tmp_path, monkeypatch):
    """把数据湖隔离到 tmp,import 全新 storage_engine(读 REBIRTH_DATA)。"""
    monkeypatch.setenv("REBIRTH_DATA", str(tmp_path))
    import storage_engine
    importlib.reload(storage_engine)  # 让 DATA_ROOT/RAW_DIR 重新取 env
    assert str(tmp_path) in str(storage_engine.RAW_DIR)
    return storage_engine


def _row(i: int, ts: int) -> dict:
    """一条最小合法 trade(唯一 tx_hash → 不会被去重误合)。"""
    return {
        "transaction_hash": f"0x{i:064x}", "proxy_wallet": "0xw", "condition_id": "0xc",
        "asset": "1", "outcome_index": 0, "outcome_label": "Yes", "side": "BUY",
        "size": 1.0, "price": 0.5, "timestamp": ts, "ingested_at": ts,
    }


def _mk_files(se, day: str, n: int, ts: int) -> None:
    """在 dt=<day> 分区写 n 个独立小文件(每次 write_trades 落一个)。"""
    for i in range(n):
        se.write_trades([_row(i, ts)], day=day)


def _count(se, day: str) -> int:
    return len(list((se.RAW_DIR / f"dt={day}").glob("*.parquet")))


def test_sweep_compacts_over_threshold_partitions(se):
    """超阈值分区(含被回填污染的旧分区)都合并;阈值内的分区不动。"""
    _mk_files(se, "2026-07-20", 60, ts=1_752_000_000)   # 旧分区被回填污染 → 应合并
    _mk_files(se, "2026-07-21", 10, ts=1_752_100_000)   # 阈值内 → 应保持不动
    _mk_files(se, "2026-07-22", 55, ts=1_752_200_000)   # 超阈值 → 应合并

    compacted = se.compact_due_partitions(min_files=50)

    assert set(compacted) == {"2026-07-20", "2026-07-22"}, "只该合并超阈值的分区"
    assert _count(se, "2026-07-20") == 1, "旧污染分区应被收成 1 个文件"
    assert _count(se, "2026-07-22") == 1
    assert _count(se, "2026-07-21") == 10, "阈值内分区不该被重写"


def test_sweep_preserves_all_rows(se):
    """合并不丢数据:去重后行数 = 唯一 tx 数(此处全唯一)。"""
    _mk_files(se, "2026-07-20", 60, ts=1_752_000_000)
    se.compact_due_partitions(min_files=50)
    con = se.duckdb_conn()
    n = con.execute("SELECT count(*) FROM trades_deduped").fetchone()[0]
    assert n == 60, f"合并后应保留全部 60 笔,实得 {n}"


def test_sweep_noop_when_nothing_due(se):
    """所有分区都在阈值内 → 返回空,不做任何重写。"""
    _mk_files(se, "2026-07-20", 5, ts=1_752_000_000)
    assert se.compact_due_partitions(min_files=50) == []
    assert _count(se, "2026-07-20") == 5


def test_default_threshold_is_reasonable(se):
    """默认阈值存在且落在合理区间(>1 才有合并意义,别太大失去意义)。"""
    import inspect
    sig = inspect.signature(se.compact_due_partitions)
    default = sig.parameters["min_files"].default
    assert 1 < default <= 200
