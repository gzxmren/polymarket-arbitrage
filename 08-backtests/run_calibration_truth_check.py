#!/usr/bin/env python3
"""测试 A:校准 edge 的 +0.46% 是不是"代理真值剔掉自己亏损案例"剔出来的?

背景(2026-07-15):run_calibration_oos.py 的已结算判定用近似(engine/data.py:81):
    end_date 已过 且 终值价收敛到 <0.05 / >0.95
并且**胜负也由终值价推定**(run_calibration_oos.py:50 `won_yes = term[1] > 0.95`)。
40 市场抽样实测:该近似胜负从不判错(0 冲突),但**静默丢掉 ~26% 的已结算市场**——
丢掉的正是终值未收敛的盘,即"市场到最后还看走眼"的那些。而本策略是**买热门那侧**,
一个收在 0.6 却结算成 NO 的盘正是买热门方的亏损案例。剔除方向 = 系统性抬高净利。

本脚本把真值换成官方权威源(Gamma outcomePrices,经 backfill_price_history_api.py
落到 data/price_history_api.db),**其余一切不变**:同一 2 个月窗口、同一真实
volume_24h 容量、同一价格敏感成本、同一预登记 band 网格与 cutoff。
唯一变量 = 真值口径 → 干净隔离偏差问题。

用法:
  PYTHONPATH=08-backtests python3 08-backtests/run_calibration_truth_check.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.data import connect, load_price_series  # noqa: E402
from run_calibration_oos import (  # noqa: E402
    BANDS,
    CUTOFFS,
    ENTRY_MAX_DTE,
    MIN_TEST_N,
    VOL_FLOOR,
    _p,
    cw,
    cw_drop_top,
    make_trades,
    market_entries,
    winrate,
)
from run_h7b_price_sensitive import calibrate_curve, price_sensitive_cost, CAP  # noqa: E402

TRUTH_DB = Path(__file__).resolve().parent / "data/price_history_api.db"


def load_api_truth(path: Path) -> tuple[dict[str, float], set[str]]:
    """返回 (truth, attempted)。

    truth    : slug -> 官方结算 (1.0=YES赢 / 0.0=NO赢),只含官方已结算且干净 0/1 的市场。
    attempted: 回填**尝试过**的所有 slug —— 这是两种真值口径的**公共可比范围**。
               不加这个范围限制,api 侧会被限制在已回填市场、proxy 侧却用全库 39190 个,
               "真值口径"与"市场范围"两个变量就混了,测试失去意义。
    """
    if not path.exists():
        sys.exit(f"❌ 找不到权威真值库 {path}\n   先跑: python3 08-backtests/backfill_price_history_api.py")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT slug, resolved_yes, status FROM markets").fetchall()
    finally:
        conn.close()
    truth = {s: float(v) for s, v, _ in rows if v is not None}
    # status='error' = 网络失败,关于该市场我们其实一无所知 -> 不算"尝试过",排除出可比范围
    attempted = {s for s, _, st in rows if st != "error"}
    return truth, attempted


def verify_index_alignment(series, path: Path, min_days: int = 4, min_rate: float = 0.95) -> float:
    """闸门:库价与 API 必须指同一侧,否则胜负标签会反,整个测试作废。

    背景:Gamma 的 outcomes 实测只有 54% 是 ["Yes","No"],其余是 ['Long','Short']/队名/
    党派名;而库里 10 万行**一律**标 outcome='Yes'。所以"Yes"是个误称,真正的不变量是
    **下标 0 对齐**:库价 == outcomePrices[0] == clobTokenIds[0] 那一侧。策略本身只做
    "买标价高的那侧",与标签无关,只要求价格与结算指同一侧。

    2026-07-15 实测:重叠≥3天的 346 个市场 100% 对齐、偏差中位数 0.0000(逐位相等)。
    表面的"反向"全部集中在**只有 1 天重叠**的盘(尤以当天打完、盘中剧烈波动的 ATP 网球盘
    为甚):反向占比 1天≈4% → 2-3天≈1% → ≥4天≈0.2%,随数据变多单调趋零 = 噪声特征,
    而非系统性下标错位(若真错位,数据越多应越确信为反)。

    故**不按市场逐个剔除**:那样剔掉的恰是波动最大的市场,反而引入偏差。改为只在
    重叠≥min_days 的市场上校验全局不变量,不达标就中止(同 run_calibration_extended.py
    的 MAD 安全阀纪律:不在脏数据上叠结论)。
    """
    import statistics as st

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        aligned = total = 0
        for market, ps in series.items():
            rows = conn.execute(
                "SELECT date, price_yes FROM price_history WHERE slug=?", (market,)
            ).fetchall()
            if not rows:
                continue
            api_px = dict(rows)
            db_px = dict(zip((d.isoformat() for d in ps.dates), ps.yes_prices))
            common = set(api_px) & set(db_px)
            if len(common) < min_days:
                continue
            same = st.median([abs(api_px[k] - db_px[k]) for k in common])
            flip = st.median([abs((1 - api_px[k]) - db_px[k]) for k in common])
            total += 1
            if same < flip:
                aligned += 1
    finally:
        conn.close()

    if total == 0:
        sys.exit("❌ 闸门无法校验:没有重叠≥%d 天的市场。" % min_days)
    rate = aligned / total
    print(f"  🔒 下标对齐闸门: 重叠≥{min_days}天的 {total} 个市场中 {rate:.1%} 对齐 "
          f"(阈值 {min_rate:.0%})")
    if rate < min_rate:
        sys.exit(
            f"❌ 闸门未通过({rate:.1%} < {min_rate:.0%}):库价与 API 不指同一侧,胜负标签会反。"
            f"\n   中止 —— 不在脏数据上叠结论。"
        )
    return rate


def market_entries_api(series, truth: dict[str, float]):
    """与 run_calibration_oos.market_entries 逐字同构,只把真值换成官方源。

    差异仅两处(即本测试的自变量):
      - 是否入样:官方 closed+干净0/1  ← 原:终值收敛且 end_date 已过
      - 胜负:官方 outcomePrices       ← 原:终值价 > 0.95
    入场规则(dte 1-14 / cap>=VOL_FLOOR / 0.02<px<0.98 / 每市场首个合格点)完全不变。
    """
    out = []
    for ps in series.values():
        won_yes_f = truth.get(ps.market)
        if won_yes_f is None or ps.end_date is None:
            continue
        won_yes = won_yes_f == 1.0
        for d, px, cap in zip(ps.dates, ps.yes_prices, ps.cap_usd):
            dte = (ps.end_date - d).days
            if 1 <= dte <= ENTRY_MAX_DTE and cap >= VOL_FLOOR and 0.02 < px < 0.98:
                out.append((d, px, cap, won_yes))
                break
    return out


def band_table(entries, cost, label: str):
    print(f"\n  [{label}] 入场点 {len(entries)}")
    print(f"  {'band':>6}{'笔数':>7}{'胜率':>8}{'毛资金加权':>12}{'净资金加权':>12}{'净抗单点':>11}")
    rows = {}
    for b in BANDS:
        tr = make_trades(entries, b, cost)
        g, _ = cw(tr, "gross_ret")
        n, _ = cw(tr, "net_ret")
        nt = cw_drop_top(tr, "net_ret")
        rows[b] = (len(tr), winrate(tr), g, n, nt)
        print(f"  {b:>6.2f}{len(tr):>7}{winrate(tr)*100:>7.0f}%{_p(g):>12}{_p(n):>12}{_p(nt):>11}")
    return rows


def oos_verdict(entries, cost, label: str) -> list[float]:
    """复用预登记红线:band 需 ≥2/3 可判 cutoff 做到 test 资金加权≥0 且抗单点≥0。"""
    print(f"\n  [{label}] OOS(test 段 净资金加权/抗单点):")
    print(f"  {'band':>6}", end="")
    for c in CUTOFFS:
        print(f"{c[5:]+' cw/剔顶':>20}", end="")
    print(f"{'判决':>8}")
    winners = []
    for b in BANDS:
        tr = make_trades(entries, b, cost)
        cells, row_pass, usable = [], 0, 0
        for c in CUTOFFS:
            te = [t for t in tr if t.entry_date >= date.fromisoformat(c)]
            te_cw, _ = cw(te, "net_ret")
            te_top = cw_drop_top(te, "net_ret")
            if len(te) < MIN_TEST_N or te_cw is None:
                cells.append(f"{'⚪薄 n='+str(len(te)):>20}")
                continue
            usable += 1
            if te_cw >= 0 and (te_top is None or te_top >= 0):
                row_pass += 1
            cells.append(f"{_p(te_cw)+'/'+_p(te_top):>20}")
        band_ok = usable > 0 and row_pass >= -(-2 * usable // 3)
        if band_ok:
            winners.append(b)
        mark = "🟢过" if band_ok else ("🔴负" if usable else "⚪薄")
        print(f"  {b:>6.2f}" + "".join(cells) + f"{mark:>8}")
    return winners


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = connect(args.db)
    series_all = load_price_series(conn)
    conn.close()

    truth, attempted = load_api_truth(TRUTH_DB)

    # ★公共可比范围:只保留回填尝试过的市场,两种真值口径在**同一批市场**上对照。
    series = {m: ps for m, ps in series_all.items() if m in attempted}
    missing = len(attempted) - len(series)

    med_curve, meta = calibrate_curve("median")
    cost = price_sensitive_cost(med_curve)

    print("=" * 96)
    print("🔬 测试A:真值口径对校准 edge 的影响(唯一变量=真值来源,其余全同)")
    print("=" * 96)
    print(f"  公共可比范围: 回填尝试过 {len(attempted)} 个市场,库内有价 {len(series)} 个"
          + (f" (对不上 {missing})" if missing else ""))
    print(f"  其中官方已结算(有权威真值): {len(truth)} | 成本: 探针中位曲线({meta['probe_days']}天) 每笔 ${CAP:.0f}")
    verify_index_alignment(series, TRUTH_DB)

    e_proxy = market_entries(series, cost)
    e_api = market_entries_api(series, truth)

    # ---- 口径差异体检:代理丢了谁、判错没有 ----
    proxy_ok = {ps.market for ps in series.values() if ps.is_resolved() and ps.end_date}
    api_ok = {m for m in truth if m in series}
    both = proxy_ok & api_ok
    disagree = sum(
        1 for m in both if (series[m].terminal()[1] > 0.95) != (truth[m] == 1.0)
    )
    print(f"\n{'─'*96}\n① 口径体检:")
    print(f"  代理判'已结算': {len(proxy_ok):>5}   官方判'已结算'(且库内有价): {len(api_ok):>5}")
    print(f"  官方认、代理漏 : {len(api_ok - proxy_ok):>5}  ← 被静默剔除的样本(终值未收敛=市场看走眼的盘)")
    print(f"  代理认、官方否 : {len(proxy_ok - api_ok):>5}")
    print(f"  两者都认里胜负冲突: {disagree}  (0=代理判胜负不出错,问题只在'丢样本'非'判错')")

    print(f"\n{'─'*96}\n② 全样本校准全景:")
    r_proxy = band_table(e_proxy, cost, "代理真值(原口径)")
    r_api = band_table(e_api, cost, "官方真值(权威)")

    print(f"\n{'─'*96}\n③ 净资金加权 逐 band 对照(← 本测试的核心):")
    print(f"  {'band':>6}{'代理净':>12}{'官方净':>12}{'差':>12}{'代理n':>8}{'官方n':>8}")
    for b in BANDS:
        np_, na = r_proxy[b][3], r_api[b][3]
        d = (na - np_) if (np_ is not None and na is not None) else None
        print(f"  {b:>6.2f}{_p(np_):>12}{_p(na):>12}{_p(d):>12}{r_proxy[b][0]:>8}{r_api[b][0]:>8}")

    print(f"\n{'─'*96}\n④ OOS 红线判决:")
    w_proxy = oos_verdict(e_proxy, cost, "代理真值(原口径)")
    w_api = oos_verdict(e_api, cost, "官方真值(权威)")

    print(f"\n{'='*96}\n⚖️ 判决:")
    print(f"  代理真值下通过的 band: {w_proxy or '无'}")
    print(f"  官方真值下通过的 band: {w_api or '无'}")
    # "存活"必须是**同一个 band** 两种口径都过;换个 band 过了是新发现,不是存活。
    survived = sorted(set(w_proxy) & set(w_api))
    if not w_proxy and not w_api:
        print("  ⚪ 两种口径都不通过 → 与真值口径无关,edge 本就不成立。")
    elif w_proxy and not w_api:
        print("  🔴 edge 在权威真值下消失 → 原 +0.46% 是**剔除偏差的产物**,不是真 edge。")
    elif survived:
        print(f"  🟢 band {survived} 在两种口径下都通过 → 剔除偏差不是主因。")
        if set(w_proxy) - set(survived):
            print(f"     ⚠️ 但 band {sorted(set(w_proxy)-set(survived))} 只在代理口径下过,权威口径下没过 → 那部分是偏差产物。")
    else:
        print(f"  ⚠️ 无同一 band 两口径都过:代理过 {w_proxy}、权威过 {w_api} 且不重合。")
        print("     这不是'存活'——原 band 被证伪,新 band 是换了口径才冒出来的,属多重比较嫌疑,须重新预登记。")
    print(f"  ⚠️ 仍是同一 2 个月窗口(05-09~07-14)、cutoff 嵌套非独立。本测试只隔离'真值口径'一个变量。")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
