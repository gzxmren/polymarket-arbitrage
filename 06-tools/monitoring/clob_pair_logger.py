#!/usr/bin/env python3
"""
CLOB Pair-Cost 前向记录器 — 实时验证套利 + 积累盘口时序数据

背景(2026-06-08):历史回测证明 daily_price_snapshots 里 No≡1-Yes(按构造),
YES+NO 恒等于 1，无法检测 pair-cost 套利；全库无任何盘口/深度历史。
唯一出路是**前向**:实时扫 CLOB 盘口，记录真实双边可成交价 + 深度。

记录两个方向的套利(都需 pair_cost 偏离 $1):
  - 买边: 同时买 YES_ask + NO_ask。若 < $1，结算赎回 $1 → 锁定 (1 - cost) 利润。
  - 卖边: 铸一套(成本$1)卖 YES_bid + NO_bid。若 > $1 → 锁定 (proceeds - 1) 利润。

每次扫 top-N 流动市场，把每个市场的双边盘口快照写入独立 DB
(07-data/clob_pair_log.db，不污染主库)。既前向验证套利是否真实可成交，
又积累此前完全没有的盘口时序数据(为未来微结构假设铺路)。

诚实口径:
  - 只记 **top-level** 可成交量(min(两腿挂单量))——最保守的真实数；吃深了边际更差。
  - 保存原始 ask/bid/size，任何 gas/手续费假设可事后套用。
  - 30 分钟快照会漏掉毫秒级闪现套利;目标不是 HFT，而是回答
    "对耐心的小资金，是否存在可成交、有深度的 sub-$1 配对"。

用法:
  PYTHONPATH=06-tools/analysis python3 06-tools/monitoring/clob_pair_logger.py
  PYTHONPATH=06-tools/analysis python3 06-tools/monitoring/clob_pair_logger.py --limit 80 --once
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "analysis"))

from clob_api import get_markets_with_order_book, get_order_book  # noqa: E402

DB_PATH = PROJECT_ROOT / "07-data" / "clob_pair_log.db"
_LOCK_FILE = Path(__file__).parent / ".clob_pair_logger.lock"
_LOCK_TIMEOUT = 600

# 默认成本假设(可事后从原始价重算)。Polymarket taker 多数流程免 gas;
# 铸/赎一套有 Polygon gas，保守取每"配对往返" $0.04。
DEFAULT_GAS_PER_PAIR = 0.04


# ---------- 进程锁 ----------

def _acquire_lock():
    if _LOCK_FILE.exists():
        try:
            data = json.loads(_LOCK_FILE.read_text())
            age = time.time() - data.get("ts", 0)
            if age < _LOCK_TIMEOUT:
                print(f"⏭️ 进程锁存在 (pid={data.get('pid')}, {age:.0f}s 前)，退出")
                sys.exit(0)
        except Exception:
            pass
    _LOCK_FILE.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}))
    atexit.register(_release_lock)


def _release_lock():
    try:
        if _LOCK_FILE.exists():
            _LOCK_FILE.unlink()
    except Exception:
        pass


# ---------- DB ----------

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS clob_pair_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        run_id TEXT NOT NULL,
        market_slug TEXT,
        question TEXT,
        liquidity REAL,
        volume REAL,
        yes_ask REAL, yes_ask_size REAL,
        no_ask REAL, no_ask_size REAL,
        buy_pair_cost REAL,          -- yes_ask + no_ask
        buy_exec_pairs REAL,         -- min(两腿 ask 挂单量)
        buy_edge_per_pair REAL,      -- 1 - buy_pair_cost (毛)
        yes_bid REAL, yes_bid_size REAL,
        no_bid REAL, no_bid_size REAL,
        sell_pair_proceeds REAL,     -- yes_bid + no_bid
        sell_exec_pairs REAL,
        sell_edge_per_pair REAL,     -- sell_pair_proceeds - 1 (毛)
        buy_arb INTEGER,             -- 净>0(扣gas)
        sell_arb INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_cpl_ts ON clob_pair_log(ts);
    CREATE INDEX IF NOT EXISTS idx_cpl_run ON clob_pair_log(run_id);
    CREATE INDEX IF NOT EXISTS idx_cpl_buyarb ON clob_pair_log(buy_arb);

    CREATE TABLE IF NOT EXISTS clob_pair_runs (
        run_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        n_markets INTEGER,
        n_buy_arb INTEGER,
        n_sell_arb INTEGER,
        min_buy_pair_cost REAL,
        max_sell_proceeds REAL,
        best_buy_edge_usd REAL,
        duration_s REAL
    );
    """)
    conn.commit()


# ---------- 盘口解析 ----------

def _best(levels: list, side: str):
    """asks: 价升序取最低(best ask=买入价); bids: 价降序取最高(best bid=卖出价)。
    CLOB /book 返回顺序未必规范，显式排序确保正确。"""
    if not levels:
        return None, None
    if side == "ask":
        best = min(levels, key=lambda x: x[0])   # 最低卖价
    else:
        best = max(levels, key=lambda x: x[0])   # 最高买价
    return best[0], best[1]


def _resilient_book(token_id: str, retries: int = 2):
    """get_order_book 带一次重试 + 退避，缓解 CLOB CDN 偶发 421 Misdirected Request。"""
    for attempt in range(retries):
        ob = get_order_book(token_id)
        if ob is not None:
            return ob
        time.sleep(0.4 * (attempt + 1))
    return None


def _resilient_markets(limit: int, retries: int = 3):
    """市场列表获取带重试，缓解 Gamma API 偶发空返回（cron 环境下出现过 0 市场空跑）。"""
    for attempt in range(retries):
        markets = get_markets_with_order_book(limit=limit)
        if markets:
            return markets
        time.sleep(1.5 * (attempt + 1))
    return []


def scan_once(limit: int, gas_per_pair: float) -> dict:
    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    markets = _resilient_markets(limit)
    rows = []
    n_buy_arb = n_sell_arb = 0
    min_buy = 9.9
    max_sell = 0.0
    best_buy_edge_usd = 0.0

    for m in markets:
        yob = _resilient_book(m["yes_token"])
        nob = _resilient_book(m["no_token"])
        if not yob or not nob:
            continue
        time.sleep(0.05)  # 礼貌间隔，降低 CDN 421

        yes_ask, yes_ask_sz = _best(yob.get("asks", []), "ask")
        no_ask, no_ask_sz = _best(nob.get("asks", []), "ask")
        yes_bid, yes_bid_sz = _best(yob.get("bids", []), "bid")
        no_bid, no_bid_sz = _best(nob.get("bids", []), "bid")

        # 买边
        buy_pair_cost = buy_exec = buy_edge = None
        buy_arb = 0
        if yes_ask is not None and no_ask is not None:
            buy_pair_cost = yes_ask + no_ask
            buy_exec = min(yes_ask_sz or 0, no_ask_sz or 0)
            buy_edge = 1.0 - buy_pair_cost
            min_buy = min(min_buy, buy_pair_cost)
            # 净edge: 毛 - gas摊到每配对(按 pair_cost 计本金，gas固定)
            net_per_pair = buy_edge - (gas_per_pair / max(buy_exec, 1)) if buy_exec else buy_edge - gas_per_pair
            if buy_edge > 0 and net_per_pair > 0 and buy_exec > 0:
                buy_arb = 1
                n_buy_arb += 1
                edge_usd = buy_edge * buy_exec
                best_buy_edge_usd = max(best_buy_edge_usd, edge_usd)

        # 卖边
        sell_proceeds = sell_exec = sell_edge = None
        sell_arb = 0
        if yes_bid is not None and no_bid is not None:
            sell_proceeds = yes_bid + no_bid
            sell_exec = min(yes_bid_sz or 0, no_bid_sz or 0)
            sell_edge = sell_proceeds - 1.0
            max_sell = max(max_sell, sell_proceeds)
            if sell_edge > 0 and sell_exec > 0 and sell_edge * sell_exec > gas_per_pair:
                sell_arb = 1
                n_sell_arb += 1

        rows.append((
            ts, run_id, m["slug"], m["question"][:120],
            m["liquidity"], m["volume"],
            yes_ask, yes_ask_sz, no_ask, no_ask_sz, buy_pair_cost, buy_exec, buy_edge,
            yes_bid, yes_bid_sz, no_bid, no_bid_sz, sell_proceeds, sell_exec, sell_edge,
            buy_arb, sell_arb,
        ))

    dur = time.time() - t0
    return {
        "run_id": run_id, "ts": ts, "rows": rows,
        "n_markets": len(rows), "n_buy_arb": n_buy_arb, "n_sell_arb": n_sell_arb,
        "min_buy_pair_cost": None if min_buy > 9 else min_buy,
        "max_sell_proceeds": max_sell or None,
        "best_buy_edge_usd": best_buy_edge_usd, "duration_s": dur,
    }


def persist(conn: sqlite3.Connection, res: dict):
    conn.executemany("""
        INSERT INTO clob_pair_log (
            ts, run_id, market_slug, question, liquidity, volume,
            yes_ask, yes_ask_size, no_ask, no_ask_size, buy_pair_cost, buy_exec_pairs, buy_edge_per_pair,
            yes_bid, yes_bid_size, no_bid, no_bid_size, sell_pair_proceeds, sell_exec_pairs, sell_edge_per_pair,
            buy_arb, sell_arb
        ) VALUES (?,?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?,?,?, ?,?)
    """, res["rows"])
    conn.execute("""
        INSERT OR REPLACE INTO clob_pair_runs
        (run_id, ts, n_markets, n_buy_arb, n_sell_arb, min_buy_pair_cost,
         max_sell_proceeds, best_buy_edge_usd, duration_s)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (res["run_id"], res["ts"], res["n_markets"], res["n_buy_arb"], res["n_sell_arb"],
          res["min_buy_pair_cost"], res["max_sell_proceeds"], res["best_buy_edge_usd"], res["duration_s"]))
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description="CLOB pair-cost 前向记录器")
    ap.add_argument("--limit", type=int, default=80, help="扫描 top-N 流动市场")
    ap.add_argument("--gas-per-pair", type=float, default=DEFAULT_GAS_PER_PAIR)
    ap.add_argument("--once", action="store_true", help="只跑一次(默认也是一次)")
    args = ap.parse_args()

    _acquire_lock()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print("=" * 72)
    print("📡 CLOB Pair-Cost 前向记录器")
    print(f"   DB: {DB_PATH}")
    print(f"   扫描: top-{args.limit} 流动市场  gas/配对: ${args.gas_per_pair}")
    print("=" * 72)

    res = scan_once(args.limit, args.gas_per_pair)

    # 空运行不入库：Gamma/CLOB 瞬时失败导致 0 有效市场时，写库只会污染数据
    # 并骗过看门狗的新鲜度检查。宁可不写，让"无新鲜数据"自然触发停摆告警。
    if res["n_markets"] == 0:
        print("\n⚠️ 本轮 0 有效市场（Gamma/CLOB 瞬时失败），跳过写库，退出码1")
        conn.close()
        sys.exit(1)

    persist(conn, res)

    print(f"\n扫描完成 ({res['duration_s']:.1f}s):")
    print(f"   有效市场: {res['n_markets']}")
    print(f"   买边套利(净>0): {res['n_buy_arb']} 个")
    print(f"   卖边套利(净>0): {res['n_sell_arb']} 个")
    mb = res["min_buy_pair_cost"]
    ms = res["max_sell_proceeds"]
    print(f"   最低买边配对成本: {mb:.4f}" if mb is not None else "   最低买边配对成本: n/a")
    print(f"   最高卖边配对收入: {ms:.4f}" if ms is not None else "   最高卖边配对收入: n/a")
    if res["n_buy_arb"] or res["n_sell_arb"]:
        print(f"   🟢 发现可成交套利! 最优买边毛利: ${res['best_buy_edge_usd']:.2f}")
        # 列出套利市场
        cur = conn.execute("""
            SELECT market_slug, buy_pair_cost, buy_exec_pairs, sell_pair_proceeds, sell_exec_pairs
            FROM clob_pair_log WHERE run_id=? AND (buy_arb=1 OR sell_arb=1)
            ORDER BY buy_pair_cost ASC LIMIT 10
        """, (res["run_id"],))
        for r in cur.fetchall():
            print(f"      {r[0][:45]:<45} buy_cost={r[1]} (×{r[2]}) sell_proc={r[3]} (×{r[4]})")
    else:
        print("   ⚪ 本轮无可成交套利(符合预期:有效市场配对成本≈$1)")

    conn.close()
    print("=" * 72)


if __name__ == "__main__":
    main()
