# 鲸鱼数据质量优化计划

## 背景
经 2026-05-16 数据质量 review，发现鲸鱼追踪系统存在两项可通过代码优化的低质量表现。

## 优化项

### 优化 2: 淘汰机制从 OR 逻辑改为加权评分制

**问题**: `should_keep_whale()` 使用 OR 连接 4 个保留条件：
- 活跃（vol>=1k AND tc>=3）→ 保留
- 大单数>=3 → 保留
- DB 历史 volume>=5k → 保留
- DB 记录数>=3 → 保留

OR 逻辑过于宽松：一个钱包只要满足任意一条就永久保留。纯靠 3 笔 $500 大单混进来的噪音永远不会被淘汰。

**方案**: 替换为加权评分制
- 活跃 24h 交易量>=1k 且交易次数>=3 → +3 分
- 大单(>=500) 笔数>=3 → +2 分
- DB 历史交易量>=5k → +3 分
- DB 记录数>=3 → +1 分
- 连续 30 天无交易 → -2 分（仅对 lifetime volume < $50k 的鲸鱼生效，保护休眠的真鲸鱼）
- 总分 < 3 → 淘汰

**最终验证结果**: 118 → 保留 97，淘汰 21
- 4 个 lifetime $400k+ 的休眠真鲸鱼被正确保留（ElonSpam, shcc 等）
- 21 个淘汰全是 low volume + 无 DB 历史的噪音
- 0 误杀

### 优化 3: 新发现鲸鱼自动写入 DB

**问题**: 9 个鲸鱼（7.6%）wallet 在 whales 表中没有记录（total_volume=0），完全依赖 API 扫描数据。API 波动可能导致这些钱包丢失。

**方案**: 在 `identify_active_whales()` 发现新鲸鱼后，调用 `ensure_db_record()` 函数向 whales 表执行 `INSERT OR IGNORE`，写入钱包地址和 pseudonym，建立永久身份。

## 修改文件
`polymarket-project/06-tools/analysis/whale_tracker_v2.py`

## 修改范围
1. `should_keep_whale()` — 重写为评分制 + 历史信用保护（hist_vol < 50000 才扣不活跃分）
2. 新增 `ensure_db_record()` — DB 写入函数
3. `main()` 中调用 `ensure_db_record()` — 在识别新鲸鱼后执行
4. 新增配置常量：`INACTIVE_DAYS_THRESHOLD`、`KEEP_SCORE_THRESHOLD`

## 代码变更明细

### 配置区（WHALE_MIN_TOTAL_VALUE 之后）
```python
# 鲸鱼淘汰评分配置
INACTIVE_DAYS_THRESHOLD = 30      # 不活跃天数阈值
KEEP_SCORE_THRESHOLD = 3           # 保留分数阈值
```

### should_keep_whale() — 重写
```python
def should_keep_whale(wallet: str, info: dict) -> bool:
    """判断是否保留鲸鱼（加权评分制）"""
    now = datetime.now(timezone.utc).timestamp()
    score = 0
    
    recent_vol = info.get('total_volume', 0)
    recent_tc = info.get('trade_count', 0)
    large_trades = info.get('large_trades', 0)
    hist_vol = info.get('historical_volume', 0)
    db_changes = info.get('db_changes_count', 0)
    last_trade = info.get('last_trade', 0)
    
    # 1. 活跃交易（vol>=1k AND tc>=3）→ +3
    if recent_vol >= 1000 and recent_tc >= 3:
        score += 3
    # 2. 大单次数（lc>=3）→ +2
    if isinstance(large_trades, (int, float)) and large_trades >= 3:
        score += 2
    # 3. DB历史交易量（hist_vol>=5k）→ +3
    if isinstance(hist_vol, (int, float)) and hist_vol >= 5000:
        score += 3
    # 4. DB记录数（db_changes>=3）→ +1
    if isinstance(db_changes, (int, float)) and db_changes >= 3:
        score += 1
    # 5. 不活跃惩罚（仅对历史不显著的鲸鱼）
    if last_trade > 0 and hist_vol < 50000:
        days_inactive = (now - last_trade) / 86400
        if days_inactive >= INACTIVE_DAYS_THRESHOLD:
            score -= 2
    
    return score >= KEEP_SCORE_THRESHOLD
```

### ensure_db_record() — 新增
```python
def ensure_db_record(wallet: str, pseudonym: str):
    """确保鲸鱼在 whales 表中有记录（INSERT OR IGNORE）"""
    import sqlite3
    DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            INSERT OR IGNORE INTO whales (wallet, pseudonym, added_at, last_updated)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ''', (wallet.lower(), (pseudonym or wallet[:10] + '...')[:50]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"   ⚠️ DB 回写失败 (wallet={wallet[:10]}...): {e}", file=sys.stderr)
```

### main() 中新增调用（identify_active_whales 之后）
```python
    # DB回写：为新发现的鲸鱼建立永久的身份记录
    for wallet, info in new_whales.items():
        if wallet not in historical_whales:
            ensure_db_record(wallet, info.get('pseudonym', ''))
            print(f"   📝 DB回写新鲸鱼: {wallet[:10]}... ({info.get('pseudonym', '')[:20]})")
```

## Out of Scope（禁止事项）
- ❌ 不修改其他文件
- ❌ 不修改 DB 表结构
- ❌ 不添加新依赖
- ❌ 不修改其他函数的行为
- ❌ 不改动报告格式
- ❌ 不改动文件结构或架构

## Review Notes
- 子代理生成代码 → 主 session review 发现 -2 不活跃惩罚误杀4个真鲸鱼 → 修复为 hist_vol < 50000 才扣分
- 最终通过验证：118→97保留，21淘汰，0误杀
