from flask import Blueprint, jsonify, request
from datetime import datetime, timezone
from ..models.database import db
from ..services.whale_analyzer import analyzer
from ..services.whale_deep_analyzer import WhaleDeepAnalyzer

whales_bp = Blueprint('whales', __name__)

@whales_bp.route('/', methods=['GET'])
def get_whales():
    """获取鲸鱼列表（支持多维度排序）"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 查询参数
    is_watched = request.args.get('is_watched', None)
    sort_by = request.args.get('sort_by', 'total_value')
    sort_order = request.args.get('sort_order', 'desc')
    limit = request.args.get('limit', 100, type=int)  # 默认返回100个，支持显示全部关注鲸鱼
    
    query = 'SELECT * FROM whales WHERE 1=1'
    params = []
    
    if is_watched is not None:
        query += ' AND is_watched = ?'
        params.append(is_watched == 'true')
    
    # 排序
    valid_sort_fields = ['total_value', 'position_count', 'top5_ratio', 'last_updated', 'total_pnl', 'changes_count']
    if sort_by in valid_sort_fields:
        order = 'DESC' if sort_order == 'desc' else 'ASC'
        query += f' ORDER BY {sort_by} {order}'
    
    query += ' LIMIT ?'
    params.append(limit)
    
    cursor.execute(query, params)
    whales = [dict(row) for row in cursor.fetchall()]
    
    # 为每个鲸鱼添加变动次数（从changes表统计）
    for whale in whales:
        cursor.execute('''
            SELECT COUNT(*) as count FROM changes 
            WHERE wallet = ? AND timestamp > datetime('now', '-1 day')
        ''', (whale['wallet'],))
        result = cursor.fetchone()
        whale['changes_count'] = result['count'] if result else 0
    
    conn.close()
    
    return jsonify({
        'count': len(whales),
        'whales': whales
    })

@whales_bp.route('/<wallet>', methods=['GET'])
def get_whale_detail(wallet):
    """获取鲸鱼详情"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 鲸鱼基本信息
    cursor.execute('SELECT * FROM whales WHERE wallet = ?', (wallet,))
    whale = cursor.fetchone()
    
    if not whale:
        conn.close()
        return jsonify({'error': 'Whale not found'}), 404
    
    whale_dict = dict(whale)
    
    # 持仓明细
    cursor.execute('SELECT * FROM positions WHERE wallet = ? ORDER BY value DESC', (wallet,))
    whale_dict['positions'] = [dict(row) for row in cursor.fetchall()]
    
    # 最近变动
    cursor.execute('SELECT * FROM changes WHERE wallet = ? ORDER BY timestamp DESC LIMIT 20', (wallet,))
    whale_dict['changes'] = [dict(row) for row in cursor.fetchall()]
    
    # 集中度历史
    cursor.execute('SELECT * FROM concentration_history WHERE wallet = ? ORDER BY timestamp DESC LIMIT 24', (wallet,))
    whale_dict['concentration_history'] = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return jsonify(whale_dict)

@whales_bp.route('/<wallet>/history', methods=['GET'])
def get_whale_history(wallet):
    """获取鲸鱼历史数据"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM concentration_history 
        WHERE wallet = ? 
        ORDER BY timestamp DESC 
        LIMIT 100
    ''', (wallet,))
    
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({
        'wallet': wallet,
        'history': history
    })


@whales_bp.route('/<wallet>/analysis', methods=['GET'])
def get_whale_analysis(wallet):
    """获取鲸鱼 AI 分析（实时）"""
    try:
        analysis = analyzer.analyze_whale(wallet)
        if 'error' in analysis:
            return jsonify(analysis), 404
        return jsonify(analysis)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@whales_bp.route('/<wallet>/deep-analysis', methods=['GET'])
def get_whale_deep_analysis_cached(wallet):
    """获取深度分析（仅缓存，不触发新调用）"""
    try:
        # 只返回缓存的数据
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT wallet, content, model, generated_at, cost
            FROM whale_deep_analysis
            WHERE wallet = ? AND expires_at > ?
        ''', (wallet, datetime.now(timezone.utc).isoformat()))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return jsonify({
                'wallet': row['wallet'],
                'content': row['content'],
                'model': row['model'],
                'generated_at': row['generated_at'],
                'cost': row['cost'],
                'from_cache': True
            })
        else:
            return jsonify({'exists': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@whales_bp.route('/<wallet>/deep-analysis', methods=['POST'])
def generate_whale_deep_analysis(wallet):
    """生成深度分析（触发 LLM 调用）"""
    try:
        import os
        print(f"[DEBUG] DEEPSEEK_API_KEY: {os.getenv('DEEPSEEK_API_KEY', 'NOT SET')[:20]}...")
        
        data = request.get_json() or {}
        force_refresh = data.get('force_refresh', False)
        
        # 每次创建新实例，确保环境变量已加载
        deep_analyzer = WhaleDeepAnalyzer()
        print(f"[DEBUG] deep_analyzer.deepseek_api_key: {deep_analyzer.deepseek_api_key[:20]}..." if deep_analyzer.deepseek_api_key else "[DEBUG] deep_analyzer.deepseek_api_key: NOT SET")
        
        result = deep_analyzer.get_deep_analysis(wallet, force_refresh)
        print(f"[DEBUG] result model: {result.get('model', 'unknown')}")
        
        if 'error' in result:
            return jsonify(result), 500
            
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"[ERROR] {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
