from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
from ..models.database import db

alerts_bp = Blueprint('alerts', __name__)

@alerts_bp.route('/', methods=['GET'])
def get_alerts():
    """获取警报列表"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    alert_type = request.args.get('type', None)
    limit = request.args.get('limit', 20, type=int)
    unread_only = request.args.get('unread_only', 'false') == 'true'
    
    query = 'SELECT * FROM alerts WHERE 1=1'
    params = []
    
    if alert_type:
        query += ' AND type = ?'
        params.append(alert_type)
    
    if unread_only:
        query += ' AND is_read = 0'
    
    query += ' ORDER BY created_at DESC LIMIT ?'
    params.append(limit)
    
    cursor.execute(query, params)
    alerts = [dict(row) for row in cursor.fetchall()]
    
    # 解析 data 字段并添加额外信息
    for alert in alerts:
        try:
            import json
            alert['parsed_data'] = json.loads(alert.get('data', '{}'))
        except:
            alert['parsed_data'] = {}
    
    conn.close()
    
    return jsonify({
        'count': len(alerts),
        'alerts': alerts
    })

@alerts_bp.route('/<int:alert_id>', methods=['GET'])
def get_alert_detail(alert_id):
    """获取单个警报详情"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM alerts WHERE id = ?', (alert_id,))
    alert = cursor.fetchone()
    
    if not alert:
        conn.close()
        return jsonify({'error': 'Alert not found'}), 404
    
    alert_dict = dict(alert)
    
    # 解析 data 字段
    try:
        import json
        alert_dict['parsed_data'] = json.loads(alert_dict.get('data', '{}'))
    except:
        alert_dict['parsed_data'] = {}
    
    # 如果是鲸鱼警报，获取鲸鱼详细信息
    if alert_dict.get('type') == 'whale':
        wallet = alert_dict['parsed_data'].get('wallet')
        if wallet:
            cursor.execute('SELECT * FROM whales WHERE wallet = ?', (wallet,))
            whale = cursor.fetchone()
            if whale:
                alert_dict['whale_info'] = dict(whale)
                
                # 获取该鲸鱼的最新持仓
                cursor.execute('''
                    SELECT * FROM positions 
                    WHERE wallet = ? 
                    ORDER BY value DESC 
                    LIMIT 5
                ''', (wallet,))
                alert_dict['whale_positions'] = [dict(row) for row in cursor.fetchall()]
                
                # 获取该警报相关的变动详情
                cursor.execute('''
                    SELECT * FROM changes 
                    WHERE wallet = ? 
                    ORDER BY timestamp DESC 
                    LIMIT 5
                ''', (wallet,))
                alert_dict['recent_changes'] = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return jsonify(alert_dict)

@alerts_bp.route('/<int:alert_id>/read', methods=['POST'])
def mark_alert_read(alert_id):
    """标记警报为已读"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE alerts SET is_read = 1 WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@alerts_bp.route('/mark-all-read', methods=['POST'])
def mark_all_read():
    """标记所有警报为已读"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    alert_type = request.args.get('type', None)
    
    if alert_type:
        cursor.execute('UPDATE alerts SET is_read = 1 WHERE type = ? AND is_read = 0', (alert_type,))
    else:
        cursor.execute('UPDATE alerts SET is_read = 1 WHERE is_read = 0')
    
    updated_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'marked_count': updated_count
    })

@alerts_bp.route('/stats', methods=['GET'])
def get_alert_stats():
    """获取警报统计"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 总警报数
    cursor.execute('SELECT COUNT(*) as total FROM alerts')
    total = cursor.fetchone()['total']
    
    # 未读警报数
    cursor.execute('SELECT COUNT(*) as unread FROM alerts WHERE is_read = 0')
    unread = cursor.fetchone()['unread']
    
    # 按类型统计
    cursor.execute('''
        SELECT type, COUNT(*) as count, SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) as unread
        FROM alerts 
        GROUP BY type
    ''')
    type_stats = [dict(row) for row in cursor.fetchall()]
    
    # 最近24小时警报数
    cursor.execute('''
        SELECT COUNT(*) as recent FROM alerts 
        WHERE created_at > datetime('now', '-1 day')
    ''')
    recent = cursor.fetchone()['recent']
    
    conn.close()
    
    return jsonify({
        'total': total,
        'unread': unread,
        'recent_24h': recent,
        'by_type': type_stats
    })

@alerts_bp.route('/recent', methods=['GET'])
def get_recent_alerts():
    """获取最近警报（用于WebSocket推送）"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    minutes = request.args.get('minutes', 5, type=int)
    limit = request.args.get('limit', 10, type=int)
    
    cursor.execute('''
        SELECT * FROM alerts 
        WHERE created_at > datetime('now', '-{} minutes')
        ORDER BY created_at DESC 
        LIMIT ?
    '''.format(minutes), (limit,))
    
    alerts = [dict(row) for row in cursor.fetchall()]
    
    # 解析 data 字段
    for alert in alerts:
        try:
            import json
            alert['parsed_data'] = json.loads(alert.get('data', '{}'))
        except:
            alert['parsed_data'] = {}
    
    conn.close()
    
    return jsonify({
        'count': len(alerts),
        'alerts': alerts
    })
