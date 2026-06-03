#!/usr/bin/env python3
"""
鲸鱼垃圾数据清洗 (P0-3 / P0-4)

清洗 whales 表中的空行(无持仓且无价值且非重点关注)，并 VACUUM 回收死空间。
与 dashboard/backend/app/services/data_sync.py 的价值闸门条件保持一致。

安全设计:
  1. 默认 dry-run，只预演不删除。加 --apply 才真正执行。
  2. --apply 前自动备份数据库到 dashboard/backend/database/backups/。
  3. DELETE 包在事务里，失败回滚。
  4. 删除前后打印计数，并校验真鲸鱼(>$100k)数量不变(防误删)。

用法:
    python3 scripts/cleanup_whales.py            # 预演(dry-run)
    python3 scripts/cleanup_whales.py --apply    # 备份→清洗→VACUUM
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "dashboard" / "backend" / "database" / "polymarket.db"
BACKUP_DIR = DB_PATH.parent / "backups"

# 垃圾判定条件: 无价值 且 无持仓 且 非重点关注 且 无成交流水。
# 注意 changes_count/total_volume 这两条: sync_changes.py 会从实时成交流抓到"有交易
# 但暂无持仓快照"的 trade-flow 鲸鱼(跟鲸鱼策略的信号源),它们 has_activity=1 是正确的,
# 不算垃圾。真·空壳(7.2万)是全 0 且无任何成交记录,只删这种。
JUNK_WHERE = (
    "COALESCE(total_value,0)<=0 AND COALESCE(position_count,0)=0 "
    "AND COALESCE(is_watched,0)=0 "
    "AND COALESCE(changes_count,0)=0 AND COALESCE(total_volume,0)=0"
)


def counts(cur):
    total = cur.execute("SELECT COUNT(*) FROM whales").fetchone()[0]
    junk = cur.execute(f"SELECT COUNT(*) FROM whales WHERE {JUNK_WHERE}").fetchone()[0]
    over_100k = cur.execute("SELECT COUNT(*) FROM whales WHERE total_value>100000").fetchone()[0]
    return total, junk, over_100k


def backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"polymarket.db.{ts}"
    shutil.copy2(DB_PATH, dest)
    print(f"💾 已备份: {dest}")
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行清洗+VACUUM(默认仅预演)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(2)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    total, junk, over_100k = counts(cur)
    print(f"清洗前: whales={total}  垃圾={junk}  真鲸鱼(>$100k)={over_100k}")
    print(f"将删除 {junk} 行,预计保留 {total - junk} 行。")

    if not args.apply:
        print("\n[dry-run] 未做任何改动。确认无误后加 --apply 执行。")
        conn.close()
        return

    conn.close()
    backup()

    # 重新连接执行,事务包裹
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute(f"DELETE FROM whales WHERE {JUNK_WHERE}")
        deleted = cur.rowcount
        conn.commit()
        print(f"🧹 已删除 {deleted} 行。")
    except sqlite3.Error as e:
        conn.rollback()
        print(f"❌ 清洗失败,已回滚: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # 防误删校验: 真鲸鱼数量不应变化
    total_after, junk_after, over_100k_after = counts(cur)
    if over_100k_after != over_100k:
        print(f"⚠️  警告: 真鲸鱼数量变化 {over_100k} → {over_100k_after},请检查备份!",
              file=sys.stderr)
    print(f"清洗后: whales={total_after}  垃圾={junk_after}  真鲸鱼(>$100k)={over_100k_after}")

    # VACUUM 回收死空间(独占锁,需在停机窗口执行)
    print("🗜️  VACUUM 回收死空间中...")
    cur.execute("VACUUM")
    conn.close()
    print("✅ 完成。建议运行: python3 scripts/data_health_check.py")


if __name__ == "__main__":
    main()
