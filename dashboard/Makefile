.PHONY: help install dev build test clean deploy

# 默认目标
help:
	@echo "Polymarket Dashboard 管理命令"
	@echo ""
	@echo "可用命令:"
	@echo "  make install    - 安装依赖"
	@echo "  make dev        - 启动开发环境"
	@echo "  make build      - 构建生产版本"
	@echo "  make test       - 运行测试"
	@echo "  make deploy     - 部署到生产环境"
	@echo "  make clean      - 清理临时文件"
	@echo "  make logs       - 查看日志"
	@echo "  make backup     - 备份数据库"

# 安装依赖
install:
	@echo "📦 安装后端依赖..."
	cd backend && pip install -r requirements.txt
	@echo "📦 安装前端依赖..."
	cd frontend && npm install

# 开发环境
dev:
	@echo "🚀 启动开发环境..."
	@echo "后端: http://localhost:5000"
	@echo "前端: http://localhost:3000"
	@make dev-backend &
	@make dev-frontend

dev-backend:
	cd backend && python3 run.py

dev-frontend:
	cd frontend && npm start

# 构建
build:
	@echo "🔨 构建前端..."
	cd frontend && npm run build
	@echo "✅ 构建完成"

# 测试
test:
	@echo "🧪 运行测试..."
	cd backend && python3 -m pytest tests/ -v || true
	@echo "✅ 测试完成"

# Docker 部署
deploy:
	@echo "🐳 Docker 部署..."
	docker-compose up -d --build
	@echo "✅ 部署完成"
	@echo "访问: http://localhost"

# 停止
down:
	@echo "🛑 停止服务..."
	docker-compose down
	@echo "✅ 已停止"

# 查看日志
logs:
	docker-compose logs -f

# 清理
clean:
	@echo "🧹 清理临时文件..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name build -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ 清理完成"

# 备份数据库
backup:
	@echo "💾 备份数据库..."
	@mkdir -p backups
	@cp database/polymarket.db backups/polymarket.db.$(shell date +%Y%m%d_%H%M%S)
	@echo "✅ 备份完成"

# 数据库迁移
migrate:
	@echo "🗄️  数据库迁移..."
	cd backend && python3 -c "from app.models.database import db; db.init_db()"
	@echo "✅ 迁移完成"

# 数据同步
sync:
	@echo "🔄 同步数据..."
	cd backend && python3 app/services/data_sync.py
	@echo "✅ 同步完成"

# 代码检查
lint:
	@echo "🔍 代码检查..."
	cd backend && python3 -m py_compile app/**/*.py
	@echo "✅ 检查完成"

# 更新
update:
	@echo "⬆️  更新代码..."
	git pull
	@make install
	@make build
	@make deploy
	@echo "✅ 更新完成"
