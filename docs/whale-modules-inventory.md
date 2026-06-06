# Polymarket 鲸鱼模块完整清单

**整理时间**: 2026-04-05 18:22 GMT+8

---

## 📊 模块总览

```
┌─────────────────────────────────────────────────────────────────┐
│                    鲸鱼功能模块架构                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  数据层 (Data Layer)                                      │  │
│  │  ├── whale_states/*.json (持仓快照)                      │  │
│  │  ├── whale_watchlist.json (关注列表)                    │  │
│  │  └── database (SQLite)                                   │  │
│  │      ├── whales (鲸鱼信息)                               │  │
│  │      ├── positions (持仓明细)                            │  │
│  │      ├── changes (交易变动)                              │  │
│  │      ├── leaderboard_whales (排行榜鲸鱼)                 │  │
│  │      └── leaderboard_history (历史记录)                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  分析层 (Analysis Layer)                                  │  │
│  │  ├── whale_tracker.py (基础跟踪) 🗑️ 已归档            │  │
│  │  ├── whale_tracker_v2.py (增强版) 🗑️ 已归档           │  │
│  │  ├── whale_watchlist.py (关注管理) 🗑️ 已归档          │  │
│  │  ├── whale_following.py (跟随策略) ✅ 在用              │  │
│  │  ├── whale_news_connector.py (新闻关联) ✅ 在用         │  │
│  │  ├── whale_classifier.py (智能分类) ✅ 在用 ⭐新增             │  │
│  │  └── leaderboard_whale_tracker.py (排行榜) ✅ 在用 ⭐新增      │  │
│  ├── leaderboard_trends.py (趋势分析) ✅ 在用 ⭐新增    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  监控层 (Monitoring Layer)                                │  │
│  │  ├── sync_changes.py (实时同步) ✅ 核心                 │  │
│  │  ├── data_sync.py (定期同步) ✅ 核心                    │  │
│  │  ├── send_whale_news.py (新闻报告) ✅ Cron              │  │
│  │  ├── send_top_whales_report.py (Top10报告) ✅ Cron      │  │
│  │  └── update_whale_names_safe.py (命名更新) ✅ 备用      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  服务层 (Service Layer)                                   │  │
│  │  ├── whale_analyzer.py (分析服务) ✅ 在用               │  │
│  │  ├── whale_deep_analyzer.py (深度分析) 🗑️ 已归档      │  │
│  │  └── data_sync.py (数据同步) ✅ 在用                    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  展示层 (Presentation Layer)                              │  │
│  │  ├── Dashboard - 鲸鱼列表                                │  │
│  │  ├── Dashboard - 鲸鱼详情                                │  │
│  │  ├── Dashboard - 顶级鲸鱼 (新增) ✅                     │  │
│  │  └── Telegram 通知                                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## ✅ 活跃模块 (在用)

### 1. 核心同步模块

#### sync_changes.py
**路径**: `06-tools/monitoring/sync_changes.py`  
**状态**: ✅ 核心运行中  
**功能**:
- 实时获取 Polymarket 交易数据
- 保存交易变动到 changes 表
- 同步 API 用户名到 whales 表
- 触发频率: 外部定时调用

**依赖**: data-api.polymarket.com

---

#### data_sync.py
**路径**: `dashboard/backend/app/services/data_sync.py`  
**状态**: ✅ 核心运行中  
**功能**:
- 从 whale_states/*.json 读取持仓数据
- 同步到 Dashboard 数据库
- 计算集中度指标
- 保护已有用户名不被覆盖

**依赖**: whale_states 文件

---

### 2. 分析模块

#### whale_classifier.py ⭐ 新增
**路径**: `06-tools/analysis/whale_classifier.py`  
**状态**: ✅ 在用 (2026-04-05 新增)  
**功能**:
- 多维度鲸鱼分类 (资金/活跃度/盈利/策略/趋势)
- 综合评分系统 (S/A/B/C/D 级)
- 鲸鱼画像分析

**使用方式**: 
```bash
python3 whale_classifier.py
```

---

#### leaderboard_whale_tracker.py ⭐ 新增
│  ├── leaderboard_trends.py (趋势分析) ✅ 在用 ⭐新增    │  │
**路径**: `06-tools/analysis/leaderboard_whale_tracker.py`  
│  ├── leaderboard_trends.py (趋势分析) ✅ 在用 ⭐新增    │  │
**状态**: ✅ 在用 (2026-04-05 新增)  
**功能**:
- 获取 Polymarket 官方排行榜
- 自动分级 (Critical/High/Medium/Low)
- Telegram 告警
- 高优先级交易监控

**使用方式**:
```bash
# 手动同步
python3 leaderboard_whale_tracker.py --manual --notify
│  ├── leaderboard_trends.py (趋势分析) ✅ 在用 ⭐新增    │  │

# Cron (每周一 3 点)
0 3 * * 1 python3 leaderboard_whale_tracker.py
│  ├── leaderboard_trends.py (趋势分析) ✅ 在用 ⭐新增    │  │
```

---

#### whale_following.py
**路径**: `06-tools/analysis/whale_following.py`  
**状态**: ✅ 在用  
**功能**:
- 鲸鱼跟随策略
- 聪明钱识别
- 交易信号生成

---

#### whale_news_connector.py
**路径**: `06-tools/analysis/whale_news_connector.py`  
**状态**: ✅ 在用  
**功能**:
- 鲸鱼持仓与新闻关联
- 新闻驱动策略

**Cron**:
```bash
0 12,18 * * * python3 send_whale_news.py --top 3
```

---

### 3. 监控报告模块

#### send_whale_news.py
**路径**: `06-tools/monitoring/send_whale_news.py`  
**状态**: ✅ Cron 运行中  
**功能**:
- 每日 2 次发送鲸鱼新闻关联报告
- Telegram 推送

**Cron**: `0 12,18 * * *`

---

#### send_top_whales_report.py
**路径**: `06-tools/monitoring/send_top_whales_report.py`  
**状态**: ✅ Cron 运行中  
**功能**:
- 每日 20:00 发送 Top 10 鲸鱼报告
- 持仓变动、盈亏统计

**Cron**: `0 20 * * *`

---

### 4. 服务层模块

#### whale_analyzer.py
**路径**: `dashboard/backend/app/services/whale_analyzer.py`  
**状态**: ✅ 在用  
**功能**:
- Dashboard 后端分析服务
- 鲸鱼数据统计
- API 数据提供

---

### 5. Dashboard 模块

#### Whales.tsx
**路径**: `dashboard/frontend/src/pages/Whales.tsx`  
**状态**: ✅ 在用  
**功能**:
- 鲸鱼列表展示
- 排序、筛选
- 关注功能

---

#### WhaleDetail.tsx
**路径**: `dashboard/frontend/src/pages/WhaleDetail.tsx`  
**状态**: ✅ 在用  
**功能**:
- 单个鲸鱼详情
- 持仓明细
- 变动历史

---

#### LeaderboardWales.tsx ⭐ 新增
**路径**: `dashboard/frontend/src/pages/LeaderboardWales.tsx`  
**状态**: ✅ 在用 (2026-04-05 新增)  
**功能**:
- 官方排行榜鲸鱼展示
- 手动同步按钮
- 优先级标签
- 统计卡片

---

## 🗑️ 已归档的模块

### 1. whale_tracker.py
**路径**: `06-tools/analysis/whale_tracker.py`  
**状态**: 🗑️ 已归档  
**原因**:
- 创建时间: 2026-03-12 (最早版本)
- 功能已被 whale_tracker_v2.py 替代
- 最后修改: 3 月 12 日
- **建议**: 检查是否还在使用，如无使用可删除

---

### 2. whale_tracker_v2.py
**路径**: `06-tools/analysis/whale_tracker_v2.py`  
**状态**: 🗑️ 已归档  
**原因**:
- 创建时间: 2026-03-25
- 功能已被 sync_changes.py + data_sync.py 替代
- 最后修改: 3 月 26 日
- 有 .bak 备份文件
- **建议**: 检查是否还在使用，如无使用可删除

---

### 3. whale_watchlist.py
**路径**: `06-tools/analysis/whale_watchlist.py`  
**状态**: 🗑️ 已归档  
**原因**:
- 创建时间: 2026-03-17
- 关注列表功能已集成到 Dashboard
- 数据已迁移到数据库
- **建议**: 检查是否还在使用，如无使用可删除

---

### 4. whale_deep_analyzer.py
**路径**: `dashboard/backend/app/services/whale_deep_analyzer.py`  
**状态**: 🗑️ 已归档  
**原因**:
- 有 bug 报告文档: `whale-deep-analysis-bug-report.md`
- 功能可能被 whale_analyzer.py 替代
- **建议**: 检查使用情况和 bug 修复状态

---

### 5. .bak 备份文件
**文件**:
- `whale_news_connector.py.bak`
- `whale_tracker_v2.py.bak`

**状态**: ⚠️ 废弃  
**建议**: 可删除

---

## 📊 模块使用统计

| 模块类别 | 数量 | 在用 | 废弃/可疑 |
|---------|------|------|----------|
| 核心同步 | 2 | 2 | 0 |
| 分析模块 | 6 | 4 | 2 |
| 监控报告 | 4 | 4 | 0 |
| 服务层 | 3 | 2 | 1 |
| Dashboard | 3 | 3 | 0 |
| **总计** | **18** | **15** | **3** |

---

## 🎯 建议清理清单

### 高优先级 (可安全删除)
1. [ ] `whale_tracker.py` - 确认无使用后删除
2. [ ] `whale_tracker_v2.py.bak` - 备份文件，可删除
3. [ ] `whale_news_connector.py.bak` - 备份文件，可删除

### 中优先级 (需确认)
4. [ ] `whale_tracker_v2.py` - 确认功能已被替代后删除
5. [ ] `whale_watchlist.py` - 确认数据已迁移后删除

### 低优先级 (需评估)
6. [ ] `whale_deep_analyzer.py` - 评估 bug 修复情况

---

## 🔧 模块依赖关系

```
sync_changes.py
    ├──→ changes 表
    ├──→ whales 表 (用户名)
    └──→ Telegram 告警 (可选)

data_sync.py
    ├──→ whale_states/*.json
    ├──→ whales 表 (持仓)
    ├──→ positions 表
    └──→ Dashboard API

whale_classifier.py
    └──→ database (whales, changes, positions)

leaderboard_whale_tracker.py
│  ├── leaderboard_trends.py (趋势分析) ✅ 在用 ⭐新增    │  │
    ├──→ Polymarket Leaderboard API