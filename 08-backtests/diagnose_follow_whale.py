#!/usr/bin/env python3
"""
诊断 — 跟鲸鱼策略「为什么亏」(一次性排查脚本,非回测引擎一部分)
把 gross→net 的亏损来源逐项拆开,验证三个假设:
  H1 成本模型把"鲸鱼下单量"当成"我们的下单量" → impact/gas 被高估
  H2 彩票型收益:edge 全靠极少数大赢家,胜率低、中位负
  H3 入场价越低(长尾)越是亏损来源 / 收益结构
  H4 15d+ 长周期市场即使零成本也亏

用法: PYTHONPATH=08-backtests python3 08-backtests/diagnose_follow_whale.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.costs import PRESETS, CostModel
from engine.data import connect, load_price_series
from engine.portfolio import simulate_one
from strategies.follow_whale import generate_signals


def pct(x):
    return f"{x*100:+.2f}%" if x is not None else "  — "


def main():
    conn = connect()
    prices = load_price_series(conn)
    signals = generate_signals(conn, prices)
    conn.close()

    cost = PRESETS["base"]
    horizon = "resolution"
    mode = "realistic"

    # 收集单笔结果 + 逐项成本拆解
    rows = []
    for sig in signals:
        ps = prices.get(sig.market)
        if ps is None:
            continue
        r = simulate_one(sig, ps, cost, mode, horizon)
        if r is None:
            continue
        # 容量:用入场时快照的 cap;gas 摊到 deployable(与引擎一致),impact 仅作旧模型对照
        snap = ps.yes_on_or_after(sig.sig_date)
        cap = snap[2] if snap else 0.0
        gas = cost.gas_drag(r.deployable)
        impact = cost.impact_drag(sig.notional, cap)
        # 滑点对收益的影响 = 同信号零滑点 gross - 含滑点 gross
        r0 = simulate_one(sig, ps, PRESETS["zero"], mode, horizon)
        slip = (r0.gross_ret - r.gross_ret) if r0 else 0.0
        rows.append(dict(
            net=r.net_ret, gross=r.gross_ret, gross0=r0.gross_ret if r0 else r.gross_ret,
            gas=gas, impact=impact, slip=slip,
            entry=r.entry_price, notional=sig.notional, cap=cap,
            mdays=r.markable_days, resolved=r.resolved,
        ))

    n = len(rows)
    print("=" * 70)
    print(f"诊断: 跟鲸鱼 / cost=base / horizon=resolution / mode=realistic / n={n}")
    print("=" * 70)

    # ---- 成本归因:gross(零) → net 的逐项扣减 ----
    # 注:gas 用 deployable 口径(与修复后引擎一致);impact 仅作"旧模型对照",引擎已不再计。
    print("\n【成本归因】每信号平均收益分解(从零成本毛收益逐项扣到净):")
    mg0 = mean(r["gross0"] for r in rows)
    mslip = mean(r["slip"] for r in rows)
    mgas = mean(r["gas"] for r in rows)
    mimp = mean(r["impact"] for r in rows)
    mnet = mean(r["net"] for r in rows)
    print(f"  毛收益(零成本)   {pct(mg0)}")
    print(f"  - 滑点拖累        {pct(-mslip)}")
    print(f"  - gas 拖累        {pct(-mgas)}   (摊到 deployable)")
    print(f"  = 净收益          {pct(mnet)}")
    print(f"  [校验] gross0-滑点-gas = {pct(mg0 - mslip - mgas)} vs 实际净 {pct(mnet)}")
    print(f"  [旧模型对照] 若仍按鲸鱼全额 notional 罚冲击 -{abs(mimp)*100:.2f}% → 旧净 "
          f"{pct(mnet - mimp)}(这正是修复前的 -1.56% 来源)")

    # ---- H1: 冲击拖累有多普遍/多大 ----
    hit = [r for r in rows if r["impact"] > 0]
    print(f"\n【H1 冲击拖累】{len(hit)}/{n} ({len(hit)/n*100:.0f}%) 信号触发冲击惩罚")
    if hit:
        print(f"  触发者平均冲击拖累 {pct(mean(r['impact'] for r in hit))}  "
              f"(最大 {pct(max(r['impact'] for r in hit))})")
        print(f"  其中冲击=上限(0.5)的有 {sum(1 for r in hit if r['impact']>=0.499)} 个")
    print(f"  gas 拖累 > 1% 的信号: {sum(1 for r in rows if r['gas']>0.01)} 个 "
          f"(notional 太小)")
    # 若按"我们固定下 $X"而非鲸鱼全额,成本会怎样?
    for our in (50, 200, 1000):
        gas2 = mean(cost.gas_drag(our) for _ in rows)
        imp2 = mean(cost.impact_drag(min(our, r["notional"]), r["cap"]) for r in rows)
        net2 = mg0 - mslip - gas2 - imp2
        print(f"  若我们固定下 ${our:>4}/信号: gas {pct(-gas2)} 冲击 {pct(-imp2)} "
              f"→ 净 {pct(net2)}")

    # ---- H2: 彩票型收益 ----
    nets = sorted(r["net"] for r in rows)
    wins = [r for r in rows if r["net"] > 0]
    print(f"\n【H2 收益形状】胜率 {len(wins)/n*100:.1f}%  中位 {pct(median(nets))}  "
          f"均值 {pct(mean(nets))}")
    top1 = nets[-max(1, n//100):]
    print(f"  最赚的 1% ({len(top1)}笔) 贡献总收益: "
          f"{sum(top1)/sum(nets)*100:.0f}% (总收益 sum={sum(nets):.1f})")
    print(f"  去掉最赚的 1% 后均值: {pct(mean(nets[:-len(top1)]))}")

    # ---- H3: 按入场价分桶 ----
    print(f"\n【H3 入场价分桶】(净收益,看长尾低价 vs 高价):")
    buckets = [(0,0.05),(0.05,0.15),(0.15,0.35),(0.35,0.65),(0.65,0.85),(0.85,1.01)]
    for lo, hi in buckets:
        b = [r for r in rows if lo <= r["entry"] < hi]
        if b:
            wr = sum(1 for r in b if r["net"]>0)/len(b)
            print(f"  入场价[{lo:.2f},{hi:.2f}): n={len(b):4d}  净均值 {pct(mean(r['net'] for r in b))}  "
                  f"毛均值 {pct(mean(r['gross0'] for r in b))}  胜率 {wr*100:.0f}%")

    # ---- H4: 按可标记天数分层(零成本毛 vs 净) ----
    print(f"\n【H4 快照天数分层】(零成本毛 vs base净):")
    def strat(d):
        return "1-2d" if d<3 else "3-6d" if d<7 else "7-14d" if d<15 else "15d+"
    by = {}
    for r in rows:
        by.setdefault(strat(r["mdays"]), []).append(r)
    for k in ("1-2d","3-6d","7-14d","15d+"):
        b = by.get(k, [])
        if b:
            print(f"  {k:>5}: n={len(b):4d}  毛(零){pct(mean(r['gross0'] for r in b))}  "
                  f"净(base){pct(mean(r['net'] for r in b))}  "
                  f"已结算占比 {sum(1 for r in b if r['resolved'])/len(b)*100:.0f}%")


if __name__ == "__main__":
    main()
