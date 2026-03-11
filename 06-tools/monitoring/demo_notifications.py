#!/usr/bin/env python3
"""
通知演示
展示各种通知的样式
"""

import json
import sys
sys.path.insert(0, '.')

from telegram_notifier_v2 import (
    format_pair_cost_alert,
    format_whale_alert,
    format_cross_market_alert,
    format_summary_report
)


def demo_pair_cost():
    """演示 Pair Cost 套利通知"""
    print("="*70)
    print("🎯 Pair Cost 套利通知示例")
    print("="*70)
    
    opportunity = {
        "question": "Will Bitcoin reach $100,000 by end of 2026?",
        "slug": "will-bitcoin-reach-100000-by-end-of-2026",
        "yes_price": 0.52,
        "no_price": 0.47,
        "pair_cost": 0.99,
        "profit_margin": 0.01,
        "profit_pct": 1.0,
        "liquidity": 150000,
        "volume": 500000,
        "end_date": "2026-12-31T23:59:59Z"
    }
    
    message = format_pair_cost_alert(opportunity)
    print(message)
    print()


def demo_whale():
    """演示鲸鱼活动通知"""
    print("="*70)
    print("🐋 鲸鱼活动通知示例")
    print("="*70)
    
    analysis = {
        "info": {
            "pseudonym": "SmartMoney-Whale",
            "wallet": "0x1234567890abcdef1234567890abcdef12345678",
            "total_volume": 50000,
            "win_rate": 0.68
        },
        "position_count": 15,
        "total_value": 250000,
        "changes": [
            {
                "type": "new",
                "market": "Will Ethereum reach $5,000 by end of 2026?",
                "outcome": "Yes",
                "size": 5000,
                "avg_price": 0.35
            },
            {
                "type": "increased",
                "market": "Will Bitcoin reach $100,000 by end of 2026?",
                "outcome": "Yes",
                "change": 3000,
                "old_size": 5000,
                "new_size": 8000
            }
        ],
        "positions": [
            {"market": "BTC $100k 2026", "title": "Will Bitcoin reach $100,000 by end of 2026?", "outcome": "Yes", "size": 8000, "curPrice": 0.52, "currentPrice": 0.52, "pnl": 960},
            {"market": "ETH $5k 2026", "title": "Will Ethereum reach $5,000 by end of 2026?", "outcome": "Yes", "size": 5000, "curPrice": 0.35, "currentPrice": 0.35, "pnl": 0},
        ]
    }
    
    message = format_whale_alert(analysis)
    print(message)
    print()


def demo_cross_market():
    """演示跨平台套利通知"""
    print("="*70)
    print("💎 跨平台套利通知示例")
    print("="*70)
    
    opp = {
        "question": "Will Trump win the 2024 US Presidential Election?",
        "similarity": 0.85,
        "gap": 0.08,
        "gap_pct": 8.0,
        "low_platform": "polymarket",
        "high_platform": "manifold",
        "suggested_action": "Buy Polymarket, Sell Manifold",
        "prices": {
            "polymarket": 0.52,
            "manifold": 0.60,
            "metaculus": 0.55
        },
        "polymarket": {
            "url": "https://polymarket.com/event/will-trump-win-2024"
        },
        "manifold": {
            "url": "https://manifold.markets/user/will-trump-win-2024"
        },
        "metaculus": {
            "url": "https://www.metaculus.com/questions/12345"
        }
    }
    
    message = format_cross_market_alert(opp)
    print(message)
    print()


def demo_summary():
    """演示每日汇总通知"""
    print("="*70)
    print("📊 每日汇总通知示例")
    print("="*70)
    
    stats = {
        "scan_time": "2026-03-12 08:00",
        "pair_cost_count": 2,
        "cross_market_count": 1,
        "active_whales": 3,
        "markets_scanned": 200,
        "avg_pair_cost": 0.9998,
        "total_opportunities": 3
    }
    
    message = format_summary_report(stats)
    print(message)
    print()


def main():
    print("🚀 Telegram 通知样式演示\n")
    
    demo_pair_cost()
    demo_whale()
    demo_cross_market()
    demo_summary()
    
    print("="*70)
    print("✅ 演示完成")
    print("="*70)
    print("\n这些通知将通过 Telegram 发送给你")
    print("配置方法: 复制 .env.example 到 .env 并填入你的 Bot Token 和 Chat ID")


if __name__ == "__main__":
    main()
