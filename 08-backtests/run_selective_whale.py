#!/usr/bin/env python3
"""
精选跟鲸鱼回测 — 入口
对比"无差别跟全部鲸鱼"(基线) vs "精选过滤"(避开高价大热门 + 只做短周期)。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_selective_whale.py
  PYTHONPATH=08-backtests python3 08-backtests/run_selective_whale.py \
      --cost base --max-entry-price 0.85 --max-dte 14 --json

诚实口径:样本仅 ~1 月、逐日粒度、仅二元 Yes/No、选择偏差(信号源自被监控名单)。
入场价/到期天数过滤均为"入场时已知"条件,不引入前视。结论仍需更多数据复核。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS
from engine.data import connect, load_price_series
from engine.metrics import horizon_summary, verdict
from engine.portfolio import run_backtest
from strategies.follow_whale import generate_signals
from strategies.selective_whale import filter_results

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_HORIZONS = ["+1d", "+3d", "+7d", "resolution"]


def _fmt(v, pct=False):
    if v is None:
        return "  —  "
    return f"{v*100:+.2f}%" if pct else f"{v:.3f}"


def _line(tag: str, summ: dict):
    net = summ["net"]
    if not net["n"]:
        print(f"    {tag:<10} n=0")
        return
    cw = summ["capital_weighted"]["mean_net"]
    print(f"    {tag:<10} n={net['n']:4d}  等权净 {_fmt(net['mean'],1)}  "
          f"资金加权净 {_fmt(cw,1)}  中位 {_fmt(net['median'],1)}  "
          f"胜率 {_fmt(net['win_rate'],1)}")


def main():
    ap = argparse.ArgumentParser(description="精选跟鲸鱼回测")
    ap.add_argument("--db", default=None)
    ap.add_argument("--entry-mode", choices=["realistic", "optimistic"], default="realistic")
    ap.add_argument("--cost", choices=list(PRESETS), default="base")
    ap.add_argument("--min-entry-price", type=float, default=0.0)
    ap.add_argument("--max-entry-price", type=float, default=0.85,
                    help="避开 ≥ 此价的大热门(诊断显示零成本都不赚)")
    ap.add_argument("--max-dte", type=int, default=14,
                    help="只做 end_date 在入场后 ≤ N 天的短周期市场(入场即知,非前视)")
    ap.add_argument("--min-notional", type=float, default=0.0)
    ap.add_argument("--horizons", nargs="+", default=DEFAULT_HORIZONS)
    ap.add_argument("--price-scan", action="store_true",
                    help="扫描 max_entry_price 网格看单调性")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--test", action="store_true", help="占位:不写生产目录")
    args = ap.parse_args()

    cost = PRESETS[args.cost]
    conn = connect(args.db)
    prices = load_price_series(conn)
    signals = generate_signals(conn, prices, min_notional=args.min_notional)
    conn.close()

    results = run_backtest(signals, prices, cost, args.entry_mode, args.horizons)

    print("=" * 72)
    print("📊 精选跟鲸鱼 vs 无差别基线")
    print("=" * 72)
    print(f"生成 {datetime.now():%Y-%m-%d %H:%M}  | 入场 {args.entry_mode} | 成本 {args.cost} "
          f"| min_notional ${args.min_notional:,.0f}")
    print(f"精选条件: 入场价 ∈ [{args.min_entry_price}, {args.max_entry_price})  "
          f"且 到期天数 ≤ {args.max_dte}")

    json_out: dict = {
        "generated_at": datetime.now().isoformat(),
        "entry_mode": args.entry_mode, "cost": args.cost,
        "filter": {"min_entry_price": args.min_entry_price,
                   "max_entry_price": args.max_entry_price,
                   "max_dte": args.max_dte, "min_notional": args.min_notional},
        "horizons": {},
    }

    for h in args.horizons:
        base = horizon_summary(results[h])
        sel = horizon_summary(
            filter_results(results[h], prices,
                           min_entry_price=args.min_entry_price,
                           max_entry_price=args.max_entry_price,
                           max_dte=args.max_dte))
        print(f"\n  ── 持有期 {h} ──")
        _line("基线", base)
        _line("精选", sel)
        bm, sm = base["net"]["mean"], sel["net"]["mean"]
        bcw = base["capital_weighted"]["mean_net"]
        scw = sel["capital_weighted"]["mean_net"]
        if bm is not None and sm is not None:
            keep = sel["net"]["n"] / base["net"]["n"] * 100 if base["net"]["n"] else 0
            dcw = (f"{(scw-bcw)*100:+.2f}pp" if scw is not None and bcw is not None
                   else "—(无容量)")
            print(f"    Δ等权 {(sm-bm)*100:+.2f}pp  | "
                  f"Δ资金加权 {dcw}(诚实)  | 保留信号 {keep:.0f}%")
        print(f"    判决: {verdict(sel['net'], scw)}")
        json_out["horizons"][h] = {
            "baseline": base["net"],
            "baseline_capital_weighted": base["capital_weighted"],
            "selective": sel["net"],
            "selective_capital_weighted": sel["capital_weighted"],
            "verdict_selective": verdict(sel["net"], scw),
        }

    if args.price_scan:
        print("\n  ── max_entry_price 扫描(horizon=resolution, 固定 max_dte) ──")
        for cap_px in (0.50, 0.65, 0.75, 0.85, 0.95, 1.00):
            sel = horizon_summary(
                filter_results(results["resolution"], prices,
                               max_entry_price=cap_px, max_dte=args.max_dte))
            _line(f"<{cap_px:.2f}", sel)

    print("\n" + "=" * 72)
    print("⚠️ 诚实声明:样本仅~1月、逐日粒度、仅二元、选择偏差;过滤无前视但样本薄,需复核。")
    print("=" * 72)

    if args.json and not args.test:
        RESULTS_DIR.mkdir(exist_ok=True)
        p = RESULTS_DIR / f"selective_whale_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps(json_out, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
