#!/usr/bin/env python3
"""测试 C1:跟鲸鱼 + 持有到**真实结算**,在 H6 原生宇宙里还成不成立?

兑现 docs/PREREG_C1_WHALE_RESOLUTION_2026-07-21.md。

被检验对象:2026-07-15 意外发现——把 resolution 了结价从"最后一次快照"换成官方真实结算
(1.0/0.0)后,H6 由 +2.81% 跳到 +34.06%(剔顶 +15.66%),4/4 cutoff 全过。**但那跑在
错误的宇宙(校准候选池 9161 市场)。** 本单要求换到 **H6 原生宇宙**(全量跟鲸鱼 H0 信号触及的
distinct 市场并集,~2716)重跑同一口径对比。旧那张 +34% 只作动机,不作数。

性质:这是**推翻既往证伪的 🟢**(既往:跟鲸鱼净 −2.29%、资金加权 −4~−5%)。按项目铁律
**🟢 才是风险区,推翻旧证伪的 🟢 应受更严审视**。本单默认立场是证伪,不是求证。

唯一自变量 = 了结价口径:
  arm A = 最后一张快照价(terminal);arm B = 官方 outcomePrices 的 0/1(truth)。
两臂同宇宙、同信号、同入场、同 base 成本;唯一差别是 _mark 的了结价来源。

三道硬闸(不通过则中止/降级,见预登记 §3):
  §3.1 下标对齐(库价与 API 指同一侧,否则胜负标签反)——import 自 run_calibration_truth_check。
  §3.2 选择偏差(官方结算缺失是否与结果相关)——逐类报数 + arm-A 净收益分组对比。
  §3.3 交集闸(两臂 per-signal 一一配对,根治 run_h6 的 2563≠2534 守卫不对称)。

红线逐字 import 自 run_pzero_oos(n≥40 且 te_cw≥0 且 剔顶≥0),构造上不可漂移;
外层"每个可判 cutoff 都过"是本单自加的从严选择(见预登记 §4 诚实标注),非 import 来的。

前置:必须先跑 backfill_price_history_api.py --universe whales 回填 whales 宇宙官方真值。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_c1_resolution_check.py
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
from engine.portfolio import simulate_one  # noqa: E402
from run_calibration_truth_check import verify_index_alignment  # noqa: E402
from run_pzero_oos import cw, cw_drop_top, make_filters, _p  # noqa: E402
from strategies.follow_whale import generate_signals  # noqa: E402

TRUTH_DB = Path(__file__).resolve().parent / "data/price_history_api.db"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
PER_TRADE_CAP = 200.0     # H6/H7 微仓限额,仅施于 H6/H7(沿用 run_pzero_oos 规则,非全体)
MIN_TEST_N = 40           # 红线:沿用 run_pzero_oos 默认
# 与 run_h6_truth_exit_check 一致的 4 个 cutoff(+34% 出处);切分变量用 sig_date
# (锚定红线来源 run_pzero_oos.py:157-158 的 r.sig_date,非 run_h6 的 entry_date)。
CUTOFFS = ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]

_LOG: list[str] = []


def pr(line: str = "") -> None:
    """打印并留档,末尾统一落 results/c1_resolution_*.txt(预登记 §7)。"""
    print(line, flush=True)
    _LOG.append(line)


def load_truth(path: Path) -> dict[str, float]:
    if not path.exists():
        sys.exit(f"❌ 找不到权威真值库 {path}\n   先跑 backfill_price_history_api.py --universe whales")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT slug, resolved_yes FROM markets WHERE resolved_yes IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {s: float(v) for s, v in rows}


def load_market_status(path: Path) -> dict[str, tuple]:
    """{slug: (closed, status, resolved_yes)} —— 供 §3.2 逐类归因缺失原因。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT slug, closed, status, resolved_yes FROM markets").fetchall()
    finally:
        conn.close()
    return {s: (c, st, rv) for s, c, st, rv in rows}


def drop_reason(slug: str, mstat: dict[str, tuple]) -> str:
    """为一个"被 arm B 丢弃(无干净真值)"的市场归类丢弃原因。"""
    rec = mstat.get(slug)
    if rec is None:
        return "未回填(不在真值库)"
    closed, status, resolved_yes = rec
    if status != "ok":
        return f"抓取未果({status})"
    if not closed:
        return "尚未 closed(未到期)"
    if resolved_yes is None:
        return "closed 但非 0-1 收盘(voided/多结果)"
    return "有真值(不应在此)"


def build_paired(signals, prices, truth, cost):
    """§3.3 交集闸:逐信号同时算两臂,只有**两臂都可标记**才进样本(一一对应)。

    分类桶:
      intersection : 两臂都可标记 → (paired_a, paired_b) 一一对应,唯一样本集
      truth_missing: A 可标记、B 不可 且 该市场**无干净官方真值** → §3.2 选择偏差待检
      guard_asym_a : A 可标记、B 不可 但 市场**有真值** → §3.3 守卫不对称(end_date vs terminal)
      guard_asym_b : B 可标记、A 不可 → §3.3 守卫不对称(罕见)
      (两臂都不可标记 = 结构性无法入场/标记,不计入任何"独有")
    """
    paired_a, paired_b = [], []
    truth_missing_a, guard_asym_a, guard_asym_b = [], [], []
    for sig in signals:
        ps = prices.get(sig.market)
        if ps is None:
            continue
        rA = simulate_one(sig, ps, cost, "realistic", "resolution", truth=None)
        rB = simulate_one(sig, ps, cost, "realistic", "resolution", truth=truth)
        if rA is not None and rB is not None:
            paired_a.append(rA)
            paired_b.append(rB)
        elif rA is not None and rB is None:
            (guard_asym_a if sig.market in truth else truth_missing_a).append(rA)
        elif rB is not None and rA is None:
            guard_asym_b.append(rB)
    return {
        "paired_a": paired_a, "paired_b": paired_b,
        "truth_missing_a": truth_missing_a,
        "guard_asym_a": guard_asym_a, "guard_asym_b": guard_asym_b,
    }


def selection_bias_gate(paired_a, truth_missing_a, mstat) -> bool:
    """§3.2:官方结算缺失是否与结果相关?用 arm-A 净收益比"被 B 丢弃"vs"被 B 保留"两组。

    返回 True = 触发选择偏差嫌疑(两组资金加权差 > 2%)。
    """
    pr(f"\n{'─'*104}\n🚪 §3.2 选择偏差闸 —— 官方结算缺失是否结果相关?")
    # 逐类报数(缺失原因)
    reasons: dict[str, int] = {}
    for r in truth_missing_a:
        rk = drop_reason(r.market, mstat)
        reasons[rk] = reasons.get(rk, 0) + 1
    n_kept, n_drop = len(paired_a), len(truth_missing_a)
    total = n_kept + n_drop
    pct = (n_drop / total * 100) if total else 0.0
    pr(f"  被 arm B 保留(有干净真值,进样本): {n_kept} 笔")
    pr(f"  被 arm B 丢弃(无干净真值):        {n_drop} 笔  (占 {pct:.1f}%)")
    for reason, k in sorted(reasons.items(), key=lambda x: -x[1]):
        pr(f"      - {reason}: {k}")

    if n_drop == 0:
        pr("  ✅ 无因真值缺失被丢弃的信号 → 不存在此类选择偏差。")
        return False

    kept_cw, _ = cw(paired_a)       # 保留组 arm-A 资金加权净
    drop_cw, _ = cw(truth_missing_a)  # 丢弃组 arm-A 资金加权净
    pr(f"  arm-A 净收益(资金加权): 保留组 {_p(kept_cw)}  vs  丢弃组 {_p(drop_cw)}")
    if kept_cw is None or drop_cw is None:
        pr("  ⚠️ 某组资金加权无法计算(权重为 0)→ 无法判定,保守记触发。")
        return True
    diff = abs(kept_cw - drop_cw)
    pr(f"  两组差 = {diff*100:.2f}pp  (阈值 2.00pp)")
    if diff > 0.02:
        pr("  ⚠️ 触发:丢弃与结果相关 → arm B 的任何 🟢 降级为不可信,须分层复查(预登记 §5 第2行)。")
        return True
    pr("  ✅ 未触发:市场是否已结算与'这笔赢没赢'不显著相关,丢弃可视为时间驱动(噪声)。")
    return False


def intersection_gate(buckets) -> bool:
    """§3.3:交集 + 出声计数。返回 True = 告警(某臂独有 > 交集 5%,守卫可能有结果相关性)。"""
    n_int = len(buckets["paired_a"])
    a_only = len(buckets["guard_asym_a"])
    b_only = len(buckets["guard_asym_b"])
    pr(f"\n{'─'*104}\n🚪 §3.3 交集闸 —— 两臂 per-signal 配对(根治 run_h6 的 2563≠2534)")
    pr(f"  交集(两臂都可标记,唯一样本): {n_int}")
    pr(f"  仅 arm A 可标记 且有真值(守卫不对称): {a_only}")
    pr(f"  仅 arm B 可标记(守卫不对称):         {b_only}")
    thresh = 0.05 * n_int if n_int else 0
    warn = (a_only > thresh) or (b_only > thresh)
    if warn:
        pr(f"  ⚠️ 告警:某臂独有 > 交集 5%({thresh:.0f})→ 守卫逻辑可能藏结果相关性,须排查。")
    else:
        pr(f"  ✅ 两臂独有均 ≤ 交集 5%({thresh:.0f})→ 配对干净。")
    return warn


def evaluate(trades, filters, cutoff: date) -> dict[str, tuple]:
    """每个预登记假设的 test 段 (资金加权净, 抗单点, n)。cap=200 仅 H6/H7(沿用 run_pzero_oos)。"""
    out = {}
    for name, f in filters.items():
        cap = PER_TRADE_CAP if name.startswith(("H6", "H7")) else None
        te = [r for r in trades if f(r) and r.sig_date >= cutoff]
        c, _ = cw(te, cap=cap)
        top = cw_drop_top(te) if len(te) >= 2 else None
        out[name] = (c, top, len(te))
    return out


def is_pass(c, top, n) -> bool:
    """红线(逐字沿用 run_pzero_oos:173-181):n≥40 且 te_cw≥0 且 抗单点≥0。"""
    return n >= MIN_TEST_N and c is not None and c >= 0 and (top is None or top >= 0)


def report_cutoff(cutoff_s, res_a, res_b, filters):
    """打印单 cutoff 下 H0–H7 两臂读数;返回 {hyp: (armB_judgable, armB_pass)}。"""
    cutoff = cutoff_s
    pr(f"\n{'='*104}\n### cutoff {cutoff} (train < cutoff ≤ test, 按 sig_date)")
    pr(f"  {'假设':<24}{'最后快照 cw/剔顶':>26}{'官方结算 cw/剔顶':>26}{'n':>7}{'armB判决':>10}")
    perhyp = {}
    for nm in filters:
        ca, ta, ka = res_a[nm]
        cb, tb, kb = res_b[nm]
        judgable = kb >= MIN_TEST_N
        okb = is_pass(cb, tb, kb)
        mark = ("⚪薄" if not judgable else ("🟢过" if okb else "🔴负"))
        pr(f"  {nm:<24}{_p(ca)+'/'+_p(ta):>26}{_p(cb)+'/'+_p(tb):>26}{kb:>7}{mark:>10}")
        perhyp[nm] = (judgable, okb)
    return perhyp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--cost", default="base", choices=list(PRESETS),
                    help="首跑复现用 base(预登记 §2b);ps_cost 仅作旁证,不并入红线")
    args = ap.parse_args()

    truth = load_truth(TRUTH_DB)
    mstat = load_market_status(TRUTH_DB)
    cost = PRESETS[args.cost]

    conn = connect(args.db)
    prices_all = load_price_series(conn)
    # H6 原生宇宙 = 全量跟鲸鱼(H0,未过滤)信号触及的 distinct 市场并集。
    signals = generate_signals(conn, prices_all)
    conn.close()

    universe = sorted({s.market for s in signals})
    pr("=" * 104)
    pr("🔬 测试 C1:跟鲸鱼 + 持有到真实结算,在 H6 原生宇宙里成不成立?")
    pr("   兑现 PREREG_C1_WHALE_RESOLUTION_2026-07-21 | 唯一变量=了结价口径 | 成本=" + args.cost)
    pr("=" * 104)
    pr(f"  H6 原生宇宙: {len(universe)} distinct 市场 / {len(signals)} 信号(H0 未过滤)")
    pr(f"  其中有官方干净真值(0/1)的市场: {sum(1 for m in universe if m in truth)}")

    # §3.1 下标对齐闸(只在"宇宙内且有真值"的市场上校验全局不变量)
    series_for_align = {m: ps for m, ps in prices_all.items() if m in universe and m in truth}
    pr(f"\n{'─'*104}\n🚪 §3.1 下标对齐闸")
    verify_index_alignment(series_for_align, TRUTH_DB)

    # §3.3 交集闸:构造两臂一一对应的样本
    buckets = build_paired(signals, prices_all, truth, cost)
    gate33 = intersection_gate(buckets)
    paired_a, paired_b = buckets["paired_a"], buckets["paired_b"]

    # §3.2 选择偏差闸
    gate32 = selection_bias_gate(paired_a, buckets["truth_missing_a"], mstat)

    filters = make_filters(prices_all)

    # 逐 cutoff 判决(H0–H7 全列,多重比较)
    perhyp_all = {}  # {hyp: [(judgable, pass) per cutoff]}
    for cutoff_s in CUTOFFS:
        cutoff = date.fromisoformat(cutoff_s)
        res_a = evaluate(paired_a, filters, cutoff)
        res_b = evaluate(paired_b, filters, cutoff)
        perhyp = report_cutoff(cutoff_s, res_a, res_b, filters)
        for nm, v in perhyp.items():
            perhyp_all.setdefault(nm, []).append(v)

    # 外层聚合(§4 本单自加从严:每个可判 cutoff 都过)
    pr(f"\n{'='*104}\n📊 外层聚合(arm B 官方结算:每个可判 cutoff 都过 = 存活候选)")
    pr(f"  {'假设':<24}{'可判 cutoff':>12}{'通过 cutoff':>12}{'跨cutoff':>12}")
    survivors = []
    for nm, seq in perhyp_all.items():
        judgable = [i for i, (j, _) in enumerate(seq) if j]
        passed = [i for i, (j, p) in enumerate(seq) if j and p]
        all_pass = bool(judgable) and len(passed) == len(judgable)
        if all_pass:
            survivors.append(nm)
        tag = "🟢全过" if all_pass else ("🔴有塌" if judgable else "⚪全薄")
        pr(f"  {nm:<24}{len(judgable):>12}{len(passed):>12}{tag:>12}")

    # C1 判决(主张关于 H6)
    h6_name = next((n for n in filters if n.startswith("H6")), None)
    pr(f"\n{'='*104}\n⚖️ C1 判决(预登记 §5)")
    h6_survived = h6_name in survivors
    if not h6_survived:
        pr(f"  🔴 C1 作废:H6 在原生宇宙下未做到'每个可判 cutoff 都过'(arm B)。")
        pr(f"     → +34% 是校准宇宙的假象,口径效应不在 H6 原生总体复现。")
    elif gate32:
        pr(f"  ⚠️ 不算存活:H6 全过,但 §3.2 选择偏差闸触发 → 🟢 可能由结果相关丢弃撑起,默认不采信。")
    elif gate33:
        pr(f"  ⚠️ 存疑:H6 全过、§3.2 未触发,但 §3.3 交集闸告警 → 守卫可能藏结果相关性,须先排查。")
    else:
        pr(f"  🟢 C1 成立:H6 在原生宇宙下 arm B 每个可判 cutoff 都过,且两闸未触发。")
        pr(f"     → 这是一条**新的、待影子盘验证**的候选「跟鲸鱼持有到期」(非 H6 复活)。")
        pr(f"     → 仍须走容量前沿 + 影子盘,方可谈真钱(预登记 §6:即便 🟢 也不构成碰真钱理由)。")
    if survivors and set(survivors) - ({h6_name} if h6_name else set()):
        others = sorted(set(survivors) - ({h6_name} if h6_name else set()))
        pr(f"  ℹ️ 另有非-H6 假设全过 {others} → 记为新发现待重新预登记,不算 C1 存活(§5 多重比较)。")

    pr(f"\n{'='*104}")
    pr("⚠️ 两臂仅限'鲸鱼宇宙内有官方真值'的市场,绝对值不可与速览历史数字比大小,只可两臂间比。")
    pr("   本测试只隔离口径,未加深历史、未验证容量。即便 🟢 也只是持有到期新策略,须影子盘再证。")
    pr("=" * 104)

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"c1_resolution_{date.today():%Y%m%d}.txt"
    out_path.write_text("\n".join(_LOG) + "\n")
    pr(f"\n原始输出已留档: {out_path}")
    return 0 if not survivors else 0  # 判决语义在文本里,退出码恒 0(供自动化留档)


if __name__ == "__main__":
    sys.exit(main())
