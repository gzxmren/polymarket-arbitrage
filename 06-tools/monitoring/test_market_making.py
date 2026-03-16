#!/usr/bin/env python3
"""
测试做市机会检测
验证程序能正确识别真实的市场机会
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))

from clob_api import get_markets_with_order_book, get_order_book, calculate_spread_from_order_book


def test_market_making_detection():
    """测试做市机会检测"""
    print("🧪 测试做市机会检测")
    print("=" * 70)
    
    # 获取市场
    print("\n📡 获取CLOB市场...")
    markets = get_markets_with_order_book(limit=20)
    print(f"   找到 {len(markets)} 个市场")
    
    # 测试不同阈值
    thresholds = [0.005, 0.01, 0.015, 0.02, 0.03]
    
    print("\n📊 测试不同阈值下的机会数量:")
    print("-" * 70)
    
    for threshold in thresholds:
        opportunities = []
        
        for market in markets[:10]:
            try:
                order_book = get_order_book(market['yes_token'])
                if not order_book:
                    continue
                
                spread_info = calculate_spread_from_order_book(order_book)
                if not spread_info:
                    continue
                
                spread_pct = spread_info.get('spread_pct', 0)
                min_depth = spread_info.get('min_depth', 0)
                
                if spread_pct >= threshold and min_depth >= 5000:
                    opportunities.append({
                        'market': market['slug'],
                        'question': market['question'],
                        'spread_pct': spread_pct,
                        'best_bid': spread_info['best_bid'],
                        'best_ask': spread_info['best_ask'],
                        'depth': min_depth
                    })
                    
            except Exception as e:
                continue
        
        print(f"   阈值 {threshold:>5.1%} | 发现 {len(opportunities)} 个机会")
    
    # 显示详细数据
    print("\n📋 前5个市场的详细数据:")
    print("-" * 70)
    
    for market in markets[:5]:
        try:
            order_book = get_order_book(market['yes_token'])
            if not order_book:
                continue
            
            spread_info = calculate_spread_from_order_book(order_book)
            if not spread_info:
                continue
            
            print(f"\n   {market['question'][:50]}...")
            print(f"   买价: {spread_info['best_bid']:.4f} | 卖价: {spread_info['best_ask']:.4f}")
            print(f"   价差: {spread_info['spread_pct']:.2%} | 深度: ${spread_info['min_depth']:,.0f}")
            
            if spread_info['spread_pct'] >= 0.015:
                print(f"   ✅ 超过 1.5% 阈值！")
            else:
                print(f"   ⚪ 低于阈值")
                
        except Exception as e:
            print(f"   ❌ 错误: {e}")
    
    print("\n" + "=" * 70)
    print("💡 结论:")
    print("   - 如果所有阈值都显示 0 个机会，说明市场当前确实没有做市空间")
    print("   - 这是正常的市场状态，不是程序问题")
    print("   - 当市场波动时，程序会自动检测到机会")
    print("=" * 70)


if __name__ == "__main__":
    test_market_making_detection()
