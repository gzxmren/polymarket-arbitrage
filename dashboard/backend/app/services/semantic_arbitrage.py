#!/usr/bin/env python3
"""
语义套利引擎 - Phase 2 优化版
集成真实 CLOB API，提高扫描质量
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# 添加分析工具路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "06-tools/analysis"))

# 导入CLOB API
try:
    from clob_api import (
        get_markets_with_order_book,
        get_order_book,
        calculate_spread_from_order_book
    )
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    print("⚠️ CLOB API 不可用")

# 导入Gamma API（获取市场元数据）
try:
    from cross_market_scanner import fetch_polymarket_markets
    GAMMA_AVAILABLE = True
except ImportError:
    GAMMA_AVAILABLE = False
    print("⚠️ Gamma API 不可用")


class SemanticArbitrageEngine:
    """语义套利引擎 - 检测定价矛盾和套利机会"""
    
    def __init__(self):
        self.clob_available = CLOB_AVAILABLE
        self.gamma_available = GAMMA_AVAILABLE
        self.min_spread_threshold = 0.015  # 1.5% 最小价差
        self.min_depth_threshold = 5000    # $5000 最小深度
        self.min_price_threshold = 0.05    # 最小价格 5%
        self.max_price_threshold = 0.95    # 最大价格 95%
    
    def scan_market_making_opportunities(self, limit: int = 50) -> List[Dict]:
        """
        扫描做市机会 - 使用真实CLOB订单簿数据
        
        Args:
            limit: 扫描市场数量
            
        Returns:
            做市机会列表
        """
        if not self.clob_available:
            print("⚠️ CLOB API 不可用，无法扫描做市机会")
            return []
        
        print(f"\n📡 从CLOB获取 {limit} 个市场...")
        markets = get_markets_with_order_book(limit=limit)
        print(f"   找到 {len(markets)} 个启用了订单簿的市场")
        
        opportunities = []
        
        for market in markets[:limit]:
            try:
                opp = self._analyze_market(market)
                if opp:
                    opportunities.append(opp)
            except Exception as e:
                print(f"   ⚠️ 分析市场失败 {market.get('slug', 'unknown')}: {e}")
                continue
        
        # 按价差排序
        opportunities.sort(key=lambda x: x.get('spread_pct', 0), reverse=True)
        
        print(f"\n✅ 发现 {len(opportunities)} 个做市机会")
        return opportunities
    
    def _analyze_market(self, market: Dict) -> Optional[Dict]:
        """分析单个市场"""
        # 获取YES token订单簿
        order_book = get_order_book(market['yes_token'])
        if not order_book:
            return None
        
        # 计算价差
        spread_info = calculate_spread_from_order_book(order_book)
        if not spread_info:
            return None
        
        best_bid = spread_info.get('best_bid', 0)
        best_ask = spread_info.get('best_ask', 1)
        spread_pct = spread_info.get('spread_pct', 0)
        min_depth = spread_info.get('min_depth', 0)
        
        # 过滤条件
        if spread_pct < self.min_spread_threshold:
            return None
        if min_depth < self.min_depth_threshold:
            return None
        if best_bid < self.min_price_threshold or best_bid > self.max_price_threshold:
            return None
        
        # 计算预期利润（假设能吃到40%价差）
        expected_profit = spread_pct * 0.4
        
        return {
            'market_id': market.get('id', ''),
            'slug': market.get('slug', ''),
            'question': market.get('question', ''),
            'yes_token': market['yes_token'],
            'no_token': market['no_token'],
            'best_bid': best_bid,
            'best_ask': best_ask,
            'spread': spread_info.get('spread', 0),
            'spread_pct': spread_pct,
            'bid_depth': spread_info.get('bid_depth', 0),
            'ask_depth': spread_info.get('ask_depth', 0),
            'min_depth': min_depth,
            'expected_profit': expected_profit,
            'liquidity': market.get('liquidity', 0),
            'volume': market.get('volume', 0),
            'data_source': 'real_clob',
            'detected_at': datetime.now().isoformat()
        }
    
    def detect_pricing_contradictions(self, markets: List[Dict]) -> List[Dict]:
        """
        检测定价矛盾
        
        检测逻辑：
        1. 同一事件的不同市场（如Trump选举的不同维度）
        2. 价格之和不为1的互补市场
        3. 时间序列上的价格反转
        
        Args:
            markets: 市场列表
            
        Returns:
            定价矛盾列表
        """
        contradictions = []
        
        # 1. 检测互补市场价格矛盾
        complementary_contradictions = self._detect_complementary_contradictions(markets)
        contradictions.extend(complementary_contradictions)
        
        # 2. 检测相关事件价格矛盾
        related_contradictions = self._detect_related_event_contradictions(markets)
        contradictions.extend(related_contradictions)
        
        return contradictions
    
    def _detect_complementary_contradictions(self, markets: List[Dict]) -> List[Dict]:
        """检测互补市场（YES/NO）价格矛盾"""
        contradictions = []
        
        for market in markets:
            try:
                # 获取价格
                outcome_prices = market.get('outcomePrices', '[0.5, 0.5]')
                if isinstance(outcome_prices, str):
                    import json
                    outcome_prices = json.loads(outcome_prices)
                
                if len(outcome_prices) >= 2:
                    yes_price = float(outcome_prices[0])
                    no_price = float(outcome_prices[1])
                    
                    # 检测Pair Cost矛盾（价格之和 < 0.995）
                    pair_cost = yes_price + no_price
                    if pair_cost < 0.995:
                        profit_potential = (1 - pair_cost) / pair_cost * 100
                        contradictions.append({
                            'type': 'pair_cost',
                            'market_id': market.get('id', ''),
                            'slug': market.get('slug', ''),
                            'question': market.get('question', ''),
                            'yes_price': yes_price,
                            'no_price': no_price,
                            'pair_cost': pair_cost,
                            'profit_potential': profit_potential,
                            'severity': 'high' if profit_potential > 2 else 'medium',
                            'detected_at': datetime.now().isoformat()
                        })
                    
                    # 检测定价过高（价格之和 > 1.005）
                    elif pair_cost > 1.005:
                        contradictions.append({
                            'type': 'overpriced',
                            'market_id': market.get('id', ''),
                            'slug': market.get('slug', ''),
                            'question': market.get('question', ''),
                            'yes_price': yes_price,
                            'no_price': no_price,
                            'pair_cost': pair_cost,
                            'severity': 'medium',
                            'detected_at': datetime.now().isoformat()
                        })
            
            except Exception as e:
                continue
        
        return contradictions
    
    def _detect_related_event_contradictions(self, markets: List[Dict]) -> List[Dict]:
        """检测相关事件价格矛盾"""
        contradictions = []
        
        # 关键词分组检测
        keyword_groups = {
            'trump': ['trump', 'donald trump'],
            'biden': ['biden', 'joe biden'],
            'election': ['election', 'president', 'vote'],
            'crypto': ['bitcoin', 'btc', 'crypto', 'ethereum'],
            'war': ['war', 'conflict', 'israel', 'iran', 'ukraine']
        }
        
        for group_name, keywords in keyword_groups.items():
            group_markets = []
            
            for market in markets:
                question = market.get('question', '').lower()
                if any(kw in question for kw in keywords):
                    group_markets.append(market)
            
            # 如果同一组内有多个市场，检测价格矛盾
            if len(group_markets) >= 2:
                prices = []
                for m in group_markets:
                    try:
                        outcome_prices = m.get('outcomePrices', '[0.5, 0.5]')
                        if isinstance(outcome_prices, str):
                            outcome_prices = json.loads(outcome_prices)
                        yes_price = float(outcome_prices[0])
                        prices.append({
                            'market': m,
                            'yes_price': yes_price
                        })
                    except:
                        continue
                
                # 检测价格差异过大的情况
                if len(prices) >= 2:
                    prices.sort(key=lambda x: x['yes_price'])
                    min_price = prices[0]['yes_price']
                    max_price = prices[-1]['yes_price']
                    price_gap = max_price - min_price
                    
                    # 如果同一主题市场价格差异 > 20%，可能存在矛盾
                    if price_gap > 0.20:
                        contradictions.append({
                            'type': 'related_event_gap',
                            'group': group_name,
                            'markets': [
                                {
                                    'question': p['market'].get('question', '')[:80],
                                    'price': p['yes_price'],
                                    'slug': p['market'].get('slug', '')
                                }
                                for p in prices
                            ],
                            'price_gap': price_gap,
                            'gap_pct': price_gap * 100,
                            'severity': 'high' if price_gap > 0.30 else 'medium',
                            'detected_at': datetime.now().isoformat()
                        })
        
        return contradictions
    
    def calculate_implied_probability(self, market: Dict) -> Dict:
        """
        计算隐含概率
        
        基于订单簿深度加权计算
        """
        if not self.clob_available:
            return {'implied_prob': 0.5, 'confidence': 0}
        
        try:
            token_id = market.get('yes_token', '')
            if not token_id:
                return {'implied_prob': 0.5, 'confidence': 0}
            
            order_book = get_order_book(token_id)
            if not order_book:
                return {'implied_prob': 0.5, 'confidence': 0}
            
            bids = order_book.get('bids', [])
            asks = order_book.get('asks', [])
            
            if not bids or not asks:
                return {'implied_prob': 0.5, 'confidence': 0}
            
            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 1
            
            # 中价作为隐含概率
            mid_price = (best_bid + best_ask) / 2
            
            # 基于价差计算置信度
            spread = best_ask - best_bid
            confidence = max(0, 1 - spread * 10)  # 价差越小，置信度越高
            
            # 基于深度计算置信度
            bid_depth = sum(b[1] for b in bids[:5])
            ask_depth = sum(a[1] for a in asks[:5])
            min_depth = min(bid_depth, ask_depth)
            depth_confidence = min(1, min_depth / 10000)  # $10k深度为满置信
            
            # 综合置信度
            final_confidence = (confidence + depth_confidence) / 2
            
            return {
                'implied_prob': mid_price,
                'best_bid': best_bid,
                'best_ask': best_ask,
                'spread': spread,
                'confidence': final_confidence,
                'bid_depth': bid_depth,
                'ask_depth': ask_depth
            }
        
        except Exception as e:
            return {'implied_prob': 0.5, 'confidence': 0, 'error': str(e)}
    
    def get_scan_summary(self, opportunities: List[Dict], contradictions: List[Dict]) -> Dict:
        """获取扫描摘要"""
        return {
            'timestamp': datetime.now().isoformat(),
            'clob_available': self.clob_available,
            'gamma_available': self.gamma_available,
            'market_making': {
                'count': len(opportunities),
                'avg_spread': sum(o.get('spread_pct', 0) for o in opportunities) / len(opportunities) if opportunities else 0,
                'max_spread': max((o.get('spread_pct', 0) for o in opportunities), default=0),
                'total_liquidity': sum(o.get('liquidity', 0) for o in opportunities)
            },
            'contradictions': {
                'count': len(contradictions),
                'by_type': {
                    'pair_cost': len([c for c in contradictions if c.get('type') == 'pair_cost']),
                    'overpriced': len([c for c in contradictions if c.get('type') == 'overpriced']),
                    'related_event_gap': len([c for c in contradictions if c.get('type') == 'related_event_gap'])
                },
                'high_severity': len([c for c in contradictions if c.get('severity') == 'high'])
            }
        }


# 全局引擎实例
engine = SemanticArbitrageEngine()


def scan_semantic_arbitrage(limit: int = 50) -> Dict:
    """
    扫描语义套利机会
    
    这是主入口函数，供API调用
    """
    print("=" * 70)
    print("🔍 语义套利引擎扫描")
    print("=" * 70)
    
    # 1. 扫描做市机会
    opportunities = engine.scan_market_making_opportunities(limit=limit)
    
    # 2. 获取市场数据用于矛盾检测
    markets = []
    if GAMMA_AVAILABLE:
        markets = fetch_polymarket_markets(limit=limit)
    
    # 3. 检测定价矛盾
    contradictions = engine.detect_pricing_contradictions(markets)
    
    # 4. 生成摘要
    summary = engine.get_scan_summary(opportunities, contradictions)
    
    print(f"\n📊 扫描完成:")
    print(f"   做市机会: {summary['market_making']['count']}")
    print(f"   定价矛盾: {summary['contradictions']['count']}")
    print(f"   高严重度: {summary['contradictions']['high_severity']}")
    
    return {
        'success': True,
        'summary': summary,
        'market_making_opportunities': opportunities,
        'pricing_contradictions': contradictions
    }


if __name__ == "__main__":
    # 测试运行
    result = scan_semantic_arbitrage(limit=20)
    print("\n" + "=" * 70)
    print("测试结果:")
    print(json.dumps(result['summary'], indent=2, default=str))
