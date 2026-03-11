# 鲸鱼追踪策略

## 核心概念

**鲸鱼（Whales）** = 大额交易者，通常有信息优势或专业分析能力。

**策略：** 跟踪高绩效钱包，跟随他们的操作。

---

## 为什么有效？

### 信息优势假设
```
鲸鱼可能拥有：
- 内部信息
- 专业研究团队
- 更好的分析模型
- 早期信息渠道

跟随他们 = 搭便车
```

### 市场影响
```
大额订单推动价格
鲸鱼进场 = 信号
鲸鱼离场 = 警示
```

---

## 识别鲸鱼

### 数据来源

#### 1. Polymarket 排行榜
```bash
GET /leaderboard?window=all
```

**筛选标准：**
| 指标 | 阈值 |
|------|------|
| 总盈利 | > $50,000 |
| 胜率 | > 60% |
| 交易次数 | > 100 |
| 最近活动 | < 7 天 |

#### 2. 链上分析（Polygon）
```
Polygonscan API
- 交易历史
- 大额转账
- 钱包聚类分析
```

### 鲸鱼分类

| 类型 | 特征 | 跟随价值 |
|------|------|----------|
| **信息型** | 早期进场，高胜率 | ⭐⭐⭐⭐⭐ |
| **趋势型** | 追涨杀跌，快进出 | ⭐⭐⭐⭐ |
| **噪音型** | 随机交易，低胜率 | ⭐ |

---

## 监控流程

### Step 1: 建立鲸鱼名单
```python
whales = []
leaderboard = fetch_leaderboard()

for user in leaderboard:
    if (user['profit'] > 50000 and 
        user['win_rate'] > 0.60 and
        user['trade_count'] > 100):
        whales.append(user['wallet_address'])
```

### Step 2: 跟踪持仓变化
```bash
GET /positions?user={wallet_address}
```

**监控指标：**
- 新进场
- 加仓
- 减仓
- 清仓

### Step 3: 分析交易模式
```python
patterns = {
    'categories': [],      # 偏好哪些类别
    'avg_hold_time': 0,    # 平均持仓时间
    'entry_timing': [],    # 进场时机
    'position_sizing': []  # 仓位管理
}
```

---

## 跟随策略

### 1. 百分比跟随
```
鲸鱼买入 $10,000 (占其仓位 10%)
你跟随买入 $1,000 (占你仓位 10%)
```

### 2. 固定金额
```
每个信号固定买入 $500
严格风险控制
```

### 3. 自适应跟随
```
根据鲸鱼信心调整：
- 鲸鱼重仓 (>20% 仓位) → 你重仓
- 鲸鱼轻仓 (<5% 仓位) → 你观望
```

---

## 信号解读

### 强信号
```
✓ 多个鲸鱼同向进场
✓ 鲸鱼重仓 (>10% 仓位)
✓ 早期进场 (>48h 前)
✓ 鲸鱼历史胜率高
```

### 弱信号
```
✗ 单个鲸鱼，小仓位
✗ 晚期进场 (<12h 前)
✗ 鲸鱼之间分歧大
✗ 鲸鱼历史胜率一般
```

---

## 风险因素

### 1. 鲸鱼也会错
```
即使是顶级交易员也有失误
不能盲目跟随
```

### 2. 抢先交易风险
```
太多人跟随 = 价格被推高
你成交的价格比鲸鱼差
```

### 3. 数据延迟
```
API 数据有延迟
你看到时鲸鱼可能已经离场
```

### 4. 操纵风险
```
鲸鱼故意制造假信号
诱导跟随者接盘
```

---

## 输出格式

```markdown
### 鲸鱼活动警报

**市场:** 比特币今天会超过7万吗？
**鲸鱼:** 0x7845...51b5 | 排行榜 #3
**操作:** 买入 YES $15,000
**占其仓位:** 12%
**历史战绩:** 68% 胜率 | $127k 盈利
**信号强度:** Strong
**其他鲸鱼:** 2个同向，1个反向
**建议:** 关注，可考虑小仓位跟随
```

---

## 代码框架

```python
# whale_tracker.py

class WhaleTracker:
    def __init__(self):
        self.whales = self.identify_whales()
        self.positions_cache = {}
    
    def identify_whales(self):
        """识别高绩效钱包"""
        leaderboard = fetch_leaderboard(window='all')
        whales = []
        
        for user in leaderboard:
            if self.is_qualified(user):
                whales.append({
                    'address': user['wallet'],
                    'profit': user['profit'],
                    'win_rate': user['win_rate'],
                    'rank': user['rank']
                })
        
        return whales
    
    def is_qualified(self, user):
        """筛选标准"""
        return (user['profit'] > 50000 and 
                user['win_rate'] > 0.60 and
                user['trade_count'] > 100)
    
    def monitor(self):
        """监控持仓变化"""
        alerts = []
        
        for whale in self.whales:
            current = fetch_positions(whale['address'])
            previous = self.positions_cache.get(whale['address'])
            
            if previous:
                changes = self.detect_changes(previous, current)
                if changes:
                    alerts.append({
                        'whale': whale,
                        'changes': changes
                    })
            
            self.positions_cache[whale['address']] = current
        
        return alerts
    
    def detect_changes(self, old, new):
        """检测持仓变化"""
        changes = []
        # 对比新旧持仓，找出变化
        return changes
```
