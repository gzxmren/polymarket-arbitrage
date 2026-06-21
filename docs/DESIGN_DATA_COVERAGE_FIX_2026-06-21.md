# 设计方案文档：数据覆盖率与质量问题修复 — 2026-06-21

> 本文档**只做设计，不包含任何代码改动**。对应 `docs/reports/data-quality-review-20260621.html` 里列出的问题，逐项给出技术方案、改动点、风险评估，供确认后再进入实施阶段。

## ⚠️ 2026-06-21 补充：范围已按用户目标重新校准

用户目标明确为"系统稳定运行 + 采集的数据要能作为投资参考"。对照已有决策记录复核后，**方案①②(鲸鱼watchlist覆盖率/价格快照覆盖率)已确认暂停**，理由：

- memory `stratified-findings-2026-06-08` / `profitability-direction-2026-06`：跟鲸鱼策略已用36种过滤组合(6过滤×2持有期×3切点)测过，真实可交易宇宙资金加权收益**全部为负(-4%~-5%)**，且呈现"训练正/测试负"的过拟合模式——这不是数据覆盖率不够造成的假阴性，是这条策略本身没有 edge。
- 6/8 决策已经是"不再积累同类数据，转向前向实测套利"。方案①②补全的正是跟鲸鱼这条已被否掉的线的数据，不会变成投资参考数据。
- 当前真正对得上"投资参考"目标、且已经在跑的是 `clob_pair_logger.py` 的前向套利测试：复查时点 13天/546轮(累积546轮扫描/32401个市场快照)，已发现 **5次真实可成交套利**(2次量较大：×6558、×53488单位)，6/8 定的判定门槛是"≥2周/≥600轮"，目前只差1-2天。`08-backtests/analyze_clob_pairs.py` 现有判读："出现过可成交套利，需要更多样本评估持续性"。
- 该管道**不需要代码改动**：30分钟cron会自动重试，`clob_logger_watchdog.py` 已在检测并告警长时间缺口(如此前那次92分钟DNS中断)，缺口本身（真实网络中断）无法靠加重试弥补。当下唯一需要的动作是**等它跑满600轮/2周的阈值，然后认真看一遍 `analyze_clob_pairs.py` 的最终判读**——这是运营观察动作，不是设计/代码任务。

方案①②的设计内容保留在本文档下方，作为"如果未来有除投资信号外的其他用途(如纯监控/风控展示)需要更全的鲸鱼数据"时可以参考的方案，但**当前不安排实施**。

## 范围（已按上述调整）

| 来源问题 | 优先级 | 状态 |
|---|---|---|
| changes表752条重复行(实为1139行tx_hash碰撞) | P0 | 方案③，**唯一安排实施的代码改动** |
| BUY/SELL比例80/20失衡 | 已排查 | 不是bug，不需要修 |
| CLOB套利前向测试管道跑到600轮/2周决策点 | P0 | 无需代码改动，运营观察，见上方说明 |
| "重点关注"鲸鱼74%零成交记录 | ~~P0~~→暂停 | 方案①(保留设计，不实施) |
| 60%鲸鱼交易市场无价格快照 | ~~P0~~→暂停 | 方案②(保留设计，不实施) |
| whale_deep_analysis全部过期 / Phase-3四表悬空 / pair_cost_arbitrage(主DB)废弃schema / data_sync_v2.py废弃代码 | P2 | 清理建议(供拍板，未变) |

---

## 方案① 修复"重点关注"鲸鱼覆盖率 — ⏸️ 已暂停，仅保留设计供future参考

### 现状数据流（问题所在）

```
07-data/whale_watchlist.json (292个 is_watched 钱包，权威来源)
        │
        │  ※ 这条线目前不存在 ※
        ▼
07-data/whale_states/*.json  ←──┬── whale_tracker_v2.py::fetch_recent_trades(1000)
   (实际只有18个文件)            │   (全平台最近1000笔交易里"恰好出现"的钱包才会被发现/刷新)
        │                       │
        ▼                       └── update_whale_states.py 的补充步骤
data_sync.py::sync_whales()         (按 total_volume 排序，每轮只补20个占位，不读 is_watched)
        │
        ▼
whales.changes_count / concentration_history （292个里只有106个真的被记录）
```

`is_watched` 字段只在**下游消费**时被读取（选信号候选、UI展示、`data_sync.py` 判断是否写 `concentration_history`），但从未在**JSON刷新源头**被用来决定"该主动去查谁"——一个钱包想被持续追踪，唯一途径是"恰好出现在全平台最近1000笔交易里"，这对很多重点关注但交易频率不算最高的鲸鱼来说基本不可能。

### 技术可行性核查（已确认）

- 权威 watchlist 来源：`07-data/whale_watchlist.json`，顶层 `whales` 字典，292 条，每条含 `wallet`/`pseudonym` 等字段。
- 按钱包拉**当前持仓**的现成函数：`06-tools/analysis/whale_tracker_v2.py::fetch_wallet_positions(wallet)` → `DATA_API/positions?user={wallet}`。
- 按钱包拉**交易历史**的现成模式（已在 `06-tools/monitoring/backfill_side_for_whales.py::fetch_trades` 验证可用）：`DATA_API/trades?user={wallet}&limit=200`。
- 两者都是 Polymarket Data API 已有的、按钱包过滤的端点，不需要新增外部依赖。

### 设计方案

新增一个独立脚本 `06-tools/monitoring/refresh_watched_whales.py`（不改动现有 `whale_tracker_v2.py`/`update_whale_states.py` 的发现逻辑，纯增量、低风险）：

1. 读取 `whale_watchlist.json` 的 292 个钱包。
2. **分批轮转**：每次运行处理一个批次（建议批大小 50），用一个小的 checkpoint 文件（如 `07-data/.watchlist_refresh_cursor.json`）记录"下次从第几个开始"，循环滚动，保证全量 292 个在数小时内轮完一圈，而不是一次性发出 292×2 个请求。
   - 292 ÷ 50 ≈ 6 批，按每 30 分钟一批，约 3 小时轮完一圈——和现有 `update_whale_states.py` 6 小时的节奏量级相当。
3. 对批次内每个钱包：
   - 调 `/positions?user={wallet}` 取当前持仓 → 写/更新 `07-data/whale_states/{wallet}.json`（複用现有 JSON 结构，使其能被 `data_sync.py::sync_whales()` 正常消费，不需要改 `data_sync.py`）。
   - 调 `/trades?user={wallet}&limit=50` 取最近成交 → 按 `tx_hash` 去重后写入 `changes` 表（复用方案③的去重逻辑）。
4. 异常处理：单个钱包请求失败（超时/404/限流）只跳过该钱包，不影响批次内其他钱包，按 CLAUDE.md 异常处理规范捕获 `(URLError, TimeoutError, OSError, json.JSONDecodeError)`。
5. 速率控制：每个请求间 sleep 0.2-0.3s，避免短时间内对 Data API 打太密集。

### Cron 调度

```
*/30 * * * * cd .../polymarket-project && python3 06-tools/monitoring/refresh_watched_whales.py >> /tmp/refresh_watched_whales.log 2>&1
```

### 风险与开放问题（需要确认）

- whale_watchlist.json 里 95% 的条目 `last_seen` 停留在 3 月——这批钱包是否还值得继续追踪，还是该先清理watchlist本身？**本方案默认"先把追踪补上，watchlist 的去留是另一个独立决策"**，但建议同时评估是否要对 watchlist 做一次"是否仍然是大户"的重新校验（不在本方案范围内，列为后续可选项）。
- 292 个钱包全部纳入轮转，未来 watchlist 增长（比如到 1000+）需要重新评估批大小/轮转周期，目前按当前规模设计。

---

## 方案② daily_price_snapshots 长尾市场补抓 — ⏸️ 已暂停，仅保留设计供future参考

### 现状

`snapshot_daily_prices.py` 按 `volume24hr` 排序只抓 Gamma API 的 top ~2000 活跃市场。`changes` 表里鲸鱼实际交易过 9158 个不同市场，5480 个（60%）从未进过 `daily_price_snapshots`。

历史缺口（已发生的 5480 个市场从未被采集过）**无法回补**——Gamma API 不提供历史价格查询，daily_price_snapshots 本身就是"每天采集当天价格"的唯一记录方式，错过的日子永久缺失。本方案只解决"**从今天起**，鲸鱼交易过的长尾市场不再被漏采"。

### 技术可行性核查（已确认）

`GET https://gamma-api.polymarket.com/markets?slug={slug}` 支持按 slug 查询单个市场，返回字段与批量接口一致（`outcomePrices`/`endDateIso`/`volume24hr` 等），已用 `will-usa-win-the-2026-fifa-world-cup-467` 实测验证可用。

### 规模评估（已查询）

近 1 天 `changes` 表涉及 607 个不同市场，其中 424 个不在当天快照里——这是新设计每天需要补抓的量级，每个补抓一次 Gamma API 请求，按 0.3s/请求估算约 2-3 分钟即可完成，不会显著拉长 `snapshot_daily_prices.py` 的运行时间。

### 设计方案

在 `snapshot_daily_prices.py::snapshot()` 主流程（top-2000 批量采集）**之后**追加一个补抓步骤：

1. 查询 `changes` 表里 `timestamp > now - 2 days`（覆盖跨天边界）的全部 distinct market。
2. 减去本次主流程已经成功写入今天 `daily_price_snapshots` 的 market 集合，得到"今天还缺的长尾市场"列表（预期量级几百个，参考上面 424 这个数字）。
3. 对缺口列表逐个调 `/markets?slug={slug}`，解析后用与主流程相同的字段映射写入 `daily_price_snapshots`（复用现有 `INSERT OR REPLACE` 逻辑，自然幂等）。
4. 单个 slug 查询失败（市场已下架/404/网络错误）只跳过，不影响其他市场，也不影响主流程已采集的数据——失败处理逻辑与方案①一致（捕获网络异常即跳过，不中止整个补抓步骤；这与今天已修的"主流程页失败要中止"是两种不同场景，主流程失败会产生**系统性**缺失整天数据，补抓步骤单条失败只丢一个市场，影响面天然有界，不需要同等severity的中止逻辑）。

### 风险与开放问题

- 补抓步骤让 `snapshot_daily_prices.py` 总运行时间增加几分钟，需要确认现有 cron 调度（每日16:00）有没有下游任务紧跟着对运行时长敏感（目前看没有，下一个相关任务是次日的 `weekly_pzero`/`settle_signals`，时间窗口宽裕）。
- 是否需要给补抓设置一个每日上限（比如最多补 1000 个），防止某天 `changes` 异常活跃导致补抓耗时过长？建议加一个保守上限（如 1500），超出部分留到下一天的"近2天"窗口里继续补，不会丢失。

---

## 方案③ changes 表插入去重 — ✅ 已实施（2026-06-21）

实施记录：清理历史660条重复行（479个tx_hash分组，保留每组最早一条），新建 `idx_changes_tx_hash_unique` partial unique index，`sync_changes.py` 改为 `INSERT OR IGNORE` 并修正 `saved` 计数。功能测试验证：故意插入已存在的 tx_hash 被正确静默忽略。python-reviewer 审查后追加两处：① 空 tx_hash 交易加监控日志（不受唯一索引约束，是已知潜伏缺口非bug）② 同款计数偏差顺手修了 `backfill_side_for_whales.py`。详见下方原始设计（已落地，不再是计划）。

### 排查结论

`sync_changes.py::save_changes_to_db()` 对每条从 `/trades` 接口拿到的交易**直接 INSERT，没有任何去重检查**。该接口每次只返回"最近 N 条交易"（`limit=200`），如果两次抓取的时间窗口有重叠（不管是 OpenClaw cron 实际触发间隔与配置不一致，还是高频市场某个窗口里交易本身重复出现），同一笔交易就会被插入多次。

已用数据验证：`changes` 表里 1139 行的 `tx_hash`（区块链交易哈希，理论上每笔真实交易全局唯一）与另一行重复——这是比"四字段完全相同"更可靠的判定，因为 `tx_hash` 是上游 API 已经提供、按交易本身唯一的字段（`backfill_side_for_whales.py`/`sync_changes.py` 都已在用这个字段，只是没用来去重）。

### 设计方案

两层防护，互相独立：

1. **数据库层**：给 `changes.tx_hash` 加唯一索引（允许空值多条，因为存量数据里有少量 `tx_hash=''` 的历史记录需要兼容）：
   ```sql
   CREATE UNIQUE INDEX idx_changes_tx_hash_unique
   ON changes(tx_hash) WHERE tx_hash != '';
   ```
   SQLite 支持"partial unique index"，正好排除空字符串，不影响历史脏数据。
2. **应用层**：`save_changes_to_db()` 插入语句从 `INSERT INTO changes (...)` 改为 `INSERT OR IGNORE INTO changes (...)`，配合上面的唯一索引，重复 `tx_hash` 的插入会被静默忽略而不是报错中断整批写入。

这样无论未来重复的根因是 cron 触发频率、窗口重叠还是别的原因，都不会再产生重复行——属于"治标但完全堵住口子"的方案，不需要先精确定位"为什么会出现重叠"才能修。

### 历史脏数据清理（可选，独立于上面的防再发方案）

现有 1139 行重复可以用一次性脚本清理（保留每个 `tx_hash` 最早插入的一行，删除其余），建议放在方案③代码改动确认后一起处理，避免先清理又被旧代码重新插入回来。

---

## 排查结论：BUY/SELL 比例 80/20 — 不是 bug，不需要修复

担心点：`sync_changes.py` 里 `side = trade.get('side', 'BUY')` 在字段缺失时默认归类为 BUY，怀疑 SELL 被系统性漏报。

实测 Data API `/trades?limit=20` 实时样本：`side` 字段分布 BUY 17 / SELL 3 ≈ 85/15，与数据库历史整体比例 21k:5k ≈ 81/19 基本吻合。说明 `side` 字段在 API 响应里**稳定存在、不缺失**，代码里的默认值分支基本不会被触发——这就是 Polymarket 市场本身的真实交易行为（买入显著多于卖出，与"很多仓位持有到结算而非主动卖出"的预测市场特性一致），不是解析逻辑的问题。**本方案不对此做任何改动。**

---

## P2 清理项：需要拍板范围，不展开详细技术方案

以下几项是"悬空/废弃"而非"运行中的 bug"，处理方式取决于产品决策，列出选项供选择：

| 项目 | 现状 | 选项A | 选项B |
|---|---|---|---|
| `whale_deep_analysis` 表 | 11条记录全部过期(3月)，功能疑似停用数月 | 重新接入一个定期刷新job（需要先确认这个深度分析功能现在还有没有人用） | 正式废弃，清空表+从代码中移除相关调用 |
| Phase-3四表(`quality_reports`/`threshold_history`/`opportunity_history`/`strategy_performance`) | 代码与API路由已实现，无调度触发 | 给 `quality_report.py`/`threshold_optimizer.py` 接入定期cron，真正用起来 | 继续搁置，在文档里明确标注"已实现未启用"，避免被反复当成"新发现的故障" |
| `pair_cost_arbitrage`(主DB) | 0行，功能已迁移到独立 `07-data/clob_pair_log.db` | 删除主DB这张废弃表 | 保留但加注释说明已废弃，防止误读 |
| `data_sync_v2.py` | 无调度路径，仅被一次性脚本引用 | 移到 `99-archive/` | 直接删除（git历史仍可追溯） |

本文档不对这4项给出默认推荐——优先级是 P2，建议等 P0/P1（方案①②③）落地后再讨论。

---

## 实施优先级建议（已按用户目标重新校准）

1. ~~**方案③（changes去重）**~~ —— ✅ 已实施完成（见上方实施记录）。
2. **CLOB套利前向测试管道** —— 无代码改动，运营观察：等其跑满600轮/2周阈值，跑一遍 `08-backtests/analyze_clob_pairs.py` 认真看判读，这是当前最贴近"投资参考数据"目标的产出。**当前重点。**
3. ~~方案①②（鲸鱼覆盖率）~~ —— 已暂停，理由见文档顶部。除非未来明确有非投资信号的用途，否则不安排。
4. P2清理项 —— 优先级最低，等产品侧确认范围后再排期。
