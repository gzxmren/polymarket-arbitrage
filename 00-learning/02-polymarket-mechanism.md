# Polymarket 机制详解

## 平台架构

### 三大 API

```
┌─────────────────────────────────────────┐
│           Polymarket 平台               │
├─────────────┬─────────────┬─────────────┤
│  Gamma API  │  Data API   │  CLOB API   │
│   市场数据   │  用户数据   │   交易执行   │
├─────────────┼─────────────┼─────────────┤
│ 价格、交易量 │ 持仓、盈亏  │  下单、订单簿 │
│ 市场列表    │ 交易历史    │  成交       │
│ 历史价格    │ 排行榜      │  撤单       │
└─────────────┴─────────────┴─────────────┘
```

---

## Gamma API（市场数据）

### 基础 URL
```
https://gamma-api.polymarket.com
```

### 核心端点

#### 1. 获取活跃市场
```bash
GET /markets?active=true&closed=false&limit=50
```

**返回字段：**
- `question` - 市场问题
- `slug` - URL标识
- `outcomePrices` - YES/NO当前价格
- `volume` - 交易量
- `liquidity` - 流动性
- `resolutionDate` - 结算日期

#### 2. 市场详情
```bash
GET /markets/{market_id}
GET /markets?slug={market-slug}
```

#### 3. 价格历史
```bash
GET /prices/{token_id}?interval=1h
```

**时间间隔：** `1m`, `5m`, `15m`, `1h`, `4h`, `1d`

#### 4. 排行榜
```bash
GET /leaderboard?window=all
```

**窗口期：** `daily`, `weekly`, `monthly`, `all`

---

## Data API（用户数据）

### 基础 URL
```
https://data-api.polymarket.com
```

### 核心端点

#### 1. 用户持仓
```bash
GET /positions?user={wallet_address}
```

**返回字段：**
- `market` - 市场名称
- `outcome` - YES/NO
- `size` - 持仓数量
- `avgPrice` - 入场均价
- `currentPrice` - 当前价格
- `pnl` - 未实现盈亏

#### 2. 交易历史
```bash
GET /trades?user={wallet_address}
```

#### 3. 盈亏历史
```bash
GET /profit-loss?user={wallet_address}
```

#### 4. 排行榜排名
```bash
GET /leaderboard?user={wallet_address}
```

---

## CLOB API（交易执行）

### 基础 URL
```
https://clob.polymarket.com
```

### 核心端点

#### 1. 当前价格
```bash
GET /price?tokenId={token_id}
```

#### 2. 中间价
```bash
GET /midpoint?tokenId={token_id}
```

#### 3. 订单簿
```bash
GET /book?tokenId={token_id}
```

**返回结构：**
```json
{
  "bids": [{"price": 0.62, "size": 1000}, ...],
  "asks": [{"price": 0.63, "size": 500}, ...]
}
```

---

## 交易机制

### CLOB（中央限价订单簿）

```
买单 (Bids)          卖单 (Asks)
─────────────        ─────────────
价格    数量         价格    数量
0.62    1000   ←→   0.63    500
0.61    2000        0.64    1000
0.60    1500        0.65    800

Spread = 0.63 - 0.62 = 0.01 (1%)
```

### 订单类型

| 类型 | 说明 |
|------|------|
| **Limit Order** | 限价单，指定价格成交 |
| **Market Order** | 市价单，立即成交 |

---

## 费用结构

| 费用 | 说明 |
|------|------|
| **交易费** | 约 2%（从盈利中扣除） |
| **Gas费** | Polygon网络MATIC费用 |
| **提现费** | USDC跨链费用 |

---

## 结算流程

```
1. 事件结果确定
      ↓
2. UMA Optimistic Oracle 验证
      ↓
3. 市场结算（Resolution）
      ↓
4. 获胜方按$1/股获得赔付
```

**结算时间：**
- 明确结果：立即结算
- 争议结果：48小时挑战期

---

## 实用命令

```bash
# 获取活跃市场
curl "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=10"

# 获取用户持仓
curl "https://data-api.polymarket.com/positions?user=0x7845bc5e15bc9c41be5ac0725e68a16ec02b51b5"

# 获取价格历史
curl "https://gamma-api.polymarket.com/prices/{token_id}?interval=1h"
```

---

## 限制

- **速率限制：** 约100请求/分钟（公开端点）
- **WebSocket：** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
