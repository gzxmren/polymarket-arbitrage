#!/usr/bin/env python3
"""
策略 — 逆鲸鱼(大额+超流动市场)

经济假设:
  在高流动市场(vol24 > $200k)里,鲸鱼没有信息优势;大额 BUY 产生
  短期价格冲击,价格随后均值回归。我们在鲸鱼 BUY Yes 的信号日后
  以 No 仓位(= 1 - yes_price)入场,等待价格回落。

信号定义(预登记):
  - 鲸鱼原始信号: BUY Yes,notional ≥ min_notional
  - 入场方向: 反转为 BUY No(outcome='No')
  - 不变: wallet / market / sig_date / shares / notional

与 follow_whale 的区别:
  - outcome 从 'Yes' → 'No' → engine 自动取 1-yes_price 入场/标记
  - 没有 include_sell 选项:逆势只针对 BUY
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from engine.data import PriceSeries, Signal, load_signals


def generate_contrarian_signals(
    conn: sqlite3.Connection,
    prices: dict[str, PriceSeries],
    min_notional: float = 1000.0,
    min_vol24: float = 200_000.0,
) -> list[Signal]:
    """
    只取 Yes BUY 中 notional≥min_notional 且流动市场(vol24≥min_vol24)的信号,
    反转 outcome 为 'No'。
    min_notional 默认 $1000:P0 诊断显示该桶占 64.6% 资金且跟鲸鱼 CW -2.16%,
    逆势假设该桶会产生正 alpha。
    """
    yes_sigs = load_signals(conn, prices, sides=("BUY",), min_notional=min_notional)

    contrarian: list[Signal] = []
    for s in yes_sigs:
        ps = prices.get(s.market)
        if ps is None:
            continue
        snap = ps.yes_on_or_after(s.sig_date)
        if snap is None:
            continue
        _, _, vol24 = snap
        if vol24 < min_vol24:
            continue
        # No 价 = 1 - yes_price; 若 yes_price 接近 1,No 价接近 0 流动性极差 → 跳过
        _, yes_px, _ = snap
        no_px = 1.0 - yes_px
        if no_px <= 0.02:
            continue
        contrarian.append(replace(s, outcome="No"))

    return contrarian
