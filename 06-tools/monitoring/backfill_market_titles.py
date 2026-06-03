#!/usr/bin/env python3
"""
Backfill market titles in the changes table.

Two data sources (tried in order):
1. market_title_map table (existing slug→title mappings)
2. slug_to_title() from monitor_utils.py (slug derivation)

Fallback: mark remaining empty titles as 'unknown'.
"""

import sqlite3
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from monitor_utils import slug_to_title

DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute('PRAGMA busy_timeout=60000')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def main():
    parser = argparse.ArgumentParser(description='Backfill missing market titles in changes table')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no writes')
    args = parser.parse_args()

    conn = get_db()
    cur = conn.cursor()

    # ── 1. Count empty titles ──
    cur.execute("SELECT COUNT(*) FROM changes")
    total_all = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM changes WHERE market_title IS NULL OR market_title = ''")
    total_empty = cur.fetchone()[0]

    print(f"📊 Changes total: {total_all:,}, empty titles: {total_empty:,}\n")

    if total_empty == 0:
        print("✅ All changes already have titles. Nothing to do.")
        conn.close()
        return

    # ── 2. Get list of empty records ──
    cur.execute("SELECT id, market FROM changes WHERE market_title IS NULL OR market_title = ''")
    rows = cur.fetchall()
    total = len(rows)

    # Pre-compute counts for dry-run / preview
    cur.execute("SELECT slug FROM market_title_map WHERE title IS NOT NULL AND title != ''")
    map_slugs = set(row[0] for row in cur.fetchall())

    map_count = sum(1 for _, market in rows if market in map_slugs)
    slug_count = 0
    unknown_count = 0
    for _, market in rows:
        if market not in map_slugs:
            derived = slug_to_title(market)
            if derived:
                slug_count += 1
            else:
                unknown_count += 1

    print(f"🔍 Breakdown of {total:,} empty records:")
    print(f"   Can be filled from title_map:  {map_count:,}")
    print(f"   Can be filled from slug:       {slug_count:,}")
    print(f"   Will be marked 'unknown':      {unknown_count:,}")

    if args.dry_run:
        print(f"\n🔍 DRY RUN — No changes made.")
        conn.close()
        return

    # ── Phase A: market_title_map ──
    print(f"\nPhase A: Backfill from market_title_map ...", flush=True)
    cur.execute("""
        UPDATE changes SET market_title = (
            SELECT title FROM market_title_map
            WHERE market_title_map.slug = changes.market
              AND title IS NOT NULL AND title != ''
        )
        WHERE (market_title IS NULL OR market_title = '')
        AND EXISTS (
            SELECT 1 FROM market_title_map
            WHERE market_title_map.slug = changes.market
              AND title IS NOT NULL AND title != ''
        )
    """)
    from_map = cur.rowcount
    conn.commit()
    print(f"   ✅ {from_map:,} titles backfilled from market_title_map", flush=True)

    # ── Phase B: slug_to_title() ──
    cur.execute("SELECT id, market FROM changes WHERE market_title IS NULL OR market_title = ''")
    remaining = cur.fetchall()
    print(f"\nPhase B: Backfill from slug_to_title() ({len(remaining):,} remaining) ...", flush=True)

    from_slug = 0
    slug_updates = []
    unknown_updates = []
    for i, (row_id, market) in enumerate(remaining):
        derived = slug_to_title(market)
        if derived:
            slug_updates.append((derived, row_id))
            from_slug += 1
        else:
            unknown_updates.append(('unknown', row_id))

        # Flush in batches of 1000
        if len(slug_updates) >= 1000:
            cur.executemany("UPDATE changes SET market_title = ? WHERE id = ?", slug_updates)
            slug_updates = []
        if len(unknown_updates) >= 1000:
            cur.executemany("UPDATE changes SET market_title = ? WHERE id = ?", unknown_updates)
            unknown_updates = []

        if (i + 1) % 1000 == 0:
            conn.commit()
            print(f"   ... {i+1:,}/{len(remaining):,}", flush=True)

    # Final commit + flush remainder
    if slug_updates:
        cur.executemany("UPDATE changes SET market_title = ? WHERE id = ?", slug_updates)
    if unknown_updates:
        cur.executemany("UPDATE changes SET market_title = ? WHERE id = ?", unknown_updates)
    conn.commit()

    unknown_marked = len(remaining) - from_slug
    print(f"   ✅ Slug-derived: {from_slug:,}, Marked unknown: {unknown_marked:,}", flush=True)

    # ── Final stats ──
    cur.execute("SELECT COUNT(*) FROM changes WHERE market_title IS NULL OR market_title = ''")
    still_empty = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM changes WHERE market_title = 'unknown'")
    unknown_total = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM changes 
        WHERE market_title IS NOT NULL 
          AND market_title != ''
          AND market_title != 'unknown'
    """)
    has_real_title = cur.fetchone()[0]

    print(f"\n📊 Final Statistics:")
    print(f"   {'Total changes:':<30} {total_all:>10,}")
    print(f"   {'With real title:':<30} {has_real_title:>10,} ({has_real_title/total_all*100:.1f}%)")
    print(f"   {'Marked as unknown:':<30} {unknown_total:>10,} ({unknown_total/total_all*100:.1f}%)")
    print(f"   {'Still empty (should be 0):':<30} {still_empty:>10,}")
    print(f"   {'───':<30} {'─'*15}")
    print(f"   {'Backfilled from title_map:':<30} {from_map:>10,}")
    print(f"   {'Backfilled from slug:':<30} {from_slug:>10,}")

    conn.close()


if __name__ == '__main__':
    main()
