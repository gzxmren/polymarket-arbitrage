#!/usr/bin/env python3
"""
H6 宇宙滑点测量影子盘 —— read-only 测量，非执行层。

目的:用实测的真实盘口，替掉回测里拍脑袋的 150bps 滑点假设。
做法:定时扫描"H6 会下注的那类市场"(vol24h<200k + dte<=14 + 入场侧价<0.85),
     对每个符合条件的 outcome 抓完整订单簿,算"下一笔 $200 市价买单走一遍盘口"
     的实际成交价 vs 中间价 → 真实滑点(bps)。跑几周得到一条真实滑点分布。

⚠️ 盘口排序坑(2026-07-06 实测):Polymarket /book 的 asks 按价格**降序**、bids 按价格**升序**返回,
   最优价在数组**末尾**。clob_api.calculate_spread_from_order_book() 用 [0] 取最优价是错的,
   本脚本自算 best_bid=max(bid)、best_ask=min(ask),买入从最低 ask 开始吃。

口径对齐 08-backtests/run_pzero_oos.py::make_filters (H6 = H1+H2+H3+微仓$200)。
成本模型对照 08-backtests/engine/costs.py。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timezone

# 复用现有 CLOB 封装(自带超时/异常处理,失败返回 None)
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "analysis"))
sys.path.insert(0, _HERE)
import clob_api  # noqa: E402

# ---- H6 宇宙口径(对齐 make_filters) ----
VOL24H_MAX = 200_000.0   # H1 避超流动
VOL24H_MIN = 10_000.0    # 下限:H6 实盘只在鲸鱼下单的活跃市场触发。实测 $1k 太松(放进结算日天气死盘,
                         #       滑点被拉爆到中位92%);$10k 才是 BTC/体育/事件等真实两侧活跃市场(--min-vol 可调)
DTE_MAX = 14             # H2 短周期
PRICE_MAX = 0.85         # H3 避准定局(入场侧价上限)
PRICE_MIN = 0.01         # 下限:排除近 0 的死盘(H6 本身无下限,仅防噪音/除零)
PROBE_NOTIONAL = 200.0   # H6 微仓 $200

GAMMA_PAGE = 100         # 每页市场数
GAMMA_MAX_PAGES = 25     # 分页安全上限

PROD_DB = os.path.join(_HERE, "..", "..", "07-data", "slippage_probe.db")
TEST_DB = "/tmp/slippage_probe_test.db"


def fetch_h6_universe(today: date, dte_max: int):
    """
    按到期日范围高效拉取"未来 dte_max 天内到期"的活跃市场(H6 的 dte 窗口)。
    比按成交量降序翻 2000 个市场快得多——H6 要的是低量、近到期市场,正好落在这个窗口。
    返回 Gamma 市场对象列表(未按 vol/price 过滤,交给 eligible_sides)。
    """
    from datetime import timedelta
    hi = today + timedelta(days=dte_max)
    out = []
    for page in range(GAMMA_MAX_PAGES):
        ep = (f"/markets?closed=false"
              f"&end_date_min={today}T00:00:00Z&end_date_max={hi}T23:59:59Z"
              f"&order=endDate&ascending=true&limit={GAMMA_PAGE}&offset={page * GAMMA_PAGE}")
        # 单页重试:瞬时网络失败(SSL/DNS/IncompleteRead)不该截断整个宇宙——
        # 截断会偏向"最早到期"的死盘,污染滑点分布。
        batch = None
        for attempt in range(3):
            resp = clob_api.fetch_gamma_api(ep)  # 失败返回 None(内部已接住网络异常)
            # /markets 正常返回 list;非 list(如 HTTP200 的 {"error":...})按失败处理,
            # 否则 extend 会把 dict 的 key 串混进 universe,后续 m.get 崩溃。
            if isinstance(resp, list):
                batch = resp
                break
            time.sleep(1.0 * (attempt + 1))
        if batch is None:
            print(f"   ⚠️ 分页在 offset={page * GAMMA_PAGE} 连续失败,universe 可能不完整"
                  f"(已获 {len(out)} 个)", flush=True)
            break
        out.extend(batch)
        if len(batch) < GAMMA_PAGE:
            break  # 正常到达数据末尾
    return out


def best_prices(ob: dict):
    """稳健取最优价:best_bid=最高买价, best_ask=最低卖价(不依赖数组顺序)。"""
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    if best_ask <= 0 or best_bid <= 0 or best_ask < best_bid:
        return None  # 空/交叉盘,不可用
    mid = (best_bid + best_ask) / 2.0
    return best_bid, best_ask, mid


def walk_book_buy(asks: list, notional_usd: float):
    """
    模拟一笔市价买单吃 notional_usd 美元,返回加权成交均价。
    asks=[[price,size],...]; 一份 share 花 price 美元,该档最多可花 price*size 美元。
    从最低卖价开始吃(先按价格升序排序,消除 API 返回顺序影响)。
    返回 (eff_price, filled_usd, levels_used); 深度不足则 filled_usd<notional。
    """
    remaining = notional_usd
    cost = 0.0
    shares = 0.0
    levels = 0
    for price, size in sorted(asks, key=lambda x: x[0]):
        if price <= 0 or size <= 0:
            continue
        level_cap = price * size
        take = min(remaining, level_cap)
        if take <= 0:
            break
        cost += take
        shares += take / price
        remaining -= take
        levels += 1
        if remaining <= 1e-9:
            break
    if shares <= 0:
        return None, 0.0, 0
    eff_price = cost / shares
    return eff_price, cost, levels


def eligible_sides(m: dict, today: date, min_vol: float):
    """
    从一个 Gamma 市场对象产出符合 H6 宇宙的 (side, token_id, gamma_price, vol24h, dte)。
    min_vol<=vol24h<200k + dte∈[0,14] + 该侧价∈(0.01,0.85)。end_date 缺失 → 跳过(对齐 H2 的 None 淘汰)。
    """
    try:
        vol24h = float(m.get("volume24hr", 0) or 0)
    except (ValueError, TypeError):
        return
    if vol24h >= VOL24H_MAX or vol24h < min_vol:
        return
    if not m.get("enableOrderBook"):
        return

    end_iso = (m.get("endDateIso") or "")[:10]
    if not end_iso:
        return
    try:
        end_d = date.fromisoformat(end_iso)
    except ValueError:
        return
    dte = (end_d - today).days
    if not (0 <= dte <= DTE_MAX):
        return

    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        prices = json.loads(m.get("outcomePrices") or "[]")
    except (json.JSONDecodeError, TypeError):
        return
    if len(toks) < 2 or len(prices) < 2:
        return

    for idx, side in ((0, "Yes"), (1, "No")):
        try:
            px = float(prices[idx])
        except (ValueError, TypeError, IndexError):
            continue
        if PRICE_MIN < px < PRICE_MAX:
            yield side, str(toks[idx]), px, vol24h, dte


def ensure_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS slippage_probe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            probe_ts TEXT NOT NULL,
            market_slug TEXT,
            token_id TEXT,
            side TEXT,
            gamma_price REAL,
            vol24h REAL,
            dte INTEGER,
            best_bid REAL,
            best_ask REAL,
            mid REAL,
            spread_bps REAL,
            order_notional REAL,
            eff_fill_price REAL,
            slippage_bps REAL,
            filled_usd REAL,
            fully_filled INTEGER,
            levels_used INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_probe_ts ON slippage_probe(probe_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_probe_slug ON slippage_probe(market_slug)")


def probe(db_path: str, max_markets: int, sleep_s: float, min_vol: float,
          dry_run: bool, verbose: bool):
    probe_ts = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date()

    print(f"📡 拉取未来 {DTE_MAX} 天内到期的活跃市场… (probe_ts={probe_ts})", flush=True)
    markets = fetch_h6_universe(today, DTE_MAX)
    if not markets:
        print("❌ 未拉到市场(网络失败或窗口内无市场),本轮跳过", flush=True)
        return 1
    print(f"   窗口内 {len(markets)} 个市场,筛选 H6 宇宙(vol∈[{min_vol:.0f},{VOL24H_MAX:.0f}) "
          f"px<{PRICE_MAX})…", flush=True)

    # 先把符合条件的 (market, side) 全列出来,再逐个抓盘口(受 max_markets 限制)
    targets = []
    for m in markets:
        slug = m.get("slug", "")
        for side, tok, px, vol24h, dte in eligible_sides(m, today, min_vol):
            targets.append((slug, side, tok, px, vol24h, dte))
    print(f"   H6 宇宙候选 outcome: {len(targets)} 个", flush=True)
    if max_markets and len(targets) > max_markets:
        targets = targets[:max_markets]
        print(f"   受 --max 限制,本轮抓前 {len(targets)} 个", flush=True)

    rows = []
    n_book_fail = 0
    n_thin = 0
    for slug, side, tok, px, vol24h, dte in targets:
        # get_order_book 的 JSON 解析在 clob_api 内部 try 之外,schema 漂移(缺 key/非数字/
        # 非 dict)会抛未捕获异常。这里逐条兜住:一个坏响应只丢这一条,不能崩掉整轮
        # (DB 是循环后一次性写,崩了=本轮全部测量白费=样本静默缺口)。
        try:
            ob = clob_api.get_order_book(tok)
            time.sleep(sleep_s)  # 温柔限速
            if not ob:
                n_book_fail += 1
                continue
            bp = best_prices(ob)
            if bp is None:
                n_book_fail += 1
                continue
            best_bid, best_ask, mid = bp
            spread_bps = (best_ask - best_bid) / mid * 10_000.0
            eff, filled_usd, levels = walk_book_buy(ob["asks"], PROBE_NOTIONAL)
            if eff is None or mid <= 0:
                n_book_fail += 1
                continue
            slippage_bps = (eff - mid) / mid * 10_000.0
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            n_book_fail += 1
            if verbose:
                print(f"   ⚠️ 跳过 {slug[:30]} {side}: {type(e).__name__}: {e}", flush=True)
            continue
        fully = 1 if filled_usd >= PROBE_NOTIONAL - 1e-6 else 0
        if not fully:
            n_thin += 1
        rows.append((probe_ts, slug, tok, side, px, vol24h, dte,
                     best_bid, best_ask, mid, spread_bps,
                     PROBE_NOTIONAL, eff, slippage_bps, filled_usd, fully, levels))
        if verbose:
            flag = "" if fully else "  ⚠️深度不足"
            print(f"   {slug[:42]:42s} {side:3s} mid={mid:.3f} spread={spread_bps:6.0f}bps "
                  f"slip={slippage_bps:6.0f}bps fill=${filled_usd:5.0f}{flag}", flush=True)

    # 汇总
    _summary(rows, n_book_fail, n_thin)

    if dry_run:
        print("🔍 [dry-run] 不写库", flush=True)
        return 0
    if not rows:
        print("⚠️ 无有效数据,不写库", flush=True)
        return 0

    conn = sqlite3.connect(db_path, timeout=60)
    try:
        conn.execute("PRAGMA busy_timeout=60000")
        ensure_table(conn)
        conn.executemany(
            """INSERT INTO slippage_probe
               (probe_ts, market_slug, token_id, side, gamma_price, vol24h, dte,
                best_bid, best_ask, mid, spread_bps, order_notional,
                eff_fill_price, slippage_bps, filled_usd, fully_filled, levels_used)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    print(f"✅ 写入 {len(rows)} 条 → {db_path}", flush=True)
    return 0


def _summary(rows, n_book_fail, n_thin):
    print("─" * 60, flush=True)
    if not rows:
        print(f"📊 本轮 0 条有效; 盘口失败/不可用 {n_book_fail}", flush=True)
        return
    slips = sorted(r[13] for r in rows)
    spreads = sorted(r[10] for r in rows)

    def pctl(xs, q):
        if not xs:
            return float("nan")
        i = min(len(xs) - 1, int(q * len(xs)))
        return xs[i]

    print(f"📊 有效 {len(rows)} 条 | 盘口失败 {n_book_fail} | $200 深度不足 {n_thin} "
          f"({n_thin/len(rows)*100:.0f}%)", flush=True)
    print(f"   $200 买单真实滑点(bps): 中位 {statistics.median(slips):.0f} | "
          f"p25 {pctl(slips,0.25):.0f} | p75 {pctl(slips,0.75):.0f} | "
          f"最差 {slips[-1]:.0f}", flush=True)
    print(f"   顶档买卖价差(bps):       中位 {statistics.median(spreads):.0f} | "
          f"p75 {pctl(spreads,0.75):.0f}", flush=True)
    print(f"   对照:回测 base=50bps / conservative=150bps。中位滑点若 >150 → H6 净利被高估。",
          flush=True)


def main():
    ap = argparse.ArgumentParser(description="H6 宇宙滑点测量影子盘(read-only)")
    ap.add_argument("--test", action="store_true", help="写到 /tmp 测试库,不碰生产 07-data")
    ap.add_argument("--dry-run", action="store_true", help="只测量打印,不写库")
    ap.add_argument("--max", type=int, default=300, help="本轮最多抓多少个 outcome(限速)")
    ap.add_argument("--sleep", type=float, default=0.15, help="每次盘口请求间隔秒")
    ap.add_argument("--min-vol", type=float, default=VOL24H_MIN,
                    help="成交量下限,排除死盘(H6 实盘只在活跃市场触发)")
    ap.add_argument("--verbose", action="store_true", help="逐条打印")
    args = ap.parse_args()

    db_path = TEST_DB if args.test else PROD_DB
    return probe(db_path, args.max, args.sleep, args.min_vol, args.dry_run, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
