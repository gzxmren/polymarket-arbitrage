#!/usr/bin/env python3
"""
发送鲸鱼持仓新闻报告
可以手动运行或加入定时任务
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
sys.path.insert(0, str(Path(__file__).parent))

from whale_news_connector import WhaleNewsConnector
from telegram_notifier_v2 import send_telegram_message

# 数据目录
from config import DATA_DIR
WHALE_STATES_DIR = DATA_DIR / "whale_states"


def load_whale_data(wallet: str) -> dict:
    """加载鲸鱼数据"""
    state_file = WHALE_STATES_DIR / f"{wallet}.json"
    
    if not state_file.exists():
        return None
    
    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
        
        # 提取持仓信息
        positions_data = state.get('positions', {})
        positions = []
        total_value = 0
        
        for market, pos in positions_data.items():
            size = float(pos.get('size', 0))
            price = float(pos.get('curPrice', pos.get('currentPrice', 0)))
            value = size * price
            
            positions.append({
                'market': market,
                'outcome': pos.get('outcome', '?'),
                'size': size,
                'avg_price': float(pos.get('avgPrice', 0)),
                'cur_price': price,
                'value': value,
                'pnl': float(pos.get('cashPnl', 0))
            })
            total_value += value
        
        return {
            'wallet': wallet,
            'pseudonym': state.get('pseudonym', wallet[:10] + '...'),
            'total_value': total_value,
            'positions': positions,
            'last_check': state.get('last_check', datetime.now(timezone.utc).isoformat())
        }
    
    except Exception as e:
        print(f"❌ 加载鲸鱼数据失败: {e}")
        return None


def send_whale_news_report(wallet: str, send_to_telegram: bool = True) -> str:
    """
    生成并发送鲸鱼持仓新闻报告
    
    Args:
        wallet: 鲸鱼钱包地址
        send_to_telegram: 是否发送到Telegram
    
    Returns:
        生成的报告文本
    """
    print(f"🐋 生成鲸鱼 {wallet[:10]}... 的持仓新闻报告...")
    
    # 加载鲸鱼数据
    whale_data = load_whale_data(wallet)
    if not whale_data:
        print(f"❌ 无法加载鲸鱼数据: {wallet}")
        return None
    
    print(f"   找到 {len(whale_data['positions'])} 个持仓")
    
    # 生成报告
    connector = WhaleNewsConnector()
    report = connector.generate_whale_news_report(
        whale_data,
        whale_data['positions']
    )
    
    # 发送到Telegram
    if send_to_telegram:
        print("   发送到Telegram...")
        success = send_telegram_message(report)
        if success:
            print("   ✅ 发送成功")
        else:
            print("   ❌ 发送失败")
    
    return report


def send_top_whales_news(limit: int = 3, send_to_telegram: bool = True):
    """
    发送Top N鲸鱼的新闻报告
    """
    print(f"🐋 生成Top {limit}鲸鱼持仓新闻报告...")
    
    # 加载所有鲸鱼
    whales = []
    if WHALE_STATES_DIR.exists():
        for state_file in WHALE_STATES_DIR.glob('*.json'):
            wallet = state_file.stem
            whale_data = load_whale_data(wallet)
            if whale_data and whale_data['total_value'] > 10000:  # 只关注>$10k的
                whales.append(whale_data)
    
    # 按持仓价值排序
    whales.sort(key=lambda x: x['total_value'], reverse=True)
    top_whales = whales[:limit]
    
    print(f"   找到 {len(top_whales)} 个重点鲸鱼")
    
    # 为每个鲸鱼生成报告
    connector = WhaleNewsConnector()
    
    for i, whale in enumerate(top_whales, 1):
        print(f"\n   {i}. {whale['pseudonym']} (${whale['total_value']:,.0f})")
        
        report = connector.generate_whale_news_report(
            whale,
            whale['positions']
        )
        
        if send_to_telegram:
            success = send_telegram_message(report)
            if success:
                print("      ✅ 发送成功")
            else:
                print("      ❌ 发送失败")
        
        # 添加间隔，避免消息过快
        import time
        time.sleep(2)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='发送鲸鱼持仓新闻报告')
    parser.add_argument('--wallet', '-w', type=str, help='指定鲸鱼钱包地址')
    parser.add_argument('--top', '-t', type=int, default=0, help='发送Top N鲸鱼报告')
    parser.add_argument('--no-send', action='store_true', help='不发送到Telegram，仅打印')
    
    args = parser.parse_args()
    
    send_to_telegram = not args.no_send
    
    if args.wallet:
        # 发送指定鲸鱼的报告
        report = send_whale_news_report(args.wallet, send_to_telegram)
        if report and args.no_send:
            print(report)
    
    elif args.top > 0:
        # 发送Top N报告
        send_top_whales_news(args.top, send_to_telegram)
    
    else:
        # 默认发送Top 3
        send_top_whales_news(3, send_to_telegram)


if __name__ == "__main__":
    main()
