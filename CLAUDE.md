# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🔴 最高原则(2026-08-06 用户明令):这个项目是为了赚钱

**本项目存在的唯一目的,是找到能实际盈利的 edge。** 其它一切 —— 采集器、数据湖、
Dashboard、告警、判据、重构 —— 都是**手段**,不是目的。

判断任何一件工作值不值得做,先问这一句:

> **它离"能赚到钱"这件事有多远?中间还隔着几步?那几步现在成立吗?**

三条推论(都是本项目真踩过的坑):

1. **不许拿"数据更全 / 代码更干净 / 覆盖率更高"本身当理由。**
   数据的价值不在总量,在于**能支撑多少个独立、可验证的结论**。
   实测:成交总笔数 1300 万,但统计功效由**已结算市场个数**(约 5 万)决定,
   不是由笔数决定。多采一倍的笔数不会让结论强一倍。
2. **数据变多但口径变了,比数据少更危险。** 样本量大会让置信区间变窄,
   于是**错误的结论看起来更可信**。故任何采集口径变更必须留下带日期的记录,
   发掘窗/验证窗切分时把它当边界。
3. **先证伪比先建设便宜。** 本项目已证伪的候选(跟鲸鱼 / 长热偏差 / H6 / H7 /
   跨市场套利)都是靠"先做最便宜的那个检验"砍掉的。
   任何新方向,先找那个**最可能一枪打死它**的检验,别先修基础设施。

## What this is

Polymarket 智能监控系统 — a prediction-market **monitoring / arbitrage-discovery / whale (大户) behavior-tracking** platform. It is **read-only / advisory**: there is no order-execution layer (`06-tools/trading/` is a README only). Comments, docs, and Telegram output are in Chinese.

## Three subsystems

1. **Offline monitoring engine** (Python) — entry `06-tools/monitoring/polymarket_monitor_v2.py`. Scans every 5 minutes via cron/nohup. Pulls market + trade + position data, runs Pair-Cost and cross-market arbitrage scans plus whale tracking, scores risk, and pushes Telegram alerts. Writes JSON artifacts to `07-data/`.
2. **Web Dashboard** — Flask backend `dashboard/backend/` (entry `run.py`, port **5000**, blueprints under `app/api/`, SocketIO realtime, APScheduler) + React/TypeScript/AntD frontend `dashboard/frontend/` (port 3000).
3. **Shared data layer** — the engine writes `07-data/*.json`; the dashboard reads SQLite. `dashboard/backend/app/services/data_sync.py` bridges JSON → DB, scheduled every 5 min.

### Critical architectural facts (read before changing data flow)
- **Optional-import + boolean-flag graceful degradation**: the monitor wraps most feature imports in `try/except ImportError`, setting flags like `NOTIFICATIONS_ENABLED`, `RISK_REVIEW_AVAILABLE`, `MARKET_MAKING_NOTIFY_ENABLED`. A missing module disables a feature instead of crashing. Preserve this pattern when adding features — never make a new import unconditional at module top.
- **Config single source of truth**: `06-tools/analysis/config.py`. `PROJECT_ROOT`, `DATA_DIR=07-data/`, all thresholds (`Thresholds.PAIR_COST`, currently `0.90`), API config, and `DBTables` table-name constants live here. The monitor *also* reads runtime feature toggles from env vars (`RISK_REVIEW_ENABLED`, `MARKET_MAKING_NOTIFY`, `NOTIFY_IMMEDIATELY`).
- **The live database is `dashboard/backend/database/polymarket.db`** (~138 MB). Other `*.db` files in the tree are/were symlinks to it, and root `polymarket_data.db` is a stale 0-byte leftover. Note `config.py` defines `DASHBOARD_DB_DIR = dashboard/database` which does **not** match the actual runtime path — verify which DB a script opens before trusting it.
- **Multi-source data fallback**: `06-tools/analysis/hybrid_data_source.py` resolves whale data by priority: rebuild-from-Trades → JSON → Leaderboard → no-data. Sources include Polymarket (Gamma/Data/CLOB), Manifold, Metaculus, Kalshi, news RSS, and the Leaderboard.
- **Two sync implementations coexist** (`data_sync.py` and `data_sync_v2.py`); authority is ambiguous. Check which one the scheduler actually invokes before editing sync logic.

## Common commands

Dashboard (run from `dashboard/`):
```bash
make install      # pip install backend + npm install frontend
make dev          # backend (run.py, :5000) + frontend (npm start, :3000)
make build        # frontend production build
make deploy       # docker-compose up -d --build  (also: make down, make logs)
make backup       # snapshot the sqlite DB
make sync         # run data_sync.py once
make lint         # py_compile the backend
```

Monitoring engine:
```bash
./start_monitor.sh                                    # nohup the 5-min monitor, logs to /tmp/monitor.log
PYTHONPATH=06-tools/analysis python3 06-tools/monitoring/polymarket_monitor_v2.py
./run_sync_changes.sh                                 # one-shot changes-table sync
```
The monitor and most analysis scripts require `06-tools/analysis` (and often `06-tools/monitoring`) on `PYTHONPATH` because modules import each other by bare name (e.g. `from pair_cost_scanner import ...`).

Tests (pytest, config in `10-tests/conftest.py` which puts the analysis/monitoring dirs on `sys.path`):
```bash
pip install -r 10-tests/requirements-test.txt
python3 -m pytest 10-tests/ -v                        # unit / integration / e2e subdirs
python3 -m pytest 10-tests/unit/test_x.py::test_name  # single test
cd dashboard && make test                             # backend pytest (dashboard/backend/tests)
```

## 🔴 前置工作项:先写判据,再写代码(2026-07-15 用户明令,无例外)

**在动手写/改代码之前,先把"什么算成功"白纸黑字写死。** 这是前置项,不是收尾项。

| 工作类型 | 前置产物 | 位置 |
|---|---|---|
| **研究性结论**(回测/策略/任何要下判断的数字) | **预登记单**:自变量、红线、判决表、"本测试不能回答什么" | `docs/PREREG_*.md` |
| **功能性代码**(工具/管线/修 bug) | **测试**(或可执行的验收判据) | `10-tests/` |

理由(血的教训,见 2026-07-15):结果出来之后再定标准,人**必然**会不自觉地挑一个刚好能通过的标准——那是自欺,且事后无法自证清白。红线**先写**、且尽量**逐字沿用/`import` 被检验对象自己的红线**(而非复制粘贴),是唯一能让"我没放水"这句话可验证的办法。

配套铁律:
1. **一次只动一个自变量。** 对照两臂的其它维度必须逐条列表确认相同(市场范围/成本模型/入场规则/红线…)。同时动两个 → 差分无法归因 → 测了等于没测。
2. **绝不用与"结果"相关的变量筛样本或贴标签。** 例:用"价格是否收敛到 0/1"判断市场是否已结算 ↔ 等价于用"胜负是否已明朗"筛样本 → 样本里只剩赢定了的。**数据缺得随机=噪声(无害);缺得与结果相关=错误(致命)。**
3. **任何降级/剔除/回退,必须出声计数。** 静默丢样本是真凶(曾静默丢掉 80% 仍一声不吭)。
4. **改共享引擎必须默认关闭 + 回归证明。** 新行为一律做成可选开关;并用「**原版代码 vs 新版代码跑同一份数据**」证明逐项一致。⚠️ 回归对照必须**固定数据、只变代码**——曾用今天的数据去比两天前的 json,被数据漂移误导。
5. **🟢 才是风险区。** 抬高收益的偏差吹不倒本来就趴着的结论,只可能吹起本来站不住的。故 🔴 证伪结论稳健;**任何 🟢、尤其推翻既往证伪的 🟢,应受更严审视而非更松**。

## Code review discipline (mandatory before declaring a change done)

### Automatic subagent review — required after Python edits

After editing ANY `.py` file in `06-tools/`、`dashboard/backend/` 或 **`11-collector/`**,
immediately invoke the `python-reviewer` subagent before marking the task complete:

```
use subagent: python-reviewer
```

For files whose names include any of: `monitor`, `notif`, `telegram`, `sync`, `fetch`, `http`, `request`, `socket`, `api`, `clob`, `polymarket` — this is **mandatory**, not optional. The PostToolUse hook will surface a reminder automatically.

> **`11-collector/` 于 2026-08-06 加入(用户明令)。** 理由:它是 07-22 起从零写的目录,
> 出坑最多,而原范围**恰好没覆盖它** —— 自己写的代码自己 review,今天已被证明无效
> (同一个游标 bug 上午修了一处、下午在新代码里又写了一遍)。

### 🔴 动手前先自查这两个形状(2026-08-06 设计梳理归纳,不是泛泛提醒)

这两条是从十几个实际坑里归纳出来的**同一形状的反复发作**,不是"要细心"这类空话。
`11-collector/` 的 PostToolUse 钩子会在每次编辑时把它们摆到眼前。

1. **「记录事实 vs 使用事实,只接了一头」**(犯过 4 次)——
   新增/修改的量,记下来之后**有人读它吗**?
   实例:水位线前进却没记截断;计数器算了没进心跳;时间闸参数定义了调用方没传;
   截断留了痕而下游没有任何消费者。
2. **「照抄结构而不抽象」**(犯过 3 次)——
   这段逻辑在别处是不是已经有一份?抄的话,上一份的教训跟过来了吗?
   实例:游标轮转抄 3 遍、时间闸抄 5 遍、连零守护抄 4 遍。

### 🔴 每一步动手前先写三问(2026-08-06 用户明令,进提交信息)

1. 这一步**服务哪条需求**?(指不到 `docs/DESIGN_COLLECTOR_INVARIANTS.md` 里某条不变量的,就别做)
2. **不做会怎样**?—— 答不出实质后果的,**不做**。
3. 做完**怎么验证**它确实服务了那条需求?

实例:2026-08-06 的"巨盘优先名额"就是靠第 2 问被取消的 ——
证据显示那个坑不存在,而我差点为它建一整套机制。

### Exception handling checklist (every network/IO/subprocess edit)

Explicitly verify every `except` clause:
1. List every exception type the called library can raise (not just the obvious ones).
2. For `urllib.request.urlopen`: `TimeoutError` / `socket.timeout` propagates directly from `resp.read()` — it is **not** wrapped in `URLError`. Must catch `(URLError, TimeoutError, OSError)`.
3. `requests.*()` raises `requests.exceptions.Timeout` (wraps `socket.timeout`) — and separately `requests.exceptions.ConnectionError`. Both must be caught. A bare `except Exception` that does `continue` or `pass` silently drops both.
4. Confirm the retry / fallback logic still runs under each exception path — do not accept "has try/except" at face value.
5. After writing exception handling, ask: "what happens if the network hangs for 60s mid-read?" and trace it through the code.

When reviewing **existing code** (not just new additions), apply the same checklist to any `except` block you read.

## 静默失败检查清单(每次改采集/数据接口/告警必过)

**背景(2026-08-03 血的教训)**:结算真值采集静默死了 11 天 —— 进程正常、日志照打、看板全绿,
唯一异常是 `newly_resolved` 恒为 0,而没人规定过"它不该是 0"。同期告警推了 1340 条,
**没有一条是关于它的**。完整分析:`docs/PITFALLS_SILENT_FAILURE_2026-08-03.md`

1. **批量/过滤类接口:先做「请求数 vs 返回数」对账。** 数量对不上必须抽样核对**丢的是哪一类**,
   不许用"大概是脏数据"糊过去。实例:Gamma `condition_ids=` 默认只返回未关闭市场,
   请求 100 返回 72,丢的 28 个**全是已结算的** —— 即接口默认参数本身就是个**与结果相关的筛选器**。
   丢样本不致命,**丢得与结果相关才致命**。
2. **排序键里的缺失值必须显式决定排哪里。** `key=lambda r: r["x"] or ""` 不是"防崩溃",
   而是把所有缺失值钉死在优先级最高处。
3. **"每轮取前 N 个"必须能回答"第 N+1 个何时轮到"。** 答不上来就是饿死;正解是游标轮转 + 回卷。
4. **恒定不变的计数 = 强可疑信号。** 真实系统的计数会抖动;每轮精确相同通常意味着**每轮处理同一批**。
5. **阈值必须有实测分布支撑,并把分位数写进判据断言。** 独立链路分开判定,不许相加;
   分母会变的指标用比率而非绝对值。
6. **每条关键产出配一条"连续 N 轮为 0"守护**,且区分「有活没干成」(异常)与「没活可干」(正常)。
7. **新增告警必须自带防洪判据。** 稳态完全静默 + 真异常必推,两头都要焊死 ——
   降噪不是体验优化,是可靠性工作(噪音会让真信号无处可显)。

## Conventions
- **Test isolation is enforced**: test code must run with a `--test` flag and write to `/tmp/`; production data dirs (`07-data/`) must never receive test fixtures. Honor this when adding scripts.
- Database schema changes are done via hand-written `dashboard/backend/migrate_db*.py` scripts (no migration framework). There is no CI.
- `.bak` / timestamped backup files and `backfill_*.py` / `cleanup_*.py` one-off scripts are scattered in the source tree — these are operational scripts, not part of the import graph.
- **Crontab: 绝对路径，无例外**。cron 的工作目录是 `$HOME`，所有相对路径都会解析错误。每条 crontab 条目必须满足以下格式之一：
  - `cd /absolute/path/to/project && python3 script.py` — 推荐，适合项目内脚本
  - `python3 /absolute/path/to/script.py` — 适合路径固定的独立脚本
  - PYTHONPATH 若用相对路径，必须在 `cd project_dir &&` 之后才有效
  写完 crontab 条目后，逐行确认没有裸露的相对路径，再执行 `crontab` 安装。

## Known data-integrity issues (state, not aspiration)

A 2026-06 review flagged data corruption. **Re-verified against the live DB on 2026-06-06 — most items are already remediated.** Authoritative check: `python3 scripts/data_health_check.py` (junk 0.0% / dead-space 0.5% / has_activity-mislabel 0 as of 2026-06-06).

RESOLVED (verified):
- ~~`whales` table ~98% garbage~~ → **junk rate now 0.0%** (only 1 truly-empty row of 3035). The original "98% garbage" used a flawed predicate (`total_value=0 AND position_count=0`): that catches *trade-flow whales* (real `total_volume`/`changes_count`, just no current position snapshot — these are the follow-whale signal source and are **not** garbage). `data_sync.py::sync_whales` now has a value gate (`[P0-2]`, ~line 137); `scripts/cleanup_whales.py` deletes only true empties (`changes_count=0 AND total_volume=0`).
- ~~~47% freelist dead space~~ → **0.5% now** (DB VACUUMed; ~51 MB, was ~138 MB).
- ~~`has_activity` set-once bug~~ → main `data_sync.py` path now direct-assigns (`has_activity = excluded.has_activity`, can reset to 0). `sync_changes.py` hardcodes `=1` but only for wallets with a change this batch (correct, not a bug — see inline comment).
- ~~`config.DBTables.SIGNALS` name drift~~ → fixed: `SIGNALS='signals'`, added `SEMANTIC_SIGNALS='semantic_signals'`. NB: the whole `DBTables` class has **0 references** project-wide (reserved constants only).

STILL OPEN:
- Phase-3 "quality & automation" tables (`signals`, `signal_results`, `whale_performance`, `strategy_performance`, `quality_reports`, `threshold_history`, `opportunity_history`) are all empty — code merged but the scheduling chain isn't running. (This is an unfinished feature, not corruption.)
- No file/archive rotation/TTL: `07-data` ≈ 286 MB / ~2200 files; `monitor_report_*.json`, `whale_states/*.json`, and `positions_archive` (~38.6k rows, 3.4× `positions`) accumulate. Housekeeping only — disk, not correctness.

Full analysis: `PROJECT_REVIEW.md` (+ 2026-06-06 correction note at top) and `PROJECT_OVERVIEW.md`.
