#!/usr/bin/env python3
"""
H7b —— 价格敏感成本下的 H6 vs H7(预登记后续测试,兑现 PREREG_H7_PRICE_FLOOR_2026-07-06.md §7)

背景:H7(H6+价格下限≥0.20)在 **flat 50bps** 滑点下已被 OOS 证伪(红线②,H7 每 cutoff ≤ H6)。
但那把尺子有结构盲点——价格下限的**全部价值在于省滑点**,flat 成本量不出来。本脚本用**滑点探针**
实测标定的"滑点=f(入场价)"替换 flat 滑点,重跑 H6 vs H7,看下限是否带来 OOS 净改善。

**预登记红线(H7b,写在跑之前,见 PREREG §7):**
  H7 记为"价格下限被 OOS 证实为净改善"当且仅当:
  H7 的 resolution 抗单点(剔顶资金加权)在 **≥5/6 cutoff 净高于 H6**
  (此时 H6 的贴边盘被真实高滑点惩罚)。任一不满足 → 价格下限不采纳。

**标定纪律(避免调参自欺):**
  - 曲线 = 探针按离边距离 d=min(px,1-px) 分 0.05 桶的**中位滑点**,原样使用,不平滑/不单调化/不择优。
  - 用**中位数**(非均值):中位对 H6 的尾部贴边盘惩罚更轻 → 保守**偏向不利于 H7**;
    若 H7 在中位曲线下仍胜,结论更稳。均值曲线并列作敏感性(尾部更狠,偏向 H7)。
  - 探针为 $200 单,与 H6/H7 微仓 cap $200 完全匹配;本测试只比 H6/H7,不碰无 cap 的 H0-H5。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_h7b_price_sensitive.py
  PYTHONPATH=08-backtests python3 08-backtests/run_h7b_price_sensitive.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from statistics import median

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS, CostModel
from engine.data import connect, load_price_series
from engine.portfolio import run_backtest
from strategies.follow_whale import generate_signals

# run_pzero_oos 里已有的预登记过滤集与资金加权工具,直接复用避免口径漂移
from run_pzero_oos import make_filters, cw, cw_drop_top

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE_DB = PROJECT_ROOT / "07-data" / "slippage_probe.db"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 预登记 cutoff 集(PREREG §3)+ 之后到齐的 06-19/06-22
CUTOFFS = ["2026-05-18", "2026-05-25", "2026-06-01", "2026-06-05",
           "2026-06-08", "2026-06-15", "2026-06-19", "2026-06-22"]
BIN_WIDTH = 0.05
N_BINS = int(0.5 / BIN_WIDTH) + 1  # d∈[0,0.5],最后一桶收 d≥0.45
CAP = 200.0  # 微仓每笔封顶,与探针单规模一致
MIN_TEST_N = 40  # 红线③:每 cutoff resolution test n 门槛


def calibrate_curve(stat="median"):
    """从探针 DB 标定 滑点=f(d) 的 0.05 桶曲线。返回 (curve_tuple, meta)。"""
    if not PROBE_DB.exists():
        raise SystemExit(f"探针 DB 不存在: {PROBE_DB}")
    con = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT gamma_price, slippage_bps FROM slippage_probe "
        "WHERE gamma_price IS NOT NULL AND slippage_bps IS NOT NULL"
    ).fetchall()
    span = con.execute(
        "SELECT MIN(substr(probe_ts,1,10)), MAX(substr(probe_ts,1,10)), "
        "COUNT(DISTINCT substr(probe_ts,1,10)) FROM slippage_probe"
    ).fetchone()
    con.close()

    buckets: list[list[float]] = [[] for _ in range(N_BINS)]
    for px, slip in rows:
        d = min(px, 1.0 - px)
        i = min(int(d / BIN_WIDTH), N_BINS - 1)
        buckets[i].append(slip)

    agg = median if stat == "median" else (lambda v: sum(v) / len(v))
    curve = []
    counts = []
    for b in buckets:
        counts.append(len(b))
        curve.append(agg(b) if b else 0.0)
    # 空桶(极少)用相邻非空桶回填,避免 0 滑点假象
    for i in range(N_BINS):
        if counts[i] == 0:
            nb = next((curve[j] for j in range(i, N_BINS) if counts[j]), None)
            pb = next((curve[j] for j in range(i, -1, -1) if counts[j]), None)
            curve[i] = nb if nb is not None else (pb or 0.0)
    meta = {"stat": stat, "n_rows": len(rows), "probe_days": span[2],
            "probe_span": [span[0], span[1]], "bin_counts": counts,
            "bin_width": BIN_WIDTH}
    return tuple(curve), meta


def price_sensitive_cost(curve, base="base"):
    """在 base 预设(gas/容量不变)上,把 flat 滑点换成探针曲线。"""
    b = PRESETS[base]
    return CostModel(fee_bps=b.fee_bps, gas_usd=b.gas_usd,
                     capacity_frac=b.capacity_frac, impact_coef=b.impact_coef,
                     slip_curve=curve, slip_curve_width=BIN_WIDTH)


def _drop_top_cap(rs, cap):
    """cap 版抗单点:剔掉对(|net|*capped_dep)贡献最大的一笔后的资金加权。"""
    if len(rs) < 2:
        return None
    top = max(rs, key=lambda r: abs(r.net_ret) * min(r.deployable, cap))
    rest = [r for r in rs if r is not top]
    c, _ = cw(rest, cap=cap)
    return c


def run_arm(signals, prices, cost, cutoffs):
    """对给定成本,跑 resolution 口径,按 cutoff 返回 H6/H7 的抗单点资金加权。"""
    filters = make_filters(prices)
    res = run_backtest(signals, prices, cost, "realistic", ["resolution"])["resolution"]
    out = {}
    for cut in cutoffs:
        c = date.fromisoformat(cut)
        row = {}
        for name in ("H6 H5+微仓$200", "H7 H6+价格下限≥0.20"):
            fn = filters[name]
            te = [r for r in res if r.sig_date >= c and fn(r)]
            cw_v, _ = cw(te, cap=CAP)
            row[name] = {"n": len(te), "cw": cw_v, "drop_top": _drop_top_cap(te, CAP)}
        out[cut] = row
    return out


def _pct(v):
    return "   —   " if v is None else f"{v*100:+7.2f}%"


def _verdict(arm, label):
    """按 H7b 红线判 H7 抗单点是否净高于 H6。
    ⚠️ 关键修正:跟鲸鱼信号仅 06-13 起(样本一个月),cutoff≤06-13 全捕获同一 test 集,
    这些'早 cutoff'退化为重复点。故按 H6_n 去重,只在**互不相同的 test 窗口**上计票,
    避免把'一个月切 4 个嵌套窗口'虚报成'8 个独立 cutoff'。"""
    h6k, h7k = "H6 H5+微仓$200", "H7 H6+价格下限≥0.20"
    print(f"\n{'─'*78}\n▶ 成本口径: {label}")
    print(f"  {'cutoff':<12}{'H6抗单点':>11}{'H7抗单点':>11}{'H7−H6':>10}"
          f"{'H6_n':>7}{'H7_n':>7}{'比较':>10}")
    wins = usable = 0
    seen_n = set()
    for cut in CUTOFFS:
        h6, h7 = arm[cut][h6k], arm[cut][h7k]
        d6, d7 = h6["drop_top"], h7["drop_top"]
        dup = h6["n"] in seen_n
        seen_n.add(h6["n"])
        thin = h7["n"] < MIN_TEST_N or h6["n"] < MIN_TEST_N
        if d6 is None or d7 is None or thin:
            cmp = "⚪薄"
        elif dup:
            cmp = "≈重复窗"  # 与更早 cutoff 同一 test 集,不重复计票
        else:
            usable += 1
            if d7 > d6:
                cmp = "H7胜"
                wins += 1
            else:
                cmp = "H6胜"
        diff = (d7 - d6) if (d6 is not None and d7 is not None) else None
        print(f"  {cut:<12}{_pct(d6):>11}{_pct(d7):>11}{_pct(diff):>10}"
              f"{h6['n']:>7}{h7['n']:>7}{cmp:>10}")
    need = -(-5 * usable // 6) if usable else 0  # ceil(5/6*usable)
    passed = usable > 0 and wins >= need
    print(f"  → 去重后 {usable} 个**互异** test 窗口;H7 净高于 H6: {wins}/{usable}"
          f"(红线需 ≥{need});判定: {'🟢 达标' if passed else '🔴 未达标'}")
    print(f"     ⚠️ 这 {usable} 个窗口是同一个月样本的嵌套切片,并非独立月份——一致性可参考,独立性不足")
    return {"wins": wins, "usable_distinct": usable, "need": need, "passed": passed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = connect(args.db)
    prices = load_price_series(conn)
    signals = generate_signals(conn, prices)
    conn.close()

    med_curve, med_meta = calibrate_curve("median")
    mean_curve, mean_meta = calibrate_curve("mean")

    print("=" * 78)
    print("🔬 H7b — 价格敏感成本下 H6 vs H7(兑现 PREREG_H7_PRICE_FLOOR §7)")
    print("=" * 78)
    print(f"探针标定: {med_meta['n_rows']} 行 / {med_meta['probe_days']} 天 "
          f"({med_meta['probe_span'][0]}~{med_meta['probe_span'][1]})")
    print(f"  ⚠️ 预登记要求探针 ≥2 周;当前 {med_meta['probe_days']} 天"
          f"{'(达标)' if med_meta['probe_days'] >= 14 else '(未满 14 天,以下为初判,官方判决须待窗口满)'}")
    edges = "  离边d桶: " + " ".join(
        f"[{i*BIN_WIDTH:.2f}]{med_curve[i]:.0f}b(n{med_meta['bin_counts'][i]})"
        for i in range(N_BINS))
    print(edges)

    arms = {}
    # 对照:flat base(重现证伪结论)+ 中位曲线(主)+ 均值曲线(敏感性)
    arms["flat base(50bps,对照)"] = run_arm(signals, prices, PRESETS["base"], CUTOFFS)
    arms["探针中位曲线(主判据)"] = run_arm(signals, prices, price_sensitive_cost(med_curve), CUTOFFS)
    arms["探针均值曲线(敏感性,尾部更狠)"] = run_arm(signals, prices, price_sensitive_cost(mean_curve), CUTOFFS)

    verdicts = {label: _verdict(arm, label) for label, arm in arms.items()}

    print(f"\n{'='*78}")
    main_v = verdicts["探针中位曲线(主判据)"]
    print("⚖️ H7b 判决(主判据=探针中位曲线):")
    print(f"   H7 净高于 H6 于 {main_v['wins']}/{main_v['usable_distinct']} 个互异 test 窗口 → "
          f"{'🟢 价格下限被价格敏感成本证实为净改善' if main_v['passed'] else '🔴 未达红线,价格下限仍不采纳'}")
    if med_meta['probe_days'] < 14:
        print(f"   ⚠️ 但探针仅 {med_meta['probe_days']} 天(<预登记14天),此为**初判**;"
              f"须待探针满窗后复跑方为官方判决,当前不据此碰真钱。")
    print("=" * 78)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        p = RESULTS_DIR / f"h7b_price_sensitive_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps({
            "generated_at": datetime.now().isoformat(),
            "median_curve": med_curve, "median_meta": med_meta,
            "mean_curve": mean_curve, "cutoffs": CUTOFFS,
            "arms": arms, "verdicts": verdicts,
            "probe_days": med_meta["probe_days"],
            "preliminary": med_meta["probe_days"] < 14,
        }, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
