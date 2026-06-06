# Polymarket App 架构分析

**文档生成时间**: 2026-04-05 04:12 GMT+8

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Polymarket Dashboard                    │
├─────────────────────┬───────────────────────────────────────┤
│   Frontend (React)  │      Backend (Flask Python)           │
├─────────────────────┼───────────────────────────────────────┤
│  • Dashboard.tsx    │  • API Layer (RESTful)                │
│  • Whales.tsx       │  • Services (Business Logic)          │
│  • Arbitrage.tsx    │  • Models (Database)                  │
│  • Alerts.tsx       │  • External APIs (Polymarket)         │
│  • Settings.tsx     │                                       │
└─────────────────────┴───────────────────────────────────────┘
           │                           │
           └───────────┬───────────────┘
                       ▼
              ┌─────────────────┐
              │   SQLite DB     │
              │  + JSON Files   │
              └─────────────────┘
```

---

## 二、目录结构详解

```
polymarket-project/dashboard/
├── backend/                    # Flask 后端
│   ├── app/
│   │   ├── api/               # REST API 路由层
│   │   │   ├── alerts.py      # 警报管理 API
│   │   │   ├── arbitrage.py   # 套利检测 API
│   │   │   ├── markets.py     # 市场数据 API
│   │   │   ├── semantic.py    # 语义套利 API
│   │   │   ├── settings.py    # 配置管理 API
│   │   │   ├── signals.py     # 交易信号 API
│   │   │   ├── summary.py     # 汇总数据 API
│   │   │   └── whales.py      # 鲸鱼跟踪 API
│   │   │
│   │   ├── models/            # 数据模型层
│   │   │   ├── database.py    # 数据库连接 & ORM
│   │   │   ├── cross_market_arbitrage.py
│   │   │   └── signal_tracking.py
│   │   │
│   │   ├── services/          # 业务逻辑层
│   │   │   ├── data_sync.py   # 数据同步服务
│   │   │   ├── semantic_arbitrage.py  # 语义套利引擎
│   │   │   ├── whale_analyzer.py      # 鲸鱼分析
│   │   │   ├── clob_service.py        # CLOB API 封装
│   │   │   ├── notification.py        # 通知服务
│   │   │   ├── scheduler.py   # 定时任务调度
│   │   │   └── quality_report.py      # 质量报告
│   │   │
│   │   └── utils/             # 工具函数
│   │
│   ├── config.py              # 全局配置
│   └── run.py                 # 启动入口
│
├── frontend/                   # React + TypeScript 前端
│   ├── src/
│   │   ├── pages/             # 页面组件
│   │   │   ├── Dashboard.tsx      # 首页仪表盘
│   │   │   ├── Whales.tsx         # 鲸鱼列表
│   │   │   ├── WhaleDetail.tsx    # 鲸鱼详情
│   │   │   ├── Arbitrage.tsx      # 套利机会
│   │   │   ├── SemanticArbitrage.tsx  # 语义套利
│   │   │   ├── Alerts.tsx         # 警报中心
│   │   │   ├── Settings.tsx       # 系统设置
│   │   │   ├── NewsDriven.tsx     # 新闻驱动
│   │   │   └── QualityReport.tsx  # 质量报告
│   │   │
│   │   └── services/          # API 客户端
│   │       ├── api.ts         # HTTP API 封装
│   │       └── socket.ts      # WebSocket 实时推送
│   │
│   └── package.json
│
└── tests/                      # 测试文件
```

---

## 三、核心功能模块

| 模块 | 功能 | 技术实现 |
|------|------|---------|
| **鲸鱼跟踪** | 监控大户交易行为 | 分析持仓变动、集中度计算 (HHI指数) |
| **语义套利** | 检测市场定价矛盾 | LLM + 逻辑链分析 |
| **Pair Cost 套利** | 同一事件不同平台价差 | CLOB API 实时价格对比 |
| **跨平台套利** | Polymarket ↔ Manifold 等 | 多平台数据聚合 |
| **新闻驱动** | 事件驱动交易策略 | 新闻抓取 → 关键词提取 → 关联市场 |
| **质量监控** | 信号胜率追踪 | 历史回测 + 实时评估 |

---

## 四、数据流架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Polymarket  │     │  Manifold   │     │   News API  │
│   API       │     │    API      │     │             │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ▼
              ┌─────────────────────┐
              │   Data Sync Service │  (每5分钟)
              │   (data_sync.py)    │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │    SQLite Database  │
              │  whales/positions/  │
              │  changes/alerts     │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │   Analysis Engine   │
              │  • 鲸鱼分析         │
              │  • 套利检测         │
              │  • 风险评估         │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │   Flask REST API    │
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │   React Frontend    │
              │  Dashboard/Alerts   │
              └─────────────────────┘
```

---

## 五、关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| **后端框架** | Flask | 轻量、Python 生态、适合量化计算 |
| **前端框架** | React + TypeScript | 组件化、类型安全、生态丰富 |
| **数据库** | SQLite | 单机部署、零配置、足够用 |
| **实时通信** | WebSocket (SocketIO) | 实时推送警报和更新 |
| **外部 API** | Polymarket Gamma API | 官方数据、免费、RESTful |
| **部署方式** | Systemd 服务 | 简单、自动重启、日志管理 |

---

## 六、技术栈总结

### Backend:
- Python 3.12 + Flask + Flask-SocketIO
- SQLite + SQLAlchemy (ORM)
- pandas, numpy (数据分析)
- scikit-learn (机器学习)
- requests, aiohttp (HTTP 客户端)

### Frontend:
- React 18 + TypeScript
- Ant Design (UI 组件库)
- Recharts (图表)
- Socket.IO Client (实时通信)

### DevOps:
- Systemd 服务管理
- Docker (可选)
- Nginx (反向代理)

---

## 七、系统功能状态

| 阶段 | 功能 | 状态 |
|------|------|------|
| **Phase 1** | 核心模块（语义套利、鲸鱼跟踪） | ✅ 已完成 |
| **Phase 2** | Dashboard Web 集成、CLOB API 接入 | ✅ 已完成 |
| **Phase 3** | 质量监控、胜率追踪、每周报告 | ✅ 已完成 |

---

## 八、访问地址

- **Dashboard**: http://localhost:3001
- **API**: http://localhost:5000

---

## 九、项目根目录结构

```
polymarket-project/
├── 00-learning/           # 学习资料
├── 01-arbitrage-pair-cost/    # Pair Cost 套利策略
├── 02-arbitrage-cross-market/ # 跨平台套利策略
├── 03-momentum-trading/       # 动量交易策略
├── 04-whale-following/        # 鲸鱼跟随策略
├── 05-sentiment-contrarian/   # 情绪反向策略
├── 06-tools/              # 工具脚本
│   ├── analysis/          # 分析工具
│   └── monitoring/        # 监控工具
├── 07-data/               # 数据存储
├── 08-backtests/          # 回测结果
├── 09-docs/               # 文档
├── 10-tests/              # 测试代码
├── dashboard/             # Web 仪表盘（本文档重点）
├── database/              # 数据库文件
├── docs/                  # 项目文档
├── memory/                # 工作日志
├── README.md              # 项目说明
└── PROJECT_STRUCTURE.md   # 项目结构规范
```

---

*文档由 AI 助手生成*
*生成时间: 2026-04-05 04:12 GMT+8*
