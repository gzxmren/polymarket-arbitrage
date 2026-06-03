#!/bin/bash
# 加载环境变量并运行命令

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 加载 .env 文件（[P2安全] 2026-06-03: 不再硬编码密钥，改为 source .env）
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    . "$SCRIPT_DIR/.env"
    set +a
fi

# 执行命令
cd "$SCRIPT_DIR"
python3 "$@"
