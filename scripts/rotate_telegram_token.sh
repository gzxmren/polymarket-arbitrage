#!/bin/bash
# 一次性轮换 Telegram bot token
#
# 背景: 旧 token 曾硬编码并 push 到公开 GitHub 仓库 → 已公网泄露,且为 OpenClaw 共享 bot。
# 必须在 BotFather 执行 /revoke 后,用本脚本把新 token 写入所有使用点。
#
# 安全设计:
#   - 脚本本身不含任何 token(旧 token 从现有 .env 动态读出作为替换目标)。
#   - 新 token 通过隐藏输入读取,不回显、不进对话记录、不进 git。
#
# 用法(在你自己的终端运行,不要经过 Claude):
#   bash scripts/rotate_telegram_token.sh
#
# 完成后可删除本脚本: rm scripts/rotate_telegram_token.sh

set -euo pipefail

P="/home/xmren/.openclaw/workspace/polymarket-project"
MON_ENV="$P/06-tools/monitoring/.env"

# 旧 token = 当前 .env 里的值(替换目标),脚本不硬编码
OLD=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$MON_ENV" | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "${OLD:-}" ]; then
    echo "❌ 无法从 $MON_ENV 读取当前 token,请检查文件。"
    exit 1
fi

read -rsp "粘贴【新】bot token 后回车(输入不显示): " NEW; echo
if [ -z "${NEW:-}" ]; then
    echo "❌ 未输入,已退出。"
    exit 1
fi
if [ "$NEW" = "$OLD" ]; then
    echo "❌ 新旧 token 相同,未做改动。"
    exit 1
fi

FILES=(
    "$MON_ENV"
    "$P/dashboard/.env"
    "/home/xmren/.openclaw/workspace/config/system_config.json"
    "/home/xmren/.openclaw/workspace/polymarket-monitor-loop.sh"
)

echo "开始替换……"
for f in "${FILES[@]}"; do
    if [ -f "$f" ] && grep -qF "$OLD" "$f"; then
        sed -i "s|$OLD|$NEW|g" "$f"
        echo "  ✅ 已更新 $f"
    else
        echo "  ⏭  跳过(无旧 token 或文件不存在) $f"
    fi
done
unset NEW OLD

echo
echo "✅ 所有使用点已更新。接下来:"
echo "  1) 重启 Dashboard 后端: systemctl --user restart polymarket-dashboard-backend.service"
echo "  2) 验证发消息: 见对话里的验证命令"
echo "  3) 删除本脚本: rm scripts/rotate_telegram_token.sh"
