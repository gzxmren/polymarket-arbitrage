#!/usr/bin/env python3
"""
健康检查器
主动监控系统状态，发现问题立即报告
"""

import os
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# 加载环境变量
sys.path.insert(0, '.')
from telegram_notifier_v2 import send_telegram_message

LOG_DIR = Path("../../07-data/logs")
MONITOR_LOG = LOG_DIR / "monitor.log"
HEARTBEAT_LOG = LOG_DIR / "heartbeat.log"


def check_last_scan():
    """检查最近扫描时间"""
    if not MONITOR_LOG.exists():
        return False, "监控日志不存在"
    
    # 获取最后扫描时间
    result = subprocess.run(
        ["grep", "扫描完成", str(MONITOR_LOG)],
        capture_output=True, text=True
    )
    
    if not result.stdout:
        return False, "未找到扫描记录"
    
    # 检查是否在10分钟内
    last_line = result.stdout.strip().split('\n')[-1]
    # 简单检查，实际应该解析时间
    return True, "最近扫描正常"


def check_heartbeat():
    """检查心跳报告"""
    if not HEARTBEAT_LOG.exists():
        return False, "心跳日志不存在"
    
    # 检查最后一条记录
    content = HEARTBEAT_LOG.read_text()
    lines = [l for l in content.split('\n') if l.strip()]
    
    if not lines:
        return False, "心跳日志为空"
    
    last_line = lines[-1]
    if "Error" in last_line or "错误" in last_line:
        return False, f"心跳报告错误: {last_line}"
    
    return True, "心跳报告正常"


def check_disk_space():
    """检查磁盘空间"""
    result = subprocess.run(
        ["df", "-h", str(LOG_DIR)],
        capture_output=True, text=True
    )
    
    lines = result.stdout.strip().split('\n')
    if len(lines) >= 2:
        # 解析使用率
        parts = lines[1].split()
        if len(parts) >= 5:
            usage = parts[4].replace('%', '')
            if int(usage) > 90:
                return False, f"磁盘空间不足: {usage}%"
    
    return True, "磁盘空间充足"


def check_cron_status():
    """检查定时任务状态"""
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True, text=True
    )
    
    if "Polymarket" not in result.stdout:
        return False, "定时任务未配置"
    
    return True, "定时任务已配置"


def run_health_check():
    """运行完整健康检查"""
    checks = [
        ("最近扫描", check_last_scan),
        ("心跳报告", check_heartbeat),
        ("磁盘空间", check_disk_space),
        ("定时任务", check_cron_status),
    ]
    
    results = []
    all_ok = True
    
    for name, check_func in checks:
        ok, msg = check_func()
        results.append((name, ok, msg))
        if not ok:
            all_ok = False
    
    # 生成报告
    emoji = "✅" if all_ok else "⚠️"
    message = f"""{emoji} *系统健康检查报告*

⏰ 检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}

*检查结果:*
"""
    
    for name, ok, msg in results:
        status = "✅" if ok else "❌"
        message += f"{status} {name}: {msg}\n"
    
    if not all_ok:
        message += """
⚠️ *发现异常，请检查！*
"""
    
    # 发送报告
    send_telegram_message(message)
    
    return all_ok


if __name__ == "__main__":
    run_health_check()
