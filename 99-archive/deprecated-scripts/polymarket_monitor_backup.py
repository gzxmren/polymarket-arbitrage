#!/usr/bin/env python3
"""
Polymarket 持续监控脚本
每5分钟运行一次完整分析
"""

import json
import time
import sys
import ssl
import os
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

API_BASE = "https://gamma-api.polymarket.com"

# ========== 配置参数（优化后） ==========
# [优化1] 降低Pair Cost阈值：0.985 → 0.99，让更多做市机会被捕捉到
PAIR_COST_THRESHOLD = 0.99

# [优化3] 降低鲸鱼检测阈值：$50k → $20k，增加鲸鱼检测灵敏度
WHALE_PROFIT_THRESHOLD = 20000

# [优化3] 增加鲸鱼数量限制，获取更多鲸鱼数据
WHALE_LIMIT = 50

# 创建SSL上下文（忽略证书验证以解决SSL错误）
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# [优化5] 用于跟踪状态变化，减少重复报告
STATE_FILE = "/tmp/polymarket_monitor_state.json"

def load_previous_state():
    """加载上一次的状态，用于对比变化。"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_current_state(state):
    """保存当前状态。"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass

def fetch_api(endpoint: str):
    """Fetch data from Gamma API."""
    url = f"{API_BASE}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "ClawdbotMonitor/1.0"})
        # 使用SSL_CONTEXT忽略证书验证（修复SSL证书错误）
        with urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None

def get_leaderboard():
    """Fetch top traders (whales) from user stats endpoint."""
    # [优化3] 增加limit参数获取更多鲸鱼数据
    data = fetch_api(f"/users?limit={WHALE_LIMIT}&sortBy=profit&sortDirection=desc")
    if data and isinstance(data, list):
        return data
    # 备用：尝试其他可能的端点
    data = fetch_api(f"/leaderboard?limit={WHALE_LIMIT}")
    if data and isinstance(data, list):
        return data
    return []

def analyze_markets():
    """Fetch and analyze active markets."""
    data = fetch_api("/markets?active=true&closed=false&limit=100")
    if not data:
        return None, 0, [], []

    # Keywords for interesting markets
    keywords = ['btc', 'bitcoin', 'eth', 'ethereum', 'sol', 'solana', 'crypto', 'price', 'trump', 'tariff', 'fed', 'rate']

    markets = []
    high_volume_markets = []  # [优化4] 高交易量市场
    new_markets = []  # [优化4] 新上市市场
    now = datetime.now(timezone.utc)

    for m in data:
        q = m.get('question', '').lower()
        volume = m.get('volumeNum', 0)
        volume24h = m.get('volume24hr', 0)
        is_relevant = any(k in q for k in keywords) or volume > 500000

        # 解析创建时间用于检测新市场
        created_at = m.get('createdAt')
        is_new = False
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                days_since_created = (now - created_dt).days
                is_new = days_since_created < 7
            except:
                pass

        # [优化4] 高交易量市场检测（24h > $1M）
        is_high_volume = volume24h > 1000000

        if not is_relevant and not is_high_volume and not is_new:
            continue

        prices = m.get('outcomePrices', '[]')
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except:
                prices = []

        yes_price = float(prices[0]) if len(prices) > 0 else 0
        no_price = float(prices[1]) if len(prices) > 1 else 0
        pair_cost = yes_price + no_price

        market_data = {
            'id': m.get('id'),
            'question': m.get('question'),
            'yes_price': yes_price,
            'no_price': no_price,
            'pair_cost': pair_cost,
            'volume': volume,
            'liquidity': m.get('liquidityNum', 0),
            'spread': m.get('spread', 0),
            'volume24h': volume24h,
            'price_change_1h': m.get('oneHourPriceChange', 0) or 0,
            'price_change_24h': m.get('oneDayPriceChange', 0) or 0,
            'bestBid': m.get('bestBid', 0),
            'bestAsk': m.get('bestAsk', 0),
            'createdAt': created_at,
        }

        markets.append(market_data)

        if is_high_volume:
            high_volume_markets.append(market_data)
        if is_new:
            new_markets.append(market_data)

    return markets, len(data), high_volume_markets, new_markets

def find_arbitrage_opportunities(markets, threshold=PAIR_COST_THRESHOLD):
    """Find pair cost arbitrage opportunities."""
    opportunities = []
    for m in markets:
        if m['pair_cost'] < threshold and m['pair_cost'] > 0:
            profit_pct = (1 - m['pair_cost']) * 100
            opportunities.append({
                **m,
                'profit_pct': profit_pct,
                'max_size': min(m['liquidity'] * 0.1, 5000)  # Conservative estimate
            })
    return sorted(opportunities, key=lambda x: x['profit_pct'], reverse=True)

def find_price_movers(markets, threshold=0.015):
    """Find markets with significant price movement."""
    movers = []
    for m in markets:
        if abs(m['price_change_1h']) >= threshold:
            movers.append(m)
    return sorted(movers, key=lambda x: abs(x['price_change_1h']), reverse=True)

def find_spread_opportunities(markets, min_spread=0.015):
    """Find markets with wide spreads (>1.5%)."""
    spreads = []
    for m in markets:
        if m['spread'] >= min_spread and m['liquidity'] > 1000:
            spreads.append(m)
    return sorted(spreads, key=lambda x: x['spread'], reverse=True)

def count_active_whales():
    """Count active whale wallets from leaderboard."""
    leaderboard = get_leaderboard()
    # [优化3] 使用降低后的阈值检测鲸鱼
    whales = [w for w in leaderboard if w.get('totalProfit', 0) > WHALE_PROFIT_THRESHOLD]
    return len(whales), leaderboard[:10] if leaderboard else []

def has_significant_changes(current, previous):
    """检查是否有显著变化，用于减少重复报告。"""
    if not previous:
        return True
    
    # 检查关键指标是否变化
    checks = [
        current.get('opportunity_count', 0) != previous.get('opportunity_count', 0),
        current.get('mover_count', 0) != previous.get('mover_count', 0),
        current.get('spread_count', 0) != previous.get('spread_count', 0),
        current.get('whale_count', 0) != previous.get('whale_count', 0),
        current.get('high_volume_count', 0) != previous.get('high_volume_count', 0),
        current.get('new_market_count', 0) != previous.get('new_market_count', 0),
    ]
    return any(checks)

def print_analysis(timestamp, total_scanned, markets, opportunities, movers, spreads, 
                   whale_count, top_whales, high_volume_markets, new_markets):
    """Print structured analysis report."""
    
    # [优化5] 构建当前状态用于对比
    current_state = {
        'opportunity_count': len(opportunities),
        'mover_count': len(movers),
        'spread_count': len(spreads),
        'whale_count': whale_count,
        'high_volume_count': len(high_volume_markets),
        'new_market_count': len(new_markets),
    }
    
    previous_state = load_previous_state()
    has_changes = has_significant_changes(current_state, previous_state)
    
    # [优化5] 如果没有变化，只输出简洁状态
    if not has_changes and previous_state:
        print(f"[{timestamp}] 无显著变化 | 市场:{len(markets)} 机会:{len(opportunities)} 鲸鱼:{whale_count}")
        return
    
    # 保存当前状态
    save_current_state(current_state)
    
    print(f"\n{'='*60}")
    print(f"🦐 Polymarket 监控报告 | {timestamp}")
    print(f"{'='*60}")

    print(f"\n📊 市场概览:")
    print(f"  - 扫描市场: {total_scanned}")
    print(f"  - 相关市场: {len(markets)}")
    print(f"  - 总交易量: ${sum(m['volume'] for m in markets):,.0f}")

    # [优化1] 使用新阈值显示做市机会
    print(f"\n💰 做市机会 (Pair Cost < {PAIR_COST_THRESHOLD}): {len(opportunities)} 个")
    if opportunities:
        best = opportunities[0]
        print(f"  ⭐ 最佳机会: {best['question'][:50]}")
        print(f"     - Pair Cost: {best['pair_cost']:.4f}")
        print(f"     - 预期利润: {best['profit_pct']:.2f}%")
        print(f"     - 建议操作: 同时买入 YES + NO")
        print(f"     - 最大仓位: ${best['max_size']:,.0f}")
        
        # [优化5] 只显示前3个机会
        if len(opportunities) > 1:
            print(f"  - 其他机会 ({len(opportunities)-1}个):")
            for opp in opportunities[1:3]:
                print(f"    • {opp['question'][:40]}... | PC: {opp['pair_cost']:.4f} | 利润: {opp['profit_pct']:.2f}%")

    print(f"\n📈 价格异常波动 (1h >1.5%): {len(movers)} 个")
    if movers:
        for m in movers[:3]:
            direction = "📈" if m['price_change_1h'] > 0 else "📉"
            print(f"  {direction} {m['question'][:45]}... | {m['price_change_1h']*100:+.1f}% | YES: ${m['yes_price']:.3f}")

    print(f"\n📊 宽价差机会 (Spread >1.5%): {len(spreads)} 个")
    if spreads:
        for s in spreads[:3]:
            print(f"  - {s['question'][:45]}... | Spread: {s['spread']*100:.2f}% | Vol: ${s['volume']:,.0f}")

    # [优化4] 新增：高交易量市场
    print(f"\n🔥 高交易量市场 (24h >$1M): {len(high_volume_markets)} 个")
    if high_volume_markets:
        for hv in high_volume_markets[:3]:
            print(f"  - {hv['question'][:45]}... | 24h: ${hv['volume24h']:,.0f} | Vol: ${hv['volume']:,.0f}")

    # [优化4] 新增：新上市市场
    print(f"\n🆕 新上市市场 (<7天): {len(new_markets)} 个")
    if new_markets:
        for nm in new_markets[:3]:
            print(f"  - {nm['question'][:45]}... | YES: ${nm['yes_price']:.3f} | Liquidity: ${nm['liquidity']:,.0f}")

    # [优化3] 改进鲸鱼活动显示
    print(f"\n🐋 鲸鱼活动 (利润>${WHALE_PROFIT_THRESHOLD/1000:.0f}k):")
    print(f"  - 活跃鲸鱼数量: {whale_count}")
    if top_whales:
        print(f"  - 顶级鲸鱼 (前3):")
        for i, w in enumerate(top_whales[:3], 1):
            profit = w.get('totalProfit', 0)
            win_rate = w.get('winRate', 0) * 100
            print(f"    {i}. {w.get('username', 'Unknown')[:15]} | 利润: ${profit:,.0f} | 胜率: {win_rate:.1f}%")

    print(f"\n💡 建议操作:")
    if opportunities:
        print(f"  1. 优先关注做市机会: {opportunities[0]['question'][:40]}...")
    if spreads:
        print(f"  2. 考虑在宽价差市场做市: {spreads[0]['question'][:40]}...")
    if movers:
        print(f"  3. 监控价格异动: {movers[0]['question'][:40]}...")
    if high_volume_markets:
        print(f"  4. 关注高交易量市场: {high_volume_markets[0]['question'][:40]}...")
    if new_markets:
        print(f"  5. 研究新市场机会: {new_markets[0]['question'][:40]}...")
    if not opportunities and not spreads and not movers:
        print("  - 当前无明显机会，继续监控")

    print(f"\n{'='*60}")

def main():
    """Main monitoring loop."""
    print("🦐 Polymarket 持续监控启动...")
    print(f"  - Pair Cost阈值: {PAIR_COST_THRESHOLD}")
    print(f"  - 鲸鱼利润阈值: ${WHALE_PROFIT_THRESHOLD/1000:.0f}k")
    print("按 Ctrl+C 停止")

    iteration = 0
    while True:
        iteration += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        try:
            # Fetch and analyze
            markets, total_scanned, high_volume_markets, new_markets = analyze_markets()
            if not markets:
                print(f"[{timestamp}] 无法获取市场数据，5分钟后重试...")
                time.sleep(300)
                continue

            # Find opportunities
            opportunities = find_arbitrage_opportunities(markets)
            movers = find_price_movers(markets)
            spreads = find_spread_opportunities(markets)

            # Get whale data
            whale_count, top_whales = count_active_whales()

            # Print report
            print_analysis(timestamp, total_scanned, markets, opportunities, movers, spreads, 
                          whale_count, top_whales, high_volume_markets, new_markets)

        except Exception as e:
            print(f"[{timestamp}] 错误: {e}")

        # Wait 5 minutes
        print(f"\n⏱️ 下次分析: 5分钟后... (迭代 #{iteration})")
        time.sleep(300)

if __name__ == "__main__":
    main()
