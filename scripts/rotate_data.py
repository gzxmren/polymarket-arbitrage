#!/usr/bin/env python3
"""
数据文件轮转 (P0-7)

给 07-data/ 下无限增长的产物文件加保留策略，可由 cron 定期调用，防止文件堆积。

策略:
  - monitor_report_*.json: 按数量保留最新 N 个(默认 30),其余删除。
  - whale_states/*.json: 删除"非重点关注 且 超过 N 天未更新"的状态文件;
    重点关注鲸鱼(whale_watchlist.json)的 state 一律保留。

安全: 默认 dry-run,只列出将删除的文件;加 --apply 才真正删除。

用法:
    python3 scripts/rotate_data.py                       # 预演
    python3 scripts/rotate_data.py --apply               # 执行
    python3 scripts/rotate_data.py --keep-reports 50 --whale-days 60 --apply
"""

import argparse
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "07-data"
WHALE_STATES_DIR = DATA_DIR / "whale_states"
WATCHLIST_FILE = DATA_DIR / "whale_watchlist.json"


def rotate_reports(keep: int, apply: bool) -> int:
    reports = sorted(DATA_DIR.glob("monitor_report_*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    stale = reports[keep:]
    print(f"[monitor_report] 共 {len(reports)} 个,保留最新 {keep} 个,删除 {len(stale)} 个")
    for p in stale:
        if apply:
            p.unlink()
        else:
            print(f"    将删除: {p.name}")
    return len(stale)


def load_watched() -> set:
    if not WATCHLIST_FILE.exists():
        return set()
    try:
        data = json.loads(WATCHLIST_FILE.read_text())
        return set(data.get("whales", {}).keys())
    except (json.JSONDecodeError, OSError):
        return set()


def rotate_whale_states(days: int, apply: bool) -> int:
    if not WHALE_STATES_DIR.exists():
        print("[whale_states] 目录不存在,跳过")
        return 0
    watched = load_watched()
    cutoff = time.time() - days * 86400
    files = list(WHALE_STATES_DIR.glob("*.json"))
    stale = [p for p in files
             if p.stem not in watched and p.stat().st_mtime < cutoff]
    print(f"[whale_states] 共 {len(files)} 个,重点关注 {len(watched)} 个(保留),"
          f"删除非关注且超 {days} 天未更新 {len(stale)} 个")
    for p in stale:
        if apply:
            p.unlink()
        else:
            print(f"    将删除: {p.name}")
    return len(stale)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-reports", type=int, default=30,
                    help="monitor_report 保留最新数量(默认 30)")
    ap.add_argument("--whale-days", type=int, default=30,
                    help="whale_states 非关注文件保留天数(默认 30)")
    ap.add_argument("--apply", action="store_true", help="真正删除(默认仅预演)")
    args = ap.parse_args()

    print("=" * 56)
    print("数据文件轮转" + ("" if args.apply else " (dry-run)"))
    print("=" * 56)
    n1 = rotate_reports(args.keep_reports, args.apply)
    n2 = rotate_whale_states(args.whale_days, args.apply)
    print("-" * 56)
    action = "已删除" if args.apply else "将删除"
    print(f"{action}合计: {n1 + n2} 个文件")
    if not args.apply:
        print("加 --apply 执行实际删除。")


if __name__ == "__main__":
    main()
