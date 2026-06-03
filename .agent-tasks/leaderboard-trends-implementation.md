# 子代理任务: Leaderboard 历史趋势分析完整版实现

## 任务概述
实现完整版的历史排名趋势分析功能，包括数据库表设计、趋势计算、API 接口和前端展示。

## 任务要求

### 1. 数据库设计

创建 `leaderboard_trends` 表：

```sql
CREATE TABLE leaderboard_trends (
    wallet TEXT PRIMARY KEY,
    
    -- 排名趋势
    rank_7d_ago INTEGER,
    rank_30d_ago INTEGER,
    rank_change_7d INTEGER,      -- 排名变化 (+上升, -下降)
    rank_change_30d INTEGER,
    
    -- 盈亏趋势
    pnl_7d_ago REAL,
    pnl_30d_ago REAL,
    pnl_change_7d REAL,
    pnl_change_30d REAL,
    
    -- 成交量趋势
    volume_change_7d REAL,
    volume_change_30d REAL,
    
    -- 综合趋势
    trend_direction TEXT,        -- 'rising', 'falling', 'stable', 'new', 'dropped'
    momentum_score REAL,         -- 0-100 动量评分
    
    updated_at TIMESTAMP
);
```

### 2. 趋势计算逻辑

#### 排名变化计算
```python
def calculate_rank_change(current_rank, past_rank):
    """
    计算排名变化
    正值: 排名上升 (如 #10 → #5, change = +5)
    负值: 排名下降 (如 #5 → #10, change = -5)
    """
    if past_rank is None:
        return None  # 新上榜
    return past_rank - current_rank  # 正值表示上升
```

#### 趋势方向判断
```python
def determine_trend_direction(rank_change_7d, rank_change_30d, pnl_change_7d):
    """
    判断趋势方向
    - 'rising': 排名上升 + 盈利增加
    - 'falling': 排名下降 + 盈利减少
    - 'stable': 排名稳定
    - 'new': 新上榜
    - 'dropped': 掉出榜单
    """
    if rank_change_7d is None:
        return 'new'
    
    if rank_change_7d >= 5 and pnl_change_7d > 0:
        return 'rising'
    elif rank_change_7d <= -5 and pnl_change_7d < 0:
        return 'falling'
    elif abs(rank_change_7d) <= 2:
        return 'stable'
    else:
        return 'mixed'
```

#### 动量评分计算
```python
def calculate_momentum_score(rank_change_7d, rank_change_30d, pnl_change_7d, pnl_change_30d):
    """
    计算动量评分 (0-100)
    综合考虑短期和长期趋势
    """
    score = 50  # 基础分
    
    # 短期排名变化 (7天)
    if rank_change_7d:
        score += min(rank_change_7d * 5, 20)  # 最多 +20
    
    # 长期排名变化 (30天)
    if rank_change_30d:
        score += min(rank_change_30d * 3, 15)  # 最多 +15
    
    # 盈亏变化
    if pnl_change_7d and pnl_change_7d > 0:
        score += min(pnl_change_7d / 10000, 10)  # 最多 +10
    
    return min(100, max(0, score))
```

### 3. 同步时自动计算趋势

修改 `leaderboard_whale_tracker.py`，在同步时自动计算趋势：

```python
def calculate_trends(self, wallet: str, current_entry: LeaderboardEntry):
    """计算单个鲸鱼的趋势"""
    cursor = self.conn.cursor()
    
    # 获取7天前的数据
    cursor.execute('''
        SELECT rank, pnl, volume
        FROM leaderboard_history
        WHERE wallet = ?
          AND recorded_at > datetime('now', '-7 days')
        ORDER BY recorded_at ASC
        LIMIT 1
    ''', (wallet,))
    row_7d = cursor.fetchone()
    
    # 获取30天前的数据
    cursor.execute('''
        SELECT rank, pnl, volume
        FROM leaderboard_history
        WHERE wallet = ?
          AND recorded_at > datetime('now', '-30 days')
        ORDER BY recorded_at ASC
        LIMIT 1
    ''', (wallet,))
    row_30d = cursor.fetchone()
    
    # 计算变化
    rank_change_7d = row_7d[0] - current_entry.rank if row_7d else None
    rank_change_30d = row_30d[0] - current_entry.rank if row_30d else None
    
    pnl_change_7d = current_entry.pnl - row_7d[1] if row_7d else None
    pnl_change_30d = current_entry.pnl - row_30d[1] if row_30d else None
    
    volume_change_7d = current_entry.volume - row_7d[2] if row_7d else None
    volume_change_30d = current_entry.volume - row_30d[2] if row_30d else None
    
    # 判断趋势
    trend_direction = self.determine_trend_direction(
        rank_change_7d, rank_change_30d, pnl_change_7d
    )
    
    # 计算动量
    momentum = self.calculate_momentum_score(
        rank_change_7d, rank_change_30d, pnl_change_7d, pnl_change_30d
    )
    
    # 保存到 trends 表
    cursor.execute('''
        INSERT OR REPLACE INTO leaderboard_trends
        (wallet, rank_7d_ago, rank_30d_ago, rank_change_7d, rank_change_30d,
         pnl_7d_ago, pnl_30d_ago, pnl_change_7d, pnl_change_30d,
         volume_change_7d, volume_change_30d, trend_direction, momentum_score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (wallet, ...))
```

### 4. Dashboard API 接口

在 `whales.py` 中添加趋势相关 API：

```python
@whales_bp.route('/leaderboard/trends', methods=['GET'])
def get_leaderboard_trends():
    """获取鲸鱼趋势数据"""
    # 查询参数
    trend_type = request.args.get('trend', None)  # 'rising', 'falling', 'stable', 'new'
    min_momentum = request.args.get('min_momentum', 0, type=int)
    
    # 查询趋势数据
    query = '''
        SELECT 
            t.*,
            w.username, w.rank, w.pnl, w.volume
        FROM leaderboard_trends t
        JOIN leaderboard_whales w ON t.wallet = w.wallet
        WHERE 1=1
    '''
    
    if trend_type:
        query += f" AND t.trend_direction = '{trend_type}'"
    
    if min_momentum:
        query += f" AND t.momentum_score >= {min_momentum}"
    
    query += " ORDER BY t.momentum_score DESC"
    
    # 返回数据
    ...

@whales_bp.route('/leaderboard/trends/<wallet>', methods=['GET'])
def get_whale_trend_detail(wallet):
    """获取单个鲸鱼的趋势详情"""
    # 返回历史排名变化、盈亏趋势图表数据
    ...
```

### 5. 前端趋势展示组件

创建 `LeaderboardTrends.tsx`：

```typescript
// 趋势筛选
const trendFilters = [
  { key: 'rising', label: '🔥 快速上升', color: 'red' },
  { key: 'stable', label: '➡️ 保持稳定', color: 'blue' },
  { key: 'falling', label: '⬇️ 排名下降', color: 'orange' },
  { key: 'new', label: '✨ 新上榜', color: 'green' },
];

// 趋势卡片
<TrendCard
  title="快速上升的鲸鱼"
  icon={<FireOutlined />}
  data={risingWhales}
  columns={[
    { title: '用户名', dataIndex: 'username' },
    { title: '排名变化', render: (t) => `+${t.rank_change_7d}` },
    { title: '动量评分', render: (t) => <Progress percent={t.momentum_score} /> },
  ]}
/>

// 趋势图表
<RankTrendChart
  wallet={selectedWallet}
  data={historicalRanks}
/>
```

### 6. Telegram 告警增强

添加趋势变化告警：

```python
def send_trend_alert(wallet, old_trend, new_trend):
    """发送趋势变化告警"""
    if old_trend != new_trend:
        message = f"""
🚨 *鲸鱼趋势变化*

👤 {wallet}
📊 趋势: {old_trend} → {new_trend}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
        send_telegram_message(message)
```

## 测试要求

1. **单元测试**: 测试趋势计算函数
2. **集成测试**: 测试完整同步流程
3. **数据验证**: 验证历史数据准确性
4. **边界测试**: 测试新上榜、掉榜等边界情况

## 输出文件

1. `06-tools/analysis/leaderboard_trends.py` - 趋势计算模块
2. `dashboard/backend/app/api/whales.py` - 添加趋势 API (修改)
3. `dashboard/frontend/src/pages/LeaderboardTrends.tsx` - 趋势展示页面
4. `06-tools/analysis/test_leaderboard_trends.py` - 测试文件

## 注意事项

1. 确保向后兼容 (现有数据不受影响)
2. 性能优化 (趋势计算不应显著增加同步时间)
3. 错误处理 (处理缺失历史数据的情况)
4. 文档注释 (所有函数添加详细注释)

## 验收标准

- [ ] 数据库表创建成功
- [ ] 趋势计算逻辑正确
- [ ] API 接口返回正确数据
- [ ] 前端页面正常显示
- [ ] Telegram 告警正常发送
- [ ] 所有测试通过
- [ ] 代码审查通过
