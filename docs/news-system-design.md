# Polymarket 新闻监控系统设计

## 设计目标

构建一个双层新闻监控体系：
- **宏观层**: 追踪 Polymarket 全局热门话题
- **微观层**: 关联具体鲸鱼的持仓市场
- **核心**: 时间高度相关（<1小时延迟）

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    新闻监控系统                              │
├─────────────────────┬───────────────────────────────────────┤
│     宏观层 (全局)    │           微观层 (鲸鱼)                │
├─────────────────────┼───────────────────────────────────────┤
│ 1. 抓取全局热门市场   │ 1. 监听鲸鱼调仓事件                    │
│ 2. 提取热门类别      │ 2. 提取持仓市场关键词                   │
│ 3. 抓取类别相关新闻   │ 3. 抓取市场相关新闻                     │
│ 4. 生成热门新闻概览   │ 4. 关联时间窗口                        │
└─────────────────────┴───────────────────────────────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  输出层       │
                    │ - Telegram   │
                    │ - Dashboard  │
                    │ - 实时推送   │
                    └──────────────┘
```

---

## 宏观层：全局热门新闻

### 数据来源

| 来源 | 类型 | 优先级 | 说明 |
|------|------|--------|------|
| Polymarket Trending | 市场数据 | 最高 | 官方热门市场 |
| 高持仓价值市场 | 市场数据 | 高 | 交易量>$1M |
| 高波动市场 | 市场数据 | 高 | 24h价格变化>10% |
| Twitter Trending | 社交数据 | 中 | 相关话题热度 |
| 新闻网站头条 | 新闻数据 | 中 | Reuters/Bloomberg |

### 热门类别识别

```python
# 从市场标题聚类
markets = [
    "Will Trump win 2024?",
    "Will Trump win popular vote?",
    "Will Biden drop out?",
    "Will RFK Jr. endorse Trump?"
]

# 提取类别
Category: "US Election 2024"
Keywords: ["Trump", "Biden", "election", "2024"]
Volume: $50M total
Trend: ↑ 15% (24h)
```

### 输出格式

```
🔥 Polymarket 热门新闻概览 (08:00)

📊 热门类别 TOP 3:

1️⃣ US Election 2024
   💰 总交易量: $125M (↑23%)
   📈 活跃市场: 15个
   🔥 热度: 🔥🔥🔥🔥🔥
   
   📰 相关新闻 (1小时内):
   • [12min] Reuters: 最新民调显示Trump在摇摆州领先
   • [45min] Bloomberg: 民主党内部讨论替换候选人
   • [58min] Twitter: @NateSilver 发布选举模型更新
   
   💡 市场情绪: 看涨Trump (65% vs 35%)

2️⃣ Middle East Conflict
   💰 总交易量: $89M (↑45%)
   📈 活跃市场: 8个
   🔥 热度: 🔥🔥🔥🔥
   
   📰 相关新闻:
   • [8min] Al Jazeera: 伊朗外长表态愿意谈判
   • [22min] Reuters: 美国航母抵达地中海
   • [51min] Twitter: 多个信源称停火协议接近达成
   
   💡 市场情绪: 缓和预期上升

3️⃣ Crypto ETF
   💰 总交易量: $67M (↑12%)
   ...
```

---

## 微观层：鲸鱼持仓关联新闻

### 触发机制

```python
# 触发条件（满足任一）
triggers = [
    "新鲸鱼加入关注列表",
    "鲸鱼调仓 >20%",
    "鲸鱼持仓市场出现大新闻",
    "手动查询特定鲸鱼"
]
```

### 关键词提取策略

```python
# 示例：鲸鱼A的持仓
positions = [
    {"market": "Will Iran attack Israel in 2024?", "value": 50000},
    {"market": "Will BTC hit $100k in 2024?", "value": 30000},
    {"market": "Will Trump win 2024?", "value": 20000}
]

# 提取关键词（分层）
keywords = {
    "primary": ["Iran", "Israel", "BTC", "Trump"],  # 核心实体
    "secondary": ["attack", "2024", "$100k"],         # 关键条件
    "context": ["Middle East", "crypto", "election"]  # 背景类别
}

# 时间窗口
time_window = "whale.last_trade_time ± 2 hours"
```

### 新闻-持仓关联度评分

```python
def relevance_score(news, position):
    """
    评分维度:
    1. 关键词匹配度 (40%)
    2. 时间接近度 (30%) 
    3. 市场情绪一致性 (20%)
    4. 新闻权威性 (10%)
    """
    
    # 示例
    news: "Iran says ready for nuclear talks"
    position: "Will Iran attack Israel in 2024?"
    
    score = 85  # 高度相关
    # - 关键词: Iran (匹配)
    # - 时间: 新闻在调仓后1小时
    # - 情绪: 缓和 → 可能降低攻击概率
```

### 输出格式

```
🐋 鲸鱼 Stupendous-Eddy 持仓新闻

⏰ 最后调仓: 2小时前
💰 总持仓: $180k (5个市场)

📊 持仓市场与新闻关联:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Iran-Israel Conflict ($50k, 27%)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   🎯 市场: Will Iran attack Israel in 2024?
   📈 当前概率: 35% (↓8% from 2h ago)
   
   🔗 关联新闻 (调仓前后2小时):
   
   [调仓后1h] Reuters
   ├─ 标题: Iran signals willingness to resume nuclear talks
   ├─ 情绪: 正面 (缓和)
   ├─ 关联度: 90%
   └─ 影响: 可能降低冲突概率，与鲸鱼做空方向一致
   
   [调仓后30min] Twitter @user
   ├─ 标题: 以色列军方称未发现伊朗异常军事调动
   ├─ 情绪: 中性偏正面
   ├─ 关联度: 75%
   └─ 影响: 支持缓和判断
   
   💡 解读: 鲸鱼可能在押注局势缓和，新闻支持这一判断

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2. Trump 2024 ($40k, 22%)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   🎯 市场: Will Trump win 2024?
   📈 当前概率: 52% (↑3% from 2h ago)
   
   🔗 关联新闻:
   
   [调仓前30min] Bloomberg
   ├─ 标题: Trump leads in latest swing state polls
   ├─ 情绪: 正面 (对Trump)
   ├─ 关联度: 85%
   └─ 影响: 可能推动鲸鱼加仓Trump相关市场
   
   💡 解读: 新闻利好Trump，与鲸鱼做多方向一致

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3. BTC ETF ($30k, 17%)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   🎯 市场: Will BTC hit $100k in 2024?
   📈 当前概率: 28% (↑5% from 2h ago)
   
   🔗 关联新闻:
   [调仓后2h] CoinDesk
   ├─ 标题: 多家机构提交比特币ETF申请
   ├─ 情绪: 正面
   ├─ 关联度: 80%
   └─ 影响: 利好BTC价格
   
   💡 解读: 机构入场消息，支持鲸鱼看涨判断

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 整体分析:
├─ 新闻-持仓一致性: 85% (高度一致)
├─ 时间相关性: 优秀 (平均延迟<1h)
├─ 信号可信度: 高 (新闻支持鲸鱼判断)
└─ 建议: 关注Iran局势发展，可能影响最大持仓
```

---

## 时间相关性保障

### 实时性策略

| 策略 | 实现 | 延迟 |
|------|------|------|
| **RSS订阅** | Reuters/Bloomberg RSS | <15min |
| **Twitter流** | Twitter API v2 (stream) | <1min |
| **新闻API** | NewsAPI.org | <30min |
| **主动轮询** | 每5分钟抓取 | 5-10min |

### 时间窗口设计

```python
# 根据事件类型动态调整
TIME_WINDOWS = {
    "breaking_news": 30,      # 突发新闻：30分钟内
    "whale_trade": 120,       # 鲸鱼交易：前后2小时
    "market_movement": 60,    # 市场波动：1小时内
    "daily_summary": 1440     # 日汇总：24小时内
}
```

---

## 技术实现

### 数据流

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  数据源       │     │  新闻抓取器   │     │  关联引擎    │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ Twitter API  │────▶│ 实时流处理   │────▶│ 关键词匹配   │
│ Reuters RSS  │────▶│ 定时轮询    │────▶│ 时间窗口过滤 │
│ Bloomberg    │────▶│ 增量更新    │────▶│ 关联度评分   │
│ NewsAPI      │────▶│ 去重存储    │────▶│ 情绪分析    │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                       ┌──────────────────────────┘
                       ▼
              ┌─────────────────┐
              │    输出层        │
              ├─────────────────┤
              │ Telegram Bot    │
              │ Dashboard API   │
              │ 实时推送        │
              └─────────────────┘
```

### 核心模块

```python
# 1. 宏观新闻抓取器
class GlobalNewsFetcher:
    def fetch_trending_topics(self) -> List[Topic]
    def fetch_category_news(self, category: str) -> List[News]
    def calculate_heatmap(self) -> Heatmap

# 2. 微观新闻关联器
class WhaleNewsConnector:
    def extract_keywords(self, positions: List[Position]) -> Keywords
    def fetch_related_news(self, keywords: Keywords, window: int) -> List[News]
    def calculate_relevance(self, news: News, position: Position) -> Score
    def generate_report(self, whale: Whale) -> Report

# 3. 时间同步器
class TimeSync:
    def align_news_with_trades(self, news: List[News], trades: List[Trade])
    def detect_temporal_patterns(self) -> Patterns
```

---

## 输出策略

### 实时推送（高优先级）

```
触发条件:
- 突发新闻 + 高关联度 (>80%)
- 鲸鱼调仓后1小时内出现相关新闻
- 市场情绪剧烈变化

推送渠道: Telegram (即时)
```

### 定时汇总（中优先级）

```
频率:
- 每小时: 热门类别更新
- 每4小时: 鲸鱼持仓新闻汇总
- 每日: 完整新闻分析报告

推送渠道: Telegram + Dashboard
```

### 按需查询（低优先级）

```
场景:
- 用户想查看特定鲸鱼的新闻
- 用户想查看特定类别的新闻

渠道: Dashboard 查询界面
```

---

## 下一步行动

1. **确认数据源的API访问**
   - Twitter API v2 (需要开发者账号)
   - NewsAPI (免费版足够)
   - Reuters RSS (公开)

2. **实现优先级**
   - P0: 微观层（鲸鱼持仓关联）- 对你最有用
   - P1: 宏观层热门类别
   - P2: 高级分析（情绪、预测）

3. **验证指标**
   - 新闻抓取延迟 < 15分钟
   - 关联度评分准确率 > 70%
   - 用户觉得有用（主观）

你觉得这个设计符合你的需求吗？需要调整哪些部分？
