#!/usr/bin/env python3
"""
发送工作总结报告
硬编码环境变量，确保可靠
"""

import os
import sys
from datetime import datetime

# 硬编码环境变量
os.environ['TELEGRAM_BOT_TOKEN'] = '8693703622:AAGtlESUqoc4qH7qusEbOlxX8X-mlj2gwyw'
os.environ['TELEGRAM_CHAT_ID'] = '1530224854'
os.environ['NOTIFY_IMMEDIATELY'] = 'true'
os.environ['RISK_REVIEW_ENABLED'] = 'true'
os.environ['RISK_REVIEW_THRESHOLD'] = '0.5'

from telegram_notifier_v2 import send_summary_report

# 生成报告数据
report = {
    'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    'pair_cost_count': 0,  # 从实际数据读取
    'cross_market_count': 0,
    'active_whales': 0,
    'markets_scanned': 200,
    'total_opportunities': 0,
    'avg_pair_cost': 1.0
}

# 读取最新报告数据
try:
    import json
    from pathlib import Path
    
    log_dir = Path('../../07-data')
    reports = sorted(log_dir.glob('monitor_report_*.json'), reverse=True)
    
    if reports:
        with open(reports[0]) as f:
            data = json.load(f)
            report['pair_cost_count'] = data.get('pair_cost', {}).get('approved_count', 0)
            report['cross_market_count'] = data.get('cross_market', {}).get('approved_count', 0)
            report['active_whales'] = data.get('whale', {}).get('active', 0)
            report['total_opportunities'] = data.get('summary', {}).get('approved_opportunities', 0)
except Exception as e:
    print(f"读取报告失败: {e}", file=sys.stderr)

# 发送报告
send_summary_report(report)
print(f"工作总结报告已发送: {report['scan_time']}")
