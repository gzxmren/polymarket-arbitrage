#!/usr/bin/env python3
"""
做市策略
在订单簿同时挂买单和卖单，赚取买卖价差
"""

import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class OrderBookLevel:
    """订单簿档位"""
    price: float
    size: float
    total: float = 0  # 累计数量


@dataclass
class MarketMakingSignal:
    """做市信号"""
    market: str
    bid_price: float
    ask_price: float
    spread: float
    spread_pct: float
    suggested_bid: float
    suggested_ask: float
    expected_profit: float
    confidence: float
    timestamp: datetime


class MarketMaker:
    """做市策略分析器"""
    
    def __init__(self, min_spread_pct: float = 0.01, max_position: float = 1000):
        """
        初始化
        
        Args:
            min_spread_pct: 最小价差百分比（默认1%）
            max_position: 最大持仓金额
        """
        self.min_spread_pct = min_spread_pct
        self.max_position = max_position
        self.active_orders = {}  # {market: {side: price}}
        
    def analyze_order_book(self, order_book: Dict) -> Optional[MarketMakingSignal]:
        """
        分析订单簿，生成做市信号
        
        Args:
            order_book: {
                'bids': [[price, size], ...],
                'asks': [[price, size], ...]
            }
            
        Returns:
            做市信号或 None
        """
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])
        
        if not bids or not asks:
            return None
        
        # 最优买卖价
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        
        # 计算价差
        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2
        spread_pct = spread / mid_price if mid_price > 0 else 0
        
        # 检查是否满足做市条件
        if spread_pct < self.min_spread_pct:
            return None
        
        # 计算建议挂单价格
        # 在买卖中间挂单，赚取部分价差
        suggested_bid = best_bid + spread * 0.3
        suggested_ask = best_ask - spread * 0.3
        
        # 确保我们的价格仍在买卖价差内
        if suggested_bid >= suggested_ask:
            return None
        
        # 计算预期利润
        expected_profit = (suggested_ask - suggested_bid) / mid_price
        
        # 计算置信度（基于价差大小和订单簿深度）
        confidence = self._calculate_confidence(order_book, spread_pct)
        
        return MarketMakingSignal(
            market=order_book.get('market', 'Unknown'),
            bid_price=best_bid,
            ask_price=best_ask,
            spread=spread,
            spread_pct=spread_pct,
            suggested_bid=suggested_bid,
            suggested_ask=suggested_ask,
            expected_profit=expected_profit,
            confidence=confidence,
            timestamp=datetime.now()
        )
    
    def _calculate_confidence(self, order_book: Dict, spread_pct: float) -> float:
        """
        计算做市置信度
        
        基于：
        1. 价差大小
        2. 订单簿深度
        3. 买卖平衡度
        """
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])
        
        if not bids or not asks:
            return 0.0
        
        # 价差因子 (0-0.5)
        spread_factor = min(spread_pct / 0.05, 1.0) * 0.5
        
        # 深度因子 (0-0.3)
        bid_depth = sum(b[1] for b in bids[:5])
        ask_depth = sum(a[1] for a in asks[:5])
        min_depth = min(bid_depth, ask_depth)
        depth_factor = min(min_depth / 10000, 1.0) * 0.3
        
        # 平衡因子 (0-0.2)
        if bid_depth + ask_depth > 0:
            balance = 1 - abs(bid_depth - ask_depth) / (bid_depth + ask_depth)
            balance_factor = balance * 0.2
        else:
            balance_factor = 0.0
        
        return spread_factor + depth_factor + balance_factor
    
    def calculate_optimal_quotes(self, order_book: Dict, 
                                  inventory: float = 0,
                                  target_inventory: float = 0) -> Dict:
        """
        计算最优报价（考虑库存调整）
        
        Args:
            order_book: 订单簿
            inventory: 当前库存（正=多头，负=空头）
            target_inventory: 目标库存
            
        Returns:
            {'bid': price, 'ask': price, 'sizes': {'bid': size, 'ask': size}}
        """
        signal = self.analyze_order_book(order_book)
        if not signal:
            return {}
        
        # 库存调整因子
        inventory_skew = (inventory - target_inventory) / self.max_position
        inventory_skew = max(-1, min(1, inventory_skew))  # 限制在 -1 到 1
        
        # 根据库存调整报价
        # 库存过多：降低卖价，提高买价（鼓励卖出）
        # 库存过少：提高卖价，降低买价（鼓励买入）
        adjustment = inventory_skew * 0.002  # 0.2% 调整
        
        adjusted_bid = signal.suggested_bid - adjustment
        adjusted_ask = signal.suggested_ask - adjustment
        
        # 计算订单大小
        base_size = min(self.max_position * 0.1, 1000)
        
        # 根据库存调整大小
        if inventory_skew > 0.5:  # 库存过多，多卖
            bid_size = base_size * 0.5
            ask_size = base_size * 1.5
        elif inventory_skew < -0.5:  # 库存过少，多买
            bid_size = base_size * 1.5
            ask_size = base_size * 0.5
        else:
            bid_size = base_size
            ask_size = base_size
        
        return {
            'bid': round(adjusted_bid, 4),
            'ask': round(adjusted_ask, 4),
            'sizes': {
                'bid': round(bid_size, 2),
                'ask': round(ask_size, 2)
            },
            'expected_profit': signal.expected_profit,
            'confidence': signal.confidence
        }
    
    def estimate_market_impact(self, order_book: Dict, 
                               trade_size: float,
                               side: str) -> Dict:
        """
        估算交易对市场的影响（滑点）
        
        Args:
            order_book: 订单簿
            trade_size: 交易数量
            side: 'buy' 或 'sell'
            
        Returns:
            {'vwap': 成交均价, 'slippage': 滑点%, 'remaining': 未成交数量}
        """
        levels = order_book.get('asks', []) if side == 'buy' else order_book.get('bids', [])
        
        if not levels:
            return {'vwap': 0, 'slippage': 0, 'remaining': trade_size}
        
        total_cost = 0
        total_fill = 0
        remaining = trade_size
        
        for price, size in levels:
            if remaining <= 0:
                break
            
            fill = min(size, remaining)
            total_cost += price * fill
            total_fill += fill
            remaining -= fill
        
        vwap = total_cost / total_fill if total_fill > 0 else 0
        
        # 计算滑点
        best_price = levels[0][0]
        slippage = abs(vwap - best_price) / best_price * 100 if best_price > 0 else 0
        
        return {
            'vwap': round(vwap, 4),
            'slippage': round(slippage, 2),
            'remaining': round(remaining, 2),
            'fill_rate': round(total_fill / trade_size * 100, 1) if trade_size > 0 else 0
        }


def format_market_making_signal(signal: MarketMakingSignal) -> str:
    """格式化做市信号"""
    return f"""
💹 *做市机会*

📊 {signal.market}

💰 当前价差:
   买价: ${signal.bid_price:.3f}
   卖价: ${signal.ask_price:.3f}
   价差: {signal.spread_pct:.2%}

📈 建议挂单:
   买入: ${signal.suggested_bid:.3f}
   卖出: ${signal.suggested_ask:.3f}

💵 预期利润: {signal.expected_profit:.2%}
✅ 置信度: {signal.confidence:.1%}

💡 策略: 同时挂买单和卖单，赚取价差
"""


if __name__ == "__main__":
    # 测试
    mm = MarketMaker(min_spread_pct=0.01)
    
    # 模拟订单簿
    test_book = {
        'market': 'BTC_100k_2026',
        'bids': [[0.62, 5000], [0.61, 8000], [0.60, 10000]],
        'asks': [[0.63, 3000], [0.64, 6000], [0.65, 9000]]
    }
    
    signal = mm.analyze_order_book(test_book)
    if signal:
        print(format_market_making_signal(signal))
        
        # 测试最优报价
        quotes = mm.calculate_optimal_quotes(test_book, inventory=500, target_inventory=0)
        print(f"\n最优报价: {json.dumps(quotes, indent=2)}")
        
        # 测试市场影响
        impact = mm.estimate_market_impact(test_book, trade_size=5000, side='buy')
        print(f"\n市场影响: {json.dumps(impact, indent=2)}")
    else:
        print("无做市机会")
