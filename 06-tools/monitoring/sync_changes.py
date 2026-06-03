#!/usr/bin/env python3
"""
同步鲸鱼变动数据到 changes 表
定期抓取 Polymarket 数据并保存变动记录
"""

import os
import sys
import json
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

# 清除代理环境变量,避免 urllib 挂起
for _k in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','ALL_PROXY','all_proxy']:
    os.environ.pop(_k, None)

# 创建无代理的 opener(比清除 env 更可靠)
_no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

sys.path.insert(0, str(Path(__file__).parent))
from monitor_utils import slug_to_title as _slug_to_title

# 导入 PnL 计算模块
import importlib
_pnl_module = None
def _get_pnl_module():
    global _pnl_module
    if _pnl_module is None:
        _pnl_module = importlib.import_module('backfill_pnl')
    return _pnl_module

# 直接使用绝对路径的数据库
import sqlite3
DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'

def get_db_connection(retries=3):
    """获取数据库连接,带重试逻辑"""
    import time
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.execute('PRAGMA busy_timeout=60000')
            # 尝试启用 WAL 模式(可能被其他进程锁定)
            try:
                conn.execute('PRAGMA journal_mode=WAL')
            except sqlite3.OperationalError:
                pass  # WAL 切换失败不影响读写
            return conn
        except sqlite3.OperationalError as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 5
                print(f"⚠️  数据库锁定,{wait}秒后重试... ({attempt+1}/{retries})", flush=True)
                time.sleep(wait)
            else:
                raise

DATA_API = "https://data-api.polymarket.com"

def fetch_recent_trades(limit=100):
    """获取最近交易"""
    url = f"{DATA_API}/trades?limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PolymarketMonitor/1.0"})
        with _no_proxy_opener.open(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"❌ 获取交易失败: {e}")
        return []

def save_changes_to_db(trades):
    """保存变动到数据库,同时更新鲸鱼名称"""
    import time

    # 先准备数据
    changes_data = []
    whale_data = []

    for trade in trades:
        try:
            wallet = trade.get('proxyWallet', 'unknown')
            if wallet != 'unknown':
                wallet = wallet.lower()  # 统一小写，避免DB中同一钱包多条记录
            market = trade.get('slug', '') or trade.get('eventSlug', '') or 'unknown'
            if not market or market == 'unknown':
                wallet_short = wallet[:10] if wallet != 'unknown' else 'unknown'
                tx_hash = trade.get('transactionHash', '') or ''
                tx_short = tx_hash[:10] if tx_hash else 'N/A'
                amount_raw = float(trade.get('size', 0)) * float(trade.get('price', 0))
                print(f"⚠️ 跳过无 market 的交易: wallet={wallet_short}..., tx={tx_short}..., amount={amount_raw:.2f}", flush=True)
                continue
            outcome = trade.get('outcome', 'unknown')
            size = float(trade.get('size', 0))
            price = float(trade.get('price', 0))
            ts = trade.get('timestamp', 0)
            timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()
            tx_hash = trade.get('transactionHash', '') or ''

            if size * price < 10:
                continue

            side = trade.get('side', 'BUY')
            change_amount = size * price
            if side == 'SELL':
                change_amount = -change_amount
            # 提取市场标题(如果有)
            market_title = trade.get('title', trade.get('question', ''))
            # Fallback: if no title from API, try slug-to-title derivation
            if not market_title and market and market != 'unknown':
                market_title = _slug_to_title(market)
            changes_data.append((wallet, 'trade', market, outcome, 0, size, change_amount, timestamp, market_title, side, tx_hash))

            api_pseudonym = trade.get('pseudonym')
            if wallet != 'unknown' and api_pseudonym and not api_pseudonym.startswith('0x'):
                whale_data.append((wallet, api_pseudonym, datetime.now().isoformat(), api_pseudonym))
        except Exception as e:
            print(f"⚠️  解析失败: {e}", flush=True)

    # 带重试的批量写入
    saved = 0
    names_updated = 0
    max_retries = 3

    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            for row in changes_data:
                cursor.execute('''
                    INSERT INTO changes (wallet, type, market, outcome, old_size, new_size, change_amount, timestamp, market_title, side, tx_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', row)
                saved += 1

            for row in whale_data:
                wallet, pseudonym, now_iso, _ = row
                wallet_volume = sum(r[6] for r in changes_data if r[0] == wallet)
                wallet_trades = sum(1 for r in changes_data if r[0] == wallet)
                cursor.execute('''
                    INSERT INTO whales (wallet, pseudonym, last_updated, total_volume, changes_count, has_activity)
                    VALUES (?, ?, ?, ?, ?, 1)
                    ON CONFLICT(wallet) DO UPDATE SET
                        pseudonym = CASE
                            WHEN whales.pseudonym IS NULL 
                              OR whales.pseudonym = '' 
                              OR whales.pseudonym LIKE '0x%' THEN ?
                            ELSE whales.pseudonym
                        END,
                        last_updated = excluded.last_updated,
                        total_volume = (SELECT COALESCE(SUM(change_amount), 0) FROM changes WHERE wallet = ?),
                        changes_count = (SELECT COUNT(*) FROM changes WHERE wallet = ?),
                        has_activity = 1
                ''', (wallet, pseudonym, now_iso, wallet_volume, wallet_trades,
                      pseudonym, wallet, wallet))
                if cursor.rowcount > 0:
                    names_updated += 1

            conn.commit()

            # 更新受影响钱包的 PnL
            affected_wallets = list(set(r[0] for r in changes_data))
            if saved > 0:
                try:
                    pnl = _get_pnl_module()
                    pnl_dict = pnl.compute_wallet_pnl(conn, affected_wallets)
                    pnl_count = pnl.update_whales_pnl(conn, pnl_dict, dry_run=False)
                    if pnl_count > 0:
                        print(f"   📈 更新 {pnl_count} 个钱包 PnL", flush=True)
                except Exception as e:
                    print(f"⚠️  PnL 更新失败: {e}", flush=True)

            # 更新受影响钱包的集中度指标（HHI）
            if saved > 0:
                try:
                    concentration_count = 0
                    for wallet in affected_wallets:
                        cursor.execute("""
                            SELECT market, outcome, size, cur_price FROM positions WHERE wallet = ?
                        """, (wallet,))
                        positions = cursor.fetchall()

                        if not positions:
                            continue

                        total_value = sum(abs(float(r[2]) * float(r[3])) for r in positions)
                        if total_value <= 0:
                            continue

                        shares = [abs(float(r[2]) * float(r[3])) / total_value for r in positions]
                        hhi = sum(s * s for s in shares)

                        sorted_shares = sorted(shares, reverse=True)
                        top5_ratio = sum(sorted_shares[:5])
                        top10_ratio = sum(sorted_shares[:10])

                        cursor.execute("""
                            INSERT INTO concentration_history (wallet, hhi, top5_ratio, top10_ratio, timestamp)
                            VALUES (?, ?, ?, ?, ?)
                        """, (wallet, hhi, top5_ratio, top10_ratio, datetime.now().isoformat()))
                        concentration_count += 1

                    if concentration_count > 0:
                        conn.commit()
                        print(f"   📊 更新 {concentration_count} 个钱包集中度", flush=True)
                except Exception as e:
                    print(f"⚠️  集中度更新失败: {e}", flush=True)

            break  # 成功,退出重试
        except sqlite3.OperationalError as e:
            if 'locked' in str(e) and attempt < max_retries - 1:
                wait = (attempt + 1) * 10
                print(f"⚠️  数据库锁定,{wait}秒后重试... ({attempt+1}/{max_retries})", flush=True)
                saved = 0
                names_updated = 0
                time.sleep(wait)
            else:
                print(f"❌ 数据库写入失败: {e}", flush=True)
                break
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    return saved, names_updated

def main():
    print("🔄 同步鲸鱼变动数据...", flush=True)

    # 获取最近交易
    trades = fetch_recent_trades(200)
    print(f"   获取 {len(trades)} 条交易记录", flush=True)

    if not trades:
        print("⚠️  无交易数据,跳过", flush=True)
        return

    # 保存到数据库
    saved, names_updated = save_changes_to_db(trades)
    print(f"   保存 {saved} 条变动记录", flush=True)
    if names_updated > 0:
        print(f"   更新 {names_updated} 个鲸鱼名称", flush=True)

    # 验证
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM changes')
        count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM whales WHERE pseudonym NOT LIKE "0x%" AND pseudonym IS NOT NULL')
        named_whales = cursor.fetchone()[0]
        conn.close()
        print(f"✅ Changes 表现在有 {count} 条数据", flush=True)
        print(f"✅ 有效名称的鲸鱼: {named_whales} 个", flush=True)
    except Exception as e:
        print(f"⚠️  验证查询失败: {e}", flush=True)

if __name__ == "__main__":
    main()