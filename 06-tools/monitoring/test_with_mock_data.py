#!/usr/bin/env python3
"""
使用模拟高价差数据测试通知功能
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))

import os
os.environ['MARKET_MAKING_NOTIFY'] = 'true'
os.environ['TELEGRAM_BOT_TOKEN'] = '8693703622:AAGtlESUqoc4qH7qusEbOlxX8X-mlj2gwyw'
os.environ['TELEGRAM_CHAT_ID'] = '-5052636342'

from telegram_notifier_v2 import send_telegram_message, format_real_market_making_signal

# 创建模拟高价差机会
mock_opportunity = {
    'market': 'test-market-2026',
    'question': 'Test Market: Will this message be sent successfully?',
    'spread_pct': 0.025,  # 2.5% 价差
    'best_bid': 0.450,
    'best_ask': 0.475,
    'bid_depth': 15000,
    'ask_depth': 12000,
    'min_depth': 12000,  # 用于计算建议仓位
    'liquidity': 50000,
    'token_id': 'test_token_123',
    '_data_source': 'simulated'  # 标记为模拟数据
}

print("🧪 测试做市机会通知")
print("=" * 70)
print("\n📤 发送测试消息到菜园子...")

message = format_real_market_making_signal(mock_opportunity)
print("\n消息内容:")
print("-" * 70)
print(message)
print("-" * 70)

# 发送
result = send_telegram_message(message)
if result:
    print("\n✅ 测试消息发送成功！")
    print("   请检查菜园子是否收到消息")
else:
    print("\n❌ 发送失败")
    print("   检查 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID")
