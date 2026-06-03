# Polymarket 项目 As-Built 架构图（2026-06-03）

> **这份文档记录"实际建成的样子",不是"设计意图/愿景"。**
> 全部结论基于一手证据:`git log`(提交是作者 `gzxmren <gdming@gmail.com>` 本人所写)、
> 仓库真实代码、运行库 `dashboard/backend/database/polymarket.db` 实查。
> 凡"名义 ≠ 实际"之处明确标出。与 `PROJECT_REVIEW.md` / `PROJECT_OVERVIEW.md` 冲突时,以本文为准
> ——本文含本会话(P0/P1/P2)的整改后状态与新发现。
>
> 背景结论:**项目的设计记录就是 git 历史 + 代码本身。** OpenClaw 的会话历史里只有 5 月的运维任务,
> 对"为什么这么设计"无任何记录——它从来不是架构师,只是后期被叫来打杂的运维工具。

---

## 1. 起源与演化时间线（git 一手）

| 日期 | 提交 | 实际发生了什么 |
|---|---|---|
| 2026-03-12 | `46295a2` Initial commit | **起家命题 = 套利监控**:"Polymarket **arbitrage** monitoring system" |
| 2026-03-12 | `b9c5370` Major update | **同一天就膨胀成"7 大策略"**:VWAP / Kelly 仓位 / 动量(RSI-MA) / 新闻事件 / 相关性跨市场 / 做市价差 / 风险评审 |
| 2026-03-16~17 | `357fe7d` `c71a2b2` | 关注列表动态调整、新闻系统、Dashboard 整合、V2 设计文档 |
| 2026-03~04 | `14305a5` `119c026` `6131ca7` | V2-Day1 基础优化 → Dashboard Phase 1(套利/鲸鱼/警报页) → Phase 2(语义套利、跨平台同步、人工确认) |
| (某次) | `8d4c5d1` | **Phase 3「质量监控与自动化」**:signals.py(388行)+ signal_tracking.py(447行)+ quality_report.py + threshold_optimizer.py + 建 signal_results/quality_reports/threshold_history 表 |
| **2026-04-22 19:02** | — | **核心监控引擎最后一次产出 `monitor_report`,此后停摆。** |
| 2026-04-15~05-10 | (零提交) | 引擎停摆期无任何对应代码改动 → 它是临时跑的,不是被管理的服务,一停没人拉起 |
| 2026-05 | (OpenClaw 运维) | data_sync、data_quality_check、PolyCop 信号检查等运维类任务 |
| 2026-06-03 | 本会话 | P0 数据止血 + WIP 梳理 + 文件清理 + P1 空表处置 + P2 安全/CI/去硬编码路径 |

**核心病根:从第一天就过度设计**(套利→7策略→V2→Phase1/2/3),但**运行层始终是临时拼凑**(无管理调度)。这解释了今天的死代码、空表、数据腐化。

---

## 2. 三大子系统（as-built 实际状态）

### ① 离线监控引擎 — 🔴 **停摆中**
- 入口 `06-tools/monitoring/polymarket_monitor_v2.py`。设计为每 5 分钟扫市场/交易/持仓,跑 Pair-Cost + 跨市场套利 + 鲸鱼追踪,评分推 Telegram,写 `07-data/*.json`。
- **实际:2026-04-22 起无进程、无 cron、无 systemd。代码本身仍可跑**(上次手测取市场+扫描无报错,但 0 套利机会 @ PAIR_COST=0.90)。
- 这是 `pair_cost_arbitrage` / `signals` 等表为空的根因。

### ② Web Dashboard — 🟢 **在跑**
- 后端 Flask+SocketIO+APScheduler:`dashboard/backend/run.py`(:5000),由 **systemd --user `polymarket-dashboard-backend.service`** 管理。
- 前端 React/TS/AntD(:3000),systemd `polymarket-dashboard-frontend.service`。
- ⚠️ 这两个是 systemd 服务,**重启用 `systemctl --user restart …`,不能 kill**(会自动复活跑旧代码)。
- 调度真相:后端 `scheduler.py` **只注册了一个任务** `data_sync.run_full_sync`,每 5 分钟一次。它内部只调 `sync_whales + sync_alerts + sync_cross_market_arbitrage`。

### ③ 共享数据层 — 🟡 **部分**
- 名义:引擎写 `07-data/*.json` → `data_sync.py` 桥接 → SQLite → 前端读。
- 实际:**引擎停摆,JSON 不再更新**;data_sync 每 5 分钟仍在跑,但主要消费的是历史 JSON + 实时 API + `changes` 表聚合。
- 唯一权威 sync 实现 = `data_sync.py`(`data_sync_v2.py` 本会话已确认死代码并删除)。

---

## 3. 数据流真相（谁写谁读）

```
                    [Polymarket Gamma/Data/CLOB API]
                               │
        ┌──────────────────────┼───────────────────────┐
        │                      │                        │
 polymarket_monitor_v2    sync_changes.py        leaderboard_whale_tracker.py
   🔴 停摆(无调度)        ⚠️ 触发源不明           cron 每周一 3:00
        │                      │                        │
   07-data/*.json         写 changes 表 +            写 leaderboard_* 表
   (停在 04-22)           whales(trade-flow 行)
        │                      │                        │
        └──────────┐           │                        │
                   ▼           ▼                         ▼
            ┌─────────────────────────────────────────────────┐
            │  data_sync.run_full_sync  (后端调度,每 5 分钟)    │
            │  sync_whales / sync_alerts / sync_cross_market    │
            └─────────────────────────────────────────────────┘
                               │
                               ▼
                  SQLite polymarket.db  ──►  Flask API ──► React 前端
```

**⚠️ 未决问题(Q4):`sync_changes.py` 的触发源至今未定位** —— 它不在 cron、无常驻进程、后端不调它,但 `changes` 表有 30118 行且今天仍在增长(最近成交 13:33)。它产生的"有成交流水、无持仓快照"的 trade-flow 鲸鱼是合法信号源(见 §5)。**触发它的上层(疑某次手动/agent 临时调用)需查清,否则数据流不可控。**

---

## 4. 数据库实况（实库 22 张表）

> 库:`dashboard/backend/database/polymarket.db`(本会话止血后 ~50MB,死空间 0%)。
> ⚠️ `config.py` 旧版指向 `dashboard/database`(仅软链),本会话已修正为真实路径 + 支持 `POLYMARKET_DB` 覆盖。

**有数据(承载业务):**
| 表 | 行数 | 说明 |
|---|---|---|
| `changes` | 30118 | 鲸鱼成交流水(sync_changes 写) |
| `concentration_history` | 67930 | 持仓集中度历史 |
| `market_title_map` | 46656 | 市场标题映射 |
| `daily_price_snapshots` | 39439 | 每日价格快照 |
| `positions_archive` | 38419 | 持仓归档(3.4× positions) |
| `positions` | 11284 | 当前持仓 |
| `whales` | 1852 | 鲸鱼(止血后:真空壳 0,1633 有持仓,210 trade-flow,垃圾率 0%) |
| `leaderboard_history/_trends/_whales` | 275/169/169 | Leaderboard(周更) |
| `alerts` | 50 | 警报 |
| `whale_deep_analysis` | 11 | 鲸鱼深度分析 |
| `arbitrage_feedback` | 2 | 套利人工反馈 |
| `cross_market_arbitrage` | 1 | 跨市场套利(data_sync 产) |

**空表(8 张)—— 引擎停摆 / 调度没接上的直接证据:**
| 表 | 为什么空 |
|---|---|
| `pair_cost_arbitrage` | 🔴 **核心套利输出**,引擎停摆故无产出 |
| `signals` `signal_results` | Phase-3 代码写了,产数据的调度链没接上 |
| `whale_performance` `strategy_performance` | 同上(P3 绩效/回测预留) |
| `quality_reports` `threshold_history` | Phase-3 质量自动化,服务未调度 |
| `opportunity_history` | P3 回测预留,无写入路径运行 |

---

## 5. 本会话整改后的已知状态（防回归基准）

- **数据止血**:whales 72,867→1,852;真空壳(全0且无成交流水)=**0**;DB 死空间 48%→0%;has_activity 错标 70,973→0。
- **价值闸门**:`data_sync.sync_whales` 的价值闸门是本会话 `49a5640` **首次加入**(git 证实:之前从来没有,腐化是"裸写"而非"闸门被删")。
- **垃圾定义**(`a07dda3`):统一为「total_value≤0 且 position_count=0 且 非关注 且 changes_count=0 且 total_volume=0」。**trade-flow 鲸鱼**(有成交流水无持仓快照)是合法的跟鲸鱼信号源,不算垃圾,has_activity=1 正确。`scripts/cleanup_whales.py` 与 `scripts/data_health_check.py` 共用此定义。
- **健康闸门**:`data_health_check.py --assert`(垃圾率<5% / 死空间<10% / has_activity 错标=0)三项全绿,已进 CI。
- **去硬编码路径**(`b1217c8`):32 处 `/home/xmren` 绝对路径全改为 `Path(__file__)` 推导 + `POLYMARKET_DB` 覆盖。
- **密钥**:旧 token 公网泄露已 revoke;新 token 仅存于两个 gitignored `.env`;明文 loop 脚本已删。

---

## 6. 死代码 / 僵尸组件清单（已处理 + 待定）

| 组件 | 状态 |
|---|---|
| `data_sync_v2.py` | ✅ 已删(确认死代码) |
| `whale_alerts` 表 + leaderboard 里的 CREATE | ✅ 已删(与 alerts 冗余) |
| `quality_logs` 表 | ✅ 已删(被 data_health_check 覆盖) |
| 根目录 0 字节 `polymarket_data.db` | ✅ 已删 |
| `polymarket_monitor_v2.py` 引擎 | 🟡 **停摆但代码可用**,复活 or 退役待定(P2/P3) |
| Phase-3 信号/质量代码(signals.py 等) | 🟡 **建好没通电**,接调度 or 删待定 |
| `06-tools/trading/` | ℹ️ 仅 README,无下单层(项目本就 read-only/advisory) |
| `polymarket-monitor-loop.sh`(workspace 外) | ✅ 已删(引用的 comprehensive-monitor 根本不存在,本就是坏的) |

---

## 7. 对"交易赚钱地基"的影响（可信度分级）

- **可直接信的数据**:`positions` / `changes` / `concentration_history` / `daily_price_snapshots` / `leaderboard_*`(有真实写入路径、止血后干净)。
- **半信**:`whales`(止血后干净,但 trade-flow 与持仓两类语义不同,回测时要分清)。
- **不可堆策略的**:8 张空表代表的信号/绩效/回测能力**尚不存在**——必须先"通电产数据"或重建,才能谈回测。
- **里程碑纪律**:M1(数据干净,✅ 已达)前不开新功能;M4(信号经回测出正期望、含手续费/滑点)前不投实盘。

---

## 8. 待办与未决（按优先级）

1. 🔴 **定位 `sync_changes` 触发源**(§3 Q4)——数据流不可控的唯一缺口。
2. 🟡 **引擎去留决策**:复活 `polymarket_monitor_v2` 并做成 systemd 管理服务,还是退役?这决定 `pair_cost_arbitrage` 等表能否产数据。
3. 🟡 **Phase-3 去留**:接调度让 signals/quality 系列产数据,还是删表清理?
4. ⚙️ **P2 剩余工程化**:③ logging(scoped)→ ④ gunicorn(替换开发服务器)→ ⑤ schema 迁移规范。
5. 🎯 **P3 回测框架**:通用框架 → Pair-Cost → 跨市场 → 跟鲸鱼(按序验证,先验证再投实盘)。

---

*生成:2026-06-03 | 依据:git 全历史 + 实库实查 + 本会话 P0/P1/P2 整改记录*
