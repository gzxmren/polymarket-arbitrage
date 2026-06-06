#!/usr/bin/env python3
"""
市场数据 API
提供活跃市场列表和实时价格数据
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "06-tools/analysis"))

from semantic_arbitrage import SemanticArbitrageEngine, Market as SemanticMarket
from logic_chain_analyzer import LogicChainAnalyzer

markets_bp = Blueprint('markets', __name__)

# 全局实例
semantic_engine = SemanticArbitrageEngine()
logic_analyzer = LogicChainAnalyzer()


def fetch_polymarket_markets(limit: int = 100) -> list:
    """
    从 Polymarket API 获取活跃市场
    
    Returns:
        市场列表
    """
    import urllib.request
    import urllib.error
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    try:
        url = f"{GAMMA_API}/markets?active=true&closed=false&liquidityMin=1000&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "PolymarketDashboard/1.0"})
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            
        markets = []
        for item in data if isinstance(data, list) else data.get('markets', []):
            # 解析价格
            prices = item.get('outcomePrices', [])
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except:
                    prices = []
            
            if len(prices) < 2:
                continue
                
            try:
                yes_price = float(prices[0])
                no_price = float(prices[1])
            except (ValueError, TypeError):
                continue
            
            markets.append({
                'id': item.get('id', 'unknown'),
                'title': item.get('question', 'Unknown'),
                'description': item.get('description', ''),
                'yes_price': yes_price,
                'no_price': no_price,
                'liquidity': float(item.get('liquidity', 0) or 0),
                'volume': float(item.get('volume', 0) or 0),
                'slug': item.get('slug', ''),
                'end_date': item.get('endDate', '')
            })
        
        return markets
    
    except Exception as e:
        print(f"Error fetching markets: {e}")
        return []


@markets_bp.route('/active', methods=['GET'])
def get_active_markets():
    """
    获取活跃市场列表
    
    GET /api/markets/active
    
    Returns:
        活跃市场列表
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        markets = fetch_polymarket_markets(limit)
        
        return jsonify({
            'success': True,
            'count': len(markets),
            'markets': markets,
            'fetched_at': datetime.now(timezone.utc).isoformat()
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@markets_bp.route('/scan', methods=['POST'])
def scan_markets():
    """
    扫描市场，执行语义套利检测
    
    POST /api/markets/scan
    Body: {"markets": [...], "scan_type": "semantic"}
    
    Returns:
        扫描结果
    """
    try:
        data = request.get_json() or {}
        markets_data = data.get('markets', [])
        scan_type = data.get('scan_type', 'semantic')  # semantic, logic, all
        
        if not markets_data:
            # 如果没有提供市场，自动获取
            markets_data = fetch_polymarket_markets(50)
        
        # 转换为 Market 对象
        markets = [
            SemanticMarket(
                id=m['id'],
                title=m['title'],
                description=m.get('description', m['title']),
                yes_price=m['yes_price'],
                no_price=m['no_price'],
                liquidity=m.get('liquidity', 0)
            )
            for m in markets_data
        ]
        
        result = {
            'success': True,
            'scan_time': datetime.now(timezone.utc).isoformat(),
            'markets_scanned': len(markets),
            'scan_type': scan_type
        }
        
        # 执行语义套利扫描
        if scan_type in ['semantic', 'all']:
            semantic_signals = semantic_engine.scan_with_predefined(markets)
            result['semantic_signals'] = [
                {
                    'type': s.type,
                    'subtype': s.subtype,
                    'market_a': s.market_a,
                    'market_b': s.market_b,
                    'price_a': s.price_a,
                    'price_b': s.price_b,
                    'violation': s.violation,
                    'expected_profit': s.expected_profit,
                    'confidence': s.confidence,
                    'reasoning': s.reasoning,
                    'suggested_action': s.suggested_action
                }
                for s in semantic_signals
            ]
        
        # 执行逻辑链分析
        if scan_type in ['logic', 'all']:
            logic_violations = logic_analyzer.scan_all_violations(markets_data)
            result['logic_violations'] = [
                {
                    'violation_type': v.violation_type,
                    'nodes': v.nodes,
                    'expected': v.expected,
                    'actual': v.actual,
                    'severity': v.severity,
                    'profit_potential': v.profit_potential,
                    'description': v.description
                }
                for v in logic_violations
            ]
        
        # 统计
        result['total_opportunities'] = (
            len(result.get('semantic_signals', [])) +
            len(result.get('logic_violations', []))
        )
        
        return jsonify(result)
    
    except Exception as e:
        import traceback
        print(f"Scan error: {e}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@markets_bp.route('/quick-scan', methods=['GET'])
def quick_scan():
    """
    快速扫描 - 自动获取市场并扫描
    
    GET /api/markets/quick-scan?limit=50
    
    Returns:
        扫描结果
    """
    try:
        limit = request.args.get('limit', 50, type=int)
        
        # 获取市场
        markets_data = fetch_polymarket_markets(limit)
        
        if not markets_data:
            return jsonify({
                'success': False,
                'error': 'Failed to fetch markets from Polymarket'
            }), 500
        
        # 转换为 Market 对象
        markets = [
            SemanticMarket(
                id=m['id'],
                title=m['title'],
                description=m.get('description', m['title']),
                yes_price=m['yes_price'],
                no_price=m['no_price'],
                liquidity=m.get('liquidity', 0)
            )
            for m in markets_data
        ]
        
        # 执行扫描
        semantic_signals = semantic_engine.scan_with_predefined(markets)
        logic_violations = logic_analyzer.scan_all_violations(markets_data)
        
        return jsonify({
            'success': True,
            'scan_time': datetime.now(timezone.utc).isoformat(),
            'markets_scanned': len(markets),
            'markets': markets_data[:10],  # 只返回前10个市场详情
            'semantic_signals': [
                {
                    'type': s.type,
                    'subtype': s.subtype,
                    'market_a': s.market_a,
                    'market_b': s.market_b,
                    'price_a': s.price_a,
                    'price_b': s.price_b,
                    'violation': s.violation,
                    'expected_profit': s.expected_profit,
                    'confidence': s.confidence,
                    'reasoning': s.reasoning,
                    'suggested_action': s.suggested_action
                }
                for s in semantic_signals
            ],
            'logic_violations': [
                {
                    'violation_type': v.violation_type,
                    'nodes': v.nodes,
                    'expected': v.expected,
                    'actual': v.actual,
                    'severity': v.severity,
                    'profit_potential': v.profit_potential,
                    'description': v.description
                }
                for v in logic_violations
            ],
            'total_opportunities': len(semantic_signals) + len(logic_violations)
        })
    
    except Exception as e:
        import traceback
        print(f"Quick scan error: {e}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@markets_bp.route('/<market_id>/orderbook', methods=['GET'])
def get_market_orderbook(market_id):
    """
    获取市场订单簿（从CLOB）
    
    GET /api/markets/<market_id>/orderbook
    
    Returns:
        完整订单簿数据（YES和NO）
    """
    try:
        if not market_id:
            return jsonify({'success': False, 'error': 'Missing market_id'}), 400
        
        # 导入CLOB服务
        from ..services.clob_service import get_clob_service
        clob_service = get_clob_service()
        
        # 获取订单簿
        order_book = clob_service.get_market_order_book(market_id)
        if not order_book:
            return jsonify({
                'success': False,
                'error': f'Failed to fetch order book for market {market_id}'
            }), 500
        
        return jsonify({
            'success': True,
            'market_id': market_id,
            'market_name': order_book.get('market_name', ''),
            'slug': order_book.get('slug', ''),
            'yes': order_book.get('yes', {}),
            'no': order_book.get('no', {}),
            'fetched_at': order_book.get('fetched_at', ''),
            'source': 'CLOB'
        })
    
    except Exception as e:
        import traceback
        print(f"Get orderbook error: {e}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@markets_bp.route('/<market_id>/realtime-price', methods=['GET'])
def get_market_realtime_price(market_id):
    """
    获取市场实时价格（从CLOB）
    
    GET /api/markets/<market_id>/realtime-price
    
    Returns:
        实时价格数据
    """
    try:
        if not market_id:
            return jsonify({'success': False, 'error': 'Missing market_id'}), 400
        
        # 导入CLOB服务
        from ..services.clob_service import get_clob_service
        clob_service = get_clob_service()
        
        # 获取实时价格
        pair_cost_data = clob_service.get_pair_cost_prices(market_id)
        if not pair_cost_data:
            return jsonify({
                'success': False,
                'error': f'Failed to fetch real-time prices for market {market_id}'
            }), 500
        
        return jsonify({
            'success': True,
            'market_id': market_id,
            'market_name': pair_cost_data.get('market_name', ''),
            'slug': pair_cost_data.get('slug', ''),
            'yes_price': pair_cost_data.get('yes_price', 0),
            'no_price': pair_cost_data.get('no_price', 0),
            'pair_cost': pair_cost_data.get('pair_cost', 0),
            'profit_potential': pair_cost_data.get('profit_potential', 0),
            'min_depth': pair_cost_data.get('min_depth', 0),
            'fetched_at': pair_cost_data.get('fetched_at', ''),
            'source': 'CLOB'
        })
    
    except Exception as e:
        import traceback
        print(f"Get realtime price error: {e}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500