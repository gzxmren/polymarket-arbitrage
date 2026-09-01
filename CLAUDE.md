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

## 🔴🔴 代码工作的六步流程(2026-08-27 用户明令,取代原四步)

> **前三步必须落成文件**(「落地」)。理由不是仪式感 ——
> 2026-08-07 的教训原文:*救我的不是"先设计了",而是**设计成了文字所以能被别人打**。*
> 只在对话里说过的设计,没人能反驳,也没人能在三天后核对我有没有照着做。

| 步 | 产物 | 落在哪 |
|---|---|---|
| 1. **需求** | 三问(服务哪条需求 / 不做会怎样 / 怎么验证)+ 明确的**不做什么** | `docs/DESIGN_<主题>_<日期>.md` |
| 2. **设计** | 方案、被否掉的备选及理由、要用户拍板的点、已知边界 | 同上(同一文件) |
| 3. **设计 review** | ⭐**动代码之前**先审设计:具体失败场景、我自己设计里的错 | 同上(追加一节,写明谁审的、改了什么) |
| 4. **代码(TDD)** | 判据**先写并先确认是红的**,再写实现 | `10-tests/` |
| 5. **代码 review** | `python-reviewer` 子代理;逐条自己核实再采纳 | 提交信息里记结论 |
| 6. **完成** | 全量判据零红 + 变异验证 + 现网端到端(若涉运行中的东西) | — |

**第 3 步是本次新增的**,由来:2026-08-27 用户说「我担心你修改又带来新的 bug」,
让我对着**还没写的**改动做 review —— 结果当场查出**我自己设计里的对账口径是错的**
(用集合比自然键会把重复行折叠掉,丢一笔查不出来),还有一条新引入的静默劣化
(按天分组会产生几百个碎片文件,而 compaction 阈值是每分区 50 个,永远够不到)。
**这两条都是在写一行代码之前发现的。**

### 适用范围(2026-08-27 用户拍板)

- **走完整六步**:新功能、重构、改采集口径、改分析口径
- **可简版**(需求 + 判据 + 代码 + review,略过设计文档与设计 review):**已定位的 bug 修复**
- ⛔ **走简版必须出声**:当场说明「我判这是 bug 修复所以走简版」+ 理由,让用户能否掉。
  界线由我判 ⇒ 有被我用来绕过流程的风险 ⇒ **静默简化等于绕过**,必须可见。
  (同项目铁律「任何降级/剔除/回退必须出声计数」,只是对象换成流程本身。)

### 研究性结论另有一套(不变)

| 工作类型 | 前置产物 | 位置 |
|---|---|---|
| **研究性结论**(回测/策略/任何要下判断的数字) | **预登记单**:自变量、红线、判决表、"本测试不能回答什么" | `docs/PREREG_*.md` |
| **功能性代码**(工具/管线/修 bug) | **测试**(或可执行的验收判据) | `10-tests/` |

## 🔴 前置工作项:先写判据,再写代码(2026-07-15 用户明令,无例外)

**在动手写/改代码之前,先把"什么算成功"白纸黑字写死。** 这是前置项,不是收尾项。

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

⚠️ 2026-08-17 删掉了原第 1 条(「列出被调库能抛的每一种异常」)与原第 4 条
(「不许把'有 try/except'当数」):两条都是**劝诫**,没有可当场执行的动作 ——
当天写 `except OSError` 时它们一次都没触发。

⚠️ **2026-09-01 复核**:当时接替它们的那条(造真坏输入、打 `__mro__`)**同样没触发** ——
可执行还不够,它还得有**触发时机**。已改写为下面的第 4 条(数坏输入用例的条数),
时机是"写完一组判据的那一刻"。第 5 条同日泛化。详见文末台账的复核结果。

Explicitly verify every `except` clause:
1. For `urllib.request.urlopen`: `TimeoutError` / `socket.timeout` propagates directly from `resp.read()` — it is **not** wrapped in `URLError`. Must catch `(URLError, TimeoutError, OSError)`.
2. `requests.*()` raises `requests.exceptions.Timeout` (wraps `socket.timeout`) — and separately `requests.exceptions.ConnectionError`. Both must be caught. A bare `except Exception` that does `continue` or `pass` silently drops both.
3. After writing exception handling, ask: "what happens if the network hangs for 60s mid-read?" and trace it through the code.
4. **⭐写完一组判据,先数这组里有几条是喂"真坏输入"的 —— 零 = 没覆盖**
   (2026-08-17 立作「注释承诺的保护要实测」,**2026-09-01 复核改写**)。
   改写依据:原版是一条**没有触发时机**的动作 —— 全项目 `__mro__` 出现 **0 次**,
   每次都是 code review 先拍肩膀才做。08-28 review 判 Block,才发现我在设计单里写的
   「会安全退化」是假话(状态文件"语法合法但形状不对"会崩,实测四种坏形状分属**三个**
   异常家族)。⇒ 它是**修的方法**,不是**写的时候的闸**。
   可数版本(有时机、能当场数):最低造四类坏输入 —— 类型错 / 嵌套值类型错 /
   时钟倒退越界 / 上一版 schema 的合法旧形状;且要断言**安全退化的方向**,不只是"不崩"。
   (08-28 实测:42 条判据里 **0 条**喂坏输入,而 review 的两个 BLOCKER 正好都落在那里。)
   量异常家族的手法照旧,只是降级为"怎么量"而不是闸:**造一个真的坏输入跑一遍**
   (不是内存里的假对象),把 `type(e).__mro__` 打出来,再决定 `except` 写什么。
   原始教训留档:pyarrow 分属 `ArrowInvalid`→`ValueError`、`ArrowTypeError`→`TypeError`、
   `ArrowIOError`→`OSError`,改成 `(OSError, ValueError)` **仍然漏**,
   最后用共同基类 `pa.lib.ArrowException` 才真覆盖。
5. **附加职责的异常不许打断核心职责**(2026-08-17 立作「可选导入不许只写 `ImportError`」,
   **2026-09-01 复核改写为泛化形态** —— 实测真正起作用的就是这一层)。
   可选导入只是它的一个实例:模块级的 `NameError` / `SyntaxError` 抛的都不是 `ImportError`
   ⇒ 一个附加功能坏掉,异常一路冒出去把**调用方其余职责全部打断**。
   实例:08-17 daily_digest;08-26 看门狗侧表守护(`087846c`)在同一位置复发 ——
   修法是外层 `except Exception` 兜底 **+ 判据**(让那一路抛 `RuntimeError`,
   断言核心检查仍在输出里),并把附加职责挪到 `check()` **最后**,
   这样即便兜底本身有洞,被牺牲的也只是附加那一条。
   写法照 `alerts.py` 对 telegram 的可选导入抄(`except Exception`)。
   ⚠️ **记账**:字面那条没被守住 —— 全项目仍有 **16 处** `except ImportError` 窄写法
   (全在 `06-tools/` 与 `dashboard/` 的老监控代码里),**零判据**在守。
   2026-09-01 拍板**不补**:不在采集器上,也不在判决路径上,按"离赚钱几步"不值当。
   **这是有意识的取舍,不是遗漏。**

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

---

### 2026-08-17 新增四条(同一天连踩四个,前 7 条都没盖住)

8. **⭐已经写下的"保证/安慰话",工况变了之后必须重审。**
   问句:*这句话是在什么前提下写的?那个前提现在还成立吗?*
   实例:告警正文写着「本轮空转,**数据不丢(下轮自愈回填)**」—— 在**单轮抖动**下是真的,
   而 08-13 连续 56 轮(14 小时)时它推了 **61 遍**,当天永久丢了约 1.4 万笔。
   这不是"没有告警",是**告警响了 61 次、每次都说了假话**,比没有更糟(它主动让人放心)。
   ⇒ 任何形如"没事/会自愈/不影响"的措辞,都要问"持续下去还成立吗",并按连计分档。

9. **⭐判据的工况参数必须来自现网实测,否则它测的是一个不会发生的世界。**
   (与第 5 条不同:第 5 条管**阈值取值**,这条管**判据跑在什么条件下**。)
   实例:「不饿死」判据用涌入 30 / 预算 10 跑 40 轮,积压最多长到 800,
   **永远碰不到 MAX_BACKLOG=2000** —— 而生产里恰恰是队列顶死的情形。
   判据全绿三天,不变量已经破了。写完判据要问:*现网今天的数值代进去,它还绿吗?*

10. **⭐新加的一步,要问「上游失败时它还跑不跑」。**
    实例:systemd 里两条 `ExecStart` 串在一起,而语义是「前一条失败(且没有 `-` 前缀),
    后面的都不执行」。日报"发不出去→进队列"是**预期内经常发生**的自愈行为却返回 1
    ⇒ 每次网络抖一下,当天的趋势报表根本不会生成。
    判据当时是绿的 —— 它只检查了 service 文件里**有没有那行字**,没检查它**会不会被执行**。
    ⇒ 编排层(systemd/cron/流水线)也有静默失败,别只盯代码里面。

11. **⭐我自己造的检查工具同样会静默坏掉,而且坏得跟"没问题"一模一样。**
    实例(同一天两次):① 用日期过滤日志查告警,而 collector.log 的告警行**不带日期**、
    watchdog.log 只有 `[HH:MM]` ⇒ 两次都返回空,差点得出"告警一条没响"的错误结论。
    ② 判据里的假接口按**第几次调用**作答,而改动恰好让批数变了 ⇒ 错位,红绿都不可信。
    ⇒ 查出"没有异常"时,先问*这个查法在真有异常时会返回什么*;能构造就构造一次坏输入验一下。

---

## ⏰ 规则复核台账(2026-08-17 立)

**规则本身也要有实测支撑 —— 写下来不等于有效。**

由来:2026-08-17 用户问「规则都写了你为什么还犯错」。审计发现两类东西混在一起 ——
**问句/动作/事实**(当天全部触发过,痕迹可查:核心问句逐字出现在 6 个判据文件里、
`MEASURED_` 常量 24 处、对账习惯挖出了 `limit=` 那个雷)与**原则复述**
(当天一条都没触发)。后者已删两条(异常清单原第 1、4 条)。

还发现一条**正在无声腐烂**的规则:三问要求进提交信息,实测 12 个提交里漏了 4 个,
且今天最后两个都漏 —— 会话越长漏得越多。**已改由 commit-msg 钩子强制**
(`deploy/githooks/commit-msg`,判据 `test_commit_msg_hook.py`)。

### 2026-09-01 复核结果(第一次到期复核)

是判据 `test_rule_review_due.py` 到期变红把人拽回来的 —— 那张表没烂,因为有人
把"两周后回来看一眼"焊成了一条会红的判据。**7 条一条没删**(08-17 那次删了两条),
**2 条改写**。证据全部是从 git log 与代码里**读出来的**,每条都指得到具体提交:

| 条目 | 判 | 铁证 |
|---|---|---|
| 静默失败 8:保证在新工况下变假话 | ✅ 留(触发 2 次) | `522a1af` 08-27:换 flock 后,告警正文里「清掉残留的 `.backfill.lock`」从**唯一出路**变成**危险动作**(照做会两个实例同时跑);`e15bfa9` 08-28:`offset_overflow_warm` 那句「丢一次下轮自愈」在加了冷却之后又变假话 |
| 静默失败 9:判据工况参数用实测值 | ✅ 留(1 次) | `e15bfa9`:用**四天现网真实心跳**重放,红线单位换成"最长中断窗"(实测 458 分钟 → `MEASURED_WORST_OUTAGE_S`) |
| 静默失败 10:上游失败时它还跑不跑 | ✅ 留,已转成判据(1 次) | `e6145ef` 08-26:侧表 timer 的判据里就有「绝对路径 + **单条 ExecStart**」;同批判据当场把一个**恒真式**排班判据改红,查出 `:48` 相位真撞采集器忙区 |
| 静默失败 11:我的检查工具会静默坏掉 | ✅ 留(≥5 次,最活跃) | `e66f827` 08-28「今天第三次」(按 mtime 挑到 compaction 重写的旧分区);08-30 跑钱包曲线前先拿代码给的 4,973 验查法;08-31 `nethour.py` 硬编码分区静默漏掉最近 4 小时;09-01 跑完 V6 同样先验 7,321 |
| 异常清单 4:注释承诺的保护要实测 | 🔧 **改写** | 动作只在 code review 拍肩膀时做过(08-28 三个异常家族),**写代码那一刻从没触发**;全项目 `__mro__` 出现 **0 次** |
| 异常清单 5:可选导入的 except | 🔧 **改写** | 08-26 `087846c` 确实触发,但用的是**泛化形态**(附加职责不许打断核心职责);字面形态全项目仍有 **16 处**窄写法且**零判据**在守 |
| 全局第 4 条(别的项目) | ✅ 留(跨项目 1 次) | stock 项目 `HANDOFF_2026-08-30` §3.3:每天推的「主源 mootdx 连挂 49 日、由 BaoStock 兜底」在主源换成腾讯后**成了假话**,动作是迁移 state 键并主动清除旧键 |

⭐ **这次复核本身最值钱的结论:「问句/动作/事实 vs 原则复述」这把尺子不够细。**
异常清单 4 是一条**动作**(去打 `__mro__`),却照样一次没触发 —— 因为它**没有触发时机**:
没人规定"什么时候该去打"。而触发最勤的第 11 条自带时机(每次我造检查工具的那一刻)。
⇒ **新判准:规则里必须写清"什么时刻做这个动作",只写"要做什么"不够。**
两条改写都是按这条新判准改的:第 4 条的时机是"写完一组判据",第 5 条泛化到"每个附加职责"。

### 待复核:2026-09-15

到期仍按同一把尺子量:*它有没有真的在写代码/做判断的那一刻改变过我的动作?*
答不出实例的,按本文件标准**删掉或转成判据**,不许因为"话说得对"就留着。
新增一问:*它写清触发时机了吗?*

| 条目 | 位置 | 到期要回答 |
|---|---|---|
| 异常清单 4(改写版):数坏输入用例的条数 | 本文件 | 有没有在写完一组判据后真的数过一次 |
| 异常清单 5(改写版):附加职责不许打断核心 | 本文件 | 泛化形态有没有再拦下一次 |
| 新判准:规则必须写清触发时机 | 本文件 | 有没有用它砍掉或改写过一条没时机的规则 |
| 静默失败 8 + 全局第 4 条 | 本文件 + `~/.claude/CLAUDE.md` | 还在触发吗(已 3 次,含跨项目 1 次) |
| 静默失败 9 / 10 | 本文件 | 各只触发过 1 次,是真有用还是碰巧撞上 |
| 静默失败 11:我的检查工具会静默坏掉 | 本文件 | 触发最勤但纯靠我记得,该不该转成判据 |
| 16 处 `except ImportError` 窄写法 | `06-tools/` `dashboard/` | 09-01 拍板不补的理由还成立吗(老系统真退役了吗) |

**⚠️ 这张表本身也会腐烂** —— 若 2026-09-15 过去而没人复核,它就变成又一条
"写下来没人读"的东西,正是本文件通篇在讲的那个病。
故复核结果(删了哪些/留了哪些/依据是什么)必须写回这里,不许只在对话里说。

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
