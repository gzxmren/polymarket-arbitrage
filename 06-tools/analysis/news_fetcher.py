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
        self.rss_sources = {
            "reuters": {
                "name": "Reuters",
                "url": "https://www.reutersagency.com/feed/?taxonomy=markets&post_type=reuters-best",
                "enabled": True
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
            }
        }
    
    def fetch_rss_news(self, source: str, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        从RSS源抓取新闻
        """
        if source not in self.rss_sources:
            return []
        
        config = self.rss_sources[source]
        if not config["enabled"]:
            return []
        
        try:
            print(f"   抓取 {config['name']} RSS...")
            feed = feedparser.parse(config["url"])
            
            news_list = []
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            for entry in feed.entries[:20]:  # 只检查最近20条
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
                
                # 关键词匹配
                content = (title + " " + summary).lower()
                matched_keywords = [kw for kw in keywords if kw.lower() in content]
                
                if matched_keywords:
                    # 清理HTML标签
                    summary_clean = BeautifulSoup(summary, 'html.parser').get_text()[:200]
                    
                    news_list.append({
                        "source": config["name"],
                        "author": entry.get('author', config["name"]),
                        "title": title,
                        "summary": summary_clean,
                        "url": entry.get('link', ''),
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
        
        # RSS源
        for source in ["reuters", "bbc", "cnn", "wsj"]:
            news = self.fetch_rss_news(source, keywords, hours)
            all_news.extend(news)
        
        # Twitter
        twitter_news = self.fetch_twitter_news(keywords, hours)
        all_news.extend(twitter_news)
        
        # NewsAPI
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
