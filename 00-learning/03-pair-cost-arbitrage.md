# Pair Cost 套利策略

## 核心原理

**当 YES价格 + NO价格 < $1.00 时，存在无风险套利。**

### 数学基础

```
Pair Cost = YES价格 + NO价格

如果 Pair Cost < 1.00:
    买入 YES + 买入 NO
    成本 < $1.00
    结算时获得 $1.00
    利润 = $1.00 - Pair Cost
```

### 实例

```
市场："比特币2026年会超过10万吗？"

价格：
- YES = $0.52
- NO  = $0.47

计算：
Pair Cost = 0.52 + 0.47 = $0.99
套利空间 = $1.00 - $0.99 = $0.01 (1%)

操作：
- 买入 1000 YES @ $0.52 = $520
- 买入 1000 NO  @ $0.47 = $470
- 总成本 = $990
- 结算获得 = $1000
- 利润 = $10 (1.01%)
```

---

## 为什么存在套利机会？

### 1. 流动性差异
- YES侧流动性好，价格准确
- NO侧流动性差，价格偏离

### 2. 市场情绪偏斜
- 大家都买YES，推高价格
- NO被忽视，价格偏低

### 3. 订单簿深度
- 大额买单吃掉卖单，价格跳升
- 两侧不平衡

---

## 检测流程

### Step 1: 获取市场列表
```bash
GET /markets?active=true&closed=false
```

### Step 2: 提取价格
```python
yes_price = market['outcomePrices'][0]  # YES价格
no_price = market['outcomePrices'][1]   # NO价格
pair_cost = yes_price + no_price
```

### Step 3: 计算套利空间
```python
if pair_cost < 0.99:  # 1%安全边际
    opportunity = {
        'market': market['question'],
        'pair_cost': pair_cost,
        'profit_margin': 1.00 - pair_cost,
        'yes_price': yes_price,
        'no_price': no_price
    }
```

### Step 4: 检查流动性
```bash
GET /book/{token_id}
```

确保两侧都有足够深度执行订单。

---

## 机会筛选标准

| 指标 | 阈值 | 说明 |
|------|------|------|
| **Pair Cost** | < 0.99 | 最低1%毛利 |
| **流动性** | > $1000/侧 | 确保可执行 |
| **结算时间** | < 24h | 快速资金回笼 |
| **价差** | < 3% | 避免流动性差的市场 |

---

## 风险因素

### 1. 滑点（Slippage）
```
问题：大额订单会推动价格

解决：
- 小额分批执行
- 模拟订单簿深度计算实际成本
```

### 2. 时机风险
```
问题：价格变动快，数据延迟

解决：
- 实时WebSocket数据
- 快速执行（< 30秒）
```

### 3. 结算风险
```
问题：市场被取消/无效

解决：
- 选择高流动性市场
- 避免争议性事件
```

### 4. Gas费用
```
问题：Polygon网络费侵蚀薄利

解决：
- 计算总成本（含Gas）
- 只抓 >1.5% 的机会
```

---

## 执行策略

### 理想情况
```
Pair Cost = $0.97 (3%空间)
买入 $1000 YES + $1000 NO
成本 = $970
结算 = $1000
利润 = $30 (3.1%)
```

### 现实考虑
```
Pair Cost = $0.98 (2%空间)
滑点损失 = $0.01
Gas费用 = $0.50
净利润 ≈ 0.5-1%
```

---

## 输出格式

```markdown
### Pair Cost 套利机会

**市场:** 比特币2026年会超过10万吗？
**Pair Cost:** $0.97 (3% below $1.00)
**最大规模:** $5,000 (基于订单簿深度)
**预估利润:** $150
**信心度:** High
**风险:** 结算时间较远(3个月)
```

---

## 代码框架

```python
# scanner.py
import requests

def scan_pair_cost_opportunities():
    markets = fetch_active_markets()
    opportunities = []
    
    for market in markets:
        yes_price = market['outcomePrices'][0]
        no_price = market['outcomePrices'][1]
        pair_cost = yes_price + no_price
        
        if pair_cost < 0.99:
            liquidity = check_liquidity(market['id'])
            if liquidity > 1000:
                opportunities.append({
                    'market': market['question'],
                    'pair_cost': pair_cost,
                    'profit': 1.00 - pair_cost,
                    'liquidity': liquidity
                })
    
    return opportunities
```
