#!/bin/bash
# 启动 Polymarket 监控程序

cd /home/xmren/.openclaw/workspace/polymarket-project/06-tools/monitoring

# 设置环境
export PYTHONPATH=/home/xmren/.openclaw/workspace/polymarket-project/06-tools/analysis:$PYTHONPATH

# 启动监控
echo "🚀 启动 Polymarket 监控程序..."
nohup python3 polymarket_monitor_v2.py > /tmp/monitor.log 2>&1 &

sleep 3

# 检查是否启动
if ps aux | grep -v grep | grep "polymarket_monitor_v2" > /dev/null; then
    echo "✅ 监控程序已启动"
    echo "日志: tail -f /tmp/monitor.log"
else
    echo "❌ 启动失败，检查日志:"
    tail -20 /tmp/monitor.log
fi