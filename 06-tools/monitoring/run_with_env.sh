#!/bin/bash
# 加载环境变量并运行命令

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 加载 .env 文件
export TELEGRAM_BOT_TOKEN="8693703622:AAGtlESUqoc4qH7qusEbOlxX8X-mlj2gwyw"
export TELEGRAM_CHAT_ID="1530224854"
export NOTIFY_IMMEDIATELY="true"
export RISK_REVIEW_ENABLED="true"
export RISK_REVIEW_THRESHOLD="0.5"

# 执行命令
cd "$SCRIPT_DIR"
python3 "$@"
