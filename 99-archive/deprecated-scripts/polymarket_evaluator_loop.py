#!/usr/bin/env python3
"""
Polymarket 持续评估子代理（循环模式）
每小时分析监控数据质量，判断是否需要优化
"""

import os
import json
import time
import re
from datetime import datetime, timedelta
from collections import defaultdict
import urllib.request
import urllib.error

# Telegram 配置
TELEGRAM_BOT_TOKEN = "<REDACTED_TELEGRAM_TOKEN>"
TELEGRAM_CHAT_ID = "1530224854"

# 日志文件路径
MONITOR_LOG_PATH = "/home/xmren/.openclaw/workspace/polymarket-project/07-data/logs/monitor.log"
REPORT_DIR = "/home/xmren/.openclaw/workspace/polymarket-project/07-data/"

# 状态跟踪
STATE_FILE = "/home/xmren/.openclaw/workspace/polymarket_evaluator_state.json"


def load_state():
    """加载评估器状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "consecutive_no_opportunities": 0,
        "last_reports": [],
        "api_error_count": 0,
        "total_checks": 0,
        "start_time": datetime.now().isoformat()
    }


def save_state(state):
    """保存评估器状态"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def send_telegram_message(message):
    """发送 Telegram 消息"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }).encode('utf-8')

        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"发送 Telegram 消息失败: {e}")
        return None


def analyze_last_hour_logs():
    """分析过去1小时的监控日志"""
    if not os.path.exists(MONITOR_LOG_PATH):
        return None

    one_hour_ago = datetime.now() - timedelta(hours=1)

    stats = {
        "report_count": 0,
        "opportunities_found": 0,
        "api_errors": 0,
        "pair_cost_opportunities": 0,
        "cross_market_opportunities": 0,
        "whale_count": 0,
        "market_making_opportunities": 0,
        "reports": []
    }

    try:
        with open(MONITOR_LOG_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # 查找所有报告块
        report_pattern = r'={40,}\s*📈 本次扫描汇总:.*?通过审核:\s*\d+\s*个'
        reports = re.findall(report_pattern, content, re.DOTALL)

        for report in reports:
            # 提取关键数据
            report_data = {}

            # Pair Cost 机会
            pair_cost_match = re.search(r'Pair Cost 机会:\s*(\d+)\s*个', report)
            if pair_cost_match:
                report_data['pair_cost'] = int(pair_cost_match.group(1))

            # 跨平台套利
            cross_match = re.search(r'跨平台套利:\s*(\d+)\s*个', report)
            if cross_match:
                report_data['cross_market'] = int(cross_match.group(1))

            # 活跃鲸鱼
            whale_match = re.search(r'活跃鲸鱼:\s*(\d+)\s*个', report)
            if whale_match:
                report_data['whales'] = int(whale_match.group(1))

            # 做市机会
            mm_match = re.search(r'做市机会:\s*(\d+)\s*个', report)
            if mm_match:
                report_data['market_making'] = int(mm_match.group(1))

            # 通过审核的机会
            approved_match = re.search(r'通过审核:\s*(\d+)\s*个', report)
            if approved_match:
                report_data['approved'] = int(approved_match.group(1))

            stats["reports"].append(report_data)
            stats["report_count"] += 1
            stats["pair_cost_opportunities"] += report_data.get('pair_cost', 0)
            stats["cross_market_opportunities"] += report_data.get('cross_market', 0)
            stats["whale_count"] += report_data.get('whales', 0)
            stats["market_making_opportunities"] += report_data.get('market_making', 0)
            stats["opportunities_found"] += report_data.get('approved', 0)

        # 统计 API 错误
        # 查找过去1小时内的错误
        error_patterns = [
            r'Error fetching',
            r'HTTP Error \d+',
            r'urlopen error',
            r'Traceback',
            r'IncompleteRead'
        ]

        for pattern in error_patterns:
            matches = re.findall(pattern, content)
            stats["api_errors"] += len(matches)

    except Exception as e:
        print(f"分析日志时出错: {e}")
        return None

    return stats


def evaluate_and_recommend(stats, state):
    """评估数据质量并生成建议"""
    recommendations = []
    alerts = []

    if stats is None:
        return ["⚠️ 无法读取监控日志"], []

    # 评估标准 1: 连续无做市机会
    if stats["opportunities_found"] == 0:
        state["consecutive_no_opportunities"] += 1
        if state["consecutive_no_opportunities"] >= 3:
            recommendations.append(
                f"🔴 **建议降低阈值**: 连续 {state['consecutive_no_opportunities']} 次扫描未发现通过审核的机会\n"
                f"   - 当前 Pair Cost 机会: {stats['pair_cost_opportunities']}\n"
                f"   - 做市机会: {stats['market_making_opportunities']}\n"
                f"   - 建议: 降低 Pair Cost 阈值或放宽风险评估标准"
            )
            alerts.append("连续无机会")
    else:
        state["consecutive_no_opportunities"] = 0

    # 评估标准 2: API 错误率
    total_api_calls = stats["report_count"] * 5  # 估算每次扫描约5个API调用
    if total_api_calls > 0:
        error_rate = stats["api_errors"] / total_api_calls
        if error_rate > 0.1:
            recommendations.append(
                f"🔴 **建议修复 API 调用**: API 错误率 {error_rate*100:.1f}% > 10%\n"
                f"   - 错误数: {stats['api_errors']}\n"
                f"   - 常见错误: SSL 证书问题、HTTP 403/404、连接中断"
            )
            alerts.append("API错误率高")

    # 评估标准 3: 报告内容重复
    if len(stats["reports"]) >= 3:
        recent_reports = stats["reports"][-3:]
        # 检查是否高度相似
        pair_cost_values = [r.get('pair_cost', 0) for r in recent_reports]
        cross_values = [r.get('cross_market', 0) for r in recent_reports]

        if len(set(pair_cost_values)) == 1 and len(set(cross_values)) == 1:
            recommendations.append(
                f"🟡 **建议调整监控逻辑**: 最近3次报告内容高度重复\n"
                f"   - Pair Cost 机会均为: {pair_cost_values[0]}\n"
                f"   - 跨平台套利均为: {cross_values[0]}\n"
                f"   - 建议: 增加监控市场数量或调整扫描频率"
            )
            alerts.append("报告重复")

    # 一般性统计
    if not recommendations:
        recommendations.append(
            f"✅ **监控运行正常**\n"
            f"   - 过去1小时报告数: {stats['report_count']}\n"
            f"   - 发现机会: {stats['opportunities_found']}\n"
            f"   - API错误: {stats['api_errors']}"
        )

    return recommendations, alerts


def generate_report(stats, recommendations, alerts, state):
    """生成评估报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"""🦐 **Polymarket 监控质量评估报告** | {now}

📊 **过去1小时统计:**
• 监控报告数: {stats['report_count'] if stats else 'N/A'}
• Pair Cost 机会: {stats['pair_cost_opportunities'] if stats else 'N/A'}
• 跨平台套利: {stats['cross_market_opportunities'] if stats else 'N/A'}
• 活跃鲸鱼: {stats['whale_count'] if stats else 'N/A'}
• 做市机会: {stats['market_making_opportunities'] if stats else 'N/A'}
• 通过审核: {stats['opportunities_found'] if stats else 'N/A'}
• API 错误: {stats['api_errors'] if stats else 'N/A'}

💡 **评估建议:**
"""

    for rec in recommendations:
        report += f"\n{rec}\n"

    if alerts:
        report += f"\n⚠️ **触发警报:** {', '.join(alerts)}\n"

    report += f"\n📈 **累计检查次数:** {state['total_checks']}\n"
    report += f"⏱️ **运行时长:** {state.get('start_time', 'N/A')}\n"

    return report


def log_message(msg):
    """输出日志消息"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg, flush=True)
    # 同时写入文件
    with open('/tmp/polymarket_evaluator.log', 'a') as f:
        f.write(full_msg + '\n')

def main_loop():
    """主循环"""
    log_message("🦐 Polymarket 评估子代理启动...")
    log_message(f"📊 监控日志: {MONITOR_LOG_PATH}")
    log_message(f"📤 Telegram Chat ID: {TELEGRAM_CHAT_ID}")
    log_message("⏱️ 每小时执行一次评估")
    log_message("按 Ctrl+C 停止")

    state = load_state()

    while True:
        try:
            log_message("开始评估...")

            # 1. 分析过去1小时的日志
            stats = analyze_last_hour_logs()

            # 2. 评估并生成建议
            recommendations, alerts = evaluate_and_recommend(stats, state)

            # 3. 更新状态
            state["total_checks"] += 1
            state["last_reports"].append({
                "time": datetime.now().isoformat(),
                "opportunities": stats["opportunities_found"] if stats else 0,
                "api_errors": stats["api_errors"] if stats else 0
            })
            # 只保留最近24条记录
            state["last_reports"] = state["last_reports"][-24:]
            save_state(state)

            # 4. 如果有警报，发送 Telegram 通知
            if alerts:
                report = generate_report(stats, recommendations, alerts, state)
                log_message(f"⚠️ 发现 {len(alerts)} 个问题，发送通知...")
                result = send_telegram_message(report)
                if result:
                    log_message("✅ 通知已发送")
                else:
                    log_message("❌ 通知发送失败")
            else:
                log_message("✅ 监控正常，无问题需要报告")

            # 5. 等待1小时
            log_message("⏱️ 下次评估: 1小时后...")
            time.sleep(3600)

        except KeyboardInterrupt:
            log_message("🛑 收到停止信号，保存状态并退出...")
            save_state(state)
            break
        except Exception as e:
            log_message(f"❌ 错误: {e}")
            import traceback
            log_message(traceback.format_exc())
            time.sleep(60)  # 出错后1分钟重试


if __name__ == "__main__":
    main_loop()