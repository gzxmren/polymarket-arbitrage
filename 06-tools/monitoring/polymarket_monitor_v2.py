#!/usr/bin/env python3
"""
Polymarket 综合监控器 V2
整合 Pair Cost、跨平台套利、鲸鱼追踪
支持 Telegram 通知 + 风险评估
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# 添加分析工具路径
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))

from pair_cost_scanner import scan_pair_cost_opportunities, PAIR_COST_THRESHOLD
from cross_market_scanner import (
    fetch_polymarket_markets, fetch_manifold_markets, fetch_metaculus_questions,
    match_events, find_arbitrage_opportunities, MIN_GAP_THRESHOLD
)
from whale_tracker_v2 import (
    fetch_recent_trades, identify_active_whales, analyze_whale,
    WHALE_TRADE_THRESHOLD, WHALE_TOTAL_VOLUME
)
from vwap_calculator import calculate_slippage
from kelly_criterion import assess_opportunity
from momentum_strategy import detect_breakout
from news_monitor import NewsMonitor, format_news_alert
from correlation_matrix import CorrelationMatrix, format_correlation_signal
from market_maker import MarketMaker, format_market_making_signal

# 导入重点鲸鱼跟踪
try:
    from whale_watchlist import (
        load_watchlist, update_watchlist_from_analysis,
        get_watchlist_summary, format_watchlist_summary,
        save_watchlist
    )
    WATCHLIST_AVAILABLE = True
except ImportError:
    WATCHLIST_AVAILABLE = False
    print("⚠️  Whale watchlist not available")

# 导入鲸鱼跟随策略（V2新增）
try:
    from whale_following import WhaleFollowingStrategy, format_signal_telegram
    WHALE_FOLLOWING_AVAILABLE = True
except ImportError:
    WHALE_FOLLOWING_AVAILABLE = False
    print("⚠️  Whale following strategy not available")

# 导入CLOB API（真实订单簿）
try:
    from clob_api import get_markets_with_order_book, get_order_book, calculate_spread_from_order_book
    CLOB_API_AVAILABLE = True
except ImportError:
    CLOB_API_AVAILABLE = False
    print("⚠️  CLOB API not available, using simulated order book")

# 导入通知器（使用 V2 版本）
try:
    from telegram_notifier_v2 import (
        send_pair_cost_alert, send_whale_alert, send_cross_market_alert, send_summary_report,
        format_real_market_making_signal
    )
    NOTIFICATIONS_ENABLED = True
except ImportError:
    NOTIFICATIONS_ENABLED = False
    print("⚠️  Telegram notifier not available")

# 导入风险评估
try:
    from risk_reviewer import (
        review_pair_cost_opportunity,
        review_cross_market_opportunity,
        review_whale_signal,
        format_risk_review,
        should_review
    )
    RISK_REVIEW_AVAILABLE = True
except ImportError:
    RISK_REVIEW_AVAILABLE = False
    print("⚠️  Risk reviewer not available")

# 配置
DATA_DIR = Path(__file__).parent.parent.parent / "07-data"
NOTIFY_IMMEDIATELY = os.getenv("NOTIFY_IMMEDIATELY", "true").lower() == "true"
RISK_REVIEW_ENABLED = os.getenv("RISK_REVIEW_ENABLED", "true").lower() == "true"

# 做市机会通知控制
# 现在使用真实CLOB API数据，可以启用通知
MARKET_MAKING_NOTIFY_ENABLED = os.getenv("MARKET_MAKING_NOTIFY", "true").lower() == "true"
MARKET_MAKING_MIN_SPREAD = float(os.getenv("MARKET_MAKING_MIN_SPREAD", "0.015"))  # 最小价差1.5%
MARKET_MAKING_MIN_DEPTH = float(os.getenv("MARKET_MAKING_MIN_DEPTH", "5000"))     # 最小深度$5000
MARKET_MAKING_MIN_PRICE = float(os.getenv("MARKET_MAKING_MIN_PRICE", "0.05"))     # 最小价格5%
MARKET_MAKING_MAX_PRICE = float(os.getenv("MARKET_MAKING_MAX_PRICE", "0.95"))     # 最大价格95%


def run_pair_cost_scan() -> dict:
    """运行 Pair Cost 扫描"""
    print("\n" + "="*70)
    print("🔍 扫描 Pair Cost 套利机会...")
    print("="*70)
    
    opportunities = scan_pair_cost_opportunities(limit=500)  # 增加扫描数量（评价报告建议）
    approved_opportunities = []
    
    result = {
        "count": len(opportunities),
        "approved_count": 0,
        "opportunities": opportunities,
        "threshold": PAIR_COST_THRESHOLD,
        "risk_reviews": []
    }
    
    if opportunities:
        print(f"\n✅ 发现 {len(opportunities)} 个套利机会")
        
        for opp in opportunities[:5]:
            print(f"\n   💰 {opp['question'][:60]}...")
            print(f"      利润: {opp['profit_pct']:.2f}% | Pair Cost: ${opp['pair_cost']:.4f}")
            
            # 使用 Kelly 准则评估仓位
            kelly_assessment = assess_opportunity(
                profit_margin=opp['profit_margin'],
                risk_score=0.2,  # 基础风险分
                historical_win_rate=0.6
            )
            opp['kelly_ratio'] = kelly_assessment['kelly_ratio']
            opp['suggested_position'] = kelly_assessment['suggested_position']
            print(f"      Kelly建议: {kelly_assessment['suggested_position']:.1%} 仓位")
            
            # 风险评估
            if RISK_REVIEW_AVAILABLE and RISK_REVIEW_ENABLED:
                review = review_pair_cost_opportunity(opp)
                result["risk_reviews"].append(review)
                
                print(f"      风险评估: {review['risk_level'].upper()} (分数: {review['risk_score']:.1%})")
                
                if review['approved']:
                    approved_opportunities.append(opp)
                    print(f"      ✅ 通过审核")
                else:
                    print(f"      ❌ 未通过审核: {review['recommendation']}")
                    
                # 发送详细风险评估
                if NOTIFICATIONS_ENABLED:
                    from telegram_notifier_v2 import send_telegram_message
                    risk_message = format_risk_review(review, "Pair Cost 套利")
                    send_telegram_message(risk_message)
            else:
                approved_opportunities.append(opp)
            
            # 发送机会通知（只有通过审核的）
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                if not RISK_REVIEW_ENABLED or review['approved']:
                    send_pair_cost_alert(opp)
    else:
        print("\n⚪ 未发现套利机会")
    
    result["approved_count"] = len(approved_opportunities)
    return result


def run_cross_market_scan() -> dict:
    """运行跨平台套利扫描"""
    print("\n" + "="*70)
    print("🔍 扫描跨平台套利机会...")
    print("="*70)
    
    # 获取数据
    print("\n📡 获取数据...")
    poly_markets = fetch_polymarket_markets(limit=100)
    mf_markets = fetch_manifold_markets(limit=100)
    me_questions = fetch_metaculus_questions(limit=50)
    
    print(f"   Polymarket: {len(poly_markets)} 个")
    print(f"   Manifold: {len(mf_markets)} 个")
    print(f"   Metaculus: {len(me_questions)} 个")
    
    if not poly_markets:
        print("\n❌ 无法获取 Polymarket 数据")
        return {"count": 0, "approved_count": 0, "opportunities": []}
    
    # 匹配和检测
    matches = match_events(poly_markets, mf_markets, me_questions)
    opportunities = find_arbitrage_opportunities(matches)
    approved_opportunities = []
    
    result = {
        "count": len(opportunities),
        "approved_count": 0,
        "opportunities": opportunities,
        "threshold": MIN_GAP_THRESHOLD,
        "platforms": {
            "polymarket": len(poly_markets),
            "manifold": len(mf_markets),
            "metaculus": len(me_questions)
        },
        "risk_reviews": []
    }
    
    if opportunities:
        print(f"\n✅ 发现 {len(opportunities)} 个套利机会")
        
        for opp in opportunities[:5]:
            print(f"\n   💎 {opp['question'][:60]}...")
            print(f"      价差: {opp['gap']:.1%} | 匹配度: {opp['similarity']:.1%}")
            
            # 风险评估
            if RISK_REVIEW_AVAILABLE and RISK_REVIEW_ENABLED:
                review = review_cross_market_opportunity(opp)
                result["risk_reviews"].append(review)
                
                print(f"      风险评估: {review['risk_level'].upper()} (分数: {review['risk_score']:.1%})")
                
                if review['approved']:
                    approved_opportunities.append(opp)
                    print(f"      ✅ 通过审核")
                else:
                    print(f"      ❌ 未通过审核: {review['recommendation']}")
                    
                if NOTIFICATIONS_ENABLED:
                    from telegram_notifier_v2 import send_telegram_message
                    risk_message = format_risk_review(review, "跨平台套利")
                    send_telegram_message(risk_message)
            else:
                approved_opportunities.append(opp)
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                if not RISK_REVIEW_ENABLED or review['approved']:
                    send_cross_market_alert(opp)
    else:
        print("\n⚪ 未发现套利机会")
    
    result["approved_count"] = len(approved_opportunities)
    return result


def run_whale_tracking() -> dict:
    """运行鲸鱼追踪（集成重点鲸鱼跟踪 + Top 10 排行榜）"""
    print("\n" + "="*70)
    print("🐋 追踪鲸鱼活动...")
    print("="*70)
    
    # 加载重点鲸鱼列表
    watchlist = load_watchlist() if WATCHLIST_AVAILABLE else None
    
    # 获取交易
    trades = fetch_recent_trades(limit=1000)
    print(f"\n📡 获取 {len(trades)} 笔最近交易")
    
    # 识别鲸鱼
    whales = identify_active_whales(trades)
    print(f"   识别 {len(whales)} 个活跃大户")
    
    if not whales:
        print("\n⚪ 未发现活跃鲸鱼")
        return {"tracked": 0, "active": 0, "whales": [], "active_analyses": [], "top_10": []}
    
    # 分析每个鲸鱼
    active_whales = []
    approved_signals = []
    
    for wallet, info in whales.items():
        analysis = analyze_whale(wallet, info)
        
        # 跳过异常数据（持仓多但价值极低）
        if analysis.get("is_suspicious", False):
            print(f"\n   ⚠️  {info['pseudonym']} - 数据异常，跳过")
            continue
        
        # 更新重点鲸鱼列表
        is_watched = False
        is_new_watched = False
        if WATCHLIST_AVAILABLE and watchlist is not None:
            watch_result = update_watchlist_from_analysis(watchlist, wallet, analysis)
            is_watched = watch_result.get("is_watched", False)
            is_new_watched = watch_result.get("is_new", False)
        
        if analysis["has_activity"] or is_watched:
            # 风险评估
            if RISK_REVIEW_AVAILABLE and RISK_REVIEW_ENABLED:
                review = review_whale_signal(analysis)
                
                print(f"\n   🐋 {info['pseudonym']}" + (" 🔔" if is_watched else ""))
                print(f"      风险评估: {review['risk_level'].upper()} (分数: {review['risk_score']:.1%})")
                
                if review['approved']:
                    approved_signals.append(analysis)
                    active_whales.append(analysis)
                    print(f"      ✅ 通过审核")
                else:
                    print(f"      ❌ 未通过审核")
                    
                if NOTIFICATIONS_ENABLED:
                    from telegram_notifier_v2 import send_telegram_message
                    risk_message = format_risk_review(review, "鲸鱼信号")
                    send_telegram_message(risk_message)
            else:
                active_whales.append(analysis)
                approved_signals.append(analysis)
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                if not RISK_REVIEW_ENABLED or review['approved']:
                    # 传递重点鲸鱼标记
                    send_whale_alert(analysis, is_watched=is_watched, is_new_watched=is_new_watched)
    
    # 保存重点鲸鱼列表
    if WATCHLIST_AVAILABLE and watchlist is not None:
        save_watchlist(watchlist)
    
    # 🆕 生成 Top 10 鲸鱼排名
    top_10_whales = []
    print("\n" + "-"*70)
    print("🏆 Top 10 鲸鱼排行榜 (按持仓价值)")
    print("-"*70)
    
    # 收集所有分析结果（排除异常数据）
    all_analyses = []
    for wallet, info in whales.items():
        analysis = analyze_whale(wallet, info)
        if not analysis.get("is_suspicious", False):
            all_analyses.append(analysis)
    
    # 按持仓价值排序
    all_analyses.sort(key=lambda x: x["total_value"], reverse=True)
    top_10_whales = all_analyses[:10]
    
    # 打印 Top 10
    for i, analysis in enumerate(top_10_whales, 1):
        w = analysis["info"]
        rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i:2d}.")
        activity = "🔥" if analysis["has_activity"] else "⚪"
        is_watched = any(
            w['wallet'] == whale.get('wallet') 
            for whale in (watchlist.get('whales', {}).values() if watchlist else [])
        )
        watch_badge = "👑" if is_watched else ""
        pnl = analysis["total_pnl"]
        pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"
        
        print(f"   {rank_emoji} {activity} {watch_badge} {w['pseudonym'][:18]:<18} | "
              f"💰 ${analysis['total_value']:>12,.0f} | "
              f"📊 {analysis['position_count']:>3}个 | "
              f"{pnl_emoji} ${pnl:>+10,.0f}")
    
    print("-"*70)
    
    result = {
        "tracked": len(whales),
        "active": len(active_whales),
        "approved_signals": len(approved_signals),
        "whales": list(whales.values()),
        "active_analyses": active_whales,
        "top_10": top_10_whales,  # 🆕 添加 Top 10 数据
        "threshold": WHALE_TRADE_THRESHOLD
    }
    
    if active_whales:
        print(f"\n✅ 发现 {len(active_whales)} 个活跃鲸鱼（通过审核: {len(approved_signals)}）")
    else:
        print("\n⚪ 无明显鲸鱼活动")
    
    return result


def run_news_monitoring() -> dict:
    """运行新闻监控"""
    print("\n" + "="*70)
    print("📰 监控热点新闻...")
    print("="*70)
    
    monitor = NewsMonitor()
    
    # 这里可以从外部 API 获取新闻
    # 暂时使用示例数据演示
    test_headlines = [
        {'title': 'Bitcoin price movement detected', 'source': 'crypto'},
        {'title': 'Fed meeting scheduled next week', 'source': 'finance'}
    ]
    
    results = monitor.scan_news_impact(test_headlines)
    signals = monitor.generate_trading_signals(results)
    
    if signals:
        print(f"\n✅ 发现 {len(signals)} 个新闻驱动信号")
        for signal in signals:
            print(f"\n   📊 {signal['category']}: {signal['direction']}")
            print(f"      置信度: {signal['confidence']:.1%}")
            print(f"      建议: {signal['reason']}")
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                from telegram_notifier_v2 import send_telegram_message
                alert = format_news_alert({
                    'headline': signal['reason'],
                    'impact_score': int(signal['confidence'] * 100),
                    'category': signal['category'],
                    'urgency': signal['timeframe'],
                    'sentiment': 'POSITIVE' if signal['direction'] == 'UP' else 'NEGATIVE',
                    'keywords': signal['suggested_markets'],
                    'suggested_action': f"关注 {signal['category']} 相关市场"
                })
                send_telegram_message(alert)
    else:
        print("\n⚪ 无重大新闻影响")
    
    return {
        "signals": len(signals),
        "details": signals
    }


def run_correlation_analysis() -> dict:
    """运行相关性分析"""
    print("\n" + "="*70)
    print("📊 分析市场相关性...")
    print("="*70)
    
    analyzer = CorrelationMatrix(lookback_period=50, correlation_threshold=0.7)
    
    # 从活跃市场获取价格历史（示例）
    markets = fetch_polymarket_markets(limit=20)
    
    if len(markets) < 3:
        print("\n⚪ 市场数据不足，跳过相关性分析")
        return {"signals": 0, "details": []}
    
    # 更新价格数据
    for market in markets[:10]:  # 分析前 10 个市场
        market_id = market.get('id', market.get('slug', ''))
        price = market.get('yes_price', 0.5)
        analyzer.update_price(market_id, price)
    
    # 检测相关性断裂
    market_ids = [m.get('id', m.get('slug', '')) for m in markets[:10]]
    signals = analyzer.detect_correlation_breakdown(market_ids)
    
    if signals:
        print(f"\n✅ 发现 {len(signals)} 个相关性断裂信号")
        for signal in signals[:3]:  # 只显示前 3 个
            print(f"\n   📊 {signal.market_a} vs {signal.market_b}")
            print(f"      相关性: {signal.normal_correlation:.2f} → {signal.current_correlation:.2f}")
            print(f"      偏离: {signal.divergence:.2f}")
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                from telegram_notifier_v2 import send_telegram_message
                send_telegram_message(format_correlation_signal(signal))
    else:
        print("\n⚪ 无相关性断裂信号")
    
    return {
        "signals": len(signals),
        "details": [
            {
                'market_a': s.market_a,
                'market_b': s.market_b,
                'divergence': s.divergence,
                'direction': s.direction
            } for s in signals
        ]
    }


def run_market_making_scan() -> dict:
    """运行做市策略扫描 - 使用真实CLOB订单簿"""
    print("\n" + "="*70)
    print("💹 扫描做市机会 (真实CLOB数据)...")
    print("="*70)
    
    opportunities = []
    
    if not CLOB_API_AVAILABLE:
        print("\n⚠️  CLOB API不可用，跳过做市扫描")
        return {"opportunities": 0, "details": []}
    
    # 获取启用了订单簿的市场
    print("\n📡 获取CLOB市场...")
    markets = get_markets_with_order_book(limit=20)
    print(f"   找到 {len(markets)} 个CLOB市场")
    
    for market in markets[:10]:  # 分析前10个
        try:
            # 获取YES token的订单簿
            order_book = get_order_book(market['yes_token'])
            if not order_book:
                continue
            
            # 计算价差
            spread_info = calculate_spread_from_order_book(order_book)
            if not spread_info:
                continue
            
            spread_pct = spread_info.get('spread_pct', 0)
            min_depth = spread_info.get('min_depth', 0)
            best_bid = spread_info.get('best_bid', 0)
            best_ask = spread_info.get('best_ask', 1)
            
            # 过滤条件检查
            # 注意：用best_bid判断极端市场，而不是mid_price
            # 因为对于买价0.001/卖价0.999的市场，mid_price=0.50是正常的
            filtered_reason = None
            if spread_pct < MARKET_MAKING_MIN_SPREAD:
                filtered_reason = f"价差{spread_pct:.2%}<阈值"
            elif min_depth < MARKET_MAKING_MIN_DEPTH:
                filtered_reason = f"深度${min_depth:,.0f}<阈值"
            elif best_bid < MARKET_MAKING_MIN_PRICE or best_bid > MARKET_MAKING_MAX_PRICE:
                # 排除极端价格市场（买价接近0或1，流动性枯竭）
                filtered_reason = f"买价{best_bid:.3f}极端(流动性枯竭)"
            
            if filtered_reason:
                # 只打印被过滤的极端市场（调试用）
                if best_bid < 0.05 or best_bid > 0.95:
                    print(f"\n   ⚪ {market['question'][:45]}... | 已过滤: {filtered_reason}")
                continue
            
            # 创建信号
            opportunity = {
                'market': market['slug'],
                'question': market['question'],
                'spread_pct': spread_pct,
                'best_bid': spread_info['best_bid'],
                'best_ask': spread_info['best_ask'],
                'bid_depth': spread_info['bid_depth'],
                'ask_depth': spread_info['ask_depth'],
                'min_depth': spread_info['min_depth'],
                'expected_profit': spread_pct * 0.4,  # 假设能吃到40%价差
                'liquidity': market['liquidity'],
                'token_id': market['yes_token'],
                '_data_source': 'real_clob'  # 标记为真实CLOB数据
            }
            opportunities.append(opportunity)
            
        except Exception as e:
            continue
    
    # 按价差排序
    opportunities.sort(key=lambda x: x['spread_pct'], reverse=True)
    
    if opportunities:
        print(f"\n✅ 发现 {len(opportunities)} 个做市机会")
        print(f"   📋 最小价差: {MARKET_MAKING_MIN_SPREAD:.1%}")
        print(f"   📋 最小深度: ${MARKET_MAKING_MIN_DEPTH:,.0f}")
        print(f"   📋 价格范围: {MARKET_MAKING_MIN_PRICE:.0%} ~ {MARKET_MAKING_MAX_PRICE:.0%} (排除极端市场)")
        
        for opp in opportunities[:5]:
            print(f"\n   💹 {opp['question'][:50]}...")
            print(f"      价差: {opp['spread_pct']:.2%}")
            print(f"      买价: {opp['best_bid']:.4f} | 卖价: {opp['best_ask']:.4f}")
            print(f"      深度: ${opp['bid_depth']:,.0f} / ${opp['ask_depth']:,.0f}")
        
        # 发送Telegram通知
        if MARKET_MAKING_NOTIFY_ENABLED and NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
            print(f"\n   📱 发送 {len(opportunities)} 条做市机会通知")
            for opp in opportunities:
                from telegram_notifier_v2 import send_telegram_message
                send_telegram_message(format_real_market_making_signal(opp))
        else:
            print(f"\n   ⚪ Telegram通知已禁用")
    else:
        print("\n⚪ 无合适做市机会")
    
    return {
        "opportunities": len(opportunities),
        "details": opportunities
    }


def save_report(pair_cost: dict, cross_market: dict, whale: dict, news: dict = None, correlation: dict = None, market_making: dict = None):
    """保存监控报告"""
    report = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "pair_cost": pair_cost,
        "cross_market": cross_market,
        "whale": whale,
        "news": news or {"signals": 0, "details": []},
        "correlation": correlation or {"signals": 0, "details": []},
        "market_making": market_making or {"opportunities": 0, "details": []},
        "summary": {
            "total_opportunities": pair_cost["count"] + cross_market["count"] + (market_making or {}).get("opportunities", 0),
            "approved_opportunities": pair_cost["approved_count"] + cross_market["approved_count"],
            "active_whales": whale["active"],
            "news_signals": (news or {}).get("signals", 0),
            "correlation_signals": (correlation or {}).get("signals", 0),
            "market_making_opportunities": (market_making or {}).get("opportunities", 0),
            "risk_review_enabled": RISK_REVIEW_ENABLED
        }
    }
    
    # 保存JSON
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = DATA_DIR / f"monitor_report_{timestamp}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n📄 报告已保存: {report_file}")
    
    return report


def send_summary(report: dict):
    """发送汇总通知"""
    if not NOTIFICATIONS_ENABLED:
        return
    
    stats = {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pair_cost_count": report["pair_cost"]["approved_count"],
        "cross_market_count": report["cross_market"]["approved_count"],
        "active_whales": report["whale"]["active"],
        "markets_scanned": report["cross_market"].get("platforms", {}).get("polymarket", 0),
        "total_opportunities": report["summary"]["approved_opportunities"],
        "avg_pair_cost": 1.0  # 简化
    }
    
    # 添加风险评估状态
    if RISK_REVIEW_ENABLED:
        stats["risk_review"] = "✅ 已启用"
    else:
        stats["risk_review"] = "⚪ 已关闭"
    
    send_summary_report(stats)


def run_whale_following_scan() -> dict:
    """运行鲸鱼跟随策略扫描（V2新增）"""
    print("\n" + "="*70)
    print("🐋 运行鲸鱼跟随策略...")
    print("="*70)
    
    if not WHALE_FOLLOWING_AVAILABLE:
        print("\n⚪ 鲸鱼跟随策略不可用")
        return {"signals": 0, "details": []}
    
    try:
        strategy = WhaleFollowingStrategy()
        signals = strategy.scan()
        
        if signals:
            print(f"\n✅ 发现 {len(signals)} 个鲸鱼跟随信号")
            
            # 发送Telegram通知
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                from telegram_notifier_v2 import send_telegram_message
                for signal in signals[:3]:  # 最多发送3个
                    message = format_signal_telegram(signal)
                    send_telegram_message(message)
                    print(f"   📱 已发送信号: {signal.whale.pseudonym}")
            
            # 记录信号到数据库
            save_signals_to_db(signals)
        else:
            print("\n⚪ 未发现鲸鱼跟随信号")
        
        return {
            "signals": len(signals),
            "details": [{
                'whale': s.whale.pseudonym,
                'market': s.market,
                'direction': s.direction,
                'confidence': s.confidence
            } for s in signals]
        }
    except Exception as e:
        print(f"\n❌ 鲸鱼跟随策略错误: {e}")
        import traceback
        traceback.print_exc()
        return {"signals": 0, "details": [], "error": str(e)}


def save_signals_to_db(signals: list):
    """保存信号到数据库"""
    try:
        import sqlite3
        from pathlib import Path
        
        db_path = Path(__file__).parent.parent.parent / "dashboard/backend/database/polymarket.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        for signal in signals:
            cursor.execute('''
                INSERT INTO signals 
                (type, wallet, market, direction, confidence, suggested_position, 
                 expected_price_change, suggested_holding_hours, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                signal.type,
                signal.whale.wallet,
                signal.market,
                signal.direction,
                signal.confidence,
                signal.suggested_position,
                signal.expected_return,
                48,  # 建议持有48小时
                'pending',
                signal.created_at.isoformat()
            ))
        
        conn.commit()
        conn.close()
        print(f"   💾 已保存 {len(signals)} 个信号到数据库")
    except Exception as e:
        print(f"   ⚠️  保存信号失败: {e}")


def main():
    print("🚀 Polymarket 综合监控器 V2")
    print("="*70)
    print(f"通知功能: {'✅ 已启用' if NOTIFICATIONS_ENABLED else '❌ 未启用'}")
    print(f"风险评估: {'✅ 已启用' if RISK_REVIEW_ENABLED else '⚪ 已关闭'}")
    print(f"即时通知: {'✅ 开启' if NOTIFY_IMMEDIATELY else '⚪ 关闭'}")
    print(f"CLOB API: {'✅ 可用' if CLOB_API_AVAILABLE else '❌ 不可用'}")
    print(f"做市通知: {'✅ 开启' if MARKET_MAKING_NOTIFY_ENABLED else '⚪ 关闭'}")
    print(f"鲸鱼跟随: {'✅ 可用' if WHALE_FOLLOWING_AVAILABLE else '❌ 不可用'}")
    print(f"做市阈值: 价差>{MARKET_MAKING_MIN_SPREAD:.1%}, 深度>${MARKET_MAKING_MIN_DEPTH:,.0f}")
    print(f"价格范围: {MARKET_MAKING_MIN_PRICE:.0%}~{MARKET_MAKING_MAX_PRICE:.0%} (排除极端市场)")
    print("="*70)
    
    # 运行各项扫描
    pair_cost_result = run_pair_cost_scan()
    cross_market_result = run_cross_market_scan()
    whale_result = run_whale_tracking()
    whale_following_result = run_whale_following_scan()  # V2新增
    news_result = run_news_monitoring()
    correlation_result = run_correlation_analysis()
    market_making_result = run_market_making_scan()
    
    # 保存报告
    report = save_report(pair_cost_result, cross_market_result, whale_result, news_result, correlation_result, market_making_result)
    report['whale_following'] = whale_following_result  # 添加到报告
    
    # 发送汇总
    print("\n" + "="*70)
    print("📊 扫描完成，发送汇总...")
    send_summary(report)
    
    # 最终输出
    print("\n" + "="*70)
    print("📈 本次扫描汇总:")
    print("="*70)
    print(f"   Pair Cost 机会: {pair_cost_result['count']} 个 (通过审核: {pair_cost_result['approved_count']})")
    print(f"   跨平台套利: {cross_market_result['count']} 个 (通过审核: {cross_market_result['approved_count']})")
    print(f"   活跃鲸鱼: {whale_result['active']} 个")
    print(f"   新闻信号: {news_result.get('signals', 0)} 个")
    print(f"   相关性断裂: {correlation_result.get('signals', 0)} 个")
    print(f"   做市机会: {market_making_result.get('opportunities', 0)} 个 (仅记录)")
    print(f"   总计机会: {report['summary']['total_opportunities']} 个")
    print(f"   通过审核: {report['summary']['approved_opportunities']} 个")


if __name__ == "__main__":
    main()
