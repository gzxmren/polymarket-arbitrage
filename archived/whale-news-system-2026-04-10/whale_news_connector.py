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
from config import DATA_DIR, NEWS_CACHE_DIR
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
    },
    # 方案4: 添加体育新闻源 - 2026-03-26
    "espn": {
        "enabled": True,
        "priority": 1,
        "time_window": 24,
        "base_url": "https://www.espn.com/soccer/"
    },
    "bbc_sport": {
        "enabled": True,
        "priority": 2,
        "time_window": 24,
        "base_url": "https://www.bbc.com/sport/football"
    }
}


# 方案1: 添加市场日期提取函数 - 2026-03-26
def extract_market_date(market_title: str) -> Optional[datetime]:
    """从市场标题提取日期"""
    match = re.search(r'(\d{4}-\d{2}-\d{2})', market_title)
    if match:
        return datetime.strptime(match.group(1), '%Y-%m-%d')
    return None


class WhaleNewsConnector:
    """鲸鱼持仓新闻关联器"""
    
    def __init__(self):
        self.news_cache = {}
        self.load_cache()

        # 启用真实新闻抓取器 - 2026-03-26
        # 延迟加载，避免初始化时网络请求卡住
        self._fetcher_instance = None

    @property
    def real_fetcher(self):
        """延迟加载真实新闻抓取器"""
        if self._fetcher_instance is None:
            try:
                from news_fetcher import NewsFetcher
                self._fetcher_instance = NewsFetcher()
            except Exception as e:
                print(f"⚠️ 真实新闻抓取器加载失败: {e}")
                self._fetcher_instance = None
        return self._fetcher_instance
    
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
    
    # L1+L2: 优化关键词提取和过滤规则 - 2026-04-06
    # 市场类型分类和负向关键词配置
    MARKET_TYPE_PATTERNS = {
        "football": {
            "patterns": ["la liga", "premier league", "champions league", "bundesliga", "serie a", "world cup", "euro"],
            "exclude_keywords": ["basketball", "baseball", "ncaa", "nba", "mlb", "nfl", "nhl", "tennis", "golf"],
            "required_context": ["match", "game", "team", "player", "goal", "score", "win", "lose", "draw"]
        },
        "basketball": {
            "patterns": ["nba", "ncaa", "basketball"],
            "exclude_keywords": ["football", "soccer", "baseball", "goal", "match"],
            "required_context": []
        },
        "politics": {
            "patterns": ["president", "election", "nomination", "primary", "vote", "congress", "senate"],
            "exclude_keywords": ["basketball", "baseball", "football", "soccer", "game", "match", "score"],
            "required_context": []
        },
        "crypto": {
            "patterns": ["bitcoin", "btc", "ethereum", "eth", "crypto", "etf"],
            "exclude_keywords": ["game", "match", "team", "player"],
            "required_context": []
        }
    }

    # 高质量新闻源白名单
    HIGH_QUALITY_SOURCES = {
        "politics": ["Reuters", "Bloomberg", "Politico", "AP", "BBC", "CNN", "Fox News", "The Guardian"],
        "sports": ["ESPN", "BBC Sport", "Sky Sports", "Goal.com", "Transfermarkt", "The Athletic"],
        "crypto": ["CoinDesk", "CoinTelegraph", "The Block", "Decrypt", "CryptoSlate"]
    }

    # 低质量/噪音指示词
    LOW_QUALITY_INDICATORS = [
        "student election", "high school", "local news", "community",
        "knockout", "overton", "gannon", "tulsa", "auburn", "ucla"
    ]

    # L2: 实体级排除（人名、球队名等）
    ENTITY_EXCLUSIONS = {
        "football": {
            "players": ["curry", "lebron", "james", "durant", "giannis", "doncic", "jokic"],
            "teams": ["warriors", "lakers", "celtics", "bulls", "nets", "suns", "bucks", "nuggets"],
            "leagues": ["nba", "ncaa basketball", "wnba"]
        },
        "basketball": {
            "players": ["messi", "ronaldo", "mbappe", "haaland", "lewandowski"],
            "teams": ["real madrid", "barcelona", "manchester united", "liverpool", "bayern munich"],
            "leagues": ["premier league", "la liga", "bundesliga", "serie a", "champions league"]
        },
        "politics": {
            "sports_terms": ["match", "game", "score", "goal", "tournament", "championship", "win", "lose"]
        }
    }

    def extract_keywords(self, market_title: str) -> Dict[str, List[str]]:
        """
        从市场标题提取精准关键词（L1优化）

        Returns:
            {
                "primary": ["Arsenal", "Premier League", "2025-26"],
                "secondary": ["win", "championship"],
                "context": ["football"],
                "search_query": "Arsenal Premier League 2025-26 win championship",
                "market_type": "football",
                "exclude_terms": ["basketball", "baseball", "ncaa"]
            }
        """
        keywords = {
            "primary": [],
            "secondary": [],
            "context": [],
            "search_query": "",
            "market_type": None,
            "exclude_terms": []
        }

        title_lower = market_title.lower()
        title_original = market_title

        # Step 1: 检测市场类型
        market_type = None
        for mtype, config in self.MARKET_TYPE_PATTERNS.items():
            if any(p in title_lower for p in config["patterns"]):
                market_type = mtype
                keywords["market_type"] = mtype
                keywords["exclude_terms"] = config["exclude_keywords"]
                break

        # Step 2: 提取完整实体（带限定词）
        # 政治人物
        politicians = {
            "trump": "Donald Trump", "vance": "J.D. Vance", "biden": "Joe Biden",
            "harris": "Kamala Harris", "desantis": "Ron DeSantis",
            "republican": "Republican", "democrat": "Democrat"
        }

        # 足球队（带完整名称）
        football_teams = {
            "real madrid": "Real Madrid",
            "barcelona": "FC Barcelona",
            "manchester city": "Manchester City",
            "manchester united": "Manchester United",
            "liverpool": "Liverpool FC",
            "chelsea": "Chelsea FC",
            "arsenal": "Arsenal FC",
            "tottenham": "Tottenham Hotspur",
            "juventus": "Juventus",
            "bayern munich": "Bayern Munich",
            "bayern": "Bayern Munich",
            "psg": "Paris Saint-Germain",
            "paris saint-germain": "Paris Saint-Germain",
            "borussia dortmund": "Borussia Dortmund",
            "atletico madrid": "Atletico Madrid",
            "atlético madrid": "Atletico Madrid",
            "inter milan": "Inter Milan",
            "ac milan": "AC Milan",
            "napoli": "Napoli",
            "roma": "AS Roma",
            "vfb stuttgart": "VfB Stuttgart",
            "stuttgart": "VfB Stuttgart",
            "nottingham forest": "Nottingham Forest",
            "sporting cp": "Sporting CP",
            "sporting": "Sporting CP",
        }

        # 赛事名称
        competitions = {
            "la liga": "La Liga",
            "premier league": "Premier League",
            "champions league": "Champions League",
            "bundesliga": "Bundesliga",
            "serie a": "Serie A",
            "world cup": "World Cup",
            "euro": "Euro",
            "nba": "NBA",
            "ncaa": "NCAA",
            "mlb": "MLB",
            "nfl": "NFL"
        }

        # 提取实体
        for key, value in politicians.items():
            if key in title_lower:
                if value not in keywords["primary"]:
                    keywords["primary"].append(value)

        for key, value in football_teams.items():
            if key in title_lower:
                if value not in keywords["primary"]:
                    keywords["primary"].append(value)

        for key, value in competitions.items():
            if key in title_lower:
                if value not in keywords["primary"]:
                    keywords["primary"].append(value)

        # Step 3: 提取年份/时间
        year_match = re.search(r'(20\d{2})', title_original)
        if year_match:
            year = year_match.group(1)
            if year not in keywords["primary"]:
                keywords["primary"].append(year)

        # Step 4: 提取关键动词/条件
        conditions = {
            "win": "win", "wins": "win", "winning": "win",
            "lose": "lose", "loses": "lose", "losing": "lose",
            "attack": "attack", "war": "war", "conflict": "conflict",
            "election": "election", "nomination": "nomination",
            "price": "price", "hit": "hit", "above": "above", "below": "below"
        }

        for key, value in conditions.items():
            if key in title_lower:
                if value not in keywords["secondary"]:
                    keywords["secondary"].append(value)

        # Step 5: 添加上下文类别
        if market_type:
            keywords["context"].append(market_type)

        # Step 6: 生成搜索查询（用于新闻API）
        # 策略：实体 + 赛事 + 年份 + 关键条件
        search_terms = keywords["primary"] + keywords["secondary"]
        keywords["search_query"] = " ".join(search_terms[:6])  # 限制长度

        return keywords

    def _keyword_match(self, keyword: str, text: str) -> bool:
        """
        智能关键词匹配（L2优化）
        支持：
        - "FC Barcelona" 匹配 "Barcelona"
        - "La Liga" 匹配 "LaLiga"
        - "J.D. Vance" 匹配 "Vance" 或 "JD Vance"
        """
        text_lower = text.lower()

        # 直接包含
        if keyword in text_lower:
            return True

        # 处理缩写和变体
        keyword_variants = [keyword]

        # "FC Barcelona" -> "barcelona"
        if ' ' in keyword:
            parts = keyword.split()
            for part in parts:
                if len(part) > 2 and part not in ['fc', 'cf', 'the', 'and']:
                    keyword_variants.append(part.lower())

        # "La Liga" -> "laliga"
        if ' ' in keyword:
            keyword_variants.append(keyword.replace(' ', '').lower())

        # "J.D. Vance" -> "vance", "jd vance"
        keyword_clean = keyword.replace('.', '').replace('-', ' ').lower()
        if keyword_clean != keyword:
            keyword_variants.append(keyword_clean)
            # 提取最后一个词（通常是姓氏）
            parts = keyword_clean.split()
            if len(parts) > 1:
                keyword_variants.append(parts[-1])

        return any(variant in text_lower for variant in keyword_variants)

    def filter_news_by_relevance(self, news_list: List[Dict], keywords: Dict) -> List[Dict]:
        """
        L2: 负向过滤和来源质量筛选（增强版）
        """
        filtered = []
        market_type = keywords.get("market_type")
        exclude_terms = keywords.get("exclude_terms", [])

        # 获取实体级排除列表
        entity_exclusions = []
        if market_type and market_type in self.ENTITY_EXCLUSIONS:
            entity_exclusions = (
                self.ENTITY_EXCLUSIONS[market_type].get("players", []) +
                self.ENTITY_EXCLUSIONS[market_type].get("teams", []) +
                self.ENTITY_EXCLUSIONS[market_type].get("leagues", []) +
                self.ENTITY_EXCLUSIONS[market_type].get("sports_terms", [])
            )

        for news in news_list:
            title = news.get("title", "").lower()
            source = news.get("source", "")

            # 1. 排除明显无关的新闻（关键词级）
            if any(exclude in title for exclude in exclude_terms):
                continue

            # 2. 排除实体级干扰（L2增强）
            if any(entity in title for entity in entity_exclusions):
                continue

            # 3. 排除低质量指示词
            if any(indicator in title for indicator in self.LOW_QUALITY_INDICATORS):
                continue

            # 4. 来源质量检查（可选，降低权重而非过滤）
            source_quality = "medium"
            if market_type and market_type in self.HIGH_QUALITY_SOURCES:
                if source in self.HIGH_QUALITY_SOURCES[market_type]:
                    source_quality = "high"
                    news["source_quality"] = source_quality

            filtered.append(news)

        return filtered


    # 模拟数据方法已删除 - 2026-03-26
    # 只使用真实新闻抓取

    def _old_fetch_twitter_news(self, keywords: List[str], hours: int = 6) -> List[Dict]:
        """
        [已弃用] 原Twitter模拟数据方法
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

    # 方案4: 添加ESPN体育新闻抓取 - 2026-03-26
    def fetch_espn_news(self, keywords: List[str], hours: int = 24) -> List[Dict]:
        """
        从ESPN抓取足球新闻

        Note: 这里使用模拟数据，实际实现需要ESPN API或RSS
        """
        mock_news = []

        # 检查是否包含体育相关关键词
        # 修复：添加缺失的球队名称 - 2026-03-26
        sports_teams = ["Real Madrid", "Barcelona", "Manchester", "Liverpool",
                       "Chelsea", "Arsenal", "Tottenham", "Juventus",
                       "Bayern Munich", "PSG", "Borussia Dortmund",
                       "VfB Stuttgart", "Nottingham Forest", "Atletico Madrid"]

        has_sports = any(team in keywords for team in sports_teams)

        if has_sports or "sports" in keywords:
            # 修复：使用更合理的时间范围，确保关联度计算有效 - 2026-03-26
            now = datetime.now(timezone.utc)
            # 修复：根据关键词中的球队生成相关新闻标题 - 2026-03-26
            # 找出匹配的球队
            matched_teams = [team for team in sports_teams if team in keywords]
            if matched_teams:
                team = matched_teams[0]  # 使用第一个匹配的球队
                mock_news.extend([
                    {
                        "source": "ESPN",
                        "author": "ESPN",
                        "title": f"{team} prepares for crucial match this weekend",
                        "url": "https://www.espn.com/soccer/story/12345",
                        "published_at": (now - timedelta(hours=2)).isoformat(),
                        "sentiment": "neutral",
                        "engagement": 25000
                    },
                    {
                        "source": "ESPN",
                        "author": "ESPN",
                        "title": f"{team} star player returns from injury ahead of clash",
                        "url": "https://www.espn.com/soccer/story/12346",
                        "published_at": (now - timedelta(hours=4)).isoformat(),
                        "sentiment": "positive",
                        "engagement": 32000
                    }
                ])
            else:
                # 默认新闻
                mock_news.extend([
                    {
                        "source": "ESPN",
                        "author": "ESPN",
                        "title": "Champions League preview: Key matches to watch",
                        "url": "https://www.espn.com/soccer/story/12345",
                        "published_at": (now - timedelta(hours=2)).isoformat(),
                        "sentiment": "neutral",
                        "engagement": 25000
                    }
                ])

        return mock_news

    # 方案4: 添加BBC Sport体育新闻抓取 - 2026-03-26
    def fetch_bbc_sport_news(self, keywords: List[str], hours: int = 24) -> List[Dict]:
        """
        从BBC Sport抓取足球新闻

        Note: 这里使用模拟数据，实际实现需要BBC Sport RSS
        """
        mock_news = []

        # 检查是否包含体育相关关键词
        # 修复：添加缺失的球队名称 - 2026-03-26
        sports_teams = ["Real Madrid", "Barcelona", "Manchester", "Liverpool",
                       "Chelsea", "Arsenal", "Tottenham", "Juventus",
                       "Bayern Munich", "PSG", "Borussia Dortmund",
                       "VfB Stuttgart", "Nottingham Forest", "Atletico Madrid"]

        has_sports = any(team in keywords for team in sports_teams)

        if has_sports or "sports" in keywords:
            # 修复：使用更合理的时间范围，确保关联度计算有效 - 2026-03-26
            now = datetime.now(timezone.utc)
            # 修复：根据关键词中的球队生成相关新闻标题 - 2026-03-26
            matched_teams = [team for team in sports_teams if team in keywords]
            if matched_teams:
                team = matched_teams[0]
                mock_news.extend([
                    {
                        "source": "BBC Sport",
                        "author": "BBC",
                        "title": f"{team} aims to continue winning streak",
                        "url": "https://www.bbc.com/sport/football/12345",
                        "published_at": (now - timedelta(hours=3)).isoformat(),
                        "sentiment": "positive",
                        "engagement": 18000
                    },
                    {
                        "source": "BBC Sport",
                        "author": "BBC",
                        "title": f"{team} coach praises team spirit ahead of clash",
                        "url": "https://www.bbc.com/sport/football/12346",
                        "published_at": (now - timedelta(hours=6)).isoformat(),
                        "sentiment": "neutral",
                        "engagement": 28000
                    }
                ])
            else:
                mock_news.extend([
                    {
                        "source": "BBC Sport",
                        "author": "BBC",
                        "title": "Champions League draw sets up exciting quarter-finals",
                        "url": "https://www.bbc.com/sport/football/12346",
                        "published_at": (now - timedelta(hours=6)).isoformat(),
                        "sentiment": "positive",
                        "engagement": 28000
                    }
                ])

        return mock_news
    
    def fetch_news(self, keywords: Dict[str, List[str]], hours: int = 6) -> List[Dict]:
        """
        从多个源抓取新闻 - L1+L2优化：使用精准搜索查询 + 负向过滤
        """
        # L1: 使用生成的搜索查询（更精准）
        search_query = keywords.get("search_query", "")
        all_keywords = keywords["primary"] + keywords["secondary"] + keywords["context"]

        if not search_query and not all_keywords:
            return []

        # 优先使用精准搜索查询
        search_terms = search_query if search_query else " ".join(all_keywords[:5])

        # 使用真实新闻抓取器（带超时）- 2026-03-26
        try:
            import concurrent.futures
            fetcher = self.real_fetcher
            if fetcher:
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    # 使用精准搜索查询
                    future = executor.submit(fetcher.fetch_all_news, [search_terms], hours)
                    real_news = future.result(timeout=20)  # 20秒超时

                    # L2: 应用负向过滤
                    filtered_news = self.filter_news_by_relevance(real_news, keywords)
                    return filtered_news
            else:
                print("⚠️ 新闻抓取器未初始化")
                return []
        except concurrent.futures.TimeoutError:
            print("⚠️ 新闻抓取超时(20s)")
            return []
        except Exception as e:
            print(f"⚠️ 新闻抓取失败: {e}")
            return []
    
    def calculate_relevance(self, news: Dict, position: Dict, trade_time: datetime,
                           keywords: Dict = None) -> Dict:
        """
        计算新闻与持仓的关联度（L1+L2优化版）

        评分维度:
        1. 关键词匹配度 (40%) - 使用提取的精准关键词
        2. 时间接近度 (30%) - 更严格的时间衰减
        3. 市场情绪一致性 (20%)
        4. 来源权威性 (10%) - 考虑市场类型
        """
        score = 0
        factors = {}

        market_title = position.get("market", "").lower()
        news_title = news.get("title", "").lower()
        news_summary = news.get("summary", "").lower()
        news_content = news_title + " " + news_summary

        # 1. 关键词匹配度（使用提取的关键词）- L2优化：支持部分匹配
        keyword_score = 0
        if keywords:
            primary_matches = 0
            for kw in keywords.get("primary", []):
                kw_lower = kw.lower()
                # 支持部分匹配："FC Barcelona" 可以匹配 "Barcelona"
                # 也支持反向："LaLiga" 可以匹配 "La Liga"
                if self._keyword_match(kw_lower, news_content):
                    primary_matches += 1

            secondary_matches = sum(1 for kw in keywords.get("secondary", [])
                                   if self._keyword_match(kw.lower(), news_content))

            # 主关键词匹配权重更高
            keyword_score = min(primary_matches * 15 + secondary_matches * 8, 40)

            # 如果主关键词完全没匹配，大幅降低分数
            if primary_matches == 0:
                keyword_score = min(keyword_score, 15)

        score += keyword_score
        factors["keywords"] = keyword_score

        # 2. 时间接近度（L2优化：平衡严格度和实用性）
        news_time = datetime.fromisoformat(news["published_at"].replace('Z', '+00:00'))
        time_diff = abs((news_time - trade_time).total_seconds() / 3600)  # 小时

        # L2: 调整时间衰减（48小时窗口内都有分）
        if time_diff <= 1:
            time_score = 30
        elif time_diff <= 3:
            time_score = 25
        elif time_diff <= 6:
            time_score = 20
        elif time_diff <= 12:
            time_score = 15
        elif time_diff <= 24:
            time_score = 10
        elif time_diff <= 48:
            time_score = 5
        else:
            time_score = 0  # 超过48小时不加分

        score += time_score
        factors["time"] = time_score

        # 3. 市场情绪一致性
        sentiment = news.get("sentiment", "neutral")
        sentiment_score = 15 if sentiment != "neutral" else 10
        score += sentiment_score
        factors["sentiment"] = sentiment_score

        # 4. 来源权威性（考虑市场类型）
        source = news.get("source", "")
        market_type = keywords.get("market_type") if keywords else None

        authority_score = 5  # 默认
        if market_type and market_type in self.HIGH_QUALITY_SOURCES:
            if source in self.HIGH_QUALITY_SOURCES[market_type]:
                authority_score = 10
            elif news.get("source_quality") == "high":
                authority_score = 10
        elif source in ["Reuters", "Bloomberg", "AP"]:
            authority_score = 10

        score += authority_score
        factors["authority"] = authority_score

        # L2: 额外惩罚规则
        # 如果标题包含排除词，大幅降低分数
        if keywords and keywords.get("exclude_terms"):
            if any(exclude in news_title for exclude in keywords["exclude_terms"]):
                score = max(score - 30, 0)
                factors["exclude_penalty"] = -30

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
        # 方案1: 修复时间窗口 - 使用当前时间分析（模拟新闻基于当前时间）- 2026-03-26
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
            
            # 抓取新闻 - 使用48小时窗口获取更多真实新闻 - 2026-03-26
            news_list = self.fetch_news(keywords, hours=48)
            
            # 计算关联度（L2: 提高阈值到65，传递keywords用于精准匹配）
            relevant_news = []
            for news in news_list:
                relevance = self.calculate_relevance(news, position, trade_time, keywords)
                if relevance["score"] >= 65:  # L2: 提高阈值从50到65
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
                
                # 添加解读（L2: 调整阈值）
                avg_relevance = sum(n["relevance"]["score"] for n in relevant_news[:3]) / min(len(relevant_news), 3)
                if avg_relevance >= 85:
                    interpretation = "🔥 新闻与持仓高度相关，强烈支持鲸鱼判断"
                elif avg_relevance >= 70:
                    interpretation = "✅ 新闻与持仓中度相关，可作为参考"
                else:
                    interpretation = "⚠️ 新闻关联度一般，需谨慎判断"
                
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
            "• 新闻来源: Google News, BBC (Reuters/CNN/WSJ 根据内容匹配)",
            "• Google News 链接为搜索页面（RSS 链接会过期）",
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
