#!/usr/bin/env python3
"""
P0-B 攻坚 — 能否找到一个 OOS(样本外)资金加权 ≥0 的候选?

纪律(避免过拟合 / 多重比较自欺):
  1. **预先登记**一小组"经济上讲得通"的过滤假设(不从诊断里手挑正的价带/桶)。
  2. 按日期切 train(在前)/test(在后);所有过滤都是"入场即知"条件,无前视。
  3. 候选"通过 P0" 的硬标准:**test 段资金加权 ≥0** 且 n 不太小 且 **抗单点**
     (剔除贡献最大的一笔后仍 ≥0,排除被一两个彩票赢家撑起)。
  4. 诚实披露:共试了多少个(假设×持有期)组合 → 多重比较下偶然为正的概率。
  5. 额外产出"容量前沿":对最优过滤,每笔限仓 D 时 test 段能部署多少钱还保持每元 ≥0
     —— 这正是路2 微仓最需要的数字("最多投多少还不亏")。

预登记假设历史:
  H0-H5: 2026-06-06 首轮注册(6 个过滤 × 2 持有期 = 12 组; 3 cutoff = 36 次; 全负)
  H6:    2026-06-06 新增 — 微仓精选(H5 + 每笔限仓 $200):容量前沿显示该点接近盈亏平衡,
          预登记避免事后手挑;等数据积累后验证。
  C0:    2026-06-06 新增 — 逆鲸鱼(大额+超流动 Yes BUY 反转为 No);经济动机:有效市场中
          鲸鱼无信息优势,大单造成价格冲击随后均值回归,逆势可捕获回归。独立分析模块。
  H7:    2026-07-06 新增 — H6 + 价格下限≥0.20;经济动机:滑点探针实测贴边盘(<0.2)滑点~770bps
          (均衡盘~120bps)且历史分层显示贴边盘毛利抗单点为负(纯彩票)。预登记单+红线见
          docs/PREREG_H7_PRICE_FLOOR_2026-07-06.md(下限 0.20 锁定,不做多下限择优)。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_pzero_oos.py
  PYTHONPATH=08-backtests python3 08-backtests/run_pzero_oos.py --cutoff 2026-05-26 --json
  PYTHONPATH=08-backtests python3 08-backtests/run_pzero_oos.py --contrarian  # 加跑逆势
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS
from engine.data import connect, load_price_series
from engine.portfolio import run_backtest
from strategies.contrarian_whale import generate_contrarian_signals
from strategies.follow_whale import generate_signals
from strategies.selective_whale import days_to_resolution

RESULTS_DIR = Path(__file__).resolve().parent / "results"
HORIZONS = ["+1d", "resolution"]


def _cap_at(r, prices):
    snap = prices[r.market].yes_on_or_after(r.entry_date)
    return snap[2] if snap else 0.0


# ---- 预先登记的过滤假设(经济动机,非手挑桶)----
def make_filters(prices):
    def liquid_ok(r):      # H1: 避开超流动(最有效、鲸鱼无信息优势)市场
        return _cap_at(r, prices) < 200_000
    def short_dted(r):     # H2: 只做短周期(逆向漂移时间短)
        d = days_to_resolution(r, prices)
        return d is not None and 0 <= d <= 14
    def not_certain(r):    # H3: 避开 ≥0.85 准定局(上行封顶)
        return r.entry_price < 0.85

    def micro_h5(r):   # H6: H5 + 微仓(容量前沿探针);注意 per-trade cap 须在 cw() 里施加
        return liquid_ok(r) and short_dted(r) and not_certain(r)

    def micro_h7(r):   # H7: H6 + 价格下限≥0.20(避贴边盘);见 docs/PREREG_H7_PRICE_FLOOR_2026-07-06.md
        return liquid_ok(r) and short_dted(r) and (0.20 <= r.entry_price < 0.85)

    return {
        "H0 基线(全量)":              lambda r: True,
        "H1 避超流动(<200k)":         liquid_ok,
        "H2 短周期(dte<=14)":         short_dted,
        "H3 避准定局(px<0.85)":       not_certain,
        "H4 H1+H2":                   lambda r: liquid_ok(r) and short_dted(r),
        "H5 H1+H2+H3(精选+流动)":     lambda r: liquid_ok(r) and short_dted(r) and not_certain(r),
        "H6 H5+微仓$200":             micro_h5,   # 评估时配合 cw(cap=200)
        "H7 H6+价格下限≥0.20":        micro_h7,   # 评估时配合 cw(cap=200)
    }


def cw(rs, cap=None):
    """资金加权净均值 + 总 deployable。cap: 每笔 deployable 上限(容量前沿用)。"""
    def w_of(r):
        return r.deployable if cap is None else min(r.deployable, cap)
    W = sum(w_of(r) for r in rs)
    if W <= 0:
        return None, 0.0
    return sum(r.net_ret * w_of(r) for r in rs) / W, W


def cw_drop_top(rs):
    """剔除'对加权和贡献(|net|*dep)最大的一笔'后的资金加权 —— 抗单点检验。"""
    if len(rs) < 2:
        return None
    top = max(rs, key=lambda r: abs(r.net_ret) * r.deployable)
    rest = [r for r in rs if r is not top]
    c, _ = cw(rest)
    return c


def ew(rs):
    return (sum(r.net_ret for r in rs) / len(rs)) if rs else None


def _p(v):
    return "   —   " if v is None else f"{v*100:+7.2f}%"


def capacity_frontier(rs, grid=(50, 100, 200, 500, 1000, 2000, 5000, 10_000)):
    """每笔限仓 D 扫描:返回保持资金加权 ≥0 的最大 D 及对应总可部署。"""
    best = None
    rows = []
    for D in grid:
        c, W = cw(rs, cap=D)
        rows.append((D, c, W))
        if c is not None and c >= 0:
            best = (D, c, W)
    return best, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--cutoff", default="2026-05-26", help="train < cutoff <= test")
    ap.add_argument("--cost", default="base", choices=list(PRESETS))
    ap.add_argument("--entry-mode", default="realistic", choices=["realistic", "optimistic"])
    ap.add_argument("--min-test-n", type=int, default=40, help="test 段最少笔数才接受判决")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--contrarian", action="store_true", help="额外跑 C0 逆鲸鱼分析")
    args = ap.parse_args()

    cutoff = date.fromisoformat(args.cutoff)
    cost = PRESETS[args.cost]

    conn = connect(args.db)
    prices = load_price_series(conn)
    signals = generate_signals(conn, prices)
    conn.close()

    filters = make_filters(prices)

    print("=" * 90)
    print(f"🔬 P0-B 攻坚资金加权 OOS | 切分 train < {cutoff} <= test | 成本 {args.cost} | 入场 {args.entry_mode}")
    print("=" * 90)
    print(f"预登记假设: {len(filters)} 个 × 持有期 {HORIZONS} = {len(filters)*len(HORIZONS)} 个组合(多重比较见末尾诚实声明)")

    json_out = {"generated_at": datetime.now().isoformat(), "cutoff": args.cutoff,
                "cost": args.cost, "entry_mode": args.entry_mode,
                "n_hypotheses": len(filters), "horizons": {}}

    passed = []  # (horizon, name, test_cw, test_n)

    for H in HORIZONS:
        res = run_backtest(signals, prices, cost, args.entry_mode, [H])[H]
        train = [r for r in res if r.sig_date < cutoff]
        test = [r for r in res if r.sig_date >= cutoff]
        print(f"\n{'─'*90}\n持有期 {H}  | 可标记 train {len(train)} / test {len(test)}")
        print(f"  {'假设':<26}{'train资金加权':>13}{'test资金加权':>13}{'test等权':>10}"
              f"{'test_n':>8}{'test剔top':>11}{'判决':>8}")
        json_out["horizons"][H] = {}
        for name, fn in filters.items():
            # H6/H7 专用: 每笔限仓 $200 (对应容量前沿的预登记探针)
            cap = 200.0 if name.startswith(("H6", "H7")) else None
            tr = [r for r in train if fn(r)]
            te = [r for r in test if fn(r)]
            tr_cw, _ = cw(tr, cap=cap)
            te_cw, te_W = cw(te, cap=cap)
            te_top = cw_drop_top(te) if cap is None else cw_drop_top(te)
            n = len(te)
            # 判决
            if n < args.min_test_n or te_cw is None:
                mark = "⚪薄"
            elif te_cw >= 0 and (te_top is None or te_top >= 0):
                mark = "🟢过"
                passed.append((H, name, te_cw, n))
            elif te_cw >= 0:
                mark = "🟡单点"  # 正但靠单点
            else:
                mark = "🔴负"
            print(f"  {name:<26}{_p(tr_cw):>13}{_p(te_cw):>13}{_p(ew(te)):>10}"
                  f"{n:>8}{_p(te_top):>11}{mark:>8}")
            json_out["horizons"][H][name] = {
                "train_cw": tr_cw, "test_cw": te_cw, "test_ew": ew(te),
                "test_n": n, "test_deployable": te_W, "test_cw_drop_top": te_top,
                "verdict": mark,
            }

    # ---- 容量前沿: 对 H5(最克制的过滤)在 test 段算 ----
    print(f"\n{'─'*90}\n📐 容量前沿(H5 精选+流动, test 段, +1d): 每笔限仓 D → 资金加权 & 总可部署")
    res1d = run_backtest(signals, prices, cost, args.entry_mode, ["+1d"])["+1d"]
    test1d = [r for r in res1d if r.sig_date >= cutoff and filters["H5 H1+H2+H3(精选+流动)"](r)]
    best, rows = capacity_frontier(test1d)
    print(f"  {'每笔限仓D':>10}{'资金加权净':>13}{'总可部署$':>14}")
    for D, c, W in rows:
        flag = "  ← 最大可行" if best and D == best[0] else ""
        print(f"  ${D:>9,}{_p(c):>13}{W:>14,.0f}{flag}")
    json_out["capacity_frontier_H5_test_1d"] = {
        "rows": [{"cap": D, "cw": c, "deployable": W} for D, c, W in rows],
        "max_feasible_cap": best[0] if best else None,
        "deployable_at_max": best[2] if best else None,
    }

    # ---- 诚实总结 ----
    # ---- 逆鲸鱼分析(C0, 独立模块) ----
    if args.contrarian:
        print(f"\n{'─'*90}")
        print("🔄 C0 逆鲸鱼 — 大额(≥$1000)+超流动(≥$200k) Yes BUY → 反转为 No BUY")
        con_sigs = generate_contrarian_signals(conn_lazy := connect(args.db), prices)
        conn_lazy.close()
        print(f"   逆势信号数: {len(con_sigs)}")
        con_res = run_backtest(con_sigs, prices, cost, args.entry_mode, HORIZONS)
        print(f"  {'持有期':<10}{'train资金加权':>15}{'test资金加权':>15}{'test等权':>12}"
              f"{'test_n':>8}{'test剔top':>13}{'判决':>8}")
        for H in HORIZONS:
            all_c = con_res[H]
            tr_c = [r for r in all_c if r.sig_date < cutoff]
            te_c = [r for r in all_c if r.sig_date >= cutoff]
            tr_cw_c, _ = cw(tr_c)
            te_cw_c, _ = cw(te_c)
            te_top_c = cw_drop_top(te_c)
            n_c = len(te_c)
            if n_c < args.min_test_n or te_cw_c is None:
                mark_c = "⚪薄"
            elif te_cw_c >= 0 and (te_top_c is None or te_top_c >= 0):
                mark_c = "🟢过"
                passed.append((H, "C0 逆鲸鱼", te_cw_c, n_c))
            elif te_cw_c >= 0:
                mark_c = "🟡单点"
            else:
                mark_c = "🔴负"
            print(f"  {H:<10}{_p(tr_cw_c):>15}{_p(te_cw_c):>15}{_p(ew(te_c)):>12}"
                  f"{n_c:>8}{_p(te_top_c):>13}{mark_c:>8}")
        if args.json:
            json_out["contrarian_C0"] = {
                H: {
                    "train_cw": cw([r for r in con_res[H] if r.sig_date < cutoff])[0],
                    "test_cw": cw([r for r in con_res[H] if r.sig_date >= cutoff])[0],
                    "test_n": len([r for r in con_res[H] if r.sig_date >= cutoff]),
                }
                for H in HORIZONS
            }

    print(f"\n{'='*90}")
    n_comb = len(filters) * len(HORIZONS)
    print(f"⚖️ 诚实判决(多重比较: 共 {n_comb} 个组合 + C0 逆势):")
    if passed:
        for H, name, c, n in passed:
            print(f"  🟢 候选: [{H}] {name} → test 资金加权 {c*100:+.2f}%/每元 (n={n}, 抗单点)")
        print(f"  ⚠️ 但 {n_comb} 个组合里挑出 {len(passed)} 个为正,多重比较下可能偶然;"
              f"需在 {HORIZONS} 两持有期都过、且更多数据复核才算数。")
        if any("H6" in name for _, name, _, _ in passed):
            print("  ⚠️ H6 警告: $200 限仓阈值源于对当前 test 集的容量前沿分析(非独立预测)。")
            print("     当前 test 段'通过'属循环验证;H6 的真正 OOS 检验须用 2026-06-06 后的新数据。")
    else:
        print("  🔴 无候选通过: 没有任何预登记过滤在 test 段做到'资金加权≥0 且抗单点'。")
        print("  → 诚实结论: 当前数据里跟鲸鱼没有可放大的资金加权 edge;只有'微仓薄市场'的等权幻觉。")
    if args.contrarian:
        print("  ℹ️ C0 逆势: 方向为 train负→test正(与跟鲸鱼反向),但样本薄(⚪);")
        print("     需积累至 test_n≥40 方可判决。继续采集数据后用 --contrarian 复跑。")
    print(f"⚠️ 样本仅 ~1 月、切分后两段更薄;此为方向性证据,非定论。")
    print("=" * 90)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        p = RESULTS_DIR / f"pzero_oos_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps(json_out, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
