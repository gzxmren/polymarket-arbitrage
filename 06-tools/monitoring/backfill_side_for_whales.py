#!/usr/bin/env python3
"""
为 $10K+ 鲸鱼补全 changes 表的 side / tx_hash / change_amount 符号。

直接从 data-api 重新拉交易，用 transactionHash 精确匹配已有记录，
更新 side 字段和 change_amount 符号，然后重新计算 PnL。
"""

import sqlite3, json, urllib.request, time, argparse, sys
from datetime import datetime, timezone
from pathlib import Path

for k in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','ALL_PROXY','all_proxy']:
    try: del k
    except KeyError: pass

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
DATA_API = "https://data-api.polymarket.com"
DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'
INTERVAL = 0.25
RETRIES = 2

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute('PRAGMA busy_timeout=60000')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def fetch_trades(wallet: str, limit: int = 200) -> list:
    url = f'{DATA_API}/trades?user={wallet}&limit={limit}'
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'PolymarketMonitor/1.0'})
            with opener.open(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404: return []
            if attempt < RETRIES:
                time.sleep(3)
            else:
                return []
        except Exception:
            if attempt < RETRIES:
                time.sleep(3)
            else:
                return []
    return []

def process_wallet(db, wallet: str, trades: list) -> dict:
    """用 tx_hash 精确匹配并更新 changes 记录"""
    cur = db.cursor()

    # 库内所有 (tx_hash, id) 映射
    cur.execute("SELECT id, tx_hash, side FROM changes WHERE wallet = ?", (wallet,))
    existing = {row[1]: {'id': row[0], 'old_side': row[2]} for row in cur.fetchall() if len(row) >= 2}

    updated = 0
    matched = 0
    to_insert = []

    for t in trades:
        tx_hash = t.get('transactionHash', '') or ''
        side = t.get('side', 'BUY')
        size = float(t.get('size', 0))
        price = float(t.get('price', 0))
        change_amount = size * price
        if side == 'SELL':
            change_amount = -change_amount

        if abs(change_amount) < 10:
            continue

        if tx_hash and tx_hash in existing:
            rec = existing[tx_hash]
            matched += 1
            # 需要更新 side 和 change_amount？
            if rec['old_side'] != side or rec['old_side'] == 'BUY' or not rec['old_side']:
                cur.execute(
                    "UPDATE changes SET side = ?, change_amount = ?, tx_hash = ? WHERE id = ?",
                    (side, change_amount, tx_hash, rec['id'])
                )
                updated += 1
        elif tx_hash and tx_hash not in existing:
            # 新记录：准备插入
            market = t.get('slug', '')
            outcome = t.get('outcome', '')
            ts = t.get('timestamp', 0)
            timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()
            title = t.get('title', '')
            to_insert.append((
                wallet, 'trade', market, outcome, 0.0, size,
                change_amount, timestamp, title, side, tx_hash
            ))

    # 批量插入新记录
    inserted = 0
    if to_insert:
        cur.executemany("""
            INSERT OR IGNORE INTO changes
                (wallet, type, market, outcome, old_size, new_size,
                 change_amount, timestamp, market_title, side, tx_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, to_insert)
        inserted = len(to_insert)
        db.commit()

    return {'matched': matched, 'updated': updated, 'inserted': inserted, 'api_trades': len(trades)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--threshold', type=float, default=10000)
    parser.add_argument('--wallet', type=str)
    args = parser.parse_args()

    db = get_db()
    cur = db.cursor()

    if args.wallet:
        cur.execute("SELECT w.wallet, w.pseudonym, w.total_volume, c.cnt FROM whales w LEFT JOIN (SELECT wallet, COUNT(*) as cnt FROM changes GROUP BY wallet) c ON w.wallet = c.wallet WHERE w.wallet = ?", (args.wallet,))
        whales = cur.fetchall()
    else:
        cur.execute("""
            SELECT w.wallet, w.pseudonym, w.total_volume, COALESCE(c.cnt, 0)
            FROM whales w
            LEFT JOIN (SELECT wallet, COUNT(*) as cnt FROM changes GROUP BY wallet) c ON w.wallet = c.wallet
            WHERE w.total_volume > ?
            ORDER BY w.total_volume DESC
        """, (args.threshold,))
        whales = cur.fetchall()

    print(f"🎯 {len(whales)} whales > ${args.threshold:,.0f}")
    if args.dry_run:
        print("🔍 DRY RUN\n")

    total_matched = total_updated = total_inserted = api_calls = 0
    start = time.time()

    for i, (wallet, pseudonym, vol, chg_cnt) in enumerate(whales):
        label = (pseudonym or wallet[:16])[:20]
        print(f"  [{i+1}/{len(whales)}] {wallet[:16]}... ({label}) vol=${vol:,.0f}", end='', flush=True)

        trades = fetch_trades(wallet)
        api_calls += 1

        if not trades:
            print(" → 0 trades", flush=True)
            time.sleep(INTERVAL)
            continue

        if args.dry_run:
            sides = {}
            for t in trades: sides[t.get('side','?')] = sides.get(t.get('side','?'), 0) + 1
            print(f" → {len(trades)} trades (B={sides.get('BUY',0)} S={sides.get('SELL',0)})", flush=True)
            time.sleep(INTERVAL)
            continue

        result = process_wallet(db, wallet, trades)
        db.commit()

        total_matched += result['matched']
        total_updated += result['updated']
        total_inserted += result['inserted']

        print(f" → {result['api_trades']} trades, matched={result['matched']}, upd={result['updated']}, ins={result['inserted']}", flush=True)
        time.sleep(INTERVAL)

    if not args.dry_run:
        print(f"\n🔄 重新计算 $10K+ 鲸鱼 PnL ...", flush=True)
        cur.execute("""
            SELECT wallet, SUM(change_amount)
            FROM changes
            WHERE wallet IN (SELECT wallet FROM whales WHERE total_volume > ?)
            GROUP BY wallet
        """, (args.threshold,))
        for wallet, cost in cur.fetchall():
            pnl = -cost if cost else 0
            cur.execute("UPDATE whales SET total_pnl = ? WHERE wallet = ?", (pnl, wallet))
        db.commit()

    elapsed = time.time() - start

    # 最终统计
    cur.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN total_volume > 10000 AND total_pnl > 0 THEN 1 ELSE 0 END) as profit,
            SUM(CASE WHEN total_volume > 10000 AND total_pnl < 0 THEN 1 ELSE 0 END) as loss,
            SUM(CASE WHEN total_volume > 10000 AND total_pnl = 0 THEN 1 ELSE 0 END) as zero,
            ROUND(AVG(CASE WHEN total_volume > 10000 THEN total_pnl ELSE NULL END), 2) as avg_pnl
        FROM whales
    """)
    s = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM changes WHERE side = 'SELL'")
    sell_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM changes WHERE tx_hash != ''")
    tx_count = cur.fetchone()[0]

    print(f"\n{'='*50}")
    print(f"📊 完成统计:")
    print(f"   处理鲸鱼: {len(whales)}")
    print(f"   API 调用: {api_calls}")
    print(f"   匹配: {total_matched} | 更新: {total_updated} | 插入: {total_inserted}")
    print(f"   总 SELL 记录: {sell_count}")
    print(f"   总 tx_hash 记录: {tx_count}")
    print(f"   大户 PnL: profit={s[1]} loss={s[2]} zero={s[3]} avg={s[4]}")
    print(f"   耗时: {elapsed:.1f}s")
    db.close()

if __name__ == '__main__':
    main()
