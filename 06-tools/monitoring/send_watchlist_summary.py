#!/usr/bin/env python3
"""
发送重点鲸鱼概况报告 + Top 10 排行榜
每4小时运行一次
"""

import sys
from pathlib import Path

# 添加分析工具路径
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
sys.path.insert(0, str(Path(__file__).parent))

from whale_watchlist import load_watchlist, get_watchlist_summary, format_watchlist_summary
from whale_tracker_v2 import get_top_whales
from telegram_notifier_v2 import send_telegram_message, send_top_whales_report


def main():
    """发送重点鲸鱼概况 + Top 10 排行榜"""
    print("🔔 生成鲸鱼报告...")
    
    # 1. 发送 Top 10 排行榜
    print("\n📊 获取 Top 10 鲸鱼数据...")
    top_whales = get_top_whales(limit=10)
    
    if top_whales:
        print(f"   ✅ 获取到 {len(top_whales)} 个鲸鱼")
        if send_top_whales_report(top_whales):
            print("   ✅ 已发送 Top 10 排行榜")
        else:
            print("   ❌ Top 10 排行榜发送失败")
    else:
        print("   ⚪ 未获取到鲸鱼数据")
    
    # 2. 发送重点鲸鱼概况
    print("\n🔔 生成重点鲸鱼概况...")
    watchlist = load_watchlist()
    summary = get_watchlist_summary(watchlist)
    
    if summary["count"] > 0:
        message = format_watchlist_summary(summary)
        if send_telegram_message(message):
            print(f"   ✅ 已发送概况报告: {summary['count']} 位重点鲸鱼")
        else:
            print("   ❌ 概况报告发送失败")
    else:
        print("   ⚪ 当前无重点关注的鲸鱼")
    
    print("\n✅ 报告完成")


if __name__ == "__main__":
    main()
