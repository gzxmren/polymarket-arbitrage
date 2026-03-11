#!/bin/bash
# 设置定时监控任务

echo "🚀 Polymarket 监控定时任务设置"
echo "================================"

# 获取当前目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo ""
echo "📁 工作目录: $WORKSPACE_DIR"
echo ""

# 检查环境变量
echo "🔍 检查配置..."
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
    echo "✅ 配置文件已加载"
else
    echo "❌ 配置文件不存在: $SCRIPT_DIR/.env"
    echo "   请先复制 .env.example 到 .env 并填写配置"
    exit 1
fi

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ "$TELEGRAM_BOT_TOKEN" = "your_bot_token_here" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN 未配置"
    exit 1
fi

if [ -z "$TELEGRAM_CHAT_ID" ] || [ "$TELEGRAM_CHAT_ID" = "your_chat_id_here" ]; then
    echo "❌ TELEGRAM_CHAT_ID 未配置"
    exit 1
fi

echo "✅ Telegram 配置检查通过"
echo ""

# 创建日志目录
mkdir -p "$WORKSPACE_DIR/07-data/logs"

# 生成 crontab 条目
echo "📝 生成定时任务..."
echo ""

CRON_JOB="# Polymarket 监控 - 每5分钟运行一次
*/5 * * * * cd $SCRIPT_DIR && /usr/bin/python3 polymarket_monitor_v2.py >> $WORKSPACE_DIR/07-data/logs/monitor.log 2>&1

# Polymarket 监控 - 每小时汇总报告
0 * * * * cd $SCRIPT_DIR && /usr/bin/python3 -c \"from telegram_notifier_v2 import send_telegram_message; send_telegram_message('📊 每小时心跳: 监控运行中')\" >> $WORKSPACE_DIR/07-data/logs/heartbeat.log 2>&1

# 清理旧日志 - 每天凌晨执行
0 0 * * * find $WORKSPACE_DIR/07-data/logs -name '*.log' -mtime +7 -delete
"

echo "将要添加的定时任务:"
echo "-------------------"
echo "$CRON_JOB"
echo "-------------------"
echo ""

# 询问是否添加
read -p "是否添加到 crontab? (y/n): " confirm

if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
    # 备份现有 crontab
    crontab -l > "$WORKSPACE_DIR/07-data/cron_backup_$(date +%Y%m%d_%H%M%S).txt" 2>/dev/null
    
    # 添加新任务
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    
    echo ""
    echo "✅ 定时任务已添加！"
    echo ""
    echo "📋 当前 crontab:"
    crontab -l | grep -A 10 "Polymarket"
    echo ""
    echo "📊 日志位置: $WORKSPACE_DIR/07-data/logs/"
    echo ""
    echo "💡 管理命令:"
    echo "   查看日志: tail -f $WORKSPACE_DIR/07-data/logs/monitor.log"
    echo "   编辑任务: crontab -e"
    echo "   查看任务: crontab -l"
    echo "   删除任务: crontab -r"
else
    echo ""
    echo "❌ 已取消"
    echo ""
    echo "你可以手动添加以下任务到 crontab:"
    echo "$CRON_JOB"
fi

echo ""
echo "🎉 设置完成！"
