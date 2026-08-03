#!/usr/bin/env python3
"""判据焊死:存量结算回填驱动(2026-08-03)。

回填脚本刻意做成 `watch_settlements` 的**薄驱动**而非并行实现 —— 两套实现必然分叉,
而分叉的那一套一定是没被测试盖住的那套(本项目已有前科:data_sync 与 data_sync_v2 并存、
权威不明)。故这里只检验"循环与停止条件"本身。

停止条件的要害:必须在**轮完一圈**后停,不能无限跑(游标会回卷 → 永远有得查 → 死循环)。
"""
import sys
from pathlib import Path

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import backfill_settlements as bf  # noqa: E402


def _fake_watch(script):
    """按剧本逐轮返回 watch_settlements 的计数。"""
    it = iter(script)

    def watch():
        return next(it)

    return watch


def test_stops_after_one_full_pass():
    """扫过的数量达到起始 pending 即停 —— 游标会回卷,不停就是死循环。"""
    script = [{"pending_settlement": 300, "checked": 100, "newly_resolved": 50, "lookup_fail": 0}
              for _ in range(10)]
    out = bf.backfill(watch=_fake_watch(script), on_round=lambda *_: None)
    assert out["rounds"] == 3, f"起始 pending=300、每轮 100 → 应 3 轮停,实为 {out['rounds']}"


def test_stops_when_nothing_left():
    """pending 清空(checked=0)必须立刻停,不空转。"""
    script = [{"pending_settlement": 0, "checked": 0, "newly_resolved": 0, "lookup_fail": 0}]
    out = bf.backfill(watch=_fake_watch(script), on_round=lambda *_: None)
    assert out["rounds"] == 1


def test_respects_max_rounds():
    """硬上限必须生效(兜底:任何意外都不该让它无限跑)。"""
    script = [{"pending_settlement": 10 ** 9, "checked": 100, "newly_resolved": 1,
               "lookup_fail": 0} for _ in range(50)]
    out = bf.backfill(max_rounds=5, watch=_fake_watch(script), on_round=lambda *_: None)
    assert out["rounds"] == 5


def test_counts_are_accumulated_loudly():
    """逐轮计数必须累加返回(铁律 §3:任何剔除/失败都要出声,不得静默)。"""
    script = [{"pending_settlement": 200, "checked": 100, "newly_resolved": 60, "lookup_fail": 3},
              {"pending_settlement": 140, "checked": 100, "newly_resolved": 40, "lookup_fail": 2}]
    out = bf.backfill(watch=_fake_watch(script), on_round=lambda *_: None)
    assert out["newly_resolved"] == 100
    assert out["lookup_fail"] == 5
    assert out["checked"] == 200
