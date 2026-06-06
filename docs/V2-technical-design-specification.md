# V2 技术设计规格说明书 (TDS)

## 文档信息
- **版本**: V2.1
- **日期**: 2026-03-17
- **状态**: 定稿
- **作者**: 虾头 🦐

---

## 一、技术架构总览

### 1.1 架构原则
1. **模块化**: 每个策略独立模块，可插拔
2. **可扩展**: 支持新策略的快速接入
3. **可观测**: 完整的日志和监控
4. **成本可控**: LLM调用优化，控制API成本

### 1.2 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        数据层                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ 市场数据    │  │ 鲸鱼数据    │  │ 新闻数据           │ │
│  │ Polymarket │  │ 持仓/变动   │  │ BBC RSS            │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       策略引擎层                            │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐ │
│  │ 语义套利     │ │ 逻辑链矛盾   │ │ 鲸鱼跟随           │ │
│  │ Semantic     │ │ Logic Chain  │ │ Whale Following    │ │
│  └──────────────┘ └──────────────┘ └────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐ │
│  │ 新闻驱动     │ │ 行为预测     │ │ Pair Cost          │ │
│  │ News Driven  │ │ Prediction   │ │ (降低权重)         │ │
│  └──────────────┘ └──────────────┘ └────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       信号处理层                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ 信号评分    │  │ 质量检查    │  │ 效果追踪           │ │
│  │ Scoring     │  │ Quality     │  │ Tracking           │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       输出层                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Telegram    │  │ Dashboard   │  │ 数据库             │ │
│  │ 通知        │  │ 展示        │  │ SQLite             │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、核心模块技术设计

### 模块 1: 语义套利系统 (Semantic Arbitrage)

#### 2.1.1 核心类设计
```python
@dataclass
class Market:
    """市场数据类"""
    id: str
    title: str
    description: str
    yes_price: float
    no_price: float
    liquidity: float
    volume: float

@dataclass
class SemanticRelationship:
    """语义关系数据类"""
    market_a: str
    market_b: str
    similarity: float  # 0-1
    relationship_type: str  # 'implies', 'mutex', 'related', 'independent'
    confidence: float
    detected_by: str  # 'llm', 'manual', 'algorithm'
    detected_at: datetime

@dataclass
class SemanticSignal:
    """语义套利信号"""
    signal_type: str  # 'implies_violation', 'mutex_violation'
    market_a: Market
    market_b: Market
    violation_amount: float
    expected_profit: float
    confidence: float
    suggested_action: str
    created_at: datetime

class SemanticArbitrageStrategy:
    """语义套利策略核心类"""
    
    def __init__(self, llm_client, db):
        self.llm = llm_client
        self.db = db
        self.cache = {}  # 内存缓存
        self.SIMILARITY_THRESHOLD = 0.8
        self.TOLERANCE = 0.05
        
    def analyze_market_pair(self, market_a: Market, market_b: Market) -> Optional[SemanticRelationship]:
        """
        分析两个市场的语义关系
        
        流程:
        1. 检查缓存
        2. 使用LLM计算相似度
        3. 使用LLM识别逻辑关系
        4. 缓存结果
        """
        cache_key = f"{market_a.id}_{market_b.id}"
        
        # 检查缓存
        if cached := self._get_from_cache(cache_key):
            return cached
        
        # 计算语义相似度
        similarity = self._calculate_similarity(market_a, market_b)
        
        if similarity < self.SIMILARITY_THRESHOLD:
            return None
        
        # 识别逻辑关系
        relationship = self._detect_relationship(market_a, market_b, similarity)
        
        # 缓存结果
        self._cache_result(cache_key, relationship)
        self.db.save_relationship(relationship)
        
        return relationship
    
    def _calculate_similarity(self, market_a: Market, market_b: Market) -> float:
        """使用LLM计算语义相似度"""
        prompt = f"""
        分析以下两个预测市场的语义相似度：

        市场A: {market_a.title}
        描述: {market_a.description}

        市场B: {market_b.title}
        描述: {market_b.description}

        请评估它们的相似度（0-1），并说明原因。
        只返回JSON格式：{{"similarity": 0.85, "reason": "..."}}
        """
        
        response = self.llm.analyze(prompt)
        return self._parse_similarity(response)
    
    def _detect_relationship(self, market_a: Market, market_b: Market, similarity: float) -> SemanticRelationship:
        """使用LLM识别逻辑关系"""
        prompt = f"""
        分析以下两个市场的逻辑关系：

        市场A: {market_a.title}
        市场B: {market_b.title}

        关系类型：
        - implies: A发生则B必然发生（如：酋长队赢 → AFC赢）
        - mutex: A和B互斥（如：Trump赢 vs Biden赢）
        - related: 相关但无明确逻辑
        - independent: 无关

        只返回JSON格式：{{"relationship": "implies", "confidence": 0.9, "reasoning": "..."}}
        """
        
        response = self.llm.analyze(prompt)
        return self._parse_relationship(response, market_a, market_b)
    
    def scan_for_opportunities(self, markets: List[Market]) -> List[SemanticSignal]:
        """
        扫描所有市场，找出语义套利机会
        
        算法复杂度: O(n²) - 需要优化
        优化策略: 只检查高流动性市场，使用缓存
        """
        opportunities = []
        
        # 获取已知关系
        known_relationships = self.db.get_active_relationships()
        
        for rel in known_relationships:
            market_a = self._get_market_by_id(markets, rel.market_a)
            market_b = self._get_market_by_id(markets, rel.market_b)
            
            if not market_a or not market_b:
                continue
            
            if signal := self._check_violation(market_a, market_b, rel):
                opportunities.append(signal)
        
        return opportunities
    
    def _check_violation(self, a: Market, b: Market, rel: SemanticRelationship) -> Optional[SemanticSignal]:
        """检查定价是否违反逻辑关系"""
        
        if rel.relationship_type == 'implies':
            # A implies B: Price(A) <= Price(B) + TOLERANCE
            if a.yes_price > b.yes_price + self.TOLERANCE:
                return SemanticSignal(
                    signal_type='implies_violation',
                    market_a=a,
                    market_b=b,
                    violation_amount=a.yes_price - b.yes_price,
                    expected_profit=a.yes_price - b.yes_price,
                    confidence=rel.confidence,
                    suggested_action=f'SELL {a.title}, BUY {b.title}',
                    created_at=datetime.now(timezone.utc)
                )
        
        elif rel.relationship_type == 'mutex':
            # A mutex B: Price(A) + Price(B) <= 1 + TOLERANCE
            if a.yes_price + b.yes_price > 1.0 + self.TOLERANCE:
                return SemanticSignal(
                    signal_type='mutex_violation',
                    market_a=a,
                    market_b=b,
                    violation_amount=a.yes_price + b.yes_price - 1.0,
                    expected_profit=a.yes_price + b.yes_price - 1.0,
                    confidence=rel.confidence,
                    suggested_action=f'SELL both {a.title} and {b.title}',
                    created_at=datetime.now(timezone.utc)
                )
        
        return None

class LLMClient:
    """LLM客户端封装"""
    
    def __init__(self, provider='openai'):
        self.provider = provider
        self.cache = {}
        
    def analyze(self, prompt: str) -> str:
        """调用LLM进行分析"""
        # 检查缓存
        cache_key = hash(prompt)
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # 调用API
        if self.provider == 'openai':
            response = self._call_openai(prompt)
        else:
            response = self._call_other(prompt)
        
        # 缓存结果
        self.cache[cache_key] = response
        return response
    
    def _call_openai(self, prompt: str) -> str:
        """调用OpenAI API"""
        import openai
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )
        return response.choices[0].message.content
```

---

## 三、数据库设计

### 3.1 新增表结构

```sql
-- 语义关系表
CREATE TABLE semantic_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_a_id TEXT NOT NULL,
    market_b_id TEXT NOT NULL,
    similarity REAL NOT NULL,
    relationship_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    detected_by TEXT NOT NULL,
    is_verified BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market_a_id, market_b_id)
);

-- 逻辑规则表
CREATE TABLE logic_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    premise TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 信号记录表（扩展）
ALTER TABLE signals ADD COLUMN signal_source TEXT;
ALTER TABLE signals ADD COLUMN relationship_id INTEGER;
ALTER TABLE signals ADD COLUMN violation_amount REAL;
```

---

## 四、API设计

### 4.1 语义套利API

```python
@app.route('/api/semantic/relationships', methods=['GET'])
def get_semantic_relationships():
    """获取语义关系列表"""
    pass

@app.route('/api/semantic/analyze', methods=['POST'])
def analyze_market_pair():
    """分析市场对的语义关系"""
    pass

@app.route('/api/semantic/signals', methods=['GET'])
def get_semantic_signals():
    """获取语义套利信号"""
    pass
```

### 4.2 逻辑链API

```python
@app.route('/api/logic/rules', methods=['GET'])
def get_logic_rules():
    """获取逻辑规则"""
    pass

@app.route('/api/logic/contradictions', methods=['GET'])
def get_logic_contradictions():
    """获取逻辑矛盾"""
    pass
```

---

## 五、成本估算

### 5.1 LLM API成本

| 功能 | 调用频率 | 单次成本 | 月成本 |
|------|---------|---------|--------|
| 语义相似度 | 1000次/天 | $0.002 | $60 |
| 逻辑关系识别 | 500次/天 | $0.002 | $30 |
| 新闻情绪分析 | 100次/天 | $0.002 | $6 |
| **总计** | - | - | **$96/月** |

### 5.2 优化策略
- 缓存语义分析结果（24小时）
- 只分析高流动性市场
- 批量处理降低API调用次数

---

## 六、部署架构

```
┌─────────────────────────────────────────┐
│              监控程序                    │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │ 语义套利 │ │ 逻辑链  │ │ 鲸鱼    │  │
│  │ 策略     │ │ 检测    │ │ 跟随    │  │
│  └─────────┘ └─────────┘ └─────────┘  │
│              每10分钟扫描               │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│              Dashboard                  │
│         Flask + React                  │
│              实时展示                   │
└─────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│              SQLite                     │
│         数据持久化                      │
└─────────────────────────────────────────┘
```

---

## 七、监控与告警

### 7.1 关键指标
- LLM API调用次数/成本
- 语义分析准确率
- 逻辑矛盾检测准确率
- 信号胜率

### 7.2 告警规则
- LLM成本超过$5/天
- 语义分析准确率低于80%
- 系统错误率超过1%

---

*文档完成: 2026-03-17*  
*作者: 虾头 🦐*
