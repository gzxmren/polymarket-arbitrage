# Polymarket Dashboard 启动指令文档

> 快速启动指南 - 后端API + 前端界面

---

## 📋 目录

1. [快速启动](#快速启动)
2. [手动启动](#手动启动)
3. [后台运行](#后台运行)
4. [停止服务](#停止服务)
5. [查看状态](#查看状态)
6. [故障排除](#故障排除)

---

## 快速启动

### 一键启动脚本

```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard
./start.sh
```

**访问地址**:
- 前端界面: http://localhost:3001
- 后端API: http://localhost:5000

---

## 手动启动

### 1. 启动后端

```bash
# 进入项目目录
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard

# 激活虚拟环境
source venv/bin/activate

# 进入后端目录
cd backend

# 启动后端服务
python3 run.py
```

**访问地址**: http://localhost:5000

### 2. 启动前端

```bash
# 进入前端目录
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend

# 启动前端开发服务器（使用3001端口）
PORT=3001 BROWSER=none npm start
```

**访问地址**: http://localhost:3001

---

## 后台运行

### 后端后台运行

```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend
source ../venv/bin/activate
nohup python3 run.py > ../backend.log 2>&1 &
```

### 前端后台运行

```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend
PORT=3001 BROWSER=none nohup npm start > ../frontend.log 2>&1 &
```

### 查看日志

```bash
# 后端日志
tail -f /home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend.log

# 前端日志
tail -f /home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend.log
```

---

## 停止服务

### 停止后端

```bash
pkill -f "python3 run.py"
```

### 停止前端

```bash
pkill -f "react-scripts start"
```

### 一键停止所有

```bash
pkill -f "python3 run.py" && pkill -f "react-scripts start"
```

---

## 查看状态

### 检查后端API

```bash
curl http://localhost:5000/api/summary/
```

### 检查前端界面

```bash
curl http://localhost:3001
```

### 查看运行进程

```bash
ps aux | grep -E "(python3 run.py|react-scripts)" | grep -v grep
```

---

## 故障排除

### 端口被占用

```bash
# 检查端口
sudo lsof -i :5000  # 后端端口
sudo lsof -i :3001  # 前端端口

# 杀死占用进程
kill -9 <PID>
```

### 后端启动失败

```bash
# 检查日志
cat /home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend.log

# 重新初始化数据库
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend
source ../venv/bin/activate
python3 -c "from app.models.database import db; db.init_db()"
```

### 前端编译错误

```bash
# 清除缓存
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend
rm -rf node_modules package-lock.json
npm install
npm start
```

---

## 一键启动脚本

创建 `start.sh`:

```bash
cat > /home/xmren/.openclaw/workspace/polymarket-project/dashboard/start.sh << 'EOF'
#!/bin/bash

echo "🚀 启动 Polymarket Dashboard..."

# 启动后端
echo "📡 启动后端服务..."
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend
source ../venv/bin/activate
nohup python3 run.py > ../backend.log 2>&1 &
echo "✅ 后端已启动: http://localhost:5000"

# 等待后端启动
sleep 5

# 启动前端
echo "🎨 启动前端服务..."
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend
PORT=3001 BROWSER=none nohup npm start > ../frontend.log 2>&1 &
echo "✅ 前端已启动: http://localhost:3001"

echo ""
echo "📊 服务状态:"
echo "  后端API: http://localhost:5000"
echo "  前端界面: http://localhost:3001"
echo ""
echo "📝 查看日志:"
echo "  tail -f backend.log"
echo "  tail -f frontend.log"
EOF

chmod +x /home/xmren/.openclaw/workspace/polymarket-project/dashboard/start.sh
```

---

## 服务地址汇总

| 服务 | 地址 | 说明 |
|-----|------|------|
| 前端界面 | http://localhost:3001 | React应用 |
| 后端API | http://localhost:5000 | Flask API |
| API文档 | http://localhost:5000/api/ | REST API |

---

*最后更新: 2026-03-15*
