#!/usr/bin/env python3
"""
VWAP 计算器
计算成交量加权平均价格
"""

import json
from typing import List, Dict, Tuple


def calculate_vwap(order_book: Dict, quantity: float = 1000) -> Tuple[float, float]:
    """
    计算 VWAP 价格
    
    Args:
        order_book: 订单簿数据 {'bids': [[price, size], ...], 'asks': [...]}
        quantity: 要成交的数量
    
    Returns:
        (buy_vwap, sell_vwap): 买入和卖出的 VWAP
    """
    
    def walk_book(orders: List, target_qty: float) -> float:
        """ walk the order book to calculate VWAP """
        total_cost = 0
        total_qty = 0
        
        for price, size in orders:
            if total_qty >= target_qty:
                break
            
            take_qty = min(size, target_qty - total_qty)
            total_cost += price * take_qty
            total_qty += take_qty
        
        return total_cost / total_qty if total_qty > 0 else 0
    
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])
    
    # 买入 VWAP（吃 ask）
    buy_vwap = walk_book(asks, quantity)
    
    # 卖出 VWAP（吃 bid）
    sell_vwap = walk_book(bids, quantity)
    
    return buy_vwap, sell_vwap


def calculate_slippage(order_book: Dict, quantity: float = 1000) -> Dict:
    """
    计算滑点
    
    Returns:
        {
            'best_bid': 最优买价,
            'best_ask': 最优卖价,
            'buy_vwap': 买入 VWAP,
            'sell_vwap': 卖出 VWAP,
            'buy_slippage': 买入滑点%,
            'sell_slippage': 卖出滑点%,
            'total_slippage': 总滑点%
        }
    """
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])
    
    if not bids or not asks:
        return {}
    
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    
    buy_vwap, sell_vwap = calculate_vwap(order_book, quantity)
    
    # 计算滑点
    buy_slippage = (buy_vwap - best_ask) / best_ask * 100 if best_ask > 0 else 0
    sell_slippage = (best_bid - sell_vwap) / best_bid * 100 if best_bid > 0 else 0
    
    # 总滑点（往返）
    total_slippage = buy_slippage + sell_slippage
    
    return {
        'best_bid': best_bid,
        'best_ask': best_ask,
        'mid_price': (best_bid + best_ask) / 2,
        'buy_vwap': buy_vwap,
        'sell_vwap': sell_vwap,
        'buy_slippage': buy_slippage,
        'sell_slippage': sell_slippage,
        'total_slippage': total_slippage,
        'quantity': quantity
    }


if __name__ == "__main__":
    # 测试
    test_book = {
        'bids': [[0.62, 100], [0.61, 200], [0.60, 300]],
        'asks': [[0.63, 50], [0.64, 100], [0.65, 200]]
    }
    
    result = calculate_slippage(test_book, quantity=1000)
    print(json.dumps(result, indent=2))
