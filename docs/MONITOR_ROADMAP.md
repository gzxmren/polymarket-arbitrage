# Polymarket 监控系统演进路线图

*Created: 2026-05-02 | Last Updated: 2026-05-02*

---

## 背景

2026-05-02 将重型 `polymarket_monitor_v2.py`（864行主文件 + 26个依赖模块 ≈ 35K行）替换为轻量版 `polymarket_monitor_lite.py`（521行单文件，零外部依赖）。

**替换原因**：V2 因资源消耗大、模块互相依赖、无超时保护，从 2026-04-22 起被禁用。导致每天发送的日报全是零数据。

**核心原则**：一个能跑的80分系统 > 一个跑不动的100分系统。先有交易意愿和资金，再建系统。

---

## 当前状态 (Phase 0) ✅ 已完成

### Lite 监控 — `polymarket_monitor_lite.py`

| 项目 | 详情 |
|------|------|
| 文件 | `06-tools/monitoring/polymarket_monitor_lite.py` |
| Cron | `polymarket-monitor-lite`, 每5分钟, timeout 60s |
| 性能 | 7.5秒完成（25秒硬约束） |
| 通知 | 事件驱动，有变化才推送 Telegram |

**功能：**
1. **Pair Cost 扫描** — 200个市场，YES+NO < $0.95 才报，流动性 ≥ $5000
2. **聪明钱持仓追踪** — PolyCop Top 5（按 PnL 排序），持仓变化 ≥ $500 才报告
3. **增量去重** — 对比上次快照，只报新变化

**已禁用的旧组件：**
- `polymarket-summary-daily` → 改名 `polymarket-summary-daily-OLD`, disabled
- `polymarket-monitor-v2` → 之前已禁用
- `update-whale-states` → 之前已禁用

---

## Phase 1：跨平台套利验证 🔜 短期

### 目标
验证 Manifold / Metaculus 跨平台套利的实际可行性。

### 待验证问题
1. **匹配率** — Polymarket 事件能匹配到多少 Manifold/Metaculus 事件？
2. **价差大小** — 匹配事件的价差是否 > 5%？（< 5% 扣除手续费后无利润）
3. **流动性** — Manifold/Metaculus 能否实际成交？（小平台挂单可能成交不了）
4. **时效性** — 价差是持续存在还是瞬间消失？

### 验证方法
```bash
# 使用现有的 cross_market_scanner.py 手动跑一次
cd polymarket-project/06-tools/analysis
python3 cross_market_scanner.py
```

### 决策标准
| 结果 | 行动 |
|------|------|
| 匹配 > 10 个，价差 > 5%，流动性 > $1K | ✅ 做成独立脚本，每30分钟跑 |
| 匹配 > 10 个，但流动性不足 | ⚠️ 仅监控，不执行 |
| 匹配 < 5 个或价差 < 3% | ❌ 暂停，不值得投入 |

### 实现方案（验证通过后）
- 独立脚本 `cross_market_lite.py`，单文件
- Cron 每30分钟，timeout 60s
- 事件驱动通知（有套利才推送）
- 参考现有代码：`analysis/cross_market_scanner.py`

---

## Phase 2：Lite 增强 ⚡ 中期

### 目标
当 Lite 发现机会时，附加更多评估信息，帮助决策"该不该下注、下多少"。

### 2.1 VWAP/滑点计算 — 机会评估增强

**触发条件**：Lite 发现 Pair Cost 机会时
**功能**：估算实际成交滑点，避免"看着有利润但实际买不到"
**实现**：在 Lite 中新增 `enrich_with_slippage()` 函数
- 调用 CLOB API 获取订单簿
- 计算不同仓位的预期滑点
- 附加到通知消息

**参考代码**：`analysis/vwap_calculator.py`, `analysis/clob_api.py`

### 2.2 Kelly 公式 — 仓位建议

**触发条件**：Lite 发现任何可操作机会时
**功能**：根据胜率和赔率计算最优仓位比例
**实现**：在 Lite 中新增 `calculate_kelly()` 函数（纯数学计算，几行代码）
- 输入：估计胜率、当前赔率
- 输出：建议仓位比例

**参考代码**：`analysis/kelly_criterion.py`

### 2.3 CLOB 订单簿深度

**触发条件**：发现 PC 套利时
**功能**：拉取订单簿看真实深度，判断能否实际成交
**实现**：与 2.1 合并，同一次 API 调用

### 通知格式增强（Phase 2 完成后）

```
🎯 Pair Cost 套利机会

1. Will XXX happen?
   YES: $0.42 + NO: $0.51 = $0.93
   💰 利润: 7.0% | 流动性: $50,000
   📊 滑点: $1K 仓位 ≈ 0.3% | $5K 仓位 ≈ 1.2%
   🎯 Kelly 建议: 资金的 4.2%
   📖 订单簿: Bid $12K / Ask $8K
```

---

## Phase 3：交易系统 🔮 长期

**前提条件**：已有交易意愿和资金，已通过 Phase 0-2 验证了市场理解。

### 3.1 做市系统
- 持续挂双边单 + 管理库存风险
- 需要自动化执行引擎
- 参考代码：`analysis/market_maker.py`
- **触发条件**：决定用 API 自动做市

### 3.2 信号持久化
- 所有发现的机会写入 SQLite/PostgreSQL
- 回测历史胜率、分析信号质量
- 参考代码：V2 的 `save_signals_to_db()`
- **触发条件**：开始实际交易，需要追踪历史胜率

### 3.3 自动执行
- 监控 → 评估 → 下单 的全自动流程
- 风控：止损、仓位限制、异常检测
- **触发条件**：Phase 1-2 验证盈利模型可行

### 3.4 实时新闻驱动
- WebSocket 实时推送（不是 cron 轮询）
- 新闻事件 → 秒级反应 → 自动下单
- **触发条件**：需要分钟级响应速度的交易策略
- 参考代码：`analysis/news_monitor.py`

---

## 暂停的功能及恢复条件

| 功能 | 状态 | 恢复条件 | 参考代码 |
|------|------|---------|---------|
| 鲸鱼追踪（全网交易） | ⏸️ 暂停 | 需要发现未知鲸鱼时 | `analysis/whale_tracker_v2.py` |
| 动量策略 | ⏸️ 暂停 | 配合自动执行时 | `analysis/momentum_strategy.py` |
| 相关性矩阵 | ⏸️ 暂停 | 配合自动执行时 | `analysis/correlation_matrix.py` |
| 做市扫描 | ⏸️ 暂停 | 决定做市时 | `analysis/market_maker.py` |
| 鲸鱼跟随策略 | ⏸️ Lite 已替代 | 当前持仓追踪更优 | `analysis/whale_following.py` |
| 新闻监控 | ⏸️ 暂停 | 需要实时推送时 | `analysis/news_monitor.py` |
| 信号持久化 | ⏸️ 暂停 | 开始交易后 | V2 `save_signals_to_db()` |

---

## 架构演进

```
Phase 0 (当前)
┌──────────────────────────┐
│  polymarket_monitor_lite │  单文件，每5分钟
│  ├── Pair Cost 扫描      │
│  └── 聪明钱持仓追踪      │  → Telegram (事件驱动)
└──────────────────────────┘

Phase 1 (验证后)
┌──────────────────────────┐  ┌─────────────────────┐
│  polymarket_monitor_lite │  │  cross_market_lite   │  独立脚本
│  ├── Pair Cost 扫描      │  │  └── 跨平台套利      │  每30分钟
│  └── 聪明钱持仓追踪      │  └─────────────────────┘
└──────────────────────────┘

Phase 2 (增强)
┌──────────────────────────────────────────┐
│  polymarket_monitor_lite (增强版)          │
│  ├── Pair Cost 扫描                       │
│  ├── 聪明钱持仓追踪                       │
│  └── 机会评估 (VWAP + Kelly + CLOB深度)   │  → Telegram
└──────────────────────────────────────────┘

Phase 3 (交易系统)
┌───────────┐  ┌──────────┐  ┌──────────┐
│  监控层    │→│  评估层   │→│  执行层   │
│  Lite+跨平台│  │ VWAP/Kelly│  │ 自动下单  │
└───────────┘  └──────────┘  └──────────┘
                                  ↓
                            ┌──────────┐
                            │  数据层   │
                            │ DB + 回测 │
                            └──────────┘
```

---

## 文件索引

| 文件 | 说明 | 状态 |
|------|------|------|
| `monitoring/polymarket_monitor_lite.py` | 轻量监控主程序 | ✅ 运行中 |
| `monitoring/polymarket_monitor_v2.py` | 旧版重型监控 | ⏸️ 已禁用，保留参考 |
| `monitoring/send_summary.py` | 旧版日报发送 | ⏸️ 已禁用 |
| `analysis/cross_market_scanner.py` | 跨平台套利扫描 | 📋 待验证 |
| `analysis/pair_cost_scanner.py` | PC 扫描器 | 已被 Lite 内置替代 |
| `analysis/vwap_calculator.py` | VWAP/滑点计算 | 📋 Phase 2 嵌入 |
| `analysis/kelly_criterion.py` | Kelly 仓位计算 | 📋 Phase 2 嵌入 |
| `analysis/clob_api.py` | CLOB 订单簿 API | 📋 Phase 2 嵌入 |
| `07-data/monitor_lite_state.json` | Lite 运行状态 | ✅ 自动维护 |
| `07-data/polycop_observe/polycop_addresses.json` | PolyCop 高分地址 | ✅ 每6小时更新 |

---

## 决策日志

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-05-02 | V2 → Lite 重写 | V2 已禁用10天，35K行太重，无超时保护 |
| 2026-05-02 | 追踪持仓替代追踪交易 | 持仓快照对比噪音低，直接看聪明钱意图 |
| 2026-05-02 | 按 PnL 排序替代按 Score 排序 | Score 100 可能只有 $1K PnL，PnL 才是真聪明钱 |
| 2026-05-02 | 事件驱动替代定时报告 | 没机会时沉默 > 每天发空报告 |
| 2026-05-02 | 暂停做市/动量/新闻/相关性 | 无自动执行能力，纯监控无意义 |

---

*本文档随项目演进持续更新。每次重大变更时在决策日志中追加记录。*
