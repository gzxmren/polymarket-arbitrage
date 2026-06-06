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

# 导入混合数据源
try:
    from hybrid_data_source import HybridDataSource
    HYBRID_SOURCE_AVAILABLE = True
    print("✅ 混合数据源已加载")
except ImportError as e:
    HYBRID_SOURCE_AVAILABLE = False
    print(f"⚠️ 混合数据源加载失败: {e}")

# 尝试导入 WebSocket 服务
try:
    from .websocket import broadcast_new_alert
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("⚠️ WebSocket 服务不可用")

class DataSyncService:
    """数据同步服务"""
    
    def __init__(self):
        # 使用相对路径，从项目根目录计算
        project_root = Path(__file__).parent.parent.parent.parent.parent
        data_dir = project_root / "07-data"
        self.whale_states_dir = data_dir / "whale_states"
        self.watchlist_file = data_dir / "whale_watchlist.json"
        self.db_path = project_root / "dashboard" / "backend" / "database" / "polymarket.db"
        
        # 初始化混合数据源
        if HYBRID_SOURCE_AVAILABLE:
            self.hybrid_source = HybridDataSource(str(self.db_path))
            print("✅ 混合数据源已初始化")
        else:
            self.hybrid_source = None
            print("⚠️ 使用传统 JSON 数据源")
    
    def _get_existing_pseudonym(self, wallet):
        """从数据库获取现有的有效 pseudonym"""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT pseudonym FROM whales WHERE wallet = ?', (wallet,))
            result = cursor.fetchone()
            conn.close()
            if result and result[0] and not result[0].startswith('0x'):
                return result[0]
        except Exception:
            pass
        return None
    
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
                        
                        # 获取 pseudonym（优先保留数据库中的有效名称）
                        db_pseudonym = self._get_existing_pseudonym(wallet)
                        if db_pseudonym and not db_pseudonym.startswith('0x'):
                            pseudonym = db_pseudonym
                        else:
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

                # [P0-2] 价值闸门：whale_states 来源若无持仓且无价值且非重点关注，跳过不落库。
                # 注: 此闸门只拦 whale_states 来源的新空行；trade-flow 鲸鱼(有成交量/笔数但暂无
                # 持仓快照)由 sync_changes.py 维护、不经此路径，故此处无需再查 changes_count/total_volume。
                # 删除存量空壳见 scripts/cleanup_whales.py(判据更严: 还要求 changes_count=0 且 total_volume=0)。
                if total_value <= 0 and len(positions) == 0 and not whale_data['is_watched']:
                    continue

                # 计算集中度指标 (HHI, Top5, Top10)
                metrics = self._calculate_concentration_metrics(positions)
                top5_ratio = metrics['top5_ratio']
                hhi = metrics['hhi']
                top10_ratio = metrics['top10_ratio']
                
                # 获取收敛趋势（从 watchlist 如果有）
                convergence_trend = ''
                if self.watchlist_file.exists():
                    with open(self.watchlist_file, 'r') as f:
                        watchlist = json.load(f)
                    if wallet in watchlist.get('whales', {}):
                        convergence_trend = watchlist['whales'][wallet].get('convergence_trend', '')
                
                # 获取变动次数和交易量
                changes_count = len(whale_data.get('changes', []))
                
                # 从 changes 表聚合交易量
                total_volume = 0
                try:
                    vol_cursor = conn.cursor()
                    vol_cursor.execute('SELECT COALESCE(SUM(change_amount), 0) FROM changes WHERE wallet = ?', (wallet,))
                    vol_result = vol_cursor.fetchone()
                    if vol_result:
                        total_volume = vol_result[0]
                except Exception:
                    pass
                
                # 插入或更新鲸鱼数据（用 ON CONFLICT 避免覆盖 volume/pnl 等）
                # 语义说明(防误改):
                #  - has_activity / total_value / position_count = 直接赋值 → 能归零(已修复评审反馈的
                #    set-once bug,数据来自 whale_state JSON,权威)。
                #  - total_pnl / changes_count / total_volume = "保优"语义: 仅当本次传入>0/非0才覆盖,
                #    否则保留旧值。这是有意为之——本路径不重算这三项(传 0/空),避免被冲掉。代价: 真值
                #    若确实回落到 0/空不会刷新(罕见,可接受)。
                cursor.execute('''
                    INSERT INTO whales 
                    (wallet, pseudonym, total_value, position_count, top5_ratio, 
                     convergence_trend, is_watched, has_activity, total_pnl, changes_count,
                     total_volume, added_at, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(wallet) DO UPDATE SET
                        pseudonym = CASE 
                            WHEN excluded.pseudonym IS NOT NULL AND excluded.pseudonym != '' AND excluded.pseudonym NOT LIKE '0x%'
                            THEN excluded.pseudonym ELSE whales.pseudonym END,
                        total_value = excluded.total_value,
                        position_count = excluded.position_count,
                        top5_ratio = CASE WHEN excluded.top5_ratio > 0 THEN excluded.top5_ratio ELSE whales.top5_ratio END,
                        convergence_trend = excluded.convergence_trend,
                        is_watched = excluded.is_watched,
                        has_activity = excluded.has_activity,
                        total_pnl = CASE WHEN excluded.total_pnl != 0 THEN excluded.total_pnl ELSE COALESCE(whales.total_pnl, 0) END,
                        changes_count = CASE WHEN excluded.changes_count > 0 THEN excluded.changes_count ELSE COALESCE(whales.changes_count, 0) END,
                        total_volume = CASE WHEN excluded.total_volume > 0 THEN excluded.total_volume ELSE COALESCE(whales.total_volume, 0) END,
                        last_updated = excluded.last_updated
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
                    total_volume,
                    whale_data['last_check'],
                    datetime.now().isoformat()
                ))
                
                # 更新持仓数据（保留 is_expired 标记）
                # 跳过 end_date 超过 30 天的过期记录，避免过期持仓反复入库
                cursor.execute('DELETE FROM positions WHERE wallet = ?', (wallet,))
                from datetime import date, timedelta
                stale_cutoff = date.today() - timedelta(days=30)
                stale_skipped = 0
                for pos in positions:
                    # 判断是否过期
                    is_expired = 0
                    end_date = pos.get('end_date', '')
                    if end_date:
                        try:
                            if end_date < date.today().isoformat():
                                is_expired = 1
                        except:
                            pass
                    
                    # 跳过过期超过 30 天的持仓（避免它们下次同步时又被重新插入）
                    if end_date:
                        try:
                            end = date.fromisoformat(end_date)
                            if end < stale_cutoff:
                                stale_skipped += 1
                                continue
                        except (ValueError, TypeError):
                            pass
                    
                    cursor.execute('''
                        INSERT INTO positions 
                        (wallet, market, outcome, size, avg_price, cur_price, value, pnl, end_date, is_expired)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        wallet, pos['market'], pos['outcome'], pos['size'],
                        pos['avg_price'], pos['cur_price'], pos['value'], pos['pnl'], pos['end_date'],
                        is_expired
                    ))
                if stale_skipped > 0:
                    print(f"    跳过 {stale_skipped} 条过期30天+持仓(wallet={whale_data['pseudonym'][:20]})", flush=True)
                
                # 记录集中度历史（只对重点关注的鲸鱼）
                if whale_data['is_watched']:
                    cursor.execute('''
                        INSERT INTO concentration_history (wallet, hhi, top5_ratio, top10_ratio, timestamp)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (wallet, hhi, top5_ratio, top10_ratio, datetime.now().isoformat()))
            
            # 清理旧的历史数据（保留最近7天）
            cursor.execute('''
                DELETE FROM concentration_history 
                WHERE timestamp < datetime('now', '-7 days')
            ''')
            deleted = cursor.rowcount
            if deleted > 0:
                print(f"   清理 {deleted} 条旧的历史记录")
            
            # 确保所有在watchlist中的鲸鱼都被标记为is_watched=1
            #（即使它们暂时没有状态文件）
            for wallet in watched_wallets:
                if wallet not in all_whales:
                    # 从watchlist获取基本信息
                    watchlist_data = watchlist.get('whales', {}).get(wallet, {})
                    cursor.execute('''
                        INSERT INTO whales 
                        (wallet, pseudonym, is_watched, added_at, last_updated)
                        VALUES (?, ?, 1, ?, ?)
                        ON CONFLICT(wallet) DO UPDATE SET
                            is_watched = 1,
                            last_updated = excluded.last_updated
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
    
    def _calculate_concentration_metrics(self, positions):
        """计算集中度指标 (HHI, Top5占比, Top10占比)"""
        if not positions:
            return {'hhi': 0, 'top5_ratio': 0, 'top10_ratio': 0}
        
        total = sum(p['value'] for p in positions)
        if total == 0:
            return {'hhi': 0, 'top5_ratio': 0, 'top10_ratio': 0}
        
        # 计算各仓位价值占比
        shares = [p['value'] / total for p in positions]
        # HHI = sum of squared shares
        hhi = sum(s * s for s in shares)
        
        sorted_values = sorted([p['value'] for p in positions], reverse=True)
        top5_ratio = sum(sorted_values[:5]) / total
        top10_ratio = sum(sorted_values[:10]) / total
        
        return {'hhi': hhi, 'top5_ratio': top5_ratio, 'top10_ratio': top10_ratio}
    
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
            
            new_alert_ids = []
            
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
                
                new_alert_id = cursor.lastrowid
                new_alert_ids.append(new_alert_id)
                new_count += 1
                existing_set.add(unique_key)
            
            conn.commit()
            
            # 通过 WebSocket 广播新警报
            if WEBSOCKET_AVAILABLE and new_alert_ids:
                print(f"   📡 广播 {len(new_alert_ids)} 条新警报...")
                for alert_id in new_alert_ids:
                    broadcast_new_alert(alert_id)
            
            conn.close()
            
            print(f"✅ 警报同步完成: 新增 {new_count} 条, 跳过 {skip_count} 条重复")
            
        except Exception as e:
            print(f"❌ 警报同步失败: {e}")
    
    def sync_cross_market_arbitrage(self):
        """同步跨平台套利数据到 Dashboard"""
        try:
            # 从监控数据目录读取跨平台套利机会
            data_dir = Path(__file__).parent.parent.parent.parent.parent / "07-data"
            arbitrage_files = list(data_dir.glob("monitor_report_*.json"))
            
            if not arbitrage_files:
                print("⚠️ 未找到监控报告文件")
                return
            
            # 读取最新的报告
            latest_file = max(arbitrage_files, key=lambda f: f.stat().st_mtime)
            with open(latest_file, 'r') as f:
                report = json.load(f)
            
            cross_market_data = report.get('cross_market', {})
            opportunities = cross_market_data.get('opportunities', [])
            
            if not opportunities:
                print("⚪ 无跨平台套利机会需要同步")
                return
            
            conn = db.get_connection()
            cursor = conn.cursor()
            
            # 确保表存在
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cross_market_arbitrage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_name TEXT NOT NULL,
                    polymarket_price REAL,
                    manifold_price REAL,
                    price_gap REAL,
                    expected_return REAL,
                    risk_level TEXT,
                    risk_score REAL,
                    audit_status TEXT,
                    match_rate REAL,
                    polymarket_url TEXT,
                    manifold_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_read BOOLEAN DEFAULT 0
                )
            ''')
            
            # 获取已存在的机会（用于去重）
            cursor.execute('''
                SELECT event_name, polymarket_price, manifold_price, created_at 
                FROM cross_market_arbitrage 
                WHERE created_at > datetime('now', '-24 hours')
            ''')
            existing = cursor.fetchall()
            existing_set = set()
            for row in existing:
                # 使用事件名+价格+时间（精确到小时）作为唯一键
                time_key = row['created_at'][:13] if row['created_at'] else ''
                existing_set.add(f"{row['event_name']}|{row['polymarket_price']:.2f}|{row['manifold_price']:.2f}|{time_key}")
            
            new_count = 0
            skip_count = 0
            
            for opp in opportunities:
                event_name = opp.get('question', '')[:200]
                poly_price = opp.get('polymarket', {}).get('price', opp.get('poly_price', 0)) * 100
                mf_price = opp.get('manifold', {}).get('price', opp.get('manifold_price', 0)) * 100
                
                # 构建唯一键
                time_key = datetime.now().strftime('%Y-%m-%d %H')
                unique_key = f"{event_name}|{poly_price:.2f}|{mf_price:.2f}|{time_key}"
                
                if unique_key in existing_set:
                    skip_count += 1
                    continue
                
                # 获取风险评估数据
                risk_reviews = cross_market_data.get('risk_reviews', [])
                review = next((r for r in risk_reviews if r.get('opportunity', {}).get('question') == opp.get('question')), {})
                
                # 获取URL
                poly_url = opp.get('polymarket', {}).get('url', '') if isinstance(opp.get('polymarket'), dict) else opp.get('poly_url', '')
                manifold_url = opp.get('manifold', {}).get('url', '') if isinstance(opp.get('manifold'), dict) else opp.get('manifold_url', '')
                
                cursor.execute('''
                    INSERT INTO cross_market_arbitrage 
                    (event_name, polymarket_price, manifold_price, price_gap, expected_return,
                     risk_level, risk_score, audit_status, match_rate, polymarket_url, manifold_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    event_name,
                    poly_price,
                    mf_price,
                    opp.get('gap', 0) * 100,
                    opp.get('gap', 0) * 100 * 0.8,
                    review.get('risk_level', 'UNKNOWN'),
                    review.get('risk_score', 0),
                    'approved' if review.get('approved', True) else 'rejected',
                    opp.get('similarity', 0) * 100,
                    poly_url,
                    manifold_url
                ))
                
                new_count += 1
                existing_set.add(unique_key)
            
            conn.commit()
            conn.close()
            
            print(f"✅ 跨平台套利同步完成: 新增 {new_count} 条, 跳过 {skip_count} 条重复")
            
        except Exception as e:
            print(f"❌ 跨平台套利同步失败: {e}")
    
    def run_full_sync(self):
        """执行完整同步"""
        print("🔄 开始数据同步...")
        self.sync_whales()
        self.sync_alerts()
        self.sync_cross_market_arbitrage()
        print("✅ 数据同步完成")

if __name__ == '__main__':
    service = DataSyncService()
    service.run_full_sync()
