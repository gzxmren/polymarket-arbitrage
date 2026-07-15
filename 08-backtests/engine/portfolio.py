#!/usr/bin/env python3
"""
回测引擎 — 持仓/结算回路
把 (信号 + 价格序列 + 成本模型) 跑成单笔交易结果。

入场口径(两档,见 docs/BACKTEST_DESIGN_2026-06-04.md §3):
- optimistic: 以鲸鱼真实成交价 implied_px 入场(假设我们拿到同价)。
- realistic : 以信号日之后第一个可观测日快照价入场(主口径)。

持有期标记:+Nd 用 entry_date+N 之后第一个快照价;resolution 用终值价(收敛 0/1)。
预测市场持仓 = 以 entry 价买 outcome,每股结算时值 0 或 1 → 收益率 = (mark-entry)/entry。

容量口径(2026-06-05 修正,见 diagnose_follow_whale.py):
  容量是"吞吐上限"(我们最多放到 deployable=min(想跟的量, 容量)),不是"每笔收益扣血"。
  既然我们从不超过容量,就不产生市场冲击 → 收益率(%)与下单规模无关,只被滑点(已并入入场价)
  和固定 gas(摊到我们实际部署的 deployable 本金,而非鲸鱼全额 notional)拖累。
  旧实现用鲸鱼全额 notional 去罚 impact,等于假设我们照搬鲸鱼 $几十万的单去打深度——
  那不是策略亏,是建模把账算亏了。容量的真实代价是"能部署的钱更少"(体现在 total_deployable)。
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
    sig: Signal,
    ps: PriceSeries,
    entry_date: _date,
    horizon: str,
    truth: dict[str, float] | None = None,
) -> tuple[_date, float, bool] | None:
    """返回 (mark_date, mark_price_for_outcome, resolved)。无法标记返回 None。

    truth: {market_slug: 官方结算(1.0=库内'Yes'侧赢 / 0.0=输)}。
      仅作用于 horizon==RESOLUTION。**传 None 时行为与历史完全一致**(默认)。

    为什么需要它(2026-07-15):RESOLUTION 原本用 `ps.terminal()`(我们最后一次拍到的
    快照价)当了结价。对已收敛的市场≈真实结算,无害;但实测 **80% 的已结算市场终值
    未收敛**,对它们这等于**假设自己能在中间价、无成本地平仓**——而真正持有到期是
    没有卖出动作的(直接赔付),这个"免费中间价出场"是凭空多出来的。
    见 docs/PREREG_H6_TRUTH_EXIT_2026-07-15.md(测试 C1)。
    """
    if horizon == RESOLUTION:
        if truth is not None:
            settled = truth.get(sig.market)
            if settled is None:
                return None  # 无权威真值 -> 不猜(由调用方限定市场范围保证两臂可比)
            mp = outcome_price(settled, sig.outcome)  # 我方赢=1.0 / 输=0.0
            if mp is None:
                return None
            md = ps.end_date or ps.dates[-1]
            if md < entry_date:
                return None  # 与 terminal 分支同样的守卫,保持两臂对称(否则 armB 会多收交易)
            return (md, mp, True)
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
    sig: Signal,
    ps: PriceSeries,
    cost: CostModel,
    mode: str,
    horizon: str,
    truth: dict[str, float] | None = None,
    exit_cost: bool = False,
) -> TradeResult | None:
    """模拟单信号在单一持有期下的结果。不可入场/不可标记 → None。

    truth / exit_cost 均为**可选修正**,默认关闭时行为与历史完全一致(见 _mark 与下方注释)。
    """
    ent = _entry(sig, ps, cost, mode)
    if ent is None:
        return None
    entry_date, entry_price, cap = ent
    if entry_price <= 0:
        return None

    mk = _mark(sig, ps, entry_date, horizon, truth)
    if mk is None:
        return None
    mark_date, mark_price, resolved = mk

    # exit_cost(2026-07-15,测试 C2):原本 entry_price 已扣入场成本,mark_price 却是
    # **裸中间价**——卖出滑点为零。对 RESOLUTION(到期直接赔付,没有卖出动作)这是对的;
    # 但对 '+Nd' 是错的:一天后要真卖,必须吃进盘口。探针实测卖出滑点 120bps(均衡盘)
    # ~770bps(贴边盘)。见 docs/PREREG_H6_TRUTH_EXIT_2026-07-15.md。
    if exit_cost and horizon != RESOLUTION:
        mark_price = cost.entry_fill(mark_price, "SELL")  # 卖出成交价(低于中间价)

    gross = (mark_price - entry_price) / entry_price

    # 容量=吞吐上限:我们最多放到 deployable;不超过容量 → 无冲击。
    # 成本只剩固定 gas,摊到我们实际部署的本金(deployable),而非鲸鱼全额 notional。
    deployable = cost.deployable(sig.notional, cap)
    net = gross - cost.gas_drag(deployable)

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
    truth: dict[str, float] | None = None,
    exit_cost: bool = False,
) -> dict[str, list[TradeResult]]:
    """对每个持有期跑全量信号,返回 {horizon: [TradeResult,...]}。

    truth / exit_cost 默认关闭 → 与历史行为逐字节一致,不影响任何既有结果。
    """
    out: dict[str, list[TradeResult]] = {h: [] for h in horizons}
    for sig in signals:
        ps = prices.get(sig.market)
        if ps is None:
            continue
        for h in horizons:
            r = simulate_one(sig, ps, cost, mode, h, truth, exit_cost)
            if r is not None:
                out[h].append(r)
    return out
