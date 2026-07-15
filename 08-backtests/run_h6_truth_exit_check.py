#!/usr/bin/env python3
"""测试 C1 / C2:H6/H7 的两个"让数字变好看"的隐含假设,拿掉后还站得住吗?

兑现 docs/PREREG_H6_TRUTH_EXIT_2026-07-15.md。被检验对象 = 本项目目前唯一还站着的候选。

C1(resolution 口径):了结价用"我们最后一次拍到的快照价" → 换成**官方真实结算**(1/0)。
   动机:实测 80% 的已结算市场终值未收敛,对它们原口径等于"假设能在中间价免费平仓",
   而真持有到期是没有卖出动作的(直接赔付)——这个免费出场是凭空多出来的。

C2(+1d 口径):平仓不付卖出滑点 → 换成**付探针实测的真实卖出滑点**。
   动机:entry_price 已扣入场成本,mark_price 却是裸中间价。+1d 是真要卖的。

纪律:每个测试**有且仅有一个自变量**;两臂同市场范围;红线/假设集/限仓全部 import 自
run_pzero_oos,构造上不可能漂移;8 个假设全列不择优。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_h6_truth_exit_check.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS  # noqa: E402
from engine.data import connect, load_price_series  # noqa: E402
from engine.portfolio import run_backtest  # noqa: E402
from run_calibration_truth_check import verify_index_alignment  # noqa: E402
from run_h7b_price_sensitive import calibrate_curve, price_sensitive_cost  # noqa: E402
from run_pzero_oos import cw, cw_drop_top, make_filters, _p  # noqa: E402
from strategies.follow_whale import generate_signals  # noqa: E402

TRUTH_DB = Path(__file__).resolve().parent / "data/price_history_api.db"
PER_TRADE_CAP = 200.0     # H6/H7 的微仓限额,与 run_pzero_oos 一致
MIN_TEST_N = 40           # 红线:沿用 run_pzero_oos 默认
CUTOFFS = ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]


def load_truth(path: Path) -> dict[str, float]:
    if not path.exists():
        sys.exit(f"❌ 找不到权威真值库 {path}\n   先跑 backfill_price_history_api.py")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT slug, resolved_yes FROM markets WHERE resolved_yes IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {s: float(v) for s, v in rows}


def evaluate(trades, filters, cutoff: date) -> dict[str, tuple]:
    """对每个预登记假设,算 test 段(资金加权净, 抗单点, n)。限仓 $200 与 H6/H7 口径一致。"""
    out = {}
    for name, f in filters.items():
        sel = [r for r in trades if f(r)]
        te = [r for r in sel if r.entry_date >= cutoff]
        c, _ = cw(te, cap=PER_TRADE_CAP)
        top = cw_drop_top(te) if len(te) >= 2 else None
        out[name] = (c, top, len(te))
    return out


def verdict(res: dict[str, tuple]) -> list[str]:
    """红线(逐字沿用 run_pzero_oos):n>=40 且 test 资金加权>=0 且 抗单点>=0。"""
    return [
        n for n, (c, top, k) in res.items()
        if k >= MIN_TEST_N and c is not None and c >= 0 and (top is None or top >= 0)
    ]


def report(title: str, name_a: str, name_b: str, res_a, res_b, filters) -> tuple[set, set]:
    print(f"\n{'─'*104}\n{title}")
    print(f"  {'假设':<24}{name_a+' cw/剔顶':>24}{name_b+' cw/剔顶':>24}{'n':>7}{'判决':>10}")
    pass_a, pass_b = set(), set()
    for nm in filters:
        ca, ta, ka = res_a[nm]
        cb, tb, kb = res_b[nm]
        oka = ka >= MIN_TEST_N and ca is not None and ca >= 0 and (ta is None or ta >= 0)
        okb = kb >= MIN_TEST_N and cb is not None and cb >= 0 and (tb is None or tb >= 0)
        if oka:
            pass_a.add(nm)
        if okb:
            pass_b.add(nm)
        thin = "⚪薄" if kb < MIN_TEST_N else ""
        mark = thin or ("🟢过" if okb else "🔴负")
        print(f"  {nm:<24}{_p(ca)+'/'+_p(ta):>24}{_p(cb)+'/'+_p(tb):>24}{kb:>7}{mark:>10}")
    return pass_a, pass_b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    truth = load_truth(TRUTH_DB)
    conn = connect(args.db)
    prices_all = load_price_series(conn)

    # C1 需要权威真值 → 只能在"有官方真值"的市场上比(arm B 标不了别的)。
    # ★C2 **不需要真值**(只关心出场滑点),故必须跑**全库**——H6 的原生地盘。
    #   若也限制到 truth 市场,等于把 H6 圈死在"短周期候选池"里(该池由校准入场规则
    #   dte1-14 定义),那不是 H6 的宇宙,据此判它死刑不公平。
    prices_c1 = {m: ps for m, ps in prices_all.items() if m in truth}
    sig_c1 = generate_signals(conn, prices_c1)
    sig_c2 = generate_signals(conn, prices_all)
    conn.close()

    print("=" * 104)
    print("🔬 测试 C1/C2:H6/H7 的两个隐含假设 | 兑现 PREREG_H6_TRUTH_EXIT_2026-07-15")
    print("=" * 104)
    print(f"  C1 范围(需真值): {len(prices_c1)} 市场 / {len(sig_c1)} 信号")
    print(f"  C2 范围(全库,H6 原生宇宙): {len(prices_all)} 市场 / {len(sig_c2)} 信号")
    verify_index_alignment(prices_c1, TRUTH_DB)

    filters = make_filters(prices_all)
    base = PRESETS["base"]
    med_curve, meta = calibrate_curve("median")
    ps_cost = price_sensitive_cost(med_curve)
    print(f"  C2 用探针价格敏感中位曲线({meta['probe_days']}天/{meta['n_rows']}行)算卖出滑点")

    # ---------- C1: resolution 了结价 = 最后快照 vs 官方结算(限真值市场) ----------
    # 两臂同用 base 成本 → 唯一变量 = 了结价来源。
    a1 = run_backtest(sig_c1, prices_c1, base, "realistic", ["resolution"])["resolution"]
    b1 = run_backtest(sig_c1, prices_c1, base, "realistic", ["resolution"], truth=truth)["resolution"]

    # ---------- C2: +1d 平仓 免费 vs 付真实卖出滑点(全库) ----------
    # ★两臂必须同用 ps_cost:若 armA 用 base、armB 用 ps_cost,则**入场成本也变了**,
    #   = 同时动了两个变量,差分就分不出是出场滑点造成的还是换成本模型造成的。
    #   唯一变量必须只是 exit_cost 开关。
    a2 = run_backtest(sig_c2, prices_all, ps_cost, "realistic", ["+1d"])["+1d"]
    b2 = run_backtest(sig_c2, prices_all, ps_cost, "realistic", ["+1d"], exit_cost=True)["+1d"]

    print(f"\n  笔数 — C1: {len(a1)} vs {len(b1)}   C2: {len(a2)} vs {len(b2)}")

    for cutoff_s in CUTOFFS:
        cutoff = date.fromisoformat(cutoff_s)
        print(f"\n{'='*104}\n### cutoff {cutoff_s} (train < cutoff <= test)")

        pa, pb = report(
            "C1 · resolution:了结价来源(唯一变量)",
            "最后快照", "官方结算", evaluate(a1, filters, cutoff), evaluate(b1, filters, cutoff), filters,
        )
        _judge("C1", pa, pb)

        pa2, pb2 = report(
            "C2 · +1d:平仓是否付卖出滑点(唯一变量)",
            "免费平仓", "付真实滑点", evaluate(a2, filters, cutoff), evaluate(b2, filters, cutoff), filters,
        )
        _judge("C2", pa2, pb2)

    print(f"\n{'='*104}")
    print("⚠️ 两臂仅限'有官方真值'的市场,绝对值不可与速览表历史数字比大小,只可两臂间比。")
    print("   本测试只隔离口径,未加深历史、未动样本/容量等既有软肋。即便 🟢 也不构成碰真钱的理由。")
    print("=" * 104)
    return 0


def _judge(tag: str, pa: set, pb: set) -> None:
    """判决语义与预登记单 §5 一致:'存活'必须是同一假设两臂都过。"""
    survived = sorted(pa & pb)
    if not pa and not pb:
        print(f"  ⚪ {tag} 判决:两臂都无假设通过 → 与该口径无关,本就不成立。")
    elif pa and not pb:
        print(f"  🔴 {tag} 判决:修正后全军覆没 → 原结论是这个隐含假设的产物。")
    elif survived:
        print(f"  🟢 {tag} 判决:{survived} 两臂都过 → 该假设不是支柱。")
        lost = sorted(pa - set(survived))
        if lost:
            print(f"     ⚠️ 但 {lost} 只在原口径下过 → 那部分是假设的产物。")
    else:
        print(f"  ⚠️ {tag} 判决:无同一假设两臂都过(原过 {sorted(pa)} / 修正后过 {sorted(pb)})→ 不算存活。")


if __name__ == "__main__":
    sys.exit(main())
