#!/usr/bin/env python3
"""
回测引擎 — 数据层
读 DB → 标准化"信号事件流"+"价格序列",供任意策略复用。

数据契约(2026-06-04 实测,见 docs/BACKTEST_DESIGN_2026-06-04.md):
- changes: 鲸鱼成交流(信号源)。change_amount=USD名义, new_size-old_size=股数,
           implied_px = change_amount/Δshares 即鲸鱼真实成交价。
- daily_price_snapshots: 每市场逐日 Yes 价(outcome 恒为 'Yes'); No 价 = 1 - Yes。
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from pathlib import Path
import sys

# 复用项目配置取 DB 路径(支持 POLYMARKET_DB 覆盖)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "06-tools" / "analysis"))
from config import DASHBOARD_DB_FILE  # noqa: E402


# ---------- 工具 ----------

def _to_date(s: str) -> _date | None:
    """把多种时间串解析成 date。支持 '2026-05-04T07:30:14+00:00' 与 '2026-05-09'。"""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("+00:00", "")).date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def outcome_price(yes_price: float, outcome: str) -> float | None:
    """把市场的 Yes 价换成指定结果(Yes/No)的价格。"""
    if yes_price is None:
        return None
    if outcome == "Yes":
        return yes_price
    if outcome == "No":
        return 1.0 - yes_price
    return None  # 多结果市场不在可回测宇宙内


# ---------- 价格序列 ----------

@dataclass
class PriceSeries:
    """单个市场的逐日 Yes 价序列(按日期升序)。"""
    market: str
    end_date: _date | None
    dates: list[_date] = field(default_factory=list)        # 升序
    yes_prices: list[float] = field(default_factory=list)   # 与 dates 对齐
    cap_usd: list[float] = field(default_factory=list)      # volume_24h,容量代理

    @property
    def n_days(self) -> int:
        return len(self.dates)

    def yes_on_or_after(self, d: _date) -> tuple[_date, float, float] | None:
        """返回 >= d 的第一个快照 (date, yes_price, cap_usd)。无则 None。"""
        i = bisect_left(self.dates, d)
        if i >= len(self.dates):
            return None
        return self.dates[i], self.yes_prices[i], self.cap_usd[i]

    def terminal(self) -> tuple[_date, float] | None:
        """最后一个可观测快照 (date, yes_price)。"""
        if not self.dates:
            return None
        return self.dates[-1], self.yes_prices[-1]

    def is_resolved(self) -> bool:
        """近似已结算:end_date 已过 且 终值价收敛到 0/1 附近。"""
        term = self.terminal()
        if term is None:
            return False
        _, ypx = term
        converged = ypx < 0.05 or ypx > 0.95
        past = self.end_date is not None and self.dates[-1] >= self.end_date
        return converged and past


def load_price_series(conn: sqlite3.Connection) -> dict[str, PriceSeries]:
    """加载全部市场的逐日 Yes 价序列。"""
    rows = conn.execute(
        """
        SELECT market, snapshot_date, price, volume_24h, end_date
        FROM daily_price_snapshots
        WHERE outcome = 'Yes'
        ORDER BY market, snapshot_date
        """
    ).fetchall()

    series: dict[str, PriceSeries] = {}
    for market, snap, price, vol24, end_date in rows:
        d = _to_date(snap)
        if d is None:
            continue
        ps = series.get(market)
        if ps is None:
            ps = PriceSeries(market=market, end_date=_to_date(end_date))
            series[market] = ps
        # 同市场同日去重(UNIQUE 约束理应保证,稳妥起见跳过回退)
        if ps.dates and ps.dates[-1] == d:
            continue
        ps.dates.append(d)
        ps.yes_prices.append(float(price))
        ps.cap_usd.append(float(vol24 or 0.0))
    return series


# ---------- 信号事件 ----------

@dataclass
class Signal:
    wallet: str
    market: str
    outcome: str          # 'Yes' | 'No'
    side: str             # 'BUY' | 'SELL'
    sig_date: _date
    shares: float         # |new_size - old_size|
    notional: float       # change_amount (USD)
    implied_px: float | None  # 鲸鱼真实成交价 = notional/shares,落在 (0,1] 才有效


def load_signals(
    conn: sqlite3.Connection,
    prices: dict[str, PriceSeries],
    sides: tuple[str, ...] = ("BUY",),
    min_notional: float = 0.0,
) -> list[Signal]:
    """
    从 changes 生成可回测信号:
      - 仅二元 Yes/No 结果(价格表只存 Yes 价)
      - market 必须在价格表中(否则无法标记)
      - 仅取指定 side(默认 BUY)
    """
    rows = conn.execute(
        """
        SELECT wallet, market, outcome, side, timestamp,
               old_size, new_size, change_amount
        FROM changes
        WHERE outcome IN ('Yes','No')
          AND market != ''
        """
    ).fetchall()

    signals: list[Signal] = []
    for wallet, market, outcome, side, ts, old_size, new_size, amount in rows:
        if side not in sides:
            continue
        if market not in prices:
            continue
        d = _to_date(ts)
        if d is None:
            continue
        shares = abs(float(new_size or 0) - float(old_size or 0))
        notional = abs(float(amount or 0))
        if shares <= 0 or notional < min_notional:
            continue
        implied = notional / shares if shares > 0 else None
        if implied is not None and not (0.0 < implied <= 1.0):
            implied = None  # 脏价,不当作有效成交价(仍保留信号,用快照价入场)
        signals.append(Signal(
            wallet=wallet, market=market, outcome=outcome, side=side,
            sig_date=d, shares=shares, notional=notional, implied_px=implied,
        ))
    return signals


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DASHBOARD_DB_FILE
    if not path.exists():
        raise FileNotFoundError(f"DB 不存在: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
