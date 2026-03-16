# Polymarket Dashboard 部署完全指南

> 从零开始部署 Polymarket 监控仪表盘
> 版本: v1.0
> 更新时间: 2026-03-15

---

## 📋 目录

1. [部署前准备](#部署前准备)
2. [环境要求](#环境要求)
3. [部署方式选择](#部署方式选择)
4. [Docker 部署（推荐）](#docker-部署推荐)
5. [手动部署](#手动部署)
6. [生产环境配置](#生产环境配置)
7. [验证测试](#验证测试)
8. [监控维护](#监控维护)
9. [故障排除](#故障排除)
10. [升级更新](#升级更新)

---

## 部署前准备

### 1. 确认部署目标

**选择部署方式**:

| 方式 | 适用场景 | 复杂度 | 维护难度 |
|-----|---------|--------|---------|
| Docker | 生产环境、快速部署 | 低 | 低 |
| 手动 | 开发测试、学习研究 | 中 | 中 |
| 云服务 | 大规模、高可用 | 高 | 高 |

**本指南推荐**: Docker 部署

### 2. 准备必要信息

**必须准备**:
- [ ] 服务器/主机（物理机或云服务器）
- [ ] 域名（可选，用于HTTPS）
- [ ] Telegram Bot Token（用于通知）
- [ ] 服务器SSH访问权限

**可选准备**:
- [ ] SSL证书（用于HTTPS）
- [ ] 监控告警系统
- [ ] 备份存储空间

---

## 环境要求

### 硬件要求

| 组件 | 最低配置 | 推荐配置 | 说明 |
|-----|---------|---------|------|
| CPU | 2核 | 4核 | 处理实时数据 |
| 内存 | 2GB | 4GB | 运行容器和数据库 |
| 磁盘 | 20GB | 50GB | 存储数据和日志 |
| 网络 | 10Mbps | 100Mbps | API调用和数据同步 |

### 软件要求

**必须安装**:
- Docker 20.10+
- Docker Compose 2.0+
- Git（用于代码更新）

**可选安装**:
- Nginx（如果不使用Docker Nginx）
- Certbot（用于HTTPS证书）

### 操作系统支持

- ✅ Ubuntu 20.04/22.04
- ✅ CentOS 7/8
- ✅ Debian 10/11
- ✅ macOS（开发测试）

---

## 部署方式选择

### 方式对比

```
┌─────────────────────────────────────────────────────────────┐
│                    部署方式选择                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  🐳 Docker 部署 (推荐)                                      │
│  ├─ 优点: 快速、一致、易维护                                 │
│  ├─ 缺点: 需要Docker知识                                    │
│  └─ 适用: 生产环境、快速启动                                 │
│                                                             │
│  🔧 手动部署                                                │
│  ├─ 优点: 可控、灵活                                        │
│  ├─ 缺点: 复杂、易出错                                      │
│  └─ 适用: 开发测试、学习研究                                 │
│                                                             │
│  ☁️  云服务部署                                              │
│  ├─ 优点: 高可用、弹性扩展                                   │
│  ├─ 缺点: 成本高、复杂                                       │
│  └─ 适用: 大规模生产环境                                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Docker 部署（推荐）

### 1. 安装 Docker

#### Ubuntu/Debian
```bash
# 更新包索引
sudo apt-get update

# 安装依赖
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release

# 添加Docker官方GPG密钥
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# 添加Docker软件源
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装Docker
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 验证安装
sudo docker --version
sudo docker compose version
```

#### CentOS/RHEL
```bash
# 安装依赖
sudo yum install -y yum-utils

# 添加Docker软件源
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# 安装Docker
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 启动Docker
sudo systemctl start docker
sudo systemctl enable docker

# 验证安装
sudo docker --version
sudo docker compose version
```

### 2. 配置 Docker

```bash
# 将当前用户添加到docker组（避免每次使用sudo）
sudo usermod -aG docker $USER

# 重新登录生效
newgrp docker

# 验证
docker ps
```

### 3. 准备项目代码

```bash
# 进入项目目录
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard

# 确认文件存在
ls -la docker-compose.yml
ls -la backend/Dockerfile
ls -la frontend/Dockerfile
```

### 4. 配置环境变量

```bash
# 创建环境变量文件
cat > .env << 'EOF'
# Telegram 配置（必须）
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=-5052636342

# 数据库配置
DATABASE_PATH=/app/database/polymarket.db

# 监控数据路径
MONITOR_LOG_PATH=/app/data/logs/monitor.log
WHALE_STATES_PATH=/app/data/whale_states/

# Flask配置
FLASK_ENV=production
SECRET_KEY=your-secret-key-change-this-in-production

# 前端配置
REACT_APP_API_URL=http://localhost:5000
REACT_APP_SOCKET_URL=http://localhost:5000
EOF

# 设置权限
chmod 600 .env
```

**⚠️ 重要**: 修改 `.env` 文件中的敏感信息

### 5. 构建并启动服务

```bash
# 构建并启动所有服务
docker-compose up -d --build

# 查看构建过程
docker-compose build --no-cache

# 启动服务
docker-compose up -d
```

**首次启动可能需要5-10分钟**（下载镜像+构建）

### 6. 验证服务状态

```bash
# 查看运行中的容器
docker-compose ps

# 预期输出:
# NAME                    STATUS
# dashboard_backend_1     Up
# dashboard_frontend_1    Up
# dashboard_nginx_1       Up

# 查看日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f backend
docker-compose logs -f frontend
```

### 7. 初始化数据

```bash
# 进入后端容器
docker-compose exec backend bash

# 初始化数据库
python3 -c "from app.models.database import db; db.init_db()"

# 同步现有数据
python3 app/services/data_sync.py

# 退出容器
exit
```

### 8. 访问服务

```
Web界面: http://your-server-ip
API文档: http://your-server-ip/api/
```

---

## 手动部署

### 1. 后端部署

#### 1.1 创建虚拟环境
```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 验证
which python
```

#### 1.2 安装依赖
```bash
# 升级pip
pip install --upgrade pip

# 安装依赖
pip install -r requirements.txt

# 验证安装
pip list
```

#### 1.3 配置环境变量
```bash
# 创建环境变量文件
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=-5052636342
DATABASE_PATH=database/polymarket.db
FLASK_ENV=production
SECRET_KEY=your-secret-key
EOF
```

#### 1.4 初始化数据库
```bash
# 创建数据库目录
mkdir -p database

# 初始化数据库表
python3 -c "from app.models.database import db; db.init_db()"

# 验证数据库创建
ls -la database/
```

#### 1.5 启动服务
```bash
# 开发模式
python3 run.py

# 生产模式（使用Gunicorn）
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"
```

### 2. 前端部署

#### 2.1 安装Node.js
```bash
# 使用nvm安装（推荐）
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
source ~/.bashrc
nvm install 18
nvm use 18

# 验证
node --version
npm --version
```

#### 2.2 安装依赖
```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend

# 安装依赖
npm install

# 验证
ls node_modules | head -5
```

#### 2.3 配置API地址
```bash
# 创建环境变量文件
cat > .env << 'EOF'
REACT_APP_API_URL=http://localhost:5000
REACT_APP_SOCKET_URL=http://localhost:5000
EOF
```

#### 2.4 构建生产版本
```bash
# 构建
npm run build

# 验证构建
ls build/
ls build/static/
```

#### 2.5 启动服务
```bash
# 使用serve启动（需要安装）
npm install -g serve
serve -s build -l 3000

# 或使用npx
npx serve -s build -l 3000
```

### 3. Nginx配置

```bash
# 安装Nginx
sudo apt-get install -y nginx

# 复制配置文件
sudo cp nginx.conf /etc/nginx/nginx.conf

# 测试配置
sudo nginx -t

# 重启Nginx
sudo systemctl restart nginx

# 验证
sudo systemctl status nginx
```

---

## 生产环境配置

### 1. 系统优化

#### 1.1 文件描述符限制
```bash
# 编辑系统配置
sudo vim /etc/security/limits.conf

# 添加以下内容
* soft nofile 65536
* hard nofile 65536
```

#### 1.2 内核参数优化
```bash
# 编辑sysctl配置
sudo vim /etc/sysctl.conf

# 添加以下内容
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
```

### 2. SSL/HTTPS配置

#### 2.1 使用Let's Encrypt
```bash
# 安装Certbot
sudo apt-get install -y certbot python3-certbot-nginx

# 获取证书
sudo certbot --nginx -d your-domain.com

# 自动续期测试
sudo certbot renew --dry-run
```

#### 2.2 手动配置SSL
```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    ssl_protocols TLSv1.2    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # 其他配置...
}
```

### 3. 防火墙配置

```bash
# 开放必要端口
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp

# 启用防火墙
sudo ufw enable

# 查看状态
sudo ufw status
```

### 4. 自动重启配置

#### Systemd服务
```bash
# 创建服务文件
sudo vim /etc/systemd/system/polymarket-dashboard.service
```

```ini
[Unit]
Description=Polymarket Dashboard
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/xmren/.openclaw/workspace/polymarket-project/dashboard
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
User=xmren
Group=docker

[Install]
WantedBy=multi-user.target
```

```bash
# 启用服务
sudo systemctl daemon-reload
sudo systemctl enable polymarket-dashboard
sudo systemctl start polymarket-dashboard

# 查看状态
sudo systemctl status polymarket-dashboard
```

---

## 验证测试

### 1. 健康检查

```bash
# 后端API健康检查
curl http://localhost:5000/api/summary/

# 预期返回: JSON数据

# 前端健康检查
curl http://localhost

# 预期返回: HTML页面
```

### 2. 功能测试

```bash
# 测试鲸鱼列表API
curl http://localhost:5000/api/whales/

# 测试警报API
curl http://localhost:5000/api/alerts/

# 测试WebSocket（使用wscat）
npm install -g wscat
wscat -c ws://localhost:5000/socket.io/
```

### 3. Telegram测试

```bash
# 发送测试消息
curl -X POST \
  "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/sendMessage" \
  -d "chat_id=<YOUR_CHAT_ID>" \
  -d "text=🧪 部署测试消息"
```

---

## 监控维护

### 1. 日志管理

```bash
# 查看实时日志
docker-compose logs -f

# 查看最近100行
docker-compose logs --tail=100

# 查看特定服务
docker-compose logs -f backend

# 导出日志
docker-compose logs > dashboard.log
```

### 2. 性能监控

```bash
# 查看容器资源使用
docker stats

# 查看系统资源
top
htop

# 查看磁盘使用
df -h
du -sh database/
```

### 3. 备份策略

#### 自动备份脚本
```bash
# 创建备份脚本
cat > backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/path/to/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# 备份数据库
cp database/polymarket.db "$BACKUP_DIR/polymarket.db.$DATE"

# 保留最近7天备份
find "$BACKUP_DIR" -name "polymarket.db.*" -mtime +7 -delete

# 备份配置文件
tar czf "$BACKUP_DIR/config.$DATE.tar.gz" .env docker-compose.yml nginx.conf

echo "Backup completed: $DATE"
