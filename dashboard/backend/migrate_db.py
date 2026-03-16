#!/usr/bin/env python3
"""
数据库迁移脚本
添加 has_activity, total_pnl, total_volume 字段
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.models.database import db

def migrate():
    """执行数据库迁移"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 检查并添加新字段
    new_columns = [
        ('has_activity', 'BOOLEAN DEFAULT 0'),
        ('total_pnl', 'REAL DEFAULT 0'),
        ('total_volume', 'REAL DEFAULT 0'),
    ]
    
    for col_name, col_type in new_columns:
        try:
            cursor.execute(f'ALTER TABLE whales ADD COLUMN {col_name} {col_type}')
            print(f"✅ 添加字段: {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"⚠️  字段已存在: {col_name}")
            else:
                print(f"❌ 添加字段失败: {col_name} - {e}")
    
    conn.commit()
    conn.close()
    print("✅ 数据库迁移完成")

if __name__ == '__main__':
    migrate()
