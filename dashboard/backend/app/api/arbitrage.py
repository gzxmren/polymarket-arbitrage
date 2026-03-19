from flask import Blueprint, jsonify, request
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "models"))
from cross_market_arbitrage import get_recent_opportunities, get_statistics
from ..models.database import db

arbitrage_bp = Blueprint('arbitrage', __name__)

@arbitrage_bp.route('/pair-cost', methods=['GET'])
def get_pair_cost():
    """获取 Pair Cost 套利机会（从数据库）"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 获取查询参数
        limit = request.args.get('limit', 50, type=int)
        min_profit = request.args.get('min_profit', 0.5, type=float)  # 最小利润率
        
        # 查询活跃的 Pair Cost 套利机会
        cursor.execute('''
            SELECT 
                id,
                market_id,
                market_name as market,
                yes_price,
                no_price,
                sum_price as pair_cost,
                profit_potential as profit_pct,
                liquidity,
                detected_at,
                status
            FROM pair_cost_arbitrage 
            WHERE status = 'active' 
              AND profit_potential >= ?
              AND detected_at > datetime('now', '-24 hours')
            ORDER BY profit_potential DESC
            LIMIT ?
        ''', (min_profit, limit))
        
        rows = cursor.fetchall()
        opportunities = []
        
        for row in rows:
            opp = dict(row)
            # 添加 slug 用于跳转
            opp['slug'] = opp.get('market_id', '')
            opportunities.append(opp)
        
        conn.close()
        
        return jsonify({
            'success': True,
            'count': len(opportunities),
            'data': opportunities
        })
        
    except Exception as e:
        print(f"[ERROR] 获取 Pair Cost 套利机会失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'data': []
        }), 500

@arbitrage_bp.route('/cross-market', methods=['GET'])
def get_cross_market():
    """获取跨平台套利机会（从数据库）"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 获取查询参数
        limit = request.args.get('limit', 50, type=int)
        min_gap = request.args.get('min_gap', 5, type=float)  # 最小价差百分比
        
        # 查询活跃的跨平台套利机会
        cursor.execute('''
            SELECT 
                id,
                event_name,
                polymarket_price,
                manifold_price,
                price_gap as gap,
                expected_return,
                risk_level,
                risk_score,
                audit_status,
                match_rate,
                polymarket_url,
                manifold_url,
                created_at
            FROM cross_market_arbitrage 
            WHERE price_gap >= ?
              AND created_at > datetime('now', '-24 hours')
            ORDER BY price_gap DESC
            LIMIT ?
        ''', (min_gap, limit))
        
        rows = cursor.fetchall()
        opportunities = []
        
        for row in rows:
            opp = dict(row)
            # 转换价格为百分比显示
            opp['polymarket_price'] = (opp.get('polymarket_price') or 0) * 100
            opp['manifold_price'] = (opp.get('manifold_price') or 0) * 100
            opp['gap'] = (opp.get('gap') or 0) * 100
            opp['expected_return'] = (opp.get('expected_return') or 0) * 100
            opportunities.append(opp)
        
        conn.close()
        
        return jsonify({
            'success': True,
            'count': len(opportunities),
            'data': opportunities
        })
        
    except Exception as e:
        print(f"[ERROR] 获取跨平台套利机会失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'data': []
        }), 500

@arbitrage_bp.route('/statistics', methods=['GET'])
def get_arbitrage_statistics():
    """获取套利统计信息"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Pair Cost 统计
        cursor.execute('''
            SELECT 
                COUNT(*) as total_count,
                AVG(profit_potential) as avg_profit,
                MAX(profit_potential) as max_profit,
                AVG(liquidity) as avg_liquidity
            FROM pair_cost_arbitrage 
            WHERE status = 'active'
              AND detected_at > datetime('now', '-24 hours')
        ''')
        pair_cost_stats = dict(cursor.fetchone())
        
        # 跨平台套利统计
        cursor.execute('''
            SELECT 
                COUNT(*) as total_count,
                AVG(price_gap) as avg_gap,
                MAX(price_gap) as max_gap,
                AVG(expected_return) as avg_return
            FROM cross_market_arbitrage 
              AND created_at > datetime('now', '-24 hours')
        ''')
        cross_market_stats = dict(cursor.fetchone())
        
        conn.close()
        
        return jsonify({
            'success': True,
            'pair_cost': {
                'count': pair_cost_stats.get('total_count', 0),
                'avg_profit': pair_cost_stats.get('avg_profit', 0) or 0,
                'max_profit': pair_cost_stats.get('max_profit', 0) or 0,
                'avg_liquidity': pair_cost_stats.get('avg_liquidity', 0) or 0
            },
            'cross_market': {
                'count': cross_market_stats.get('total_count', 0),
                'avg_gap': cross_market_stats.get('avg_gap', 0) or 0,
                'max_gap': cross_market_stats.get('max_gap', 0) or 0,
                'avg_return': cross_market_stats.get('avg_return', 0) or 0
            }
        })
        
    except Exception as e:
        print(f"[ERROR] 获取套利统计失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
