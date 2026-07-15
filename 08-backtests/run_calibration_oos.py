#!/usr/bin/env python3
"""
市场自身校准偏差 / 长热偏差(favorite-longshot)OOS 检验
—— 兑现 docs/PREREG_CALIBRATION_2026-07-14.md,回答 edge=p−m 里"能否找到不依赖私有信息的 p̂"。

思路:p̂ = 市场自身的校准误差。若"标价 m 的一批市场,实际结算 YES 率 ≠ m"且方向稳定(理论:
冷门被高估、热门被低估),则"买热门那一边"就有毛 edge。本脚本按预登记规则,用**价格敏感成本**
(探针标定,见 run_h7b_price_sensitive.py)判它扣成本后净死活。

纪律:方向由理论预登记(非数据手挑);band 网格 {0.6,0.7,0.8,0.9} 全列不择优;
train/test 按入场日切;资金加权 + 抗单点;并列零成本毛利作对照。红线见预登记单 §6。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_calibration_oos.py
  PYTHONPATH=08-backtests python3 08-backtests/run_calibration_oos.py --json
"""
from __future__ import annotations

import argparse
import json
from collections import namedtuple
from datetime import date, datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS
from engine.data import connect, load_price_series
from run_h7b_price_sensitive import calibrate_curve, price_sensitive_cost, CAP

RESULTS_DIR = Path(__file__).resolve().parent / "results"

BANDS = [0.60, 0.70, 0.80, 0.90]           # 预登记网格,不择优
CUTOFFS = ["2026-06-01", "2026-06-15", "2026-07-01"]
ENTRY_MAX_DTE = 14
VOL_FLOOR = 1000.0
MIN_TEST_N = 40

Trade = namedtuple("Trade", "entry_date m side won entry_eff gross_ret net_ret deployable")


def market_entries(series, cost):
    """每个已结算市场取一次入场(首个 dte1-14 且有量且价格可交易)。返回 [(date,m,cap,won_yes)]。"""
    out = []
    for ps in series.values():
        if not ps.is_resolved() or ps.end_date is None:
            continue
        term = ps.terminal()
        won_yes = term[1] > 0.95  # 终值收敛到 1 → YES 赢
        for d, px, cap in zip(ps.dates, ps.yes_prices, ps.cap_usd):
            dte = (ps.end_date - d).days
            if 1 <= dte <= ENTRY_MAX_DTE and cap >= VOL_FLOOR and 0.02 < px < 0.98:
                out.append((d, px, cap, won_yes))
                break
    return out


def make_trades(entries, band, cost):
    """对给定 band,把每个入场翻成一笔'买热门side'交易(不在带内则跳过)。"""
    trades = []
    for d, m, cap, won_yes in entries:
        if m >= band:
            side, raw, won = "YES", m, won_yes
        elif m <= 1 - band:
            side, raw, won = "NO", 1 - m, (not won_yes)
        else:
            continue
        deployable = cost.deployable(CAP, cap)
        if deployable <= 0:
            continue
        entry_eff = cost.entry_fill(raw, "BUY")  # 价格敏感滑点按 d=min(raw,1-raw)
        if entry_eff <= 0:
            continue
        payoff = 1.0 if won else 0.0
        gross = (payoff - raw) / raw
        net = (payoff - entry_eff) / entry_eff - cost.gas_drag(deployable)
        trades.append(Trade(d, m, side, won, entry_eff, gross, net, deployable))
    return trades


def cw(trades, field="net_ret"):
    W = sum(t.deployable for t in trades)
    if W <= 0:
        return None, 0.0
    return sum(getattr(t, field) * t.deployable for t in trades) / W, W


def cw_drop_top(trades, field="net_ret"):
    if len(trades) < 2:
        return None
    top = max(trades, key=lambda t: abs(getattr(t, field)) * t.deployable)
    rest = [t for t in trades if t is not top]
    return cw(rest, field)[0]


def winrate(trades):
    return sum(1 for t in trades if t.won) / len(trades) if trades else 0.0


def _p(v):
    return "   —   " if v is None else f"{v*100:+7.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = connect(args.db)
    series = load_price_series(conn)
    conn.close()

    med_curve, meta = calibrate_curve("median")
    cost = price_sensitive_cost(med_curve)
    zero = PRESETS["zero"]  # 零成本对照(仍走容量)

    entries = market_entries(series, cost)
    print("=" * 96)
    print("🔬 市场校准/长热偏差 OOS 检验 | 兑现 PREREG_CALIBRATION_2026-07-14")
    print("=" * 96)
    print(f"已结算市场入场点: {len(entries)} | 价格历史 {min(d for d,_,_,_ in entries)}~{max(d for d,_,_,_ in entries)}")
    print(f"成本: 探针价格敏感中位曲线({meta['probe_days']}天/{meta['n_rows']}行) + base gas; 每笔 ${CAP:.0f}")

    # ---- 校准全景:各 band 全样本毛利/净利 + 胜率 ----
    print(f"\n{'─'*96}\n① 全样本校准全景(看毛利偏差是否真存在 & 成本吃掉多少):")
    print(f"  {'band':>6}{'笔数':>7}{'胜率':>8}{'毛资金加权':>12}{'净资金加权':>12}"
          f"{'净抗单点':>11}{'均价m':>8}")
    for b in BANDS:
        tr = make_trades(entries, b, cost)
        g, _ = cw(tr, "gross_ret")
        n, _ = cw(tr, "net_ret")
        nt = cw_drop_top(tr, "net_ret")
        avm = sum(t.m for t in tr) / len(tr) if tr else 0
        print(f"  {b:>6.2f}{len(tr):>7}{winrate(tr)*100:>7.0f}%{_p(g):>12}{_p(n):>12}"
              f"{_p(nt):>11}{avm:>8.2f}")

    # ---- OOS:各 band × cutoff 的 test 段净资金加权 + 抗单点 ----
    json_bands = {}
    print(f"\n{'─'*96}\n② OOS(test 段净资金加权 / 抗单点,价格敏感成本):")
    print(f"  {'band':>6}", end="")
    for c in CUTOFFS:
        print(f"{c[5:]+' cw/剔顶':>20}", end="")
    print(f"{'判决':>8}")
    verdict_pass = False
    for b in BANDS:
        tr = make_trades(entries, b, cost)
        row_pass_cnt = usable = 0
        cells = []
        cut_json = {}
        for c in CUTOFFS:
            cc = date.fromisoformat(c)
            te = [t for t in tr if t.entry_date >= cc]
            n = len(te)
            te_cw, _ = cw(te, "net_ret")
            te_top = cw_drop_top(te, "net_ret")
            cut_json[c] = {"n": n, "test_cw": te_cw, "test_drop_top": te_top}
            if n < MIN_TEST_N or te_cw is None:
                cells.append(f"{'⚪薄 n='+str(n):>20}")
                continue
            usable += 1
            ok = te_cw >= 0 and (te_top is None or te_top >= 0)
            if ok:
                row_pass_cnt += 1
            cells.append(f"{_p(te_cw)+'/'+_p(te_top):>20}")
        # band 级判决:≥2/3 可判 cutoff 稳健为正
        band_ok = usable > 0 and row_pass_cnt >= -(-2 * usable // 3)
        if band_ok:
            verdict_pass = True
        mark = "🟢过" if band_ok else ("🔴负" if usable else "⚪薄")
        print(f"  {b:>6.2f}" + "".join(cells) + f"{mark:>8}")
        json_bands[b] = {"cutoffs": cut_json, "pass": band_ok,
                         "pass_cnt": row_pass_cnt, "usable": usable}

    # ---- 诚实宣判 ----
    print(f"\n{'='*96}")
    print(f"⚖️ 判决(多重比较: {len(BANDS)} band × {len(CUTOFFS)} cutoff = {len(BANDS)*len(CUTOFFS)} 次):")
    if verdict_pass:
        winners = [b for b in BANDS if json_bands[b]["pass"]]
        print(f"  🟢 有 band 通过红线: {winners} — 存在净可交易的市场校准 edge(需更多数据复核)。")
    else:
        print("  🔴 无 band 通过红线: 价格敏感成本下,没有任何预登记 band 做到 test 资金加权≥0 且抗单点稳健。")
        print("  → 诚实结论: 长热偏差的**毛利**可能真实,但落在极端价的滑点黑洞里,**真实成本下不可净交易**。")
        print("     这仍是省钱结论——挡住一次注定被滑点吃光的实盘。")
    print(f"  ⚠️ 价格历史仅 2 个月(05-09~07-14),cutoff 嵌套非独立;中间价带样本薄。方向性证据,非定论。")
    print("=" * 96)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        p = RESULTS_DIR / f"calibration_oos_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps({
            "generated_at": datetime.now().isoformat(),
            "n_entries": len(entries), "bands": {str(k): v for k, v in json_bands.items()},
            "cutoffs": CUTOFFS, "probe_days": meta["probe_days"],
            "verdict_pass": verdict_pass,
        }, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
