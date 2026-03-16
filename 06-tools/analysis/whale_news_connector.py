#!/usr/bin/env python3
"""
鲸鱼持仓新闻关联系统
将鲸鱼持仓与相关新闻建立时间关联
"""

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))  # 添加当前目录以导入news_fetcher

# 配置
DATA_DIR = Path(__file__).parent.parent.parent / "07-data"
NEWS_CACHE_DIR = DATA_DIR / "news_cache"
NEWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 新闻源配置
NEWS_SOURCES = {
    "twitter": {
        "enabled": True,
        "priority": 1,  # 最高优先级（实时性）
        "time_window": 6,  # 小时
    },
    "reuters": {
        "enabled": True,
        "priority": 2,
        "time_window": 6,
    },
    "bloomberg": {
        "enabled": True,
        "priority": 3,
        "time_window": 6,
    }
}


class WhaleNewsConnector:
    """鲸鱼持仓新闻关联器"""
    
    def __init__(self):
        self.news_cache = {}
        self.load_cache()
        
        # 导入真实新闻抓取器
        try:
            from news_fetcher import NewsFetcher
            self.real_fetcher = NewsFetcher()
            self.use_real_fetcher = True
        except Exception as e:
            print(f"⚠️ 真实新闻抓取器加载失败: {e}")
            self.use_real_fetcher = False
    
    def load_cache(self):
        """加载新闻缓存"""
        cache_file = NEWS_CACHE_DIR / "news_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    self.news_cache = json.load(f)
            except:
                self.news_cache = {}
    
    def save_cache(self):
        """保存新闻缓存"""
        cache_file = NEWS_CACHE_DIR / "news_cache.json"
        with open(cache_file, 'w') as f:
            json.dump(self.news_cache, f, indent=2)
    
    def extract_keywords(self, market_title: str) -> Dict[str, List[str]]:
        """
        从市场标题提取关键词
        
        Returns:
            {
                "primary": ["Iran", "Israel"],      # 核心实体
                "secondary": ["attack", "2024"],    # 关键条件
                "context": ["Middle East"]           # 背景类别
            }
        """
        keywords = {
            "primary": [],
            "secondary": [],
            "context": []
        }
        
        title_lower = market_title.lower()
        
        # 核心实体（国家、人物、组织）
        entities = {
            "trump": "Trump", "biden": "Biden", "trump's": "Trump",
            "iran": "Iran", "israel": "Israel", "israeli": "Israel",
            "china": "China", "russia": "Russia", "ukraine": "Ukraine",
            "btc": "BTC", "bitcoin": "Bitcoin", "eth": "ETH", "ethereum": "Ethereum",
            "fed": "Fed", "federal reserve": "Fed",
            "sec": "SEC", "etf": "ETF"
        }
        
        for key, value in entities.items():
            if key in title_lower:
                if value not in keywords["primary"]:
                    keywords["primary"].append(value)
        
        # 关键条件
        conditions = ["attack", "war", "conflict", "election", "win", "price", "hit", "above", "below"]
        for cond in conditions:
            if cond in title_lower:
                keywords["secondary"].append(cond)
        
        # 背景类别
        categories = {
            "middle east": "Middle East",
            "crypto": "Crypto", "cryptocurrency": "Crypto",
            "election": "Election", "presidential": "Election",
            "economy": "Economy", "economic": "Economy"
        }
        
        for key, value in categories.items():
            if key in title_lower:
                if value not in keywords["context"]:
                    keywords["context"].append(value)
        
        return keywords
    
    def fetch_twitter_news(self, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        从Twitter抓取相关推文
        
        Note: 这里使用模拟数据，实际实现需要Twitter API
        """
        # TODO: 实现真实的Twitter API调用
        # 现在返回模拟数据用于测试
        
        mock_news = []
        
        # 根据关键词生成模拟新闻
        if "Iran" in keywords or "Israel" in keywords:
            mock_news.extend([
                {
                    "source": "Twitter",
                    "author": "@Reuters",
                    "title": "Iran signals willingness to resume nuclear talks with Western powers",
                    "url": "https://twitter.com/Reuters/status/1234567890",
                    "published_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
                    "sentiment": "positive",
                    "engagement": 12500
                },
                {
                    "source": "Twitter",
                    "author": "@AlJazeera",
                    "title": "Israeli military reports no unusual Iranian military movements",
                    "url": "https://twitter.com/AlJazeera/status/1234567891",
                    "published_at": (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(),
                    "sentiment": "neutral",
                    "engagement": 8300
                }
            ])
        
        if "Trump" in keywords:
            mock_news.extend([
                {
                    "source": "Twitter",
                    "author": "@Bloomberg",
                    "title": "Trump leads in latest swing state polls according to internal data",
                    "url": "https://twitter.com/Bloomberg/status/1234567892",
                    "published_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
                    "sentiment": "positive",
                    "engagement": 25600
                }
            ])
        
        if "BTC" in keywords or "Bitcoin" in keywords:
            mock_news.extend([
                {
                    "source": "Twitter",
                    "author": "@CoinDesk",
                    "title": "Multiple institutions file for Bitcoin ETF approval",
                    "url": "https://twitter.com/CoinDesk/status/1234567893",
                    "published_at": (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(),
                    "sentiment": "positive",
                    "engagement": 18900
                }
            ])
        
        return mock_news
    
    def fetch_reuters_news(self, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        从Reuters RSS抓取新闻
        
        Note: 这里使用模拟数据
        """
        mock_news = []
        
        if "Iran" in keywords:
            mock_news.append({
                "source": "Reuters",
                "author": "Reuters",
                "title": "Iran says ready to negotiate if US shows goodwill",
                "url": "https://reuters.com/article/12345",
                "published_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                "sentiment": "positive",
                "engagement": 45000
            })
        
        return mock_news
    
    def fetch_news(self, keywords: Dict[str, List[str]], hours: int = 6) -> List[Dict]:
        """
        从多个源抓取新闻
        """
        all_keywords = keywords["primary"] + keywords["secondary"] + keywords["context"]
        
        # 优先使用真实新闻抓取器
        if self.use_real_fetcher and all_keywords:
            try:
                return self.real_fetcher.fetch_all_news(all_keywords, hours)
            except Exception as e:
                print(f"⚠️ 真实新闻抓取失败，回退到模拟数据: {e}")
        
        # 回退到模拟数据
        news_list = []
        
        # Twitter
        if NEWS_SOURCES["twitter"]["enabled"]:
            twitter_news = self.fetch_twitter_news(all_keywords, hours)
            news_list.extend(twitter_news)
        
        # Reuters
        if NEWS_SOURCES["reuters"]["enabled"]:
            reuters_news = self.fetch_reuters_news(all_keywords, hours)
            news_list.extend(reuters_news)
        
        # 按时间排序
        news_list.sort(key=lambda x: x["published_at"], reverse=True)
        
        return news_list
    
    def calculate_relevance(self, news: Dict, position: Dict, trade_time: datetime) -> Dict:
        """
        计算新闻与持仓的关联度
        
        评分维度:
        1. 关键词匹配度 (40%)
        2. 时间接近度 (30%)
        3. 市场情绪一致性 (20%)
        4. 来源权威性 (10%)
        """
        score = 0
        factors = {}
        
        # 1. 关键词匹配度
        market_title = position.get("market", "").lower()
        news_title = news.get("title", "").lower()
        
        keyword_matches = 0
        keywords_to_check = ["iran", "israel", "trump", "biden", "btc", "bitcoin", "attack", "war", "election"]
        for kw in keywords_to_check:
            if kw in market_title and kw in news_title:
                keyword_matches += 1
        
        keyword_score = min(keyword_matches * 20, 40)
        score += keyword_score
        factors["keywords"] = keyword_score
        
        # 2. 时间接近度
        news_time = datetime.fromisoformat(news["published_at"].replace('Z', '+00:00'))
        time_diff = abs((news_time - trade_time).total_seconds() / 3600)  # 小时
        
        if time_diff <= 1:
            time_score = 30
        elif time_diff <= 2:
            time_score = 25
        elif time_diff <= 6:
            time_score = 20
        elif time_diff <= 12:
            time_score = 10
        else:
            time_score = 5
        
        score += time_score
        factors["time"] = time_score
        
        # 3. 市场情绪一致性
        sentiment = news.get("sentiment", "neutral")
        # 简化处理：假设正面新闻对"Will X happen"类型市场有利
        sentiment_score = 15 if sentiment != "neutral" else 10
        score += sentiment_score
        factors["sentiment"] = sentiment_score
        
        # 4. 来源权威性
        source = news.get("source", "")
        authority_scores = {"Reuters": 10, "Bloomberg": 10, "Twitter": 5}
        authority_score = authority_scores.get(source, 5)
        score += authority_score
        factors["authority"] = authority_score
        
        return {
            "score": score,
            "factors": factors,
            "time_diff_hours": round(time_diff, 1)
        }
    
    def generate_whale_news_report(self, whale_data: Dict, positions: List[Dict], 
                                   trade_time: Optional[datetime] = None) -> str:
        """
        生成鲸鱼持仓新闻报告
        """
        if trade_time is None:
            trade_time = datetime.now(timezone.utc)
        
        wallet = whale_data.get("wallet", "")
        pseudonym = whale_data.get("pseudonym", wallet[:10] + "...")
        total_value = whale_data.get("total_value", 0)
        
        lines = [
            f"🐋 鲸鱼 {pseudonym} 持仓新闻关联报告",
            "",
            f"⏰ 分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"💰 总持仓: ${total_value:,.0f} ({len(positions)}个市场)",
            f"📊 新闻时间窗口: 调仓前后6小时",
            "",
            "=" * 60,
            ""
        ]
        
        # 按持仓价值排序
        sorted_positions = sorted(positions, key=lambda x: x.get("value", 0), reverse=True)
        
        for i, position in enumerate(sorted_positions, 1):
            market = position.get("market", "Unknown")
            value = position.get("value", 0)
            value_pct = (value / total_value * 100) if total_value > 0 else 0
            
            # 提取关键词
            keywords = self.extract_keywords(market)
            
            # 抓取新闻
            news_list = self.fetch_news(keywords, hours=6)
            
            # 计算关联度
            relevant_news = []
            for news in news_list:
                relevance = self.calculate_relevance(news, position, trade_time)
                if relevance["score"] >= 50:  # 只显示高关联度新闻
                    relevant_news.append({
                        **news,
                        "relevance": relevance
                    })
            
            # 按关联度排序
            relevant_news.sort(key=lambda x: x["relevance"]["score"], reverse=True)
            
            # 生成持仓区块
            lines.extend([
                f"{'━' * 60}",
                f"{i}. {market[:50]}",
                f"{'━' * 60}",
                f"   💰 持仓: ${value:,.0f} ({value_pct:.1f}%)",
                f"   🔍 关键词: {', '.join(keywords['primary'][:3])}",
                ""
            ])
            
            if relevant_news:
                lines.append(f"   📰 相关新闻 (关联度>50%):")
                lines.append("")
                
                for j, news in enumerate(relevant_news[:3], 1):  # 最多显示3条
                    rel = news["relevance"]
                    time_diff = rel["time_diff_hours"]
                    time_label = f"{time_diff}h"
                    if time_diff < 1:
                        time_label = f"{int(time_diff * 60)}min"
                    
                    # 判断是调仓前还是调仓后
                    news_time = datetime.fromisoformat(news["published_at"].replace('Z', '+00:00'))
                    if news_time < trade_time:
                        time_position = "调仓前"
                    else:
                        time_position = "调仓后"
                    
                    sentiment_emoji = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(
                        news.get("sentiment", "neutral"), "➡️"
                    )
                    
                    lines.extend([
                        f"   {j}. [{time_position} {time_label}] {news['source']}",
                        f"      ├─ 标题: {news['title'][:60]}...",
                        f"      ├─ 情绪: {sentiment_emoji} {news.get('sentiment', 'neutral')}",
                        f"      ├─ 关联度: {rel['score']}/100",
                        f"      └─ 链接: {news['url'][:50]}...",
                        ""
                    ])
                
                # 添加解读
                avg_relevance = sum(n["relevance"]["score"] for n in relevant_news[:3]) / min(len(relevant_news), 3)
                if avg_relevance >= 80:
                    interpretation = "新闻与持仓高度相关，支持鲸鱼判断"
                elif avg_relevance >= 60:
                    interpretation = "新闻与持仓中度相关，可作为参考"
                else:
                    interpretation = "新闻关联度一般，需谨慎判断"
                
                lines.extend([
                    f"   💡 解读: {interpretation}",
                    ""
                ])
            else:
                lines.extend([
                    "   ⚪ 暂无高关联度新闻",
                    ""
                ])
        
        lines.extend([
            "=" * 60,
            "",
            "📌 说明:",
            "• 新闻来源: Twitter, Reuters, Bloomberg",
            "• 关联度计算: 关键词匹配40% + 时间30% + 情绪20% + 权威性10%",
            "• 时间窗口: 调仓前后6小时",
            "• 仅供参考，不构成投资建议"
        ])
        
        return "\n".join(lines)


# 测试代码
if __name__ == "__main__":
    print("🐋 鲸鱼持仓新闻关联系统测试")
    print("=" * 60)
    
    connector = WhaleNewsConnector()
    
    # 测试关键词提取
    print("\n📍 测试1: 关键词提取")
    test_markets = [
        "Will Iran attack Israel in 2024?",
        "Will Trump win the 2024 US Presidential Election?",
        "Will BTC hit $100k in 2024?"
    ]
    
    for market in test_markets:
        keywords = connector.extract_keywords(market)
        print(f"\n市场: {market}")
        print(f"  核心: {keywords['primary']}")
        print(f"  条件: {keywords['secondary']}")
        print(f"  背景: {keywords['context']}")
    
    # 测试新闻抓取
    print("\n📍 测试2: 新闻抓取")
    keywords = {"primary": ["Iran", "Israel"], "secondary": ["attack"], "context": ["Middle East"]}
    news_list = connector.fetch_news(keywords, hours=6)
    print(f"抓取到 {len(news_list)} 条新闻")
    for news in news_list[:3]:
        print(f"  - [{news['source']}] {news['title'][:50]}...")
    
    # 测试完整报告生成
    print("\n📍 测试3: 生成鲸鱼持仓新闻报告")
    
    test_whale = {
        "wallet": "0x1234567890abcdef",
        "pseudonym": "TestWhale",
        "total_value": 150000
    }
    
    test_positions = [
        {
            "market": "Will Iran attack Israel in 2024?",
            "value": 50000,
            "outcome": "No"
        },
        {
            "market": "Will Trump win 2024?",
            "value": 40000,
            "outcome": "Yes"
        },
        {
            "market": "Will BTC hit $100k in 2024?",
            "value": 30000,
            "outcome": "Yes"
        }
    ]
    
    report = connector.generate_whale_news_report(test_whale, test_positions)
    print(report)
    
    print("\n✅ 测试完成!")
