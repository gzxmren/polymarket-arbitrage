#!/usr/bin/env python3
"""
Polymarket 学习助手 - 持续运行版
连接到 GoGo_goclaw_bot，持续响应用户消息
"""

import json
import time
import sys
import ssl
import urllib.request
import urllib.error
from datetime import datetime

# Telegram 配置
BOT_TOKEN = "<REDACTED_TELEGRAM_TOKEN>"
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# SSL 上下文
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# 知识库
KNOWLEDGE = {
    "pair_cost": {
        "title": "Pair Cost 套利（无风险）",
        "content": """
**原理：**
- YES + NO 应该 = $1
- 如果 YES + NO < $1，就有套利机会

**操作：**
1. 同时买入 YES 和 NO
2. 等待市场结算
3. 无论结果，获得 $1

**利润计算：**
- 利润 = $1.00 - Pair Cost
- 例子：YES=$0.60, NO=$0.35
- Pair Cost = $0.95
- 利润 = $0.05 (5.3%)

**为什么有机会？**
- 大家只买 YES，推高价格
- NO 被忽视，价格偏低
- 流动性不平衡
"""
    },
    "market_making": {
        "title": "做市策略（赚价差）",
        "content": """
**原理：**
像小贩低买高卖，赚取买卖价差

**操作：**
1. 挂买单：$0.605（比买一高）
2. 挂卖单：$0.615（比卖一低）
3. 两边成交，赚 $0.01

**风险：**
- 单边成交：只有一边成交，暴露方向风险
- 价格波动：可能亏损
- 流动性不足：无法成交

**风险控制：**
- 设置止损（-5% 或 -8%）
- 选择高流动性市场（> $100K/天）
- 控制仓位（< 10%）
"""
    },
    "difference": {
        "title": "价差 vs Pair Cost 区别",
        "content": """
| 类型 | 价差（做市） | Pair Cost（套利） |
|:---|:---|:---|
| 计算 | best_ask - best_bid | YES_price + NO_price |
| 策略 | 低买高卖 | 买两边，结算赚 |
| 风险 | 单边成交风险 | 几乎无风险 |
| 利润来源 | 买卖价差 | 价格回归 $1 |

**简单记忆：**
- 价差 = 做市商赚差价
- Pair Cost = 套利者赚定价错误
"""
    },
    "risk_management": {
        "title": "风险控制",
        "content": """
**1. 流动性检查**
- 24h 成交量 > $100K
- 确保能进出

**2. 止损设置**
- 亏损 5-8% 自动平仓
- 避免大亏

**3. 仓位控制**
- 单笔 < 总资金 10%
- 分散风险

**4. 单边成交处理**
- 买了 YES 没卖出：等涨或止损
- 卖了 YES 没买回：等跌或止损

**5. 选择稳定事件**
- 时间明确
- 无重大新闻即将发布
"""
    },
    "examples": {
        "title": "实际案例",
        "content": """
**BitBoy convicted?**
- 价差：1.60%
- 成交量：$87K
- 类型：法律事件

**俄乌停火 before GTA VI?**
- 价差：1.00%
- 成交量：$1.37M
- 类型：地缘政治

**Carti 专辑 before GTA VI?**
- 价差：3.00% ⭐
- 成交量：$682K
- 类型：娱乐事件

**分析：**
- 价差大 ≠ 一定好
- 要看流动性和事件确定性
"""
    }
}


def log(msg):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")
    sys.stdout.flush()


def telegram_api(method, data=None):
    """调用 Telegram API"""
    try:
        url = f"{API_BASE}/{method}"
        if data:
            data = json.dumps(data).encode('utf-8')
            req = urllib.request.Request(
                url, data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
        else:
            req = urllib.request.Request(url)
        
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        log(f"API 错误: {e}")
        return None


def get_updates(offset=None):
    """获取新消息"""
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    
    result = telegram_api("getUpdates", params)
    if result and result.get("ok"):
        return result.get("result", [])
    return []


def send_message(chat_id, text):
    """发送消息"""
    return telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    })


def handle_message(message):
    """处理用户消息"""
    chat_id = message["chat"]["id"]
    text = message.get("text", "").lower()
    
    log(f"收到消息: {text[:50]}")
    
    # 关键词匹配
    if any(word in text for word in ["pair", "cost", "套利"]):
        response = KNOWLEDGE["pair_cost"]["content"]
    elif any(word in text for word in ["做市", "market", "价差"]):
        response = KNOWLEDGE["market_making"]["content"]
    elif any(word in text for word in ["区别", "不同", "对比"]):
        response = KNOWLEDGE["difference"]["content"]
    elif any(word in text for word in ["风险", "止损", "控制"]):
        response = KNOWLEDGE["risk_management"]["content"]
    elif any(word in text for word in ["案例", "例子", "实际"]):
        response = KNOWLEDGE["examples"]["content"]
    elif any(word in text for word in ["你好", "hi", "hello", "开始"]):
        response = """你好！我是 Polymarket 学习助手 🦐

我可以帮你理解：
• **Pair Cost 套利**（无风险）- 买 YES + NO，结算稳赚
• **做市策略**（赚价差）- 低买高卖，赚取买卖差价  
• **风险控制** - 止损、仓位管理、流动性检查
• **实际案例** - 真实市场数据解读

直接问我你想了解的内容，比如：
- "什么是 Pair Cost？"
- "做市怎么做？"
- "有什么区别？"
- "风险控制怎么做？"
- "有什么案例？"
"""
    else:
        response = """我不太明白你的问题 🤔

你可以问：
• Pair Cost 套利是什么？
• 做市策略怎么做？
• 价差和 Pair Cost 有什么区别？
• 如何控制风险？
• 有什么实际案例？

或者输入 "你好" 查看完整菜单。
"""
    
    send_message(chat_id, response)
    log("回复已发送")


def main():
    """主循环"""
    log("学习助手启动，连接到 GoGo_goclaw_bot")
    
    # 发送启动通知
    telegram_api("sendMessage", {
        "chat_id": "1530224854",
        "text": "🦐 Polymarket 学习助手已启动！\n\n请在 @GoGo_goclaw_bot 中开始对话。"
    })
    
    offset = None
    
    while True:
        try:
            updates = get_updates(offset)
            
            for update in updates:
                offset = update["update_id"] + 1
                
                if "message" in update:
                    handle_message(update["message"])
            
            time.sleep(1)  # 避免频繁请求
            
        except Exception as e:
            log(f"错误: {e}")
            time.sleep(5)  # 出错后等待5秒


if __name__ == "__main__":
    main()
