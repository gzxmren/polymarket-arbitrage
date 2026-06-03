#!/usr/bin/env python3
"""
Leaderboard 鲸鱼跟踪器
利用 Polymarket 官方排行榜识别和跟踪顶级鲸鱼
"""

import sqlite3
import urllib.request
import json
import ssl
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

# 导入趋势分析模块
import sys
sys.path.insert(0, str(Path(__file__).parent))
from leaderboard_trends import LeaderboardTrendAnalyzer

# 配置
DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'
LEADERBOARD_API = "https://data-api.polymarket.com/v1/leaderboard"
LOG_DIR = Path('/home/xmren/.openclaw/workspace/polymarket-project/07-data/logs')

# Telegram 配置（[P2安全] 2026-06-03: 从监控 .env 读，源码不含密钥）
import os
_env = Path(__file__).parent.parent / 'monitoring' / '.env'
if _env.exists():
    for _l in _env.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith('#') and '=' in _l:
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# SSL 上下文
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


import urllib.parse

def send_telegram_message(message: str) -> bool:
    """发送 Telegram 消息"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }).encode()
        
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"⚠️  Telegram 发送失败: {e}")
        return False


@dataclass
class LeaderboardEntry:
    """排行榜条目"""
    rank: int
    wallet: str
    username: str
    x_username: str
    verified: bool
    volume: float          # 成交量
    pnl: float             # 盈亏
    profile_image: str
    
    @property
    def is_profit(self) -> bool:
        """是否盈利"""
        return self.pnl > 0
    
    @property
    def roi(self) -> float:
        """投资回报率 (简化计算)"""
        if self.volume > 0:
            return self.pnl / self.volume * 100
        return 0.0


class LeaderboardWhaleTracker:
    """排行榜鲸鱼跟踪器"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_tables()
    
    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
    
    def _ensure_tables(self):
        """确保必要的表存在"""
        cursor = self.conn.cursor()
        
        # Leaderboard 鲸鱼观察名单
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                x_username TEXT,
                verified BOOLEAN,
                volume REAL,
                pnl REAL,
                first_seen TIMESTAMP,
                last_updated TIMESTAMP,
                priority_level TEXT,  -- 'critical', 'high', 'medium', 'low'
                watch_status TEXT,    -- 'active', 'watching', 'dormant'
                alert_threshold REAL  -- 触发警报的阈值
            )
        ''')
        
        # Leaderboard 历史记录
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (wallet) REFERENCES leaderboard_whales(wallet)
            )
        ''')
        
        # [P1] 2026-06-03: 移除 whale_alerts 建表 —— 该表仅建表、无写入/读取,
        # 与在用的 alerts 表冗余,已 DROP。告警统一走 alerts 表。

        # Leaderboard 趋势表 (由 LeaderboardTrendAnalyzer 管理)
        # 这里只确保表存在，实际结构由 trend analyzer 定义
        self._ensure_trends_table()
        
        self.conn.commit()
    
    def _ensure_trends_table(self):
        """确保趋势表存在"""
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leaderboard_trends (
                wallet TEXT PRIMARY KEY,
                rank_7d_ago INTEGER,
                rank_30d_ago INTEGER,
                rank_change_7d INTEGER,
                rank_change_30d INTEGER,
                pnl_7d_ago REAL,
                pnl_30d_ago REAL,
                pnl_change_7d REAL,
                pnl_change_30d REAL,
                volume_7d_ago REAL,
                volume_30d_ago REAL,
                volume_change_7d REAL,
                volume_change_30d REAL,
                trend_direction TEXT DEFAULT 'stable',
                momentum_score REAL DEFAULT 50.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (wallet) REFERENCES leaderboard_whales(wallet)
            )
        ''')
        
        # 创建索引
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_trends_direction 
            ON leaderboard_trends(trend_direction)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_trends_momentum 
            ON leaderboard_trends(momentum_score DESC)
        ''')
        
        self.conn.commit()
    
    def fetch_leaderboard(self, limit: int = 50) -> List[LeaderboardEntry]:
        """获取官方排行榜数据"""
        try:
            req = urllib.request.Request(
                LEADERBOARD_API,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            
            with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
                data = json.loads(resp.read().decode())
                
                entries = []
                for entry in data[:limit]:
                    entries.append(LeaderboardEntry(
                        rank=int(entry.get('rank', 0)),
                        wallet=entry.get('proxyWallet', ''),
                        username=entry.get('userName', ''),
                        x_username=entry.get('xUsername', ''),
                        verified=entry.get('verifiedBadge', False),
                        volume=float(entry.get('vol', 0)),
                        pnl=float(entry.get('pnl', 0)),
                        profile_image=entry.get('profileImage', '')
                    ))
                
                return entries
                
        except Exception as e:
            print(f"❌ 获取 Leaderboard 失败: {e}")
            return []
    
    def calculate_priority(self, entry: LeaderboardEntry) -> str:
        """
        计算优先级
        
        优先级规则:
        - Critical: Top 10 + 盈利 > $100k
        - High: Top 25 + 盈利 > $50k
        - Medium: Top 50 + 盈利 > $10k
        - Low: 其他
        """
        if entry.rank <= 10 and entry.pnl >= 100000:
            return 'critical'
        elif entry.rank <= 25 and entry.pnl >= 50000:
            return 'high'
        elif entry.rank <= 50 and entry.pnl >= 10000:
            return 'medium'
        else:
            return 'low'
    
    def calculate_alert_threshold(self, entry: LeaderboardEntry) -> float:
        """
        计算警报阈值
        
        根据级别设置不同的阈值:
        - Critical: $5,000
        - High: $3,000
        - Medium: $1,000
        - Low: $500
        """
        priority = self.calculate_priority(entry)
        thresholds = {
            'critical': 5000,
            'high': 3000,
            'medium': 1000,
            'low': 500
        }
        return thresholds.get(priority, 500)
    
    def sync_leaderboard_whales(self, entries: List[LeaderboardEntry], calculate_trends: bool = True):
        """
        同步排行榜鲸鱼到观察名单
        
        Args:
            entries: LeaderboardEntry 列表
            calculate_trends: 是否自动计算趋势 (默认 True)
            
        Returns:
            (added, updated, trends_updated) 元组
        """
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        
        added = 0
        updated = 0
        
        for entry in entries:
            priority = self.calculate_priority(entry)
            threshold = self.calculate_alert_threshold(entry)
            
            # 检查是否已存在
            cursor.execute('SELECT wallet FROM leaderboard_whales WHERE wallet = ?', 
                         (entry.wallet,))
            exists = cursor.fetchone()
            
            if exists:
                # 更新现有记录
                cursor.execute('''
                    UPDATE leaderboard_whales
                    SET rank = ?, username = ?, x_username = ?, verified = ?,
                        volume = ?, pnl = ?, last_updated = ?,
                        priority_level = ?, alert_threshold = ?
                    WHERE wallet = ?
                ''', (entry.rank, entry.username, entry.x_username, entry.verified,
                      entry.volume, entry.pnl, now, priority, threshold, entry.wallet))
                updated += 1
            else:
                # 插入新记录
                cursor.execute('''
                    INSERT INTO leaderboard_whales
                    (wallet, rank, username, x_username, verified, volume, pnl,
                     first_seen, last_updated, priority_level, watch_status, alert_threshold)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (entry.wallet, entry.rank, entry.username, entry.x_username,
                      entry.verified, entry.volume, entry.pnl, now, now,
                      priority, 'active', threshold))
                added += 1
            
            # 记录历史
            cursor.execute('''
                INSERT INTO leaderboard_history (wallet, rank, volume, pnl)
                VALUES (?, ?, ?, ?)
            ''', (entry.wallet, entry.rank, entry.volume, entry.pnl))
        
        self.conn.commit()
        
        # 自动计算趋势
        trends_updated = 0
        if calculate_trends:
            trends_updated = self._update_trends_for_entries(entries)
        
        return added, updated, trends_updated
    
    def _update_trends_for_entries(self, entries: List[LeaderboardEntry]) -> int:
        """
        为指定的 leaderboard 条目更新趋势数据
        
        Args:
            entries: LeaderboardEntry 列表
            
        Returns:
            成功更新的数量
        """
        try:
            # 初始化趋势分析器
            trend_analyzer = LeaderboardTrendAnalyzer(self.db_path)
            
            updated = 0
            for entry in entries:
                try:
                    # 计算趋势
                    metrics = trend_analyzer.calculate_trends_for_wallet(
                        entry.wallet,
                        entry.rank,
                        entry.pnl,
                        entry.volume
                    )
                    
                    # 保存趋势
                    if trend_analyzer.save_trends(entry.wallet, metrics):
                        updated += 1
                        
                except Exception as e:
                    print(f"⚠️  计算趋势失败 {entry.wallet}: {e}")
                    continue
            
            return updated
            
        except Exception as e:
            print(f"⚠️  趋势分析器初始化失败: {e}")
            return 0
    
    def check_leaderboard_whales_activity(self) -> List[Dict]:
        """
        检查排行榜鲸鱼的活动
        
        返回在 changes 表中有交易记录的鲸鱼
        """
        cursor = self.conn.cursor()
        
        # 获取最近1小时内的交易
        one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
        
        cursor.execute('''
            SELECT 
                c.wallet,
                w.rank,
                w.username,
                w.priority_level,
                w.alert_threshold,
                COUNT(*) as trade_count,
                SUM(c.change_amount) as total_value,
                MAX(c.change_amount) as max_trade,
                GROUP_CONCAT(DISTINCT c.market) as markets
            FROM changes c
            JOIN leaderboard_whales w ON c.wallet = w.wallet
            WHERE c.timestamp > ?
            GROUP BY c.wallet
            HAVING total_value >= w.alert_threshold
            ORDER BY w.priority_level, total_value DESC
        ''', (one_hour_ago,))
        
        alerts = []
        for row in cursor.fetchall():
            alerts.append({
                'wallet': row['wallet'],
                'rank': row['rank'],
                'username': row['username'],
                'priority': row['priority_level'],
                'trade_count': row['trade_count'],
                'total_value': row['total_value'],
                'max_trade': row['max_trade'],
                'markets': row['markets'].split(',') if row['markets'] else []
            })
        
        return alerts
    
    def generate_alert(self, activity: Dict):
        """生成警报消息"""
        priority_emoji = {
            'critical': '🔴',
            'high': '🟠',
            'medium': '🟡',
            'low': '⚪'
        }
        
        emoji = priority_emoji.get(activity['priority'], '⚪')
        
        message = f"""
{emoji} **Leaderboard 鲸鱼活动警报**

🏆 排名: #{activity['rank']}
👤 用户: {activity['username'][:20]}
💰 钱包: {activity['wallet'][:20]}...
📊 优先级: {activity['priority'].upper()}

📈 交易统计:
   - 交易次数: {activity['trade_count']}
   - 总交易额: ${activity['total_value']:,.0f}
   - 最大单笔: ${activity['max_trade']:,.0f}

🎯 涉及市场: {', '.join(activity['markets'][:3])}

⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return message
    
    def get_leaderboard_summary(self) -> Dict:
        """获取排行榜摘要"""
        cursor = self.conn.cursor()
        
        # 统计各优先级数量
        cursor.execute('''
            SELECT priority_level, COUNT(*) as count
            FROM leaderboard_whales
            GROUP BY priority_level
        ''')
        
        priority_counts = {row['priority_level']: row['count'] 
                          for row in cursor.fetchall()}
        
        # 统计今日活动
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COUNT(DISTINCT c.wallet) as active_count
            FROM changes c
            JOIN leaderboard_whales w ON c.wallet = w.wallet
            WHERE date(c.timestamp) = ?
        ''', (today,))
        
        active_today = cursor.fetchone()['active_count']
        
        # 获取 Top 10
        cursor.execute('''
            SELECT rank, username, wallet, pnl, volume
            FROM leaderboard_whales
            ORDER BY rank
            LIMIT 10
        ''')
        
        top_10 = []
        for row in cursor.fetchall():
            top_10.append({
                'rank': row['rank'],
                'username': row['username'][:20],
                'wallet': row['wallet'][:20] + '...',
                'pnl': row['pnl'],
                'volume': row['volume']
            })
        
        return {
            'total_tracked': sum(priority_counts.values()),
            'priority_distribution': priority_counts,
            'active_today': active_today,
            'top_10': top_10
        }
    
    def run_sync(self, limit: int = 50):
        """运行完整同步流程"""
        print("🐋 Leaderboard 鲸鱼跟踪器")
        print("=" * 70)
        
        # 1. 获取排行榜
        print(f"\n1️⃣ 获取官方排行榜 (Top {limit})...")
        entries = self.fetch_leaderboard(limit=limit)
        print(f"   ✅ 获取 {len(entries)} 条记录")
        
        # 2. 同步到数据库
        print("\n2️⃣ 同步到观察名单...")
        added, updated, trends_updated = self.sync_leaderboard_whales(entries)
        print(f"   ✅ 新增: {added} 个")
        print(f"   ✅ 更新: {updated} 个")
        print(f"   ✅ 趋势更新: {trends_updated} 个")
        
        # 3. 检查活动
        print("\n3️⃣ 检查鲸鱼活动...")
        alerts = self.check_leaderboard_whales_activity()
        print(f"   ✅ 发现 {len(alerts)} 个活跃鲸鱼")
        
        if alerts:
            print("\n   🚨 高优先级警报:")
            for alert in alerts[:5]:
                print(f"      {alert['priority'].upper()}: {alert['username'][:20]} "
                      f"(${alert['total_value']:,.0f})")
        
        # 4. 生成摘要
        print("\n4️⃣ 生成摘要...")
        summary = self.get_leaderboard_summary()
        print(f"   跟踪总数: {summary['total_tracked']}")
        print(f"   优先级分布: {summary['priority_distribution']}")
        print(f"   今日活跃: {summary['active_today']}")
        
        print("\n" + "=" * 70)
        print("✅ 同步完成!")
        
        return summary


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Leaderboard 鲸鱼跟踪器')
    parser.add_argument('--manual', action='store_true', help='手动同步模式')
    parser.add_argument('--notify', action='store_true', help='发送 Telegram 通知')
    parser.add_argument('--limit', type=int, default=50, help='获取数量 (默认50)')
    
    args = parser.parse_args()
    
    print("🐋 Leaderboard 鲸鱼跟踪器")
    if args.manual:
        print("   [手动同步模式]")
    print("=" * 70)
    
    tracker = LeaderboardWhaleTracker()
    summary = tracker.run_sync(limit=args.limit)
    
    # 发送 Telegram 通知
    if args.notify or args.manual:
        message = f"""🐋 *Leaderboard 鲸鱼同步完成*

📊 同步统计:
• 跟踪总数: {summary['total_tracked']}
• Critical: {summary['priority_distribution'].get('critical', 0)} 个
• High: {summary['priority_distribution'].get('high', 0)} 个
• Medium: {summary['priority_distribution'].get('medium', 0)} 个
• 今日活跃: {summary['active_today']} 个

💰 总盈亏: ${summary.get('total_pnl', 0):,.0f}

⏰ 同步时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        
        if send_telegram_message(message):
            print("\n✅ Telegram 通知已发送")
        else:
            print("\n⚠️  Telegram 通知发送失败")


if __name__ == "__main__":
    main()
