#!/usr/bin/env python3
"""
回测引擎 — 成本模型
Roadmap 阶段 B 核心:缺成本模型,关于"能否赚钱"的一切都是猜。

默认假设(见 docs/BACKTEST_DESIGN_2026-06-04.md §4):
- 交易手续费:0%(Polymarket 现货撮合历史上 0 fee;保留参数位)。
- 滑点垫:在入场价上叠加 slippage_bps 的保守损耗(买入按更差价成交)。
- Gas:每笔固定 gas_usd(Polygon)。摊到名义本金上成为收益拖累。
- 容量:单信号可部署本金 ≤ 市场容量(volume_24h 代理)的 capacity_frac。
        超出部分按线性冲击惩罚(超额比例 × impact_coef)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    fee_bps: float = 0.0          # 交易手续费(基点),Polymarket 默认 0
    slippage_bps: float = 50.0    # 滑点垫(基点)
    gas_usd: float = 0.02         # 每笔 gas(美元)
    capacity_frac: float = 0.02   # 单信号可吃市场容量(volume_24h)的比例
    impact_coef: float = 0.5      # 超容量部分的线性冲击系数(收益惩罚)

    # ---- 入场 ----

    def entry_fill(self, raw_price: float, side: str = "BUY") -> float:
        """
        成交价(含手续费+滑点)。买入时价格变差(更高),卖出时更低。
        价格是 0~1 的概率,乘性叠加损耗;对尾部价(≈0.99)也成立。
        """
        drag = (self.fee_bps + self.slippage_bps) / 10_000.0
        if side == "BUY":
            px = raw_price * (1.0 + drag)
            return min(px, 1.0)
        px = raw_price * (1.0 - drag)
        return max(px, 0.0)

    # ---- gas 拖累(占名义本金的比例)----

    def gas_drag(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return self.gas_usd / notional

    # ---- 容量 ----

    def deployable(self, want_notional: float, market_cap_usd: float) -> float:
        """在容量上限下,实际可部署的本金。"""
        cap = self.capacity_frac * max(market_cap_usd, 0.0)
        if cap <= 0:
            return 0.0
        return min(want_notional, cap)

    def impact_drag(self, want_notional: float, market_cap_usd: float) -> float:
        """
        超容量冲击拖累(占收益的比例)。
        若想吃的量超过容量上限,超额比例越大,冲击惩罚越大。
        """
        cap = self.capacity_frac * max(market_cap_usd, 0.0)
        if cap <= 0 or want_notional <= cap:
            return 0.0
        excess_ratio = (want_notional - cap) / want_notional
        return excess_ratio * self.impact_coef

    def describe(self) -> dict:
        return {
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "gas_usd": self.gas_usd,
            "capacity_frac": self.capacity_frac,
            "impact_coef": self.impact_coef,
        }


# 预设档:用于敏感性扫描
PRESETS = {
    "zero":         CostModel(slippage_bps=0,   gas_usd=0.0,  capacity_frac=1.0, impact_coef=0.0),
    "optimistic":   CostModel(slippage_bps=20,  gas_usd=0.01, capacity_frac=0.05),
    "base":         CostModel(slippage_bps=50,  gas_usd=0.02, capacity_frac=0.02),
    "conservative": CostModel(slippage_bps=150, gas_usd=0.05, capacity_frac=0.01),
}
