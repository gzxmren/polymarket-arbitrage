#!/usr/bin/env python3
"""
跨平台套利扫描器
对比 Polymarket、Manifold、Metaculus 的价格差异
"""

import json
import re
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime
from typing import Dict, List, Optional

# API 端点
POLYMARKET_API = "https://gamma-api.polymarket.com"
MANIFOLD_API = "https://api.manifold.markets"
METACULUS_API = "https://www.metaculus.com/api"

# 套利阈值（优化后更敏感）
MIN_GAP_THRESHOLD = 0.03  # 从 5% 降低到 3%


def fetch_api(url: str, headers: dict = None) -> dict | list | None:
    """通用API获取"""
    try:
        req = Request(url, headers=headers or {"User-Agent": "CrossMarketScanner/1.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
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
    """获取 Metaculus 问题"""
    # Metaculus API 需要搜索或获取开放问题
    data = fetch_api(f"{METACULUS_API}/questions/?status=open&limit={limit}")
    questions = []
    
    if not data:
        return questions
    
    # API 返回结构可能不同
    results = data.get("results", []) if isinstance(data, dict) else data
    
    for q in results:
        try:
            # Metaculus 预测值
            prediction = q.get("prediction", q.get("community_prediction", None))
            if prediction is None:
                continue
            
            # prediction 可能是字典或数值
            if isinstance(prediction, dict):
                yes_prob = prediction.get("yes", prediction.get("value", 0.5))
            else:
                yes_prob = float(prediction)
            
            questions.append({
                "platform": "metaculus",
                "id": q.get("id", ""),
                "slug": q.get("slug", ""),
                "question": q.get("title", q.get("question", "")),
                "yes_price": yes_prob,
                "no_price": 1.0 - yes_prob,
                "volume": 0,  # Metaculus 无交易量概念
                "liquidity": 0,
                "end_date": q.get("close_time", q.get("resolve_time", "")),
                "url": f"https://www.metaculus.com/questions/{q.get('id', '')}"
            })
        except (ValueError, TypeError):
            continue
    
    return questions


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


def validate_match(poly_question: str, other_question: str, similarity: float) -> tuple:
    """验证匹配是否有效"""
    # 提取关键实体
    poly_entities = extract_key_entities(poly_question)
    other_entities = extract_key_entities(other_question)
    
    # 检查是否有共同的实体
    common_entities = poly_entities & other_entities
    
    # 检查是否有冲突的实体（如足球 vs 棒球）
    conflicting_pairs = [
        ({'world cup', 'fifa', 'soccer'}, {'baseball', 'mlb'}),
        ({'bitcoin', 'btc'}, {'ethereum', 'eth'}),
        ({'trump'}, {'biden'}),
    ]
    
    has_conflict = False
    for set1, set2 in conflicting_pairs:
        poly_words = {e[1] for e in poly_entities}
        other_words = {e[1] for e in other_entities}
        
        if (poly_words & set1 and other_words & set2) or (poly_words & set2 and other_words & set1):
            has_conflict = True
            break
    
    # 验证规则
    if has_conflict:
        return False, "conflicting_entities"
    
    if similarity < 0.5 and len(common_entities) < 2:
        return False, "insufficient_common_entities"
    
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
    """匹配跨平台事件"""
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
            if sim > best_mf_sim:
                # 验证匹配
                is_valid, reason = validate_match(poly_question, mf["question"], sim)
                if is_valid and sim > 0.3:
                    best_mf_sim = sim
                    best_mf = mf
        match["manifold"] = best_mf
        
        # 匹配 Metaculus
        best_me = None
        best_me_sim = 0
        for me in me_questions:
            sim = calculate_similarity(poly_question, me["question"])
            if sim > best_me_sim:
                is_valid, reason = validate_match(poly_question, me["question"], sim)
                if is_valid and sim > 0.3:
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
    """找出套利机会"""
    opportunities = []
    
    for match in matches:
        poly = match["polymarket"]
        mf = match["manifold"]
        me = match["metaculus"]
        
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
        
        if gap >= MIN_GAP_THRESHOLD:
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
    
    # 风险提示
    if opp['similarity'] < 0.5:
        lines.append(f"\n   ⚠️  警告: 匹配度较低，请人工确认是同一事件！")
    
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
