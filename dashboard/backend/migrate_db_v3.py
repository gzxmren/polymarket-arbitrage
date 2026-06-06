#!/usr/bin/env python3
"""
数据库迁移脚本 V3
为V2功能创建新表
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "database" / "polymarket.db"

def migrate():
    """执行数据库迁移"""
    print("🔄 开始数据库迁移 V3...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. 创建信号记录表
    print("📊 创建 signals 表...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            wallet TEXT,
            market TEXT,
            direction TEXT,
            confidence REAL,
            suggested_position REAL,
            expected_price_change REAL,
            suggested_holding_hours INTEGER,
            actual_pnl REAL,
            actual_roi REAL,
            closed_at TIMESTAMP,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. 创建鲸鱼胜率统计表
    print("📊 创建 whale_performance 表...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS whale_performance (
            wallet TEXT PRIMARY KEY,
            pseudonym TEXT,
            total_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            avg_pnl REAL DEFAULT 0,
            sharpe_ratio REAL DEFAULT 0,
            strategy_consistency REAL DEFAULT 0,
            last_updated TIMESTAMP
        )
    ''')
    
    # 3. 创建策略效果评估表
    print("📊 创建 strategy_performance 表...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS strategy_performance (
            strategy_type TEXT PRIMARY KEY,
            total_signals INTEGER DEFAULT 0,
            winning_signals INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            avg_return REAL DEFAULT 0,
            sharpe_ratio REAL DEFAULT 0,
            false_positive_rate REAL DEFAULT 0,
            best_time_window TEXT,
            updated_at TIMESTAMP
        )
    ''')
    
    # 4. 创建质量监控日志表
    print("📊 创建 quality_logs 表...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quality_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT,
            metric_value REAL,
            alert_level TEXT,
            note TEXT,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 5. 创建历史机会记录表
    print("📊 创建 opportunity_history 表...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS opportunity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT,
            opportunity_type TEXT,
            detected_at TIMESTAMP,
            profit_potential REAL,
            actual_result REAL,
            holding_period INTEGER
        )
    ''')
    
    # 6. 创建索引
    print("🔍 创建索引...")
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_quality_logs_metric ON quality_logs(metric_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_opportunity_type ON opportunity_history(opportunity_type)')
    
    conn.commit()
    conn.close()
    
    print("✅ 数据库迁移 V3 完成！")
    print("\n新增表:")
    print("  - signals (信号记录)")
    print("  - whale_performance (鲸鱼胜率统计)")
    print("  - strategy_performance (策略效果评估)")
    print("  - quality_logs (质量监控日志)")
    print("  - opportunity_history (历史机会记录)")

if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        sys.exit(1)
