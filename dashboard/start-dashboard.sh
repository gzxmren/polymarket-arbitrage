#!/bin/bash
# Dashboard 自动守护脚本
# 每 5 分钟检查一次服务状态，如果停止则自动重启

LOG_FILE="/tmp/dashboard-guardian.log"
BACKEND_PID_FILE="/tmp/dashboard-backend.pid"
FRONTEND_PID_FILE="/tmp/dashboard-frontend.pid"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

check_and_restart_backend() {
    if ! pgrep -f "python3 run.py" > /dev/null 2>&1; then
        log "⚠️  后端服务已停止，正在重启..."
        cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend
        source ../venv/bin/activate
        nohup python3 run.py > ../backend.log 2>&1 &
        echo $! > "$BACKEND_PID_FILE"
        sleep 3
        if pgrep -f "python3 run.py" > /dev/null 2>&1; then
            log "✅ 后端服务已重启"
        else
            log "❌ 后端服务重启失败"
        fi
    fi
}

check_and_restart_frontend() {
    if ! pgrep -f "react-scripts start" > /dev/null 2>&1; then
        log "⚠️  前端服务已停止，正在重启..."
        cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend
        PORT=3001 BROWSER=none nohup npm start > ../frontend.log 2>&1 &
        echo $! > "$FRONTEND_PID_FILE"
        sleep 10
        if pgrep -f "react-scripts start" > /dev/null 2>&1; then
            log "✅ 前端服务已重启"
        else
            log "❌ 前端服务重启失败"
        fi
    fi
}

# 主循环
log "🚀 Dashboard 守护进程启动"

while true; do
    check_and_restart_backend
    check_and_restart_frontend
    sleep 300  # 每 5 分钟检查一次
done
