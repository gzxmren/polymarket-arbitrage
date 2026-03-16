#!/usr/bin/env python3
"""
发送 Top 10 鲸鱼排行榜报告
可以手动运行或加入定时任务
"""

import sys
from pathlib import Path

# 添加分析工具路径
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
sys.path.insert(0, str(Path(__file__).parent))

from whale_tracker_v2 import get_top_whales
from telegram_notifier_v2 import send_top_whales_report


def main():
    """发送 Top 10 鲸鱼排行榜"""
    print("🏆 生成 Top 10 鲸鱼排行榜...")
    
    # 获取 Top 10 鲸鱼
    top_whales = get_top_whales(limit=10)
    
    if not top_whales:
        print("⚪ 未获取到鲸鱼数据")
        return
    
    print(f"✅ 获取到 {len(top_whales)} 个鲸鱼数据")
    
    # 打印到控制台
    from whale_tracker_v2 import format_top_whales
    print(format_top_whales(top_whales))
    
    # 发送到 Telegram
    if send_top_whales_report(top_whales):
        print("✅ 已发送 Top 10 鲸鱼排行榜到 Telegram")
    else:
        print("❌ 发送失败")


if __name__ == "__main__":
    main()
