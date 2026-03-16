#!/usr/bin/env python3
"""
通知服务
通过SocketIO推送实时更新
"""

from app import socketio

class NotificationService:
    """通知服务"""
    
    @staticmethod
    def emit_whale_update(whale_data):
        """推送鲸鱼更新"""
        socketio.emit('whale_update', whale_data)
    
    @staticmethod
    def emit_alert_update(alert_data):
        """推送警报更新"""
        socketio.emit('alert_update', alert_data)
    
    @staticmethod
    def emit_summary_update(summary_data):
        """推送汇总更新"""
        socketio.emit('summary_update', summary_data)

if __name__ == '__main__':
    # 测试
    NotificationService.emit_whale_update({'test': 'data'})
