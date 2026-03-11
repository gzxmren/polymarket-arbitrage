# 动量指标与技术分析

## 核心概念

**动量交易：** 价格趋势延续，追涨杀跌。

⚠️ **重要：** 预测市场 ≠ 股票，有到期日、二元结果(0-1 bounded)。技术分析是辅助，基本面（事件本身）更重要。

---

## RSI（相对强弱指数）

### 计算公式

```
RSI = 100 - (100 / (1 + RS))
RS = 平均涨幅 / 平均跌幅 (14周期)
```

### 信号解读

| RSI 值 | 信号 | 操作 |
|--------|------|------|
| > 70 | 超买 | 可能回调，考虑 NO |
| < 30 | 超卖 | 可能反弹，考虑 YES |
| 30-70 | 中性 | 观望 |

### 预测市场特殊性

```
传统资产：RSI > 70 常意味着回调
预测市场：可能一直超买直到事件确认

例子：
- 大选前3天，候选人YES价格 0.95
- RSI = 85 (超买)
- 但不回调，因为结果即将确定
```

---

## 移动平均线（MA）

### 常用周期

| MA | 用途 |
|----|------|
| 5-period | 快速信号 |
| 20-period | 趋势确认 |

### 信号

```
Short MA > Long MA: 看涨动量
Short MA < Long MA: 看跌动量
交叉: 趋势反转信号
```

### 金叉/死叉

```
金叉: Short MA 上穿 Long MA → 买入信号
死叉: Short MA 下穿 Long MA → 卖出信号
```

---

## 价格行为模式

### 1. 突破（Breakout）
```
价格突破近期区间 + 成交量放大 = 趋势形成
```

### 2. 盘整（Consolidation）
```
价格在狭窄区间波动 = 等待催化剂
```

### 3. 背离（Divergence）
```
价格新高但 RSI 未新高 = 动能减弱，可能反转
```

---

## 成交量分析

### 信号

| 情况 | 解读 |
|------|------|
| 成交量 > 2倍平均 | 重大事件/资金进入 |
| 价格上涨 + 放量 | 趋势确认 |
| 价格上涨 + 缩量 | 趋势可能不持续 |

---

## 数据获取

### 价格历史
```bash
GET /prices/{token_id}?interval=1h
```

### 参数
- `interval`: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`
- 建议：短期交易用 `15m`，波段用 `1h`

---

## 分析流程

```
Step 1: 获取 50+ 周期价格数据
Step 2: 计算 RSI (14周期)
Step 3: 计算 MA (5, 20)
Step 4: 识别信号
Step 5: 结合事件/新闻确认
```

---

## 信号强度评估

| 信号 | 强度 | 操作 |
|------|------|------|
| RSI < 30 + MA金叉 | 强 | 考虑 YES |
| RSI > 70 + MA死叉 | 强 | 考虑 NO |
| 单一指标 | 弱 | 等待确认 |
| 背离出现 | 谨慎 | 可能反转 |

---

## 局限性

### 1. 二元边界
```
价格被限制在 0-1 之间
接近边界时指标失效
```

### 2. 事件驱动
```
突发新闻 > 技术信号
技术指标滞后于信息
```

### 3. 低流动性扭曲
```
小市场订单簿稀疏
价格跳动不连续
指标噪音大
```

### 4. 时间衰减
```
临近结算日波动加剧
技术指标不稳定
```

---

## 输出格式

```markdown
### 动量分析

**市场:** 比特币今天会超过7万吗？
**当前价格:** YES $0.62 | NO $0.38
**RSI(14):** 28.5 [超卖]
**MA信号:** 看涨金叉
**成交量:** 高于平均 150%
**信心度:** Medium
**注意:** 接近结算时间，波动可能加剧
```

---

## 代码框架

```python
# indicators.py
import numpy as np

def calculate_rsi(prices, period=14):
    """计算 RSI"""
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_ma(prices, period):
    """计算移动平均线"""
    return np.convolve(prices, np.ones(period)/period, mode='valid')

def detect_crossover(short_ma, long_ma):
    """检测金叉/死叉"""
    if short_ma[-2] < long_ma[-2] and short_ma[-1] > long_ma[-1]:
        return 'golden_cross'  # 金叉
    elif short_ma[-2] > long_ma[-2] and short_ma[-1] < long_ma[-1]:
        return 'death_cross'   # 死叉
    return None
```
