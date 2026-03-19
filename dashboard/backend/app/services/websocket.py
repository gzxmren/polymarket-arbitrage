"""
WebSocket 服务
用于实时推送警报
"""

from flask_socketio import emit
from .. import socketio
from ..models.database import db

@socketio.on('connect')
def handle_connect():
    """客户端连接"""
    print('[WebSocket] 客户端已连接')
    emit('connected', {'status': 'ok', 'message': 'Connected to Polymarket Dashboard'})

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开"""
    print('[WebSocket] 客户端已断开')

@socketio.on('subscribe_alerts')
def handle_subscribe_alerts():
    """订阅警报频道"""
    print('[WebSocket] 客户端订阅警报')
    emit('subscribed', {'channel': 'alerts'})

def broadcast_alert(alert_data):
    """
    广播新警报到所有连接的客户端
    
    Args:
        alert_data: 警报数据字典
    """
    try:
        socketio.emit('alert', {
            'type': 'alert',
            'alert': alert_data
        }, broadcast=True)
        print(f"[WebSocket] 警报已广播: {alert_data.get('title', 'Unknown')}")
    except Exception as e:
        print(f"[WebSocket] 广播警报失败: {e}")

def broadcast_new_alert(alert_id):
    """
    从数据库获取警报并广播
    
    Args:
        alert_id: 警报ID
    """
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM alerts WHERE id = ?', (alert_id,))
        alert = cursor.fetchone()
        
        if alert:
            import json
            alert_dict = dict(alert)
            try:
                alert_dict['parsed_data'] = json.loads(alert_dict.get('data', '{}'))
            except:
                alert_dict['parsed_data'] = {}
            
            broadcast_alert(alert_dict)
        
        conn.close()
    except Exception as e:
        print(f"[WebSocket] 获取并广播警报失败: {e}")
