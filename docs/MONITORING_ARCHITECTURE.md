# Polymarket 监控程序架构说明

> 文档版本: 1.1
> 最后更新: 2026-03-19
> 作者: 虾头

---

## 概述

Polymarket 监控系统采用**单一综合监控程序**架构，所有监控功能集成在一个程序中统一运行。

---

## 监控程序

### 主程序

**文件**: `06-tools/monitoring/polymarket_monitor_v2.py`

**类型**: 综合监控器（单一程序包含所有功能）

**运行方式**: systemd timer，每 15 分钟触发一次

---

## 功能模块

| 功能模块 | 函数名 | 说明 | 输出目标 |
|---------|--------|------|---------|
| **Pair Cost 套利** | `run_pair_cost_scan()` | YES+NO < 0.995 检测 | 数据库 + Telegram |
| **跨平台套利** | `run_cross_market_scan()` | Polymarket vs Manifold 价差 | 数据库 + Telegram |
| **鲸鱼追踪** | `run_whale_tracking()` | 大户交易监控 | 数据库 + Telegram |
| **鲸鱼跟随** | `run_whale_following_scan()` | 聪明钱策略信号 | 数据库 + Telegram |
| **新闻监控** | `run_news_monitoring()` | 热点新闻影响分析 | Telegram |
| **相关性分析** | `run_correlation_analysis()` | 市场相关性断裂检测 | Telegram |
| **做市机会** | `run_market_making_scan()` | CLOB 订单簿价差扫描 | Telegram |

---

## 系统服务配置

### 定时器

```ini
# ~/.config/systemd/user/polymarket-monitor.timer
[Timer]
OnCalendar=*:0/15  # 每 15 分钟触发
```

### 服务

```ini
# ~/.config/systemd/user/polymarket-monitor.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 .../polymarket_monitor_v2.py
```

### Dashboard 服务

| 服务 | 类型 | 状态 |
|------|------|------|
| `polymarket-dashboard-backend.service` | 持续运行 | ✅ Active |
| `polymarket-dashboard-frontend.service` | 持续运行 | ✅ Active |

---

## 数据流向

```
┌─────────────────────────────────────┐
│  polymarket_monitor_v2.py           │
│  (每 15 分钟运行一次)                │
└──────────────┬──────────────────────┘
               │
    ┌──────────┼──────────┬──────────┬──────────┐
    │          │          │          │          │
    ▼          ▼          ▼          ▼          ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│Pair   │ │Cross  │ │Whale  │ │News   │ │Market │
│Cost   │ │Market │ │Track  │ │Monitor│ │Making │
└───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘
    │         │         │         │         │
    ▼         ▼         ▼         ▼         ▼
┌─────────────────────────────────────────────┐
│           SQLite 数据库                      │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │pair_cost│ │cross_   │ │whales   │       │
│  │arbitrage│ │market   │ │positions│       │
│  └─────────┘ │arbitrage│ │changes  │       │
│              └─────────┘ └─────────┘       │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
            ┌─────────────────┐
            │  Dashboard API  │
            │  (Flask Backend)│
            └────────┬────────┘
                     │
                     ▼
            ┌─────────────────┐
            │ Dashboard       │
            │ (React Frontend)│
            └─────────────────┘
```

---

## 架构优缺点

### 优点

| 优点 | 说明 |
|------|------|
| **统一管理** | 一个程序，统一配置、统一日志 |
| **资源共享** | 共享数据库连接、共享 API 客户端 |
| **依赖简化** | 只需维护一个服务 |
| **一致性** | 所有模块使用相同的数据源 |

### 缺点

| 缺点 | 说明 |
|------|------|
| **无法单独调整频率** | 所有模块都是 5 分钟，无法独立设置 |
| **单点故障** | 一个模块出错可能影响其他模块 |
| **资源竞争** | 所有模块同时运行，可能互相影响性能 |
| **调试困难** | 难以单独测试某个模块 |

---

## 改进建议

### 方案 1: 保持现状

适合当前阶段，简单稳定。

### 方案 2: 按功能拆分

将不同功能拆分为独立的 timer：

```
polymarket-arbitrage.timer      # 套利扫描: 每 5 分钟
polymarket-whale.timer          # 鲸鱼追踪: 每 15 分钟
polymarket-news.timer           # 新闻监控: 每 30 分钟
polymarket-correlation.timer    # 相关性分析: 每 60 分钟
```

**优点**:
- 独立频率控制
- 模块隔离，故障不影响其他
- 可单独启停

**缺点**:
- 维护复杂度增加
- 需要共享数据库连接池

---

## 任务执行时间间隔

### 主监控程序 (polymarket_monitor_v2.py)

| 任务 | 当前频率 | 说明 |
|------|---------|------|
| **综合监控** | 每 15 分钟 | systemd timer 触发 |
| Pair Cost 套利 | 每 15 分钟 | 同一程序内执行 |
| 跨平台套利 | 每 15 分钟 | 同一程序内执行 |
| 鲸鱼追踪 | 每 15 分钟 | 同一程序内执行 |
| 鲸鱼跟随 | 每 15 分钟 | 同一程序内执行 |
| 新闻监控 | 每 15 分钟 | 同一程序内执行 |
| 相关性分析 | 每 15 分钟 | 同一程序内执行 |
| 做市机会 | 每 15 分钟 | 同一程序内执行 |

### 独立定时任务 (crontab)

| 任务 | 频率 | 命令 |
|------|------|------|
| 工作总结报告 | 每 4 小时 | `send_summary.py` |
| 重点鲸鱼概况 | 每 4 小时 | `send_watchlist_summary.py` |
| 鲸鱼新闻关联 | 每 4 小时 | `send_whale_news.py` |
| Top 10 鲸鱼排行 | 每 4 小时 | `send_top_whales_report.py` |
| 评估触发器 | 每小时 | `polymarket_evaluation_trigger.py` |
| 日志清理 | 每天 | `find ... -delete` |

### Dashboard 服务

| 服务 | 类型 | 状态 |
|------|------|------|
| 后端 API | 持续运行 | Flask 服务 |
| 前端界面 | 持续运行 | React 开发服务器 |

### 数据同步 (OpenClaw Cron)

| 任务 | 频率 | 说明 |
|------|------|------|
| sync-changes | 每 5 分钟 | 同步 changes 数据到数据库 |
| work-report-6h | 每 6 小时 | 生成工作报告 |
| daily-lessons-summary | 每天 21:00 | 总结每日经验教训 |

---

## 管理命令

```bash
# 查看定时器状态
systemctl --user status polymarket-monitor.timer

# 查看下次触发时间
systemctl --user list-timers

# 手动触发监控
systemctl --user start polymarket-monitor.service

# 查看监控日志
tail -f ~/.openclaw/workspace/polymarket-project/06-tools/monitoring/monitor.log

# 查看 Dashboard 状态
systemctl --user status polymarket-dashboard-backend
systemctl --user status polymarket-dashboard-frontend
```

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `06-tools/monitoring/polymarket_monitor_v2.py` | 主监控程序 |
| `06-tools/monitoring/clob_api.py` | CLOB API 封装 |
| `dashboard/backend/database/polymarket.db` | SQLite 数据库 |
| `~/.config/systemd/user/polymarket-monitor.*` | systemd 配置 |

---

## 更新记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-03-19 | 1.0 | 初始文档 |
| 2026-03-19 | 1.1 | 更新频率为15分钟，添加任务时间间隔详细说明 |
