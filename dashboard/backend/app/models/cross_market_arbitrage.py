#!/usr/bin/env python3
"""
跨平台套利机会数据模型
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "database" / "polymarket.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_table():
    """初始化跨平台套利表"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cross_market_arbitrage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            polymarket_price REAL,
            manifold_price REAL,
            price_gap REAL,
            expected_return REAL,
            risk_level TEXT,
            risk_score REAL,
            audit_status TEXT,
            match_rate REAL,
            polymarket_url TEXT,
            manifold_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read BOOLEAN DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()

def save_opportunity(data):
    """保存套利机会（过滤无效数据）"""
    # Bug fix: 过滤双方价格为0或都极低的假套利
    poly_price = data.get('polymarket_price', 0) or 0
    mani_price = data.get('manifold_price', 0) or 0
    if poly_price <= 1 and mani_price <= 1:
        # 双方价格都极低（<=1%），不是有效套利
        return False
    
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO cross_market_arbitrage 
        (event_name, polymarket_price, manifold_price, price_gap, expected_return,
         risk_level, risk_score, audit_status, match_rate, polymarket_url, manifold_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('event_name', ''),
        poly_price,
        mani_price,
        data.get('price_gap', 0),
        data.get('expected_return', 0),
        data.get('risk_level', 'UNKNOWN'),
        data.get('risk_score', 0),
        data.get('audit_status', 'pending'),
        data.get('match_rate', 0),
        data.get('polymarket_url', ''),
        data.get('manifold_url', '')
    ))
    
    conn.commit()
    conn.close()
    return True

def get_recent_opportunities(limit=20, hours=24):
    """获取最近的套利机会"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM cross_market_arbitrage
        WHERE created_at > datetime('now', '-{} hours')
        ORDER BY created_at DESC
        LIMIT ?
    '''.format(hours), (limit,))
    
    columns = [desc[0] for desc in cursor.description]
    opportunities = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    conn.close()
    return opportunities

def get_statistics(hours=24):
    """获取统计信息"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN audit_status = 'approved' THEN 1 ELSE 0 END) as approved,
            AVG(price_gap) as avg_gap,
            AVG(expected_return) as avg_return
        FROM cross_market_arbitrage
        WHERE created_at > datetime('now', '-{} hours')
    '''.format(hours))
    
    row = cursor.fetchone()
    stats = {
        'total': row[0] or 0,
        'approved': row[1] or 0,
        'avg_gap': row[2] or 0,
        'avg_return': row[3] or 0
    }
    
    conn.close()
    return stats

# 初始化表
init_table()
