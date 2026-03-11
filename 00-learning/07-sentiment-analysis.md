# 情绪分析与逆向策略

## 核心概念

**情绪分析：** 聚合新闻、社交媒体、搜索趋势，判断市场情绪。

**逆向策略：** 当情绪极端时，反向操作。

---

## 数据来源

| 来源 | 类型 | 用途 |
|------|------|------|
| **X/Twitter** | 社交媒体 | 实时情绪、突发新闻 |
| **新闻 API** | 传统媒体 | 机构报道、公告 |
| **链上数据** | 资金流动 | 大额转账、鲸鱼活动 |
| **Google Trends** | 搜索趋势 | 公众关注度 |

---

## 情绪评分

### 评分体系
```
范围: -100 到 +100

+100: 极度看涨 (YES favored)
+50:  适度看涨
0:    中性
-50:  适度看跌
-100: 极度看跌 (NO favored)
```

### 权重分配

| 因素 | 权重 | 信号 |
|------|------|------|
| 新闻情绪 | 30% | 标题语调 |
| 社交量 | 25% | 提及量激增 |
| 大V倾向 | 20% | 关键账号立场 |
| 链上流 | 25% | 资金流向 |

---

## 数据收集

### Twitter/X 情绪

**搜索关键词：**
```
- "[事件主题] polymarket"
- "$[相关代币]"
- 关键影响者账号
```

**分析维度：**
- 提及量变化（激增检测）
- 情绪极性（正面/负面/中性）
- 影响者立场

### 新闻聚合

**来源：**
- CoinDesk, CoinTelegraph（加密）
- Reuters, Bloomberg（宏观）
- Polymarket 官方公告

**追踪：**
- 提及市场的头条
- 机构公告
- 监管新闻

### 链上监控

**指标：**
- USDC 大额流入 Polymarket
- 持仓集中度变化
- 新钱包活动激增

---

## 逆向信号（L023策略）

### 策略原理
```
当：
1. 市场一边倒 (>70% 偏向一侧)
2. 但情绪/基本面不支持
3. 技术面显示反转信号

操作：逆向下注
逻辑：极端情绪往往错误
```

### 触发条件

| 条件 | 阈值 |
|------|------|
| 价格偏斜 | > 70% YES 或 > 70% NO |
| Metaculus 分歧 | > 15% 差异 |
| 情绪极端 | > +80 或 < -80 |
| 背离 | 价格新高但情绪未新高 |

### 实例

```
市场："美联储3月会降息吗？"

数据：
- Polymarket YES = 75% (一边倒)
- Metaculus 预测 = 45% (严重分歧)
- 新闻情绪 = 中性
- 情绪评分 = +85 (极端)

判断：
市场过度乐观，实际概率被高估

操作：买入 NO
预期：价格向 45-55% 回归
```

---

## 时间因素

### 时段波动

| 时段 | 特点 |
|------|------|
| 亚洲/欧洲交接 | 高波动 |
| 美股开盘 | 新闻催化剂 |
| 周末 | 流动性低，价差大 |

### 事件驱动

```
公告前：情绪积聚
公告后：价格快速调整
临近结算：情绪收敛于结果
```

---

## 逆向策略风险

### 1. 趋势延续
```
极端情绪可能继续
逆向 = 接飞刀
```

### 2. 信息缺失
```
你可能不知道市场知道的事
逆向可能是错的
```

### 3.  timing 风险
```
价格可能长期偏离
资金占用成本高
```

### 4. 黑天鹅
```
极端事件发生
逆向者损失惨重
```

---

## 风控原则

### 仓位管理
```
逆向交易只用小仓位 (< 2%)
高赔率但低胜率
大数定律才能盈利
```

### 止损设置
```
价格突破 85% 或跌破 15%
说明极端情绪有理
止损离场
```

---

## 输出格式

```markdown
### 情绪分析

**市场:** 美联储3月会降息吗？
**整体评分:** +87/100 [极度看涨]

**细分:**
- 新闻: +60 - "就业数据强劲"
- 社交: +95 - 讨论量激增300%
- 链上: +80 - USDC大额流入

**关键催化剂:**
- 3月13日 CPI数据公布
- 美联储议息会议

**信心度:** Medium
**逆向警报:** YES - 情绪极端+Metaculus分歧
**建议:** 小仓位逆向，严格止损
```

---

## 代码框架

```python
# sentiment_analyzer.py

class SentimentAnalyzer:
    def __init__(self):
        self.twitter_client = TwitterAPI()
        self.news_client = NewsAPI()
        self.onchain_client = OnChainAPI()
    
    def analyze(self, market_topic):
        """综合分析市场情绪"""
        
        # 各渠道数据
        twitter_score = self.analyze_twitter(market_topic)
        news_score = self.analyze_news(market_topic)
        onchain_score = self.analyze_onchain(market_topic)
        
        # 加权汇总
        final_score = (
            twitter_score * 0.25 +
            news_score * 0.30 +
            onchain_score * 0.25 +
            self.influencer_score(market_topic) * 0.20
        )
        
        return {
            'overall': final_score,
            'breakdown': {
                'twitter': twitter_score,
                'news': news_score,
                'onchain': onchain_score
            },
            'contrarian_alert': self.check_contrarian(final_score, market_topic)
        }
    
    def check_contrarian(self, score, market):
        """检查逆向信号"""
        # 检查价格偏斜、Metaculus分歧等
        if abs(score) > 80:
            return {
                'triggered': True,
                'reason': 'Extreme sentiment reading',
                'suggested_action': 'Contrarian position'
            }
        return {'triggered': False}
```
