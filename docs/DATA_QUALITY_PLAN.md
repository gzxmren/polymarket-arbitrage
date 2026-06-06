# Polymarket 数据质量修复计划

> 创建时间: 2026-05-14
> 基于全面数据审计结果

---

## 总览

### 当前数据资产（唯一活库）

| 指标 | 数值 |
|------|------|
| 数据库大小 | 133 MB |
| 数据库路径 | `polymarket-project/dashboard/backend/database/polymarket.db` |
| Changes 记录 | 49,018 条 |
| 鲸鱼记录 | 68,361 条 |
| 浓度历史 | 240,288 条 |
| Daily Price Snapshots | 9,387 条 |
| Positions | 19,546 条 |
| 数据跨度 | 1 个月（2026-04-13 → 2026-05-14） |

### 数据质量评分（基于 5 月 6 日审计）

| 阶段 | 评分 | 状态 |
|------|------|------|
| 修复前 | ~15/100 | whales 全空壳、假套利、过期数据堆积 |
| 当前 | ~55/100 | changes 可读标题 62%、PnL 仍然全 0、HHI 全 0 |

---

## 问题 1：数据库碎片化 — 7 个副本只有 1 个有数据

### 症状
- 7 个 `polymarket.db` 散落在项目目录中
- 6 个是空壳（0-72KB），最后更新于 3 月
- 唯一活库在 `dashboard/backend/database/` 下（133MB）

### 根因
- 早期开发阶段各模块各自创建 DB，没有统一约定数据库路径
- 数据采集脚本（sync_changes.py）写 133MB 活库
- 但分析脚本（e.g. 06-tools 下的）写自己的本地副本
- 应用层（dashboard）用 `database/polymarket.db` 结果读到空库

### 修改方案

**方案 A：标准化 + symlink（推荐，改动最小）**
1. 确定 `dashboard/backend/database/polymarket.db` 为主库
2. 在所有误写路径下创建 `polymarket.db` 的 symlink 指向主库
3. 在新 DB 创建前检查是否有 symlink 存在

```bash
# 操作步骤
cd /home/xmren/.openclaw/workspace

# 备份原目标
mv database/polymarket.db database/polymarket.db.bak.20260514

# 创建 symlink 指向活库
ln -sf ../polymarket-project/dashboard/backend/database/polymarket.db database/polymarket.db

# 同理处理其他路径
for d in polymarket-project/database polymarket-project/06-tools/analysis/database polymarket-project/06-tools/monitoring/database polymarket-project/dashboard/database .polymarket-monitor; do
  [ -f "$d/polymarket.db" -a ! -L "$d/polymarket.db" ] && {
    mv "$d/polymarket.db" "$d/polymarket.db.bak.20260514"
    ln -sf ../../dashboard/backend/database/polymarket.db "$d/polymarket.db"
  }
done
```

**方案 B：提取为共享数据层（推荐但需代码修改）**
- 创建 `polymarket-project/database/` 作为唯一数据目录
- 所有模块通过环境变量 `POLYMARKET_DB_PATH` 引用数据库
- 各 script 启动时从环境变量读取路径

```python
# 代码中统一使用
DB_PATH = os.environ.get(
    'POLYMARKET_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'database', 'polymarket.db')
)
```

---

## 问题 2：鲸鱼 PnL 全部为 0 — 最致命的缺陷

### 症状
- 68,361 条鲸鱼记录，total_pnl 全部为 0
- 最高 volume whale（$864K）PnL 也是 0
- 无法区分赚钱高手和亏钱韭菜

### 根因
- `sync_changes.py` 只写 volume（从 changes 表累加交易量 size）
- **从未实现 PnL 计算逻辑** — 不是数据丢了，而是根本没写过
- PnL = Σ(卖出金额) - Σ(买入金额)，需要从 changes 表按 `(market, outcome, wallet)` 分组计算

### 修改方案

**阶段 1：批量 backfill PnL**
- 编写 `backfill_pnl.py` 脚本，从 changes 表扫描所有历史记录
- 对每个 `(wallet, market, outcome)` 分组，按时间排序
- 买入段（new_size > old_size）增加成本，卖出段（new_size < old_size）计算收益
- 写入 whales 表的 `total_pnl` 字段

**阶段 2：日常同步 PnL**
- 修改 `sync_changes.py` 或 `run_sync_changes.sh`，在新 changes 写入时同步更新 PnL
- 只需处理新增的 changes 条目的相关 wallet

**PNL 累加逻辑核心**：

```python
def update_pnl_for_wallet(db, wallet):
    """
    从 changes 表累加指定钱包的 PnL。
    
    核心逻辑：按 (market, outcome) 分组排序，
    买入记录成本，卖出记录收益。
    
    简化版本（变化检测）：
    - change_amount > 0 = 买入 + 费用
    - change_amount < 0 = 卖出收入
    - PnL = Σ卖出收入 + 当前持仓价值 - Σ买入成本
    """
    cursor = db.cursor()
    cursor.execute("""
        SELECT market, outcome, change_amount, old_size, new_size
        FROM changes
        WHERE wallet = ?
        ORDER BY market, outcome, timestamp
    """, (wallet,))
    
    cost_basis = {}  # (market, outcome) -> total cost
    total_pnl = 0
    
    for mkt, outcome, amount, old_sz, new_sz in cursor:
        key = (mkt, outcome)
        if old_sz == 0:  # 开仓
            cost_basis[key] = cost_basis.get(key, 0) + amount
        elif new_sz == 0:  # 平仓
            gross = cost_basis.pop(key, 0)
            total_pnl += amount - gross
        else:  # 加减仓
            delta = new_sz - old_sz
            ratio = delta / old_sz if old_sz > 0 else 1
            if delta > 0:  # 加仓
                cost_basis[key] = cost_basis.get(key, 0) + amount
            else:  # 减仓（部分卖出）
                realized = cost_basis.get(key, 0) * abs(ratio)
                cost_basis[key] = cost_basis.get(key, 0) - realized
                total_pnl += amount - realized
    
    db.execute("UPDATE whales SET total_pnl = ? WHERE wallet = ?", (total_pnl, wallet))
```

---

## 问题 3：鲸鱼名字过半是 unknown

### 症状
| 类型 | 数量 | 占比 |
|------|------|------|
| "unknown" | 36,480 | 53% |
| 0x 地址替代 | 1,238 | 2% |
| 真实 pseudonym | 30,643 | 45% |

### 根因
- `sync_changes.py` 从 Polymarket API 获取 whales 时，API 不返回 pseudonym
- Pseudonym 需要从 `https://gamma-api.polymarket.com/...` 的特定端点获取
- 或者是 API 返回了但程序没有解析存储

### 修改方案

1. 修改 `sync_changes.py` 从 API 获取 pseudonym：
   ```python
   # Gamma API 端点获取钱包信息
   url = f"https://gamma-api.polymarket.com/users/{wallet_address}"
   response = requests.get(url)
   data = response.json()
   pseudonym = data.get('pseudonym', 'unknown')
   ```

2. 批量 backfill：扫描所有 unknown 鲸鱼，逐个查询 API
   - 注意速率控制（每秒最多 5-10 个请求）
   - 可建立队列分批量处理

---

## 问题 4：Changes 标题覆盖率仅 62%

### 症状
- 49,018 条 changes 记录中 18,794 条（38%）market_title 为空
- 展示时只能显示 market slug（如 `will-armenia-win...`）

### 根因
- changes 写入时才从 API 查询 title
- API 可能返回空 title 或连接超时
- 早期代码可能没有实现 title 回填逻辑

### 修改方案

1. 利用 `market_title_map` 表（已有 46,656 条映射）
   - 检查 `market_title_map` 能否覆盖空 title
   ```python
   cursor.execute("""
       UPDATE changes SET market_title = (
           SELECT title FROM market_title_map 
           WHERE market_title_map.market = changes.market
       )
       WHERE market_title = '' AND EXISTS (
           SELECT 1 FROM market_title_map 
           WHERE market_title_map.market = changes.market
       )
   """)
   ```

2. 未覆盖的通过 API 补充查询
   ```python
   url = f"https://gamma-api.polymarket.com/events?slug={market_slug}"
   ```

---

## 问题 5：浓度监控 HHI 全部为 0

### 症状
- 180,106 条 concentration_history 记录（2026-05-15）
- 核心指标 `hhi` 99.6% 为 0
- `top5_ratio` 有值（今日 6,015 条 > 0）但 hhi=0

### 根因（2026-05-15 发现）

**不是计算逻辑错了，而是代码根本没运行。**

- `data_sync_v2.py` 第 180-186 行已有正确的 HHI 计算（从 positions 算 shares 平方和）
- 但它通过 `scheduler.py` 长期进程触发，**该进程已停止运行**
- `run_sync_changes.sh`（cron 每 5 分钟执行）只调用 `sync_changes.py`，不碰 concentration_history
- `concentration_history` 的 `hhi` 字段默认值为 0，所有新 INSERT 用了这个默认值

### 修改方案

**推荐方案 A：直接加到 sync_changes.py（改动最小）** 🌟

`syc_changes.py` 已每 5 分钟运行，已做 whale PnL 更新。对每个有变动的鲸鱼：
1. 从 API 或 positions 表获取当前持仓
2. 计算 HHI、top5_ratio、top10_ratio
3. 写入 concentration_history

改动量 ~30 行，不引入新进程：

```python
def update_concentration(db, wallet):
    """计算并写入鲸鱼的持仓集中度"""
    cursor = db.cursor()
    cursor.execute("""
        SELECT market, outcome, size, cur_price FROM positions WHERE wallet = ?
    """, (wallet,))
    positions = cursor.fetchall()
    
    if not positions:
        return
    
    # 计算总价值
    total_value = sum(float(r[2]) * float(r[3]) for r in positions)
    if total_value <= 0:
        return
    
    # 各仓位占比
    shares = [abs(float(r[2]) * float(r[3])) / total_value for r in positions]
    
    # HHI = Σ(占比)²
    hhi = sum(s * s for s in shares)
    
    # Top5 / Top10 占比
    sorted_shares = sorted(shares, reverse=True)
    top5_ratio = sum(sorted_shares[:5])
    top10_ratio = sum(sorted_shares[:10])
    
    cursor.execute("""
        INSERT INTO concentration_history (wallet, hhi, top5_ratio, top10_ratio, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (wallet, hhi, top5_ratio, top10_ratio, datetime.now().isoformat()))

    print(f"  集中度: HHI={hhi:.4f}, Top5={top5_ratio:.2%}")
```

**方案 B：重启 scheduler（不推荐）**
- scheduler.py 作为后台进程持续运行，但需要 systemd/tmux 保活
- 和 cron 的 sync_changes 功能重叠，冗余

**方案 C：加 cron job 跑 data_sync_v2.py（中等）**
- 每 5-15 分钟调用一次 python3 data_sync_v2.py
- 但和 sync_changes 各写各的，可能存在竞态

### Backfill（一次性）
已在 2026-05-14 通过 backfill_concentration.py 补了 670 条历史记录（HHI > 0）。
修复写入管道后，新数据会自动生成正确的 HHI。

---

## 问题 6：程序各自为战，没有统一调度

### 症状
- `run_sync_changes.sh` — 同步 changes 数据
- `polymarket_monitor_unified.py` (PolyCop) — 信号分析
- `polymarket_monitor_master.py` — 监控循环
- 三者在时间上可能重叠，DB 路径可能不同

### 根因
- 渐进式开发，功能模块独立添加
- 没有统一的编排框架
- 数据采集和分析耦合在一起

### 修改方案

**阶段 1：最小改动**
1. 统一所有脚本的 DB 路径（见问题 1）
2. 在 cron job 中错开时间：
   - sync-changes：每 30 分钟（数据层）
   - polymarket-monitor：每 5 分钟（分析层）
   - polymarket-daily-price-snapshot：每天早 8 点（价格层）

**阶段 2：编排框架**
创建 `polymarket_pipeline.py` 作为统一入口：

```python
#!/usr/bin/env python3
"""
Polymarket 数据管道调度器
"""
import sys
import argparse

STAGES = ['sync_changes', 'calc_positions', 'update_pnl', 'calc_concentration', 'snapshot_prices']

def run_pipeline(stages=None):
    for stage in stages or STAGES:
        run_stage(stage)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stages', nargs='+', choices=STAGES)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    run_pipeline(stages=args.stages)
```

---

## 修复优先级

### P0 — 立刻修（影响信号质量）

| # | 问题 | 预计工时 | 影响 |
|---|------|---------|------|
| 1 | 数据库整合（symlink） | 15 分钟 | 消除数据碎片，所有模块读同一份数据 |
| 2 | PnL backfill | 2 小时 | 让鲸鱼评分有意义，筛选真正的高手 |
| 3 | HHI bug 修复 + backfill | 1 小时 | 让浓度指标有实际价值 |

### P1 — 尽快修

| # | 问题 | 预计工时 | 影响 |
|---|------|---------|------|
| 4 | Market title backfill | 1 小时 | 提升 changes 可读性 |
| 5 | 鲸鱼名字补全 | 3 小时 | 识别更多知名地址 |

### P2 — 长期优化

| # | 问题 | 预计工时 | 影响 |
|---|------|---------|------|
| 6 | 统一调度框架 | 4 小时 | 消除竞态，可维护性提升 |
| 7 | PnL 日常同步 | 1 小时 | 保持 PnL 更新 |

---

## 数据质量监控（修复后）

建议在修复完成后添加以下监控：

1. **每日质量报告**（已有 polyCop signal 可以扩展）
   - Changes 数量增长趋势
   - 标题覆盖率
   - PnL 非零比例
   - HHI 非零比例

2. **异常检测**
   - 某表行数骤降 → 可能数据被清空
   - PnL 全变为 0 → 累加逻辑出错
   - 连续 N 小时无新数据 → 源或管道路障

3. **告警阈值**

| 指标 | 警告阈值 | 严重阈值 |
|------|---------|---------|
| 标题覆盖率 | < 80% | < 50% |
| PnL 非零比例 | < 10% | < 1% |
| 连续无新 changes | 4 小时 | 24 小时 |
| HHI 全 0 | — | 任何时候 |

---

## 附录：当前收集程序清单

| 程序 | 位置 | 频率 | 写哪些表 |
|------|------|------|---------|
| run_sync_changes.sh | polymarket-project/ | cron 30 分钟 | changes, whales（部分字段） |
| polymarket_monitor_unified.py | workspace/ | cron 5 分钟 | signals（部分）、Tg 通知（群聊） |
| polymarket_monitor_master.py | workspace/ | 监控循环 | 各类告警表 |
| 日频价格快照 | 新加 | cron 每天 8:00 | daily_price_snapshots |
| 浓度计算 | 内嵌在监控中 | 每次监控 | concentration_history |
| 套利检测 | 内嵌在监控中 | 每次监控 | pair_cost_arbitrage, cross_market_arbitrage |
