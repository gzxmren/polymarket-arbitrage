#!/usr/bin/env python3
"""
Backfill total_pnl for whales table from changes table.

PnL calculation:
  changes 表记录的是钱包的交易记录。每条记录的 change_amount = size * price (交易金额)。
  所有记录中 old_size=0 (只记录了买入方向)，change_amount > 0，没有卖出记录。

  PnL = -(SUM(change_amount))
  即总花费的相反数。由于只捕获了买入方向，所有 PnL 为负数。
  要获得准确 PnL 需要补充卖出/结算数据。

数据库路径:
  <项目根>/dashboard/backend/database/polymarket.db  (或 POLYMARKET_DB 环境变量)
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
import os
from pathlib import Path

DB_PATH = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))


def compute_wallet_pnl(conn, wallets=None):
    """
    计算钱包的 PnL (简单模式: 直接按 wallet 汇总)。

    PnL = -(SUM(change_amount))

    Args:
        conn: 数据库连接
        wallets: 可选，指定要计算的钱包列表 (None = 全部)

    Returns:
        dict: {wallet: pnl_value}
    """
    cursor = conn.cursor()

    if wallets:
        placeholders = ','.join('?' for _ in wallets)
        query = f'''
            SELECT wallet, SUM(change_amount) as total_cost
            FROM changes
            WHERE wallet IN ({placeholders})
            GROUP BY wallet
        '''
        cursor.execute(query, wallets)
    else:
        cursor.execute('''
            SELECT wallet, SUM(change_amount) as total_cost
            FROM changes
            GROUP BY wallet
        ''')

    result = {}
    for row in cursor.fetchall():
        wallet = row[0]
        total_cost = row[1] if row[1] is not None else 0.0
        result[wallet] = -total_cost

    return result


def compute_wallet_pnl_detailed(conn, wallets=None):
    """
    更详细的 PnL 计算（按 market/outcome 分组后汇总）。

    Returns:
        dict: {wallet: {pnl: float, positions: int, total_cost: float}}
    """
    cursor = conn.cursor()

    if wallets:
        placeholders = ','.join('?' for _ in wallets)
        query = f'''
            SELECT wallet, market, outcome, SUM(change_amount) as cost, MAX(new_size) as final_position
            FROM changes
            WHERE wallet IN ({placeholders})
            GROUP BY wallet, market, outcome
        '''
        cursor.execute(query, wallets)
    else:
        cursor.execute('''
            SELECT wallet, market, outcome, SUM(change_amount) as cost, MAX(new_size) as final_position
            FROM changes
            GROUP BY wallet, market, outcome
        ''')

    wallet_data = defaultdict(lambda: {'total_cost': 0.0, 'positions': 0, 'pnl': 0.0})

    for row in cursor.fetchall():
        wallet, market, outcome, cost, final_pos = row
        cost = cost if cost is not None else 0.0
        wallet_data[wallet]['total_cost'] += cost
        wallet_data[wallet]['positions'] += 1
        wallet_data[wallet]['pnl'] = -wallet_data[wallet]['total_cost']

    return dict(wallet_data)


def update_whales_pnl(conn, pnl_data, dry_run=False):
    """
    更新 whales 表的 total_pnl 字段。

    Args:
        conn: 数据库连接
        pnl_data: dict {wallet: pnl_value} 或 {wallet: {pnl: float, ...}}
        dry_run: True=只打印不写入

    Returns:
        int: 更新的记录数
    """
    cursor = conn.cursor()

    # Normalize to {wallet: pnl}
    normalized = {}
    for wallet, value in pnl_data.items():
        if isinstance(value, dict):
            normalized[wallet] = value['pnl']
        else:
            normalized[wallet] = value

    updated = 0
    cursor.execute('BEGIN TRANSACTION')

    for wallet, pnl in normalized.items():
        if dry_run:
            print(f"  [DRY-RUN] 更新 {wallet}: total_pnl = {pnl:.2f}")
            updated += 1
        else:
            cursor.execute('''
                UPDATE whales
                SET total_pnl = ?
                WHERE wallet = ?
            ''', (pnl, wallet))
            if cursor.rowcount > 0:
                updated += 1

    if not dry_run:
        conn.commit()
    else:
        conn.rollback()

    return updated


def get_statistics(conn):
    """获取 whales 表 PnL 统计信息（从数据库读取）"""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN total_pnl > 0 THEN 1 ELSE 0 END) as positive,
            SUM(CASE WHEN total_pnl = 0 THEN 1 ELSE 0 END) as zero,
            SUM(CASE WHEN total_pnl < 0 THEN 1 ELSE 0 END) as negative,
            COALESCE(MAX(total_pnl), 0) as max_pnl,
            COALESCE(MIN(total_pnl), 0) as min_pnl,
            COALESCE(AVG(total_pnl), 0) as avg_pnl
        FROM whales
    ''')
    row = cursor.fetchone()
    return {
        'total': row[0],
        'positive': row[1],
        'zero': row[2],
        'negative': row[3],
        'max_pnl': row[4],
        'min_pnl': row[5],
        'avg_pnl': row[6]
    }


def get_statistics_from_pnl_data(pnl_data, total_whales):
    """从 PnL 数据中统计分布（用于 dry-run / 未写入数据库时）"""
    # Normalize
    vals = []
    for v in pnl_data.values():
        if isinstance(v, dict):
            vals.append(v['pnl'])
        else:
            vals.append(v)

    positive = sum(1 for v in vals if v > 0)
    zero_data = sum(1 for v in vals if v == 0)
    negative = sum(1 for v in vals if v < 0)
    max_pnl = max(vals) if vals else 0
    min_pnl = min(vals) if vals else 0
    avg_pnl = sum(vals) / len(vals) if vals else 0
    # Whales with no changes have pnl=0
    zero_total = zero_data + (total_whales - len(vals))
    return {
        'total': total_whales,
        'positive': positive,
        'zero': zero_total,
        'negative': negative,
        'max_pnl': max_pnl,
        'min_pnl': min_pnl,
        'avg_pnl': avg_pnl
    }


def print_statistics(stats, label="PnL 分布"):
    """打印 PnL 统计"""
    print(f"\n📊 {label}:")
    print(f"   总记录数: {stats['total']}")
    print(f"   PnL > 0 (盈利): {stats['positive']}")
    print(f"   PnL = 0 (持平): {stats['zero']}")
    print(f"   PnL < 0 (亏损): {stats['negative']}")
    print(f"   最大正 PnL: {stats['max_pnl']:.2f}")
    print(f"   最大负 PnL: {stats['min_pnl']:.2f}")
    print(f"   平均 PnL: {stats['avg_pnl']:.2f}")


def main():
    parser = argparse.ArgumentParser(description='Backfill PnL for whales table')
    parser.add_argument('--dry-run', action='store_true', help='只预览不改')
    parser.add_argument('--wallet', type=str, help='只更新指定钱包')
    parser.add_argument('--batch', action='store_true', help='分批处理')
    parser.add_argument('--batch-size', type=int, default=500, help='每批处理数量')
    parser.add_argument('--detailed', action='store_true', help='按 market/outcome 分组的详细 PnL')
    args = parser.parse_args()

    # 验证数据库存在
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        conn.execute('PRAGMA busy_timeout=60000')
        conn.execute('PRAGMA journal_mode=WAL')
    except sqlite3.OperationalError as e:
        print(f"❌ 无法连接数据库: {e}")
        sys.exit(1)

    print("🔄 Backfill PnL ...")
    print(f"   数据库: {DB_PATH}")

    # Before 统计
    before_stats = get_statistics(conn)
    print_statistics(before_stats, "Before 状态")

    # PnL 数据容器 (用于 dry-run stats)
    all_pnl_data = {}

    # 计算 & 写入 PnL
    if args.wallet:
        wallets = [args.wallet.strip().lower()]
        print(f"\n   计算钱包: {args.wallet}")
        if args.detailed:
            all_pnl_data = compute_wallet_pnl_detailed(conn, wallets)
        else:
            pnl_dict = compute_wallet_pnl(conn, wallets)
            all_pnl_data = {w: {'pnl': p, 'positions': 0, 'total_cost': abs(p)}
                           for w, p in pnl_dict.items()}
        updated = update_whales_pnl(conn, all_pnl_data, dry_run=args.dry_run)
        print(f"\n   更新 {updated} 条 whales 记录")

    elif args.batch:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT wallet FROM changes ORDER BY wallet')
        all_wallets = [row[0] for row in cursor.fetchall()]
        total = len(all_wallets)
        print(f"\n   总钱包数: {total}, 每批 {args.batch_size} 个")

        updated_total = 0
        for i in range(0, total, args.batch_size):
            batch = all_wallets[i:i + args.batch_size]
            if args.detailed:
                batch_pnl = compute_wallet_pnl_detailed(conn, batch)
            else:
                pnl_dict = compute_wallet_pnl(conn, batch)
                batch_pnl = {w: {'pnl': p, 'positions': 0, 'total_cost': abs(p)}
                            for w, p in pnl_dict.items()}

            updated = update_whales_pnl(conn, batch_pnl, dry_run=args.dry_run)
            updated_total += updated

            if not args.dry_run:
                conn.commit()

            all_pnl_data.update(batch_pnl)

            progress = min(i + args.batch_size, total)
            print(f"   进度: {progress}/{total} ({progress*100//total}%)")

        print(f"\n   共更新 {updated_total} 条记录")

    else:
        # 全量处理
        print(f"\n   计算所有钱包 PnL ...")
        if args.detailed:
            all_pnl_data = compute_wallet_pnl_detailed(conn)
        else:
            pnl_dict = compute_wallet_pnl(conn)
            all_pnl_data = {w: {'pnl': p, 'positions': 0, 'total_cost': abs(p)}
                           for w, p in pnl_dict.items()}
        print(f"   共 {len(all_pnl_data)} 个钱包")

        updated = update_whales_pnl(conn, all_pnl_data, dry_run=args.dry_run)
        print(f"\n   更新 {updated} 条 whales 记录")

        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM whales WHERE wallet NOT IN (SELECT DISTINCT wallet FROM changes)')
        no_changes = cursor.fetchone()[0]
        if no_changes > 0:
            print(f"   {no_changes} 个钱包在 changes 表中无记录 (total_pnl 保持 0)")

    # After 统计
    if args.dry_run:
        # dry-run 模式: 数据库已回滚，从内存数据算
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM whales')
        total_whales = cursor.fetchone()[0]
        after_stats = get_statistics_from_pnl_data(all_pnl_data, total_whales)
    else:
        after_stats = get_statistics(conn)
    print_statistics(after_stats, "After 状态")

    if args.dry_run:
        print("\n✅ DRY RUN 完成，未写库。使用 `python3 backfill_pnl.py` 实际执行。")
    else:
        print("\n✅ Backfill 完成！")

    conn.close()


if __name__ == "__main__":
    main()
