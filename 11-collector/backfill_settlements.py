#!/usr/bin/env python3
"""backfill_settlements.py — 一次性把积压的结算真值扫回来。

背景:结算守望因队头阻塞连续 11 天产出 0 条真值,积压 37,782 个已到期未取真值的市场
(详见 settlement_watcher.py 顶部大修说明)。修复后守望每轮 800 个、约 8 小时能自然扫完
一圈;本脚本把这件事压缩到 ~30 分钟,好让"攒够结算盘"的闸门早点开。

**刻意做成 watch_settlements 的薄驱动**,不另写一套查询/解析逻辑 —— 两套实现必然分叉,
而分叉的那套一定是没被测试盖住的那套(本项目已有 data_sync / data_sync_v2 权威不明的前科)。
判据:10-tests/unit/test_settlement_backfill.py

并发说明:systemd timer 每 10 分钟也会调 watch_settlements,与本脚本共享同一个游标文件。
两者交替推进游标是**安全**的 —— 最坏情况是本圈跳过少数市场,下一圈会扫到(游标回卷设计),
不会丢数据、不会重复写坏(注册表 append-only + 取 snapshot_at 最新版)。
"""
from __future__ import annotations

import argparse
import time

import settlement_watcher


def _default_report(i: int, out: dict, elapsed: float) -> None:
    print(f"  第 {i:>3} 轮  检查 {out['checked']:>4}  新结算 {out['newly_resolved']:>4}  "
          f"查询失败 {out['lookup_fail']:>3}  剩余 pending {out['pending_settlement']:>6}  "
          f"{elapsed:.0f}s", flush=True)


def backfill(max_rounds: int = 200, watch=None, on_round=_default_report) -> dict:
    """反复调用 watch_settlements 直到轮完一圈(或到硬上限)。返回累计计数。

    停止条件三选一,任一满足即停:
      1. 累计检查数 ≥ 起始 pending —— 已完整轮过一圈(游标会回卷,不设此条即死循环);
      2. 某轮 checked=0 —— 已无待查市场;
      3. 达到 max_rounds 硬上限。
    """
    watch = watch or settlement_watcher.watch_settlements
    total = {"rounds": 0, "checked": 0, "newly_resolved": 0, "lookup_fail": 0}
    start_pending = None
    while total["rounds"] < max_rounds:
        t0 = time.time()
        out = watch()
        total["rounds"] += 1
        for k in ("checked", "newly_resolved", "lookup_fail"):
            total[k] += out[k]
        if on_round:
            on_round(total["rounds"], out, time.time() - t0)
        if start_pending is None:
            start_pending = out["pending_settlement"]
        if out["checked"] == 0:
            break                                   # 已无待查
        if total["checked"] >= start_pending:
            break                                   # 已轮完一圈
    total["start_pending"] = start_pending or 0
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="一次性回填积压的结算真值")
    ap.add_argument("--max-rounds", type=int, default=200, help="硬上限轮数")
    args = ap.parse_args()

    t0 = time.time()
    print("=== 结算真值存量回填开始 ===", flush=True)
    r = backfill(max_rounds=args.max_rounds)
    print(f"=== 完成:{r['rounds']} 轮 / 检查 {r['checked']} / "
          f"**新增结算真值 {r['newly_resolved']}** / 查询失败 {r['lookup_fail']} / "
          f"起始 pending {r['start_pending']} / 用时 {(time.time()-t0)/60:.1f} 分钟 ===", flush=True)
