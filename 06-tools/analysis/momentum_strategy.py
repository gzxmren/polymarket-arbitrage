#!/usr/bin/env python3
"""
动量策略
检测价格突破和趋势
"""

import json
from typing import List, Dict
from datetime import datetime, timedelta


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """计算 RSI"""
    if len(prices) < period + 1:
        return 50
    
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_ma(prices: List[float], period: int) -> float:
    """计算移动平均线"""
    if len(prices) < period:
        return prices[-1] if prices else 0
    return sum(prices[-period:]) / period


def detect_breakout(price_history: List[Dict]) -> Dict:
    """
    检测价格突破
    
    Args:
        price_history: [{'timestamp': ..., 'price': ...}, ...]
    
    Returns:
        {
            'signal': 'BUY'/'SELL'/'HOLD',
            'strength': 信号强度 (0-1),
            'rsi': RSI值,
            'ma5': 5周期均线,
            'ma20': 20周期均线,
            'reason': 原因
        }
    """
    if len(price_history) < 20:
        return {'signal': 'HOLD', 'reason': 'Insufficient data'}
    
    prices = [p['price'] for p in price_history]
    current_price = prices[-1]
    
    # 计算指标
    rsi = calculate_rsi(prices)
    ma5 = calculate_ma(prices, 5)
    ma20 = calculate_ma(prices, 20)
    
    # 突破检测
    signal = 'HOLD'
    strength = 0
    reason = []
    
    # RSI 信号
    if rsi < 30:
        signal = 'BUY'
        strength += 0.3
        reason.append(f'RSI oversold ({rsi:.1f})')
    elif rsi > 70:
        signal = 'SELL'
        strength += 0.3
        reason.append(f'RSI overbought ({rsi:.1f})')
    
    # 均线交叉
    if ma5 > ma20 and prices[-2] <= calculate_ma(prices[:-1], 20):
        signal = 'BUY'
        strength += 0.4
        reason.append('Golden cross (MA5 > MA20)')
    elif ma5 < ma20 and prices[-2] >= calculate_ma(prices[:-1], 20):
        signal = 'SELL'
        strength += 0.4
        reason.append('Death cross (MA5 < MA20)')
    
    # 价格突破近期区间
    recent_high = max(prices[-20:])
    recent_low = min(prices[-20:])
    
    if current_price > recent_high * 0.99:
        signal = 'BUY'
        strength += 0.3
        reason.append('Breakout above recent high')
    elif current_price < recent_low * 1.01:
        signal = 'SELL'
        strength += 0.3
        reason.append('Breakdown below recent low')
    
    return {
        'signal': signal,
        'strength': min(strength, 1.0),
        'rsi': rsi,
        'ma5': ma5,
        'ma20': ma20,
        'current_price': current_price,
        'reason': '; '.join(reason) if reason else 'No clear signal'
    }


def scan_momentum_opportunities(markets: List[Dict]) -> List[Dict]:
    """
    扫描动量机会
    """
    opportunities = []
    
    for market in markets:
        price_history = market.get('price_history', [])
        if len(price_history) < 20:
            continue
        
        signal = detect_breakout(price_history)
        
        if signal['signal'] != 'HOLD' and signal['strength'] > 0.5:
            opportunities.append({
                'market': market.get('question', 'Unknown'),
                'signal': signal['signal'],
                'strength': signal['strength'],
                'current_price': signal['current_price'],
                'rsi': signal['rsi'],
                'reason': signal['reason']
            })
    
    return opportunities


if __name__ == "__main__":
    # 测试
    test_prices = [
        {'timestamp': i, 'price': 0.5 + 0.01 * i} for i in range(25)
    ]
    # 添加一些波动
    test_prices[-1]['price'] = 0.8  # 突破
    
    result = detect_breakout(test_prices)
    print(json.dumps(result, indent=2))
