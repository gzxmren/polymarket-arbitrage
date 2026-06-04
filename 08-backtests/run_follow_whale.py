#!/usr/bin/env python3
"""
跟鲸鱼回测 — 入口
用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_follow_whale.py
  PYTHONPATH=08-backtests python3 08-backtests/run_follow_whale.py \
      --entry-mode realistic --cost base --min-notional 100 --json

设计见 docs/BACKTEST_DESIGN_2026-06-04.md。诚实口径:样本仅 ~1 月、逐日粒度、
仅二元 Yes/No、选择偏差,结论需复核。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS, CostModel
from engine.data import connect, load_price_series
from engine.metrics import horizon_summary, verdict, whale_alpha
from engine.portfolio import run_backtest
from strategies.follow_whale import generate_signals

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_HORIZONS = ["+1d", "+3d", "+7d", "resolution"]

DISCLAIMER = [
    "样本仅约 1 个月(2026-05-04~06-04),只能测毛效应,统计稳健性弱。",
    "逐日价格粒度,非逐笔/盘口;入场价为近似。",
    "仅二元 Yes/No 市场(价格表只存 Yes 价);多结果市场被排除。",
    "快照覆盖薄:多数市场跟踪不到结算 → 远期/结算样本远小于入场样本。",
    "选择偏差:信号来自被监控的鲸鱼名单,非全市场随机样本。",
    "结算用终值价(收敛 0/1)近似,非链上 resolution 事件。",
]


def _fmt(v, pct=False):
    if v is None:
        return "  —  "
    return f"{v*100:+.2f}%" if pct else f"{v:.3f}"


def _print_horizon(h: str, summ: dict):
    net, gross = summ["net"], summ["gross"]
    print(f"\n  ── 持有期 {h} ──")
    print(f"    样本 n={net['n']}  |  毛收益均值 {_fmt(gross['mean'], 1)}  |  "
          f"扣成本净均值 {_fmt(net['mean'], 1)}  |  胜率 "
          f"{_fmt(net['win_rate'], 1) if net['win_rate'] is not None else '—'}")
    if net["n"]:
        print(f"    中位 {_fmt(net['median'],1)}  类夏普 {_fmt(net['sharpe_like'])}  "
              f"区间[{_fmt(net['min'],1)}, {_fmt(net['max'],1)}]  "
              f"可部署本金 ${summ['total_deployable']:,.0f}")
        cw = summ["capital_weighted"]
        print(f"    📊 资金加权净均值 {_fmt(cw['mean_net'],1)}  "
              f"(按资金胜率 {_fmt(cw['win_rate'],1)})  ← 可放大口径,诚实之锚")
    ro = summ["resolved_only_net"]
    if ro["n"]:
        print(f"    (仅已结算 n={ro['n']}: 净均值 {_fmt(ro['mean'],1)} 胜率 {_fmt(ro['win_rate'],1)})")
    strata = summ["by_markable_days"]
    if strata:
        cells = [f"{k}:n={v['n']},{_fmt(v['mean'],1)}" for k, v in strata.items()]
        print(f"    按快照天数分层: {'  '.join(cells)}")


def main():
    ap = argparse.ArgumentParser(description="跟鲸鱼策略回测")
    ap.add_argument("--db", default=None, help="DB 路径(默认用 config,支持 POLYMARKET_DB)")
    ap.add_argument("--entry-mode", choices=["realistic", "optimistic"], default="realistic")
    ap.add_argument("--cost", choices=list(PRESETS), default="base", help="成本预设档")
    ap.add_argument("--cost-scan", action="store_true", help="对所有成本档做敏感性扫描")
    ap.add_argument("--min-notional", type=float, default=0.0, help="最小跟单名义(过滤小单)")
    ap.add_argument("--include-sell", action="store_true", help="纳入 SELL 信号")
    ap.add_argument("--min-whale-trades", type=int, default=5, help="鲸鱼 alpha 最小出现次数")
    ap.add_argument("--top-whales", type=int, default=15)
    ap.add_argument("--horizons", nargs="+", default=DEFAULT_HORIZONS)
    ap.add_argument("--json", action="store_true", help="结果落 JSON 到 results/")
    ap.add_argument("--test", action="store_true", help="测试模式(占位:不写生产目录)")
    args = ap.parse_args()

    conn = connect(args.db)
    print("加载价格序列 / 信号 …")
    prices = load_price_series(conn)
    signals = generate_signals(conn, prices,
                               include_sell=args.include_sell,
                               min_notional=args.min_notional)
    conn.close()

    print("=" * 68)
    print("📊 跟鲸鱼策略回测报告")
    print("=" * 68)
    print(f"生成: {datetime.now():%Y-%m-%d %H:%M}  |  入场口径: {args.entry_mode}")
    print(f"可回测宇宙: {len(signals)} 信号 / "
          f"{len({s.wallet for s in signals})} 鲸鱼 / "
          f"{len({s.market for s in signals})} 市场 / "
          f"覆盖 {len(prices)} 市场价格序列")
    print(f"过滤: min_notional=${args.min_notional:,.0f}  include_sell={args.include_sell}")

    cost_presets = list(PRESETS.items()) if args.cost_scan else [(args.cost, PRESETS[args.cost])]

    json_out: dict = {
        "generated_at": datetime.now().isoformat(),
        "entry_mode": args.entry_mode,
        "universe": {
            "signals": len(signals),
            "whales": len({s.wallet for s in signals}),
            "markets": len({s.market for s in signals}),
        },
        "disclaimer": DISCLAIMER,
        "by_cost": {},
    }

    for cost_name, cost in cost_presets:
        print("\n" + "█" * 68)
        print(f"成本档: {cost_name}  {cost.describe()}")
        print("█" * 68)
        results = run_backtest(signals, prices, cost, args.entry_mode, args.horizons)

        cost_block: dict = {"cost": cost.describe(), "horizons": {}, "verdict": {}}
        for h in args.horizons:
            summ = horizon_summary(results[h])
            _print_horizon(h, summ)
            cost_block["horizons"][h] = summ
            cost_block["verdict"][h] = verdict(
                summ["net"], summ["capital_weighted"]["mean_net"])

        # 鲸鱼 alpha:以 resolution(无则 +7d)为主口径
        primary = "resolution" if "resolution" in results else args.horizons[-1]
        wa = whale_alpha(results[primary], min_trades=args.min_whale_trades)
        print(f"\n  🐋 鲸鱼 alpha(口径={primary}, 最小{args.min_whale_trades}单, 按资金加权排序）")
        if wa:
            print("    最值得跟 ↑")
            for w in wa[:args.top_whales]:
                print(f"      {w.wallet[:10]}…  n={w.n:3d}  资金加权 {_fmt(w.mean_net_cw,1)}  "
                      f"等权 {_fmt(w.mean_net,1)}  胜率 {_fmt(w.win_rate,1)}")
            if len(wa) > args.top_whales:
                print("    最该避开 ↓")
                for w in wa[-5:]:
                    print(f"      {w.wallet[:10]}…  n={w.n:3d}  资金加权 {_fmt(w.mean_net_cw,1)}  "
                          f"等权 {_fmt(w.mean_net,1)}  胜率 {_fmt(w.win_rate,1)}")
        else:
            print("    (无鲸鱼达到最小出现次数门槛)")
        cost_block["whale_alpha_primary_horizon"] = primary
        cost_block["whale_alpha_top"] = [vars(w) for w in wa[:args.top_whales]]

        # 判决:以 resolution 净为准
        print("\n  ⚖️ 判决:")
        for h in args.horizons:
            print(f"    [{h}] {cost_block['verdict'][h]}")

        json_out["by_cost"][cost_name] = cost_block

    print("\n" + "=" * 68)
    print("⚠️ 诚实声明(结论须在此前提下解读):")
    for d in DISCLAIMER:
        print(f"  · {d}")
    print("=" * 68)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        out_path = RESULTS_DIR / f"follow_whale_{datetime.now():%Y%m%d_%H%M%S}.json"
        out_path.write_text(json.dumps(json_out, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
