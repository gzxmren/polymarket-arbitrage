#!/usr/bin/env python3
"""
策略 — 跟精选鲸鱼(Roadmap 阶段 C 第 1 策略)
信号:鲸鱼 BUY 二元 Yes/No 结果。喂入通用 engine 跑回测。

本文件只负责"信号生成";入场/标记/成本/指标全在 engine 里复用。
"""

from __future__ import annotations

import sqlite3

from engine.data import PriceSeries, Signal, load_signals


def generate_signals(
    conn: sqlite3.Connection,
    prices: dict[str, PriceSeries],
    include_sell: bool = False,
    min_notional: float = 0.0,
) -> list[Signal]:
    """跟鲸鱼:默认只跟 BUY。include_sell 时一并纳入(作减仓/退出研究)。"""
    sides = ("BUY", "SELL") if include_sell else ("BUY",)
    return load_signals(conn, prices, sides=sides, min_notional=min_notional)
