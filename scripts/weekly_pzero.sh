#!/usr/bin/env bash
# 每周自动跑一次 P0-B OOS 验证，追踪 edge 随数据积累的改善趋势。
# OpenClaw cron: polymarket-pzero-weekly, 每周一 09:30 CST

set -euo pipefail
cd "$(dirname "$0")/.."

TS=$(date +%Y%m%d_%H%M%S)
# 自动用"3 周前"作为 cutoff，保证 test 段有 ~21 天数据
CUTOFF=$(date -d "21 days ago" +%Y-%m-%d 2>/dev/null || date -v-21d +%Y-%m-%d)

echo "[$(date)] P0-B 周复验 cutoff=${CUTOFF}"

# 完整输出写到文件，不输出到 stdout（避免 model 收到巨量 token 后 abort）
# 注意：set -e 下不能让这条命令直接决定脚本生死，否则失败时 Telegram 通知和下方
# 日志行都不会执行——用 set +e 包住，显式捕获退出码后再恢复 set -e。
set +e
PYTHONPATH=08-backtests python3 08-backtests/run_pzero_oos.py \
    --cutoff "$CUTOFF" \
    --json \
    --contrarian \
    > /tmp/pzero_last_run.txt 2>&1
EXIT_CODE=$?
set -e

if [ "$EXIT_CODE" -ne 0 ]; then
    echo "[$(date)] ❌ 失败 (exit=${EXIT_CODE}) cutoff=${CUTOFF}"
    tail -20 /tmp/pzero_last_run.txt
    PASS=0
    RESULT_FILE=""
    VERDICT="❌ run_pzero_oos.py 失败 (exit=${EXIT_CODE})，见 /tmp/pzero_last_run.txt"
else
    # 向 model 只输出摘要（判决行 + 结果文件路径）
    PASS=$(grep -c "🟢 候选" /tmp/pzero_last_run.txt 2>/dev/null || echo 0)
    RESULT_FILE=$(grep "结果已保存:" /tmp/pzero_last_run.txt 2>/dev/null | tail -1 || echo "")
    VERDICT=$(grep -A2 "⚖️ 诚实判决" /tmp/pzero_last_run.txt 2>/dev/null | head -4 || echo "")

    echo "[$(date)] 完成 (exit=${EXIT_CODE}) cutoff=${CUTOFF} 通过候选=${PASS}"
    echo "${RESULT_FILE}"
    echo "${VERDICT}"
fi

# Telegram 推送：从 monitoring/.env 读 token，从 openclaw.json 读 chat_id
ENV_FILE="$(dirname "$0")/../06-tools/monitoring/.env"
OPENCLAW_JSON="${HOME}/.openclaw/openclaw.json"
if [ -f "$ENV_FILE" ]; then source "$ENV_FILE"; fi
if [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    TELEGRAM_CHAT_ID=$(python3 -c "import json; d=json.load(open('${OPENCLAW_JSON}')); print(d.get('telegramChatId',''))" 2>/dev/null || echo '')
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    if [ "$EXIT_CODE" -ne 0 ]; then
        MSG="🔴 P0-B 周复验 ${TS}
${VERDICT}"
    else
        MSG="📊 P0-B 周复验 ${TS}
cutoff=${CUTOFF}
通过候选=${PASS} 个
${RESULT_FILE}"
    fi
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${MSG}" \
        > /dev/null || true
fi
