#!/usr/bin/env python3
"""
Telegram 通知器 V2
优化版：清晰、明确、美观
"""

import json
import os
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime

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
    """格式化 Pair Cost 套利提醒 - 清晰版"""
    
    # 计算建议仓位
    max_size = min(opportunity['liquidity'] * 0.1, 5000)  # 最多用10%流动性或$5000
    expected_profit = max_size * opportunity['profit_margin']
    
    return f"""🎯 *套利机会 detected*

*{opportunity['question'][:80]}*

┌─────────────────────────────┐
│  💰 利润空间: *{opportunity['profit_pct']:.2f}%*              │
│  📊 Pair Cost: ${opportunity['pair_cost']:.4f}              │
├─────────────────────────────┤
│  YES: ${opportunity['yes_price']:.3f}                      │
│  NO:  ${opportunity['no_price']:.3f}                      │
├─────────────────────────────┤
│  💧 流动性: ${opportunity['liquidity']:,.0f}              │
│  📈 24h交易量: ${opportunity['volume']:,.0f}              │
└─────────────────────────────┘

💡 *操作建议:*
   同时买入 YES + NO
   建议金额: ${max_size:,.0f}
   预期利润: ${expected_profit:.2f}

⏰ 结算时间: {opportunity.get('end_date', '未知')[:10]}

🔗 [点击打开 Polymarket](https://polymarket.com/event/{opportunity['slug']})

⚠️ *风险提示:*
   • 价格可能快速变化
   • 结算前资金锁定
   • 建议尽快执行
"""


def format_whale_alert(analysis: dict) -> str:
    """格式化鲸鱼活动提醒 - 清晰版"""
    w = analysis['info']
    
    # 判断信号强度
    total_changes = len(analysis['changes'])
    if total_changes >= 5:
        signal_strength = "🔴 极强"
    elif total_changes >= 3:
        signal_strength = "🟠 强"
    elif total_changes >= 1:
        signal_strength = "🟡 中等"
    else:
        signal_strength = "⚪ 弱"
    
    message = f"""🐋 *鲸鱼活动警报*

*交易者:* {w['pseudonym']}
`{w['wallet'][:8]}...{w['wallet'][-6:]}`

┌─────────────────────────────┐
│  📊 24h交易量: ${w['total_volume']:,.0f}              │
│  💼 当前持仓: {analysis['position_count']} 个市场          │
│  💰 持仓价值: ${analysis['total_value']:,.0f}              │
│  📈 信号强度: {signal_strength}              │
└─────────────────────────────┘
"""
    
    # 添加变动详情
    if analysis['changes']:
        message += "\n📋 *最新变动:*\n"
        
        for i, change in enumerate(analysis['changes'][:5], 1):
            market_short = change['market'][:35] + "..." if len(change['market']) > 35 else change['market']
            
            if change['type'] == 'new':
                message += f"""
{i}. 🟢 *新建仓*
   市场: {market_short}
   方向: {change['outcome']} | 数量: {change['size']:.1f}
   成本: ${change['avg_price']:.3f}
"""
            elif change['type'] == 'increased':
                message += f"""
{i}. 📈 *加仓*
   市场: {market_short}
   增加: +{change['change']:.1f} (现持仓: {change['new_size']:.1f})
"""
            elif change['type'] == 'decreased':
                message += f"""
{i}. 📉 *减仓*
   市场: {market_short}
   减少: {change['change']:.1f} (现持仓: {change['new_size']:.1f})
"""
            elif change['type'] == 'closed':
                message += f"""
{i}. 🔴 *清仓*
   市场: {market_short}
   原持仓: {change['previous_size']:.1f}
"""
    
    # 添加主要持仓
    if analysis['positions']:
        message += "\n💼 *主要持仓:*\n"
        sorted_pos = sorted(
            analysis['positions'],
            key=lambda p: float(p.get('size', 0)) * float(p.get('curPrice', p.get('currentPrice', 0))),
            reverse=True
        )[:3]
        
        for pos in sorted_pos:
            market = pos.get('market', pos.get('title', 'Unknown'))[:30]
            outcome = pos.get('outcome', '?')
            size = float(pos.get('size', 0))
            pnl = float(pos.get('pnl', 0))
            pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"
            message += f"   • {market}... | {outcome} | {pnl_emoji} ${pnl:+.0f}\n"
    
    message += """
💡 *解读:*
   该交易者近期活跃，建议关注其持仓方向
   可作为市场情绪参考，但需独立判断
"""
    
    return message


def format_cross_market_alert(opp: dict) -> str:
    """格式化跨平台套利提醒 - 清晰版"""
    
    # 判断匹配质量
    if opp['similarity'] >= 0.7:
        match_quality = "✅ 高匹配"
    elif opp['similarity'] >= 0.5:
        match_quality = "⚠️ 中匹配（建议人工确认）"
    else:
        match_quality = "❌ 低匹配（可能非同事件）"
    
    message = f"""💎 *跨平台套利机会*

*{opp['question'][:80]}*

┌─────────────────────────────┐
│  📊 匹配度: {match_quality}              │
│  📈 价差: *{opp['gap']:.1%}* ({opp['gap_pct']:.1f}个百分点)              │
├─────────────────────────────┤
│  平台价格对比:              │
"""
    
    for platform, price in opp['prices'].items():
        platform_emoji = {"polymarket": "🟣", "manifold": "🔵", "metaculus": "🟢"}.get(platform, "⚪")
        message += f"│  {platform_emoji} {platform.capitalize():12} {price:>6.1%}              │\n"
    
    message += f"""└─────────────────────────────┘

💡 *操作建议:*
   在 *{opp['low_platform'].capitalize()}* 买入 YES
   在 *{opp['high_platform'].capitalize()}* 卖出/对冲
   
   预期收益: {opp['gap']:.1%} (扣除费用后约 {(opp['gap'] - 0.04):.1%})

🔗 链接:
   • [Polymarket]({opp['polymarket']['url']})
"""
    
    if opp.get('manifold'):
        message += f"   • [Manifold]({opp['manifold']['url']})\n"
    if opp.get('metaculus'):
        message += f"   • [Metaculus]({opp['metaculus']['url']})\n"
    
    if opp['similarity'] < 0.5:
        message += """
⚠️ *重要警告:*
   事件匹配度较低，可能不是同一事件！
   请务必人工确认后再操作
"""
    
    return message


def format_summary_report(stats: dict) -> str:
    """格式化每日汇总报告 - 清晰版"""
    
    # 判断市场状态
    total_opp = stats.get('total_opportunities', 0)
    if total_opp >= 5:
        market_status = "🔥 机会较多"
    elif total_opp >= 1:
        market_status = "✅ 有机会"
    else:
        market_status = "⚪ 市场平稳"
    
    message = f"""📊 *Polymarket 监控日报*

⏰ 扫描时间: {stats.get('scan_time', 'N/A')}
📈 市场状态: {market_status}

┌─────────────────────────────┐
│  📋 扫描统计                 │
├─────────────────────────────┤
│  🎯 Pair Cost 机会: {stats.get('pair_cost_count', 0):>3} 个              │
│  💎 跨平台套利: {stats.get('cross_market_count', 0):>3} 个              │
│  🐋 活跃鲸鱼: {stats.get('active_whales', 0):>3} 位              │
├─────────────────────────────┤
│  📊 市场数据                 │
│  • 扫描市场: {stats.get('markets_scanned', 0)} 个              │
│  • 平均 Pair Cost: ${stats.get('avg_pair_cost', 1.0):.4f}              │
└─────────────────────────────┘
"""
    
    if total_opp > 0:
        message += """
✅ *发现套利机会，请查看详细通知*
"""
    else:
        message += """
⚪ *当前市场有效，无明显套利机会*
   建议继续监控，等待时机
"""
    
    message += """
💡 *提示:*
   监控每5分钟运行一次
   有重大机会时会立即通知
"""
    
    return message


# 发送函数（兼容旧版）
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
    message = format_summary_report(stats)
    return send_telegram_message(message)


def test_notification():
    """测试通知功能"""
    print("🧪 测试 Telegram 通知...")
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ 错误: 未设置 TELEGRAM_BOT_TOKEN")
        print("   export TELEGRAM_BOT_TOKEN='your_token'")
        return False
    
    if not TELEGRAM_CHAT_ID:
        print("❌ 错误: 未设置 TELEGRAM_CHAT_ID")
        print("   export TELEGRAM_CHAT_ID='your_chat_id'")
        return False
    
    test_message = """🧪 *Polymarket 监控测试*

✅ Telegram 通知已配置成功！

📊 监控内容:
• 🎯 Pair Cost 套利机会
• 💎 跨平台套利机会  
• 🐋 鲸鱼活动警报

⏰ 测试时间: 现在

💡 你将收到以下类型的通知:
1. 即时套利机会提醒
2. 鲸鱼活动警报
3. 每日汇总报告
"""
    
    if send_telegram_message(test_message):
        print("✅ 测试消息发送成功！")
        return True
    else:
        print("❌ 测试消息发送失败")
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Telegram Notifier V2")
    parser.add_argument("--test", action="store_true", help="测试通知")
    
    args = parser.parse_args()
    
    if args.test:
        test_notification()
    else:
        print("Usage: python telegram_notifier_v2.py --test")
