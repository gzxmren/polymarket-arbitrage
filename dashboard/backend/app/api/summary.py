from flask import Blueprint, jsonify
from ..models.database import db
from datetime import datetime

summary_bp = Blueprint('summary', __name__)

@summary_bp.route('/', methods=['GET'])
def get_summary():
    """获取汇总数据"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 鲸鱼统计
    cursor.execute('''
        SELECT 
            COUNT(*) as watched_count,
            SUM(total_value) as total_value,
            SUM(CASE WHEN last_updated > datetime('now', '-1 hour') THEN 1 ELSE 0 END) as active_count
        FROM whales 
        WHERE is_watched = 1
    ''')
    whale_stats = dict(cursor.fetchone())
    
    # 警报统计
    cursor.execute('''
        SELECT COUNT(*) as unread_count 
        FROM alerts 
        WHERE is_read = 0
    ''')
    alert_stats = dict(cursor.fetchone())
    
    # 最新警报
    cursor.execute('''
        SELECT * FROM alerts 
        ORDER BY created_at DESC 
        LIMIT 10
    ''')
    recent_alerts = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return jsonify({
        'timestamp': datetime.now().isoformat(),
        'whales': whale_stats,
        'alerts': {
            'unread_count': alert_stats.get('unread_count', 0),
            'recent': recent_alerts
        }
    })
