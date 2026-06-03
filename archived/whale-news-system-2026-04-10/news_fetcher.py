#!/usr/bin/env python3
"""
真实新闻抓取模块
支持: Twitter API, Reuters RSS, BBC RSS, CNN RSS, 华尔街日报 RSS
"""

import json
import re
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from bs4 import BeautifulSoup


class NewsFetcher:
    """新闻抓取器"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # RSS源配置
        # Note: Reuters 返回 HTML 而不是 RSS，已禁用
        self.rss_sources = {
            "reuters": {
                "name": "Reuters",
                "url": "https://www.reutersagency.com/feed/?taxonomy=markets&post_type=reuters-best",
                "enabled": False  # 返回 HTML 而不是 RSS
            },
            "bbc": {
                "name": "BBC",
                "url": "http://feeds.bbci.co.uk/news/world/rss.xml",
                "enabled": True
            },
            "cnn": {
                "name": "CNN",
                "url": "http://rss.cnn.com/rss/edition_world.rss",
                "enabled": True
            },
            "wsj": {
                "name": "Wall Street Journal",
                "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
                "enabled": True
            },
            # 体育新闻源
            "bbc_sport": {
                "name": "BBC Sport",
                "url": "http://feeds.bbci.co.uk/sport/rss.xml",
                "enabled": True
            },
            "espn": {
                "name": "ESPN",
                "url": "https://www.espn.com/espn/rss/news",
                "enabled": True
            },
            "espn_soccer": {
                "name": "ESPN Soccer",
                "url": "https://www.espn.com/soccer/rss",
                "enabled": True
            },
            # Google News 搜索 RSS（动态关键词搜索）
            "google_news": {
                "name": "Google News",
                "url": "https://news.google.com/rss",
                "enabled": True,
                "is_search": True  # 标记为搜索型RSS
            }
        }
    
    def fetch_rss_news(self, source: str, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        从RSS源抓取新闻
        支持Google News搜索
        """
        if source not in self.rss_sources:
            return []
        
        config = self.rss_sources[source]
        if not config["enabled"]:
            return []
        
        try:
            print(f"   抓取 {config['name']} RSS...")
            
            # Google News 搜索需要特殊处理
            if config.get("is_search") and keywords:
                # 构建Google News搜索URL
                import urllib.parse
                query = " OR ".join(keywords[:3])  # 最多3个关键词
                encoded_query = urllib.parse.quote(query)
                url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
            else:
                url = config["url"]
            
            feed = feedparser.parse(url)
            
            news_list = []
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            for entry in feed.entries[:30]:  # 检查最近30条
                # 解析发布时间
                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if published:
                    pub_time = datetime(*published[:6], tzinfo=timezone.utc)
                else:
                    continue
                
                # 检查时间窗口
                if pub_time < cutoff_time:
                    continue
                
                title = entry.get('title', '')
                summary = entry.get('summary', '')
                
                # Google News的特殊处理 - 不需要严格关键词匹配，因为搜索已经过滤
                if config.get("is_search"):
                    matched_keywords = keywords[:3]  # 使用搜索关键词
                else:
                    # 关键词匹配 - 处理空格分隔的搜索词
                    content = (title + " " + summary).lower()
                    # 将空格分隔的关键词拆分为单个词
                    all_keywords = []
                    for kw in keywords:
                        if ' ' in kw:
                            all_keywords.extend(kw.split())
                        else:
                            all_keywords.append(kw)
                    matched_keywords = [kw for kw in all_keywords if kw.lower() in content and len(kw) > 2]
                
                if matched_keywords:
                    # 清理HTML标签
                    summary_clean = BeautifulSoup(summary, 'html.parser').get_text()[:200]
                    
                    # 获取链接 - Google News链接会过期，需要特殊处理
                    raw_url = entry.get('link', '')
                    url = raw_url
                    
                    # Google News的链接是临时token链接，会过期
                    # 尝试提取真实链接或标记为过期
                    if config.get("is_search") and 'news.google.com' in raw_url:
                        # Google News RSS链接是临时的，会返回400
                        # 使用搜索链接代替（用户可手动搜索）
                        import urllib.parse
                        search_query = urllib.parse.quote(title[:50])
                        url = f"https://www.google.com/search?q={search_query}&tbm=nws"
                    
                    news_list.append({
                        "source": config["name"],
                        "author": entry.get('author', config["name"]),
                        "title": title,
                        "summary": summary_clean,
                        "url": url,
                        "published_at": pub_time.isoformat(),
                        "matched_keywords": matched_keywords,
                        "sentiment": self._analyze_sentiment(title + " " + summary_clean)
                    })
            
            print(f"   ✅ {config['name']}: 找到 {len(news_list)} 条相关新闻")
            return news_list
            
        except Exception as e:
            print(f"   ❌ {config['name']} RSS抓取失败: {e}")
            return []
    
    def fetch_twitter_news(self, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        从Twitter抓取新闻（使用Twitter API v2）
        
        Note: 需要配置Twitter API Bearer Token
        """
        # 尝试从环境变量读取
        bearer_token = None
        try:
            import os
            bearer_token = os.getenv('TWITTER_BEARER_TOKEN')
        except:
            pass
        
        if not bearer_token:
            print("   ⚠️ 未配置TWITTER_BEARER_TOKEN，跳过Twitter抓取")
            return []
        
        try:
            import tweepy
            
            print("   抓取 Twitter...")
            client = tweepy.Client(bearer_token=bearer_token)
            
            # 构建查询
            query = " OR ".join([f'"{kw}"' for kw in keywords[:3]])  # 最多3个关键词
            query += " -is:retweet lang:en"  # 排除转发，英文
            
            # 计算时间窗口
            start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            # 搜索推文
            tweets = tweepy.Paginator(
                client.search_recent_tweets,
                query=query,
                tweet_fields=['created_at', 'public_metrics', 'author_id'],
                expansions=['author_id'],
                max_results=20
            ).flatten(limit=20)
            
            news_list = []
            for tweet in tweets:
                tweet_time = tweet.created_at
                if tweet_time < start_time:
                    continue
                
                # 获取作者信息
                author = client.get_user(id=tweet.author_id).data
                author_name = author.username if author else "Unknown"
                
                # 过滤低质量推文
                metrics = tweet.public_metrics
                if metrics['like_count'] < 10:  # 只保留有一定互动的推文
                    continue
                
                news_list.append({
                    "source": "Twitter",
                    "author": f"@{author_name}",
                    "title": tweet.text[:200],
                    "summary": "",
                    "url": f"https://twitter.com/{author_name}/status/{tweet.id}",
                    "published_at": tweet_time.isoformat(),
                    "engagement": metrics['like_count'] + metrics['retweet_count'],
                    "sentiment": self._analyze_sentiment(tweet.text)
                })
            
            print(f"   ✅ Twitter: 找到 {len(news_list)} 条相关推文")
            return news_list
            
        except Exception as e:
            print(f"   ❌ Twitter抓取失败: {e}")
            return []
    
    def fetch_newsapi(self, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        从NewsAPI抓取新闻
        
        Note: 需要配置NEWSAPI_KEY
        """
        api_key = None
        try:
            import os
            api_key = os.getenv('NEWSAPI_KEY')
        except:
            pass
        
        if not api_key:
            print("   ⚠️ 未配置NEWSAPI_KEY，跳过NewsAPI")
            return []
        
        try:
            print("   抓取 NewsAPI...")
            
            query = " OR ".join(keywords[:3])
            from_date = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%d')
            
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': query,
                'from': from_date,
                'sortBy': 'relevancy',
                'language': 'en',
                'pageSize': 20,
                'apiKey': api_key
            }
            
            response = self.session.get(url, params=params, timeout=10)
            data = response.json()
            
            if data.get('status') != 'ok':
                print(f"   ❌ NewsAPI错误: {data.get('message')}")
                return []
            
            news_list = []
            for article in data.get('articles', []):
                pub_time = datetime.fromisoformat(article['publishedAt'].replace('Z', '+00:00'))
                
                news_list.append({
                    "source": article.get('source', {}).get('name', 'NewsAPI'),
                    "author": article.get('author', 'Unknown'),
                    "title": article.get('title', ''),
                    "summary": article.get('description', '')[:200],
                    "url": article.get('url', ''),
                    "published_at": pub_time.isoformat(),
                    "sentiment": self._analyze_sentiment(article.get('title', ''))
                })
            
            print(f"   ✅ NewsAPI: 找到 {len(news_list)} 条新闻")
            return news_list
            
        except Exception as e:
            print(f"   ❌ NewsAPI抓取失败: {e}")
            return []
    
    def _analyze_sentiment(self, text: str) -> str:
        """
        简单情绪分析（基于关键词）
        """
        text_lower = text.lower()
        
        positive_words = ['rise', 'gain', 'up', 'surge', 'jump', 'rally', 'bull', 'positive', 
                         'optimistic', 'agreement', 'deal', 'peace', 'negotiate', 'willing',
                         'boost', 'growth', 'success', 'win', 'lead', 'advance', 'progress']
        
        negative_words = ['fall', 'drop', 'down', 'plunge', 'crash', 'bear', 'negative',
                         'pessimistic', 'conflict', 'war', 'attack', 'crisis', 'risk',
                         'decline', 'loss', 'fail', 'lag', 'retreat', 'tension', 'threat']
        
        pos_count = sum(1 for word in positive_words if word in text_lower)
        neg_count = sum(1 for word in negative_words if word in text_lower)
        
        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        else:
            return "neutral"
    
    def fetch_all_news(self, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        从所有源抓取新闻
        """
        print(f"\n🔍 抓取新闻: 关键词 {keywords}")
        print(f"   时间窗口: 最近{hours}小时")
        print("-" * 50)
        
        all_news = []
        
        # RSS源 - 多源聚合，避免过度依赖单一源 - 2026-04-07
        # Google News（搜索全面，但链接会过期，已转换为搜索链接）
        google_news = self.fetch_rss_news("google_news", keywords, hours)
        all_news.extend(google_news)
        
        # BBC（可靠，链接永久）
        bbc_news = self.fetch_rss_news("bbc", keywords, hours)
        all_news.extend(bbc_news)
        
        # CNN（可用，链接永久）
        cnn_news = self.fetch_rss_news("cnn", keywords, hours)
        all_news.extend(cnn_news)
        
        # WSJ（可用，链接永久）
        wsj_news = self.fetch_rss_news("wsj", keywords, hours)
        all_news.extend(wsj_news)
        
        # NewsAPI（需要 API key）
        newsapi_news = self.fetch_newsapi(keywords, hours)
        all_news.extend(newsapi_news)
        
        # 按时间排序
        all_news.sort(key=lambda x: x["published_at"], reverse=True)
        
        print("-" * 50)
        print(f"✅ 总计: {len(all_news)} 条新闻\n")
        
        return all_news


# 测试
if __name__ == "__main__":
    print("🐋 新闻抓取器测试")
    print("=" * 60)
    
    fetcher = NewsFetcher()
    
    # 测试关键词
    test_keywords = ["Iran", "Israel", "conflict"]
    
    # 抓取新闻
    news_list = fetcher.fetch_all_news(test_keywords, hours=24)
    
    # 显示结果
    print("\n📰 抓取到的新闻:")
    for i, news in enumerate(news_list[:5], 1):
        print(f"\n{i}. [{news['source']}] {news['title'][:60]}...")
        print(f"   情绪: {news.get('sentiment', 'unknown')}")
        print(f"   链接: {news['url'][:50]}...")
    
    print("\n✅ 测试完成!")
