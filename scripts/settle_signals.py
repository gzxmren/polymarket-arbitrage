#!/usr/bin/env python3
"""
信号结算脚本 — settle_signals.py

定期运行（建议每天一次），结算已到期的 pending 信号：
1. 查 signals 表中已超过 suggested_holding_hours 的 pending 信号
2. 从 daily_price_snapshots 取入场价和出场价
3. 计算 PnL，写 signal_results，更新 signals.status='resolved'
4. 重新聚合 whale_performance

用法：
    python3 scripts/settle_signals.py [--dry-run] [--debug]
"""

import sys
import sqlite3
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "dashboard/backend/database/polymarket.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def settle_pending_signals(conn: sqlite3.Connection, dry_run: bool, debug: bool) -> int:
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    # 查所有到期的 pending 信号
    cur.execute("""
        SELECT id, type, wallet, market, direction, confidence,
               suggested_position, expected_price_change, suggested_holding_hours,
               created_at
        FROM signals
        WHERE status = 'pending'
          AND datetime(created_at)
              <= datetime('now', '-' || CAST(suggested_holding_hours AS TEXT) || ' hours')
    """)
    pending = cur.fetchall()

    if debug:
        print(f"[DEBUG] 待结算信号: {len(pending)} 条")

    settled = 0
    skipped = 0

    for sig in pending:
        sig_id = sig["id"]
        market = sig["market"]
        direction = (sig["direction"] or "").upper()
        created_at_str = sig["created_at"]
        holding_h = sig["suggested_holding_hours"] or 48

        # 解析入场日期
        try:
            created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except Exception:
            created_dt = datetime.fromisoformat(created_at_str)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)

        entry_date = created_dt.strftime("%Y-%m-%d")
        exit_dt = created_dt + timedelta(hours=holding_h)
        exit_date = exit_dt.strftime("%Y-%m-%d")

        # 取入场价（信号创建日的价格快照）
        entry_price = _get_price(cur, market, direction, entry_date)
        if entry_price is None:
            # 找最接近的历史价
            entry_price = _get_nearest_price(cur, market, direction, entry_date, before=True)

        # 取出场价（到期日或最新的快照）
        exit_price = _get_price(cur, market, direction, exit_date)
        if exit_price is None:
            exit_price = _get_nearest_price(cur, market, direction, exit_date, before=True)
        if exit_price is None:
            # 用最新价格兜底
            exit_price = _get_latest_price(cur, market, direction)

        if entry_price is None or exit_price is None:
            # 已到期超过72h仍无价格数据 → 标记为 no_data，不再重试
            mature_hours = (now - exit_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if mature_hours > 72:
                if debug:
                    print(f"[NO_DATA] signal_id={sig_id}, market={market}: "
                          f"到期后 {mature_hours:.0f}h 仍无价格，标记 no_data")
                if not dry_run:
                    cur.execute(
                        "UPDATE signals SET status='no_data', closed_at=? WHERE id=?",
                        (now.isoformat(), sig_id)
                    )
            else:
                if debug:
                    print(f"[SKIP] signal_id={sig_id}, market={market}: 暂无价格数据，等待中")
            skipped += 1
            continue

        # 计算盈亏
        pnl_pct, actual_result = _calc_pnl(direction, entry_price, exit_price)
        position = sig["suggested_position"] or 0
        pnl_abs = position * (pnl_pct / 100) if position else None

        if debug:
            print(f"[SETTLE] id={sig_id}, mkt={market[:40]}, dir={direction}, "
                  f"entry={entry_price:.3f}, exit={exit_price:.3f}, "
                  f"pnl={pnl_pct:+.1f}%, result={actual_result}")

        if not dry_run:
            closed_at = now.isoformat()

            # 更新 signals
            cur.execute("""
                UPDATE signals SET
                    actual_pnl = ?,
                    actual_roi = ?,
                    closed_at = ?,
                    status = 'resolved'
                WHERE id = ?
            """, (pnl_abs, pnl_pct / 100, closed_at, sig_id))

            # 写 signal_results
            cur.execute("""
                INSERT INTO signal_results
                    (signal_type, signal_id, market_id, market_name,
                     prediction, predicted_direction,
                     confidence, trigger_price, exit_price,
                     pnl_percent, actual_result, actual_direction,
                     resolved_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sig["type"],
                str(sig_id),
                market,
                market,
                sig["direction"],
                sig["direction"],
                sig["confidence"],
                entry_price,
                exit_price,
                pnl_pct,
                actual_result,
                actual_result,
                closed_at,
                json.dumps({
                    "wallet": sig["wallet"],
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "holding_hours": holding_h,
                }),
            ))

        settled += 1

    if not dry_run:
        conn.commit()

    return settled, skipped


def _get_price(cur, market: str, direction: str, date: str):
    outcome = "Yes" if direction in ("YES", "YES_") else ("No" if direction in ("NO", "NO_") else "Yes")
    cur.execute("""
        SELECT price FROM daily_price_snapshots
        WHERE market = ? AND outcome = ? AND snapshot_date = ?
        LIMIT 1
    """, (market, outcome, date))
    row = cur.fetchone()
    return row["price"] if row else None


def _get_nearest_price(cur, market: str, direction: str, date: str, before: bool = True):
    outcome = "Yes" if direction in ("YES", "YES_") else "No"
    op = "<=" if before else ">="
    order = "DESC" if before else "ASC"
    cur.execute(f"""
        SELECT price FROM daily_price_snapshots
        WHERE market = ? AND outcome = ? AND snapshot_date {op} ?
        ORDER BY snapshot_date {order} LIMIT 1
    """, (market, outcome, date))
    row = cur.fetchone()
    return row["price"] if row else None


def _get_latest_price(cur, market: str, direction: str):
    outcome = "Yes" if direction in ("YES", "YES_") else "No"
    cur.execute("""
        SELECT price FROM daily_price_snapshots
        WHERE market = ? AND outcome = ?
        ORDER BY snapshot_date DESC LIMIT 1
    """, (market, outcome))
    row = cur.fetchone()
    return row["price"] if row else None


def _calc_pnl(direction: str, entry: float, exit_price: float) -> tuple:
    """返回 (pnl_pct, actual_result)。方向为 YES 则买入做多，NO 则做空（买 No token）。"""
    if direction in ("YES", "YES_"):
        pnl_pct = (exit_price - entry) / entry * 100
    else:
        pnl_pct = (entry - exit_price) / entry * 100

    actual_result = "win" if pnl_pct > 0 else "loss"
    return round(pnl_pct, 4), actual_result


def update_whale_performance(conn: sqlite3.Connection, dry_run: bool, debug: bool) -> int:
    """从 signal_results 重新聚合 whale_performance。"""
    cur = conn.cursor()

    # 取所有有结果的 whale 信号
    cur.execute("""
        SELECT sr.metadata, sr.actual_result, sr.pnl_percent,
               s.wallet
        FROM signal_results sr
        LEFT JOIN signals s ON CAST(sr.signal_id AS INTEGER) = s.id
        WHERE sr.actual_result IS NOT NULL
    """)
    rows = cur.fetchall()

    if debug:
        print(f"[DEBUG] signal_results 行数: {len(rows)}")

    # 按钱包聚合
    wallets: dict[str, dict] = {}
    for row in rows:
        wallet = row["wallet"]
        if not wallet:
            try:
                meta = json.loads(row["metadata"] or "{}")
                wallet = meta.get("wallet")
            except Exception:
                pass
        if not wallet:
            continue

        if wallet not in wallets:
            wallets[wallet] = {"total": 0, "wins": 0, "pnl_sum": 0.0}
        wallets[wallet]["total"] += 1
        if row["actual_result"] == "win":
            wallets[wallet]["wins"] += 1
        wallets[wallet]["pnl_sum"] += row["pnl_percent"] or 0

    if not wallets:
        return 0

    updated = 0
    now_str = datetime.now(timezone.utc).isoformat()

    for wallet, stats in wallets.items():
        total = stats["total"]
        wins = stats["wins"]
        win_rate = wins / total if total else 0
        avg_pnl = stats["pnl_sum"] / total if total else 0

        if debug:
            print(f"[WHALE] {wallet[:16]}...: total={total}, win_rate={win_rate:.2%}, avg_pnl={avg_pnl:+.2f}%")

        if not dry_run:
            cur.execute("""
                INSERT INTO whale_performance (wallet, total_trades, winning_trades, win_rate, avg_pnl, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet) DO UPDATE SET
                    total_trades = excluded.total_trades,
                    winning_trades = excluded.winning_trades,
                    win_rate = excluded.win_rate,
                    avg_pnl = excluded.avg_pnl,
                    last_updated = excluded.last_updated
            """, (wallet, total, wins, win_rate, avg_pnl, now_str))
        updated += 1

    if not dry_run:
        conn.commit()

    return updated


def main():
    parser = argparse.ArgumentParser(description="结算 pending 信号")
    parser.add_argument("--dry-run", action="store_true", help="只读，不写 DB")
    parser.add_argument("--debug", action="store_true", help="打印详细日志")
    args = parser.parse_args()

    print("=" * 60)
    print("📊 settle_signals.py — 信号结算")
    print(f"   DB: {DB_PATH}")
    print(f"   模式: {'DRY-RUN (只读)' if args.dry_run else '写入'}")
    print("=" * 60)

    conn = get_db()

    # 查现有状态
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM signals GROUP BY status")
    for row in cur.fetchall():
        print(f"   signals[{row[0]}]: {row[1]} 条")

    settled, skipped = settle_pending_signals(conn, args.dry_run, args.debug)
    print(f"\n✅ 结算: {settled} 条  跳过（无价格数据）: {skipped} 条")

    if settled > 0 or args.debug:
        whale_cnt = update_whale_performance(conn, args.dry_run, args.debug)
        print(f"🐋 whale_performance 更新: {whale_cnt} 条")

    conn.close()
    print("=" * 60)


if __name__ == "__main__":
    main()
