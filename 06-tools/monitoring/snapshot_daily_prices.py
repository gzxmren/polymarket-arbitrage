#!/usr/bin/env python3
"""
每日价格快照采集 — 从 Gamma API 获取所有活跃事件市场的 Yes 价格

设计原则：
- 数据写入 polymarket.db 的 daily_price_snapshots 表
- 每天采集一次，同一天重复跑会覆盖（INSERT OR REPLACE）
- 排除 btc-updown-* 和体育市场
- autoresearch-core 从此表读取，不自行采集

用法:
    python3 snapshot_daily_prices.py              # 默认采集
    python3 snapshot_daily_prices.py --dry-run    # 只预览不写入
"""

import os
import sys
import json
import urllib.request
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# 清除代理环境变量
for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(_k, None)

_no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DB_PATH = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))
GAMMA_API = 'https://gamma-api.polymarket.com'

# 排除的市场 slug 前缀
EXCLUDED_PREFIXES = [
    'btc-updown-',
    'nba-',
    'nhl-',
    'mlb-',
    'nfl-',
    'mls-',
    'ufc-',
]


def ensure_table(conn):
    """确保 daily_price_snapshots 表存在"""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS daily_price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'Yes',
            price REAL NOT NULL,
            volume_24h REAL DEFAULT 0,
            volume_total REAL DEFAULT 0,
            end_date TEXT,
            snapshot_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(market, outcome, snapshot_date)
        )
    ''')
    # 创建索引加速查询
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_snapshots_market_date
        ON daily_price_snapshots(market, snapshot_date)
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_snapshots_date
        ON daily_price_snapshots(snapshot_date)
    ''')
    conn.commit()


def fetch_active_markets(limit=100, offset=0):
    """从 Gamma API 获取活跃市场列表"""
    url = f'{GAMMA_API}/markets?closed=false&order=volume24hr&ascending=false&limit={limit}&offset={offset}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'PolymarketMonitor/1.0'})
        with _no_proxy_opener.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f'❌ 获取市场失败 (offset={offset}): {e}', flush=True)
        return []


def should_exclude(slug):
    """判断市场是否应被排除"""
    if not slug:
        return True
    slug_lower = slug.lower()
    return any(slug_lower.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def collect_all_markets(max_pages=20):
    """分页获取所有活跃市场"""
    all_markets = []
    for page in range(max_pages):
        markets = fetch_active_markets(limit=100, offset=page * 100)
        if not markets:
            break
        all_markets.extend(markets)
        if len(markets) < 100:
            break  # 最后一页
    return all_markets


def snapshot(dry_run=False):
    """
    执行一次价格快照采集

    Returns:
        dict: {"total": int, "saved": int, "skipped": int, "errors": int}
    """
    stats = {"total": 0, "saved": 0, "skipped": 0, "errors": 0}
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # 获取市场数据
    print(f'📡 从 Gamma API 获取活跃市场...', flush=True)
    markets = collect_all_markets()
    print(f'   获取 {len(markets)} 个市场', flush=True)

    if not markets:
        print('⚠️  无市场数据', flush=True)
        return stats

    # 过滤并提取价格
    records = []
    for m in markets:
        slug = m.get('slug', '')
        if should_exclude(slug):
            stats['skipped'] += 1
            continue

        # 解析 Yes 价格
        outcome_prices_raw = m.get('outcomePrices', '[]')
        try:
            if isinstance(outcome_prices_raw, str):
                outcome_prices = json.loads(outcome_prices_raw)
            else:
                outcome_prices = outcome_prices_raw
            yes_price = float(outcome_prices[0]) if outcome_prices else None
        except (json.JSONDecodeError, ValueError, IndexError, TypeError):
            stats['errors'] += 1
            continue

        if yes_price is None or not (0 < yes_price < 1):
            stats['skipped'] += 1
            continue

        # 提取其他字段
        volume_24h = float(m.get('volume24hr', 0) or 0)
        volume_total = float(m.get('volume', 0) or 0)
        end_date = m.get('endDateIso', '')

        records.append({
            'market': slug,
            'outcome': 'Yes',
            'price': yes_price,
            'volume_24h': volume_24h,
            'volume_total': volume_total,
            'end_date': end_date,
            'snapshot_date': today,
        })
        stats['total'] += 1

    print(f'   有效市场: {stats["total"]}, 跳过: {stats["skipped"]}, 解析错误: {stats["errors"]}', flush=True)

    if dry_run:
        print(f'🔍 [DRY RUN] 将写入 {len(records)} 条快照', flush=True)
        # 显示前5条
        for r in records[:5]:
            print(f'   {r["market"][:50]:50s} price={r["price"]:.4f} vol24h=${r["volume_24h"]:,.0f}')
        return stats

    # 写入数据库
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute('PRAGMA busy_timeout=60000')
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except sqlite3.OperationalError:
        pass

    ensure_table(conn)

    saved = 0
    try:
        for r in records:
            conn.execute('''
                INSERT OR REPLACE INTO daily_price_snapshots
                    (market, outcome, price, volume_24h, volume_total, end_date, snapshot_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                r['market'], r['outcome'], r['price'],
                r['volume_24h'], r['volume_total'], r['end_date'],
                r['snapshot_date'],
            ))
            saved += 1
        conn.commit()
        stats['saved'] = saved
    except sqlite3.OperationalError as e:
        print(f'❌ 数据库写入失败: {e}', flush=True)
    finally:
        conn.close()

    print(f'✅ 写入 {saved} 条快照 (日期: {today})', flush=True)
    return stats


def main():
    dry_run = '--dry-run' in sys.argv
    if dry_run:
        print('🔍 DRY RUN 模式 — 只预览不写入', flush=True)

    stats = snapshot(dry_run=dry_run)

    if not dry_run and stats['saved'] > 0:
        # 验证
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute('''
                SELECT COUNT(*), COUNT(DISTINCT market), MIN(snapshot_date), MAX(snapshot_date)
                FROM daily_price_snapshots
            ''').fetchone()
            print(f'📊 累计: {row[0]} 条快照, {row[1]} 个市场, 日期范围: {row[2]} ~ {row[3]}')
        except Exception as e:
            print(f'⚠️  验证查询失败: {e}', flush=True)
        finally:
            if conn:
                conn.close()


if __name__ == '__main__':
    main()
