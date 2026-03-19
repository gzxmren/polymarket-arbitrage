#!/usr/bin/env python3
"""
监控告警检查器
检查连续无机会情况并发送告警
"""

import json
import glob
import os
import sys
from datetime import datetime, timedelta
from telegram_notifier_v2 import send_telegram_message

# 配置
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
from config import DATA_DIR
ALERT_THRESHOLD_HOURS = 6  # 连续6小时无机会告警


def check_no_opportunity_alert():
    """检查连续无机会告警"""
    # 读取最近6小时的报告
    cutoff_time = datetime.now() - timedelta(hours=ALERT_THRESHOLD_HOURS)
    
    reports = []
    for f in sorted(glob.glob(f"{DATA_DIR}/monitor_report_*.json"))[-50:]:  # 最近50份
        try:
            with open(f) as fp:
                data = json.load(fp)
                scan_time = data.get("scan_time", "")
                if scan_time:
                    report_time = datetime.fromisoformat(scan_time.replace("+00:00", ""))
                    if report_time > cutoff_time:
                        reports.append({
                            "time": report_time,
                            "pair_cost": data.get("pair_cost", {}).get("count", 0),
                            "cross_market": data.get("cross_market", {}).get("count", 0),
                            "whale": data.get("whale", {}).get("active", 0)
                        })
        except:
            pass
    
    if not reports:
        return
    
    # 检查是否连续无机会
    total_opportunities = sum(r["pair_cost"] + r["cross_market"] for r in reports)
    
    if total_opportunities == 0:
        # 发送告警
        message = f"""🚨 **Polymarket 监控告警**

⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
⚠️ 状态: 连续 {ALERT_THRESHOLD_HOURS} 小时未发现套利机会

📊 最近扫描统计:
- 扫描报告数: {len(reports)} 份
- Pair Cost 机会: 0 个
- 跨平台套利: 0 个
- 活跃鲸鱼: {sum(r['whale'] for r in reports)} 位

🔧 建议措施:
1. 检查市场条件是否正常
2. 考虑调整策略阈值
3. 关注市场波动时段

请检查系统状态。
"""
        send_telegram_message(message)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 告警已发送: 连续{ALERT_THRESHOLD_HOURS}小时无机会")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 检查正常: 最近{len(reports)}份报告共发现{total_opportunities}个机会")


if __name__ == "__main__":
    check_no_opportunity_alert()