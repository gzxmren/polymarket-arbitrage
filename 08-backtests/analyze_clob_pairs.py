#!/usr/bin/env python3
"""
CLOB pair-cost 前向测试 — 读结论

读 07-data/clob_pair_log.db，回答前向套利的核心问题:
  1. 累积了多少快照、跨度多久?
  2. 买边配对成本(yes_ask+no_ask)分布离 $1 有多近? 是否曾 < $1(可成交套利)?
  3. 卖边配对收入(yes_bid+no_bid)是否曾 > $1?
  4. 历来出现过几次净>0 的可成交套利? 在哪些市场?

诚实判读:
  - 若数千快照里 min(买边) 始终 ≥ 1.00 → Polymarket 主流动市场无 pair-cost 套利空间(高效)。
  - 若偶现 <1 但深度极小/转瞬即逝 → 对小资金不可成交。
  - 若反复出现 <1 且有深度 → 罕见但真实，值得进一步做执行可行性研究。

用法: python3 08-backtests/analyze_clob_pairs.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "07-data" / "clob_pair_log.db"


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    i = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
    return sorted_vals[i]


def main():
    if not DB_PATH.exists():
        print(f"❌ 尚无数据: {DB_PATH}（记录器还没跑过）")
        return

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()

    # 概览
    runs, t_min, t_max = cur.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM clob_pair_runs").fetchone()
    n_snap = cur.execute("SELECT COUNT(*) FROM clob_pair_log").fetchone()[0]

    print("=" * 70)
    print("📡 CLOB Pair-Cost 前向测试 — 累积结论")
    print("=" * 70)
    print(f"扫描轮次: {runs}   市场快照: {n_snap}")
    print(f"时间跨度: {t_min}  →  {t_max}")

    if n_snap == 0:
        print("\n⚪ 暂无快照数据。")
        conn.close()
        return

    # 买边配对成本分布
    buys = [r[0] for r in cur.execute(
        "SELECT buy_pair_cost FROM clob_pair_log WHERE buy_pair_cost IS NOT NULL").fetchall()]
    buys.sort()
    print(f"\n【买边配对成本 yes_ask+no_ask】(越低越接近套利, <1 即可成交)")
    print(f"  最低 {buys[0]:.4f}  |  p1 {pct(buys,1):.4f}  |  p5 {pct(buys,5):.4f}  |  中位 {pct(buys,50):.4f}")
    below_1 = sum(1 for b in buys if b < 1.0)
    print(f"  < $1.00 的快照: {below_1} / {len(buys)} ({below_1/len(buys)*100:.2f}%)")

    # 卖边配对收入分布
    sells = [r[0] for r in cur.execute(
        "SELECT sell_pair_proceeds FROM clob_pair_log WHERE sell_pair_proceeds IS NOT NULL").fetchall()]
    sells.sort(reverse=True)
    print(f"\n【卖边配对收入 yes_bid+no_bid】(越高越接近套利, >1 即可成交)")
    print(f"  最高 {sells[0]:.4f}  |  p1 {pct(sells,1):.4f}  |  p5 {pct(sells,5):.4f}  |  中位 {pct(sells,50):.4f}")
    above_1 = sum(1 for s in sells if s > 1.0)
    print(f"  > $1.00 的快照: {above_1} / {len(sells)} ({above_1/len(sells)*100:.2f}%)")

    # 净套利事件
    n_buy_arb = cur.execute("SELECT COUNT(*) FROM clob_pair_log WHERE buy_arb=1").fetchone()[0]
    n_sell_arb = cur.execute("SELECT COUNT(*) FROM clob_pair_log WHERE sell_arb=1").fetchone()[0]
    print(f"\n【净>0 可成交套利事件(扣 gas)】买边 {n_buy_arb} 次  卖边 {n_sell_arb} 次")
    if n_buy_arb or n_sell_arb:
        print("  🟢 历史出现过可成交套利，明细(前10):")
        for r in cur.execute("""
            SELECT ts, market_slug, buy_pair_cost, buy_exec_pairs, sell_pair_proceeds, sell_exec_pairs
            FROM clob_pair_log WHERE buy_arb=1 OR sell_arb=1
            ORDER BY buy_pair_cost ASC LIMIT 10""").fetchall():
            print(f"   {r[0][:16]} {r[1][:40]:<40} buy={r[2]:.4f}(×{r[3]:.0f}) sell={r[4]:.4f}(×{r[5]:.0f})")
    else:
        print("  ⚪ 至今无任何净>0 可成交套利。")

    # 判读
    print("\n" + "=" * 70)
    tightest = buys[0]
    if n_buy_arb == 0 and n_sell_arb == 0:
        if tightest >= 1.0:
            print("⚖️ 判读: 主流动市场配对成本始终 ≥$1（最紧 {:.4f}）。".format(tightest))
            print("   Polymarket 对 pair-cost 套利高效，小资金无空间。继续积累以确认。")
        else:
            print("⚖️ 判读: 偶有 <$1 但扣 gas 后净≤0 或深度不足，不可成交。")
    else:
        print("⚖️ 判读: 出现过可成交套利！需进一步评估持续性与执行可行性。")
    print(f"   样本仍在累积中（建议≥2周/≥600轮再下定论）。")
    print("=" * 70)

    conn.close()


if __name__ == "__main__":
    main()
