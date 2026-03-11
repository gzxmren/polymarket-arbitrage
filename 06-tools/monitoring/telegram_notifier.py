#!/usr/bin/env python3
"""
Telegram 通知器
发送套利机会和鲸鱼活动提醒
"""

import json
import os
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote

# Telegram Bot 配置
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_telegram_message(message: str, parse_mode: str = "Markdown") -> bool:
    """发送 Telegram 消息"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False
    
    url = f"{TELEGRAM_API}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    try:
        req = Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result.get("ok", False)
    except (URLError, json.JSONDecodeError) as e:
        print(f"Error sending Telegram message: {e}", file=sys.stderr)
        return False


def format_pair_cost_alert(opportunity: dict) -> str:
    """格式化 Pair Cost 套利提醒"""
    return f"""
🎯 *Pair Cost 套利机会*

📊 {opportunity['question'][:100]}

💰 *利润空间:* {opportunity['profit_pct']:.2f}%
💵 YES: ${opportunity['yes_price']:.4f}
💵 NO: ${opportunity['no_price']:.4f}
📉 Pair Cost: ${opportunity['pair_cost']:.4f}

💧 流动性: ${opportunity['liquidity']:,.0f}
📈 交易量: ${opportunity['volume']:,.0f}

🔗 [查看市场](https://polymarket.com/event/{opportunity['slug']})
"""


def format_whale_alert(analysis: dict) -> str:
    """格式化鲸鱼活动提醒"""
    w = analysis['info']
    
    message = f"""
🐋 *鲸鱼活动警报*

👤 {w['pseudonym']}
💼 钱包: `{w['wallet'][:10]}...{w['wallet'][-6:]}`
📊 24h交易量: ${w['total_volume']:,.0f}
💰 持仓价值: ${analysis['total_value']:,.0f}
"""
    
    if analysis['changes']:
        message += "\n⚡ *最新变动:*\n"
        for change in analysis['changes'][:3]:  # 只显示前3个
            if change['type'] == 'new':
                message += f"\n🟢 新建仓: {change['market'][:50]}...\n"
                message += f"   {change['outcome']} | {change['size']:.2f} @ ${change['avg_price']:.3f}\n"
            elif change['type'] in ['increased', 'decreased']:
                emoji = '📈' if change['type'] == 'increased' else '📉'
                message += f"\n{emoji} {change['type'].upper()}: {change['market'][:50]}...\n"
                message += f"   {change['change']:+.2f}\n"
            elif change['type'] == 'closed':
                message += f"\n🔴 清仓: {change['market'][:50]}...\n"
    
    return message


def format_cross_market_alert(opp: dict) -> str:
    """格式化跨平台套利提醒"""
    message = f"""
💎 *跨平台套利机会*

📊 {opp['question'][:100]}

📈 *价差:* {opp['gap']:.1%} ({opp['gap_pct']:.1f}个百分点)

*平台价格:*
"""
    
    for platform, price in opp['prices'].items():
        message += f"• {platform.capitalize()}: {price:.1%}\n"
    
    message += f"\n💡 *建议:* {opp['suggested_action']}"
    
    if opp['similarity'] < 0.5:
        message += "\n\n⚠️ *警告:* 匹配度较低，请人工确认是同一事件！"
    
    message += f"\n\n🔗 [Polymarket]({opp['polymarket']['url']})"
    
    if opp.get('manifold'):
        message += f" | [Manifold]({opp['manifold']['url']})"
    
    return message


def send_pair_cost_alert(opportunity: dict) -> bool:
    """发送 Pair Cost 套利提醒"""
    message = format_pair_cost_alert(opportunity)
    return send_telegram_message(message)


def send_whale_alert(analysis: dict) -> bool:
    """发送鲸鱼活动提醒"""
    message = format_whale_alert(analysis)
    return send_telegram_message(message)


def send_cross_market_alert(opp: dict) -> bool:
    """发送跨平台套利提醒"""
    message = format_cross_market_alert(opp)
    return send_telegram_message(message)


def send_summary_report(stats: dict) -> bool:
    """发送每日汇总报告"""
    message = f"""
📊 *Polymarket 监控日报*

⏰ 扫描时间: {stats.get('scan_time', 'N/A')}

*扫描统计:*
• Pair Cost 机会: {stats.get('pair_cost_count', 0)} 个
• 跨平台套利: {stats.get('cross_market_count', 0)} 个
• 活跃鲸鱼: {stats.get('active_whales', 0)} 个

*市场状态:*
• 平均 Pair Cost: ${stats.get('avg_pair_cost', 1.0):.4f}
• 扫描市场数: {stats.get('markets_scanned', 0)}

{'✅ 发现机会，请查看详情' if stats.get('total_opportunities', 0) > 0 else '⚪ 市场平稳，无明显机会'}
"""
    return send_telegram_message(message)


def test_notification():
    """测试通知功能"""
    print("🧪 测试 Telegram 通知...")
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ 错误: 未设置 TELEGRAM_BOT_TOKEN")
        print("   请设置环境变量: export TELEGRAM_BOT_TOKEN='your_token'")
        return False
    
    if not TELEGRAM_CHAT_ID:
        print("❌ 错误: 未设置 TELEGRAM_CHAT_ID")
        print("   请设置环境变量: export TELEGRAM_CHAT_ID='your_chat_id'")
        return False
    
    test_message = """
🧪 *Polymarket 监控测试*

✅ Telegram 通知已配置成功！

📊 监控内容:
• Pair Cost 套利机会
• 跨平台套利机会  
• 鲸鱼活动警报

⏰ 测试时间: 现在
"""
    
    if send_telegram_message(test_message):
        print("✅ 测试消息发送成功！")
        return True
    else:
        print("❌ 测试消息发送失败")
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Telegram Notifier")
    parser.add_argument("--test", action="store_true", help="测试通知")
    parser.add_argument("--pair-cost", type=str, help="发送 Pair Cost 提醒 (JSON)")
    parser.add_argument("--whale", type=str, help="发送鲸鱼提醒 (JSON)")
    parser.add_argument("--cross-market", type=str, help="发送跨平台套利提醒 (JSON)")
    
    args = parser.parse_args()
    
    if args.test:
        test_notification()
    elif args.pair_cost:
        opp = json.loads(args.pair_cost)
        send_pair_cost_alert(opp)
    elif args.whale:
        analysis = json.loads(args.whale)
        send_whale_alert(analysis)
    elif args.cross_market:
        opp = json.loads(args.cross_market)
        send_cross_market_alert(opp)
    else:
        print("Usage:")
        print("  python telegram_notifier.py --test")
        print("  python telegram_notifier.py --pair-cost '{...}'")
        print("  python telegram_notifier.py --whale '{...}'")
