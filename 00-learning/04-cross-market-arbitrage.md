# 跨平台套利策略

## 核心原理

**同一事件在不同平台定价不同，买低卖高。**

### 套利逻辑

```
事件："比特币今天会超过7万吗？"

平台价格：
┌─────────────┬────────┐
│ 平台        │ YES价格 │
├─────────────┼────────┤
│ Polymarket  │ 62%    │ ← 最低，买入
│ Manifold    │ 71%    │ ← 最高，卖出
│ Metaculus   │ 58%    │
└─────────────┴────────┘

套利：在 Polymarket 买 YES，在 Manifold 卖 YES
利润：71% - 62% = 9% (扣除手续费后约 5-7%)
```

---

## 平台对比

| 平台 | 特点 | API | 费用 |
|------|------|-----|------|
| **Polymarket** | 流动性最好 | Gamma/Data/CLOB | ~2% |
| **Manifold** | 社区驱动 | REST (免费) | 无 |
| **Metaculus** | 超级预测者 | REST (公开) | 无 |
| **Kalshi** | 美国合规 | REST (需key) | ~1% |

---

## 套利类型

### 1. 双向套利（理想）
```
Polymarket YES = 62%
Manifold YES   = 71%

操作：
- Polymarket 买入 YES ($62)
- Manifold 卖出 YES (获得 $71 play money)
- 结算后：Polymarket 获得 $100
- 净利润：$9
```

**问题：** Manifold 是 play money，无法直接套利

### 2. 单向套利（现实）
```
Polymarket YES = 62%
Metaculus 预测 = 75%

解读：
- Metaculus 超级预测者更准
- Polymarket 定价偏低
- 操作：在 Polymarket 买入 YES
- 预期：价格向 75% 靠拢
```

### 3. 信息套利
```
Polymarket 价格 = 62%
突发新闻后 = 应该 80%

操作：
- 新闻出来第一时间买入
- 市场反应滞后
- 价格追上后卖出
```

---

## 检测流程

### Step 1: 数据聚合
```python
# 获取各平台数据
polymarket = fetch_polymarket_markets()
manifold = fetch_manifold_markets()
metaculus = fetch_metaculus_questions()
```

### Step 2: 事件匹配
```python
# 匹配相同/相似事件
def match_events(polymarket, manifold, metaculus):
    matches = []
    for p_market in polymarket:
        # 关键词匹配
        m_market = find_similar(p_market['question'], manifold)
        me_question = find_similar(p_market['question'], metaculus)
        
        if m_market or me_question:
            matches.append({
                'polymarket': p_market,
                'manifold': m_market,
                'metaculus': me_question
            })
    return matches
```

### Step 3: 价差计算
```python
def calculate_gaps(matches):
    opportunities = []
    for match in matches:
        prices = {
            'polymarket': match['polymarket']['yes_price'],
            'manifold': match['manifold']['yes_price'] if match['manifold'] else None,
            'metaculus': match['metaculus']['prediction'] if match['metaculus'] else None
        }
        
        max_price = max(filter(None, prices.values()))
        min_price = min(filter(None, prices.values()))
        gap = max_price - min_price
        
        if gap > 0.05:  # 5% 阈值
            opportunities.append({
                'question': match['polymarket']['question'],
                'prices': prices,
                'gap': gap,
                'direction': f"buy_{min_platform}, sell_{max_platform}"
            })
    
    return opportunities
```

---

## 机会筛选标准

| 指标 | 阈值 | 说明 |
|------|------|------|
| **价差** | > 5% | 扣除费用后仍有利润 |
| **流动性** | > $5000 | 确保可执行 |
| **事件定义** | 高度一致 | 避免"相似但不同" |
| **结算时间** | 相同 | 避免时间错配 |

---

## 风险因素

### 1. 事件定义差异
```
Polymarket: "BTC > $70k by March 31, 2024?"
Manifold:   "BTC > $70k by end of March 2024?"

差异：截止时间可能不同
```

### 2. 结算时间错配
```
Polymarket 结算：事件发生后 24h
Manifold 结算：事件发生后立即

风险：时间窗口暴露
```

### 3. 平台费用差异
```
Polymarket: 2% 手续费
Kalshi: 1% 手续费

计算净利润时需扣除双边费用
```

### 4. 执行延迟
```
跨平台操作需要时间
价格可能在执行间变动
```

---

## Metaculus 共识策略

### 超级预测者优势
- Metaculus 聚合专业预测者
- 历史准确率 > 普通市场
- 当 Metaculus 与 Polymarket 分歧 > 15% 时，有 edge

### L023 策略
```
条件：
1. 市场一边倒 (>70%)
2. 但 Metaculus 不同意 (>15% 分歧)
3. 技术面也显示反转信号

操作：逆向下注
逻辑：极端情绪往往错误
```

---

## 输出格式

```markdown
### 跨平台套利机会

**事件:** 比特币今天会超过7万吗？
**平台价格:**
- Polymarket: 62%
- Manifold: 71%
- Metaculus: 58%

**价差:** 13% (Manifold vs Metaculus)
**建议:** 关注 Polymarket 62% 买入机会
**信心度:** Medium
**风险:** 事件定义略有差异
```

---

## 代码框架

```python
# cross_market_scanner.py

class CrossMarketArbitrage:
    def __init__(self):
        self.polymarket = PolymarketAPI()
        self.manifold = ManifoldAPI()
        self.metaculus = MetaculusAPI()
    
    def scan(self):
        # 获取各平台数据
        pm_markets = self.polymarket.get_active_markets()
        mf_markets = self.manifold.get_markets()
        me_questions = self.metaculus.get_questions()
        
        # 匹配事件
        matches = self.match_events(pm_markets, mf_markets, me_questions)
        
        # 计算价差
        opportunities = self.find_gaps(matches)
        
        return opportunities
    
    def match_events(self, pm, mf, me):
        # 使用关键词/NLP匹配相似事件
        pass
    
    def find_gaps(self, matches, threshold=0.05):
        # 找出价差 > 5% 的机会
        pass
```
