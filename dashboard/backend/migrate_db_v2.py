#!/usr/bin/env python3
"""
数据库迁移脚本 V2
添加 changes_count 字段
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / 'database' / 'polymarket.db'


def migrate():
    """执行数据库迁移"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查并添加新字段
    try:
        cursor.execute('ALTER TABLE whales ADD COLUMN changes_count INTEGER DEFAULT 0')
        print("✅ 添加字段: changes_count")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("⚠️  字段已存在: changes_count")
        else:
            print(f"❌ 添加字段失败: {e}")
    
    conn.commit()
    conn.close()
    print("✅ 数据库迁移完成")


if __name__ == '__main__':
    migrate()
