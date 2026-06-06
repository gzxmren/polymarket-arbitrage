#!/usr/bin/env python3
"""
跟鲸鱼 — 样本外(Out-Of-Sample)验证
回答唯一关键问题:"精选鲸鱼"是真 edge 还是过拟合?

方法(诚实纪律,见 docs/BACKTEST_DESIGN_2026-06-04.md):
  1. 按 cutoff 把信号切成 train(在前)/ test(在后)。
  2. 在 TRAIN 上算每鲸鱼 alpha,挑出"训练段赢家"(train 净>阈值 且 出现≥min)。
  3. 在 TEST 上只跟这些被选中的鲸鱼,看扣成本后是否仍正 → 样本外检验。
  4. 关键科学检验:train 表现 vs test 表现的相关性。
     相关≈0 → 挑过去赢家=未来随机 → 没有可跟的持续 edge(过拟合)。

⚠️ 样本仅 1 月,切分后两段都更薄,统计力弱;结论很可能是"数据不足"。
   这本身就是诚实答案:没有足够证据支持"跟精选鲸鱼能赚钱"。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS
from engine.data import connect, load_price_series
from engine.metrics import _cap_stats, pearson
from engine.portfolio import run_backtest
from strategies.follow_whale import generate_signals

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _agg(rets):
    if not rets:
        return {"n": 0, "mean": None, "win_rate": None, "median": None}
    s = sorted(rets)
    return {
        "n": len(rets),
        "mean": mean(rets),
        "median": s[len(s) // 2],
        "win_rate": sum(1 for r in rets if r > 0) / len(rets),
    }


def _whale_table(results, min_trades):
    """{wallet: {n, mean, win}} over given TradeResults."""
    by_w = {}
    for r in results:
        by_w.setdefault(r.wallet, []).append(r.net_ret)
    return {w: {"n": len(v), "mean": mean(v),
                "win": sum(1 for x in v if x > 0) / len(v)}
            for w, v in by_w.items() if len(v) >= min_trades}


def main():
    ap = argparse.ArgumentParser(description="跟鲸鱼样本外验证")
    ap.add_argument("--db", default=None)
    ap.add_argument("--cutoff", default="2026-05-19", help="train < cutoff <= test")
    ap.add_argument("--horizon", default="resolution",
                    choices=["+1d", "+3d", "+7d", "resolution"])
    ap.add_argument("--cost", default="base", choices=list(PRESETS))
    ap.add_argument("--entry-mode", default="realistic", choices=["realistic", "optimistic"])
    ap.add_argument("--min-train-trades", type=int, default=3,
                    help="鲸鱼在 train 段至少多少笔才纳入选拔")
    ap.add_argument("--min-test-trades", type=int, default=2,
                    help="持续性检验:鲸鱼在 test 段至少多少笔")
    ap.add_argument("--select-threshold", type=float, default=0.0,
                    help="train 净均值 > 此值 即视为'训练段赢家'")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cutoff = date.fromisoformat(args.cutoff)
    cost = PRESETS[args.cost]
    H = args.horizon

    conn = connect(args.db)
    prices = load_price_series(conn)
    all_sigs = generate_signals(conn, prices)
    conn.close()

    train_sigs = [s for s in all_sigs if s.sig_date < cutoff]
    test_sigs = [s for s in all_sigs if s.sig_date >= cutoff]

    train_res = run_backtest(train_sigs, prices, cost, args.entry_mode, [H])[H]
    test_res = run_backtest(test_sigs, prices, cost, args.entry_mode, [H])[H]

    print("=" * 70)
    print("🔬 跟鲸鱼 — 样本外(OOS)验证")
    print("=" * 70)
    print(f"切分: train < {cutoff} <= test  |  持有期: {H}  |  成本: {args.cost}  |  入场: {args.entry_mode}")
    print(f"可标记交易: train {len(train_res)} 笔 / test {len(test_res)} 笔")

    # ---- 基线:test 段无差别跟全部 ----
    base = _agg([r.net_ret for r in test_res])
    base_cw = _cap_stats(test_res)
    print(f"\n【基线】test 段无差别跟全部鲸鱼:")
    print(f"  n={base['n']}  等权净 {base['mean']*100:+.2f}%  "
          f"中位 {base['median']*100:+.2f}%  胜率 {base['win_rate']*100:.1f}%")
    if base_cw["mean_net"] is not None:
        print(f"  资金加权净 {base_cw['mean_net']*100:+.2f}%(诚实之锚)")

    # ---- 选拔:train 段赢家 ----
    train_tbl = _whale_table(train_res, args.min_train_trades)
    selected = {w for w, st in train_tbl.items() if st["mean"] > args.select_threshold}
    print(f"\n【选拔】train 段出现≥{args.min_train_trades}笔的鲸鱼: {len(train_tbl)} 个; "
          f"其中净均值>{args.select_threshold:.0%} 的'训练赢家': {len(selected)} 个")

    # ---- 样本外检验:test 段只跟被选中的鲸鱼 ----
    sel_test = [r for r in test_res if r.wallet in selected]
    sel = _agg([r.net_ret for r in sel_test])
    sel_cw = _cap_stats(sel_test)
    print(f"\n【样本外检验】test 段只跟'训练赢家':")
    if sel["n"]:
        print(f"  n={sel['n']}  等权净 {sel['mean']*100:+.2f}%  "
              f"中位 {sel['median']*100:+.2f}%  胜率 {sel['win_rate']*100:.1f}%")
        if sel_cw["mean_net"] is not None:
            print(f"  资金加权净 {sel_cw['mean_net']*100:+.2f}%(诚实之锚)")
        delta = (sel["mean"] - base["mean"]) * 100
        print(f"  vs 基线(等权): {delta:+.2f} 个百分点 "
              f"({'选拔有效✅' if delta > 0 else '选拔无效❌'})")
    else:
        print("  ⚠️ 训练赢家在 test 段无可标记交易 → 无法样本外验证(数据不足)。")

    # ---- 持续性:train vs test 相关(最硬的检验)----
    test_tbl = _whale_table(test_res, args.min_test_trades)
    both = sorted(set(train_tbl) & set(test_tbl))
    xs = [train_tbl[w]["mean"] for w in both]
    ys = [test_tbl[w]["mean"] for w in both]
    rho = pearson(xs, ys)
    print(f"\n【持续性检验】train净均值 vs test净均值(同时满足 train≥{args.min_train_trades}"
          f"、test≥{args.min_test_trades} 的鲸鱼):")
    print(f"  可比鲸鱼数: {len(both)}")
    if rho is None:
        print(f"  ⚠️ 样本太少({len(both)}),相关系数无意义 → 没有足够证据。")
    else:
        print(f"  Pearson r = {rho:+.3f}")
        if rho > 0.3:
            print("  → 训练表现对测试有正预测力,'精选鲸鱼'值得继续(仍需更多数据复核)。")
        elif rho < -0.1:
            print("  → 负相关/均值回归:训练赢家测试反而变差 → 不是 edge,是噪声。")
        else:
            print("  → 相关≈0:挑过去赢家≈未来随机 → 没有可跟的持续 edge(过拟合)。")

    # ---- 诚实判决 ----
    print("\n" + "=" * 70)
    print("⚖️ 诚实判决:")
    if sel["n"] and base["n"]:
        oos_pos = sel["mean"] > 0 and sel["mean"] > base["mean"]
    else:
        oos_pos = False
    persistent = rho is not None and rho > 0.3
    if oos_pos and persistent:
        print("  🟡 弱阳性:样本外选拔有效且训练表现有持续性。但样本仅1月,需更多数据确认,"
              "暂不足以投实盘。")
    elif (rho is not None and len(both) >= 10) or (sel["n"] and sel["n"] >= 20):
        print("  🔴 证伪:样本外无优势 / 训练表现无持续性 → '精选鲸鱼'是过拟合,不是真 edge。")
    else:
        print("  ⚪ 数据不足:切分后样本太薄,无法对'精选鲸鱼'下结论。"
              "诚实答案=证据不够,需引擎复活持续采集后再验。")
    print("=" * 70)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        out = {
            "generated_at": datetime.now().isoformat(),
            "cutoff": args.cutoff, "horizon": H, "cost": args.cost,
            "train_markable": len(train_res), "test_markable": len(test_res),
            "baseline_test": base,
            "n_train_whales_qualified": len(train_tbl),
            "n_selected_winners": len(selected),
            "selected_test": sel,
            "persistence": {"n_comparable": len(both), "pearson_r": rho},
        }
        p = RESULTS_DIR / f"whale_oos_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
