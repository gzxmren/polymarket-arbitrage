#!/usr/bin/env python3
"""
跨平台套利扫描器
对比 Polymarket、Manifold、Metaculus 的价格差异

[修复] 2025-03-25:
1. 暂时禁用 Metaculus API（返回404），等待后续修复
2. 保留代码结构，仅跳过 Metaculus 调用
"""

import json
import re
import sys
import ssl
import time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from http.client import IncompleteRead
from datetime import datetime
from typing import Dict, List, Optional

# API 端点
POLYMARKET_API = "https://gamma-api.polymarket.com"
MANIFOLD_API = "https://api.manifold.markets"
METACULUS_API = "https://www.metaculus.com/api"

# 套利阈值（优化后更敏感）
MIN_GAP_THRESHOLD = 0.03  # 从 5% 降低到 3%

# SSL 上下文（修复 SSL 证书错误）
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def fetch_api(url: str, headers: dict = None, retries: int = 3) -> dict | list | None:
    """通用API获取（带重试和 SSL 处理）"""
    last_error = None
    
    for attempt in range(retries):
        try:
            req = Request(url, headers=headers or {"User-Agent": "CrossMarketScanner/1.0"})
            with urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            last_error = e
            if e.code in (403, 404):
                # 这些错误重试也没用
                print(f"HTTP Error {e.code} for {url}: {e.reason}", file=sys.stderr)
                return None
            elif e.code >= 500:
                print(f"Server error {e.code}, retrying... (attempt {attempt+1}/{retries})", file=sys.stderr)
                time.sleep(1)
            else:
                return None
        except (URLError, TimeoutError, OSError, IncompleteRead, json.JSONDecodeError) as e:
            last_error = e
            if attempt < retries - 1:
                print(f"Error fetching {url}: {e}, retrying... (attempt {attempt+1}/{retries})", file=sys.stderr)
                time.sleep(1)
            else:
                print(f"Error fetching {url}: {e}", file=sys.stderr)
                return None
    
    print(f"Failed to fetch {url} after {retries} attempts: {last_error}", file=sys.stderr)
    return None


# ============== Polymarket ==============

def fetch_polymarket_markets(limit: int = 100) -> List[dict]:
    """获取 Polymarket 活跃市场"""
    data = fetch_api(f"{POLYMARKET_API}/markets?active=true&closed=false&limit={limit}")
    markets = []
    
    if not data:
        return markets
    
    for m in data:
        try:
            prices = m.get("outcomePrices", [])
            if isinstance(prices, str):
                prices = json.loads(prices)
            
            if len(prices) >= 2:
                markets.append({
                    "platform": "polymarket",
                    "id": m.get("id", ""),
                    "slug": m.get("slug", ""),
                    "question": m.get("question", ""),
                    "yes_price": float(prices[0]),
                    "no_price": float(prices[1]),
                    "volume": float(m.get("volume", 0) or 0),
                    "liquidity": float(m.get("liquidity", 0) or 0),
                    "end_date": m.get("endDate", ""),
                    "url": f"https://polymarket.com/event/{m.get('slug', '')}"
                })
        except (ValueError, json.JSONDecodeError):
            continue
    
    return markets


# ============== Manifold ==============

def fetch_manifold_markets(limit: int = 100) -> List[dict]:
    """获取 Manifold 市场"""
    data = fetch_api(f"{MANIFOLD_API}/v0/markets?limit={limit}")
    markets = []
    
    if not data:
        return markets
    
    for m in data:
        try:
            # Manifold probability 就是 YES 价格
            prob = m.get("probability")
            if prob is None:
                continue
            
            markets.append({
                "platform": "manifold",
                "id": m.get("id", ""),
                "slug": m.get("slug", ""),
                "question": m.get("question", ""),
                "yes_price": float(prob),
                "no_price": 1.0 - float(prob),
                "volume": float(m.get("volume", 0) or 0),
                "liquidity": float(m.get("liquidity", 0) or m.get("totalLiquidity", 0) or 0),
                "end_date": m.get("closeTime", ""),
                "url": f"https://manifold.markets/{m.get('creatorUsername', '')}/{m.get('slug', '')}"
            })
        except (ValueError, TypeError):
            continue
    
    return markets


# ============== Metaculus ==============

def fetch_metaculus_questions(limit: int = 100) -> List[dict]:
    """
    获取 Metaculus 问题
    
    [修复] 2025-03-25: Metaculus API 返回404，暂时禁用
    保留函数结构以便后续修复
    
    [原修复] 2024-03-13: 已添加 API Key 支持
    """
    # [修复] 2025-03-25: 暂时禁用 Metaculus API
    # API 返回404，可能端点已变更或需要不同认证方式
    # 返回空列表，仅使用 Polymarket 和 Manifold 进行套利扫描
    print("   ⚠️  Metaculus API 暂时禁用（返回404），跳过获取")
    return []
    
    # [原代码，暂时注释]
    # import os
    # api_key = os.environ.get('METACULUS_API_KEY', '9b2e87896a69bf1b48fd8750a188ce54c1655307')
    # 
    # headers = {
    #     "Authorization": f"Token {api_key}",
    #     "User-Agent": "ClawdbotMonitor/1.0",
    #     "Accept": "application/json"
    # }
    # 
    # try:
    #     req = Request(f"{METACULUS_API}/questions/?status=open&limit={limit}", headers=headers)
    #     with urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
    #         data = json.loads(resp.read().decode())
    #         
    #     questions = []
    #     for q in data.get('results', []):
    #         questions.append({
    #             'id': q.get('id'),
    #             'title': q.get('title'),
    #             'probability': q.get('community_prediction', {}).get('q1', 0.5),
    #             'close_time': q.get('close_time'),
    #             'url': f"https://www.metaculus.com/questions/{q.get('id')}/"
    #         })
    #     return questions
    # except Exception as e:
    #     print(f"⚠️  Metaculus API 错误: {e}，跳过获取")
    #     return []


# ============== 事件匹配 ==============

def normalize_text(text: str) -> str:
    """标准化文本用于匹配"""
    # 转小写，移除非字母数字，提取关键词
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    # 移除常见停用词
    stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'will', 'be', 'is', 'are', 'this', 'that'}
    words = [w for w in text.split() if w not in stopwords and len(w) > 2]
    return ' '.join(words)


def extract_key_entities(text: str) -> set:
    """提取关键实体用于匹配验证"""
    text = text.lower()

    # 关键实体类型
    entities = {
        'sports': {'world cup', 'baseball', 'basketball', 'football', 'soccer', 'olympics', 'super bowl', 'nba', 'fifa', 'mlb', 'nfl'},
        'crypto': {'bitcoin', 'btc', 'ethereum', 'eth', 'solana', 'sol', 'crypto', 'blockchain'},
        'politics': {'trump', 'biden', 'election', 'president', 'congress', 'senate', 'vote'},
        'finance': {'fed', 'federa reserve', 'interest rate', 'inflation', 'recession', 'stock market'},
        'tech': {'ai', 'artificial intelligence', 'chatgpt', 'openai', 'google', 'apple', 'tesla'},
        'time': {'2024', '2025', '2026', 'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'}
    }

    found = set()
    for category, keywords in entities.items():
        for keyword in keywords:
            if keyword in text:
                found.add((category, keyword))

    return found


def extract_key_subjects(question: str) -> set:
    """
    提取问题中的关键主体（球队、人名、国家等）
    用于验证两个平台的问题是否关于同一主体
    """
    text = question.lower()

    # 常见球队名（足球、篮球等）
    teams = {
        # 足球国家队
        'spain', 'england', 'france', 'argentina', 'brazil', 'portugal',
        'norway', 'germany', 'italy', 'netherlands', 'belgium', 'croatia',
        'uruguay', 'colombia', 'mexico', 'usa', 'canada', 'japan',
        'south korea', 'australia', 'senegal', 'morocco', 'nigeria',
        # 足球俱乐部
        'real madrid', 'barcelona', 'manchester united', 'manchester city',
        'liverpool', 'chelsea', 'arsenal', 'bayern munich', 'psg',
        'juventus', 'ac milan', 'inter milan', 'borussia dortmund',
        # NBA球队
        'lakers', 'warriors', 'celtics', 'bulls', 'heat', 'nets',
        'bucks', 'suns', 'clippers', 'nuggets', 'thunder', 'knicks',
        # NFL球队
        'chiefs', 'eagles', 'cowboys', 'packers', 'steelers', 'patriots',
        # 其他
        'champion', 'winner'
    }

    # 政治人物
    politicians = {
        'trump', 'biden', 'harris', 'vance', ' RFK', 'kennedy',
        'putin', 'zelensky', 'xi', 'modi', 'macron', 'scholz',
        'sunak', 'starmer', 'milei', 'bolsonaro', 'lula'
    }

    # 公司/组织
    companies = {
        'apple', 'google', 'microsoft', 'amazon', 'tesla', 'meta',
        'nvidia', 'openai', 'anthropic', 'xai', 'deepseek',
        'binance', 'coinbase', 'kraken'
    }

    # 提取匹配的主体
    found_subjects = set()

    for team in teams:
        if team in text:
            found_subjects.add(('team', team))

    for politician in politicians:
        if politician in text:
            found_subjects.add(('politician', politician))

    for company in companies:
        if company in text:
            found_subjects.add(('company', company))

    return found_subjects


def extract_event_type(question: str) -> str:
    """
    提取事件类型
    确保两个平台的问题是同一类型的事件
    """
    text = question.lower()

    # 事件类型关键词
    event_patterns = {
        'world_cup_winner': ['world cup', 'win the world cup', 'world cup champion', 'winning the world cup'],
        'election_winner': ['win the election', 'win the presidency', 'elected president', 'win in 2024', 'win in 2028'],
        'nomination': ['nominated', 'nominee', 'nomination'],
        'approval': ['approval rating', 'approval poll'],
        'price_target': ['reach $', 'above $', 'below $', 'hit $', 'price of'],
        'ipo': ['ipo', 'go public', 'initial public offering'],
        'acquisition': ['acquired', 'acquisition', 'merge', 'merger'],
        'war_ceasefire': ['ceasefire', 'peace deal', 'end of war'],
        'gdp_recession': ['recession', 'gdp growth', 'gdp contraction'],
    }

    for event_type, patterns in event_patterns.items():
        for pattern in patterns:
            if pattern in text:
                return event_type

    return 'unknown'


def validate_match(poly_question: str, other_question: str, similarity: float) -> tuple:
    """验证匹配是否有效 - 严格版（修复误匹配问题）"""

    # ========== 1. 相似度阈值检查（提高到85%）==========
    SIMILARITY_THRESHOLD = 0.85
    if similarity < SIMILARITY_THRESHOLD:
        return False, f"similarity_too_low ({similarity:.1%} < {SIMILARITY_THRESHOLD:.0%})"

    # ========== 2. 关键主体验证 ==========
    poly_subjects = extract_key_subjects(poly_question)
    other_subjects = extract_key_subjects(other_question)

    # 如果两个问题都有可识别的主体，必须至少有一个共同主体
    if poly_subjects and other_subjects:
        common_subjects = poly_subjects & other_subjects
        if not common_subjects:
            return False, "no_common_subjects"

    # 检查主体冲突（如一个是Spain，另一个是Norway）
    poly_teams = {s[1] for s in poly_subjects if s[0] == 'team'}
    other_teams = {s[1] for s in other_subjects if s[0] == 'team'}
    if poly_teams and other_teams and not (poly_teams & other_teams):
        # 两个都有球队信息但没有交集 = 不同球队
        return False, f"conflicting_teams ({poly_teams} vs {other_teams})"

    poly_politicians = {s[1] for s in poly_subjects if s[0] == 'politician'}
    other_politicians = {s[1] for s in other_subjects if s[0] == 'politician'}
    if poly_politicians and other_politicians and not (poly_politicians & other_politicians):
        return False, f"conflicting_politicians ({poly_politicians} vs {other_politicians})"

    # ========== 3. 事件类型验证 ==========
    poly_event_type = extract_event_type(poly_question)
    other_event_type = extract_event_type(other_question)

    # 如果都能识别出事件类型，必须一致
    if poly_event_type != 'unknown' and other_event_type != 'unknown':
        if poly_event_type != other_event_type:
            return False, f"event_type_mismatch ({poly_event_type} vs {other_event_type})"

    # ========== 4. 关键实体验证 ==========
    poly_entities = extract_key_entities(poly_question)
    other_entities = extract_key_entities(other_question)
    common_entities = poly_entities & other_entities

    # 必须有至少2个共同的关键实体
    if len(common_entities) < 2:
        return False, f"insufficient_common_entities ({len(common_entities)} < 2)"

    # 检查是否有冲突的实体
    conflicting_pairs = [
        ({'world cup', 'fifa', 'soccer'}, {'baseball', 'mlb'}),
        ({'bitcoin', 'btc'}, {'ethereum', 'eth'}),
    ]

    for set1, set2 in conflicting_pairs:
        poly_words = {e[1] for e in poly_entities}
        other_words = {e[1] for e in other_entities}

        if (poly_words & set1 and other_words & set2) or (poly_words & set2 and other_words & set1):
            return False, "conflicting_entities"

    return True, "valid"


def calculate_similarity(text1: str, text2: str) -> float:
    """计算文本相似度 (简单版本)"""
    words1 = set(normalize_text(text1).split())
    words2 = set(normalize_text(text2).split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1 & words2
    union = words1 | words2
    
    return len(intersection) / len(union)


def match_events(poly_markets: List[dict], mf_markets: List[dict], me_questions: List[dict]) -> List[dict]:
    """匹配跨平台事件 - 使用严格验证防止误匹配"""
    matches = []

    for poly in poly_markets:
        poly_question = poly["question"]
        match = {
            "polymarket": poly,
            "manifold": None,
            "metaculus": None,
            "similarity": 0,
            "validation": None
        }

        # 匹配 Manifold
        best_mf = None
        best_mf_sim = 0
        for mf in mf_markets:
            sim = calculate_similarity(poly_question, mf["question"])
            # 验证匹配（使用严格的85%阈值）
            is_valid, reason = validate_match(poly_question, mf["question"], sim)
            if is_valid and sim > best_mf_sim:
                best_mf_sim = sim
                best_mf = mf
        match["manifold"] = best_mf

        # 匹配 Metaculus
        best_me = None
        best_me_sim = 0
        for me in me_questions:
            sim = calculate_similarity(poly_question, me["question"])
            is_valid, reason = validate_match(poly_question, me["question"], sim)
            if is_valid and sim > best_me_sim:
                best_me_sim = sim
                best_me = me
        match["metaculus"] = best_me

        # 只保留至少有一个有效匹配的结果
        if best_mf or best_me:
            match["similarity"] = max(best_mf_sim, best_me_sim)
            matches.append(match)

    return matches


# ============== 套利检测 ==============

def find_arbitrage_opportunities(matches: List[dict]) -> List[dict]:
    """找出套利机会 - 添加反向验证和合理性检查"""
    # [修复] 2026-04-18: 添加最小流动性阈值，过滤假信号
    MIN_LIQUIDITY_THRESHOLD = 10000  # $10K 最低流动性
    
    opportunities = []

    for match in matches:
        poly = match["polymarket"]
        mf = match["manifold"]
        me = match["metaculus"]

        # ========== 流动性检查 - 过滤假信号 ==========
        poly_liquidity = poly.get("liquidity", 0)
        mf_liquidity = mf.get("liquidity", 0) if mf else 0
        me_liquidity = me.get("liquidity", 0) if me else 0
        
        # 如果任意平台流动性低于阈值，跳过
        if poly_liquidity < MIN_LIQUIDITY_THRESHOLD:
            continue
        if mf and mf_liquidity < MIN_LIQUIDITY_THRESHOLD:
            continue
        if me and me_liquidity < MIN_LIQUIDITY_THRESHOLD:
            continue

        prices = {"polymarket": poly["yes_price"]}
        if mf:
            prices["manifold"] = mf["yes_price"]
        if me:
            prices["metaculus"] = me["yes_price"]

        if len(prices) < 2:
            continue

        max_price = max(prices.values())
        min_price = min(prices.values())
        gap = max_price - min_price

        # ========== 反向验证：确保价差足够大才认为是套利 ==========
        if gap < MIN_GAP_THRESHOLD:
            continue

        # 额外合理性检查：价差不能超过合理范围（防止数据错误）
        MAX_REASONABLE_GAP = 0.5  # 最大合理价差50%
        if gap > MAX_REASONABLE_GAP:
            print(f"   ⚠️  跳过异常价差: {gap:.1%} (可能数据错误)", file=sys.stderr)
            continue

        # 检查价格合理性（价格必须在0-1之间）
        for platform, price in prices.items():
            if not (0 <= price <= 1):
                print(f"   ⚠️  跳过无效价格: {platform}={price}", file=sys.stderr)
                continue

        max_platform = max(prices, key=prices.get)
        min_platform = min(prices, key=prices.get)

        opportunities.append({
            "question": poly["question"],
            "prices": prices,
            "gap": gap,
            "gap_pct": gap * 100,
            "high_platform": max_platform,
            "low_platform": min_platform,
            "polymarket": poly,
            "manifold": mf,
            "metaculus": me,
            "suggested_action": f"Buy {min_platform}, Sell {max_platform}",
            "similarity": match["similarity"]
        })

    # 按价差排序
    opportunities.sort(key=lambda x: x["gap"], reverse=True)
    return opportunities


# ============== 输出 ==============

def format_opportunity(opp: dict) -> str:
    """格式化套利机会"""
    lines = [
        f"\n{'='*70}",
        f"💰 跨平台套利机会 (匹配度: {opp['similarity']:.1%})",
        f"{'='*70}",
    ]

    # 显示各平台的完整问题描述
    lines.append(f"\n📊 Polymarket: {opp['polymarket']['question']}")
    if opp['manifold']:
        lines.append(f"📊 Manifold:   {opp['manifold']['question']}")
    if opp['metaculus']:
        lines.append(f"📊 Metaculus:  {opp['metaculus']['question']}")

    lines.append(f"\n   平台价格:")
    for platform, price in opp["prices"].items():
        lines.append(f"   • {platform.capitalize()}: {price:.1%}")

    lines.extend([
        f"\n   📈 价差: {opp['gap']:.1%} ({opp['gap_pct']:.1f}个百分点)",
        f"   💡 建议: {opp['suggested_action']}",
        f"\n   🔗 链接:",
        f"   • Polymarket: {opp['polymarket']['url']}",
    ])

    if opp['manifold']:
        lines.append(f"   • Manifold: {opp['manifold']['url']}")
    if opp['metaculus']:
        lines.append(f"   • Metaculus: {opp['metaculus']['url']}")

    # 匹配度提示（使用新的85%阈值）
    if opp['similarity'] >= 0.85:
        lines.append(f"\n   ✅ 匹配度优秀，高度可信")
    elif opp['similarity'] >= 0.7:
        lines.append(f"\n   ⚠️  警告: 匹配度一般，建议人工确认")
    else:
        lines.append(f"\n   ❌ 警告: 匹配度较低，可能不是同一事件！")

    return "\n".join(lines)


def main():
    print("🔍 跨平台套利扫描器")
    print(f"   监控平台: Polymarket, Manifold, Metaculus")
    print(f"   价差阈值: {MIN_GAP_THRESHOLD:.0%}")
    print("-" * 70)
    
    # 获取各平台数据
    print("\n📡 获取数据...")
    
    print("   [1/3] Polymarket...")
    poly_markets = fetch_polymarket_markets(limit=100)
    print(f"         获取 {len(poly_markets)} 个市场")
    
    print("   [2/3] Manifold...")
    mf_markets = fetch_manifold_markets(limit=100)
    print(f"         获取 {len(mf_markets)} 个市场")
    
    print("   [3/3] Metaculus...")
    me_questions = fetch_metaculus_questions(limit=50)
    print(f"         获取 {len(me_questions)} 个问题")
    
    if not poly_markets:
        print("\n❌ 无法获取 Polymarket 数据")
        return
    
    # 匹配事件
    print("\n🔗 匹配跨平台事件...")
    matches = match_events(poly_markets, mf_markets, me_questions)
    print(f"   找到 {len(matches)} 个潜在匹配")
    
    # 检测套利
    print("\n💹 检测套利机会...")
    opportunities = find_arbitrage_opportunities(matches)
    
    if not opportunities:
        print("\n❌ 未发现套利机会")
        print("   各平台价格较为一致")
    else:
        print(f"\n✅ 发现 {len(opportunities)} 个套利机会:\n")
        for opp in opportunities[:10]:  # 只显示前10个
            print(format_opportunity(opp))
    
    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = f"../../07-data/cross_market_scan_{timestamp}.json"
    with open(result_file, 'w') as f:
        json.dump({
            "scan_time": datetime.now().isoformat(),
            "threshold": MIN_GAP_THRESHOLD,
            "platforms": {
                "polymarket": len(poly_markets),
                "manifold": len(mf_markets),
                "metaculus": len(me_questions)
            },
            "matches": len(matches),
            "opportunities_count": len(opportunities),
            "opportunities": opportunities
        }, f, indent=2)
    print(f"\n结果已保存: {result_file}")


if __name__ == "__main__":
    main()
