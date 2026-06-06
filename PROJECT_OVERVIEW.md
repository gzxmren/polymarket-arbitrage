# Polymarket 智能监控系统 — 项目总说明文档

> **文档生成时间**：2026-06-02 (UTC+8)
> **对应代码分支**：`v2.0-development`
> **最近提交**：`8d4c5d1 Phase 3: 质量监控与自动化`
> **撰写说明**：本文档基于对仓库现有代码、设计文档与配置的通读整理，旨在系统性讲解项目的**设计理念、技术实现与部署运行**三个层面。

---

## 目录

1. [一句话定位](#1-一句话定位)
2. [系统设计（Design）](#2-系统设计design)
3. [代码实现（Implementation）](#3-代码实现implementation)
4. [数据层设计](#4-数据层设计)
5. [Dashboard（Web 应用）](#5-dashboardweb-应用)
6. [部署与运行（Deployment）](#6-部署与运行deployment)
7. [配置与环境变量](#7-配置与环境变量)
8. [运维、监控与故障排除](#8-运维监控与故障排除)
9. [演进历史与现状评估](#9-演进历史与现状评估)
10. [快速上手清单](#10-快速上手清单)

---

## 1. 一句话定位

**Polymarket 智能监控系统**是一个面向预测市场（以 Polymarket 为主，兼顾 Manifold / Metaculus / Kalshi）的**自动化监控、套利发现与"鲸鱼"（大户）行为跟踪**平台。它由三部分组成：

- **离线监控引擎**（Python，定时扫描）——发现套利机会、跟踪大户、推送 Telegram 警报；
- **Web Dashboard**（Flask + React）——把监控产出的数据可视化，并提供深度分析、信号管理、质量监控等交互能力；
- **共享数据层**（JSON 状态文件 + SQLite 数据库）——连接上述两部分。

项目当前处于 **V2.0 开发阶段**，已完成 Dashboard 的 Phase 1~3（数据连接、语义套利引擎、质量监控自动化）。

---

## 2. 系统设计（Design）

### 2.1 核心设计目标

V2 设计文档（`docs/V2-design-integrated.md`）中明确诊断了 V1 的核心痛点并据此设计 V2：

| V1 问题 | 现状 | V2 应对 |
|---------|------|---------|
| 套利机会稀少 | 24h 内 0 次 | 放宽阈值 + 引入语义套利 |
| 策略过于基础 | 仅 Pair Cost + 跨平台 | 新增鲸鱼跟随、新闻驱动、动量等策略 |
| 数据未充分利用 | 历史数据沉睡 | 引入 SQLite + Dashboard 沉淀分析 |
| 鲸鱼跟踪浅层 | 仅记录持仓 | 持仓重建、集中度、收敛趋势、PnL、深度 LLM 分析 |
| 信号效果未知 | 无胜率追踪 | 信号追踪表 + 阈值自动优化 + 质量报告 |

### 2.2 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│                      外部数据源（External APIs）               │
│   Polymarket(Gamma/Data/CLOB) · Manifold · Metaculus · Kalshi │
│   新闻 RSS（BBC 等） · Leaderboard                            │
└───────────────────────────┬──────────────────────────────────┘
                            ↓ 抓取
┌──────────────────────────────────────────────────────────────┐
│                  数据获取 & 重建层（06-tools/analysis）        │
│  clob_api · cross_market_scanner · whale_tracker_v2 ·        │
│  position_rebuilder · hybrid_data_source · leaderboard_*     │
└───────────────────────────┬──────────────────────────────────┘
                            ↓ 计算
┌──────────────────────────────────────────────────────────────┐
│                       分析引擎层                              │
│  套利: pair_cost_scanner · semantic_arbitrage               │
│  大户: whale_classifier · whale_watchlist · whale_following  │
│  指标: kelly_criterion · vwap_calculator · correlation_matrix│
│  做市: market_maker · 动量: momentum_strategy               │
└───────────────────────────┬──────────────────────────────────┘
                            ↓ 评估 & 过滤
┌──────────────────────────────────────────────────────────────┐
│              风险评估 & 质量监控层                            │
│  risk_reviewer · data_quality_check · threshold_optimizer    │
└──────────────┬───────────────────────────────┬───────────────┘
               ↓ 通知                          ↓ 持久化
┌──────────────────────────┐    ┌─────────────────────────────┐
│  通知输出层               │    │  数据存储层                  │
│  telegram_notifier_v2     │    │  07-data/*.json (状态)       │
│  send_summary 等          │    │  dashboard SQLite DB         │
└──────────────────────────┘    └──────────────┬──────────────┘
                                                ↓ 读取/同步
                              ┌─────────────────────────────────┐
                              │   Web Dashboard (Flask + React) │
                              │   API 蓝图 · SocketIO · 前端页面 │
                              └─────────────────────────────────┘
```

### 2.3 关键设计概念

#### A. 套利策略

- **Pair Cost 套利**：同一事件 `YES + NO` 价格之和 < 阈值时存在无风险价差。阈值在 `config.py::Thresholds.PAIR_COST` 中定义，**当前为 0.90**（2026-04-21 从 0.995 下调以发现更多机会）。
- **跨平台套利**（`cross_market_scanner.py`）：将不同平台描述相同事件的市场做匹配，价差 > `MIN_GAP_THRESHOLD`（5%）即视为机会。
- **语义套利**（`semantic_arbitrage.py` / Dashboard `semantic.py`）：V2 引入，用语义关系（蕴含、互斥、逻辑链）发现"非字面相同但逻辑相关"的市场间错价，是 V2 的核心增量。
- **做市机会**（`market_maker.py`）：基于真实 CLOB 订单簿，买卖价差 > 1.5%、深度达标且价格非极端（避开 <2% / >98%）时提示。

#### B. 鲸鱼（大户）跟踪体系

这是项目沉淀最深的模块，逐层递进：

1. **识别**：24h 交易量 > $5,000 或单笔 > $500 即视为活跃鲸鱼（`whale_tracker_v2.py`）。
2. **持仓重建**：`position_rebuilder.py` 从成交流重建持仓；`hybrid_data_source.py` 按 **Trades 重建 → JSON 备份 → Leaderboard 参考 → 无数据** 的优先级提供统一数据，解决单一数据源不全/滞后的问题。
3. **重点关注（Watchlist）**：持仓 > $100k，或（24h 变动 > 5 次且持仓 > $50k）自动加入重点列表（`whale_watchlist.py`）。
4. **集中度与收敛**：计算 HHI 指数、Top5/Top10 占比，判断策略从"分散 → 集中"的收敛趋势（converging/diverging/stable），用作信号强度参考。
5. **鲸鱼跟随策略**（`whale_following.py`）：对历史胜率 > 60% 的"聪明钱"，在其大额加仓时生成带置信度的跟单建议。
6. **深度分析**（Dashboard `whale_deep_analyzer.py`）：调用 LLM（DeepSeek / OpenAI）生成自然语言深度解读，结果带 `data_hash` 缓存以控成本。

#### C. 风险评估与质量监控

- `risk_reviewer.py`：对每个机会/信号打风险分（LOW < 25% / MEDIUM 25–50% / HIGH > 50%），并检查市场是否过期、数据是否异常（如持仓 > 50 个但总值 < $1000 判为脏数据）。
- **Phase 3 质量自动化**：`data_quality_check.py` + Dashboard `quality_report.py` + `threshold_optimizer.py`，对信号胜率做追踪并自动建议/应用阈值优化。

---

## 3. 代码实现（Implementation）

### 3.1 仓库目录约定

项目采用**数字前缀**组织目录（详见 `PROJECT_STRUCTURE.md`）：

| 目录 | 作用 |
|------|------|
| `00-learning/` | 学习资料 |
| `01~05-*` | 五类策略的研究/文档目录（Pair Cost / 跨市场 / 动量 / 鲸鱼跟随 / 情绪逆向） |
| `06-tools/` | **核心代码**：`analysis/`（分析）、`monitoring/`（监控与通知）、`trading/`（交易，预留） |
| `07-data/` | **统一数据目录**：`logs/`、`whale_states/`、`news_cache/`、`quality_reports/` 及各类 JSON |
| `08-backtests/` | 回测框架 |
| `09-docs/` / `docs/` | 用户文档 / 设计文档 |
| `10-tests/` | 单元 / 集成 / e2e 测试 |
| `dashboard/` | Web 仪表盘（backend + frontend + database） |
| `database/` | 主数据库副本 |
| `99-archive/`、`archived/` | 已废弃模块与历史备份 |

### 3.2 路径与配置统一

`06-tools/analysis/config.py` 是路径与阈值的**单一事实来源**：

- 通过 `Path(__file__).parent...` 计算 `PROJECT_ROOT`，再派生 `DATA_DIR`、`WHALE_STATES_DIR`、`WATCHLIST_FILE`、`DASHBOARD_DB_FILE` 等；
- `Thresholds` 类集中所有阈值；`APIConfig` 集中 API 端点；`DBTables` 集中表名；
- **规范**：禁止硬编码绝对路径、禁止各文件重复定义路径（`PROJECT_STRUCTURE.md` 明确）。
  > ⚠️ 实测注记：部分较新模块（如 `hybrid_data_source.py`、`sync_changes.py`）仍内联了绝对路径，属于待收敛到 `config.py` 的技术债。

### 3.3 监控主程序

**`06-tools/monitoring/polymarket_monitor_v2.py`**（约 864 行）是离线引擎的入口，设计上采用"**能力可选加载**"模式——所有子模块用 `try/except ImportError` 包裹并设布尔开关（`WATCHLIST_AVAILABLE`、`CLOB_API_AVAILABLE`、`RISK_REVIEW_AVAILABLE` 等），使得任一模块缺失时主程序仍可降级运行。

单次扫描流程（默认每 5 分钟）：

```
1. 获取市场数据（Polymarket 500 + Manifold 100 + Metaculus 50）
2. Pair Cost 扫描 + Kelly 仓位评估
3. 跨平台套利匹配
4. 鲸鱼追踪（取最近 ~1000 笔成交 → 识别 → 分析持仓/变动/集中度 → 更新 watchlist）
5. 风险评估与异常数据过滤
6. 发送 Telegram 通知 + 写 JSON 报告
```

启动脚本 `start_monitor.sh` 用 `nohup` 后台拉起，并设置 `PYTHONPATH` 指向 `06-tools/analysis`。

### 3.4 监控目录主要模块

- 通知：`telegram_notifier_v2.py`、`send_summary.py`、`send_top_whales_report_v2.py`、`send_watchlist_summary.py`
- 数据同步/回填：`sync_changes.py`、`backfill_*.py`（pnl / 集中度 / 市场标题 / 鲸鱼名等历史数据补齐）
- 运维：`health_checker.py`、`error_monitor.py`、`data_quality_check.py`、`cleanup_stale_data.py`
- 信号：`check_polycop_signal.py`（"PolyCop" 评分信号，见 `docs/PolyCop_*.md` 系列）

### 3.5 技术栈

- **后端 / 引擎**：Python 3.12+，`requests`、`apscheduler`，类型注解 + f-string。
- **Dashboard 后端**：Flask 3 + Flask-CORS + Flask-SocketIO + python-dotenv（见 `dashboard/backend/requirements.txt`）。
- **前端**：React 18 + TypeScript + Ant Design + `@ant-design/charts`/`recharts` + axios + socket.io-client（CRA `react-scripts`）。
- **数据库**：SQLite。

---

## 4. 数据层设计

### 4.1 两类存储

1. **JSON 状态文件**（`07-data/`）：鲸鱼状态 `whale_states/*.json`、重点列表 `whale_watchlist.json`、扫描报告 `monitor_report_*.json`、新闻缓存、质量报告等——监控引擎直接读写。
2. **SQLite 数据库**：Dashboard 的核心存储。注意存在多处库路径，需保持一致：
   - 配置约定主库：`dashboard/database/polymarket.db`，备份 `database/polymarket.db`；
   - 实际后端运行库：`dashboard/backend/database/polymarket.db`（`data_sync.py`、`hybrid_data_source.py` 使用）。
   > ⚠️ 数据库路径不统一是已知技术债，部署/同步时务必确认实际使用的库文件。

### 4.2 SQLite 表结构（`dashboard/backend/app/models/database.py`）

| 表 | 用途 | 关键字段 |
|----|------|---------|
| `whales` | 鲸鱼主表 | wallet(PK)、pseudonym、total_value、position_count、top5_ratio、convergence_trend、is_watched、total_pnl、total_volume |
| `positions` | 持仓明细 | wallet(FK)、market、outcome、size、avg_price、cur_price、value、pnl、end_date |
| `changes` | 仓位变动流 | wallet(FK)、type、market、old_size/new_size、change_amount、timestamp |
| `concentration_history` | 集中度时序 | wallet(FK)、hhi、top5_ratio、top10_ratio、timestamp |
| `alerts` | 警报 | type、title、message、data、is_read |
| `whale_deep_analysis` | LLM 深度分析缓存 | wallet(PK)、content、model、data_hash、cost、expires_at |
| `arbitrage_feedback` | 套利人工确认 | arbitrage_id、arbitrage_type、feedback_type(confirmed/error) |
| （另有）`semantic_signals` / 信号追踪相关表 | 语义信号与胜率追踪 | 见 `signal_tracking.py` |

### 4.3 数据同步

- **引擎 → DB**：`dashboard/backend/app/services/data_sync.py`（及 `data_sync_v2.py`）将 `07-data/` 的 JSON 与混合数据源写入 SQLite；由 `scheduler.py`（APScheduler）每 5 分钟触发一次 `run_full_sync()`，启动时立即执行一次。
- **changes 专项同步**：`run_sync_changes.sh` → `sync_changes.py`。

---

## 5. Dashboard（Web 应用）

### 5.1 后端结构（`dashboard/backend/app/`）

应用工厂 `create_app()`（`app/__init__.py`）注册 8 个 API 蓝图：

| 蓝图 | 前缀 | 职责 |
|------|------|------|
| `whales` | `/api/whales` | 鲸鱼列表/详情/历史/PnL/集中度趋势/深度分析/信号/Leaderboard 趋势（最大蓝图，30+ 端点） |
| `markets` | `/api/markets` | 活跃市场、扫描、订单簿、实时价格 |
| `arbitrage` | `/api/arbitrage` | Pair Cost / 跨市场机会、人工反馈、价格对比、统计 |
| `alerts` | `/api/alerts` | 警报列表/已读/统计 |
| `summary` | `/api/summary` | 首页汇总 |
| `settings` | `/api/settings` | 设置读写 |
| `semantic` | `/api/semantic` | 语义套利扫描、关系图 |
| `signals` | `/api/signals` | 信号统计/追踪/阈值优化/质量报告 |

**服务层**（`app/services/`）：`data_sync*`、`scheduler`、`clob_service`、`semantic_arbitrage`、`whale_analyzer`、`whale_deep_analyzer`、`quality_report`、`threshold_optimizer`、`notification`、`websocket`。

实时推送通过 `Flask-SocketIO`（`websocket.py` 的 `broadcast_new_alert` 等）。

### 5.2 前端页面（`dashboard/frontend/src/pages/`）

Dashboard（首页）、Whales / WhaleDetail（鲸鱼）、Arbitrage / SemanticArbitrage（套利）、Alerts（警报）、LeaderboardWales / LeaderboardTrends（榜单趋势）、NewsDriven（新闻驱动）、QualityReport（质量报告）、Settings（设置）。图表组件 `ConcentrationChart`、`PnLChart`；服务封装 `services/api.ts`（axios）与 `services/socket.ts`（SocketIO）。

### 5.3 后端启动

`dashboard/backend/run.py`：手动解析 `.env` → `create_app()` → 启动 `DataScheduler` → `socketio.run(host=0.0.0.0, port=5000)`。若配置了 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 则启用真实 LLM，否则走模拟模式。

---

## 6. 部署与运行（Deployment）

### 6.1 离线监控引擎

```bash
# 一键启动（后台 nohup）
./start_monitor.sh
# 日志
tail -f /tmp/monitor.log        # 启动日志
tail -f 07-data/logs/monitor.log # 运行日志

# 定时同步 changes（建议加入 crontab）
./run_sync_changes.sh
```

引擎本身的"每 5 分钟"循环由进程内逻辑/外部 cron 驱动（生产建议用 cron 或 systemd 守护 `polymarket_monitor_v2.py`）。

### 6.2 Dashboard — Docker（推荐，见 `docs/deployment-guide-v2.md`）

`dashboard/docker-compose.yml` 定义三服务：

| 服务 | 端口 | 说明 |
|------|------|------|
| `backend` | 5000 | Flask；挂载 `./database` 与只读 `../07-data` |
| `frontend` | 3000 | React 构建产物 |
| `nginx` | 80 | 反代 backend + frontend（`nginx.conf`） |

```bash
cd dashboard
# 配置 .env（TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / FLASK_ENV / SECRET_KEY）
docker-compose up -d --build
docker-compose ps
docker-compose logs -f
# 访问：http://<server-ip>/ ；API：http://<server-ip>/api/
```

`dashboard/Makefile` 提供 `make deploy / dev / logs / backup / update` 快捷命令。

### 6.3 Dashboard — 手动部署

```bash
# 后端
cd dashboard/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 run.py            # http://localhost:5000

# 前端
cd dashboard/frontend
npm install
npm run build
npx serve -s build -l 3000
```

### 6.4 生产加固

- **SSL**：`certbot --nginx -d your-domain.com`；
- **守护**：systemd（`polymarket-dashboard` 服务）+ `restart: unless-stopped`；
- **备份**：`cron` 每日 `cp polymarket.db backups/polymarket.db.$(date +%Y%m%d)`。

---

## 7. 配置与环境变量

### 7.1 监控引擎环境变量

```bash
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="-5052636342"
export PAIR_COST_THRESHOLD="0.995"      # 注意：config.py 默认已为 0.90
export MIN_GAP_THRESHOLD="0.05"
export MARKET_MAKING_MIN_SPREAD="0.015"
export MARKET_MAKING_MIN_DEPTH="5000"
export NOTIFY_IMMEDIATELY="true"
export RISK_REVIEW_ENABLED="true"
export MARKET_MAKING_NOTIFY="true"
```

### 7.2 Dashboard `.env`

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=-5052636342
FLASK_ENV=production
SECRET_KEY=change_this_key
DEEPSEEK_API_KEY=...   # 或 OPENAI_API_KEY，启用 LLM 深度分析
```

> 阈值的**单一来源是 `config.py`**；环境变量为运行期覆盖。两者目前默认值不一致（如 Pair Cost），以代码默认 0.90 为准。

---

## 8. 运维、监控与故障排除

| 现象 | 处理 |
|------|------|
| 收不到通知 | 检查 `TELEGRAM_BOT_TOKEN`/`CHAT_ID`；`tail -f 07-data/logs/monitor.log` |
| Metaculus API 报错 | 已知 SSL 间歇问题，不影响 Polymarket+Manifold 核心 |
| 异常数据（持仓多但价值低） | `risk_reviewer` 自动过滤（持仓 > 50 且价值 < $1000），日志显示"⚠️ 数据异常，跳过" |
| 测试数据污染生产 | 测试须带 `--test`，使用 `/tmp/` 临时文件 |
| Dashboard 库为空/对不上 | 确认实际库路径（`dashboard/backend/database/polymarket.db`）；必要时 `db.init_db()` 重建 |
| 数据库锁/陈旧 | `cleanup_stale_data.py`、`Whale_States_Stale_Analysis.md` |

日志技巧：`grep "Error" 07-data/logs/monitor.log`、`grep "🐋" 07-data/logs/monitor.log`。
专项运维脚本：`health_checker.py`、`error_monitor.py`、`data_quality_check.py`。

---

## 9. 演进历史与现状评估

### 9.1 版本里程碑（git 历史）

```
46295a2 初始：套利监控系统
b9c5370 加入 7 套交易策略 + 监控系统
c71a2b2 新闻系统 + Dashboard 整合 + V2 设计文档
14305a5 V2-Day1：环境准备 + 基础优化
2e50b6f V2-Day2：鲸鱼跟随策略
119c026 Dashboard Phase 1：套利页/鲸鱼详情/警报数据连接
6131ca7 Phase 2：语义套利引擎 + 跨平台同步 + 人工确认 + 图表数据
5c2c4b6 集成 CLOB API 到 Dashboard 后端
8d4c5d1 Phase 3：质量监控与自动化   ← 当前
```

### 9.2 当前工作区状态（未提交）

`v2.0-development` 分支有大量未提交改动与新增文件：
- 新增分析模块：`hybrid_data_source.py`、`leaderboard_trends.py`、`leaderboard_whale_tracker.py`、`position_rebuilder.py`、`whale_classifier.py`、`database/` 等；
- 多个 `backfill_*.py` 历史数据回填脚本；
- 已删除并被 `.bak` 替代的旧模块（`news_fetcher.py`、`whale_news_connector.py`）。
> 建议：整理 `.bak`、`.bak.YYYYMMDD` 临时文件后再提交，明确哪些进入 V2 主线。

### 9.3 已识别技术债

1. **数据库路径三处不一致**（config / backend / 备份），易踩坑；
2. **绝对路径硬编码**仍存在于部分新模块，未完全收敛到 `config.py`；
3. **阈值默认值在代码与文档/环境变量间不一致**（Pair Cost 0.90 vs 0.995）；
4. 根目录存在空的 `polymarket_data.db`（0 字节），疑似遗留；
5. 大量 `.bak`/归档文件混在源码目录中。

---

## 10. 快速上手清单

```bash
# 0. 查看项目配置（验证路径）
python3 06-tools/analysis/config.py

# 1. 启动离线监控引擎
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
./start_monitor.sh && tail -f /tmp/monitor.log

# 2. 启动 Dashboard（开发）
cd dashboard/backend && python3 -m venv venv && source venv/bin/activate \
  && pip install -r requirements.txt && python3 run.py      # :5000
cd ../frontend && npm install && npm start                   # :3000

# 3. 或 Docker 一键
cd dashboard && docker-compose up -d --build                 # :80

# 4. 跑测试
pytest 10-tests/            # 或 06-tools/analysis 下的 test_*.py
```

**推荐阅读顺序**（深入源码时）：
`PROJECT_STRUCTURE.md` → `06-tools/analysis/config.py` → `06-tools/monitoring/polymarket_monitor_v2.py` → `docs/V2-design-integrated.md` → `dashboard/backend/app/__init__.py` → `dashboard/backend/app/models/database.py` → `docs/deployment-guide-v2.md`。

---

*本文档由代码通读自动整理，如与最新代码冲突，以代码为准。*
*生成时间：2026-06-02*
