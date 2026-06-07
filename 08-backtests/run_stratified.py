#!/usr/bin/env python3
"""
分层归因 — 在现有"全样本为负"的结论上，逐格寻找正 alpha 子集。

动机(2026-06-08):follow/contrarian/pzero 的全样本 OOS 全 🔴。
问题不是"再积累数据"(test_n 已数百~上千，均值仍为负是稳的结论)，
而是"现有数据里是否存在任一条件组合(市场类别 × 鲸鱼规模)使资金加权净收益为正"。

方法:
  1. 复用 engine 跑两套信号(follow=跟、contrarian=逆)。
  2. 逐信号配对 (Signal, TradeResult)，保留鲸鱼名义额(notional)用于规模分桶。
  3. 按 market 类别 × notional 桶聚合，算资金加权净收益(诚实之锚)+ 等权 + 胜率。
  4. 对任一全样本正 alpha 的格子，做单次 train/test 切分一致性检验
     (受限于仅 1 月数据，只能切一刀；正格若两段同号才有意义)。

诚实纪律:资金加权(CW)为准。CW>0 才叫"每投入一美元真的赚"。
样本仅 2026-05~06 一个月，任何正格都需打折，记为"待复验候选"而非"已验证"。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_stratified.py
  PYTHONPATH=08-backtests python3 08-backtests/run_stratified.py --horizon +1d --strategy contrarian --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS
from engine.data import connect, load_price_series, load_signals
from engine.portfolio import simulate_one
from strategies.contrarian_whale import generate_contrarian_signals

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# ---------- 市场分类 ----------

_SPORTS_PREFIX = (
    "nba-", "mlb-", "nhl-", "nfl-", "epl-", "cs2-", "fl1-", "lal-", "ucl-",
    "ufc-", "atp-", "wta-", "lol-", "dota-", "mls-", "f1-", "bun-", "lig-",
    "laliga-", "seriea-", "pga-", "sea-", "tor-", "esp-",
)
_SPORTS_RE = re.compile(r"^[a-z]{3}-[a-z]{3}-20\d\d")  # 三字母对阵 e.g. sea-tor-2026


def categorize(m: str) -> str:
    """把市场 slug 归到经济含义不同的类别。覆盖不到的归 other。"""
    if re.match(r"^(btc|eth|sol|xrp)-updown", m):
        return "crypto-updown-5m"          # 5分钟涨跌，高频赌局
    if m.startswith(_SPORTS_PREFIX) or _SPORTS_RE.match(m) or "-pga-" in m or m.startswith("2026-pga"):
        return "sports"
    if "temperature" in m:
        return "weather"
    if any(k in m for k in ("wti", "brent", "oil-", "natural-gas", "-gas-")):
        return "commodity"
    if any(k in m for k in ("bitcoin", "ethereum", "solana", "-xrp-", "dogecoin", "-btc-", "-eth-")):
        return "crypto-event"             # 加密事件(非5分钟)
    if any(k in m for k in ("fed-", "interest-rate", "interest-rates", "cpi", "inflation",
                            "gdp", "recession", "rate-cut", "rate-hike", "jobs-report")):
        return "econ-fed"
    if any(k in m for k in ("iran", "trump", "biden", "election", "ukraine", "russia",
                            "israel", "gaza", "hormuz", "alberta", "nuclear", "regime",
                            "ceasefire", "peace-deal", "-war", "war-", "starmer", "china",
                            "france", "epstein", "us-strike", "putin", "zelensky")):
        return "politics-geo"
    if any(k in m for k in ("elon", "musk", "mrbeast", "openai", "tweet", "amazon",
                            "apple", "tesla", "spacex", "twitter")):
        return "tweets-corp"
    return "other"


def size_bucket(notional: float) -> str:
    if notional < 1_000:
        return "0-1k"
    if notional < 10_000:
        return "1k-10k"
    if notional < 50_000:
        return "10k-50k"
    return "50k+"


SIZE_ORDER = ["0-1k", "1k-10k", "10k-50k", "50k+"]


# ---------- 聚合 ----------

def cell_stats(pairs: list[tuple[float, float]]) -> dict:
    """
    pairs: [(net_ret, deployable), ...]
    返回 n / 等权净 / 资金加权净 / 等权胜率 / 总可部署 / 类夏普。
    """
    if not pairs:
        return {"n": 0}
    nets = [p[0] for p in pairs]
    deps = [p[1] for p in pairs]
    w = sum(deps)
    ew = mean(nets)
    sd = pstdev(nets) if len(nets) > 1 else 0.0
    cw = (sum(n * d for n, d in pairs) / w) if w > 0 else None
    return {
        "n": len(pairs),
        "ew_net": ew,
        "cw_net": cw,
        "win_rate": sum(1 for n in nets if n > 0) / len(nets),
        "deployable": w,
        "sharpe_like": (ew / sd) if sd > 0 else None,
    }


def verdict_symbol(st: dict, min_n: int) -> str:
    n = st.get("n", 0)
    if n < min_n:
        return "⚪"  # 样本不足
    ew = st.get("ew_net")
    cw = st.get("cw_net")
    if ew is None or ew <= 0:
        return "🔴"  # 等权都负
    if cw is not None and cw > 0:
        return "🟢"  # 资金加权正，可放大
    return "🟡"      # 仅等权正，薄市场


# ---------- 主流程 ----------

def build_pairs(signals, prices, cost, mode, horizon):
    """逐信号配对，返回 [(sig, TradeResult)]，保留 notional。"""
    out = []
    for sig in signals:
        ps = prices.get(sig.market)
        if ps is None:
            continue
        r = simulate_one(sig, ps, cost, mode, horizon)
        if r is not None:
            out.append((sig, r))
    return out


def stratify(pairs):
    """按 (category, size_bucket) 分桶，返回 {(cat,size): [(net,dep)]} 及边际。"""
    cells = defaultdict(list)
    by_cat = defaultdict(list)
    by_size = defaultdict(list)
    for sig, r in pairs:
        cat = categorize(sig.market)
        size = size_bucket(sig.notional)
        item = (r.net_ret, r.deployable)
        cells[(cat, size)].append(item)
        by_cat[cat].append(item)
        by_size[size].append(item)
    return cells, by_cat, by_size


def print_marginal(title, groups, min_n):
    print(f"\n{title}")
    print(f"  {'分组':<20} {'n':>5} {'等权净%':>9} {'资金权净%':>10} {'胜率':>6} {'判':>3}")
    rows = []
    for k, items in groups.items():
        st = cell_stats(items)
        rows.append((k, st))
    rows.sort(key=lambda x: (x[1].get("cw_net") if x[1].get("cw_net") is not None else -9), reverse=True)
    for k, st in rows:
        if st["n"] == 0:
            continue
        ew = st["ew_net"] * 100
        cw = st["cw_net"] * 100 if st["cw_net"] is not None else float("nan")
        wr = st["win_rate"] * 100
        sym = verdict_symbol(st, min_n)
        print(f"  {k:<20} {st['n']:>5} {ew:>+9.2f} {cw:>+10.2f} {wr:>5.0f}% {sym:>3}")


def find_positive_cells(cells, min_n):
    """返回资金加权>0 且 n>=min_n 的格子，按 CW 降序。"""
    pos = []
    for (cat, size), items in cells.items():
        st = cell_stats(items)
        if st["n"] >= min_n and st.get("cw_net") is not None and st["cw_net"] > 0 and st["ew_net"] > 0:
            pos.append(((cat, size), st))
    pos.sort(key=lambda x: x[1]["cw_net"], reverse=True)
    return pos


def consistency_check(signals, prices, cost, mode, horizon, cat, size, cutoff, min_n):
    """对某个正格做单次 train/test 切分，看两段是否同号。"""
    def sub(sigs):
        ps = build_pairs(sigs, prices, cost, mode, horizon)
        items = [(r.net_ret, r.deployable) for s, r in ps
                 if categorize(s.market) == cat and size_bucket(s.notional) == size]
        return cell_stats(items)

    train = sub([s for s in signals if s.sig_date < cutoff])
    test = sub([s for s in signals if s.sig_date >= cutoff])
    return train, test


def main():
    ap = argparse.ArgumentParser(description="分层归因：逐格找正 alpha 子集")
    ap.add_argument("--db", default=None)
    ap.add_argument("--strategy", default="both", choices=["follow", "contrarian", "both"])
    ap.add_argument("--horizon", default="resolution", choices=["+1d", "+3d", "+7d", "resolution"])
    ap.add_argument("--cost", default="base", choices=list(PRESETS))
    ap.add_argument("--entry-mode", default="realistic", choices=["realistic", "optimistic"])
    ap.add_argument("--min-notional", type=float, default=0.0, help="跟鲸鱼信号的最小名义额过滤")
    ap.add_argument("--min-n", type=int, default=20, help="格子最小样本，低于此判⚪")
    ap.add_argument("--cutoff", default="2026-05-23", help="一致性检验切分点 train<cutoff<=test")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cost = PRESETS[args.cost]
    H = args.horizon
    cutoff = date.fromisoformat(args.cutoff)

    conn = connect(args.db)
    prices = load_price_series(conn)

    strategies = {}
    if args.strategy in ("follow", "both"):
        strategies["follow"] = load_signals(conn, prices, sides=("BUY",), min_notional=args.min_notional)
    if args.strategy in ("contrarian", "both"):
        strategies["contrarian"] = generate_contrarian_signals(conn, prices, min_notional=max(args.min_notional, 1000.0))
    conn.close()

    print("=" * 78)
    print("🔬 分层归因 — 逐格寻找正 alpha 子集")
    print("=" * 78)
    print(f"持有期: {H}  成本: {args.cost}  入场: {args.entry_mode}  最小格样本: {args.min_n}")

    json_out = {"generated_at": datetime.now().isoformat(), "horizon": H,
                "cost": args.cost, "entry_mode": args.entry_mode,
                "min_n": args.min_n, "strategies": {}}

    for sname, sigs in strategies.items():
        pairs = build_pairs(sigs, prices, cost, args.entry_mode, H)
        cells, by_cat, by_size = stratify(pairs)

        # 全样本基线
        all_items = [(r.net_ret, r.deployable) for _, r in pairs]
        base = cell_stats(all_items)

        print("\n" + "─" * 78)
        print(f"▶ 策略: {sname}   可标记交易: {len(pairs)} 笔")
        if base["n"]:
            print(f"  全样本基线: 等权净 {base['ew_net']*100:+.2f}%  "
                  f"资金权净 {base['cw_net']*100:+.2f}%  胜率 {base['win_rate']*100:.0f}%  "
                  f"{verdict_symbol(base, args.min_n)}")

        print_marginal("【按市场类别】", by_cat, args.min_n)
        print_marginal("【按鲸鱼规模】", by_size, args.min_n)

        # 正格搜索
        pos = find_positive_cells(cells, args.min_n)
        print(f"\n【正 alpha 候选格】(资金加权>0 且 n≥{args.min_n}):")
        if not pos:
            print("  ❌ 无。所有样本充足的格子资金加权净收益均≤0。")
        else:
            for (cat, size), st in pos:
                print(f"  🟢 {cat} × {size}: n={st['n']}, "
                      f"等权 {st['ew_net']*100:+.2f}%, 资金权 {st['cw_net']*100:+.2f}%, "
                      f"胜率 {st['win_rate']*100:.0f}%, 可部署 ${st['deployable']:,.0f}")
                # 一致性检验
                tr, te = consistency_check(sigs, prices, cost, args.entry_mode, H,
                                           cat, size, cutoff, args.min_n)
                tr_cw = tr.get("cw_net")
                te_cw = te.get("cw_net")
                tr_s = f"{tr_cw*100:+.2f}%" if tr_cw is not None else "n/a"
                te_s = f"{te_cw*100:+.2f}%" if te_cw is not None else "n/a"
                consistent = (tr_cw is not None and te_cw is not None
                              and tr_cw > 0 and te_cw > 0)
                flag = "✅两段同正" if consistent else "⚠️两段不一致/样本薄"
                print(f"       └ 一致性(切{cutoff}): train n={tr.get('n',0)} CW {tr_s} | "
                      f"test n={te.get('n',0)} CW {te_s}  {flag}")

        json_out["strategies"][sname] = {
            "n_markable": len(pairs),
            "baseline": base,
            "by_category": {k: cell_stats(v) for k, v in by_cat.items()},
            "by_size": {k: cell_stats(v) for k, v in by_size.items()},
            "positive_cells": [
                {"category": c, "size": s, **st} for (c, s), st in pos
            ],
        }

    print("\n" + "=" * 78)
    print("⚖️ 结论:见上方各策略「正 alpha 候选格」。无候选=现有数据无可放大 edge。")
    print("=" * 78)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        p = RESULTS_DIR / f"stratified_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps(json_out, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
