#!/usr/bin/env bash
# 每周自动跑一次 P0-B OOS 验证，追踪 edge 随数据积累的改善趋势。
# 建议 crontab: 0 9 * * 1 /path/to/scripts/weekly_pzero.sh >> /tmp/pzero_weekly.log 2>&1

set -euo pipefail
cd "$(dirname "$0")/.."

TS=$(date +%Y%m%d_%H%M%S)
# 自动用"3 周前"作为 cutoff，保证 test 段有 ~21 天数据
CUTOFF=$(date -d "21 days ago" +%Y-%m-%d 2>/dev/null || date -v-21d +%Y-%m-%d)

OUT="08-backtests/results/pzero_weekly_${TS}.json"

echo "[$(date)] P0-B 周复验 cutoff=${CUTOFF}"

PYTHONPATH=08-backtests python3 08-backtests/run_pzero_oos.py \
    --cutoff "$CUTOFF" \
    --json \
    --contrarian \
    2>&1 | tee /tmp/pzero_last_run.txt

# 也保存 json（run_pzero_oos.py 自己写到 results/pzero_oos_*.json）
echo "[$(date)] 完成。结果见 08-backtests/results/"

# 简短摘要推送到 Telegram（若已配置）
if [ -f .env ]; then
    source .env
fi
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    PASS=$(grep -c "🟢 候选" /tmp/pzero_last_run.txt 2>/dev/null || echo 0)
    MSG="📊 P0-B 周复验 ${TS}\ncutoff=${CUTOFF}\n通过候选=${PASS} 个\n详见 08-backtests/results/"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="${MSG}" \
        -d parse_mode="Markdown" > /dev/null || true
fi
