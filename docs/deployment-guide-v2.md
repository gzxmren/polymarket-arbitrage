# Polymarket Dashboard 部署指南

## 快速开始（Docker部署）

### 1. 安装Docker
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin

# CentOS
sudo yum install -y docker docker-compose-plugin
sudo systemctl start docker
```

### 2. 配置环境变量
```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard

# 编辑环境变量
vim .env
```

内容：
```
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=-5052636342
FLASK_ENV=production
SECRET_KEY=change_this_key
```

### 3. 启动服务
```bash
# 构建并启动
docker-compose up -d --build

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f
```

### 4. 访问服务
- Web界面: http://your-server-ip
- API: http://your-server-ip/api/

## 手动部署

### 后端部署
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

### 前端部署
```bash
cd frontend
npm install
npm run build
npx serve -s build -l 3000
```

## 常用命令

| 命令 | 说明 |
|-----|------|
| `make deploy` | Docker部署 |
| `make dev` | 开发模式 |
| `make logs` | 查看日志 |
| `make backup` | 备份数据库 |
| `make update` | 更新代码 |

## 故障排除

### 服务无法启动
```bash
docker-compose logs
```

### 数据库问题
```bash
docker-compose exec backend python3 -c "from app.models.database import db; db.init_db()"
```

### 端口占用
```bash
sudo lsof -i :5000
sudo lsof -i :80
```

## 生产环境配置

### SSL证书
```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### 自动重启
```bash
sudo systemctl enable polymarket-dashboard
sudo systemctl start polymarket-dashboard
```

### 备份
```bash
# 手动备份
cp database/polymarket.db backups/polymarket.db.$(date +%Y%m%d)

# 自动备份（crontab）
0 0 * * * cp /path/to/database/polymarket.db /path/to/backups/polymarket.db.$(date +\%Y\%m\%d)
```

## 升级更新

```bash
# 1. 备份
cp database/polymarket.db database/polymarket.db.backup

# 2. 更新代码
git pull

# 3. 重新部署
docker-compose up -d --build
```

## 联系方式

有问题请查看详细文档: `DEPLOY.md`

---

## Docker组件详解

### 部署的组件列表

#### 1. 后端服务 (Backend)
```
容器名: dashboard_backend_1
镜像: 基于Python 3.11
端口: 5000
功能:
├── Flask API服务
├── SQLite数据库
├── 数据同步服务
├── 定时任务调度
└── WebSocket实时推送
```

#### 2. 前端服务 (Frontend)
```
容器名: dashboard_frontend_1
镜像: 基于Node.js 18
端口: 3000 (开发) / 80 (生产)
功能:
├── React应用
├── 静态文件服务
└── 构建后的生产代码
```

#### 3. Nginx反向代理
```
容器名: dashboard_nginx_1
镜像: nginx:alpine
端口: 80 (HTTP) / 443 (HTTPS)
功能:
├── 反向代理到后端API
├── 静态文件服务
├── WebSocket支持
├── SSL/TLS终端
└── 负载均衡
```

#### 4. 数据卷 (Volumes)
```
持久化存储:
├── database/          → SQLite数据库文件
├── 07-data/          → 监控数据 (只读挂载)
│   ├── whale_states/  → 鲸鱼状态文件
│   └── logs/          → 监控日志
└── backups/          → 备份文件
```

### 组件关系图

```
┌─────────────────────────────────────────┐
│           用户浏览器                      │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  Nginx (端口80)                         │
│  ├── 静态文件服务 (前端)                  │
│  ├── /api/* → 后端代理                   │
│  └── /socket.io/* → WebSocket代理        │
└──────────────┬──────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
┌────────────┐   ┌────────────┐
│  Frontend  │   │  Backend   │
│  (React)   │   │  (Flask)   │
│  端口:3000 │   │  端口:5000 │
└────────────┘   └─────┬──────┘
                       │
                       ▼
               ┌────────────┐
               │  SQLite    │
               │  数据库     │
               └────────────┘
```

### 启动顺序

```
1. 启动 backend
   └── 初始化数据库
   └── 启动API服务

2. 启动 frontend
   └── 构建React应用
   └── 启动静态服务器

3. 启动 nginx
   └── 配置反向代理
   └── 暴露80端口
```

### 数据持久化

| 数据类型 | 存储位置 | 说明 |
|---------|---------|------|
| 数据库 | ./database | SQLite文件 |
| 鲸鱼状态 | ../07-data/whale_states | 只读挂载 |
| 日志 | ../07-data/logs | 只读挂载 |
| 备份 | ./backups | 手动创建 |

