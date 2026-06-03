# Polymarket 项目 As-Built 架构图（2026-06-03）

> **这份文档记录"实际建成的样子",不是"设计意图/愿景"。**
> 全部结论基于一手证据:`git log`、仓库真实代码、运行库实查、**OpenClaw 每日工作日志
> `~/.openclaw/workspace/memory/*.md`** 与 **OpenClaw cron 定义 `~/.openclaw/cron/jobs.json`**。
> 凡"名义 ≠ 实际"之处明确标出。与 `PROJECT_REVIEW.md` / `PROJECT_OVERVIEW.md` 冲突时,以本文为准。
>
> **关键背景结论(2026-06-03 修订):OpenClaw 是本项目的主要运维者、调度者与部分开发者。**
> 整个**调度编排层在 OpenClaw 的 cron 系统里**(`~/.openclaw/cron/jobs.json`),**不在仓库、也不在系统 crontab**
> ——这是从仓库内部完全看不见的一层。设计/运维缘由记录在 `~/.openclaw/workspace/memory/*.md`
> 每日工作日志(不是 `sessions/*.jsonl`;早先向 OpenClaw 问缘由得到"无记录"是因为它搜错了文件)。
> 项目代码由作者 `gzxmren <gdming@gmail.com>` 本人 + OpenClaw 子代理协作产出。

---

## 1. 起源与演化时间线（git 一手）

| 日期 | 提交 | 实际发生了什么 |
|---|---|---|
| 2026-03-12 | `46295a2` Initial commit | **起家命题 = 套利监控**:"Polymarket **arbitrage** monitoring system" |
| 2026-03-13 | (工作日志) | **真实命题更具体**(memory/2026-03-13.md):做市价差 + Pair-Cost 套利 + 鲸鱼追踪;目标"申请 API Key、小资金测试做市策略"。v1.0(11:03)只测价差;v2.0(13:34)加 Pair-Cost 阈值。**最初由 OpenClaw 后台子代理每 5 分钟跑一次**(`agent:main:subagent:9d0092a7...`)。脚本演化:`polymarket_monitor_unified.py` → … → `polymarket_monitor_v2.py`。 |
| 2026-03-12 | `b9c5370` Major update | **同一天就膨胀成"7 大策略"**:VWAP / Kelly 仓位 / 动量(RSI-MA) / 新闻事件 / 相关性跨市场 / 做市价差 / 风险评审 |
| 2026-03-16~17 | `357fe7d` `c71a2b2` | 关注列表动态调整、新闻系统、Dashboard 整合、V2 设计文档 |
| 2026-03~04 | `14305a5` `119c026` `6131ca7` | V2-Day1 基础优化 → Dashboard Phase 1(套利/鲸鱼/警报页) → Phase 2(语义套利、跨平台同步、人工确认) |
| (某次) | `8d4c5d1` | **Phase 3「质量监控与自动化」**:signals.py(388行)+ signal_tracking.py(447行)+ quality_report.py + threshold_optimizer.py + 建 signal_results/quality_reports/threshold_history 表 |
| 2026-04-21 | (工作日志) | **OpenClaw exec-preflight 故障**:拒绝 `cd /path && python3` 格式命令,一次打挂 monitor-v2 / sync-changes / update-whale-states 等多个 OpenClaw cron job;当天改绝对路径修复。同日把 Pair-Cost 阈值 0.995→0.90,但发现是**算法缺陷**(中间价算 yes+no 恒=1.0),非阈值问题,Pair-Cost 仍=0。 |
| **2026-04-22 19:02** | — | **重型引擎最后一次产出 `monitor_report`。** |
| 2026-04-23+ | (工作日志) | OpenClaw **cron lane 拥堵 + LLM 超时**,多个 job 失败/禁用;`polymarket-monitor-v2` 最终 `enabled:False` 退役,由 `polymarket-monitor-lite`(每小时)顶替。 |
| 2026-05 | (OpenClaw 运维) | data_sync、data_quality_check、PolyCop 信号检查等运维类任务 |
| 2026-06-03 | 本会话 | P0 数据止血 + WIP 梳理 + 文件清理 + P1 空表处置 + P2 安全/CI/去硬编码路径 |

**核心病根:从第一天就过度设计**(套利→7策略→V2→Phase1/2/3),但**运行层始终是临时拼凑**(无管理调度)。这解释了今天的死代码、空表、数据腐化。

---

## 2. 三大子系统（as-built 实际状态）

### ① 离线监控引擎 — 🔴 **重型 v2 已禁用/退役,轻量 lite 顶上**
- 入口 `06-tools/monitoring/polymarket_monitor_v2.py`。设计为每 5 分钟扫市场/交易/持仓,跑 Pair-Cost + 跨市场套利 + 鲸鱼追踪,评分推 Telegram,写 `07-data/*.json`。
- **真相(OpenClaw cron):`polymarket-monitor-v2` job `enabled: False` —— 是被显式禁用,不是"自然停"。** 2026-04-21 OpenClaw exec-preflight 故障打挂它(连同 sync-changes 等多个 job),当天修过;04-23 起 cron lane 拥堵 + LLM 超时,引擎彻底没救回,随后被禁用。最后报告 `monitor_report_20260422_190215.json`。
- **替代者:`polymarket-monitor-lite`(`enabled: True`,每小时)在跑** —— 轻量版顶替了重型 v2。
- 代码本身仍可跑(手测取市场+扫描无报错,但 0 套利机会 @ PAIR_COST=0.90)。`pair_cost_arbitrage` 表空,根因是 v2 退役 + Pair-Cost 算法本身有缺陷(见 §1 注:用中间价算 yes+no 恒=1.0,需改用 CLOB orderbook 重构)。

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

**真正的调度层 = OpenClaw cron(`~/.openclaw/cron/jobs.json`),不在仓库、不在系统 crontab。**
实际在跑的数据生产者:

| 调度者 | job / 机制 | 周期 | 状态 | 写什么 |
|---|---|---|---|---|
| **OpenClaw cron** | `polymarket-monitor-lite` | 每小时 | ✅ | 轻量监控(替代 v2) |
| **OpenClaw cron** | `sync-changes` | **每 90 分钟** | ✅ | `changes` 表 + whales(trade-flow 行) |
| **OpenClaw cron** | `update-whale-states` | 每 6h | ✅ | `07-data/whale_states/*.json` |
| **OpenClaw cron** | `polymarket-daily-price-snapshot` | 每天 16:00 | ✅ | `daily_price_snapshots` |
| **OpenClaw cron** | `data-quality-check` | 每天 8:00 | ✅ | 跑 `data_quality_check.py`(与 `scripts/data_health_check.py` 重叠,待整合) |
| **OpenClaw cron** | `check-polycop-signal` | 每 6h | ✅ | PolyCop 信号检查 |
| **OpenClaw cron** | `polymarket-cleanup` | 每天 3:30 | ✅ | 清理 |
| **OpenClaw cron** | `polymarket-monitor-v2` | 每小时 | 🔴 禁用 | (重型引擎,已退役) |
| **OpenClaw cron** | `update-whale-states`(旧) | 每 6h | 🔴 禁用 | 配置冗余:与上面启用的同名 job 重复,旧的未清 |

> 注:cron 状态可在 **OpenClaw web admin UI** 直接查看执行情况,无需自建看门狗。上表为 2026-06-03 快照,以 admin UI 实时状态为准。
| **系统 crontab** | `leaderboard_whale_tracker.py` | 周一 3:00 | ✅ | `leaderboard_*` 表 |
| **系统 crontab** | 日志清理 | 每天 0:00 | ✅ | 删旧日志 |
| **后端 APScheduler** | `data_sync.run_full_sync` | 每 5 分钟 | ✅ | 桥接 JSON/聚合 → SQLite(sync_whales/alerts/cross_market) |

```
                    [Polymarket Gamma/Data/CLOB API]
                               │
   ┌───────────────────────────┼──────────────────────────────┐
   │ (OpenClaw cron)            │ (OpenClaw cron 每90min)        │ (系统cron 周一)
 monitor-lite(每小时)      sync-changes.py              leaderboard_whale_tracker
 monitor-v2 🔴禁用          写 changes + trade-flow鲸鱼        写 leaderboard_*
   │                            │                               │
 07-data/*.json ◄── update-whale-states(OpenClaw cron 每6h)     │
   │                            │                               │
   └──────────┐                 │                               │
              ▼                 ▼                               ▼
       ┌─────────────────────────────────────────────────┐
       │  data_sync.run_full_sync  (后端APScheduler,每5min)│
       │  sync_whales / sync_alerts / sync_cross_market    │
       └─────────────────────────────────────────────────┘
                               │
                               ▼
                  SQLite polymarket.db  ──►  Flask API ──► React 前端
```

**✅ Q4 已解决:`sync_changes` 由 OpenClaw cron job `sync-changes` 每 90 分钟触发**(`everyMs:5400000`+stagger),这就是它"不在系统 crontab、无常驻进程、运行时间点不规则"的原因,也是 `changes` 表持续增长的来源。它产生的 trade-flow 鲸鱼是合法信号源(见 §5)。
**⚠️ 残留隐患:这个 sync-changes 走的是本会话已确认"无价值闸门保护"的旧写入路径** —— 它会持续产生 trade-flow 行。当前垃圾定义已把它们判为合法(非垃圾),但若 OpenClaw cron 再次故障/拥堵,数据新鲜度会悄悄退化而无人知(正如 v2 引擎当初的下场)。**建议给这些 OpenClaw cron job 加健康监控告警。**

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
- **价值闸门**:`data_sync.sync_whales` 的价值闸门是本会话 `49a5640` **首次加入**(git 证实:之前从来没有,腐化是"裸写"而非"闸门被删")。**运维侧帮凶**:OpenClaw 工作日志(04-24 等)反复把 whales 无界增长(命名鲸鱼 14k→24k、总记录 16万→20万)记为"数据增长稳定/正常",即**把垃圾累积误读为健康** → 无闸门 + 运维误判,双重导致 7.2 万空壳。
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

1. ✅ ~~定位 sync_changes 触发源~~ —— 已解决(OpenClaw cron `sync-changes`,每 90 分钟)。~~加健康监控看门狗~~ —— **不需要,OpenClaw web admin UI 已可查 cron 执行状态**。残留小事:清掉重复的 `update-whale-states` 禁用项;整合 `data-quality-check` cron 与 `scripts/data_health_check.py`。
2. 🟡 **引擎去留决策**:重型 `polymarket_monitor_v2` 已被 OpenClaw cron 禁用、由 `lite` 顶替。要不要复活 v2(需先按 §1 用 CLOB orderbook 重构 Pair-Cost 算法,否则 `pair_cost_arbitrage` 永远是 0)?还是正式退役、只保留 lite?
3. 🟡 **Phase-3 去留**:接调度让 signals/quality 系列产数据,还是删表清理?
4. ⚙️ **P2 剩余工程化**:③ logging(scoped)→ ④ gunicorn(替换开发服务器)→ ⑤ schema 迁移规范。
5. 🎯 **P3 回测框架**:通用框架 → Pair-Cost → 跨市场 → 跟鲸鱼(按序验证,先验证再投实盘)。

---

*生成:2026-06-03 | 依据:git 全历史 + 实库实查 + 本会话 P0/P1/P2 整改记录*
