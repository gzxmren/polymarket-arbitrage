# 新闻API配置指南

## 已接入的新闻源

### ✅ RSS源（无需API Key，已可用）

| 源 | 状态 | 说明 |
|-----|------|------|
| **BBC** | ✅ 可用 | 抓取成功，返回真实新闻 |
| **Reuters** | ⚠️ 不稳定 | 偶尔连接超时 |
| **CNN** | ⚠️ 不稳定 | 偶尔连接中断 |
| **WSJ** | ✅ 可用 | 需要订阅，目前返回空 |

### 🔧 API源（需要配置）

| 源 | 优先级 | 获取方式 | 说明 |
|-----|--------|---------|------|
| **Twitter API** | 高 | developer.twitter.com | 实时性强，需要Bearer Token |
| **NewsAPI** | 中 | newsapi.org | 免费版足够，需要API Key |

---

## 配置步骤

### 1. Twitter API (推荐)

**获取方式**:
1. 访问 https://developer.twitter.com/
2. 注册开发者账号
3. 创建项目 → 创建应用
4. 获取 **Bearer Token**

**配置**:
```bash
# 添加到 ~/.bashrc 或 ~/.zshrc
export TWITTER_BEARER_TOKEN="your_bearer_token_here"

# 立即生效
source ~/.bashrc
```

### 2. NewsAPI (简单)

**获取方式**:
1. 访问 https://newsapi.org/
2. 注册免费账号
3. 获取 **API Key**

**配置**:
```bash
# 添加到 ~/.bashrc 或 ~/.zshrc
export NEWSAPI_KEY="your_api_key_here"

# 立即生效
source ~/.bashrc
```

---

## 当前状态

```
✅ BBC RSS: 正常工作，返回真实新闻
⚠️ Reuters RSS: 偶尔超时
⚠️ CNN RSS: 偶尔连接中断
⏳ Twitter API: 等待配置
⏳ NewsAPI: 等待配置
```

---

## 测试命令

```bash
cd ~/.openclaw/workspace/polymarket-project/06-tools/analysis

# 测试新闻抓取
/usr/bin/python3 news_fetcher.py

# 测试鲸鱼新闻关联
/usr/bin/python3 whale_news_connector.py
```

---

## 下一步建议

1. **优先配置 Twitter API** - 实时性最强，对Polymarket最有用
2. **可选配置 NewsAPI** - 作为RSS的补充
3. **监控RSS稳定性** - Reuters和CNN偶尔不稳定，需要重试机制
