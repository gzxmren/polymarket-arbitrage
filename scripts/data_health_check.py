#!/usr/bin/env python3
"""
数据健康检查 (P0-7 / P0 验收工具)

对运行库 dashboard/backend/database/polymarket.db 跑一组断言式健康指标，
量化数据腐化程度。可单独运行查看报告，也可用 --assert 在 CI/cron 中做闸门
(任一红线指标不达标则退出码非零)。

用法:
    python3 scripts/data_health_check.py            # 打印报告
    python3 scripts/data_health_check.py --assert   # 不达标则 exit 1
    python3 scripts/data_health_check.py --json      # 输出 JSON
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "dashboard" / "backend" / "database" / "polymarket.db"

# 红线阈值(健康标准)
JUNK_RATE_MAX = 0.05      # whales 垃圾率上限 5%
FREELIST_RATE_MAX = 0.10  # DB 死空间上限 10%

# 垃圾判定条件，必须与 scripts/cleanup_whales.py 的 JUNK_WHERE 保持一致:
# 无价值 且 无持仓 且 非重点关注 且 无成交流水(changes_count/total_volume 均为 0)。
JUNK_WHERE = (
    "COALESCE(total_value,0)<=0 AND COALESCE(position_count,0)=0 "
    "AND COALESCE(is_watched,0)=0 "
    "AND COALESCE(changes_count,0)=0 AND COALESCE(total_volume,0)=0"
)


def _scalar(cur, sql, default=0):
    try:
        row = cur.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def collect(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    page_count = _scalar(cur, "PRAGMA page_count")
    freelist = _scalar(cur, "PRAGMA freelist_count")
    page_size = _scalar(cur, "PRAGMA page_size")

    whales_total = _scalar(cur, "SELECT COUNT(*) FROM whales")
    whales_junk = _scalar(cur, f"SELECT COUNT(*) FROM whales WHERE {JUNK_WHERE}")
    whales_100k = _scalar(cur, "SELECT COUNT(*) FROM whales WHERE total_value>100000")
    # "垃圾"= 无价值/无持仓/非关注 且 无成交流水(与 cleanup_whales.py 同义)。
    # 有 changes_count/total_volume 的是 trade-flow 鲸鱼(有交易、暂无持仓快照),合法,
    # 它们 has_activity=1 正确,不算错标。重点关注鲸鱼即使空仓也保留。
    activity_on_junk = _scalar(
        cur, f"SELECT COUNT(*) FROM whales WHERE has_activity=1 AND {JUNK_WHERE}"
    )

    conn.close()

    junk_rate = (whales_junk / whales_total) if whales_total else 0.0
    freelist_rate = (freelist / page_count) if page_count else 0.0

    return {
        "db_size_mb": round(page_count * page_size / 1e6, 1),
        "freelist_pages": freelist,
        "freelist_rate": round(freelist_rate, 4),
        "whales_total": whales_total,
        "whales_junk": whales_junk,
        "junk_rate": round(junk_rate, 4),
        "whales_over_100k": whales_100k,
        "has_activity_on_junk": activity_on_junk,
    }


def evaluate(m: dict) -> list:
    """返回 [(name, ok, detail), ...] 的检查结果。"""
    return [
        ("whales 垃圾率", m["junk_rate"] <= JUNK_RATE_MAX,
         f"{m['junk_rate']*100:.1f}% (上限 {JUNK_RATE_MAX*100:.0f}%)"),
        ("DB 死空间率", m["freelist_rate"] <= FREELIST_RATE_MAX,
         f"{m['freelist_rate']*100:.1f}% (上限 {FREELIST_RATE_MAX*100:.0f}%)"),
        ("has_activity 错标", m["has_activity_on_junk"] == 0,
         f"{m['has_activity_on_junk']} 行垃圾被标为有活动 (应为 0)"),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assert", dest="do_assert", action="store_true",
                    help="任一红线不达标则 exit 1")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(2)

    metrics = collect(DB_PATH)
    checks = evaluate(metrics)

    if args.json:
        print(json.dumps({"metrics": metrics,
                          "checks": [{"name": n, "ok": ok, "detail": d}
                                     for n, ok, d in checks]},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 56)
        print("数据健康检查报告")
        print("=" * 56)
        print(f"DB 大小:        {metrics['db_size_mb']} MB")
        print(f"whales 总行数:  {metrics['whales_total']}  (>$100k: {metrics['whales_over_100k']})")
        print("-" * 56)
        for name, ok, detail in checks:
            print(f"  [{'✅' if ok else '🔴'}] {name}: {detail}")
        print("=" * 56)

    if args.do_assert and not all(ok for _, ok, _ in checks):
        sys.exit(1)


if __name__ == "__main__":
    main()
