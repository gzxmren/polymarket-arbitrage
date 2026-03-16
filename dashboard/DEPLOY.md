# Polymarket Dashboard 部署指南

## 系统要求

- Python 3.11+
- Node.js 18+
- SQLite 3
- 2GB RAM (最低)
- 10GB 磁盘空间

## 部署方式

### 方式1: 手动部署

#### 1. 克隆代码
```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard
```

#### 2. 后端部署
```bash
# 创建虚拟环境
cd backend
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 初始化数据库
python3 -c "from app.models.database import db; db.init_db()"

# 启动服务
python3 run.py
```

后端服务将在 http://localhost:5000 运行

#### 3. 前端部署
```bash
cd frontend

# 安装依赖
npm install

# 开发模式
npm start

# 生产构建
npm run build
```

前端开发服务器将在 http://localhost:3000 运行

### 方式2: Docker 部署（推荐）

#### 1. 构建镜像
```bash
cd /home/xmren/.openclaw/workspace/polymarket-project/dashboard

# 构建并启动
docker-compose up -d --build
```

#### 2. 访问服务
- Web 界面: http://localhost
- API: http://localhost/api/

#### 3. 查看日志
```bash
docker-compose logs -f
```

#### 4. 停止服务
```bash
docker-compose down
```

## 环境变量配置

创建 `.env` 文件:

```bash
# Telegram 配置
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=-5052636342

# 数据库
DATABASE_PATH=/app/database/polymarket.db

# 监控数据路径
MONITOR_LOG_PATH=/app/data/logs/monitor.log
WHALE_STATES_PATH=/app/data/whale_states/

# Flask
FLASK_ENV=production
SECRET_KEY=your-secret-key-here
```

## 生产环境优化

### 1. 使用 Gunicorn
```bash
cd backend
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"
```

### 2. Nginx 配置
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        root /path/to/frontend/build;
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 3. 设置系统服务
创建 `/etc/systemd/system/polymarket-dashboard.service`:

```ini
[Unit]
Description=Polymarket Dashboard
After=network.target

[Service]
User=your-user
WorkingDirectory=/path/to/dashboard/backend
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/venv/bin/python run.py
Restart=always

[Install]
WantedBy=multi-user.target
```

启用服务:
```bash
sudo systemctl enable polymarket-dashboard
sudo systemctl start polymarket-dashboard
```

## 监控和维护

### 查看日志
```bash
# 后端日志
tail -f backend/app.log

# Docker日志
docker-compose logs -f backend
```

### 数据库备份
```bash
# 手动备份
cp database/polymarket.db database/polymarket.db.backup.$(date +%Y%m%d)

# 自动备份脚本
0 0 * * * cp /path/to/database/polymarket.db /path/to/backup/polymarket.db.$(date +\%Y\%m\%d)
```

### 更新部署
```bash
# 拉取最新代码
git pull

# 更新后端
cd backend
source venv/bin/activate
pip install -r requirements.txt

# 更新前端
cd ../frontend
npm install
npm run build

# 重启服务
sudo systemctl restart polymarket-dashboard
```

## 故障排除

### 后端无法启动
```bash
# 检查端口占用
sudo lsof -i :5000

# 检查日志
cat backend/app.log

# 测试数据库连接
python3 -c "from app.models.database import db; print('OK')"
```

### 前端构建失败
```bash
# 清除缓存
rm -rf node_modules package-lock.json
npm install

# 检查Node版本
node --version  # 需要 >= 18
```

### 数据同步问题
```bash
# 手动运行同步
python3 backend/app/services/data_sync.py

# 检查数据目录
ls -la /path/to/whale_states/
```

## 安全建议

1. **修改默认密钥**
   - 更改 `SECRET_KEY`
   - 使用强密码

2. **限制访问**
   - 配置防火墙
   - 使用 VPN 或内网访问

3. **定期更新**
   - 更新依赖包
   - 关注安全公告

4. **备份数据**
   - 定期备份数据库
   - 测试恢复流程

## 性能优化

### 数据库优化
```sql
-- 添加索引
CREATE INDEX IF NOT EXISTS idx_whales_is_watched ON whales(is_watched);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
CREATE INDEX IF NOT EXISTS idx_concentration_wallet ON concentration_history(wallet);
```

### 缓存配置
```python
# config.py
CACHE_TYPE = 'simple'
CACHE_DEFAULT_TIMEOUT = 300  # 5分钟
```

---

**部署完成后访问:**
- 本地: http://localhost:3000
- 生产: http://your-domain.com

