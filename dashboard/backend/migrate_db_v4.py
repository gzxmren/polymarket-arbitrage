#!/usr/bin/env python3
"""
数据库迁移脚本 V4
修复 changes 表重复写入问题（同一笔链上交易被多次插入）

背景: sync_changes.py 每次抓取 Data API 最近 N 笔交易直接 INSERT，没有去重检查。
两次抓取窗口有重叠时，同一笔交易（同一 tx_hash）会被插入多次。
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "database" / "polymarket.db"


def migrate():
    """执行数据库迁移"""
    print("🔄 开始数据库迁移 V4...")

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()

        # 1. 清理历史重复行：同一 tx_hash 只保留最早插入(id最小)的一条
        print("🧹 清理 changes 表历史重复行(按 tx_hash)...")
        cursor.execute('''
            SELECT tx_hash, COUNT(*) c FROM changes
            WHERE tx_hash IS NOT NULL AND tx_hash != ''
            GROUP BY tx_hash HAVING c > 1
        ''')
        dup_groups = cursor.fetchall()
        print(f"   发现 {len(dup_groups)} 个重复 tx_hash 分组")

        cursor.execute('''
            DELETE FROM changes
            WHERE tx_hash IN (
                SELECT tx_hash FROM changes
                WHERE tx_hash IS NOT NULL AND tx_hash != ''
                GROUP BY tx_hash HAVING COUNT(*) > 1
            )
            AND id NOT IN (
                SELECT MIN(id) FROM changes
                WHERE tx_hash IS NOT NULL AND tx_hash != ''
                GROUP BY tx_hash HAVING COUNT(*) > 1
            )
        ''')
        deleted = cursor.rowcount
        print(f"   删除 {deleted} 条重复行（保留每组最早一条）")

        # 2. 创建唯一索引防止再次出现重复（partial index，排除空字符串以兼容历史脏数据）
        print("🔍 创建 tx_hash 唯一索引...")
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_changes_tx_hash_unique
            ON changes(tx_hash) WHERE tx_hash != ''
        ''')

        conn.commit()
        print("✅ 数据库迁移 V4 完成！")
        print(f"\n清理: 删除 {deleted} 条 changes 重复行")
        print("新增: idx_changes_tx_hash_unique（partial unique index）")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        sys.exit(1)
