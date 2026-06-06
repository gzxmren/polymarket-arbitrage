# Polymarket 智能监控系统 — 全面评审报告

> 🟩 **2026-06-06 复核更正（置顶）**：本报告"数据腐化专项"的多数结论**已过时/已修复**。复核 live DB(`data_health_check.py`)：
> 垃圾率 **0.0%**(非 98%)、死空间 **0.5%**(非 47%)、has_activity 错标 **0**。
> 其中"whales 98% 垃圾"判据本身有误——`total_value=0 AND position_count=0` 会把**有真实成交量/笔数、仅无当前持仓快照的 trade-flow 鲸鱼**(跟鲸鱼信号源)误判为垃圾;真·空壳仅 1 行。
> 修复已落地:`data_sync.py` 价值闸门(P0-2) + `scripts/cleanup_whales.py`(正确判据) + VACUUM。
> 仍开放项:Phase-3 七张空表(未接调度,非腐化)、无 TTL 文件/归档轮转(磁盘卫生)。下文原文保留作历史记录。

> **评审时间**：2026-06-02 (UTC+8)
> **评审对象**：`v2.0-development` 分支（最近提交 `8d4c5d1 Phase 3: 质量监控与自动化`）
> **评审方法**：通读源码 + 实际查询运行库（`dashboard/backend/database/polymarket.db`，138 MB）+ 核对设计文档与运行数据
> **评审维度**：需求覆盖 · 设计架构 · 技术实现 · 代码逻辑 · 系统部署 · **数据腐化专项**

> ⚠️ 本报告基于**真实运行数据**，非纯文档推断。关键数字均来自对生产 SQLite 库与 `07-data/` 目录的直接采样。

---

## 0. 评审结论速览

| 维度 | 评级 | 一句话 |
|------|------|--------|
| 需求覆盖 | 🟡 中 | 监控/鲸鱼跟踪扎实，但套利与 Phase 3 质量自动化"实现了却没产出数据" |
| 设计架构 | 🟡 中 | 分层清晰、模块化好，但双存储 + 双同步实现带来一致性风险 |
| 技术实现 | 🟠 偏弱 | 612 处 print / 0 处 logging、59+ 处宽泛 except、生产用 Werkzeug 开发服务器 |
| 代码逻辑 | 🟡 中 | 核心算法合理，但存在 `has_activity` 单调标志 bug、循环内重复读文件 |
| 系统部署 | 🟡 中 | Docker/Nginx/Makefile 齐备，但无 CI、无迁移、无健康检查、备份手动 |
| **数据腐化** | 🔴 **严重** | **whales 表 97.9% 是零值垃圾行；DB 47% 是死空间从未 VACUUM；JSON/报告文件无轮转** |

**最重要的三件事**：
1. 🔴 **`whales` 表已退化为垃圾场**——72,757 行中 71,242 行（97.9%）`total_value=0 且 position_count=0`。
2. 🔴 **数据库膨胀且从不回收**——138 MB 中约 47% 是 freelist 死空间。
3. 🟠 **Phase 3"质量与自动化"形同虚设**——信号追踪、阈值优化、质量报告等 7 张表全部 0 行。

---

## 1. 需求覆盖评审

### 1.1 已较好覆盖 ✅

| 需求 | 状态 | 证据 |
|------|------|------|
| 鲸鱼识别与持仓跟踪 | ✅ 运行中 | `positions` 11,374 行，更新到 2026-06-02 |
| 集中度/收敛分析 | ✅ 运行中 | `concentration_history` 67,701 行，7 天滚动 |
| 重点鲸鱼 Watchlist | ✅ | `is_watched=1` 281 个 |
| Telegram 通知 | ✅ | `telegram_notifier_v2` + 多种报告脚本 |
| Leaderboard 趋势 | ✅ | `leaderboard_whales/history/trends` 有数据 |
| LLM 深度分析（带缓存） | ✅ | `whale_deep_analysis` 11 条，带 `data_hash`/`cost` |
| Dashboard 可视化 | ✅ | 8 个 API 蓝图、12+ 前端页面 |

### 1.2 "已实现但未产出"——需求悬空 ⚠️

V2 PRD 的核心增量（信号胜率追踪、阈值自动优化、质量监控自动化）**代码已落地、表已建，但运行数据为 0**：

| 表 / 功能 | 行数 | 含义 |
|-----------|------|------|
| `signals` / `semantic_signals` | 0 | 语义信号无产出 |
| `signal_results` | 0 | 信号胜率无追踪 |
| `whale_performance` | 0 | 鲸鱼绩效未计算 |
| `strategy_performance` | 0 | 策略绩效未计算 |
| `quality_reports` / `quality_logs` | 0 | 质量报告未生成 |
| `threshold_history` | 0 | 阈值自动优化未运行 |
| `opportunity_history` | 0 | 机会历史未沉淀 |
| `pair_cost_arbitrage` | 0 | Pair Cost 套利零落库 |
| `cross_market_arbitrage` | 1 | 跨市场套利仅 1 条 |

> **判断**：Phase 3 "质量监控与自动化"提交了代码，但调度/触发链路没有真正跑起来。这恰好印证 V2 设计文档自述的痛点——"套利机会 24h 0 次、信号效果未知"。**这些是当前最大的需求缺口**。

### 1.3 范围边界 📌

- `01-05` 五个策略目录 + `06-tools/trading/` **仅有 README**——系统是**只读/建议型**，无下单执行层。这与 README 定位一致（不盲目跟单），但需明确这是"信号系统"而非"交易系统"。

---

## 2. 设计架构评审

### 2.1 优点 ✅

- **分层清晰**：数据获取 → 分析引擎 → 风险/质量 → 通知/存储 → Dashboard，职责边界明确。
- **模块化策略**：套利/鲸鱼/做市/动量/语义各自独立文件，便于单独演进。
- **能力可选加载（优雅降级）**：`polymarket_monitor_v2.py` 用 `try/except ImportError`+布尔开关包裹每个子系统，单模块缺失不致命——这是**很好的健壮性设计**。
- **多源数据融合**：`hybrid_data_source.py` 按 `Trades重建 → JSON → Leaderboard → 无数据` 优先级兜底，应对单源不全。
- **配置集中**：`config.py` 作为路径/阈值单一来源（理念正确）。
- **DB 路径其实已统一**：4 个 `*.db` 实为**软链接**指向 `dashboard/backend/database/polymarket.db`。
  > ✅ 更正我此前 OVERVIEW 中"路径三处不一致"的说法——物理上是同一个库，靠 symlink 收敛。但**软链接本身是隐式约定**，新环境/Docker 卷挂载时极易断裂，仍属脆弱设计。

### 2.2 不足 ⚠️

1. **双存储 + 双同步**：`07-data/*.json`（引擎写）与 SQLite（Dashboard 读）并存，靠 `data_sync.py` 桥接，是**最终一致**而非强一致；同步失败时两边静默漂移。更糟的是同时存在 `data_sync.py` 与 `data_sync_v2.py` 两套实现，**权威来源不明确**。
2. **缺少数据生命周期设计**：建表用 `CREATE TABLE IF NOT EXISTS`，无 schema 版本/迁移；除 `concentration_history` 有 7 天清理外，`whales`/`positions_archive`/`daily_price_snapshots` 等无保留策略 → 直接导致第 6 节的数据腐化。
3. **配置未真正收敛**：19 个 `06-tools` 文件、2 个 backend 文件硬编码 `/home/xmren/...` 绝对路径，与 `config.py` 规范相悖（如 `hybrid_data_source.py` 顶部直接写死 DB_PATH）。
4. **阈值双轨**：`config.py::PAIR_COST=0.90` 与文档/环境变量示例的 `0.995` 不一致，运行期行为依赖于谁覆盖谁。

---

## 3. 技术实现评审

### 3.1 优点 ✅
- 类型注解 + `@dataclass`（如 `WhaleData`/`Position`）在新模块中应用良好。
- LLM 调用带 **content-hash 缓存 + 成本记录**，控费意识到位。
- 依赖在 `requirements.txt` 中**固定版本**，可复现。

### 3.2 不足 ⚠️

| 问题 | 量化证据 | 影响 |
|------|----------|------|
| **几乎不用日志** | `06-tools/monitoring` 中 `print()` **612 处 / logging 0 处** | 违反 `PROJECT_STRUCTURE.md` 自定规范；生产无结构化日志、无级别、无法采集 |
| **宽泛/裸异常吞错** | 59 处 `except Exception` + 10 处裸 `except:` | 错误被静默（如 `data_sync.py:162` `except: pass` 吞掉成交量计算失败） |
| **生产跑开发服务器** | `run.py:44` `socketio.run(..., allow_unsafe_werkzeug=True)` | Werkzeug 非生产级，无 worker、无优雅退出；应上 gunicorn+eventlet |
| **依赖偏旧** | flask==3.0.0 等固定在较老版本 | 安全/兼容更新滞后 |
| **源码混入临时件** | 15 个 `*.bak` / `*.bak.YYYYMMDD` 在源码树 | 污染、易误引用 |

---

## 4. 代码逻辑评审

### 4.1 关注点（具体到行）

**① `has_activity` 单调标志 bug（数据正确性问题）** — `data_sync.py:181`
```sql
has_activity = CASE WHEN excluded.has_activity = 1 THEN 1 ELSE whales.has_activity END
```
该字段**一旦置 1 永不复位**。后果可在库中直接观测：**71,152 行 `has_activity=1`，但其中绝大多数 `total_value=0`**——"有活动"标志已失去意义，任何基于它的查询都会被污染。

**② 循环内重复打开同一文件（性能）** — `data_sync.py:145-149`
每只鲸鱼迭代都 `open(self.watchlist_file)` 重新读一遍 watchlist JSON（外层已读过一次），O(N) 次磁盘读，N=鲸鱼数。应提到循环外读一次。

**③ ON CONFLICT 保留旧值会掩盖"归零"** — `data_sync.py:178-184`
`top5_ratio/total_volume/total_pnl/changes_count` 仅在新值 >0 时更新，否则保留旧值。本意是防止被零覆盖，但副作用是：当一只鲸鱼真的清仓归零，旧的非零指标会**僵尸式残留**，与 `total_value=0` 自相矛盾。

**④ 缺乏写入前的价值下限过滤** — `sync_whales`
零持仓/零价值的钱包仍被 `INSERT INTO whales`，没有 `if total_value < X: continue` 这类闸门 → 第 6 节垃圾行的直接成因。

### 4.2 优点 ✅
- 集中度算法（HHI、Top5/Top10）实现简洁正确（`_calculate_concentration_metrics`），含 `total==0` 防除零。
- 鲸鱼脏数据过滤思路存在（`risk_reviewer` 中"持仓>50 且价值<$1000 判异常"），方向对，只是**未在落库前的 sync 层统一执行**。

---

## 5. 系统部署评审

### 5.1 优点 ✅
- 提供 **Docker Compose（backend+frontend+nginx）+ Makefile + 部署指南**，三种部署路径（Docker/手动/生产加固）。
- `restart: unless-stopped`、SocketIO 实时推送、nginx 反代到位。
- 敏感信息治理良好：`.gitignore` 正确排除 `07-data/`、`*.db`、`*.log`、`.env`；**仓库中未发现硬编码 Telegram Token/密钥**。

### 5.2 不足 ⚠️
1. **无 CI/CD**：无自动化测试/构建/部署流水线；测试文件仅 **5 个**，覆盖远不足。
2. **无健康检查/资源限制**：`docker-compose.yml` 缺 `healthcheck`、`mem_limit`、`depends_on: condition`。
3. **无 schema 迁移**：靠 `CREATE IF NOT EXISTS`，字段变更需手工改库。
4. **引擎守护方式脆弱**：`start_monitor.sh` 用 `nohup ... > /tmp/monitor.log`——重启即丢日志，未用文档所述的 systemd。
5. **备份手动**：138 MB 库靠 `cp` 手动/cron 备份，无校验、无异地。
6. **生产 WSGI 缺位**：见 3.2，未使用 gunicorn 等。

---

## 6. 🔴 数据腐化专项（核心发现）

> 本节为本次评审最重要的部分。所有数字均来自 2026-06-02 对运行库的实测。

### 6.1 `whales` 表退化为垃圾场（最严重）

```
总行数         : 72,757
total_value=0  : 71,242  (97.9%)   ← 零值且零持仓
  其中 >$100k  :    211             ← 真正的"鲸鱼"
  $10k–100k    :    508
  $0–10k       :    796
has_activity=1 : 71,152  ← 与零值严重矛盾（见 4.1 ①）
```
**只有约 2% 的行是有意义的数据**。该表本应是"被跟踪鲸鱼"主表，却把所有出现过的钱包（疑似来自成交流/leaderboard 导入）无差别落库，且无清理。查询、统计、前端列表都要在 7 万垃圾行上过滤。

### 6.2 数据库膨胀且从不回收

```
文件大小       : 138 MB
总页数         : 33,886
freelist 空闲页: 16,021   ≈ 47% 为删除后未回收的死空间
```
大量删除（如 concentration 7 天清理、归档迁移）产生的空洞**从未 `VACUUM`**。实际有效数据可能仅 ~70 MB，一半磁盘被浪费，且影响查询缓存效率。

### 6.3 JSON / 报告文件无轮转

| 位置 | 数量 | 问题 |
|------|------|------|
| `07-data/whale_states/*.json` | 283 | 最旧 2026-03-13（近 3 个月未更新仍在"活跃"目录） |
| `07-data/whale_states_archive/` | 1,796 | 归档无上限 |
| `07-data/monitor_report_*.json` | 158 | 每次扫描落一个，从 4-17 起堆积，无清理 |

### 6.4 归档/快照表无保留策略

```
positions        : 11,374
positions_archive : 38,383   ← 归档是在用数据的 3.4 倍
daily_price_snapshots : 39,142
market_title_map : 46,656
```
归档表持续单增，无 TTL，是 6.2 死空间的来源之一。

### 6.5 配置/命名漂移（dead reference）

- `config.py::DBTables.SIGNALS = "semantic_signals"`，但实际表名为 `signals`（且为 0 行）——**配置常量指向一个查询不到数据的名字**，属典型的"代码与库脱节"腐化。

### 6.6 遗留垃圾文件

- 根目录 `polymarket_data.db` = **0 字节**（5-23 创建后从未使用）。
- 15 个 `*.bak` 散落在 `06-tools/analysis`（含 `whale_tracker_v2.py.bak.20260516` 等带日期备份）。

### 6.7 是否存在"数据腐化"？——**是，且程度严重**

综合判断，本项目同时存在三类经典数据腐化：
1. **结构腐化**：垃圾行/僵尸标志/矛盾字段（6.1、4.1①、4.1③）。
2. **物理腐化**：膨胀不回收的 SQLite（6.2）。
3. **生命周期腐化**：JSON/报告/归档无轮转、无 TTL（6.3、6.4）。
4. **引用腐化**：配置常量/空表与实际不符（6.5、1.2）。

---

## 7. 优先级改进清单

### P0 — 数据腐化止血（本周）
1. **清洗 whales 表**：删除 `total_value=0 AND position_count=0 AND is_watched=0` 的行（预计清掉 ~7 万行）；在 `sync_whales` 落库前加价值下限闸门。
2. **修复 `has_activity` 逻辑**：改为反映当前状态而非单调置位；一次性 `UPDATE whales SET has_activity=0 WHERE total_value=0`。
3. **`VACUUM` + 定期化**：立即回收 ~47% 死空间；加每周 `VACUUM`/`PRAGMA optimize` 任务。
4. **报告/状态文件轮转**：`monitor_report_*.json`、`whale_states` 加保留窗口（如 14 天）与归档上限。
5. 删除根目录 0 字节 `polymarket_data.db` 与 15 个 `*.bak`。

### P1 — 让"已实现"真正产出（两周）
6. 排查 Phase 3 调度链路，使 `signals/signal_results/quality_reports/threshold_history` 等真正落数据；否则在文档中明确标注为"未启用"。
7. 统一同步实现：在 `data_sync.py` / `data_sync_v2.py` 间选定权威者，废弃另一个。
8. 修正 `config.DBTables.SIGNALS` 命名漂移；为 schema 引入版本号。

### P2 — 工程化（一个月）
9. `print` → `logging`（结构化、分级、落 `07-data/logs/`）；收敛宽泛/裸 except。
10. 生产改用 gunicorn+eventlet；docker-compose 加 healthcheck/资源限制；引入最小 CI（pytest + lint）。
11. 将 19 处硬编码绝对路径收敛到 `config.py`；用配置替代 DB symlink 约定。

---

## 8. 总评

这是一个**功能丰富、迭代活跃、架构理念正确**的预测市场监控系统，鲸鱼跟踪深度（持仓重建→集中度→收敛→PnL→LLM 解读）尤其是亮点，优雅降级与多源兜底体现了不错的工程直觉。

但它正处于**典型的"快速迭代后未做数据治理"困境**：核心库已被 98% 的垃圾行淹没、近半磁盘是死空间、Phase 3 的质量自动化本身却没有在治理数据——**"质量监控"模块自己最需要被质量监控**。同时工程基线（日志、异常、生产 WSGI、CI、迁移）落后于功能复杂度。

**建议**：先做 P0 数据止血与回收，再补 P1 让已建表产出数据、收敛双实现，最后做 P2 工程化。当前阶段**不宜在脏数据之上继续叠加新策略**，否则信号可信度无从谈起。

---

*本报告基于 2026-06-02 的代码与运行数据快照，如后续已修复，请以最新状态为准。*
*评审生成时间：2026-06-02*
