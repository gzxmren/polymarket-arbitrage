# Polymarket 监控系统 V2 完整设计文档

## 版本信息
- **版本**: V2.0
- **日期**: 2026-03-16
- **状态**: 设计完成，待实施
- **整合内容**: 功能设计 + 技术实现 + 知识文档洞察

---

## 一、当前问题诊断

### 1.1 核心问题
| 问题 | 现状 | 影响 |
|------|------|------|
| 套利机会稀少 | 24小时0次 | 系统价值低 |
| 策略过于基础 | 仅Pair Cost + 跨平台 | 市场已有效 |
| 数据未充分利用 | 大量历史数据沉睡 | 浪费资源 |
| 鲸鱼跟踪浅层 | 仅记录持仓 | 未挖掘行为模式 |
| 信号效果未知 | 无胜率追踪 | 无法评估 |

### 1.2 数据分析结论
- 市场效率极高，简单套利几乎不存在
- 鲸鱼行为蕴含预测信息
- 新闻事件驱动价格波动
- 历史数据存在可挖掘的模式
- **关键发现**: 24小时扫描59次，0次套利机会，100次警报中67次是"新鲸鱼"

---

## 二、V2 功能设计（整合版）

### 2.1 短期优化（本周实施）

#### A. 阈值与频率调整
```python
# 原设置
PAIR_COST_THRESHOLD = 0.99
MIN_LIQUIDITY = 500
SCAN_INTERVAL = 5分钟

# 新设置（优化后）
PAIR_COST_THRESHOLD = 0.995      # 放宽，增加机会发现
MIN_LIQUIDITY = 100              # 降低，小市场也有价值
SCAN_INTERVAL = 10分钟           # 降低频率，减少API调用，专注质量
```

#### B. 新增三大策略模块

**策略1: 鲸鱼跟随策略 (Whale Following)**
```
核心逻辑:
1. 识别"聪明钱"鲸鱼（历史胜率>60%）
2. 实时跟踪其大额调仓（>$10k）
3. 当其进入新市场时，评估跟随价值
4. 生成"跟单建议"

触发条件:
- 重点鲸鱼调仓 >$10k
- 调仓方向与当前持仓一致（加仓）
- 该鲸鱼历史胜率 >60%

置信度计算:
- 历史胜率: 40%
- 行为一致性: 30%
- 市场规模: 20%
- 时机: 10%

输出示例:
🐋 聪明钱信号
鲸鱼: Cheerful-Ephemera (胜率68%)
操作: 加仓 $50k "Iran regime fall" - YES
建议: 考虑跟随，目标仓位 $1k-5k
风险: 高波动，建议分批次
```

**策略2: 新闻驱动策略 (News Driven)**
```
核心逻辑:
1. 抓取与持仓相关的新闻（BBC RSS已接入）
2. 分析新闻情绪（正面/负面）
3. 对比市场价格与新闻情绪
4. 发现"情绪-价格背离"机会

触发条件:
- 重大新闻发布（2小时内）
- 新闻情绪与市场价格方向相反
- 鲸鱼持仓与新闻方向一致

背离计算:
- 正面新闻 + 低价格(<40%) = 做多机会
- 负面新闻 + 高价格(>60%) = 做空机会

输出示例:
📰 新闻套利信号
市场: "Iran strike Israel"
新闻: BBC报道伊朗愿意谈判 (正面)
当前价格: 35% (看空)
背离度: 高
建议: 考虑做多，目标45%
```

**策略3: 鲸鱼行为预测 (Whale Behavior Prediction)**
```
核心逻辑:
1. 分析鲸鱼历史行为模式
2. 识别"收敛前兆"（调仓频率增加）
3. 预测鲸鱼下一步动作
4. 提前布局

模式识别特征:
- 调仓频率突然增加
- 集中度从分散→集中
- 连续多次同方向调仓
- 市场轮动行为

输出示例:
🔮 鲸鱼行为预测
鲸鱼: Stupendous-Eddy
当前状态: 高度活跃，连续3次加仓Iran
预测: 48小时内可能大幅调仓
建议: 提前观察，准备跟随
```

#### C. 实时分析 + LLM深度分析（混合架构）

参考现有成功的"实时分析+深度分析"模式：

```
实时策略信号（程序代码）
├─ 每10分钟扫描
├─ 快速检测信号
├─ 实时评分
└─ 显示在Dashboard

深度策略分析（LLM）
├─ 用户点击信号后触发
├─ LLM分析信号质量
├─ 生成自然语言建议
├─ 24小时缓存
└─ 显示在独立卡片
```

### 2.2 中期改进（2周内实施）

#### D. 历史数据分析模块（核心）

**功能1: 机会窗口分析**
```
目标: 找出历史上真正的套利机会

分析维度:
1. 时间窗口
   - 哪些时段机会最多？
   - 事件前后多久有套利空间？

2. 市场特征
   - 哪些类型市场容易套利？
   - 流动性 vs 机会的关系

3. 鲸鱼行为
   - 鲸鱼调仓前是否有规律？
   - 鲸鱼集群行为是否可预测？

输出示例:
📊 历史机会分析报告
- 最佳交易时段: 08:00-10:00 UTC
- 高机会市场类型: 政治事件 > 体育 > 加密
- 鲸鱼领先指标: 调仓频率突增后24h内有60%概率大动作
```

**功能2: 胜率分析（关键）**
```
目标: 评估哪些信号真正有效

追踪指标:
- 每个信号发出后的价格变化
- 信号胜率（盈利次数/总次数）
- 信号夏普比率
- 最佳持有时间
- 误报率 / 漏报率

输出示例:
📈 信号效果评估
- Pair Cost信号胜率: 45%（不佳，考虑降低权重）
- 鲸鱼跟随信号胜率: 62%（良好，重点使用）
- 新闻背离信号胜率: 71%（优秀，重点使用）
- 建议: 重点使用新闻背离策略，暂停Pair Cost
```

**功能3: 鲸鱼画像分析**
```
目标: 识别真正的"聪明钱"

分析维度:
- 胜率排名（盈利次数/总交易）
- 夏普比率（风险调整后收益）
- 预测准确度（提前布局成功率）
- 行为稳定性（策略是否一致）

输出示例:
🐋 鲸鱼排行榜（按智能程度）
1. Cheerful-Ephemera - 胜率71%, 夏普1.8 ⭐⭐⭐
2. Stupendous-Eddy - 胜率68%, 夏普1.5 ⭐⭐⭐
3. ...

⚠️ 反向指标（避免跟随）:
- Gullible-Alteratio - 胜率38%, 跟随需谨慎
```

### 2.3 质量评估体系（新增，来自QUALITY_REVIEW.md）

#### E. 策略质量监控模块

```python
class StrategyQualityMonitor:
    """策略质量监控器"""
    
    def __init__(self):
        self.metrics = {
            'signals_per_day': 0,      # 每日信号数（目标: >5）
            'signal_win_rate': 0,      # 信号胜率（目标: >60%）
            'avg_return': 0,           # 平均收益
            'false_positive_rate': 0,  # 误报率（目标: <20%）
            'missed_opportunities': 0, # 漏报次数
        }
    
    def weekly_review(self) -> Report:
        """每周质量Review"""
        return {
            '发现能力': self.analyze_discovery(),
            '准确性': self.analyze_accuracy(),
            '稳定性': self.analyze_stability(),
            '改进建议': self.generate_recommendations()
        }
    
    def alert_if_quality_drop(self):
        """质量下降预警"""
        if self.metrics['signal_win_rate'] < 0.5:
            send_alert("🔴 红灯：策略胜率低于50%，需立即检查")
        elif self.metrics['signals_per_day'] == 0:
            send_alert("🔴 红灯：连续3天无信号，策略可能失效")
        elif self.metrics['false_positive_rate'] > 0.3:
            send_alert("🟡 黄灯：误报率过高，建议调整阈值")
```

#### F. 信号可追踪性（关键）

每个信号必须包含：
```python
class Signal:
    def __init__(self):
        # 基础信息
        self.created_at = datetime.now()
        self.type = 'whale_following'  # 信号类型
        self.confidence = 0.75         # 置信度
        
        # 预期结果（生成时填写）
        self.expected_direction = 'YES'
        self.expected_price_change = 0.1  # 预期10%变化
        self.suggested_holding_period = 48  # 建议持有48小时
        
        # 实际结果（追踪后填写）
        self.actual_result = None      # 实际盈亏
        self.actual_price_change = None
        self.closed_at = None
        self.status = 'pending'        # pending/completed/expired
    
    def track_result(self):
        """自动追踪信号结果"""
        # 48小时后检查价格变化
        # 记录实际盈亏
        # 更新胜率统计
        pass
```

#### G. 自动化运维报告

每周自动生成并发送到Telegram：

```
📊 第X周策略质量报告

一、发现能力
- 信号总数: XX个
- 日均信号: X.X个
- 目标达成率: XX% (目标: >5个/天)
- 🟢/🟡/🔴 状态

二、准确性
- 信号胜率: XX% (目标: >60%)
- 平均收益: $XXX
- 夏普比率: X.X
- 误报率: XX% (目标: <20%)

三、策略效果对比
- 鲸鱼跟随: XX%胜率, $XXX收益 ⭐⭐⭐
- 新闻驱动: XX%胜率, $XXX收益 ⭐⭐
- 行为预测: XX%胜率, $XXX收益 ⭐

四、预警与建议
- 🟡 信号频率低于目标，建议放宽阈值
- 🟢 新闻驱动策略表现优秀，可增加权重
- 🔴 Pair Cost策略胜率过低，建议暂停

五、下周行动计划
1. 调整Pair Cost阈值到0.997
2. 增加Twitter新闻源
3. 暂停低胜率策略
```

### 2.4 长期重构（放入TODO）

#### H. 预测分析系统（TODO）
- 机器学习价格预测模型
- 情绪分析自动交易
- 事件影响量化评估
- 组合优化算法

---

## 三、技术架构（整合版）

### 3.1 模块划分

```
06-tools/
├── analysis/
│   ├── pair_cost_scanner.py      # 现有（优化阈值）
│   ├── whale_tracker_v2.py       # 现有（增强行为分析）
│   ├── whale_following.py        # 新增（鲸鱼跟随策略）
│   ├── news_driven_strategy.py   # 新增（新闻驱动策略）
│   ├── whale_behavior_predict.py # 新增（行为预测）
│   ├── historical_analyzer.py    # 新增（历史数据分析）
│   └── strategy_quality_monitor.py # 新增（质量监控）
├── monitoring/
│   ├── polymarket_monitor_v2.py  # 现有（调整频率10分钟）
│   ├── strategy_evaluator.py     # 新增（策略效果评估）
│   └── quality_reporter.py       # 新增（质量报告生成）
└── dashboard/
    ├── api/strategy.py           # 新增（策略API）
    └── api/quality.py            # 新增（质量指标API）
```

### 3.2 数据流（优化后）

```
[监控程序] 每10分钟
    ↓
[数据收集] 市场数据 + 鲸鱼数据 + 新闻数据
    ↓
[策略引擎] 多策略并行分析
    ├─ Pair Cost扫描（降低权重）
    ├─ 鲸鱼跟随策略（重点）
    ├─ 新闻驱动策略（重点）
    └─ 行为预测策略
    ↓
[信号评估] 实时评分 + 质量检查
    ↓
[LLM深度分析] 用户触发（可选）
    ↓
[通知系统] Telegram + Dashboard
    ↓
[数据存储] SQLite
    ↓
[效果追踪] 记录信号结果 → 胜率统计
    ↓
[质量监控] 自动评估 → 每周报告
```

### 3.3 数据库表结构（新增）

```sql
-- 信号记录表（核心）
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,  -- 'pair_cost', 'whale_following', 'news_driven'
    wallet TEXT,
    market TEXT,
    direction TEXT,  -- 'YES', 'NO'
    confidence REAL,
    suggested_position REAL,
    
    -- 预期结果
    expected_price_change REAL,
    suggested_holding_hours INTEGER,
    
    -- 实际结果（追踪后填写）
    actual_pnl REAL,
    actual_roi REAL,
    closed_at TIMESTAMP,
    status TEXT DEFAULT 'pending',
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 鲸鱼胜率统计表
CREATE TABLE whale_performance (
    wallet TEXT PRIMARY KEY,
    pseudonym TEXT,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    avg_pnl REAL DEFAULT 0,
    sharpe_ratio REAL DEFAULT 0,
    strategy_consistency REAL DEFAULT 0,  -- 策略一致性评分
    last_updated TIMESTAMP
);

-- 策略效果评估表
CREATE TABLE strategy_performance (
    strategy_type TEXT PRIMARY KEY,
    total_signals INTEGER DEFAULT 0,
    winning_signals INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    avg_return REAL DEFAULT 0,
    sharpe_ratio REAL DEFAULT 0,
    false_positive_rate REAL DEFAULT 0,
    best_time_window TEXT,
    updated_at TIMESTAMP
);

-- 质量监控日志
CREATE TABLE quality_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT,
    metric_value REAL,
    alert_level TEXT,  -- 'green', 'yellow', 'red'
    note TEXT,
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.4 API设计（新增）

```python
# 策略相关API
@app.route('/api/strategies/signals', methods=['GET'])
def get_signals():
    """获取当前信号"""
    pass

@app.route('/api/strategies/performance', methods=['GET'])
def get_strategy_performance():
    """获取策略效果"""
    pass

@app.route('/api/strategies/historical', methods=['GET'])
def get_historical_analysis():
    """获取历史分析"""
    pass

# 质量监控API
@app.route('/api/quality/metrics', methods=['GET'])
def get_quality_metrics():
    """获取质量指标"""
    pass

@app.route('/api/quality/weekly-report', methods=['GET'])
def get_weekly_report():
    """获取每周报告"""
    pass

# 鲸鱼智能排名API
@app.route('/api/whales/smart-money', methods=['GET'])
def get_smart_money_whales():
    """获取聪明钱鲸鱼"""
    pass

@app.route('/api/predictions/whale-actions', methods=['GET'])
def get_whale_predictions():
    """获取鲸鱼行为预测"""
    pass
```

### 3.5 Dashboard新增页面

```
Dashboard导航
├─ 首页（现有）
├─ 鲸鱼列表（现有）
├─ 策略信号（新增）
│   ├─ 实时信号流
│   ├─ 信号详情（含LLM分析）
│   └─ 信号历史
├─ 策略效果（新增）
│   ├─ 胜率统计
│   ├─ 收益曲线
│   ├─ 策略对比
│   └─ 质量报告
├─ 智能排名（新增）
│   ├─ 聪明钱鲸鱼
│   ├─ 反向指标
│   └─ 鲸鱼画像
└─ 设置（现有）
```

---

## 四、实施计划（整合版）

### Week 1: 短期优化 + 基础策略
- [x] 调整阈值（0.99→0.995，流动性500→100）
- [x] 修改扫描频率（5分钟→10分钟）
- [ ] 实现鲸鱼跟随策略
- [ ] 实现新闻驱动策略（复用现有新闻模块）
- [ ] 创建信号记录表
- [ ] 基础信号效果追踪

### Week 2: 质量监控 + 历史分析
- [ ] 实现鲸鱼行为预测
- [ ] 实现历史数据分析模块
- [ ] 实现胜率追踪系统
- [ ] 实现鲸鱼画像分析
- [ ] 实现质量监控模块
- [ ] 实现每周自动报告

### Week 3: Dashboard集成
- [ ] 策略信号页面
- [ ] 策略效果仪表板
- [ ] 智能排名页面
- [ ] 质量报告页面
- [ ] 前后端API联调

### Week 4: 优化迭代
- [ ] 根据数据反馈调整策略参数
- [ ] 优化信号质量
- [ ] 完善LLM深度分析
- [ ] 性能优化

### Month 2+: 长期重构（TODO）
- [ ] 机器学习模型
- [ ] 自动化交易（待定）

---

## 五、预期效果（整合版）

| 指标 | 当前 | 目标 | 提升 |
|------|------|------|------|
| 信号频率 | 0次/天 | 5-10次/天 | +∞ |
| 信号胜率 | N/A | >60% | 新指标 |
| 策略类型 | 2个 | 5个 | +150% |
| 质量监控 | 无 | 自动周报 | 质变 |
| 数据利用率 | <10% | >80% | +700% |
| 鲸鱼利用率 | 记录 | 预测 | 质的飞跃 |

---

## 六、风险评估与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 策略失效 | 中 | 高 | 多策略并行，持续评估，快速迭代 |
| API限制 | 低 | 中 | 降低频率，缓存优化，降级方案 |
| 数据不足 | 中 | 中 | 2周数据后开始分析，逐步调优 |
| 过度拟合 | 中 | 高 | 交叉验证，滚动测试，正则化 |
| 质量监控失效 | 低 | 高 | 多指标监控，人工Review兜底 |

---

## 七、关键成功因素

1. **质量监控是核心**: 必须建立完整的胜率追踪体系
2. **快速迭代**: 每周根据数据反馈调整策略
3. **数据驱动**: 所有决策基于历史数据分析
4. **风险控制**: 设置红黄绿灯预警，及时止损
5. **用户反馈**: 根据实际使用效果持续优化

---

## 八、文档清单

| 文档 | 位置 | 状态 |
|------|------|------|
| 功能设计文档 | `docs/feature-redesign-v2.md` | ✅ 完成 |
| 技术实现文档 | `docs/technical-implementation-v2.md` | ✅ 完成 |
| 整合设计文档 | `docs/V2-design-integrated.md` | ✅ 完成（本文件） |
| 质量评估体系 | `QUALITY_REVIEW.md` | ✅ 已有 |
| 运维计划 | `MAINTENANCE.md` | ✅ 已有 |
| 待办清单 | `TODO.md` | ✅ 已有 |

---

*文档版本: v2.0-integrated*
*最后更新: 2026-03-16*
*整合者: 虾头 🦐*
*状态: 待实施*
