#!/usr/bin/env python3
"""
Backfill whale pseudonyms from Polymarket Data API.

For each whale wallet, queries Data API trades endpoint to find the user's
pseudonym/name. Rate-limited to 5 req/s, retries on network errors.
"""

import sqlite3
import argparse
import sys
import time
import json
import urllib.request
from pathlib import Path
from datetime import datetime
import os

# Remove proxy env vars to avoid issues with urllib
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    try:
        del k
    except KeyError:
        pass

# Build a non-proxied opener
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({})
)

DB_PATH = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))
DATA_API = 'https://data-api.polymarket.com'

REQUEST_INTERVAL = 0.2  # 5 req/s max
MAX_RETRIES = 2
RETRY_DELAY = 5


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute('PRAGMA busy_timeout=60000')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def fetch_pseudonym(wallet: str) -> str | None:
    """
    Fetch pseudonym from Polymarket Data API trades endpoint.
    
    Uses the user parameter to find trades by this proxy wallet and extract
    the user's pseudonym or display name.
    """
    url = f'{DATA_API}/trades?user={wallet}&limit=1'
    
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'PolymarketMonitor/1.0'})
            with opener.open(req, timeout=15) as resp:
                trades = json.loads(resp.read().decode())
            
            if not trades:
                return None  # No trades found for this wallet
            
            trade = trades[0]
            pseudonym = trade.get('pseudonym', '') or ''
            name = trade.get('name', '') or ''
            
            # Prefer pseudonym, fall back to name if available
            # Skip if empty or '0x...' format
            if pseudonym and not pseudonym.startswith('0x'):
                return pseudonym
            if name and not name.startswith('0x'):
                return name
            return None
            
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt < MAX_RETRIES:
                print(f"      ⚠️  HTTP {e.code} for {wallet[:20]}..., retry {attempt+1}/{MAX_RETRIES}", flush=True)
                time.sleep(RETRY_DELAY)
            else:
                print(f"      ❌ HTTP {e.code} for {wallet[:20]}..., giving up", flush=True)
        except (urllib.error.URLError, json.JSONDecodeError, ConnectionError, TimeoutError) as e:
            if attempt < MAX_RETRIES:
                print(f"      ⚠️  Error for {wallet[:20]}...: {e}, retry {attempt+1}/{MAX_RETRIES}", flush=True)
                time.sleep(RETRY_DELAY)
            else:
                print(f"      ❌ Error for {wallet[:20]}...: {e}, giving up", flush=True)
        except Exception as e:
            print(f"      ❌ Unexpected error for {wallet[:20]}...: {e}", flush=True)
            return None
    
    return None


def main():
    parser = argparse.ArgumentParser(description='Backfill whale pseudonyms from Polymarket API')
    parser.add_argument('--batch', type=int, default=20, help='Batch size for transaction commits (default: 20)')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no writes')
    parser.add_argument('--wallet', type=str, help='Only process a specific wallet address')
    parser.add_argument('--force', action='store_true', help='Force overwrite existing names (default: only fill empty/bad ones)')
    parser.add_argument('--limit', type=int, default=0, help='Max number of wallets to process (0 = all)')
    parser.add_argument('--offset', type=int, default=0, help='Skip N wallets from the start (for resume support)')
    args = parser.parse_args()

    conn = get_db()
    cur = conn.cursor()

    # ── Query candidates ──
    if args.wallet:
        cur.execute('SELECT wallet, pseudonym FROM whales WHERE wallet = ?', (args.wallet,))
        rows = cur.fetchall()
        if not rows:
            print(f"❌ Wallet {args.wallet} not found in whales table.")
            conn.close()
            return
        candidate_label = f"specified wallet {args.wallet[:20]}..."
    elif args.force:
        # Force mode: update ALL whales with bad/empty names
        cur.execute("""
            SELECT wallet, pseudonym FROM whales 
            WHERE pseudonym IN ('unknown', '') 
               OR pseudonym IS NULL 
               OR pseudonym LIKE '0x%'
        """)
        rows = cur.fetchall()
        candidate_label = f"{len(rows):,} whales with empty/bad names"
    else:
        # Default: only those explicitly 'unknown' or empty or NULL (not 0x addresses)
        cur.execute("""
            SELECT wallet, pseudonym FROM whales 
            WHERE pseudonym IN ('unknown', '') OR pseudonym IS NULL
        """)
        rows = cur.fetchall()
        candidate_label = f"{len(rows):,} whales with 'unknown' or empty names"

    # Apply offset and limit
    if args.offset > 0:
        rows = rows[args.offset:]
        print(f"   (offset {args.offset} applied)")
    if args.limit > 0:
        rows = rows[:args.limit]
        candidate_label = f"{len(rows):,} whales (limited to {args.limit})"

    if not rows:
        print("✅ No whales need updating.")
        conn.close()
        return

    print(f"🎯 Found {candidate_label}")
    
    if args.dry_run:
        print(f"\n🔍 DRY RUN — No changes will be made.\n")
        for i, (wallet, current) in enumerate(rows):
            label = f"   [{i+1}/{len(rows)}] {wallet[:20]}...  current: {current}"
            if (i + 1) <= 30 or (i + 1) % 500 == 0 or i == len(rows) - 1:
                print(label, flush=True)
        print(f"\n🔍 DRY RUN complete: {len(rows):,} candidates")
        conn.close()
        return

    # ── Process ──
    total = len(rows)
    api_calls = 0
    updated = 0
    failed = 0
    start_time = time.time()
    batch_buffer = []

    print(f"\n🚀 Starting backfill (batch size: {args.batch})...\n")

    for i, (wallet, current_pseudonym) in enumerate(rows):
        pseudonym = fetch_pseudonym(wallet)
        api_calls += 1

        if pseudonym:
            batch_buffer.append((pseudonym, datetime.now().isoformat(), wallet))
            updated += 1
            print(f"   [{i+1}/{total}] ✅ {wallet[:20]}... → {pseudonym}", flush=True)
        else:
            failed += 1
            if (i + 1) % 100 == 0:
                print(f"   [{i+1}/{total}] ❌ ... no valid pseudonym (progress: {i+1}/{total})", flush=True)

        # Rate limit
        time.sleep(REQUEST_INTERVAL)

        # Flush batch
        if len(batch_buffer) >= args.batch:
            cur.executemany(
                "UPDATE whales SET pseudonym = ?, last_updated = ? WHERE wallet = ?",
                batch_buffer
            )
            conn.commit()
            elapsed = time.time() - start_time
            speed = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"      📦 Batch committed ({len(batch_buffer)} updates, {i+1}/{total} done, {speed:.1f}/s)\n", flush=True)
            batch_buffer = []

    # Final flush
    if batch_buffer:
        cur.executemany(
            "UPDATE whales SET pseudonym = ?, last_updated = ? WHERE wallet = ?",
            batch_buffer
        )
        conn.commit()

    # ── Final stats ──
    elapsed = time.time() - start_time
    cur.execute("SELECT COUNT(*) FROM whales WHERE pseudonym = 'unknown' OR pseudonym IS NULL OR pseudonym = ''")
    still_unknown = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM whales WHERE pseudonym LIKE '0x%'")
    still_0x = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM whales")
    total_whales = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM whales 
        WHERE pseudonym NOT IN ('unknown', '') 
          AND pseudonym IS NOT NULL 
          AND pseudonym NOT LIKE '0x%'
    """)
    named = cur.fetchone()[0]

    print(f"\n{'='*50}")
    print(f"📊 Final Statistics:")
    print(f"   {'Total whales:':<30} {total_whales:>10,}")
    print(f"   {'With good name:':<30} {named:>10,} ({named/total_whales*100:.1f}%)")
    print(f"   {'Still unknown/empty:':<30} {still_unknown:>10,} ({still_unknown/total_whales*100:.1f}%)")
    print(f"   {'Still 0x address>:':<30} {still_0x:>10,}")
    print(f"   {'':<30} {'─'*15}")
    print(f"   {'API calls made:':<30} {api_calls:>10,}")
    print(f"   {'Successfully updated:':<30} {updated:>10,}")
    print(f"   {'Failed (no valid name):':<30} {failed:>10,}")
    print(f"   {'API rate interval:':<30} {REQUEST_INTERVAL*1000:.0f}ms")
    print(f"   {'Elapsed time:':<30} {elapsed:.1f}s")
    if api_calls > 0:
        print(f"   {'Avg API speed:':<30} {elapsed/api_calls*1000:.0f}ms/call")
        speed = api_calls / elapsed if elapsed > 0 else 0
        print(f"   {'Throughput:':<30} {speed:.1f} calls/s")
    
    # Estimate total time
    if api_calls > 0 and api_calls < total:
        remaining = total - api_calls
        eta = remaining / (api_calls / elapsed) if api_calls / elapsed > 0 else 0
        print(f"   {'Estimated total time:':<30} {elapsed:.0f}s / ~{elapsed + eta:.0f}s")

    conn.close()

    # ── Summary for reporting ──
    print(f"\n{'-'*50}")
    print(f"SUMMARY: API calls={api_calls}, updated={updated}, failed={failed}")


if __name__ == '__main__':
    main()
