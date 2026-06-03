# Polymarket 项目完善方案

> 版本: v1.0 ｜ 生成日期: 2026-06-03 ｜ 分支: v2.0-development
> 目标: **提升数据质量、优化系统架构**,为今后基于 Polymarket 的量化交易打下可信、可验证、可扩展的基础。
> 依据: 2026-06-02 全面评审 + 2026-06-03 对运行库 `dashboard/backend/database/polymarket.db` 的实测复核。

---

## 决策定稿(2026-06-03,经确认)

| 议题 | 决定 | 含义 |
|---|---|---|
| 节奏纪律 | **严格先止血** | P0 数据干净(垃圾率<5%)前**冻结一切新功能开发** |
| 清洗力度 | **标准:删空行** | 仅删 `total_value=0 且 pos=0` 的空行,剩 ~1,634 行,保留全部有效鲸鱼 |
| 实盘资金 | **严格:先验证再投** | 信号经回测跑出正期望(含手续费/滑点)前,一分实盘不投 |
| 回测策略 | **四个全要,按序推进** | 先建通用回测框架(地基)→ 再按 ① Pair-Cost → ② 跨市场 → ③ 跟鲸鱼 逐个验证 |

---

## 0. 指导原则(先读这一段)

1. **先止血,再造血。** 当前 98% 的鲸鱼数据是垃圾。**绝不在脏数据上叠加新策略或新功能** —— 否则信号、回测、PnL 全部建立在污染样本上,越努力越亏。
2. **数据可信 > 功能数量。** 交易系统的护城河是"数据干净 + 信号被回测验证",不是页面多。本方案 70% 的精力投在数据层和验证层。
3. **每一步可验证。** 每个任务都给出"验收 SQL/命令",做完能当场量化确认,不靠感觉。
4. **执行层暂不碰。** 当前系统是**只读/建议型**,无下单层。本方案是"赚钱的地基"(干净数据 + 可回测信号),不是下单引擎。下单/资金/私钥是独立的后续项目,需单独的风控与授权评审。

---

## 1. 现状实测快照(2026-06-03)

| 指标 | 实测值 | 健康标准 | 状态 |
|---|---|---|---|
| `whales` 总行数 | 72,855 | — | |
| └ 垃圾行 (value=0 且 pos=0) | **71,221 (97.8%)** | <5% | 🔴 |
| └ 真鲸鱼 (>$100k / >$10k) | 214 / 720 | — | |
| `has_activity=1` 中的垃圾行 | **70,961 / 71,250 (99.6%)** | ~0 | 🔴 |
| DB 死空间 (freelist) | **16,364 页 / 48.3%** | <10% | 🔴 |
| Phase-3 空表 | 9 张全 0 行 | 有数据 | 🔴 |
| `positions_archive` vs `positions` | 38,419 vs 11,245 (3.4×) | 有 TTL | 🟠 |
| 文件堆积 | 158 报告 / 282 whale_states / 10 `.bak` | 轮转 | 🟠 |
| 双 sync 实现 | `data_sync.py`(在用) + `data_sync_v2.py`(死代码) | 单一 | 🟠 |

**根因(已定位到行):**
- `data_sync.py::sync_whales` (L69–199):构建阶段无价值闸门,value=0/pos=0 的状态直接落库。
- `data_sync.py:181`:`has_activity = CASE WHEN excluded.has_activity=1 THEN 1 ELSE whales.has_activity END` → 单调置位,一旦为 1 永不复位。
- `config.DBTables.SIGNALS='semantic_signals'` 与真实表名 `signals` 漂移。

---

## 2. 分阶段方案

> 优先级 P0→P3。P0 是阻塞项,未完成前**冻结新功能开发**。每阶段标注预估工作量(人日,单人)。

### 🔴 P0 — 数据止血(目标 2–3 人日,本周必须完成)

| # | 任务 | 关键改动 | 验收 |
|---|---|---|---|
| P0-1 | **修 `has_activity` 复位 bug** | `data_sync.py:181` 改为 `has_activity = excluded.has_activity` | 跑一轮 sync 后 `has_activity=1 AND total_value=0` 行数应 ≈ 0 |
| P0-2 | **落库加价值闸门** | `sync_whales` 循环内,INSERT 前加 `if total_value < 1000 and position_count == 0: continue`(阈值取自 `config.Thresholds`,不硬编码) | 新增垃圾行增长率归零 |
| P0-3 | **一次性清洗历史垃圾** | 写 `scripts/cleanup_whales.py`:`DELETE FROM whales WHERE total_value=0 AND position_count=0`;先 `make backup` | `whales` 总行数 72,855 → ~1,634 |
| P0-4 | **VACUUM 回收死空间** | 清洗后执行 `VACUUM;` + 设 `PRAGMA auto_vacuum=INCREMENTAL`(需重建生效) | DB 138MB → 预计 <30MB,freelist <10% |
| P0-5 | ~~修表名漂移~~ **(复核后撤销)** | 2026-06-03 复核:`config.DBTables.SIGNALS` 是**未被任何代码引用的死常量**;真实语义表 `semantic_signals` 由 `semantic_scanner.py`/`api/semantic.py` 按需建表使用;`signals` 是另一张独立的 Phase-3 空表。原 review 描述不准确。→ 不改 config,改为 P1 阶段清理死常量 | — |
| P0-6 | **删遗留垃圾文件** | 删根目录 0 字节 `polymarket_data.db`、10 个 `*.bak`;归档/压缩 158 个 `monitor_report_*.json` | 源码树无 `.bak`,根无空 db |
| P0-7 | **文件轮转机制** | `whale_states/` 与 `monitor_report_*` 加保留策略(如保留最近 7 天 / N 个),写成 `scripts/rotate_data.py` 由 cron 调用 | 文件数稳定不再单调增长 |

**P0 完成判据(一条命令出报告):** `whales` 垃圾率 <5%、freelist <10%、`has_activity` 无错标、源码树无 `.bak`。建议把这些断言写进 `scripts/data_health_check.py`,后续每天 cron 跑并告警。

### 🟠 P1 — 让数据"流起来"(目标 3–5 人日)

| # | 任务 | 说明 |
|---|---|---|
| P1-1 | **收敛双 sync 实现** | 确认 `data_sync_v2.py` 无引用后删除(`scheduler.py:14` 只用 `data_sync.py`);把 v2 中有价值的逻辑(若有)合并回主实现,消除"权威不明" |
| P1-2 | **打通 Phase-3 调度链** | 9 张空表(signals/signal_results/whale_performance/strategy_performance/quality_reports/quality_logs/threshold_history/opportunity_history/pair_cost_arbitrage)代码已提交但没人调用 → 排查 `scheduler.py` 缺哪些 job,逐张接通或**明确删除**未用表(不要留空表当摆设) |
| P1-3 | **`positions_archive` TTL** | 加归档保留窗口(如 90 天),超期清理;明确 positions vs archive 的语义边界 |
| P1-4 | **数据健康看板** | 把 P0-7 的 `data_health_check.py` 结果接入 Dashboard 一个 `/health` 页,数据腐化可视化、可监控 |

### 🟡 P2 — 工程化基建(目标 5–8 人日)

| # | 任务 | 说明 |
|---|---|---|
| P2-1 | **统一 logging** | 监控目录 612 处 `print` / 0 处 `logging` → 引入 `logging` + 统一格式 + 文件轮转 handler;分级 INFO/WARN/ERROR |
| P2-2 | **收敛异常处理** | 59+ 宽泛 `except` + 10 处裸 `except` 吞错 → 改为捕获具体异常并记日志,禁止静默吞错 |
| P2-3 | **生产 WSGI** | `run.py` 的 Werkzeug 开发服务器(`allow_unsafe_werkzeug=True`)→ 换 gunicorn + SocketIO worker |
| P2-4 | **消除硬编码路径** | 19 处硬编码 `/home/xmren` 绝对路径 → 统一走 `config.PROJECT_ROOT`;修 `config.DASHBOARD_DB_DIR` 与真实 `dashboard/backend/database/` 不一致的问题 |
| P2-5 | **Schema 迁移规范化** | 手写 `migrate_db*.py` → 引入轻量迁移(如 alembic 或编号 SQL + 版本表),DB 结构变更可追溯、可回滚 |
| P2-6 | **最小 CI** | GitHub Actions / 本地 hook:`pytest 10-tests/` + `py_compile` + `data_health_check.py`,PR 必过 |

### 🟢 P3 — 架构清晰化 & 交易地基(目标 8–12 人日)

| # | 任务 | 说明 |
|---|---|---|
| P3-1 | **明确数据层契约** | 画清 `07-data/*.json`(引擎写)→ `data_sync` → SQLite(Dashboard 读)的单向数据流;定义每张表的 owner、写入时机、schema。消除"谁是权威"的模糊 |
| P3-2 | **模块边界整理** | `06-tools/analysis` 内 bare-import 互相耦合 → 整理依赖图,核心(扫描/鲸鱼/集中度)与可选(news/llm)解耦,保留优雅降级模式 |
| P3-3 | **信号验证层(交易地基核心)** | 现有信号(Pair-Cost、跨市场、鲸鱼收敛)**未经回测验证**。建标准化回测框架:历史快照 → 信号 → 模拟成交(含手续费/滑点/流动性约束)→ PnL 曲线。已有 `daily_price_snapshots`(39,142 行)可作回测数据源 |
| P3-4 | **信号质量度量** | 对每类信号统计命中率、夏普、最大回撤、样本量;低质量信号下线。让 Phase-3 的 `signal_results`/`strategy_performance` 真正承载这些指标 |
| P3-5 | **(后续独立项目)执行层评审** | 下单/资金/私钥管理需单独立项,先有 P0–P3 的干净数据与验证过的信号,再谈实盘。本方案不含 |

---

## 3. 建议执行顺序与里程碑

```
Week 1  ── P0 全部 ──────────────► 里程碑 M1: 数据干净(垃圾率<5%, DB<30MB, 健康检查通过)
Week 2  ── P1-1/P1-2 ───────────► 里程碑 M2: 单一 sync, Phase-3 表要么产数据要么删除
Week 3  ── P2-1/P2-2/P2-3/P2-4 ─► 里程碑 M3: 可运维(日志/异常/WSGI/路径)
Week 4+ ── P2-5/P2-6 + P3 ──────► 里程碑 M4: 可验证(CI + 回测框架 + 信号质量度量)
```

**关键纪律:** M1 未达成前不开新功能;M4 的信号验证未跑出正期望前不投入实盘资金。

---

## 4. 风险与回滚

- **清洗误删:** P0-3/P0-4 前强制 `make backup`(已有 Makefile target);清洗脚本先 `SELECT COUNT` 预演再 `DELETE`,用事务包裹。
- **VACUUM 锁库:** 在监控引擎与 Dashboard 停机窗口执行(VACUUM 会独占锁);138MB 预计秒级完成。
- **改 sync 逻辑致漏数据:** P0-1/P0-2 改完先在备份库上跑一轮 diff,确认真鲸鱼(214 个 >$100k)一个不少再上生产。

---

## 5. 我可以立即着手的第一批(待你确认)

P0-1 + P0-2 + P0-3 + P0-4 可以打包成一次"数据止血" PR:
1. 修 `data_sync.py:181` 的 `has_activity`;
2. `sync_whales` 加价值闸门;
3. 新建 `scripts/cleanup_whales.py`(备份→预演→事务清洗→VACUUM);
4. 新建 `scripts/data_health_check.py`(P0 验收断言)。

预计半个 PR 的体量,改动集中、风险可控、收益最大(立刻把 98% 噪声砍掉)。
