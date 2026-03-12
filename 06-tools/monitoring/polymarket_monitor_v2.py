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

# 导入通知器（使用 V2 版本）
try:
    from telegram_notifier_v2 import (
        send_pair_cost_alert, send_whale_alert, send_cross_market_alert, send_summary_report
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


def run_pair_cost_scan() -> dict:
    """运行 Pair Cost 扫描"""
    print("\n" + "="*70)
    print("🔍 扫描 Pair Cost 套利机会...")
    print("="*70)
    
    opportunities = scan_pair_cost_opportunities(limit=200)
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
    """运行鲸鱼追踪"""
    print("\n" + "="*70)
    print("🐋 追踪鲸鱼活动...")
    print("="*70)
    
    # 获取交易
    trades = fetch_recent_trades(limit=1000)
    print(f"\n📡 获取 {len(trades)} 笔最近交易")
    
    # 识别鲸鱼
    whales = identify_active_whales(trades)
    print(f"   识别 {len(whales)} 个活跃大户")
    
    if not whales:
        print("\n⚪ 未发现活跃鲸鱼")
        return {"tracked": 0, "active": 0, "whales": [], "active_analyses": []}
    
    # 分析每个鲸鱼
    active_whales = []
    approved_signals = []
    
    for wallet, info in whales.items():
        analysis = analyze_whale(wallet, info)
        
        if analysis["has_activity"]:
            # 风险评估
            if RISK_REVIEW_AVAILABLE and RISK_REVIEW_ENABLED:
                review = review_whale_signal(analysis)
                
                print(f"\n   🐋 {info['pseudonym']}")
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
                    send_whale_alert(analysis)
    
    result = {
        "tracked": len(whales),
        "active": len(active_whales),
        "approved_signals": len(approved_signals),
        "whales": list(whales.values()),
        "active_analyses": active_whales,
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
    """运做市策略扫描"""
    print("\n" + "="*70)
    print("💹 扫描做市机会...")
    print("="*70)
    
    mm = MarketMaker(min_spread_pct=0.01, max_position=1000)
    
    # 获取高流动性市场
    markets = fetch_polymarket_markets(limit=20)
    opportunities = []
    
    for market in markets[:10]:
        # 模拟订单簿（实际应从 CLOB API 获取）
        yes_price = market.get('yes_price', 0.5)
        spread = 0.02  # 假设 2% 价差
        
        order_book = {
            'market': market.get('slug', ''),
            'bids': [[yes_price * 0.99, 5000], [yes_price * 0.98, 8000]],
            'asks': [[yes_price * 1.01, 3000], [yes_price * 1.02, 6000]]
        }
        
        signal = mm.analyze_order_book(order_book)
        if signal and signal.confidence > 0.5:
            opportunities.append(signal)
    
    if opportunities:
        print(f"\n✅ 发现 {len(opportunities)} 个做市机会")
        for opp in opportunities[:3]:
            print(f"\n   💹 {opp.market}")
            print(f"      价差: {opp.spread_pct:.2%}")
            print(f"      预期利润: {opp.expected_profit:.2%}")
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                from telegram_notifier_v2 import send_telegram_message
                send_telegram_message(format_market_making_signal(opp))
    else:
        print("\n⚪ 无合适做市机会")
    
    return {
        "opportunities": len(opportunities),
        "details": [
            {
                'market': o.market,
                'spread_pct': o.spread_pct,
                'expected_profit': o.expected_profit
            } for o in opportunities
        ]
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


def main():
    print("🚀 Polymarket 综合监控器 V2")
    print("="*70)
    print(f"通知功能: {'✅ 已启用' if NOTIFICATIONS_ENABLED else '❌ 未启用'}")
    print(f"风险评估: {'✅ 已启用' if RISK_REVIEW_ENABLED else '⚪ 已关闭'}")
    print(f"即时通知: {'✅ 开启' if NOTIFY_IMMEDIATELY else '⚪ 关闭'}")
    print("="*70)
    
    # 运行各项扫描
    pair_cost_result = run_pair_cost_scan()
    cross_market_result = run_cross_market_scan()
    whale_result = run_whale_tracking()
    news_result = run_news_monitoring()
    correlation_result = run_correlation_analysis()
    market_making_result = run_market_making_scan()
    
    # 保存报告
    report = save_report(pair_cost_result, cross_market_result, whale_result, news_result, correlation_result, market_making_result)
    
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
    print(f"   做市机会: {market_making_result.get('opportunities', 0)} 个")
    print(f"   总计机会: {report['summary']['total_opportunities']} 个")
    print(f"   通过审核: {report['summary']['approved_opportunities']} 个")


if __name__ == "__main__":
    main()
