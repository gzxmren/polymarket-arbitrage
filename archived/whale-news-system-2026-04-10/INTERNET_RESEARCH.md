# 互联网调研报告 - 鲸鱼新闻关联系统替代方案

**调研时间**: 2026-04-10
**调研范围**: GitHub, Reddit, X

---

## 📊 现有开源项目分析

### 1. dylanpersonguy/Fully-Autonomous-Polymarket-AI-Trading-Bot (40 stars) ⭐推荐

**特点**: 
- 使用**AI研究代理**做新闻分析，不是简单的RSS
- Web Search via SERPAPI/Bing/Tavily（不是RSS）
- 获取**完整页面内容**（不只是摘要）
- 权威性评分 + 来源过滤
- 2小时缓存避免重复请求

**关键源码**:
```
src/research/
├── source_fetcher.py   # 获取完整页面内容
├── evidence_extractor.py # 提取证据
├── query_builder.py   # 构建搜索查询
src/connectors/
├── web_search.py      # SERPAPI/Bing/Tavily
```

**API成本**:
- SERPAPI: $50/1000次搜索
- Bing: 付费API
- Tavily: 有免费额度

### 2. punkde99/polymarket-whale-bot (51 stars)

**特点**:
- 只做鲸鱼跟踪，不涉及新闻
- 使用Polygon WebSocket实时监控链上交易
- 鲸鱼评分系统（基于90天胜率）
- 有仪表盘可视化

**结论**: 不需要新闻功能，不适合我们的需求

### 3. sarviinageelen/polymarket-sports-analysis (11 stars)

**特点**:
- 专注体育市场分析
- 使用Gamma API + GraphQL subgraphs
- 计算P&L和排行榜
- 没有新闻整合功能

**结论**: 不适合我们的需求

---

## 🔧 解决方案对比

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **现有RSS系统** | 免费 | 链接过期，覆盖不足，时间窗口错位 | ❌ 不推荐 |
| **SERPAPI/Bing** | 真实网页，稳定链接 | 需要API Key，付费 | 优先考虑 |
| **Tavily API** | 专为AI设计，免费额度 | 需要API Key | 备选 |
| **NewsAPI.org** | 稳定链接，新闻丰富 | 付费，链接有时效 | 备选 |
| **LLM直接分析** | 准确度高 | 成本高 | 长期方向 |

---

## 💡 推荐方案

### 方案A: 使用SERPAPI/Bing API（短期修复）

```python
# 参考Fully-Autonomous-Polymarket-AI-Trading-Bot的实现
from src.connectors.web_search import SerpAPIProvider

provider = SerpAPIProvider(api_key=os.environ.get("SERPAPI_KEY"))
results = await provider.search("Real Madrid vs Barcelona 2026-03-22", num_results=10)

# 获取完整页面内容
fetcher = SourceFetcher(provider, config)
sources = await fetcher.fetch_sources(queries)
```

**优点**:
- 真实网页，稳定链接
- 完整内容，不依赖RSS
- 权威性评分

**缺点**:
- 需要配置API Key
- 付费（但成本可控）

### 方案B: 使用Tavily API（免费额度）

```python
from tavily import TavilyClient
client = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY"))
results = client.search(query="Real Madrid match news March 2026")
```

**优点**:
- 免费额度（1000次/月）
- 专为AI研究设计
- 包含摘要和来源

**缺点**:
- 免费额度有限
- 需要注册获取API Key

### 方案C: 完全重构 - LLM直接分析（长期方向）

让LLM直接分析市场标题 + 搜索结果，生成关联报告：
- 不依赖关键词匹配
- 可以理解上下文
- 关联度判断更准确

**成本**: 约$0.5-1/次报告

---

## 📋 下一步行动建议

1. **获取API Key**（优先级：高）
   - 注册 Tavily (免费) 或 SERPAPI ($50试用)

2. **重构新闻抓取模块**（优先级：高）
   - 参考 `Fully-Autonomous-Polymarket-AI-Trading-Bot/src/research/`
   - 使用搜索引擎API替代RSS

3. **修复时间窗口**（优先级：高）
   - 从市场标题提取日期
   - 使用比赛时间找新闻

4. **测试验证**（优先级：中）
   - 验证体育新闻真正有返回
   - 验证关联度>70%

---

## 🔗 相关链接

- Polymarket AI Bot: https://github.com/dylanpersonguy/Fully-Autonomous-Polymarket-AI-Trading-Bot
- Polymarket Whale Bot: https://github.com/punkde99/polymarket-whale-bot
- Polymarket Sports Analysis: https://github.com/sarviinageelen/polymarket-sports-analysis
- Tavily API: https://tavily.com
- SerpAPI: https://serpapi.com

---

**调研人**: 虾头 🦐
**调研日期**: 2026-04-10