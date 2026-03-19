#!/bin/bash
# 定期同步 changes 数据

echo "$(date): 开始同步 changes 数据..."
cd /home/xmren/.openclaw/workspace/polymarket-project/06-tools/monitoring
python3 sync_changes.py >> /tmp/sync_changes.log 2>&1
echo "$(date): 同步完成"

# 显示当前数量
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM changes')
count = cursor.fetchone()[0]
print(f'Changes 表: {count} 条数据')
conn.close()
"