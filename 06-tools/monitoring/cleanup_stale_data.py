#!/usr/bin/env python3
"""
Polymarket 过期数据清理脚本
- 清理已过期30天+的 positions
- 清理30天+的已读告警
- 标记新过期的 positions
- 清理旧的 changes 记录(保留30天,分批删除)
"""

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))

def main():
    start_time = time.time()
    print("🧹 Polymarket 数据清理...", flush=True)
    print(f"⏰ {datetime.now().isoformat()}", flush=True)

    db = sqlite3.connect(DB, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    cursor = db.cursor()

    # 0. 创建 positions_archive 表(如果不存在),结构与 positions 相同 + archived_at
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions_archive (
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
            is_expired BOOLEAN DEFAULT 0,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print(f"  positions_archive 表已就绪", flush=True)

    # 清理前过期分布统计
    cursor.execute('''
        SELECT
            CASE
                WHEN is_expired=0 THEN 'active'
                WHEN end_date < date('now', '-30 days') THEN 'expired_30dplus'
                WHEN end_date < date('now', '-7 days') THEN 'expired_7d_30d'
                ELSE 'expired_recent'
            END as bucket,
            COUNT(*) as cnt
        FROM positions
        GROUP BY bucket
        ORDER BY bucket
    ''')
    print(f"  清理前过期分布:")
    for row in cursor.fetchall():
        print(f"    {row[0]}: {row[1]}")

    # 1. 标记新过期的 positions(排除空/非法 end_date)
    cursor.execute("""
        UPDATE positions SET is_expired = 1
        WHERE end_date IS NOT NULL
        AND end_date != ''
        AND end_date != '1970-01-01'
        AND end_date < date('now')
        AND is_expired = 0
    """)
    newly_expired = cursor.rowcount
    print(f"  新过期 positions: {newly_expired}", flush=True)

    # 1b. 强制标记过期30天+的记录(处理报告中可能计数但未标记的边界情况)
    cursor.execute("""
        UPDATE positions SET is_expired = 1
        WHERE end_date IS NOT NULL
        AND end_date != ''
        AND end_date != '1970-01-01'
        AND end_date < date('now', '-30 days')
        AND is_expired = 0
    """)
    force_expired = cursor.rowcount
    if force_expired > 0:
        print(f"  强制标记过期30天+ positions: {force_expired}", flush=True)

    # 2. 将过期30天+的 positions 归档到 positions_archive 表,再从主表删除
    cursor.execute("""
        INSERT INTO positions_archive (wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date, updated_at, is_expired, archived_at)
        SELECT wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date, updated_at, is_expired, datetime('now')
        FROM positions
        WHERE is_expired = 1
        AND end_date IS NOT NULL
        AND end_date != ''
        AND end_date != '1970-01-01'
        AND end_date < date('now', '-30 days')
    """)
    archived_count = cursor.rowcount

    cursor.execute("""
        DELETE FROM positions 
        WHERE is_expired = 1 
        AND end_date IS NOT NULL 
        AND end_date != '' 
        AND end_date < date('now', '-30 days')
    """)
    deleted_positions = cursor.rowcount
    # 一致性检查
    if archived_count != deleted_positions:
        print(f"  ⚠️ 归档/删除不一致: archive={archived_count}, delete={deleted_positions}", flush=True)
    print(f"  归档过期30天+ positions: {archived_count} 条移至归档表, 从主表删除 {deleted_positions} 条", flush=True)

    # 2b. 兜底清理：直接按 end_date 删除30天+（无论 is_expired 标记），防止遗漏
    cursor.execute("""
        INSERT OR IGNORE INTO positions_archive (wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date, updated_at, is_expired, archived_at)
        SELECT wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date, updated_at, is_expired, datetime('now')
        FROM positions
        WHERE end_date IS NOT NULL 
        AND end_date != '' 
        AND end_date != '1970-01-01'
        AND end_date < date('now', '-30 days')
    """)
    archived_fallback = cursor.rowcount
    cursor.execute("""
        DELETE FROM positions 
        WHERE end_date IS NOT NULL 
        AND end_date != '' 
        AND end_date != '1970-01-01'
        AND end_date < date('now', '-30 days')
    """)
    deleted_fallback = cursor.rowcount
    if archived_fallback > 0 or deleted_fallback > 0:
        print(f"  兜底清理 end_date 过期: 归档 {archived_fallback} 条, 删除 {deleted_fallback} 条", flush=True)

    # 3. 清理30天+的已读告警
    cursor.execute("""
        DELETE FROM alerts
        WHERE is_read = 1
        AND created_at < datetime('now', '-30 days')
    """)
    deleted_alerts = cursor.rowcount
    print(f"  删除旧告警: {deleted_alerts}", flush=True)

    # 4. 自动标30天+未读告警为已读
    cursor.execute("""
        UPDATE alerts SET is_read = 1
        WHERE is_read = 0
        AND created_at < datetime('now', '-30 days')
    """)
    auto_read = cursor.rowcount
    print(f"  自动标已读(30天+): {auto_read}", flush=True)

    # 5. 清理 rejected 的套利数据(30天+)
    cursor.execute("""
        DELETE FROM cross_market_arbitrage
        WHERE audit_status = 'rejected'
        AND created_at < datetime('now', '-30 days')
    """)
    deleted_arb = cursor.rowcount
    print(f"  清理旧 rejected 套利: {deleted_arb}", flush=True)

    # 5b. 清理 end_date 为非法值(1970-01-01)的已过期 positions
    cursor.execute("""
        INSERT OR IGNORE INTO positions_archive (wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date, updated_at, is_expired, archived_at)
        SELECT wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date, updated_at, is_expired, datetime('now')
        FROM positions
        WHERE is_expired = 1 AND (end_date = '1970-01-01' OR end_date IS NULL OR end_date = '')
    """)
    archived_bad = cursor.rowcount
    cursor.execute("""
        DELETE FROM positions
        WHERE is_expired = 1 AND (end_date = '1970-01-01' OR end_date IS NULL OR end_date = '')
    """)
    deleted_bad = cursor.rowcount
    if archived_bad > 0:
        print(f"  清理非法 end_date 过期 positions: 归档 {archived_bad} 条, 删除 {deleted_bad} 条", flush=True)

    # 6. 清理旧的 concentration_history(保留7天)
    cursor.execute("""
        DELETE FROM concentration_history
        WHERE timestamp < datetime('now', '-7 days')
    """)
    deleted_conc = cursor.rowcount
    print(f"  清理旧 concentration_history: {deleted_conc}", flush=True)

    # 先 commit 前6步修改
    db.commit()
    print("  步骤1-6数据修改已提交", flush=True)

    # 清理后过期分布统计
    cursor.execute('''
        SELECT
            CASE
                WHEN is_expired=0 THEN 'active'
                WHEN end_date < date('now', '-30 days') THEN 'expired_30dplus'
                WHEN end_date < date('now', '-7 days') THEN 'expired_7d_30d'
                ELSE 'expired_recent'
            END as bucket,
            COUNT(*) as cnt
        FROM positions
        GROUP BY bucket
        ORDER BY bucket
    ''')
    print(f"  清理后过期分布:")
    for row in cursor.fetchall():
        print(f"    {row[0]}: {row[1]}")

    # 7. 清理旧的 changes 记录(保留30天,分批删除避免超时和锁竞争)
    batch_size = 10000
    deleted_changes = 0
    timeout_seconds = 90
    timed_out = False
    try:
        while True:
            cursor.execute("""
                DELETE FROM changes WHERE id IN (
                    SELECT id FROM changes
                    WHERE timestamp < datetime('now', '-30 days')
                    LIMIT ?
                )
            """, (batch_size,))
            batch_deleted = cursor.rowcount
            deleted_changes += batch_deleted
            db.commit()
            if batch_deleted < batch_size:
                break
            time.sleep(0.1)  # 减少锁竞争
            # 超时检查:运行超过90秒则停止,下次继续
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                # 查询剩余待删除数量
                cursor.execute("SELECT COUNT(*) FROM changes WHERE timestamp < datetime('now', '-30 days')")
                remaining = cursor.fetchone()[0]
                print(f"  ⚠️ changes 清理超时({elapsed:.0f}s),已删{deleted_changes}条,剩余{remaining}条待下次清理", flush=True)
                timed_out = True
                break
        if not timed_out:
            print(f"  清理旧 changes (30天+): {deleted_changes}", flush=True)
    except sqlite3.OperationalError as e:
        db.rollback()
        print(f"  changes 清理失败: {e}", flush=True)

    # 8. 创建索引(如果不存在)
    indexes = [
        ("idx_positions_expired_enddate", "positions (is_expired, end_date)"),
        ("idx_alerts_read_created", "alerts (is_read, created_at)"),
        ("idx_arb_status_created", "cross_market_arbitrage (audit_status, created_at)"),
        ("idx_conc_history_timestamp", "concentration_history (timestamp)"),
        ("idx_changes_timestamp", "changes (timestamp)"),
    ]
    for idx_name, idx_def in indexes:
        try:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}")
        except sqlite3.OperationalError as e:
            print(f"  索引 {idx_name} 创建跳过: {e}", flush=True)
    db.commit()
    print("  索引检查完成", flush=True)

    # 9. VACUUM 已移除 - 需要独占锁,与后台服务冲突导致超时
    # 如需 VACUUM,请在停服后手动执行:
    #   sqlite3 <db_path> "PRAGMA journal_mode=DELETE; VACUUM; PRAGMA journal_mode=WAL;"

    # 10. 清理不活跃鲸鱼JSON文件(孤儿文件 + 已标记不活跃的鲸鱼)
    whale_dir = str(Path(__file__).resolve().parents[2] / "07-data" / "whale_states")
    if os.path.exists(whale_dir):
        # 获取数据库中所有钱包地址
        cursor.execute("SELECT wallet, has_activity FROM whales")
        db_wallets = {row[0].lower(): row[1] for row in cursor.fetchall()}

        orphan_count = 0
        inactive_count = 0
        for f in os.listdir(whale_dir):
            if not f.endswith('.json'):
                continue
            # 跳过非钱包地址文件
            wallet_from_file = f.replace('.json', '').lower()
            if not wallet_from_file.startswith('0x'):
                continue

            fp = os.path.join(whale_dir, f)
            # 孤儿文件:钱包不在数据库中
            if wallet_from_file not in db_wallets:
                os.remove(fp)
                orphan_count += 1
                continue
            # 不活跃文件:钱包在数据库中但 has_activity=0
            if db_wallets[wallet_from_file] == 0:
                os.remove(fp)
                inactive_count += 1

        if orphan_count > 0 or inactive_count > 0:
            print(f"  JSON清理: 删除 {orphan_count} 个孤儿文件, {inactive_count} 个不活跃鲸鱼文件", flush=True)

    # 清理空 market 的 changes 记录
    cursor.execute("DELETE FROM changes WHERE market = '' OR market IS NULL")
    empty_market_deleted = cursor.rowcount
    if empty_market_deleted > 0:
        print(f"  清理空 market 的 changes: {empty_market_deleted} 条", flush=True)
    db.commit()

    db.close()

    # 统计
    db2 = sqlite3.connect(DB)
    c2 = db2.cursor()
    c2.execute("SELECT COUNT(*) FROM positions")
    pos = c2.fetchone()[0]
    c2.execute("SELECT COUNT(*) FROM alerts")
    alerts = c2.fetchone()[0]
    c2.execute("SELECT COUNT(*) FROM cross_market_arbitrage")
    arb = c2.fetchone()[0]
    c2.execute("SELECT COUNT(*) FROM concentration_history")
    conc = c2.fetchone()[0]
    c2.execute("SELECT COUNT(*) FROM changes")
    changes_count = c2.fetchone()[0]
    db_size = os.path.getsize(DB) / 1024 / 1024

    # 统计清理后仍有问题的记录(关键监控指标)
    c2.execute("""SELECT COUNT(*) FROM positions
        WHERE is_expired = 1 AND end_date IS NOT NULL
        AND end_date != '' AND end_date != '1970-01-01'
        AND end_date < date('now', '-30 days')""")
    remaining_stale = c2.fetchone()[0]

    print(f"\n📊 清理后统计:", flush=True)
    print(f"  positions: {pos:,}", flush=True)
    print(f"  alerts: {alerts:,}", flush=True)
    print(f"  cross_market_arbitrage: {arb:,}", flush=True)
    print(f"  concentration_history: {conc:,}", flush=True)
    print(f"  changes: {changes_count:,}", flush=True)
    print(f"  数据库大小: {db_size:.1f} MB", flush=True)
    print(f"  剩余过期30天+未清理: {remaining_stale}", flush=True)
    if remaining_stale > 0:
        print(f"  ⚠️ 需要关注: {remaining_stale} 条过期30天+记录未被清理", flush=True)

    db2.close()

    # 11. 清理 monitor_report_*.json（保留30天）
    import glob, time as _time
    report_dir = str(Path(__file__).resolve().parents[2] / "07-data")
    cutoff = _time.time() - 30 * 86400
    removed_reports = 0
    for fp in glob.glob(f"{report_dir}/monitor_report_*.json"):
        if os.path.getmtime(fp) < cutoff:
            os.remove(fp)
            removed_reports += 1
    if removed_reports:
        print(f"  清理旧 monitor_report: {removed_reports} 个", flush=True)

    # 12. 清理 whale_states_archive/（保留30天）
    archive_dir = str(Path(__file__).resolve().parents[2] / "07-data" / "whale_states_archive")
    removed_archive = 0
    if os.path.exists(archive_dir):
        for fp in os.listdir(archive_dir):
            full = os.path.join(archive_dir, fp)
            if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                os.remove(full)
                removed_archive += 1
    if removed_archive:
        print(f"  清理旧 whale_states_archive: {removed_archive} 个", flush=True)

    print("\n✅ 清理完成", flush=True)

if __name__ == "__main__":
    main()
