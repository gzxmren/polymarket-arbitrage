from flask import Blueprint, jsonify, request
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
    conn.close()
    
    return jsonify({
        'count': len(alerts),
        'alerts': alerts
    })

@alerts_bp.route('/<int:alert_id>/read', methods=['POST'])
def mark_alert_read(alert_id):
    """标记警报为已读"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE alerts SET is_read = 1 WHERE id = ?', (alert_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})
