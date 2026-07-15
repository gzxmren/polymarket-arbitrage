#!/usr/bin/env python3
"""
半凯利下注 —— 在市场校准/长热偏差 edge 上做仓位管理(应用户风控要求)。

动机:我们的 p̂(市场校准率)只有 2 个月、中间价带薄 → p̂ 有误差。凯利对高估 edge 极敏感,
故用**半凯利**(0.5·f*)。二元合约凯利: f* = (p̂ − c)/(1 − c) = edge/(1−价格);c 用**扣滑点的有效成本**,
则极端贴边盘 c_eff→1 使 f*≤0,凯利**自动把滑点黑洞交易的仓位压到 0**。

纪律:p̂ 用 **train 段** Platt 校准(logistic of outcome ~ logit(m))拟合,**test 段**应用——避免用同一批数据估 p̂ 又下注的循环自欺。半凯利仓位封顶 F_CAP,f*≤0 不下注。

用法:PYTHONPATH=08-backtests python3 08-backtests/run_calibration_kelly.py [--json]
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.data import connect, load_price_series
from run_h7b_price_sensitive import calibrate_curve, price_sensitive_cost, CAP
from run_calibration_oos import market_entries, CUTOFFS

RESULTS_DIR = Path(__file__).resolve().parent / "results"
F_CAP = 0.25          # 单笔半凯利仓位上限(占资金),防 p̂ 误差下过配
KELLY_FRAC = 0.5      # 半凯利
EPS = 1e-6


def _logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def fit_platt(ms, outs):
    """train 段拟合 p̂(m)=sigmoid(a+b·logit(m)),Newton-Raphson。返回 (a,b)。"""
    x = _logit(np.asarray(ms, float))
    y = np.asarray(outs, float)
    X = np.column_stack([np.ones_like(x), x])
    w = np.zeros(2)
    for _ in range(50):
        z = X @ w
        p = 1 / (1 + np.exp(-z))
        W = np.clip(p * (1 - p), EPS, None)
        grad = X.T @ (p - y)
        H = X.T @ (X * W[:, None]) + 1e-6 * np.eye(2)
        step = np.linalg.solve(H, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def phat(w, m):
    return float(1 / (1 + np.exp(-(w[0] + w[1] * _logit(np.array([m]))[0]))))


def kelly_f(p, c):
    """二元合约半凯利仓位(封顶);edge/(1-c);c 为扣滑点有效成本。"""
    if c >= 1 - EPS or c <= EPS:
        return 0.0
    f = (p - c) / (1 - c)
    return max(0.0, min(KELLY_FRAC * f, F_CAP))


def eval_cutoff(entries, cost, cutoff):
    """train 拟合 p̂ → test 逐笔选侧+半凯利下注。返回统计。"""
    cc = date.fromisoformat(cutoff)
    train = [(m, 1.0 if won else 0.0) for d, m, cap, won in entries if d < cc]
    test = [(d, m, cap, won) for d, m, cap, won in entries if d >= cc]
    w = fit_platt([m for m, _ in train], [o for _, o in train])

    bets = []  # (f, net_ret, is_extreme_skip)
    taken = skipped = 0
    for d, m, cap, won in test:
        p_yes = phat(w, m)
        # 两侧各算有效成本与凯利,取 f>0 的一侧
        c_yes = cost.entry_fill(m, "BUY")
        c_no = cost.entry_fill(1 - m, "BUY")
        f_yes = kelly_f(p_yes, c_yes)
        f_no = kelly_f(1 - p_yes, c_no)
        if f_yes >= f_no and f_yes > 0:
            side_c, f, won_side = c_yes, f_yes, won
        elif f_no > 0:
            side_c, f, won_side = c_no, f_no, (not won)
        else:
            skipped += 1
            continue
        taken += 1
        payoff = 1.0 if won_side else 0.0
        net = (payoff - side_c) / side_c  # gas 略(占比极小);半凯利已保守
        bets.append((f, net))
    return w, test, bets, taken, skipped


def stake_weighted(bets):
    W = sum(f for f, _ in bets)
    if W <= 0:
        return None
    return sum(f * n for f, n in bets) / W


def sequential_growth(bets):
    """按序把每笔半凯利收益复利,返回 (终值倍数, 最大回撤)。粗略(忽略同期并发)。"""
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for f, n in bets:
        eq *= (1 + f * n)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return eq, mdd


MIN_TEST_N = 40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = connect(args.db)
    series = load_price_series(conn)
    conn.close()

    curve, meta = calibrate_curve("median")
    cost = price_sensitive_cost(curve)
    entries = market_entries(series, cost)

    print("=" * 92)
    print("🎲 半凯利仓位管理 @ 市场校准 edge | train拟合p̂ → test半凯利(扣滑点成本)")
    print("=" * 92)
    print(f"入场点 {len(entries)} | 半凯利={KELLY_FRAC} 单笔封顶={F_CAP} | 成本=探针{meta['probe_days']}天价格敏感")
    print(f"\n{'cutoff':<12}{'a':>7}{'b':>6}{'test_n':>8}{'下注':>6}{'跳过':>6}"
          f"{'半凯利仓位均值':>14}{'仓位加权净':>12}{'复利终值':>10}{'最大回撤':>9}")

    out = {}
    for cut in CUTOFFS:
        w, test, bets, taken, skipped = eval_cutoff(entries, cost, cut)
        if not bets:
            print(f"{cut:<12}  test 太薄或无正 f,跳过")
            continue
        sw = stake_weighted(bets)
        term, mdd = sequential_growth(bets)
        avg_f = sum(f for f, _ in bets) / len(bets)
        print(f"{cut:<12}{w[0]:>7.2f}{w[1]:>6.2f}{len(test):>8}{taken:>6}{skipped:>6}"
              f"{avg_f*100:>13.1f}%{sw*100:>+11.2f}%{term:>10.3f}{mdd*100:>8.1f}%")
        out[cut] = {"a": w[0], "b": w[1], "test_n": len(test), "taken": taken,
                    "skipped": skipped, "avg_f": avg_f, "stake_weighted_net": sw,
                    "terminal": term, "max_drawdown": mdd}

    print(f"\n{'─'*92}")
    print("读法:")
    print(" · '跳过'=半凯利判 f≤0 不下注的笔数——多为滑点黑洞的极端贴边盘,凯利自动躲开(这正是想要的)。")
    print(" · '仓位加权净'与等额下注的 +0.46% 同口径可比;半凯利下应更高(自动只押甜区)。")
    print(" · '复利终值/最大回撤'粗略(忽略同期并发下注),仅供直觉:半凯利求'活得久',非最大化单期收益。")
    print(f" · b>0 且 a 使低 m 段 p̂<m = 拟合复现了长热偏差(冷门高估)。")
    print("=" * 92)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        p = RESULTS_DIR / f"calibration_kelly_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps({"generated_at": datetime.now().isoformat(),
                                 "kelly_frac": KELLY_FRAC, "f_cap": F_CAP,
                                 "cutoffs": out}, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
