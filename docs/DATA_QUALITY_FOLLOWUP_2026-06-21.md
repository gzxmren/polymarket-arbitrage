# 数据质量复查与优化方案 — 2026-06-21

## 背景

距 6/16 全面代码审查（commit `b8b31fa`）已过 5 天，期间系统持续采集数据。本次复查目的：评估这 5 天采集的数据质量，定位健康巡检告警（信号链 2 天零新增、CLOB 摄像头停摆 92 分钟）的真实根因，并检查 6/16 修复的 B1-B5 是否在真实数据中生效。

## 结论速览

| # | 问题 | 严重度 | 状态 |
|---|------|--------|------|
| 1 | `daily_price_snapshots` 分页采集把"请求失败"误判为"翻到最后一页"，导致整天数据缺失或部分缺失 | 🔴 高 | 已修复 |
| 2 | `whale_following.py` 用手写前缀黑名单判断"可结算市场"，覆盖不全，是信号链零新增的根因 | 🔴 高 | 已修复 |
| 3 | `cleanup_stale_data.py` 03:30 CST 运行早于 UTC 跨天，造成系统性 ~8.5h"未清理"假阳性 | 🟡 中 | 已修复 |
| 4 | CLOB 摄像头停摆 92 分钟 | ⚪ 环境噪音 | 不修复（见下） |
| 5 | B1（信号去重）/ B4（72h no_data 判定） | ✅ 验证生效 | 无需改动 |

---

## 问题 1：daily_price_snapshots 分页缺陷（高优先级）

### 现象

直接查 `daily_price_snapshots` 按 `snapshot_date` 分组统计，发现：

```
2026-06-15  (缺失)
2026-06-16  297   ← 正常应 ~1950
2026-06-17  1973  ← 正常
2026-06-18  (缺失)
2026-06-19  (缺失)
2026-06-20  1970  ← 正常
```

历史上还有 05-10、05-26(677)、05-28(缺失)、05-30(198)、06-01(缺失)、06-03(297)、06-06(1349) 等同类缺口——这是一个持续存在、本次复查窗口内仍在发生的系统性问题，不是孤立事件。

### 根因

`06-tools/monitoring/snapshot_daily_prices.py::collect_all_markets()`：

```python
for page in range(max_pages):
    markets = fetch_active_markets(limit=page_size, offset=page * page_size)
    if not markets:
        break          # <-- 请求失败(网络异常被 fetch_active_markets 捕获后返回 [])
                        #     和"API 返回空列表=没有更多数据"被当成同一回事
    all_markets.extend(markets)
    if len(markets) < page_size:
        break
return all_markets
```

`fetch_active_markets()` 捕获 `(URLError, IncompleteRead, TimeoutError, OSError, JSONDecodeError)` 后统一 `return []`。`collect_all_markets()` 看到空列表就 `break`，无法区分"这页请求失败"和"已经没有更多市场了"：

- 如果**第一页**就请求失败 → 直接返回空列表 → 整天 0 条快照（06-18、06-19 属于此类）
- 如果**中途某页**失败 → 提前结束分页 → 只拿到部分市场（05-30 的 198 条、06-16 的 297 条属于此类）

`daily_price_snapshots` 是 `settle_signals.py` 计算入场/出场价格的唯一数据源，也是 `weekly_pzero.sh` 做 OOS 回测验证的根基。这张表的缺口会直接污染下游所有依赖它的结论，比 CLOB 摄像头的缺口严重得多（CLOB 数据只影响一个小众套利扫描）。

### 方案

把"请求失败"和"合法的空页"拆成两种返回值：
- `fetch_active_markets` 失败时返回 `None`（而不是 `[]`），且在抛出之前先做 3 次重试、线性退避 5s/10s，吸收短暂抖动；
- `collect_all_markets` 收到 `None` 时不再 `break`，而是直接中止整轮采集（抛异常），避免把"部分市场"悄悄当成"完整数据集"写入数据库——宁可今天整天缺数据（下游已有 `_get_nearest_price` 兜底逻辑），也不要带着虚假的"看起来正常"的部分数据。

### 实施

见下方代码改动（`snapshot_daily_prices.py`）。

---

## 问题 2：whale_following.py 可结算市场判定（高优先级，信号链零新增根因）

### 现象

`auto_health_check.py` 报告"signals 表 2 天内零新增"。直接验证 20 个候选聪明钱鲸鱼过去 48 小时的交易：

```
swisstony      changes48h=32  settleable=0
mooseborzoi    changes48h=34  settleable=1（wnba-min-gsv-2026-06-19，confidence=0.523 < 0.7 阈值，被过滤）
0x53757615...  changes48h=5   settleable=1（will-usa-win-the-2026-fifa-world-cup-467，已生成信号）
其余16个鲸鱼  全部 0 笔满足"可结算"
```

20 个鲸鱼 48 小时内共发生约 88 笔达标交易（>$10），只有 2 笔落在"可结算市场"判定内——其中一笔置信度不够被过滤，最终只生成了 1 个信号（6/19 那条）。**这不是代码崩溃导致零信号，而是黑名单覆盖不全，把鲸鱼真实交易最多的品类（加密短线/电竞/天气/WNBA 等）系统性地挡在外面。**

### 根因

`_UNSETTLEABLE_PREFIXES`（6/16 引入）是一份手写前缀黑名单：

```python
_UNSETTLEABLE_PREFIXES = (
    'btc-updown-', 'eth-updown-', 'sol-updown-',
    'nba-', 'nhl-', 'mlb-', 'nfl-', 'mls-', 'ufc-',
    'fifwc-', 'wta-', 'atp-', 'epl-', 'ucl-',
)
```

逐一核对最近 10 天 `changes` 表的市场前缀分布，发现该名单**漏掉了** `wnba-`、`doge-updown-`、`xrp-updown-`、`lol-`（英雄联盟电竞）、`val-`（Valorant 电竞）、`highest/lowest-temperature-`（天气）、`crint-` 等——而这些恰恰是鲸鱼交易量最大的品类（`highest-temperature` 单项就有 398 个不同市场）。

更深一层的问题：这种"手写前缀黑名单"设计本身就是在打地鼠——Polymarket 随时可能上新品类（下一个电竞游戏、下一种加密货币的 updown 市场……），黑名单永远追不上。而且这份名单已经和 `snapshot_daily_prices.py` 自己的 `EXCLUDED_PREFIXES`（只有 7 项，缺 fifwc-/wta-/atp-/epl-/ucl-/eth-updown-/sol-updown-）不一致——注释里写"与 snapshot_daily_prices.py 保持一致"，但实际上两份名单早已分叉。

### 方案

放弃黑名单，改用数据驱动的正向判定：查 `daily_price_snapshots` 里该市场最新一条快照的 `end_date`（市场关闭时间），只有当 `end_date` 距现在还有 ≥48 小时（信号持有期），才认为"可结算"——因为这样信号到期时市场必然还活着、还能取到出场价。

验证过样本：
- `will-usa-win-the-2026-fifa-world-cup-467`（FIFA 冠军盘口）：`end_date=2026-07-20`，远超 48h → 可结算 ✅
- `wnba-wsh-conn-2026-06-17`（WNBA 单场）：`end_date=2026-05-13`（次日就关闭）→ 不可结算 ❌，且无需手动维护 wnba 这个前缀

这个判定方式自动覆盖所有"次日即关闭"的短周期市场（不管是体育、电竞还是加密货币 updown），无需再手工枚举品类。`daily_price_snapshots.end_date` 字段覆盖率 98.5%（63637/64575 行非空），数据基础足够。

`snapshot_daily_prices.py` 自己的 `EXCLUDED_PREFIXES` 保持不变——那是"要不要采集这个市场快照"的性能优化（避免浪费 API 配额抓注定用不上的品类），目的和正确性要求都跟 `whale_following.py` 的"能不能结算"判定不同，没有必要、也不应该合并成一份名单。

### 实施

见下方代码改动（`whale_following.py`）。

---

## 问题 3：cleanup_stale_data.py 与 UTC 跨天时序错位（中优先级）

### 现象

`data_quality_check.py`（每日 08:00 CST 运行）连续多天报告"🔴 positions: N 条过期 30 天+仍未清理"（N 在 35~88 之间波动），看起来像清理逻辑失效。但手动重跑 `cleanup_stale_data.py` 立即把这批 35 条全部归档清理干净，"剩余过期30天+未清理: 0"。

### 根因

`cleanup_stale_data.py` 的 cron 是 `30 3 * * *`（03:30 **CST**）。服务器时区是 Asia/Shanghai (+08:00)，所以这个时刻对应 **UTC 前一天 19:30**。脚本里所有"过期判定"用的是 SQLite 的 `date('now', '-30 days')`，而 `date('now')` 永远是 **UTC** 日期。

也就是说：03:30 CST 运行时，SQLite 认为的"今天"其实是 CST 日历上的"昨天"。任何 `end_date` 恰好落在"昨天-30天"这个边界上的记录，会被这次清理判定为"还没到 30 天"而放过；要等到第二天 08:00 `data_quality_check.py` 跑的时候（那时 UTC 已经跨天），才会被认定为"过期 30 天+未清理"——而那次的清理窗口（03:30）已经过去了，所以每天都会有一批"刚跨过 30 天线"的记录被晚发现 ~8.5 小时甚至一整天，造成持续性的红色误报。

这不是清理逻辑坏了，是**清理时间点选得太早，比 UTC 日历跨天还早**，跟 `date('now')` 的 UTC 语义对不上。

### 方案

把 `cleanup_stale_data.py` 的 cron 从 03:30 CST 挪到 08:10 CST（=00:10 UTC，确保过了 UTC 0 点），并把 `data_quality_check.py` 从 08:00 CST 挪到 08:30 CST，让它在清理跑完之后再检查，不再用"清理前的快照"去评判"清理是否到位"。

---

## 问题 4：CLOB 摄像头停摆 92 分钟（环境噪音，不修复）

`/tmp/clob_pair_log.db` 里的 `clob_pair_runs` 显示 2026-06-21 05:00 之后连续两个 30 分钟周期（05:30、06:00）颗粒无收，06:30 才恢复（耗时 169.7s，是正常值的 2-3 倍）。期间日志里能看到 DNS 解析失败（`Name or service not known`）和 TLS 握手失败交替出现，是真实的网络/DNS 层中断，持续约 60-90 分钟——这个时长已经超出任何"脚本内重试退避"能合理覆盖的范围（重试几十秒解决不了一个持续 1.5 小时的网络中断）。

这和 6/16 那次复查记录的"00:00 UTC 前后短暂网络中断"是同一种模式，重复出现。`clob_logger_watchdog.py` 已经正确检测并告警（"摄像头停摆"），这正是这类环境性故障该有的应对方式。**不建议为此修改代码**——CLOB 订单簿数据是时间点快照，错过了无法回补，唯一能做的只有告警（已经有了），强行加重试只会徒增复杂度而不解决问题。

---

## 问题 5：验证 6/16 修复（B1/B4 确认生效，仅作记录）

- **B1 信号去重**：6/16 之后无重复信号组（`SELECT market,direction,wallet,count(*) ... HAVING c>1` 结果为空）。✅
- **B4 settle_signals 72h no_data 判定**：核对了 signal id=27（`wnba-wsh-conn-2026-06-17`），48h 持有期到期后第 X 小时才会被标记 no_data，逻辑按预期工作，此前怀疑的"超 72h 未结算"是笔误算错时间，非真实问题。✅

---

## 实施清单

1. `06-tools/monitoring/snapshot_daily_prices.py`：分页请求失败 ≠ 翻到最后一页，失败时重试 + 中止整轮采集。
2. `06-tools/analysis/whale_following.py`：可结算市场判定从前缀黑名单改为 `daily_price_snapshots.end_date` 数据驱动判定。
3. crontab：`cleanup_stale_data.py` 03:30→08:10，`data_quality_check.py` 08:00→08:30（均为 CST）。
