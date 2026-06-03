#!/usr/bin/env python3
"""
回溯回填 concentration_history 的 hhi 和 top10_ratio 字段
由于 data_sync.py 和 data_sync_v2.py 在 INSERT 时只写了 wallet, top5_ratio, timestamp，
导致 hhi 和 top10_ratio 全部为 0。此脚本从 positions 表读取未过期持仓，
按 wallet 分组重新计算 HHI、Top5 Ratio、Top10 Ratio 并写入。

用法:
    python backfill_concentration.py                    # 回填今天的全部
    python backfill_concentration.py --date 2026-05-01  # 回填指定日期
    python backfill_concentration.py --dry-run          # 预览（不写入）
    python backfill_concentration.py --all              # 回填所有现有记录（覆盖已有 timestamp）
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "dashboard" / "backend" / "database" / "polymarket.db"


def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def calculate_metrics(positions):
    """
    计算集中度指标
    
    Args:
        positions: list of dict with 'value' key
        
    Returns:
        dict with 'hhi', 'top5_ratio', 'top10_ratio'
    """
    if not positions:
        return {'hhi': 0, 'top5_ratio': 0, 'top10_ratio': 0}
    
    values = [abs(p['value']) for p in positions if p['value'] > 0]
    total_value = sum(values)
    if total_value == 0:
        return {'hhi': 0, 'top5_ratio': 0, 'top10_ratio': 0}
    
    # HHI = sum of (share)^2, range 0-1
    shares = [v / total_value for v in values]
    hhi = sum(s * s for s in shares)
    
    sorted_values = sorted(values, reverse=True)
    top5_ratio = sum(sorted_values[:5]) / total_value
    top10_ratio = sum(sorted_values[:10]) / total_value
    
    return {
        'hhi': round(hhi, 6),
        'top5_ratio': round(top5_ratio, 6),
        'top10_ratio': round(top10_ratio, 6)
    }


def get_all_wallets_with_positions(conn, exclude_expired=True):
    """获取所有有持仓记录的钱包"""
    query = "SELECT DISTINCT wallet FROM positions"
    if exclude_expired:
        query += " WHERE is_expired = 0"
    cursor = conn.execute(query)
    return [row['wallet'] for row in cursor.fetchall()]


def get_wallet_positions(conn, wallet, exclude_expired=True):
    """获取指定钱包的持仓"""
    query = "SELECT market, outcome, value, size FROM positions WHERE wallet = ?"
    if exclude_expired:
        query += " AND is_expired = 0"
    cursor = conn.execute(query, (wallet,))
    return [dict(row) for row in cursor.fetchall()]


def get_existing_records(conn, backfill_date=None):
    """获取现有的 concentration_history 记录（用于 update 而非 insert）"""
    if backfill_date:
        date_start = backfill_date + " 00:00:00"
        date_end = backfill_date + " 23:59:59"
        cursor = conn.execute(
            "SELECT wallet, timestamp FROM concentration_history WHERE timestamp BETWEEN ? AND ?",
            (date_start, date_end)
        )
    else:
        cursor = conn.execute("SELECT wallet, timestamp FROM concentration_history")
    
    records = {}
    for row in cursor.fetchall():
        records[row['wallet']] = row['timestamp']
    return records


def get_total_whale_count(conn):
    """获取 whales 表中的记录数"""
    cursor = conn.execute("SELECT COUNT(*) as cnt FROM whales")
    return cursor.fetchone()['cnt']


def backfill(args):
    """执行回填"""
    conn = get_connection()
    
    # 获取所有有持仓的钱包
    exclude_expired = not args.include_expired
    wallets = get_all_wallets_with_positions(conn, exclude_expired=exclude_expired)
    
    if not wallets:
        print("❌ 没有找到任何钱包的持仓记录")
        conn.close()
        return
    
    print(f"📊 找到 {len(wallets)} 个钱包有{'未' if exclude_expired else ''}过期持仓")
    
    # 获取现有记录（用于 update 或 skip）
    existing = get_existing_records(conn, args.date)
    print(f"📋 concentration_history 现有 {len(existing)} 条记录（"
          f"{'匹配日期' if args.date else '全部'})")
    
    # 遍历计算
    results = []  # (wallet, hhi, top5, top10, ts)
    
    for wallet in wallets:
        positions = get_wallet_positions(conn, wallet, exclude_expired=exclude_expired)
        if not positions:
            continue
        
        metrics = calculate_metrics(positions)
        
        # 如果已有记录，复用其时间戳保持一致性
        timestamp = args.date + " 00:00:00" if args.date else datetime.now().isoformat()
        
        results.append((wallet, metrics['hhi'], metrics['top5_ratio'], metrics['top10_ratio'], timestamp))
    
    if not results:
        print("⚠️ 没有可以回填的数据")
        conn.close()
        return
    
    print(f"\n📈 计算结果: {len(results)} 条")
    
    # 统计分布
    hhi_zero = sum(1 for r in results if r[1] == 0)
    hhi_low = sum(1 for r in results if 0 < r[1] <= 0.1)
    hhi_medium = sum(1 for r in results if 0.1 < r[1] <= 0.3)
    hhi_high = sum(1 for r in results if r[1] > 0.3)
    
    top5_zero = sum(1 for r in results if r[2] == 0)
    top10_zero = sum(1 for r in results if r[3] == 0)
    
    print(f"  HHI 分布:")
    print(f"    =0 (完全分散):          {hhi_zero:>6}")
    print(f"    0 < x ≤ 0.1 (低集中):   {hhi_low:>6}")
    print(f"    0.1 < x ≤ 0.3 (中集中): {hhi_medium:>6}")
    print(f"    > 0.3 (高集中):         {hhi_high:>6}")
    print(f"  Top5=0: {top5_zero} 条")
    print(f"  Top10=0: {top10_zero} 条")
    
    if args.dry_run:
        print("\n🔍 Dry-Run 模式，未写入任何数据")
        print(f"  将影响 {len(results)} 条 concentration_history 记录")
        conn.close()
        return
    
    # 写入数据库
    updated = 0
    inserted = 0
    
    for wallet, hhi, top5, top10, ts in results:
        if wallet in existing:
            # UPDATE 现有记录
            conn.execute(
                "UPDATE concentration_history SET hhi = ?, top5_ratio = ?, top10_ratio = ? WHERE wallet = ? AND timestamp = ?",
                (hhi, top5, top10, wallet, existing[wallet])
            )
            updated += 1
        else:
            # INSERT 新记录
            conn.execute(
                "INSERT INTO concentration_history (wallet, hhi, top5_ratio, top10_ratio, timestamp) VALUES (?, ?, ?, ?, ?)",
                (wallet, hhi, top5, top10, ts)
            )
            inserted += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ 回填完成: 更新 {updated} 条, 新增 {inserted} 条")
    
    # 验证
    verify()


def verify():
    """验证回填结果"""
    conn = get_connection()
    cursor = conn.execute("SELECT COUNT(*) as cnt FROM concentration_history WHERE hhi > 0")
    hhi_gt_zero = cursor.fetchone()['cnt']
    
    cursor = conn.execute("SELECT COUNT(*) as cnt FROM concentration_history WHERE hhi = 0")
    hhi_eq_zero = cursor.fetchone()['cnt']
    
    cursor = conn.execute("SELECT COUNT(*) as total FROM concentration_history")
    total = cursor.fetchone()['total']
    
    cursor = conn.execute("""
        SELECT wallet, hhi, top5_ratio, top10_ratio, timestamp 
        FROM concentration_history 
        WHERE hhi > 0 
        ORDER BY hhi DESC 
        LIMIT 5
    """)
    top_hhi = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    print(f"\n🔍 验证:")
    print(f"  concentration_history 总记录数: {total}")
    print(f"  hhi > 0: {hhi_gt_zero}")
    print(f"  hhi = 0: {hhi_eq_zero}")
    
    if top_hhi:
        print(f"  Top 5 HHI 记录:")
        for r in top_hhi:
            print(f"    {r['wallet'][:12]}... HHI={r['hhi']:.4f} Top5={r['top5_ratio']:.2%} Top10={r['top10_ratio']:.2%} @ {r['timestamp']}")
    
    if hhi_gt_zero > 0:
        print("  ✅ 验证通过: hhi > 0 的记录存在")
    else:
        print("  ❌ 验证失败: 所有 hhi 仍然为 0")
    
    print(f"\n  SQL 验证: SELECT COUNT(*) FROM concentration_history WHERE hhi > 0;")
    print(f"  结果: {hhi_gt_zero}")
    
    return hhi_gt_zero > 0


def main():
    parser = argparse.ArgumentParser(
        description="回溯回填 concentration_history 的 HHI 和 Top10 Ratio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python backfill_concentration.py                    # 回填今天的数据
  python backfill_concentration.py --date 2026-05-01  # 回填指定日期
  python backfill_concentration.py --dry-run          # 预览（不写入）
  python backfill_concentration.py --all              # 回填所有历史记录
  python backfill_concentration.py --include-expired  # 包含已过期持仓
        """
    )
    parser.add_argument('--date', type=str, default=None,
                        help='回填日期 (YYYY-MM-DD)，默认今天')
    parser.add_argument('--dry-run', action='store_true',
                        help='预览模式，不写入数据库')
    parser.add_argument('--all', action='store_true',
                        help='回填所有现有记录（覆盖已有 timestamp）')
    parser.add_argument('--include-expired', action='store_true',
                        help='包含已过期的持仓记录')
    
    args = parser.parse_args()
    
    # 确定日期
    if not args.date:
        if args.all:
            args.date = None  # 不回填特殊日期，而是遍历所有
        else:
            args.date = datetime.now().strftime('%Y-%m-%d')
    
    print("=" * 60)
    print(f"  Polymarket 集中度指标回填工具")
    print("=" * 60)
    print(f"  数据库: {DB_PATH}")
    print(f"  日期: {args.date or '全部'}")
    print(f"  模式: {'Dry-Run' if args.dry_run else '写入'}")
    print(f"  包含过期持仓: {'是' if args.include_expired else '否'}")
    print()
    
    backfill(args)


if __name__ == "__main__":
    main()
