#!/usr/bin/env python3
"""
Pair Cost 套利扫描器
扫描所有活跃市场，找出 YES+NO < $1 的套利机会
"""

import json
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

# 套利阈值（V2优化：更宽松以发现更多机会）
PAIR_COST_THRESHOLD = 0.995  # V2: 放宽到0.995，增加机会发现
MIN_LIQUIDITY = 100          # V2: 降低到100，小市场也有价值


def fetch_api(base_url: str, endpoint: str) -> dict | list | None:
    """获取API数据"""
    url = f"{base_url}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "PolymarketScanner/1.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def fetch_active_markets(limit: int = 100) -> list:
    """获取活跃市场列表"""
    data = fetch_api(GAMMA_API, f"/markets?active=true&closed=false&limit={limit}")
    return data if data else []


def calculate_pair_cost(market: dict) -> dict | None:
    """计算市场的 Pair Cost"""
    prices = market.get("outcomePrices", [])
    
    # 处理字符串格式的JSON数组
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            return None
    
    if len(prices) < 2:
        return None
    
    try:
        yes_price = float(prices[0])
        no_price = float(prices[1])
    except (ValueError, TypeError):
        return None
    
    pair_cost = yes_price + no_price
    
    # 获取流动性
    liquidity = float(market.get("liquidity", 0) or 0)
    volume = float(market.get("volume", 0) or 0)
    
    return {
        "market_id": market.get("id", "unknown"),
        "slug": market.get("slug", ""),
        "question": market.get("question", "Unknown"),
        "yes_price": yes_price,
        "no_price": no_price,
        "pair_cost": pair_cost,
        "profit_margin": 1.0 - pair_cost,
        "profit_pct": (1.0 - pair_cost) * 100,
        "liquidity": liquidity,
        "volume": volume,
        "end_date": market.get("endDate", ""),
        "is_opportunity": pair_cost < PAIR_COST_THRESHOLD and liquidity >= MIN_LIQUIDITY
    }


def scan_pair_cost_opportunities(limit: int = 100) -> list:
    """扫描所有市场的 Pair Cost 套利机会"""
    markets = fetch_active_markets(limit)
    opportunities = []
    
    print(f"扫描 {len(markets)} 个活跃市场...")
    
    for market in markets:
        result = calculate_pair_cost(market)
        if result and result["is_opportunity"]:
            opportunities.append(result)
    
    # 按利润率排序
    opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)
    return opportunities


def format_opportunity(opp: dict) -> str:
    """格式化输出套利机会"""
    lines = [
        f"\n{'='*60}",
        f"📊 市场: {opp['question'][:60]}",
        f"{'='*60}",
        f"   YES价格: ${opp['yes_price']:.4f}",
        f"   NO价格:  ${opp['no_price']:.4f}",
        f"   Pair Cost: ${opp['pair_cost']:.4f}",
        f"   💰 利润空间: {opp['profit_pct']:.2f}%",
        f"   💧 流动性: ${opp['liquidity']:,.0f}",
        f"   📈 交易量: ${opp['volume']:,.0f}",
    ]
    
    if opp['end_date']:
        lines.append(f"   ⏰ 结算时间: {opp['end_date']}")
    
    lines.append(f"   🔗 链接: https://polymarket.com/event/{opp['slug']}")
    
    return "\n".join(lines)


def save_results(opportunities: list, filename: str = None):
    """保存结果到文件"""
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"../../07-data/pair_cost_opportunities_{timestamp}.json"
    
    with open(filename, 'w') as f:
        json.dump({
            "scan_time": datetime.now().isoformat(),
            "threshold": PAIR_COST_THRESHOLD,
            "min_liquidity": MIN_LIQUIDITY,
            "opportunities_count": len(opportunities),
            "opportunities": opportunities
        }, f, indent=2)
    
    print(f"\n结果已保存: {filename}")


def main():
    print("🔍 Pair Cost 套利扫描器")
    print(f"   阈值: < ${PAIR_COST_THRESHOLD}")
    print(f"   最低流动性: ${MIN_LIQUIDITY:,.0f}")
    print("-" * 60)
    
    opportunities = scan_pair_cost_opportunities(limit=200)
    
    if not opportunities:
        print("\n❌ 未发现套利机会")
        print("   市场当前比较有效，Pair Cost 都接近 $1.00")
    else:
        print(f"\n✅ 发现 {len(opportunities)} 个套利机会:\n")
        for opp in opportunities:
            print(format_opportunity(opp))
        
        # 保存结果
        save_results(opportunities)
    
    # 同时输出统计信息
    all_markets = fetch_active_markets(200)
    pair_costs = []
    for m in all_markets:
        result = calculate_pair_cost(m)
        if result:
            pair_costs.append(result["pair_cost"])
    
    if pair_costs:
        avg_pair_cost = sum(pair_costs) / len(pair_costs)
        min_pair_cost = min(pair_costs)
        print(f"\n{'='*60}")
        print("📈 市场统计:")
        print(f"   扫描市场数: {len(pair_costs)}")
        print(f"   平均 Pair Cost: ${avg_pair_cost:.4f}")
        print(f"   最低 Pair Cost: ${min_pair_cost:.4f}")


if __name__ == "__main__":
    main()
