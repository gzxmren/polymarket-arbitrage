#!/usr/bin/env python3
"""
Polymarket 综合监控器
整合 Pair Cost、跨平台套利、鲸鱼追踪，支持 Telegram 通知
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

# 导入通知器（使用 V2 版本）
try:
    from telegram_notifier_v2 import (
        send_pair_cost_alert, send_whale_alert, send_cross_market_alert, send_summary_report
    )
    NOTIFICATIONS_ENABLED = True
except ImportError:
    try:
        # 回退到旧版本
        from telegram_notifier import (
            send_pair_cost_alert, send_whale_alert, send_cross_market_alert, send_summary_report
        )
        NOTIFICATIONS_ENABLED = True
    except ImportError:
        NOTIFICATIONS_ENABLED = False
        print("⚠️  Telegram notifier not available")

# 配置
DATA_DIR = Path(__file__).parent.parent.parent / "07-data"
NOTIFY_IMMEDIATELY = os.getenv("NOTIFY_IMMEDIATELY", "true").lower() == "true"


def run_pair_cost_scan() -> dict:
    """运行 Pair Cost 扫描"""
    print("\n" + "="*70)
    print("🔍 扫描 Pair Cost 套利机会...")
    print("="*70)
    
    opportunities = scan_pair_cost_opportunities(limit=200)
    
    result = {
        "count": len(opportunities),
        "opportunities": opportunities,
        "threshold": PAIR_COST_THRESHOLD
    }
    
    if opportunities:
        print(f"\n✅ 发现 {len(opportunities)} 个套利机会")
        for opp in opportunities[:5]:  # 只显示前5个
            print(f"\n   💰 {opp['question'][:60]}...")
            print(f"      利润: {opp['profit_pct']:.2f}% | Pair Cost: ${opp['pair_cost']:.4f}")
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                send_pair_cost_alert(opp)
    else:
        print("\n⚪ 未发现套利机会")
    
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
    
    # 匹配和检测
    matches = match_events(poly_markets, mf_markets, me_questions)
    opportunities = find_arbitrage_opportunities(matches)
    
    result = {
        "count": len(opportunities),
        "opportunities": opportunities,
        "threshold": MIN_GAP_THRESHOLD,
        "platforms": {
            "polymarket": len(poly_markets),
            "manifold": len(mf_markets),
            "metaculus": len(me_questions)
        }
    }
    
    if opportunities:
        print(f"\n✅ 发现 {len(opportunities)} 个套利机会")
        for opp in opportunities[:5]:
            print(f"\n   💎 {opp['question'][:60]}...")
            print(f"      价差: {opp['gap']:.1%} | 建议: {opp['suggested_action']}")
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                send_cross_market_alert(opp)
    else:
        print("\n⚪ 未发现套利机会")
    
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
    
    # 分析每个鲸鱼
    active_whales = []
    for wallet, info in whales.items():
        analysis = analyze_whale(wallet, info)
        if analysis["has_activity"]:
            active_whales.append(analysis)
            
            if NOTIFY_IMMEDIATELY and NOTIFICATIONS_ENABLED:
                send_whale_alert(analysis)
    
    result = {
        "tracked": len(whales),
        "active": len(active_whales),
        "whales": list(whales.values()),
        "active_analyses": active_whales,
        "threshold": WHALE_TRADE_THRESHOLD
    }
    
    if active_whales:
        print(f"\n✅ 发现 {len(active_whales)} 个活跃鲸鱼")
        for analysis in active_whales:
            w = analysis['info']
            print(f"\n   🐋 {w['pseudonym']}")
            print(f"      24h量: ${w['total_volume']:,.0f} | 变动: {len(analysis['changes'])} 个")
    else:
        print("\n⚪ 无明显鲸鱼活动")
    
    return result


def save_report(pair_cost: dict, cross_market: dict, whale: dict):
    """保存监控报告"""
    report = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "pair_cost": pair_cost,
        "cross_market": cross_market,
        "whale": whale,
        "summary": {
            "total_opportunities": pair_cost["count"] + cross_market["count"],
            "active_whales": whale["active"]
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
        "pair_cost_count": report["pair_cost"]["count"],
        "cross_market_count": report["cross_market"]["count"],
        "active_whales": report["whale"]["active"],
        "markets_scanned": report["cross_market"]["platforms"]["polymarket"],
        "total_opportunities": report["summary"]["total_opportunities"]
    }
    
    send_summary_report(stats)


def main():
    print("🚀 Polymarket 综合监控器")
    print("="*70)
    print(f"通知功能: {'✅ 已启用' if NOTIFICATIONS_ENABLED else '❌ 未启用'}")
    print(f"即时通知: {'✅ 开启' if NOTIFY_IMMEDIATELY else '⚪ 关闭'}")
    print("="*70)
    
    # 运行各项扫描
    pair_cost_result = run_pair_cost_scan()
    cross_market_result = run_cross_market_scan()
    whale_result = run_whale_tracking()
    
    # 保存报告
    report = save_report(pair_cost_result, cross_market_result, whale_result)
    
    # 发送汇总
    print("\n" + "="*70)
    print("📊 扫描完成，发送汇总...")
    send_summary(report)
    
    # 最终输出
    print("\n" + "="*70)
    print("📈 本次扫描汇总:")
    print("="*70)
    print(f"   Pair Cost 机会: {pair_cost_result['count']} 个")
    print(f"   跨平台套利: {cross_market_result['count']} 个")
    print(f"   活跃鲸鱼: {whale_result['active']} 个")
    print(f"   总计机会: {report['summary']['total_opportunities']} 个")


if __name__ == "__main__":
    main()
