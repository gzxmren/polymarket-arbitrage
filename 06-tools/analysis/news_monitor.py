#!/usr/bin/env python3
"""
热点新闻监控器
监控新闻对 Polymarket 套利机会的影响
"""

import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional


# 关键新闻源配置
NEWS_SOURCES = {
    'crypto': [
        'coindesk',
        'cointelegraph',
        'decrypt',
        'theblock'
    ],
    'finance': [
        'bloomberg',
        'reuters',
        'wsj',
        'ft'
    ],
    'politics': [
        'politico',
        'cnn',
        'bbc',
        'reuters'
    ]
}

# 关键词映射到 Polymarket 市场类别
KEYWORD_MAPPING = {
    'bitcoin': ['BTC', 'crypto', 'bitcoin'],
    'ethereum': ['ETH', 'ethereum'],
    'election': ['election', 'trump', 'biden', 'vote'],
    'fed': ['fed', 'interest rate', 'powell', 'inflation'],
    'war': ['war', 'ukraine', 'israel', 'gaza'],
    'tech': ['apple', 'google', 'ai', 'openai']
}


class NewsMonitor:
    def __init__(self):
        self.impact_scores = {}
        self.last_check = datetime.now()
    
    def analyze_headline(self, headline: str, source: str = "") -> Dict:
        """
        分析新闻标题对 Polymarket 的影响
        
        Returns:
            {
                'impact_score': 影响分数 (0-100),
                'category': 影响类别,
                'keywords': 匹配关键词,
                'urgency': 紧急程度,
                'suggested_action': 建议操作
            }
        """
        headline_lower = headline.lower()
        
        # 计算影响分数
        impact_score = 0
        matched_keywords = []
        category = 'OTHER'
        
        for cat, keywords in KEYWORD_MAPPING.items():
            for kw in keywords:
                if kw in headline_lower:
                    impact_score += 25
                    matched_keywords.append(kw)
                    category = cat.upper()
        
        # 紧急程度判断
        urgency = 'LOW'
        urgent_words = ['breaking', 'urgent', 'just', 'now', 'alert']
        if any(w in headline_lower for w in urgent_words):
            urgency = 'HIGH'
            impact_score += 20
        
        # 情绪判断
        sentiment = 'NEUTRAL'
        positive = ['surge', 'rally', 'gain', 'up', 'high', 'bull']
        negative = ['crash', 'drop', 'fall', 'down', 'low', 'bear']
        
        if any(w in headline_lower for w in positive):
            sentiment = 'POSITIVE'
        elif any(w in headline_lower for w in negative):
            sentiment = 'NEGATIVE'
        
        # 建议操作
        suggested_action = self._suggest_action(category, sentiment, urgency)
        
        return {
            'impact_score': min(impact_score, 100),
            'category': category,
            'keywords': matched_keywords,
            'urgency': urgency,
            'sentiment': sentiment,
            'suggested_action': suggested_action,
            'headline': headline,
            'timestamp': datetime.now().isoformat()
        }
    
    def _suggest_action(self, category: str, sentiment: str, urgency: str) -> str:
        """根据分析结果建议操作"""
        if urgency == 'HIGH':
            if category == 'BTC' and sentiment == 'POSITIVE':
                return '关注 BTC 上涨相关市场'
            elif category == 'BTC' and sentiment == 'NEGATIVE':
                return '关注 BTC 下跌相关市场'
            elif category == 'ELECTION':
                return '关注选举相关市场，可能有剧烈波动'
            elif category == 'FED':
                return '关注利率相关市场，重大政策影响'
        
        if category in ['BTC', 'ETH']:
            return '监控加密货币市场波动'
        elif category == 'ELECTION':
            return '关注政治预测市场'
        elif category == 'FED':
            return '关注宏观经济市场'
        
        return '持续监控'
    
    def scan_news_impact(self, headlines: List[Dict]) -> List[Dict]:
        """
        扫描多条新闻的影响
        """
        results = []
        
        for news in headlines:
            headline = news.get('title', '')
            source = news.get('source', '')
            
            analysis = self.analyze_headline(headline, source)
            
            if analysis['impact_score'] > 30:  # 只返回高影响新闻
                results.append(analysis)
        
        # 按影响分数排序
        results.sort(key=lambda x: x['impact_score'], reverse=True)
        
        return results
    
    def generate_trading_signals(self, news_analysis: List[Dict]) -> List[Dict]:
        """
        基于新闻分析生成交易信号
        """
        signals = []
        
        for analysis in news_analysis:
            if analysis['impact_score'] < 50:
                continue
            
            signal = {
                'type': 'NEWS_DRIVEN',
                'category': analysis['category'],
                'urgency': analysis['urgency'],
                'direction': 'UP' if analysis['sentiment'] == 'POSITIVE' else 'DOWN',
                'confidence': analysis['impact_score'] / 100,
                'timeframe': 'IMMEDIATE' if analysis['urgency'] == 'HIGH' else '24H',
                'reason': analysis['headline'],
                'suggested_markets': self._get_relevant_markets(analysis['category']),
                'timestamp': analysis['timestamp']
            }
            
            signals.append(signal)
        
        return signals
    
    def _get_relevant_markets(self, category: str) -> List[str]:
        """获取相关市场类别"""
        market_map = {
            'BTC': ['Bitcoin', 'BTC', 'crypto'],
            'ETH': ['Ethereum', 'ETH', 'crypto'],
            'ELECTION': ['election', 'trump', 'biden', 'vote'],
            'FED': ['Fed', 'interest rate', 'inflation'],
            'WAR': ['war', 'ukraine', 'israel'],
            'TECH': ['apple', 'google', 'ai', 'tech']
        }
        
        return market_map.get(category, [])


def format_news_alert(analysis: Dict) -> str:
    """格式化新闻警报"""
    emoji = {
        'HIGH': '🚨',
        'MEDIUM': '⚠️',
        'LOW': 'ℹ️'
    }.get(analysis['urgency'], 'ℹ️')
    
    sentiment_emoji = {
        'POSITIVE': '📈',
        'NEGATIVE': '📉',
        'NEUTRAL': '➡️'
    }.get(analysis['sentiment'], '➡️')
    
    return f"""
{emoji} *新闻影响警报*

📰 {analysis['headline'][:80]}...

📊 影响分析:
• 分数: {analysis['impact_score']}/100
• 类别: {analysis['category']}
• 情绪: {sentiment_emoji} {analysis['sentiment']}
• 紧急: {analysis['urgency']}

💡 建议: {analysis['suggested_action']}

🔑 关键词: {', '.join(analysis['keywords'])}
"""


if __name__ == "__main__":
    # 测试
    monitor = NewsMonitor()
    
    test_headlines = [
        {'title': 'Bitcoin surges 10% after Fed announcement', 'source': 'coindesk'},
        {'title': 'Trump wins key swing state polls', 'source': 'politico'},
        {'title': 'Breaking: Major earthquake in California', 'source': 'cnn'}
    ]
    
    results = monitor.scan_news_impact(test_headlines)
    for r in results:
        print(format_news_alert(r))
        print("---")
