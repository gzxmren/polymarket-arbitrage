#!/usr/bin/env python3
"""
P0-A 诊断 — 资金加权(cap-weighted)为什么是负的?

精选策略 +1d: 等权 +52% 但资金加权 −0.85%/每元。本脚本只读地拆解"漏点":
net 收益相对 deployable / 入场价带 / 到期天数(dte) / 市场流动性(cap=volume_24h)
怎么分布,定位"大额可部署的单在哪亏",从而判断哪个'入场即知'的轴上还有正 edge。

纯诊断,不写任何生产数据。复用 08-backtests/engine。
用法: PYTHONPATH=08-backtests python3 08-backtests/diagnose_cap_weighted.py --horizon +1d
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import mean, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS
from engine.data import connect, load_price_series
from engine.portfolio import run_backtest
from strategies.follow_whale import generate_signals
from strategies.selective_whale import days_to_resolution


def cw(rs):
    """资金加权净均值 + 总 deployable。"""
    w = sum(r.deployable for r in rs)
    if w <= 0:
        return None, 0.0
    return sum(r.net_ret * r.deployable for r in rs) / w, w


def ew(rs):
    return mean([r.net_ret for r in rs]) if rs else None


def pearson(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sx, sy = pstdev(xs), pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    return cov / (sx * sy)


def _pct(v):
    return "   —   " if v is None else f"{v*100:+7.2f}%"


def bucket_report(title, rs, keyfn, edges, prices=None, fmt=lambda x: f"{x:g}"):
    """按 keyfn 的值落入 edges 区间分桶,打印每桶 n / 等权 / 资金加权 / 该桶占总资金%。"""
    total_w = sum(r.deployable for r in rs) or 1.0
    print(f"\n  ── 分桶: {title} ──")
    print(f"    {'区间':<16}{'n':>5}{'等权净':>10}{'资金加权净':>12}{'桶内总$':>12}{'占总资金':>9}")
    labels = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        labels.append((lo, hi))
    buckets = {i: [] for i in range(len(labels))}
    other = []
    for r in rs:
        v = keyfn(r)
        if v is None:
            other.append(r)
            continue
        placed = False
        for i, (lo, hi) in enumerate(labels):
            if (lo is None or v >= lo) and (hi is None or v < hi):
                buckets[i].append(r)
                placed = True
                break
        if not placed:
            other.append(r)
    for i, (lo, hi) in enumerate(labels):
        b = buckets[i]
        if not b:
            continue
        c, w = cw(b)
        lab = f"[{fmt(lo) if lo is not None else '-inf'},{fmt(hi) if hi is not None else 'inf'})"
        print(f"    {lab:<16}{len(b):>5}{_pct(ew(b)):>10}{_pct(c):>12}"
              f"{w:>12,.0f}{w/total_w*100:>8.1f}%")
    if other:
        c, w = cw(other)
        print(f"    {'(无值/越界)':<16}{len(other):>5}{_pct(ew(other)):>10}{_pct(c):>12}"
              f"{w:>12,.0f}{w/total_w*100:>8.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", default="+1d", choices=["+1d", "+3d", "+7d", "resolution"])
    ap.add_argument("--cost", default="base", choices=list(PRESETS))
    ap.add_argument("--entry-mode", default="realistic", choices=["realistic", "optimistic"])
    args = ap.parse_args()

    cost = PRESETS[args.cost]
    conn = connect()
    prices = load_price_series(conn)
    signals = generate_signals(conn, prices)
    conn.close()

    H = args.horizon
    rs = run_backtest(signals, prices, cost, args.entry_mode, [H])[H]

    print("=" * 78)
    print(f"📊 P0-A 资金加权漏点诊断 | 持有期 {H} | 成本 {args.cost} | 入场 {args.entry_mode}")
    print("=" * 78)

    # 数据家底
    dates = sorted(r.entry_date for r in rs)
    n_dep0 = sum(1 for r in rs if r.deployable <= 0)
    print(f"信号(全量 BUY): {len(signals)}  | 该持有期可标记: {len(rs)}")
    if dates:
        print(f"入场日范围: {dates[0]} → {dates[-1]}")
    print(f"deployable=0 (无 volume 容量数据)的笔数: {n_dep0} "
          f"({n_dep0/len(rs)*100:.0f}%) —— 这些对资金加权零贡献")

    base_cw, total_w = cw(rs)
    print(f"\n基线: 等权净 {_pct(ew(rs))}  | 资金加权净 {_pct(base_cw)}  "
          f"| 总可部署 ${total_w:,.0f}")

    # 相关性: net 与各轴
    print("\n  ── 相关性(net_ret vs 轴) ──")
    valid = [r for r in rs if r.deployable > 0]
    for name, fn in [
        ("deployable", lambda r: r.deployable),
        ("entry_price", lambda r: r.entry_price),
        ("dte", lambda r: days_to_resolution(r, prices)),
    ]:
        pairs = [(fn(r), r.net_ret) for r in rs if fn(r) is not None]
        rho = pearson([a for a, _ in pairs], [b for _, b in pairs])
        print(f"    net vs {name:<12} r = {('  —  ' if rho is None else f'{rho:+.3f}')}  (n={len(pairs)})")

    # 分桶诊断
    bucket_report("deployable 分位($)", rs, lambda r: r.deployable,
                  [0, 1, 10, 50, 200, 1000, None], fmt=lambda x: f"{x:g}")
    bucket_report("入场价带", rs, lambda r: r.entry_price,
                  [0, 0.15, 0.30, 0.50, 0.70, 0.85, 1.01], fmt=lambda x: f"{x:.2f}")
    bucket_report("到期天数 dte", rs, lambda r: days_to_resolution(r, prices),
                  [0, 2, 4, 7, 14, 30, None], prices=prices, fmt=lambda x: f"{x:g}")
    bucket_report("市场流动性 cap=vol24($)", rs,
                  lambda r: (prices[r.market].yes_on_or_after(r.entry_date) or (None, None, 0))[2],
                  [0, 1000, 10000, 50000, 200000, None], fmt=lambda x: f"{x:g}")

    print("\n" + "=" * 78)
    print("读法: 找'桶内总$占比高 且 资金加权净为负'的桶 = 漏点;"
          "找'资金加权净为正且非极小样本'的桶 = 候选 edge 所在。")
    print("=" * 78)


if __name__ == "__main__":
    main()
