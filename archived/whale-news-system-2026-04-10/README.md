# 鲸鱼持仓新闻关联系统 - 归档说明

**归档时间**: 2026-04-10
**归档原因**: 系统存在严重缺陷，导致无效报告污染群聊

---

## 📋 核心文件清单

| 文件 | 功能 | 状态 |
|------|------|------|
| `whale_news_connector.py` | 新闻关联主逻辑 | ❌ 已停用 |
| `news_fetcher.py` | 新闻抓取模块 | ❌ 已停用 |
| `send_whale_news.py` | Telegram报告发送 | ❌ 已停用 |

---

## ❌ 已识别的三大核心缺陷

### 1. 体育新闻源未真正激活

**问题**: 代码中ESPN/BBC Sport抓取函数有"模拟数据"注释

```python
def fetch_espn_news(self, keywords, hours=24):
    """从ESPN抓取足球新闻
    Note: 这里使用模拟数据  # ⚠️ 注释还在
    """
    mock_news = []  # 返回模拟数据
```

**表现**: 
- 6个体育比赛市场，只有2个找到新闻（67%无数据）
- 关联度都很低（65-68分）

**根因**: RSS源配置了但未真正调用，fallback到模拟数据

---

### 2. 时间窗口错位

**问题**: 新闻关联使用"当前时间"而非"比赛时间"

```python
def generate_whale_news_report(..., trade_time=None):
    if trade_time is None:
        trade_time = datetime.now(timezone.utc)  # ⚠️ 使用当前时间
```

**表现**:
- 报告时间: `2026-04-10 12:00`
- 市场标题: `"Will Real Madrid win on 2026-03-22?"`
- 时间差: 19天！

**根因**: 有 `extract_market_date()` 方法但未调用

**修复方案**:
```python
# 应该从市场标题提取日期
market_date = extract_market_date(market_title)
if market_date:
    # 使用比赛时间作为参考点找新闻
    news_window_start = market_date - timedelta(hours=6)
    news_window_end = market_date + timedelta(hours=6)
```

---

### 3. Google News链接过期

**问题**: Google News RSS返回的是临时token链接，24小时过期

```python
# Google News的链接是临时token链接，会过期
if config.get("is_search") and 'news.google.com' in raw_url:
    # ⚠️ 使用搜索链接代替
    url = f"https://www.google.com/search?q={search_query}&tbm=nws"
```

**表现**:
- 报告链接: `https://www.google.com/search?q=Europa%20League%20...`
- 用户必须手动点击搜索才能看到新闻
- 无法直接查看原始新闻内容

**根因**: Google News RSS机制限制，token链接会过期

---

## 🔍 系统架构回顾

```
鲸鱼持仓数据 (whale_tracker.py)
    ↓
提取市场标题 (whale_news_connector.py)
    ↓
生成搜索关键词 (extract_keywords())
    ↓
抓取新闻 (news_fetcher.py)
    ├─ Google News RSS ✅ (但链接过期)
    ├─ BBC/CNN/WSJ RSS ✅
    ├─ ESPN/BBC Sport RSS ❌ (未真正调用)
    └─ Twitter API ❌ (未配置token)
    ↓
计算关联度 (calculate_relevance())
    ├─ 关键词匹配 40%
    ├─ 时间接近 30%
    ├─ 情绪一致 20%
    └─ 来源权威 10%
    ↓
生成报告 (generate_whale_news_report())
    ↓
发送到Telegram (send_whale_news.py)
```

---

## 💡 未来修复方向

### 方案1: 修复现有系统

1. **真正激活体育新闻源**
   - 验证ESPN Soccer RSS是否可用
   - 添加Sky Sports RSS
   - 测试RSS返回真实数据

2. **修复时间窗口**
   - 调用 `extract_market_date()`
   - 使用比赛时间而非当前时间找新闻

3. **解决链接过期**
   - 保存新闻标题+摘要，而非链接
   - 或使用newsapi.org等付费API获取稳定链接

### 方案2: 使用第三方服务

- **NewsAPI.org** - 付费API，稳定链接
- **GNews API** - 免费额度，体育新闻支持
- **Currents API** - 免费额度，实时新闻

### 方案3: 完全重构

- 使用LLM直接分析市场+新闻
- 例如: Claude/GPT-4 API直接生成关联分析
- 不依赖RSS和复杂的关键词匹配

---

## 📊 影响评估

**停用前**: 每天2次报告（12:00, 18:00）
**报告质量**: 
- 体育市场: 67%无新闻
- 政治市场: 相对较好（但链接过期）
- 总体: 低质量，污染群聊

**停用后**: 
- 群聊清爽，无无效报告
- 可专心修复系统
- 避免"狼来了"效应（用户不再信任报告）

---

## 🔄 重启条件

系统重启需满足:

1. ✅ 体育新闻RSS真正调用并有返回
2. ✅ 时间窗口修复（使用比赛时间）
3. ✅ 链接过期问题有解决方案
4. ✅ 测试报告质量达标（关联度>70%）
5. ✅ 用户确认重启

---

## 📝 相关日志

- 运行日志: `07-data/logs/whale_news.log`
- Cron备份: `07-data/cron_backup_20260410_*.txt`
- 原位置: `06-tools/analysis/`, `06-tools/monitoring/`

---

**归档人**: 虾头 🦐
**归档日期**: 2026-04-10