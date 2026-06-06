#!/usr/bin/env python3
"""
Backfill changes table titles from market_title_map + cleanup expired positions

1. Build title map from: market_title_map table + changes that already have titles
2. Backfill all changes entries missing titles
3. Delete expired positions (>30 days past end_date)
"""

import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from monitor_utils import slug_to_title
import os

DB_PATH = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute('PRAGMA busy_timeout=60000')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def backfill_titles():
    """Backfill missing market_title in changes table using all available title sources"""
    conn = get_db()
    cur = conn.cursor()

    # Step 1: Build comprehensive title map from ALL sources
    title_map = {}

    # Source A: market_title_map table
    cur.execute('SELECT slug, title FROM market_title_map WHERE title IS NOT NULL AND title != ""')
    for slug, title in cur.fetchall():
        title_map[slug] = title
    print(f"  market_title_map entries: {len(title_map)}")

    # Source B: changes that already have titles
    cur.execute('''SELECT DISTINCT market, market_title FROM changes 
        WHERE market_title IS NOT NULL AND market_title != ""''')
    added_from_changes = 0
    for market, title in cur.fetchall():
        if market not in title_map:
            title_map[market] = title
            added_from_changes += 1
    print(f"  Additional from changes with titles: {added_from_changes}")
    print(f"  Total title map: {len(title_map)}")

    # Step 2: Backfill changes without titles using map + slug derivation
    cur.execute('''SELECT id, market FROM changes 
        WHERE market_title IS NULL OR market_title = ""''')
    to_backfill = cur.fetchall()
    print(f"  Changes missing titles: {len(to_backfill)}")

    backfilled_from_map = 0
    backfilled_from_slug = 0
    skipped = 0

    for row_id, market in to_backfill:
        if market in title_map:
            cur.execute('UPDATE changes SET market_title = ? WHERE id = ?', (title_map[market], row_id))
            backfilled_from_map += 1
        else:
            derived = slug_to_title(market)
            if derived:
                cur.execute('UPDATE changes SET market_title = ? WHERE id = ?', (derived, row_id))
                backfilled_from_slug += 1
                title_map[market] = derived
            else:
                skipped += 1

    conn.commit()

    # Step 3: Report
    cur.execute('''SELECT COUNT(*) FROM changes 
        WHERE market_title IS NOT NULL AND market_title != ""''')
    with_title_now = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM changes')
    total = cur.fetchone()[0]

    print(f"  ✅ From map: {backfilled_from_map}, From slug: {backfilled_from_slug}, Skipped: {skipped}")
    print(f"  📊 Title coverage: {with_title_now}/{total} ({with_title_now/total*100:.1f}%)")

    conn.close()
    return backfilled_from_map + backfilled_from_slug


def cleanup_expired_positions(days_past=30):
    """Delete positions that expired more than N days ago"""
    conn = get_db()
    cur = conn.cursor()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_past)).strftime('%Y-%m-%d')

    # Count first
    cur.execute('SELECT COUNT(*) FROM positions WHERE is_expired = 1')
    expired_total = cur.fetchone()[0]

    cur.execute('SELECT COUNT(*) FROM positions WHERE is_expired = 1 AND (end_date < ? OR end_date IS NULL OR end_date = "")', (cutoff,))
    to_delete = cur.fetchone()[0]

    cur.execute('SELECT COUNT(*) FROM positions')
    total = cur.fetchone()[0]

    print(f"  Expired positions: {expired_total}/{total}")
    print(f"  Expired >{days_past}d ago (to delete): {to_delete}")

    if to_delete > 0:
        # Delete in batches using subquery (compatible with all SQLite versions)
        batch_size = 5000
        deleted = 0
        while True:
            cur.execute('''DELETE FROM positions WHERE rowid IN (
                SELECT rowid FROM positions 
                WHERE is_expired = 1 AND (end_date < ? OR end_date IS NULL OR end_date = "") 
                LIMIT ?
            )''', (cutoff, batch_size))
            batch_deleted = cur.rowcount
            deleted += batch_deleted
            conn.commit()
            if batch_deleted < batch_size:
                break
            print(f"    Deleted batch: {deleted}/{to_delete}...")

        print(f"  ✅ Deleted: {deleted} expired positions")

    cur.execute('SELECT COUNT(*) FROM positions')
    remaining = cur.fetchone()[0]
    print(f"  📊 Remaining positions: {remaining}")

    conn.close()
    return to_delete


def main():
    print("🔧 Backfill titles + cleanup positions")
    print("=" * 50)

    print("\n📖 Step 1: Backfill titles")
    backfilled = backfill_titles()

    print("\n🧹 Step 2: Cleanup expired positions")
    cleaned = cleanup_expired_positions(days_past=30)

    print(f"\n✅ Done! Backfilled {backfilled} titles, cleaned {cleaned} positions")


if __name__ == "__main__":
    main()
