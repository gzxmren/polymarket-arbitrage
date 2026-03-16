# Polymarket Dashboard

🐋 Polymarket 智能监控仪表盘 - Web 界面 + Telegram 双通道

## 功能特性

### 🐋 鲸鱼跟踪
- 实时追踪大户持仓变化
- 集中度分析 (Top5/Top10占比)
- 收敛趋势检测
- 历史数据可视化

### 🎯 套利机会
- Pair Cost 套利检测
- 跨平台套利 (Polymarket/Manifold/Metaculus)
- 实时利润计算
- 一键跳转交易

### 🔔 警报中心
- 鲸鱼活动实时提醒
- 套利机会即时通知
- 分类筛选管理
- 已读/未读标记

### 📊 数据可视化
- 集中度趋势图表
- 持仓分布分析
- 历史变动记录
- 实时数据刷新

## 技术栈

### 后端
- **框架**: Flask + Python 3.11
- **数据库**: SQLite
- **实时通信**: Flask-SocketIO
- **定时任务**: APScheduler

### 前端
- **框架**: React 18 + TypeScript
- **UI库**: Ant Design
- **图表**: Recharts
- **状态管理**: React Hooks

### 部署
- **容器化**: Docker + Docker Compose
- **Web服务器**: Nginx
- **进程管理**: Gunicorn

## 快速开始

### 1. 克隆项目
```bash
git clone <repository-url>
cd polymarket-project/dashboard
```

### 2. Docker 部署（推荐）
```bash
# 启动服务
docker-compose up -d

# 访问
open http://localhost
```

### 3. 手动部署

#### 后端
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

#### 前端
```bash
cd frontend
npm install
npm start
```

## 项目结构

```
dashboard/
├── backend/                 # 后端服务
│   ├── app/
│   │   ├── api/            # API路由
│   │   ├── models/         # 数据模型
│   │   └── services/       # 业务逻辑
│   ├── config.py           # 配置文件
│   ├── requirements.txt    # Python依赖
│   └── run.py              # 启动脚本
├── frontend/               # 前端应用
│   ├── src/
│   │   ├── components/     # 组件
│   │   ├── pages/          # 页面
│   │   └── services/       # API服务
│   ├── package.json        # Node依赖
│   └── tsconfig.json       # TypeScript配置
├── database/               # 数据库文件
├── tests/                  # 测试脚本
├── docker-compose.yml      # Docker配置
└── README.md               # 本文件
```

## API 文档

### 鲸鱼相关
- `GET /api/whales/` - 获取鲸鱼列表
- `GET /api/whales/:wallet` - 获取鲸鱼详情
- `GET /api/whales/:wallet/history` - 获取历史数据

### 汇总数据
- `GET /api/summary/` - 获取汇总统计

### 警报
- `GET /api/alerts/` - 获取警报列表
- `POST /api/alerts/:id/read` - 标记已读

### 套利
- `GET /api/arbitrage/pair-cost` - Pair Cost套利
- `GET /api/arbitrage/cross-market` - 跨平台套利

## 环境变量

| 变量 | 说明 | 默认值 |
|-----|------|--------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | - |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | -5052636342 |
| `DATABASE_PATH` | 数据库路径 | database/polymarket.db |
| `FLASK_ENV` | 运行环境 | production |

## 开发计划

- [x] 阶段1: 基础框架
- [x] 阶段2: 核心功能
- [x] 阶段3: 增强功能
- [x] 阶段4: 完善优化
- [ ] 阶段5: 生产部署

## 贡献指南

1. Fork 项目
2. 创建分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 许可证

MIT License

## 联系方式

- 项目主页: [Your Project URL]
- 问题反馈: [Issues URL]
- 邮箱: [Your Email]

---

Made with ❤️ by 虾头 🦐
