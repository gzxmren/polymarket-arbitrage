#!/usr/bin/env python3
"""
策略 — 精选跟鲸鱼(Roadmap 阶段 C 第 2 策略 / 排查"为什么亏"的后续)

诊断(diagnose_follow_whale.py)发现:无差别跟鲸鱼里
  · 64% 的信号是鲸鱼在 ≥0.85 高价买"准定局",上行封顶、零成本都不赚;
  · 长周期市场是亏损/噪声来源(且 markable_days 是前视量,不能当过滤器)。

本策略只用"入场时已知"的过滤条件,把跟单收窄到有 edge 的子集:
  · 入场价区间 [min_entry_price, max_entry_price):避开高价大热门(和极端长尾彩票)。
  · 到期天数 end_date - entry_date ≤ max_dte:只做短周期市场(可交易、非前视)。
    —— 用 end_date(市场预定结算日,入场即知)而非 markable_days(整段样本长度,前视)。
  · 可选 wallet 白名单(由调用方在样本外纪律下挑选,避免在样本内挑赢家)。

只负责"过滤已模拟的 TradeResult";入场/标记/成本/指标全在 engine 里复用。
"""
from __future__ import annotations

from datetime import timedelta

from engine.data import PriceSeries
from engine.portfolio import TradeResult


def days_to_resolution(r: TradeResult, prices: dict[str, PriceSeries]) -> int | None:
    """到期天数 = 市场 end_date - 入场日。end_date 未知 → None(入场时不可判定短周期)。"""
    ps = prices.get(r.market)
    if ps is None or ps.end_date is None:
        return None
    return (ps.end_date - r.entry_date).days


def passes(
    r: TradeResult,
    prices: dict[str, PriceSeries],
    min_entry_price: float = 0.0,
    max_entry_price: float = 1.0,
    max_dte: int | None = None,
    wallets: set[str] | None = None,
) -> bool:
    """单笔是否通过精选过滤(全部为入场时已知的条件)。"""
    if not (min_entry_price <= r.entry_price < max_entry_price):
        return False
    if wallets is not None and r.wallet not in wallets:
        return False
    if max_dte is not None:
        dte = days_to_resolution(r, prices)
        if dte is None or dte < 0 or dte > max_dte:
            return False
    return True


def filter_results(
    results: list[TradeResult],
    prices: dict[str, PriceSeries],
    min_entry_price: float = 0.0,
    max_entry_price: float = 1.0,
    max_dte: int | None = None,
    wallets: set[str] | None = None,
) -> list[TradeResult]:
    """对一个持有期的 TradeResult 列表施加精选过滤。"""
    return [
        r for r in results
        if passes(r, prices, min_entry_price, max_entry_price, max_dte, wallets)
    ]
