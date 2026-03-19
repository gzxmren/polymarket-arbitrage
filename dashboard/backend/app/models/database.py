import sqlite3
import json
from datetime import datetime
from pathlib import Path

class Database:
    def __init__(self, db_path='database/polymarket.db'):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
    
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """初始化数据库表"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 鲸鱼表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS whales (
                wallet TEXT PRIMARY KEY,
                pseudonym TEXT,
                total_value REAL DEFAULT 0,
                position_count INTEGER DEFAULT 0,
                top5_ratio REAL DEFAULT 0,
                convergence_trend TEXT DEFAULT '',
                is_watched BOOLEAN DEFAULT 0,
                has_activity BOOLEAN DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                total_volume REAL DEFAULT 0,
                changes_count INTEGER DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 持仓表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                market TEXT,
                outcome TEXT,
                size REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                cur_price REAL DEFAULT 0,
                value REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                end_date TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (wallet) REFERENCES whales(wallet)
            )
        ''')
        
        # 变动记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                type TEXT,
                market TEXT,
                outcome TEXT,
                old_size REAL DEFAULT 0,
                new_size REAL DEFAULT 0,
                change_amount REAL DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (wallet) REFERENCES whales(wallet)
            )
        ''')
        
        # 集中度历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS concentration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                hhi REAL DEFAULT 0,
                top5_ratio REAL DEFAULT 0,
                top10_ratio REAL DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (wallet) REFERENCES whales(wallet)
            )
        ''')
        
        # 警报表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                title TEXT,
                message TEXT,
                data TEXT,
                is_read BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 深度分析缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS whale_deep_analysis (
                wallet TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                model TEXT,
                generated_at TIMESTAMP,
                data_hash TEXT,
                cost REAL,
                expires_at TIMESTAMP
            )
        ''')
        
        # 套利反馈表（人工确认）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS arbitrage_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                arbitrage_id INTEGER,
                arbitrage_type TEXT,  -- 'pair_cost' 或 'cross_market'
                feedback_type TEXT,   -- 'confirmed' 或 'error'
                feedback_text TEXT,
                user_notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Pair Cost 套利表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pair_cost_arbitrage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                market_name TEXT,
                yes_price REAL,
                no_price REAL,
                sum_price REAL,
                profit_potential REAL,
                liquidity REAL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active',
                is_read BOOLEAN DEFAULT 0
            )
        ''')
        
        # 跨平台套利表
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
        print("✅ 数据库初始化完成")

# 全局数据库实例
db = Database()
