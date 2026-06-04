#!/usr/bin/env python3
"""
回测引擎 — 持仓/结算回路
把 (信号 + 价格序列 + 成本模型) 跑成单笔交易结果。

入场口径(两档,见 docs/BACKTEST_DESIGN_2026-06-04.md §3):
- optimistic: 以鲸鱼真实成交价 implied_px 入场(假设我们拿到同价)。
- realistic : 以信号日之后第一个可观测日快照价入场(主口径)。

持有期标记:+Nd 用 entry_date+N 之后第一个快照价;resolution 用终值价(收敛 0/1)。
预测市场持仓 = 以 entry 价买 outcome,每股结算时值 0 或 1 → 收益率 = (mark-entry)/entry。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, timedelta

from .costs import CostModel
from .data import PriceSeries, Signal, outcome_price


RESOLUTION = "resolution"


@dataclass
class TradeResult:
    wallet: str
    market: str
    outcome: str
    sig_date: _date
    horizon: str             # '+1d' / '+7d' / 'resolution'
    entry_date: _date
    entry_price: float       # 含成本的有效入场价
    mark_date: _date
    mark_price: float        # 结果价(已换算到 outcome)
    deployable: float        # 容量约束下实际可部署本金
    gross_ret: float         # 未扣 gas/冲击的收益率
    net_ret: float           # 扣 gas+冲击后的收益率
    markable_days: int       # 该市场快照天数(分层用)
    resolved: bool


def _entry(
    sig: Signal, ps: PriceSeries, cost: CostModel, mode: str
) -> tuple[_date, float, float] | None:
    """返回 (entry_date, entry_eff_price, market_cap_usd)。无法入场返回 None。"""
    if mode == "optimistic" and sig.implied_px is not None:
        # 鲸鱼成交价入场;容量仍取信号日后第一个快照的 volume 代理
        snap = ps.yes_on_or_after(sig.sig_date)
        cap = snap[2] if snap else 0.0
        entry_eff = cost.entry_fill(sig.implied_px, sig.side)
        return sig.sig_date, entry_eff, cap

    # realistic(或 optimistic 缺成交价时回退):信号日后第一个可观测快照
    snap = ps.yes_on_or_after(sig.sig_date)
    if snap is None:
        return None
    snap_date, yes_px, cap = snap
    raw = outcome_price(yes_px, sig.outcome)
    if raw is None or raw <= 0:
        return None
    entry_eff = cost.entry_fill(raw, sig.side)
    return snap_date, entry_eff, cap


def _mark(
    sig: Signal, ps: PriceSeries, entry_date: _date, horizon: str
) -> tuple[_date, float, bool] | None:
    """返回 (mark_date, mark_price_for_outcome, resolved)。无法标记返回 None。"""
    if horizon == RESOLUTION:
        term = ps.terminal()
        if term is None:
            return None
        td, yes_px = term
        if td < entry_date:
            return None
        mp = outcome_price(yes_px, sig.outcome)
        return (td, mp, ps.is_resolved()) if mp is not None else None

    # '+Nd'
    days = int(horizon.lstrip("+").rstrip("d"))
    target = entry_date + timedelta(days=days)
    snap = ps.yes_on_or_after(target)
    if snap is None:
        return None
    md, yes_px, _ = snap
    mp = outcome_price(yes_px, sig.outcome)
    return (md, mp, False) if mp is not None else None


def simulate_one(
    sig: Signal, ps: PriceSeries, cost: CostModel, mode: str, horizon: str
) -> TradeResult | None:
    """模拟单信号在单一持有期下的结果。不可入场/不可标记 → None。"""
    ent = _entry(sig, ps, cost, mode)
    if ent is None:
        return None
    entry_date, entry_price, cap = ent
    if entry_price <= 0:
        return None

    mk = _mark(sig, ps, entry_date, horizon)
    if mk is None:
        return None
    mark_date, mark_price, resolved = mk

    gross = (mark_price - entry_price) / entry_price

    deployable = cost.deployable(sig.notional, cap)
    net = gross - cost.gas_drag(sig.notional) - cost.impact_drag(sig.notional, cap)

    return TradeResult(
        wallet=sig.wallet, market=sig.market, outcome=sig.outcome,
        sig_date=sig.sig_date, horizon=horizon,
        entry_date=entry_date, entry_price=entry_price,
        mark_date=mark_date, mark_price=mark_price,
        deployable=deployable, gross_ret=gross, net_ret=net,
        markable_days=ps.n_days, resolved=resolved,
    )


def run_backtest(
    signals: list[Signal],
    prices: dict[str, PriceSeries],
    cost: CostModel,
    mode: str,
    horizons: list[str],
) -> dict[str, list[TradeResult]]:
    """对每个持有期跑全量信号,返回 {horizon: [TradeResult,...]}。"""
    out: dict[str, list[TradeResult]] = {h: [] for h in horizons}
    for sig in signals:
        ps = prices.get(sig.market)
        if ps is None:
            continue
        for h in horizons:
            r = simulate_one(sig, ps, cost, mode, h)
            if r is not None:
                out[h].append(r)
    return out
