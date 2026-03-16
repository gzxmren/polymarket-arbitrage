#!/usr/bin/env python3
"""
数据同步服务
从现有监控程序同步数据到Web仪表盘数据库
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / '06-tools/analysis'))

from app.models.database import db

class DataSyncService:
    """数据同步服务"""
    
    def __init__(self):
        self.whale_states_dir = Path('/home/xmren/.openclaw/workspace/polymarket-project/07-data/whale_states')
        self.watchlist_file = Path('/home/xmren/.openclaw/workspace/polymarket-project/07-data/whale_watchlist.json')
    
    def sync_whales(self):
        """同步鲸鱼数据（包括所有活跃鲸鱼和重点关注鲸鱼）"""
        try:
            # 读取 watchlist 获取重点关注列表
            watched_wallets = set()
            if self.watchlist_file.exists():
                with open(self.watchlist_file, 'r') as f:
                    watchlist = json.load(f)
                watched_wallets = set(watchlist.get('whales', {}).keys())
            
            # 从 whale_states 目录读取所有活跃鲸鱼
            all_whales = {}
            if self.whale_states_dir.exists():
                for state_file in self.whale_states_dir.glob('*.json'):
                    wallet = state_file.stem
                    try:
                        with open(state_file, 'r') as f:
                            state = json.load(f)
                        
                        positions_data = state.get('positions', {})
                        positions = []
                        total_value = 0
                        
                        for market, pos in positions_data.items():
                            pos_value = float(pos.get('size', 0)) * float(pos.get('curPrice', pos.get('currentPrice', 0)))
                            positions.append({
                                'market': market,
                                'outcome': pos.get('outcome', '?'),
                                'size': float(pos.get('size', 0)),
                                'avg_price': float(pos.get('avgPrice', 0)),
                                'cur_price': float(pos.get('curPrice', pos.get('currentPrice', 0))),
                                'value': pos_value,
                                'pnl': float(pos.get('cashPnl', 0)),
                                'end_date': pos.get('endDate', '')
                            })
                            total_value += pos_value
                        
                        # 获取 pseudonym（从文件名或状态文件）
                        pseudonym = state.get('pseudonym', wallet[:10] + '...')
                        
                        # 检查是否有活动（有变动）
                        has_activity = state.get('has_activity', False)
                        
                        all_whales[wallet] = {
                            'wallet': wallet,
                            'pseudonym': pseudonym,
                            'total_value': total_value,
                            'position_count': len(positions),
                            'positions': positions,
                            'has_activity': has_activity,
                            'is_watched': wallet in watched_wallets,
                            'total_pnl': state.get('total_pnl', 0),
                            'last_check': state.get('last_check', datetime.now().isoformat())
                        }
                    except Exception as e:
                        print(f"⚠️  读取 {wallet} 状态失败: {e}")
            
            conn = db.get_connection()
            cursor = conn.cursor()
            
            for wallet, whale_data in all_whales.items():
                positions = whale_data['positions']
                total_value = whale_data['total_value']
                
                # 计算集中度
                top5_ratio = self._calculate_top5_ratio(positions)
                
                # 获取收敛趋势（从 watchlist 如果有）
                convergence_trend = ''
                if self.watchlist_file.exists():
                    with open(self.watchlist_file, 'r') as f:
                        watchlist = json.load(f)
                    if wallet in watchlist.get('whales', {}):
                        convergence_trend = watchlist['whales'][wallet].get('convergence_trend', '')
                
                # 获取变动次数
                changes_count = len(whale_data.get('changes', []))
                
                # 插入或更新鲸鱼数据
                cursor.execute('''
                    INSERT OR REPLACE INTO whales 
                    (wallet, pseudonym, total_value, position_count, top5_ratio, 
                     convergence_trend, is_watched, has_activity, total_pnl, changes_count,
                     added_at, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    wallet,
                    whale_data['pseudonym'],
                    total_value,
                    len(positions),
                    top5_ratio,
                    convergence_trend,
                    1 if whale_data['is_watched'] else 0,
                    1 if whale_data['has_activity'] else 0,
                    whale_data['total_pnl'],
                    changes_count,
                    whale_data['last_check'],
                    datetime.now().isoformat()
                ))
                
                # 更新持仓数据
                cursor.execute('DELETE FROM positions WHERE wallet = ?', (wallet,))
                for pos in positions:
                    cursor.execute('''
                        INSERT INTO positions 
                        (wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        wallet, pos['market'], pos['outcome'], pos['size'],
                        pos['avg_price'], pos['cur_price'], pos['value'], pos['pnl'], pos['end_date']
                    ))
                
                # 记录集中度历史（只对重点关注的鲸鱼）
                if whale_data['is_watched']:
                    cursor.execute('''
                        INSERT INTO concentration_history (wallet, top5_ratio, timestamp)
                        VALUES (?, ?, ?)
                    ''', (wallet, top5_ratio, datetime.now().isoformat()))
            
            # 确保所有在watchlist中的鲸鱼都被标记为is_watched=1
            #（即使它们暂时没有状态文件）
            for wallet in watched_wallets:
                if wallet not in all_whales:
                    # 从watchlist获取基本信息
                    watchlist_data = watchlist.get('whales', {}).get(wallet, {})
                    cursor.execute('''
                        INSERT OR REPLACE INTO whales 
                        (wallet, pseudonym, is_watched, added_at, last_updated)
                        VALUES (?, ?, 1, ?, ?)
                    ''', (
                        wallet,
                        watchlist_data.get('pseudonym', wallet[:10] + '...'),
                        watchlist_data.get('added_at', datetime.now().isoformat()),
                        datetime.now().isoformat()
                    ))
            
            # 清理不在watchlist中的鲸鱼的is_watched标记
            if watched_wallets:
                watched_wallets_str = ','.join(f"'{w}'" for w in watched_wallets)
                cursor.execute(f'''
                    UPDATE whales 
                    SET is_watched = 0 
                    WHERE wallet NOT IN ({watched_wallets_str}) AND is_watched = 1
                ''')
                removed_count = cursor.rowcount
                if removed_count > 0:
                    print(f"   ℹ️  {removed_count} 个鲸鱼不再关注，已取消标记")
            
            conn.commit()
            conn.close()
            
            print(f"✅ 同步完成: {len(all_whales)} 个鲸鱼（重点关注: {len(watched_wallets)}）")
            
        except Exception as e:
            print(f"❌ 同步失败: {e}")
    
    def _calculate_top5_ratio(self, positions):
        """计算Top5占比"""
        if not positions:
            return 0
        
        total = sum(p['value'] for p in positions)
        if total == 0:
            return 0
        
        sorted_positions = sorted(positions, key=lambda x: x['value'], reverse=True)
        top5_value = sum(p['value'] for p in sorted_positions[:5])
        
        return top5_value / total
    
    def sync_alerts(self):
        """同步警报数据（改进去重逻辑）"""
        try:
            if not self.watchlist_file.exists():
                return
            
            with open(self.watchlist_file, 'r') as f:
                watchlist = json.load(f)
            
            alerts_data = watchlist.get('alerts', [])
            
            conn = db.get_connection()
            cursor = conn.cursor()
            
            # 获取数据库中已存在的所有警报（用于去重）
            cursor.execute('''
                SELECT data, message, created_at FROM alerts 
            ''')
            existing_alerts = cursor.fetchall()
            
            print(f"   数据库中已有 {len(existing_alerts)} 条警报")
            
            # 构建已存在警报的集合（用于快速查找）
            existing_set = set()
            for alert in existing_alerts:
                try:
                    data = json.loads(alert['data'] or '{}')
                    wallet = data.get('wallet', '')
                    alert_type = alert['message']
                    # 使用 wallet + type + 时间（精确到分钟）作为唯一键
                    time_key = alert['created_at'][:16] if alert['created_at'] else ''
                    existing_set.add(f"{wallet}|{alert_type}|{time_key}")
                except:
                    pass
            
            print(f"   已构建去重集合，共 {len(existing_set)} 条唯一键")
            
            new_count = 0
            skip_count = 0
            
            for alert in alerts_data[-50:]:  # 只同步最近50条
                wallet = alert.get('wallet', '')
                timestamp = alert.get('timestamp', '')
                alert_type = alert.get('type', 'activity')
                
                # 构建唯一键
                time_key = timestamp[:16] if timestamp else ''
                unique_key = f"{wallet}|{alert_type}|{time_key}"
                
                # 检查是否已存在
                if unique_key in existing_set:
                    skip_count += 1
                    continue
                
                # 插入新警报
                cursor.execute('''
                    INSERT INTO alerts 
                    (type, title, message, data, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    'whale',
                    f"{wallet[:8]}...{wallet[-6:] if len(wallet) > 6 else ''} 活动",
                    alert_type,
                    json.dumps(alert),
                    timestamp
                ))
                
                new_count += 1
                existing_set.add(unique_key)
            
            conn.commit()
            conn.close()
            
            print(f"✅ 警报同步完成: 新增 {new_count} 条, 跳过 {skip_count} 条重复")
            
        except Exception as e:
            print(f"❌ 警报同步失败: {e}")
    
    def run_full_sync(self):
        """执行完整同步"""
        print("🔄 开始数据同步...")
        self.sync_whales()
        self.sync_alerts()
        print("✅ 数据同步完成")

if __name__ == '__main__':
    service = DataSyncService()
    service.run_full_sync()
