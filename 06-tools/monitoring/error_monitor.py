#!/usr/bin/env python3
"""
错误监控器
主动发现错误，立即报告
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

# 加载环境变量
os.environ['TELEGRAM_BOT_TOKEN'] = '8693703622:AAGtlESUqoc4qH7qusEbOlxX8X-mlj2gwyw'
os.environ['TELEGRAM_CHAT_ID'] = '-5052636342'

from telegram_notifier_v2 import send_telegram_message

LOG_DIR = Path("../../07-data/logs")
MONITOR_LOG = LOG_DIR / "monitor.log"
HEARTBEAT_LOG = LOG_DIR / "heartbeat.log"


def check_log_errors(log_file: Path, hours: int = 1):
    """检查日志文件中的错误"""
    if not log_file.exists():
        return []
    
    errors = []
    cutoff_time = datetime.now() - timedelta(hours=hours)
    
    try:
        with open(log_file, 'r') as f:
            for line in f:
                # 检查错误关键词
                if any(keyword in line for keyword in ['Error', 'ERROR', 'Traceback', 'KeyError', 'Exception']):
                    errors.append(line.strip())
    except Exception as e:
        errors.append(f"读取日志失败: {e}")
    
    return errors


def send_error_alert(errors: list, log_name: str):
    """发送错误警报"""
    message = f"""🚨 *程序错误警报*

⏰ 发现时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
📁 日志文件: {log_name}
❌ 错误数量: {len(errors)} 个

*错误详情:*
"""
    
    for i, error in enumerate(errors[:5], 1):  # 只显示前5个
        message += f"{i}. `{error[:100]}...`\n"
    
    if len(errors) > 5:
        message += f"\n... 还有 {len(errors) - 5} 个错误\n"
    
    message += """
⚠️ *请检查程序运行状态！*
"""
    
    send_telegram_message(message)


def run_error_monitor():
    """运行错误监控"""
    all_errors = []
    
    # 检查 monitor.log
    monitor_errors = check_log_errors(MONITOR_LOG, hours=1)
    if monitor_errors:
        all_errors.extend([(e, "monitor.log") for e in monitor_errors])
    
    # 检查 heartbeat.log
    heartbeat_errors = check_log_errors(HEARTBEAT_LOG, hours=1)
    if heartbeat_errors:
        all_errors.extend([(e, "heartbeat.log") for e in heartbeat_errors])
    
    # 发送警报
    if all_errors:
        errors_by_file = {}
        for error, log_name in all_errors:
            if log_name not in errors_by_file:
                errors_by_file[log_name] = []
            errors_by_file[log_name].append(error)
        
        for log_name, errors in errors_by_file.items():
            send_error_alert(errors, log_name)
        
        return True  # 发现错误
    
    return False  # 未发现错误


if __name__ == "__main__":
    found_errors = run_error_monitor()
    if found_errors:
        print(f"发现错误，已发送警报: {datetime.now()}")
    else:
        print(f"未发现错误: {datetime.now()}")
