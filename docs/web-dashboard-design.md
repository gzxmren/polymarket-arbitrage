# Polymarket 监控仪表盘设计文档

> Web 界面 + Telegram 双通道监控系统
> 版本: v1.0
> 创建时间: 2026-03-15
> 技术栈: React + Flask API + SQLite

---

## 📋 目录

1. [系统架构](#系统架构)
2. [功能设计](#功能设计)
3. [界面设计](#界面设计)
4. [API 设计](#api-设计)
5. [数据库设计](#数据库设计)
6. [技术实现](#技术实现)
7. [开发计划](#开发计划)

---

## 系统架构

### 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        用户层                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Web 界面    │  │  Telegram    │  │   手机App    │      │
│  │   (React)    │  │   (Bot)      │  │   (未来)     │      │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘      │
└─────────┼────────────────┼─────────────────────────────────┘
          │                │
          └────────┬───────┘
                   │
┌──────────────────┼──────────────────────────────────────────┐
│                  │         API 层 (Flask)                   │
│  ┌───────────────┼──────────────────────────────────────┐   │
│  │               │                                      │   │
│  │  /api/whales  │  /api/markets  │  /api/alerts       │   │
│  │  /api/summary │  /api/news     │  /api/settings     │   │
│  │               │                                      │   │
│  └───────────────┴──────────────────────────────────────┘   │
└──────────────────┬──────────────────────────────────────────┘
                   │
          ┌────────┴────────┐
          │                 │
┌─────────┴──────┐  ┌──────┴──────────┐
│   数据层        │  │    监控引擎      │
│  ┌──────────┐  │  │  ┌──────────┐  │
│  │ SQLite   │  │  │ │ 监控程序  │  │
│  │ 数据库    │  │  │ │ (现有)   │  │
│  └──────────┘  │  │ └──────────┘  │
│                │  │               │
│  ┌──────────┐  │  │  ┌──────────┐ │
│  │ 缓存     │  │  │  │ 定时任务 │ │
│  │ (Redis)  │  │  │  │ (Celery)│ │
│  │ 可选     │  │  │  └──────────┘ │
│  └──────────┘  │  │               │
└────────────────┘  └───────────────┘
```

### 数据流

```
监控程序 (每5分钟)
    ↓
抓取数据 → 分析处理 → 保存到数据库
    ↓                    ↓
发送Telegram通知    Web界面实时更新
    ↓                    ↓
用户收到即时消息    用户查看仪表盘
```

---

## 功能设计

### 1. 仪表盘首页 (Dashboard)

**功能**:
- 实时数据总览
- 重点指标卡片
- 最新警报列表
- 快速导航

**组件**:
```
┌─────────────────────────────────────────┐
│  Polymarket 监控仪表盘                    │
├─────────────────────────────────────────┤
│                                         │
│  ┌────────┐ ┌────────┐ ┌────────┐      │
│  │ 重点   │ │ 套利   │ │ 活跃   │      │
│  │ 鲸鱼 6 │ │ 机会 0 │ │ 市场 0 │      │
│  │ $979K  │ │        │ │        │      │
│  └────────┘ └────────┘ └────────┘      │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ 📈 重点鲸鱼集中度趋势            │   │
│  │ (折线图: 6位鲸鱼Top5占比变化)    │   │
│  └─────────────────────────────────┘   │
│                                         │
│  ┌─────────────────┐ ┌───────────────┐ │
│  │ 🔔 最新警报      │ │ 📊 市场概况    │ │
│  │ (最近10条)       │ │ (活跃/总市场)  │ │
│  └─────────────────┘ └───────────────┘ │
│                                         │
└─────────────────────────────────────────┘
```

### 2. 鲸鱼跟踪页面 (Whales)

**功能**:
- 重点鲸鱼列表
- 详细持仓信息
- 集中度分析
- 历史变动记录

**组件**:
```
┌─────────────────────────────────────────┐
│  🐋 鲸鱼跟踪                              │
├─────────────────────────────────────────┤
│                                         │
│  [筛选: 全部 ▼] [排序: 集中度 ▼] [搜索]  │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ 1. Stupendous-Eddy              │   │
│  │    💰 $74,874 | 9市场 | Top5:96%│   │
│  │    📈 24h变动: 9次              │   │
│  │    🎯 策略: 看空伊朗局势         │   │
│  │    [查看详情] [历史记录]         │   │
│  └─────────────────────────────────┘   │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ 2. Cheerful-Ephemera            │   │
│  │    💰 $478,730 | 100市场 | 57%  │   │
│  │    📈 正在收敛中...              │   │
│  │    [查看详情] [历史记录]         │   │
│  └─────────────────────────────────┘   │
│                                         │
└─────────────────────────────────────────┘
```

**详情页**:
```
┌─────────────────────────────────────────┐
│  Stupendous-Eddy 详情                    │
├─────────────────────────────────────────┤
│                                         │
│  📊 集中度趋势图 (最近24小时)             │
│                                         │
│  💼 持仓明细                             │
│  ┌─────────────────────────────────┐   │
│  │ 市场              │ 方向 │ 价值  │   │
│  │ Iran regime fall  │ No   │ $20K │   │
│  │ US x Iran         │ No   │ $18K │   │
│  │ ...               │ ...  │ ...  │   │
│  └─────────────────────────────────┘   │
│                                         │
│  📈 历史变动                             │
│  [时间轴: 建仓 → 加仓 → 减仓]            │
│                                         │
└─────────────────────────────────────────┘
```

### 3. 套利机会页面 (Arbitrage)

**功能**:
- Pair Cost 套利列表
- 跨平台套利列表
- 实时利润计算
- 一键跳转交易

**组件**:
```
┌─────────────────────────────────────────┐
│  🎯 套利机会                              │
├─────────────────────────────────────────┤
│  [Pair Cost] [跨平台] [历史记录]          │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ 市场: Will Trump win 2024?      │   │
│  │ 💰 利润: 2.5%                   │   │
│  │ YES: $0.48 | NO: $0.52          │   │
│  │ 💧 流动性: $50,000              │   │
│  │ [查看详情] [前往交易]            │   │
│  └─────────────────────────────────┘   │
│                                         │
└─────────────────────────────────────────┘
```

### 4. 警报中心页面 (Alerts)

**功能**:
- 所有历史警报
- 分类筛选
- 标记已读/未读
- 搜索功能

**组件**:
```
┌─────────────────────────────────────────┐
│  🔔 警报中心                              │
├─────────────────────────────────────────┤
│  [全部] [鲸鱼] [套利] [系统] [未读]       │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ 🔴 16:00                        │   │
│  │ 🐋 Stupendous-Eddy 新建仓       │   │
│  │ 市场: Iran regime fall          │   │
│  │ [标记已读] [查看]                │   │
│  └─────────────────────────────────┘   │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ 🟡 15:30                        │   │
│  │ 🔔 重点鲸鱼概况报告              │   │
│  │ 6位鲸鱼，总持仓$979K            │   │
│  │ [标记已读] [查看]                │   │
│  └─────────────────────────────────┘   │
│                                         │
└─────────────────────────────────────────┘
```

### 5. 设置页面 (Settings)

**功能**:
- Telegram Bot 配置
- 监控阈值调整
- 提醒规则设置
- 数据导出

**组件**:
```
┌─────────────────────────────────────────┐
│  ⚙️ 设置                                  │
├─────────────────────────────────────────┤
│                                         │
│  📱 Telegram 配置                        │
│  Bot Token: [****************]          │
│  Chat ID: [-5052636342]                 │
│                                         │
│  🔔 提醒设置                             │
│  [✓] 鲸鱼活动警报                        │
│  [✓] 套利机会警报                        │
│  [✓] 重点鲸鱼概况 (每4小时)              │
│                                         │
│  🎯 阈值设置                             │
│  重点鲸鱼持仓: [$100,000]               │
│  Pair Cost 阈值: [$0.995]               │
│                                         │
│  [保存设置] [重置默认]                   │
│                                         │
└─────────────────────────────────────────┘
```

---

## 界面设计

### 整体风格

- **主题**: 深色模式（适合长时间观看）
- **主色调**: 蓝色 (#1890ff) + 绿色 (成功) + 红色 (警告)
- **字体**: Inter / Roboto
- **布局**: 侧边栏导航 + 主内容区

### 响应式设计

```
桌面端 (>= 1024px):
┌──────┬──────────────────────┐
│ 侧边栏 │      主内容区         │
│ 200px │      剩余宽度          │
└──────┴──────────────────────┘

平板端 (768px - 1024px):
┌──────────────────────┐
│ 顶部导航栏            │
├──────────────────────┤
│      主内容区         │
└──────────────────────┘

手机端 (< 768px):
┌──────────────────────┐
│ ≡ 菜单  Polymarket   │
├──────────────────────┤
│      主内容区         │
│  (卡片式布局)         │
└──────────────────────┘
```

### 导航结构

```
📊 仪表盘 (首页)
├─ 🐋 鲸鱼跟踪
│  ├─ 重点鲸鱼列表
│  ├─ 鲸鱼详情页
│  └─ 历史记录
├─ 🎯 套利机会
│  ├─ Pair Cost
│  ├─ 跨平台套利
│  └─ 历史记录
├─ 🔔 警报中心
│  ├─ 全部警报
│  ├─ 未读警报
│  └─ 设置规则
├─ 📈 数据分析
│  ├─ 市场趋势
│  ├─ 鲸鱼对比
│  └─ 盈亏统计
└─ ⚙️ 设置
   ├─ Telegram配置
   ├─ 阈值设置
   ├─ 提醒设置
   └─ 数据管理
```

---

## API 设计

### REST API 规范

#### 1. 鲸鱼相关 API

```
GET /api/whales
响应: {
  "count": 6,
  "whales": [
    {
      "wallet": "0x...",
      "pseudonym": "Stupendous-Eddy",
      "total_value": 74874,
      "position_count": 9,
      "top5_ratio": 0.96,
      "convergence_trend": "stable",
      "is_watched": true,
      "last_updated": "2026-03-15T16:00:00Z"
    }
  ]
}

GET /api/whales/{wallet}
响应: {
  "wallet": "0x...",
  "pseudonym": "Stupendous-Eddy",
  "total_value": 74874,
  "positions": [...],
  "changes": [...],
  "concentration_history": [...]
}

GET /api/whales/{wallet}/history
响应: {
  "history": [
    {
      "timestamp": "2026-03-15T16:00:00Z",
      "total_value": 74874,
      "position_count": 9,
      "top5_ratio": 0.96
    }
  ]
}
```

#### 2. 市场相关 API

```
GET /api/markets
响应: {
  "count": 100,
  "markets": [
    {
      "slug": "will-trump-win-2024",
      "question": "Will Trump win 2024?",
      "yes_price": 0.48,
      "no_price": 0.52,
      "liquidity": 50000,
      "volume": 100000
    }
  ]
}

GET /api/markets/{slug}
响应: {
  "slug": "will-trump-win-2024",
  "question": "Will Trump win 2024?",
  "yes_price": 0.48,
  "no_price": 0.52,
  "order_book": {...}
}
```

#### 3. 套利相关 API

```
GET /api/arbitrage/pair-cost
响应: {
  "opportunities": [
    {
      "market": "Will Trump win 2024?",
      "yes_price": 0.48,
      "no_price": 0.52,
      "pair_cost": 1.00,
      "profit_pct": 0
    }
  ]
}

GET /api/arbitrage/cross-market
响应: {
  "opportunities": [
    {
      "question": "Will Trump win 2024?",
      "polymarket": 0.48,
      "manifold": 0.55,
      "gap": 0.07
    }
  ]
}
```

#### 4. 警报相关 API

```
GET /api/alerts
参数: ?type=whale&limit=10&unread_only=true
响应: {
  "count": 10,
  "alerts": [
    {
      "id": "alert-001",
      "type": "whale",
      "title": "Stupendous-Eddy 新建仓",
      "message": "...",
      "timestamp": "2026-03-15T16:00:00Z",
      "is_read": false
    }
  ]
}

POST /api/alerts/{id}/read
响应: { "success": true }
```

#### 5. 汇总 API

```
GET /api/summary
响应: {
  "timestamp": "2026-03-15T16:00:00Z",
  "whales": {
    "watched_count": 6,
    "total_value": 979235,
    "active_count": 3
  },
  "arbitrage": {
    "pair_cost_count": 0,
    "cross_market_count": 0
  },
  "markets": {
    "scanned": 100,
    "active": 50
  }
}
```

---

## 数据库设计

### SQLite 数据库结构

```sql
-- 鲸鱼表
CREATE TABLE whales (
    wallet TEXT PRIMARY KEY,
    pseudonym TEXT,
    total_value REAL,
    position_count INTEGER,
    top5_ratio REAL,
    convergence_trend TEXT,
    is_watched BOOLEAN,
    added_at TIMESTAMP,
    last_updated TIMESTAMP
);

-- 持仓表
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT,
    market TEXT,
    outcome TEXT,
    size REAL,
    avg_price REAL,
    cur_price REAL,
    value REAL,
    pnl REAL,
    end_date TEXT,
    updated_at TIMESTAMP,
    FOREIGN KEY (wallet) REFERENCES whales(wallet)
);

-- 变动记录表
CREATE TABLE changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT,
    type TEXT, -- new, increased, decreased, closed
    market TEXT,
    outcome TEXT,
    old_size REAL,
    new_size REAL,
    change_amount REAL,
    timestamp TIMESTAMP,
    FOREIGN KEY (wallet) REFERENCES whales(wallet)
);

-- 集中度历史表
CREATE TABLE concentration_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT,
    hhi REAL,
    top5_ratio REAL,
    top10_ratio REAL,
    timestamp TIMESTAMP,
    FOREIGN KEY (wallet) REFERENCES whales(wallet)
);

-- 警报表
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT, -- whale, arbitrage, system
    title TEXT,
    message TEXT,
    data JSON,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP
);

-- 市场表
CREATE TABLE markets (
    slug TEXT PRIMARY KEY,
    question TEXT,
    yes_price REAL,
    no_price REAL,
    liquidity REAL,
    volume REAL,
    end_date TEXT,
    updated_at TIMESTAMP
);

-- 套利机会表
CREATE TABLE arbitrage_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT, -- pair_cost, cross_market
    market TEXT,
    profit_pct REAL,
    data JSON,
    detected_at TIMESTAMP,
    expired_at TIMESTAMP
);
```

---

## 技术实现

### 技术栈

| 层级 | 技术 | 说明 |
|-----|------|------|
| **前端** | React 18 + TypeScript | 单页应用 |
| **UI库** | Ant Design / Material-UI | 组件库 |
| **图表** | Recharts / ECharts | 数据可视化 |
| **后端** | Flask + Python 3.11 | API服务 |
| **数据库** | SQLite | 轻量级，无需配置 |
| **缓存** | Flask-Caching | 可选Redis |
| **实时** | Flask-SocketIO | WebSocket推送 |
| **部署** | Gunicorn + Nginx | 生产环境 |

### 项目结构

```
polymarket-dashboard/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── whales.py
│   │   │   ├── markets.py
│   │   │   ├── arbitrage.py
│   │   │   ├── alerts.py
│   │   │   └── summary.py
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── database.py
│   │   │   └── schemas.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── whale_service.py
│   │   │   └── alert_service.py
│   │   └── utils/
│   │       └── helpers.py
│   ├── config.py
│   ├── requirements.txt
│   └── run.py
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Layout/
│   │   │   ├── Dashboard/
│   │   │   ├── Whales/
│   │   │   ├── Arbitrage/
│   │   │   ├── Alerts/
│   │   │   └── Settings/
│   │   ├── pages/
│   │   ├── services/
│   │   │   └── api.ts
│   │   ├── store/
│   │   │   └── index.ts
│   │   ├── types/
│   │   ├── App.tsx
│   │   └── index.tsx
│   ├── package.json
│   └── tsconfig.json
├── database/
│   └── init.sql
├── docs/
├── docker-compose.yml
└── README.md
```

### 实时更新机制

```
方式1: WebSocket (推荐)
后端: Flask-SocketIO
前端: socket.io-client
推送: 有新数据时主动推送

方式2: 轮询 (备选)
前端: 每30秒轮询一次
后端: 返回最新数据
```

---

## 开发计划

### 阶段1: 基础框架 (1-2天)
- [ ] 后端: Flask API 框架搭建
- [ ] 后端: 数据库模型创建
- [ ] 前端: React 项目初始化
- [ ] 前端: 路由和布局组件

### 阶段2: 核心功能 (2-3天)
- [ ] 后端: 鲸鱼相关 API
- [ ] 后端: 数据同步（从现有监控程序）
- [ ] 前端: 仪表盘页面
- [ ] 前端: 鲸鱼列表和详情页

### 阶段3: 增强功能 (2-3天)
- [ ] 后端: 套利 API
- [ ] 后端: 警报 API
- [ ] 前端: 套利机会页面
- [ ] 前端: 警报中心页面
- [ ] 前端: 图表组件

### 阶段4: 完善优化 (1-2天)
- [ ] 后端: 实时推送 (WebSocket)
- [ ] 前端: 设置页面
- [ ] 前端: 响应式适配
- [ ] 测试和优化

### 阶段5: 部署上线 (1天)
- [ ] Docker 容器化
- [ ] 部署文档
- [ ] 生产环境配置

**总计**: 7-11天

---

## 与现有系统的集成

### 数据同步

```python
# 现有监控程序每5分钟扫描后，同时写入：
# 1. JSON 文件（现有）
# 2. SQLite 数据库（新增）

def save_data(data):
    # 现有：保存到JSON
    with open('monitor_report.json', 'w') as f:
        json.dump(data, f)
    
    # 新增：保存到数据库
    db.save_whale_data(data['whales'])
    db.save_alerts(data['alerts'])
```

### Telegram 保持同步

```
现有逻辑不变：
- 每5分钟扫描 → 发送 Telegram 通知
- 每4小时 → 发送 Telegram 概况

新增逻辑：
- 同时写入数据库
- Web 界面读取数据库显示
```

---

*文档版本: v1.0*
*最后更新: 2026-03-15*
*状态: 设计完成，待开发*
