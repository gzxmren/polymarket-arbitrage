from flask import Blueprint, jsonify, request
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "models"))
from cross_market_arbitrage import get_recent_opportunities, get_statistics
from ..models.database import db
from ..services.clob_service import get_clob_service

arbitrage_bp = Blueprint('arbitrage', __name__)

# CLOB服务实例
clob_service = get_clob_service()


@arbitrage_bp.route('/feedback', methods=['POST'])
def submit_arbitrage_feedback():
    """提交套利反馈（人工确认）"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        arbitrage_id = data.get('arbitrage_id')
        arbitrage_type = data.get('arbitrage_type')  # 'pair_cost' 或 'cross_market'
        feedback_type = data.get('feedback_type')    # 'confirmed' 或 'error'
        feedback_text = data.get('feedback_text', '')
        user_notes = data.get('user_notes', '')
        
        if not all([arbitrage_id, arbitrage_type, feedback_type]):
            return jsonify({
                'success': False, 
                'error': 'Missing required fields: arbitrage_id, arbitrage_type, feedback_type'
            }), 400
        
        if feedback_type not in ['confirmed', 'error']:
            return jsonify({
                'success': False,
                'error': 'feedback_type must be "confirmed" or "error"'
            }), 400
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 插入反馈
        cursor.execute('''
            INSERT INTO arbitrage_feedback 
            (arbitrage_id, arbitrage_type, feedback_type, feedback_text, user_notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (arbitrage_id, arbitrage_type, feedback_type, feedback_text, user_notes, datetime.now().isoformat()))
        
        feedback_id = cursor.lastrowid
        
        # 更新对应套利记录的状态
        if arbitrage_type == 'pair_cost':
            new_status = 'confirmed' if feedback_type == 'confirmed' else 'error'
            cursor.execute('''
                UPDATE pair_cost_arbitrage 
                SET status = ? 
                WHERE id = ?
            ''', (new_status, arbitrage_id))
        elif arbitrage_type == 'cross_market':
            new_status = 'confirmed' if feedback_type == 'confirmed' else 'error'
            cursor.execute('''
                UPDATE cross_market_arbitrage 
                SET audit_status = ? 
                WHERE id = ?
            ''', (new_status, arbitrage_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'feedback_id': feedback_id,
            'message': f'Feedback submitted: {feedback_type}'
        })
        
    except Exception as e:
        print(f"[ERROR] 提交套利反馈失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@arbitrage_bp.route('/feedback/<int:arbitrage_id>', methods=['GET'])
def get_arbitrage_feedback(arbitrage_id):
    """获取套利反馈记录"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM arbitrage_feedback 
            WHERE arbitrage_id = ?
            ORDER BY created_at DESC
        ''', (arbitrage_id,))
        
        rows = cursor.fetchall()
        feedback_list = [dict(row) for row in rows]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'arbitrage_id': arbitrage_id,
            'count': len(feedback_list),
            'feedback': feedback_list
        })
        
    except Exception as e:
        print(f"[ERROR] 获取套利反馈失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@arbitrage_bp.route('/compare', methods=['POST'])
def compare_arbitrage_prices():
    """
    对比套利价格
    实时获取最新价格进行对比
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        arbitrage_type = data.get('arbitrage_type')
        market_id = data.get('market_id')
        event_name = data.get('event_name')
        
        if not arbitrage_type:
            return jsonify({'success': False, 'error': 'Missing arbitrage_type'}), 400
        
        result = {
            'arbitrage_type': arbitrage_type,
            'comparison_time': datetime.now().isoformat(),
            'prices': {}
        }
        
        if arbitrage_type == 'pair_cost':
            # 从CLOB API获取Pair Cost实时价格
            if not market_id:
                return jsonify({'success': False, 'error': 'Missing market_id for pair_cost'}), 400
            
            pair_cost_data = clob_service.get_pair_cost_prices(market_id)
            if not pair_cost_data:
                return jsonify({
                    'success': False, 
                    'error': f'Failed to fetch CLOB prices for market {market_id}'
                }), 500
            
            result['prices'] = {
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
            }
            
        elif arbitrage_type == 'cross_market':
            # 获取跨平台最新价格
            result['prices'] = {
                'message': 'Cross-market comparison - real-time prices would be fetched from Polymarket and Manifold APIs',
                'event_name': event_name
            }
        
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        print(f"[ERROR] 对比套利价格失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@arbitrage_bp.route('/realtime-price/<market_id>', methods=['GET'])
def get_realtime_price(market_id):
    """
    获取市场实时价格
    
    GET /api/arbitrage/realtime-price/<market_id>
    
    Returns:
        实时价格数据（从CLOB获取）
    """
    try:
        if not market_id:
            return jsonify({'success': False, 'error': 'Missing market_id'}), 400
        
        # 从CLOB获取实时价格
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
        print(f"[ERROR] 获取实时价格失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

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
            WHERE created_at > datetime('now', '-24 hours')
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
