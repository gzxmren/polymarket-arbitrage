#!/usr/bin/env python3
"""
Telegram 通知器 V2
优化版：清晰、明确、美观

[修复] 2025-03-25:
1. 添加发送重试机制（最多3次），网络错误时自动重试
"""

import json
import os
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from datetime import datetime

# Telegram Bot 配置
import json

# 尝试从配置文件读取
def load_telegram_config():
    """从 openclaw.json 加载配置。

    [修复 2026-06-08] OpenClaw 新版把 token 移到了 channels.telegram.accounts.default.botToken，
    旧代码只读 channels.telegram.botToken 导致独立脚本（monitor-v2 cron 直跑、看门狗等）
    取不到 token、Telegram 静默失败。这里改为优先读新路径，回退旧路径。
    """
    try:
        config_path = os.environ.get("OPENCLAW_CONFIG") or os.path.expanduser("~/.openclaw/openclaw.json")
        with open(config_path) as f:
            config = json.load(f)
            telegram = config.get("channels", {}).get("telegram", {})
            # 新结构: accounts.<defaultAccount>.botToken
            default_acct = telegram.get("defaultAccount", "default")
            acct = telegram.get("accounts", {}).get(default_acct, {}) \
                or telegram.get("accounts", {}).get("default", {})
            token = acct.get("botToken", "") or telegram.get("botToken", "")  # 新 → 旧回退
            # 默认投递群：优先显式 chatId，否则取 groups 里第一个具体群（不硬编码 ID）
            groups = [g for g in telegram.get("groups", {}) if g and g != "*"]
            group_default = groups[0] if groups else ""
            chat_id = (acct.get("chatId", "") or acct.get("defaultChatId", "")
                       or telegram.get("chatId", "") or telegram.get("defaultChatId", "")
                       or group_default)
            return token, chat_id
    except Exception:
        return "", ""


def get_admin_chat_id() -> str:
    """管理员私聊 ID（运维/失败告警目标）。从 openclaw.json 的 telegram.allowFrom 读，
    不在代码里硬编码。无则返回空串。"""
    try:
        config_path = os.environ.get("OPENCLAW_CONFIG") or os.path.expanduser("~/.openclaw/openclaw.json")
        with open(config_path) as f:
            telegram = json.load(f).get("channels", {}).get("telegram", {})
        allow = telegram.get("allowFrom") or telegram.get("groupAllowFrom") or []
        return str(allow[0]) if allow else ""
    except Exception:
        return ""

# 优先环境变量，其次配置文件
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or load_telegram_config()[0]
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or load_telegram_config()[1]

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_telegram_message(message: str, parse_mode: str = "Markdown", max_retries: int = 2) -> bool:
    """
    发送 Telegram 消息
    
    [修复] 2025-03-25: 添加重试机制，网络错误时自动重试（最多3次）
    [修复] 2025-04-05: 添加消息长度检查和Markdown失败回退，解决400错误
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False
    
    # [修复] 2025-04-05: 消息长度检查 - Telegram限制4096字符
    MAX_LENGTH = 4000  # 留余量
    if len(message) > MAX_LENGTH:
        print(f"⚠️ 消息过长({len(message)}字符)，截断至{MAX_LENGTH}字符", file=sys.stderr)
        message = message[:MAX_LENGTH-50] + "\n\n... (消息已截断，内容过长)"
    
    # [修复] 2025-04-05: 尝试发送（带Markdown失败回退）
    # 先尝试指定parse_mode，失败则尝试纯文本
    for parse in [parse_mode, ""]:
        for attempt in range(max_retries):
            try:
                url = f"{TELEGRAM_API}/sendMessage"
                data = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": parse,
                    "disable_web_page_preview": True
                }
                
                req = Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode())
                    if result.get("ok", False):
                        return True
                    else:
                        error_desc = result.get('description', 'Unknown error')
                        # Markdown解析错误，跳出重试循环，换纯文本模式
                        # 错误示例: "can't parse entities: Can't find end of the entity starting at byte offset 183"
                        if parse and ("can't parse" in error_desc.lower() or "parse entities" in error_desc.lower()):
                            print(f"⚠️ Markdown解析失败，尝试纯文本模式...", file=sys.stderr)
                            break  # 跳出重试循环，进入下一个parse模式
                        print(f"Telegram API error: {error_desc}", file=sys.stderr)
                        return False
                        
            except HTTPError as e:
                # HTTP错误（如400 Bad Request），尝试读取错误详情
                try:
                    error_body = e.read().decode()
                    error_json = json.loads(error_body)
                    error_desc = error_json.get('description', str(e))
                except:
                    error_desc = str(e)
                
                # Markdown解析错误，跳出重试循环，换纯文本模式
                # 错误示例: "can't parse entities: Can't find end of the entity starting at byte offset 183"
                if parse and ("can't parse" in error_desc.lower() or "parse entities" in error_desc.lower()):
                    print(f"⚠️ Markdown解析失败，尝试纯文本模式...", file=sys.stderr)
                    break  # 跳出重试循环，进入下一个parse模式
                
                # 其他HTTP错误，按原逻辑重试
                if attempt < max_retries - 1:
                    import time
                    print(f"Error sending Telegram message (attempt {attempt + 1}/{max_retries}): {error_desc}, retrying...", file=sys.stderr)
                    time.sleep(1)
                else:
                    print(f"Error sending Telegram message after {max_retries} attempts: {error_desc}", file=sys.stderr)
                    return False
                
            except (URLError, json.JSONDecodeError) as e:
                if attempt < max_retries - 1:
                    # 网络错误，等待1秒后重试
                    import time
                    print(f"Error sending Telegram message (attempt {attempt + 1}/{max_retries}): {e}, retrying...", file=sys.stderr)
                    time.sleep(1)
                else:
                    # 最后一次尝试失败
                    print(f"Error sending Telegram message after {max_retries} attempts: {e}", file=sys.stderr)
                    return False
    
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


def check_market_status(end_date_str: str) -> tuple:
    """
    检查市场状态
    返回: (状态emoji, 状态描述, 是否过期)
    """
    if not end_date_str:
        return "", "", False
    
    try:
        from datetime import datetime, timezone
        # 解析日期
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        
        days_to_end = (end_date - now).days
        
        if days_to_end < 0:
            # 已过期
            days_overdue = abs(days_to_end)
            if days_overdue <= 1:
                return "⏰", "刚刚过期，等待结算", True
            elif days_overdue <= 7:
                return "⚠️", f"已过期 {days_overdue} 天，等待结算", True
            else:
                return "🚨", f"已过期 {days_overdue} 天，可能异常", True
        elif days_to_end <= 1:
            return "🔥", "即将到期", False
        elif days_to_end <= 3:
            return "⏳", f"{days_to_end} 天后到期", False
        else:
            return "", "", False
    except:
        return "", "", False


def format_whale_alert(analysis: dict, is_watched: bool = False, is_new_watched: bool = False) -> str:
    """格式化鲸鱼活动提醒 - 清晰版（添加市场状态检查和重点标记）"""
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
    
    # 重点鲸鱼标记
    watch_badge = ""
    if is_new_watched:
        watch_badge = "🔔 *[新重点关注]* "
    elif is_watched:
        watch_badge = "🔔 *[重点关注]* "
    
    message = f"""🐋 *鲸鱼活动警报* {watch_badge}

*交易者:* {w['pseudonym']}
`{w['wallet'][:8]}...{w['wallet'][-6:]}'`

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
            
            # 检查市场状态
            end_date = change.get('end_date', '')
            status_emoji, status_desc, is_expired = check_market_status(end_date)
            status_warning = f"\n   {status_emoji} *{status_desc}*" if status_emoji else ""
            
            if change['type'] == 'new':
                message += f"""
{i}. 🟢 *新建仓*
   市场: {market_short}{status_warning}
   方向: {change['outcome']} | 数量: {change['size']:.1f}
   成本: ${change['avg_price']:.3f}
"""
            elif change['type'] == 'increased':
                message += f"""
{i}. 📈 *加仓*
   市场: {market_short}{status_warning}
   增加: +{change['change']:.1f} (现持仓: {change['new_size']:.1f})
"""
            elif change['type'] == 'decreased':
                message += f"""
{i}. 📉 *减仓*
   市场: {market_short}{status_warning}
   减少: {change['change']:.1f} (现持仓: {change['new_size']:.1f})
"""
            elif change['type'] == 'closed':
                message += f"""
{i}. 🔴 *清仓*
   市场: {market_short}{status_warning}
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
            # 修复：使用正确的字段名 cashPnl（API返回的是cashPnl而不是pnl）
            pnl = float(pos.get('cashPnl', pos.get('pnl', 0)))
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


def send_whale_alert(analysis: dict, is_watched: bool = False, is_new_watched: bool = False) -> bool:
    """发送鲸鱼活动提醒（支持重点标记）"""
    message = format_whale_alert(analysis, is_watched=is_watched, is_new_watched=is_new_watched)
    return send_telegram_message(message)


def send_cross_market_alert(opp: dict) -> bool:
    """发送跨平台套利提醒"""
    message = format_cross_market_alert(opp)
    return send_telegram_message(message)


def send_summary_report(stats: dict) -> bool:
    """发送每日汇总报告"""
    message = format_summary_report(stats)
    return send_telegram_message(message)


def format_top_whales_message(analyses: list) -> str:
    """格式化 Top 鲸鱼排行榜为 Telegram 消息"""
    if not analyses:
        return "📊 *Top 鲸鱼排行榜*\n\n⚪ 当前无活跃鲸鱼数据"
    
    separator = "=" * 50
    message = f"""🏆 *Top {len(analyses)} 鲸鱼排行榜*

_按持仓价值排序 | 实时数据_

```
{separator}
"""
    
    for i, analysis in enumerate(analyses, 1):
        w = analysis['info']
        
        # 排名奖牌
        rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i:2d}.")
        
        # 盈亏状态
        pnl = analysis["total_pnl"]
        pnl_str = f"+${pnl:,.0f}" if pnl > 0 else f"-${abs(pnl):,.0f}" if pnl < 0 else "$0"
        
        # 活跃度标记
        activity = "🔥" if analysis["has_activity"] else "⚪"
        
        # 重点标记
        watch_badge = "👑" if analysis.get("is_watched", False) else ""
        
        message += f"""
{rank_emoji} {activity} {watch_badge} *{w['pseudonym'][:18]}*
   💰 持仓: ${analysis['total_value']:,.0f} | 📊 {analysis['position_count']}个市场
   📈 24h交易量: ${w['total_volume']:,.0f} | P&L: {pnl_str}
   🔗 `{w['wallet'][:8]}...{w['wallet'][-6:]}`
"""
    
    message += f"""
{separator}
```

💡 *解读:*
• 🥇🥈🥉 = 持仓价值排名
• 🔥 = 最近有交易活动
• 👑 = 已加入重点关注列表

📊 *建议:*
关注高持仓 + 高活跃度的鲸鱼
集中度高的鲸鱼信号更强
"""
    
    return message


def send_top_whales_report(analyses: list) -> bool:
    """发送 Top 鲸鱼排行榜"""
    message = format_top_whales_message(analyses)
    return send_telegram_message(message)


def format_real_market_making_signal(opp: dict) -> str:
    """格式化真实CLOB做市信号"""
    
    # 计算建议挂单价格
    spread = opp['best_ask'] - opp['best_bid']
    suggested_bid = opp['best_bid'] + spread * 0.3
    suggested_ask = opp['best_ask'] - spread * 0.3
    
    # 预期利润
    expected_profit = (suggested_ask - suggested_bid) / ((opp['best_bid'] + opp['best_ask']) / 2)
    
    # 建议仓位
    suggested_size = min(opp['min_depth'] * 0.1, 1000)
    
    # 判断是否为模拟数据
    is_simulated = opp.get('market', '').startswith('test-') or 'simulated' in opp.get('_data_source', '')
    data_source = "🧪 模拟数据" if is_simulated else "✅ 真实CLOB"
    
    return f"""💹 *做市机会* | {data_source}

*{opp['question'][:70]}*

┌─────────────────────────────┐
│  📊 市场深度                 │
├─────────────────────────────┤
│  💰 当前价差: *{opp['spread_pct']:.2%}*              │
│  📈 买价: {opp['best_bid']:.4f}              │
│  📉 卖价: {opp['best_ask']:.4f}              │
├─────────────────────────────┤
│  📦 深度                     │
│  • 买盘: ${opp['bid_depth']:,.0f}              │
│  • 卖盘: ${opp['ask_depth']:,.0f}              │
│  • 流动性: ${opp['liquidity']:,.0f}              │
└─────────────────────────────┘

💡 *建议挂单:*
   买入: {suggested_bid:.4f}
   卖出: {suggested_ask:.4f}
   
💵 *预期利润:* {expected_profit:.2%}
📦 *建议仓位:* ${suggested_size:,.0f}

🔗 [查看市场](https://polymarket.com/event/{opp['market']})
⚠️ *风险提示:* 价格可能快速变动
"""


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
