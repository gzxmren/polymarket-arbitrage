# Polymarket 监控系统 V2 技术实现文档

## 技术架构

### 1. 核心模块实现

#### 1.1 鲸鱼跟随策略 (whale_following.py)

```python
class WhaleFollowingStrategy:
    """鲸鱼跟随策略"""
    
    def __init__(self):
        self.db = Database()
        self.min_trade_size = 10000  # $10k
        self.min_win_rate = 0.6      # 60%胜率
        
    def identify_smart_money(self) -> List[Whale]:
        """识别聪明钱鲸鱼"""
        # 查询历史胜率
        query = """
            SELECT wallet, 
                   COUNT(CASE WHEN pnl > 0 THEN 1 END) * 1.0 / COUNT(*) as win_rate,
                   AVG(pnl) as avg_pnl
            FROM trades_history
            WHERE timestamp > datetime('now', '-30 days')
            GROUP BY wallet
            HAVING COUNT(*) >= 10 AND win_rate >= 0.6
            ORDER BY win_rate DESC
        """
        return self.db.query(query)
    
    def detect_large_trade(self, whale: Whale) -> Optional[Signal]:
        """检测大额调仓"""
        # 获取最近变动
        changes = self.get_recent_changes(whale.wallet, hours=1)
        
        for change in changes:
            if abs(change['value_change']) >= self.min_trade_size:
                return Signal(
                    type='whale_following',
                    whale=whale,
                    market=change['market'],
                    direction=change['direction'],
                    confidence=self.calculate_confidence(whale, change),
                    suggested_position=self.calculate_position(whale, change)
                )
        return None
    
    def calculate_confidence(self, whale: Whale, change: Change) -> float:
        """计算信号置信度"""
        factors = {
            'win_rate': whale.win_rate * 0.4,      # 历史胜率 40%
            'consistency': self.check_consistency(whale) * 0.3,  # 行为一致性 30%
            'market_size': self.market_size_score(change['market']) * 0.2,  # 市场规模 20%
            'timing': self.timing_score(change) * 0.1  # 时机 10%
        }
        return sum(factors.values())
```

#### 1.2 新闻驱动策略 (news_driven_strategy.py)

```python
class NewsDrivenStrategy:
    """新闻驱动策略"""
    
    def __init__(self):
        self.news_fetcher = NewsFetcher()
        self.sentiment_analyzer = SentimentAnalyzer()
        
    def analyze_opportunity(self, market: Market) -> Optional[Signal]:
        """分析新闻-价格背离机会"""
        # 1. 获取相关新闻
        news = self.news_fetcher.fetch_news(
            keywords=self.extract_keywords(market),
            hours=2
        )
        
        if not news:
            return None
        
        # 2. 分析新闻情绪
        sentiment = self.sentiment_analyzer.analyze(news)
        
        # 3. 获取当前价格
        current_price = market.current_price
        
        # 4. 计算背离
        divergence = self.calculate_divergence(sentiment, current_price)
        
        if divergence['score'] > 0.7:  # 高背离
            return Signal(
                type='news_driven',
                market=market,
                sentiment=sentiment,
                divergence=divergence,
                suggested_direction=divergence['direction'],
                confidence=divergence['score']
            )
        return None
    
    def calculate_divergence(self, sentiment: Sentiment, price: float) -> Dict:
        """计算情绪-价格背离"""
        # 正面新闻 + 低价格 = 做多机会
        if sentiment.score > 0.6 and price < 0.4:
            return {'score': 0.8, 'direction': 'YES'}
        
        # 负面新闻 + 高价格 = 做空机会
        if sentiment.score < -0.6 and price > 0.6:
            return {'score': 0.8, 'direction': 'NO'}
        
        return {'score': 0, 'direction': None}
```

#### 1.3 鲸鱼行为预测 (whale_behavior_predict.py)

```python
class WhaleBehaviorPredictor:
    """鲸鱼行为预测器"""
    
    def __init__(self):
        self.db = Database()
        self.model = self.load_model()
    
    def predict_next_action(self, whale: Whale) -> Prediction:
        """预测鲸鱼下一步动作"""
        # 1. 获取历史行为序列
        behavior_sequence = self.get_behavior_sequence(whale.wallet, days=7)
        
        # 2. 提取特征
        features = self.extract_features(behavior_sequence)
        
        # 3. 预测
        prediction = self.model.predict(features)
        
        return Prediction(
            whale=whale,
            predicted_action=prediction['action'],
            confidence=prediction['confidence'],
            expected_time=prediction['time'],
            reasoning=prediction['reasoning']
        )
    
    def extract_features(self, sequence: List[Action]) -> Features:
        """提取行为特征"""
        return {
            'frequency_trend': self.calc_frequency_trend(sequence),  # 频率趋势
            'concentration_change': self.calc_concentration_change(sequence),  # 集中度变化
            'direction_consistency': self.calc_direction_consistency(sequence),  # 方向一致性
            'market_rotation': self.detect_market_rotation(sequence),  # 市场轮动
        }
```

#### 1.4 历史数据分析 (historical_analyzer.py)

```python
class HistoricalAnalyzer:
    """历史数据分析器"""
    
    def __init__(self):
        self.db = Database()
    
    def analyze_opportunity_windows(self) -> Report:
        """分析机会窗口"""
        # 1. 时间窗口分析
        time_analysis = self.analyze_by_time()
        
        # 2. 市场特征分析
        market_analysis = self.analyze_by_market()
        
        # 3. 鲸鱼行为分析
        whale_analysis = self.analyze_by_whale()
        
        return Report(
            best_time_windows=time_analysis,
            best_market_types=market_analysis,
            whale_patterns=whale_analysis
        )
    
    def evaluate_signal_performance(self, signal_type: str) -> Metrics:
        """评估信号效果"""
        query = """
            SELECT 
                AVG(CASE WHEN result_pnl > 0 THEN 1 ELSE 0 END) as win_rate,
                AVG(result_pnl) as avg_pnl,
                AVG(result_pnl / result_risk) as sharpe
            FROM signals
            WHERE type = ? AND created_at > datetime('now', '-30 days')
        """
        return self.db.query(query, (signal_type,))
```

### 2. 数据库表结构

```sql
-- 信号记录表
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,  -- 'pair_cost', 'whale_following', 'news_driven', etc.
    wallet TEXT,
    market TEXT,
    direction TEXT,  -- 'YES', 'NO'
    confidence REAL,
    suggested_position REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    result_pnl REAL,  -- 实际盈亏（追踪后填写）
    result_roi REAL,  -- 实际收益率
    status TEXT DEFAULT 'pending'  -- 'pending', 'completed', 'expired'
);

-- 鲸鱼胜率统计表
CREATE TABLE whale_performance (
    wallet TEXT PRIMARY KEY,
    total_trades INTEGER,
    winning_trades INTEGER,
    win_rate REAL,
    avg_pnl REAL,
    sharpe_ratio REAL,
    last_updated TIMESTAMP
);

-- 策略效果评估表
CREATE TABLE strategy_performance (
    strategy_type TEXT PRIMARY KEY,
    total_signals INTEGER,
    winning_signals INTEGER,
    win_rate REAL,
    avg_return REAL,
    sharpe_ratio REAL,
    best_time_window TEXT,
    updated_at TIMESTAMP
);

-- 历史机会记录表
CREATE TABLE opportunity_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT,
    opportunity_type TEXT,
    detected_at TIMESTAMP,
    profit_potential REAL,
    actual_result REAL,
    holding_period INTEGER  -- 持有时间（小时）
);
```

### 3. API设计

```python
# 新增API端点

@app.route('/api/strategies/signals', methods=['GET'])
def get_signals():
    """获取当前信号"""
    pass

@app.route('/api/strategies/performance', methods=['GET'])
def get_strategy_performance():
    """获取策略效果"""
    pass

@app.route('/api/analysis/historical', methods=['GET'])
def get_historical_analysis():
    """获取历史分析"""
    pass

@app.route('/api/whales/smart-money', methods=['GET'])
def get_smart_money_whales():
    """获取聪明钱    """获取聪明钱鲸鱼"""
    pass

@app.route('/api/predictions/whale-actions', methods=['GET'])
def get_whale_predictions():
    """获取鲸鱼行为预测"""
    pass
```

### 4. 配置参数

```python
# config.py

# 扫描配置
SCAN_INTERVAL = 600  # 10分钟
PAIR_COST_THRESHOLD = 0.995
MIN_LIQUIDITY = 100

# 策略配置
STRATEGIES = {
    'whale_following': {
        'enabled': True,
        'min_trade_size': 10000,
        'min_win_rate': 0.6,
        'confidence_threshold': 0.7
    },
    'news_driven': {
        'enabled': True,
        'news_hours': 2,
        'sentiment_threshold': 0.6,
        'divergence_threshold': 0.7
    },
    'behavior_predict': {
        'enabled': True,
        'prediction_hours': 48,
        'confidence_threshold': 0.65
    }
}

# 通知配置
NOTIFICATION_CONFIG = {
    'min_confidence': 0.7,  # 只通知高置信度信号
    'cooldown_minutes': 30,  # 同一鲸鱼冷却时间
    'max_signals_per_hour': 5  # 每小时最大通知数
}
```

### 5. 部署计划

#### 5.1 文件变更

```
新增文件:
- 06-tools/analysis/whale_following.py
- 06-tools/analysis/news_driven_strategy.py
- 06-tools/analysis/whale_behavior_predict.py
- 06-tools/analysis/historical_analyzer.py
- 06-tools/monitoring/strategy_evaluator.py
- dashboard/backend/app/api/strategy.py

修改文件:
- 06-tools/analysis/pair_cost_scanner.py (调整阈值)
- 06-tools/monitoring/polymarket_monitor_v2.py (调整频率)
- dashboard/backend/app/models/database.py (新增表)
```

#### 5.2 实施步骤

```bash
# Step 1: 数据库迁移
python3 dashboard/backend/migrate_db.py

# Step 2: 部署新模块
# 复制新文件到对应目录

# Step 3: 更新定时任务
crontab -e
# 修改: */5 * * * * -> */10 * * * *

# Step 4: 重启服务
sudo systemctl restart polymarket-monitor
sudo systemctl restart polymarket-dashboard

# Step 5: 验证
python3 06-tools/monitoring/strategy_evaluator.py --test
```

### 6. 监控指标

```python
# 需要监控的关键指标

METRICS = {
    # 系统健康
    'scan_success_rate': '扫描成功率',
    'api_response_time': 'API响应时间',
    'error_rate': '错误率',
    
    # 策略效果
    'signals_per_day': '每日信号数',
    'signal_win_rate': '信号胜率',
    'avg_signal_return': '平均信号收益',
    'sharpe_ratio': '夏普比率',
    
    # 数据质量
    'data_completeness': '数据完整度',
    'whale_coverage': '鲸鱼覆盖率',
    'news_latency': '新闻延迟'
}
```

---

## 附录

### A. 依赖库

```
# requirements.txt 新增
scikit-learn>=1.3.0      # 机器学习
pandas>=2.0.0            # 数据分析
numpy>=1.24.0            # 数值计算
scipy>=1.11.0            # 科学计算
textblob>=0.17.1         # 情绪分析
```

### B. 测试计划

```python
# 单元测试
test_whales_following.py
test_news_driven.py
test_behavior_predict.py
test_historical_analyzer.py

# 集成测试
test_strategy_pipeline.py
test_end_to_end.py

# 性能测试
test_scan_performance.py
test_api_load.py
```

### C. 回滚方案

```bash
# 如果出现问题，快速回滚
1. 恢复配置文件
   git checkout config.py

2. 恢复定时任务
   crontab cron_backup.txt

3. 重启服务
   sudo systemctl restart polymarket-monitor
```

---

*技术文档版本: v1.0*
*最后更新: 2026-03-16*
*作者: 虾头 🦐*
