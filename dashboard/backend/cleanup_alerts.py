#!/usr/bin/env python3
"""
清理重复警报记录
保留每组重复记录中最早的一条
"""

import json
import sqlite3
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent / 'database' / 'polymarket.db'


def cleanup_duplicate_alerts():
    """清理重复的警报记录"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 获取所有警报
    cursor.execute('''
        SELECT id, type, message, data, created_at 
        FROM alerts 
        ORDER BY created_at ASC
    ''')
    all_alerts = cursor.fetchall()
    
    print(f"📊 总警报数: {len(all_alerts)}")
    
    # 按 wallet + type + 时间（分钟）分组
    groups = defaultdict(list)
    
    for alert in all_alerts:
        try:
            data = json.loads(alert['data'] or '{}')
            wallet = data.get('wallet', '')
            alert_type = alert['message']
            time_key = alert['created_at'][:16] if alert['created_at'] else ''
            
            key = f"{wallet}|{alert_type}|{time_key}"
            groups[key].append({
                'id': alert['id'],
                'created_at': alert['created_at']
            })
        except:
            pass
    
    # 找出重复记录
    duplicates_to_delete = []
    
    for key, alerts in groups.items():
        if len(alerts) > 1:
            # 保留最早的一条，删除其他的
            alerts.sort(key=lambda x: x['created_at'])
            for alert in alerts[1:]:
                duplicates_to_delete.append(alert['id'])
    
    print(f"🔁 发现重复组: {sum(1 for v in groups.values() if len(v) > 1)}")
    print(f"🗑️  待删除记录: {len(duplicates_to_delete)}")
    
    if duplicates_to_delete:
        # 删除重复记录
        placeholders = ','.join('?' * len(duplicates_to_delete))
        cursor.execute(f'DELETE FROM alerts WHERE id IN ({placeholders})', duplicates_to_delete)
        
        conn.commit()
        print(f"✅ 已删除 {len(duplicates_to_delete)} 条重复记录")
    else:
        print("✅ 无重复记录")
    
    # 统计剩余记录
    cursor.execute('SELECT COUNT(*) FROM alerts')
    remaining = cursor.fetchone()[0]
    print(f"📊 剩余记录: {remaining}")
    
    conn.close()


if __name__ == '__main__':
    cleanup_duplicate_alerts()
