#!/usr/bin/env python3
"""
同步鲸鱼变动数据到 changes 表
定期抓取 Polymarket 数据并保存变动记录
"""

import sys
import json
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))

# 直接使用绝对路径的数据库
import sqlite3
DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'

def get_db_connection():
    return sqlite3.connect(DB_PATH)

DATA_API = "https://data-api.polymarket.com"

def fetch_recent_trades(limit=100):
    """获取最近交易"""
    url = f"{DATA_API}/trades?limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PolymarketMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"❌ 获取交易失败: {e}")
        return []

def save_changes_to_db(trades):
    """保存变动到数据库"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    saved = 0
    for trade in trades:
        try:
            wallet = trade.get('proxyWallet', 'unknown')
            market = trade.get('slug', trade.get('eventSlug', 'unknown'))
            outcome = trade.get('outcome', 'unknown')
            size = float(trade.get('size', 0))
            price = float(trade.get('price', 0))
            # timestamp 是 Unix 时间戳，需要转换
            ts = trade.get('timestamp', 0)
            timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()
            
            # 只保存大单 (> $10)
            if size * price < 10:
                continue
            
            # 计算变动金额 = 数量 * 价格
            change_amount = size * price
            
            cursor.execute('''
                INSERT INTO changes (wallet, type, market, outcome, old_size, new_size, change_amount, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (wallet, 'trade', market, outcome, 0, size, change_amount, timestamp))
            
            saved += 1
        except Exception as e:
            print(f"⚠️  保存失败: {e}")
    
    conn.commit()
    conn.close()
    
    return saved

def main():
    print("🔄 同步鲸鱼变动数据...")
    
    # 获取最近交易
    trades = fetch_recent_trades(200)
    print(f"   获取 {len(trades)} 条交易记录")
    
    # 保存到数据库
    saved = save_changes_to_db(trades)
    print(f"   保存 {saved} 条变动记录")
    
    # 验证
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM changes')
    count = cursor.fetchone()[0]
    conn.close()
    
    print(f"✅ Changes 表现在有 {count} 条数据")

if __name__ == "__main__":
    main()