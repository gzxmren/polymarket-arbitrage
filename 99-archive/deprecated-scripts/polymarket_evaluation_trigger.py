#!/usr/bin/env python3
"""
Polymarket 评价任务触发器
每小时运行，触发主Agent执行评价
"""

import os
import json
import urllib.request
from datetime import datetime

# 配置
TELEGRAM_TOKEN = "<REDACTED_TELEGRAM_TOKEN>"
TELEGRAM_CHAT_ID = "-5052636342"  # 菜园子

def send_telegram(message):
    """发送 Telegram 消息触发主Agent"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }).encode()
        
        req = urllib.request.Request(
            url, data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"发送失败: {e}")
        return False

def main():
    """主函数 - 触发评价任务"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    message = f"""🦐 **Polymarket 评价任务触发** | {timestamp}

请执行以下任务：
1. 读取监控日志（最近1小时）
2. 分析监控数据质量
3. 生成评价报告
4. 发送到本群

监控日志位置：
`/home/xmren/.openclaw/workspace/polymarket-project/07-data/logs/monitor.log`

请回复评价结果。"""
    
    if send_telegram(message):
        print(f"[{timestamp}] 评价任务触发成功")
    else:
        print(f"[{timestamp}] 评价任务触发失败")

if __name__ == "__main__":
    main()
