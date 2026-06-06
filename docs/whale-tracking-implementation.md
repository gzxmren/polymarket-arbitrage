# Polymarket 鲸鱼跟踪系统实现详解

**文档时间**: 2026-04-05 16:53 GMT+8  
**版本**: v2.0

---

## 📊 系统架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                     鲸鱼跟踪系统架构                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ Polymarket   │    │  File        │    │  Dashboard   │      │
│  │ API          │───→│  Storage     │───→│  Database    │      │
│  │ (实时交易)    │    │ (whale_states│    │ (SQLite)     │      │
│  └──────────────┘    │  .json)      │    └──────────────┘      │
│         │            └──────────────┘           ▲              │
│         │                   ▲                   │              │
│         │                   │                   │              │
│         ▼                   │                   │              │
│  ┌──────────────┐          │            ┌──────────────┐      │
│  │ sync_changes │          │            │  data_sync   │      │
│  │ .py          │──────────┘            │  .py         │      │
│  │ (实时同步)   │  保存持仓              │  (定期同步)  │      │
│  └──────────────┘                        └──────────────┘      │
│         │                                   │                   │
│         │    直接写入 whales 表              │    从文件读取    │
│         │    (pseudonym)                   │    同步到数据库  │
│         ▼                                   ▼                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              SQLite Database (polymarket.db)              │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │  │
│  │  │ whales   │  │ positions│  │ changes  │  │ alerts   │ │  │
│  │  │ 鲸鱼信息 │  │ 持仓明细 │  │ 交易变动 │  │ 警报     │ │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                              ▼                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   Dashboard Web UI                        │  │
│  │              (React + Flask Backend)                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔄 数据流详解

### 1. 实时数据流 (sync_changes.py)

```
Polymarket API (data-api.polymarket.com/trades)
                    │
                    ▼
            ┌───────────────┐
            │ fetch_recent  │  获取最近 200 条交易
            │ _trades()     │
            └───────────────┘
                    │
                    ▼
            ┌───────────────┐
            │ save_changes  │  解析交易数据
            │ _to_db()      │  - wallet (proxyWallet)
            └───────────────┘   - market (slug)
                    │           - outcome
                    ▼           - size, price
            ┌───────────────┐   - timestamp
            │  保存到数据库  │
            │               │
            │ 1. changes 表 │  INSERT INTO changes
            │    (交易变动) │  (wallet, type, market, ...)
            │               │
            │ 2. whales 表  │  INSERT OR REPLACE INTO whales
            │    (用户名)   │  (wallet, pseudonym, ...)
            │    ← 新增    │  ON CONFLICT UPDATE
            └───────────────┘
```

**关键代码**:
```python
# 获取 API 数据
trades = fetch_recent_trades(200)

for trade in trades:
    wallet = trade.get('proxyWallet')
    api_pseudonym = trade.get('pseudonym')  # ← API 返回的用户名
    
    # 保存变动
    cursor.execute('INSERT INTO changes ...', (wallet, ...))
    
    # 同步用户名 (仅覆盖地址格式)
    if api_pseudonym and not api_pseudonym.startswith('0x'):
        cursor.execute('''
            INSERT INTO whales (wallet, pseudonym)
            VALUES (?, ?)
            ON CONFLICT(wallet) DO UPDATE SET
                pseudonym = CASE
                    WHEN whales.pseudonym LIKE '0x%' THEN ?
                    ELSE whales.pseudonym
                END
        ''', (wallet, api_pseudonym, api_pseudonym))
```

---

### 2. 定期同步流 (data_sync.py)

```
whale_states/*.json (1,048 个文件)
                    │
                    ▼
            ┌───────────────┐
            │ 读取所有文件  │  遍历 .json 文件
            │               │
            │ 每个文件包含: │  - positions (持仓)
            │ - positions   │  - total_pnl
            │ - total_pnl   │  - last_check
            │ - last_check  │
            └───────────────┘
                    │
                    ▼
            ┌───────────────┐
            │  解析鲸鱼数据 │
            │               │
            │ 计算:         │  - total_value (持仓总价值)
            │ - total_value │  - position_count (持仓数量)
            │ - top5_ratio  │  - top5_ratio (集中度)
            │               │
            │ 获取 pseudonym│  优先从数据库读取
            │ (保护机制)    │  否则使用地址缩写
            └───────────────┘
                    │
                    ▼
            ┌───────────────┐
            │  同步到数据库 │
            │               │
            │ INSERT OR     │  会覆盖 whales 表
            │ REPLACE INTO  │  但保留已有 pseudonym
            │ whales        │  (通过 _get_existing_
            │               │   pseudonym 保护)
            └───────────────┘
```

**关键代码**:
```python
# 保护机制：优先保留数据库中的有效名称
db_pseudonym = self._get_existing_pseudonym(wallet)
if db_pseudonym and not db_pseudonym.startswith('0x'):
    pseudonym = db_pseudonym  # ← 保留已有名称
else:
    pseudonym = wallet[:10] + '...'  # 使用地址缩写

# 同步到数据库
cursor.execute('''
    INSERT OR REPLACE INTO whales 
    (wallet, pseudonym, total_value, position_count, ...)
    VALUES (?, ?, ?, ?, ...)
''', (wallet, pseudonym, total_value, ...))
```

---

## 📁 数据存储结构

### 1. 文件存储 (whale_states/*.json)

**路径**: `~/polymarket-project/07-data/whale_states/0x{wallet}.json`

**内容示例**:
```json
{
  "positions": {
    "Bitcoin Up or Down - March 12, 6:35PM-6:40PM ET": {
      "proxyWallet": "0x0006af12cd4dacc450836a0e1ec6ce47365d8c63",
      "size": 46732.995713,
      "avgPrice": 0.055843,
      "curPrice": 0,
      "cashPnl": -2609.71,
      "outcome": "Down",
      "endDate": "2026-03-12"
    }
  },
  "total_pnl": -2609.71,
  "last_check": "2026-03-14T12:00:00Z"
}
```

**特点**:
- 每个鲸鱼一个文件
- 包含完整持仓明细
- **不包含 pseudonym** (这是问题所在)
- 由其他监控程序生成

---

### 2. 数据库存储 (polymarket.db)

#### whales 表 (鲸鱼信息)

| 字段 | 类型 | 说明 |
|------|------|------|
| wallet | TEXT PK | 钱包地址 |
| pseudonym | TEXT | 显示名称 |
| total_value | REAL | 总持仓价值 |
| position_count | INTEGER | 持仓数量 |
| top5_ratio | REAL | Top5 集中度 |
| is_watched | BOOLEAN | 是否关注 |
| has_activity | BOOLEAN | 是否有活动 |
| last_updated | TIMESTAMP | 最后更新时间 |

#### positions 表 (持仓明细)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增ID |
| wallet | TEXT FK | 鲸鱼钱包 |
| market | TEXT | 市场标识 |
| outcome | TEXT | 预测结果 |
| size | REAL | 持仓数量 |
| avg_price | REAL | 平均价格 |
| value | REAL | 当前价值 |

#### changes 表 (交易变动)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增ID |
| wallet | TEXT FK | 鲸鱼钱包 |
| type | TEXT | 变动类型 |
| market | TEXT | 市场标识 |
| change_amount | REAL | 变动金额 |
| timestamp | TIMESTAMP | 变动时间 |

---

## 🎯 核心逻辑详解

### 1. 鲸鱼识别逻辑

**什么是鲸鱼**?
- 持仓价值 > $10,000 (巨鲸)
- 持仓价值 > $1,000 (大鲸)
- 或者：有大额交易 (> $100)

**识别流程**:
```python
# 从交易数据识别
for trade in trades:
    wallet = trade['proxyWallet']
    size = trade['size']
    price = trade['price']
    value = size * price
    
    if value >= 100:  # 大额交易
        mark_as_whale(wallet)

# 从持仓数据识别
for wallet, positions in whale_states.items():
    total_value = sum(pos['value'] for pos in positions)
    if total_value >= 10000:  # $10k 以上
        mark_as_whale(wallet, tier='large')
    elif total_value >= 1000:  # $1k 以上
        mark_as_whale(wallet, tier='medium')
```

### 2. 变动追踪逻辑

**什么是变动**?
- 买入 (BUY)
- 卖出 (SELL)
- 持仓价值变化

**追踪流程**:
```
当前交易
    │
    ▼
获取 wallet + market + outcome
    │
    ▼
查询该钱包在该市场的历史持仓
    │
    ▼
对比变化:
- 新市场 → 新增持仓
- 数量增加 → 加仓
- 数量减少 → 减仓
- 价格变化 → 盈亏变动
    │
    ▼
保存到 changes 表
发送警报 (如果有重大变动)
```

**代码实现**:
```python
def track_changes(wallet, market, new_size, new_price):
    # 获取历史持仓
    old_position = get_position(wallet, market)
    
    if old_position is None:
        change_type = 'new_position'
    elif new_size > old_position['size']:
        change_type = 'increase'
    elif new_size < old_position['size']:
        change_type = 'decrease'
    else:
        change_type = 'price_change'
    
    # 计算变动金额
    change_amount = abs(new_size * new_price - 
                       old_position['size'] * old_position['price'])
    
    # 保存变动
    save_change(wallet, change_type, market, change_amount)
    
    # 发送警报 (如果变动 > $1000)
    if change_amount >= 1000:
        send_alert(wallet, change_type, change_amount)
```

### 3. 集中度计算逻辑

**什么是集中度**?
- 衡量鲸鱼持仓的分散程度
- HHI (Herfindahl-Hirschman Index)
- Top5/Top10 持仓占比

**计算公式**:
```python
def calculate_concentration(positions):
    total_value = sum(p['value'] for p in positions)
    
    # HHI 指数
    hhi = sum((p['value'] / total_value) ** 2 for p in positions)
    
    # Top5 占比
    sorted_positions = sorted(positions, key=lambda x: x['value'], reverse=True)
    top5_value = sum(p['value'] for p in sorted_positions[:5])
    top5_ratio = top5_value / total_value
    
    return {
        'hhi': hhi,
        'top5_ratio': top5_ratio,
        'diversification': 'high' if hhi < 0.2 else 'medium' if hhi < 0.5 else 'low'
    }
```

**解读**:
- HHI < 0.2: 高度分散 (风险低)
- HHI 0.2-0.5: 中度集中
- HHI > 0.5: 高度集中 (风险高)

---

## 🔔 警报系统

### 触发条件

| 条件 | 阈值 | 警报级别 |
|------|------|---------|
| 大额买入 | > $10,000 | 🔴 高 |
| 大额卖出 | > $10,000 | 🔴 高 |
| 新鲸鱼出现 | 首次交易 | 🟡 中 |
| 集中度变化 | HHI 变化 > 20% | 🟡 中 |
| 价格剧烈波动 | 日内涨跌 > 50% | 🟠 中 |

### 警报流程
```
检测到变动
    │
    ▼
判断阈值
    │
    ├─── 超过阈值 ───→ 生成警报记录
    │                      │
    │                      ▼
    │              保存到 alerts 表
    │                      │
    │                      ▼
    │              WebSocket 推送
    │                      │
    │                      ▼
    │              Telegram 通知
    │                      │
    │                      ▼
    │              Dashboard 显示
    │
    └─── 未超过阈值 ──→ 仅记录
```

---

## 📊 数据更新频率

| 数据源 | 更新频率 | 触发方式 | 延迟 |
|--------|---------|---------|------|
| sync_changes.py | 持续运行 | 外部定时调用 | ~5分钟 |
| data_sync.py | 按需运行 | Dashboard 调用 | 实时 |
| whale_states 文件 | 由其他程序更新 | 文件系统 | 不确定 |

---

## 🐛 已知问题与解决方案

### 问题1: 名称被覆盖

**症状**: data_sync.py 运行后，pseudonym 被覆盖为地址格式

**原因**: whale_states 文件不包含 pseudonym，INSERT OR REPLACE 会覆盖

**解决**: 
```python
# data_sync.py 中添加保护机制
db_pseudonym = self._get_existing_pseudonym(wallet)
if db_pseudonym and not db_pseudonym.startswith('0x'):
    pseudonym = db_pseudonym  # 保留已有名称
```

### 问题2: API 用户名覆盖率低

**症状**: 仅 30% 的交易有 pseudonym

**原因**: Polymarket API 不强制要求用户设置用户名

**解决**:
```python
# 混合策略
if api_pseudonym:
    use_api_name()
else:
    generate_pseudonym()  # 生成可读名称
```

### 问题3: 新鲸鱼识别延迟

**症状**: 新出现的鲸鱼不能立即被识别

**原因**: 需要积累足够交易数据

**解决**: 降低阈值，实时分析

---

## 🔧 扩展建议

### 1. 智能评分系统
```python
def calculate_whale_score(wallet):
    """计算鲸鱼聪明度评分"""
    factors = {
        'win_rate': get_win_rate(wallet),      # 胜率
        'avg_return': get_avg_return(wallet),  # 平均收益
        'consistency': get_consistency(wallet), # 一致性
        'risk_management': get_risk_score(wallet)  # 风险管理
    }
    return weighted_score(factors)
```

### 2. 策略识别
```python
def identify_strategy(positions):
    """识别鲸鱼交易策略"""
    if is_market_making(positions):
        return 'market_maker'
    elif is_trend_following(positions):
        return 'trend_follower'
    elif is_contrarian(positions):
        return 'contrarian'
    else:
        return 'unknown'
```

### 3. 预测模型
```python
def predict_next_move(wallet, market):
    """预测鲸鱼下一步动作"""
    features = extract_features(wallet, market)
    prediction = model.predict(features)
    return prediction
```

---

## 📁 相关文件

| 文件 | 路径 | 说明 |
|------|------|------|
| sync_changes.py | `06-tools/monitoring/sync_changes.py` | 实时同步 |
| data_sync.py | `dashboard/backend/app/services/data_sync.py` | 定期同步 |
| whale_states | `07-data/whale_states/*.json` | 持仓文件 |
| database | `dashboard/backend/database/polymarket.db` | SQLite 数据库 |

---

## 🎯 总结

### 核心流程
1. **实时同步**: sync_changes.py 获取 API 交易数据，同步用户名
2. **定期同步**: data_sync.py 从文件读取持仓，更新数据库
3. **保护机制**: 优先保留数据库中的有效名称
4. **混合命名**: API 名称 + 生成名称

### 关键设计
- **分离存储**: 文件存持仓，数据库存名称和变动
- **增量更新**: 只更新变化的数据
- **容错机制**: 多层保护防止数据丢失
- **实时警报**: 重大变动立即通知

### 当前状态
- ✅ 1,048 个鲸鱼被跟踪
- ✅ 139,612 条变动记录
- ✅ 实时 API 用户名同步
- ✅ 生成名称作为兜底

---

*文档生成时间: 2026-04-05 16:53 GMT+8*
*版本: v2.0*