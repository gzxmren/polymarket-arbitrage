#!/usr/bin/env python3
"""
Leaderboard 历史趋势分析模块

提供排行榜鲸鱼的历史趋势计算和分析功能，包括:
- 排名变化趋势 (7天/30天)
- 盈亏变化趋势
- 成交量变化趋势
- 动量评分计算
- 趋势方向判断

Author: AI Assistant
Date: 2026-04-05
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class TrendDirection(Enum):
    """趋势方向枚举"""
    RISING = 'rising'       # 快速上升
    FALLING = 'falling'     # 排名下降
    STABLE = 'stable'       # 保持稳定
    NEW = 'new'             # 新上榜
    DROPPED = 'dropped'     # 掉出榜单
    MIXED = 'mixed'         # 混合趋势


@dataclass
class TrendMetrics:
    """趋势指标数据类"""
    # 排名相关
    rank_7d_ago: Optional[int] = None
    rank_30d_ago: Optional[int] = None
    rank_change_7d: Optional[int] = None  # 正值表示上升
    rank_change_30d: Optional[int] = None
    
    # 盈亏相关
    pnl_7d_ago: Optional[float] = None
    pnl_30d_ago: Optional[float] = None
    pnl_change_7d: Optional[float] = None
    pnl_change_30d: Optional[float] = None
    
    # 成交量相关
    volume_7d_ago: Optional[float] = None
    volume_30d_ago: Optional[float] = None
    volume_change_7d: Optional[float] = None
    volume_change_30d: Optional[float] = None
    
    # 综合趋势
    trend_direction: TrendDirection = TrendDirection.STABLE
    momentum_score: float = 50.0  # 0-100
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'rank_7d_ago': self.rank_7d_ago,
            'rank_30d_ago': self.rank_30d_ago,
            'rank_change_7d': self.rank_change_7d,
            'rank_change_30d': self.rank_change_30d,
            'pnl_7d_ago': self.pnl_7d_ago,
            'pnl_30d_ago': self.pnl_30d_ago,
            'pnl_change_7d': self.pnl_change_7d,
            'pnl_change_30d': self.pnl_change_30d,
            'volume_7d_ago': self.volume_7d_ago,
            'volume_30d_ago': self.volume_30d_ago,
            'volume_change_7d': self.volume_change_7d,
            'volume_change_30d': self.volume_change_30d,
            'trend_direction': self.trend_direction.value,
            'momentum_score': round(self.momentum_score, 2)
        }


class LeaderboardTrendAnalyzer:
    """
    Leaderboard 趋势分析器
    
    负责计算和分析排行榜鲸鱼的历史趋势数据。
    支持增量更新和批量计算，确保性能优化。
    """
    
    def __init__(self, db_path: str):
        """
        初始化趋势分析器
        
        Args:
            db_path: SQLite 数据库路径
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_tables()
    
    def __del__(self):
        """析构时关闭数据库连接"""
        if hasattr(self, 'conn'):
            self.conn.close()
    
    def _ensure_tables(self):
        """
        确保必要的表存在
        
        创建 leaderboard_trends 表用于存储趋势数据
        """
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leaderboard_trends (
                wallet TEXT PRIMARY KEY,
                
                -- 排名趋势
                rank_7d_ago INTEGER,
                rank_30d_ago INTEGER,
                rank_change_7d INTEGER,
                rank_change_30d INTEGER,
                
                -- 盈亏趋势
                pnl_7d_ago REAL,
                pnl_30d_ago REAL,
                pnl_change_7d REAL,
                pnl_change_30d REAL,
                
                -- 成交量趋势
                volume_7d_ago REAL,
                volume_30d_ago REAL,
                volume_change_7d REAL,
                volume_change_30d REAL,
                
                -- 综合趋势
                trend_direction TEXT DEFAULT 'stable',
                momentum_score REAL DEFAULT 50.0,
                
                -- 元数据
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                FOREIGN KEY (wallet) REFERENCES leaderboard_whales(wallet)
            )
        ''')
        
        # 创建索引优化查询性能
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_trends_direction 
            ON leaderboard_trends(trend_direction)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_trends_momentum 
            ON leaderboard_trends(momentum_score DESC)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_trends_updated 
            ON leaderboard_trends(updated_at)
        ''')
        
        self.conn.commit()
    
    def calculate_rank_change(self, current_rank: int, past_rank: Optional[int]) -> Optional[int]:
        """
        计算排名变化
        
        正值表示排名上升 (如 #10 → #5, change = +5)
        负值表示排名下降 (如 #5 → #10, change = -5)
        
        Args:
            current_rank: 当前排名
            past_rank: 过去的排名
            
        Returns:
            排名变化值，如果 past_rank 为 None 则返回 None
        """
        if past_rank is None:
            return None
        return past_rank - current_rank
    
    def calculate_percentage_change(self, current: float, past: Optional[float]) -> Optional[float]:
        """
        计算百分比变化
        
        Args:
            current: 当前值
            past: 过去值
            
        Returns:
            百分比变化 (如 0.15 表示 15% 增长)，如果 past 为 None 或 0 则返回 None
        """
        if past is None or past == 0:
            return None
        return (current - past) / abs(past)
    
    def determine_trend_direction(
        self,
        rank_change_7d: Optional[int],
        rank_change_30d: Optional[int],
        pnl_change_7d: Optional[float],
        is_new: bool = False
    ) -> TrendDirection:
        """
        判断趋势方向
        
        根据排名变化和盈亏变化综合判断鲸鱼的趋势方向。
        
        Args:
            rank_change_7d: 7天排名变化
            rank_change_30d: 30天排名变化
            pnl_change_7d: 7天盈亏变化
            is_new: 是否为新上榜
            
        Returns:
            TrendDirection 枚举值
        """
        # 新上榜
        if is_new or rank_change_7d is None:
            return TrendDirection.NEW
        
        # 快速上升: 排名上升 >= 5 且盈利增加
        if rank_change_7d >= 5 and (pnl_change_7d is None or pnl_change_7d >= 0):
            return TrendDirection.RISING
        
        # 快速下降: 排名下降 >= 5 且盈利减少
        if rank_change_7d <= -5 and (pnl_change_7d is not None and pnl_change_7d < 0):
            return TrendDirection.FALLING
        
        # 稳定: 排名变化在 ±2 以内
        if abs(rank_change_7d) <= 2:
            return TrendDirection.STABLE
        
        # 其他情况为混合趋势
        return TrendDirection.MIXED
    
    def calculate_momentum_score(
        self,
        rank_change_7d: Optional[int],
        rank_change_30d: Optional[int],
        pnl_change_7d: Optional[float],
        pnl_change_30d: Optional[float]
    ) -> float:
        """
        计算动量评分 (0-100)
        
        综合考虑短期和长期趋势，给出一个 0-100 的动量评分。
        分数越高表示上升趋势越强。
        
        评分规则:
        - 基础分: 50
        - 短期排名变化 (7天): 每上升1名 +5分，最多 +20
        - 长期排名变化 (30天): 每上升1名 +3分，最多 +15
        - 短期盈亏变化: 正向变化按比例加分，最多 +15
        - 长期盈亏变化: 正向变化按比例加分，最多 +10
        - 新上榜: +10 分奖励
        
        Args:
            rank_change_7d: 7天排名变化
            rank_change_30d: 30天排名变化
            pnl_change_7d: 7天盈亏变化
            pnl_change_30d: 30天盈亏变化
            
        Returns:
            0-100 之间的动量评分
        """
        score = 50.0  # 基础分
        
        # 短期排名变化 (7天)
        if rank_change_7d is not None:
            # 每上升1名 +5分，下降则减分
            rank_7d_bonus = rank_change_7d * 5
            score += max(-20, min(rank_7d_bonus, 20))  # 限制在 ±20 范围内
        
        # 长期排名变化 (30天)
        if rank_change_30d is not None:
            rank_30d_bonus = rank_change_30d * 3
            score += max(-15, min(rank_30d_bonus, 15))  # 限制在 ±15 范围内
        
        # 短期盈亏变化 (7天)
        if pnl_change_7d is not None and pnl_change_7d > 0:
            # 每 $10,000 盈利 +1分，最多 +15
            pnl_7d_bonus = min(pnl_change_7d / 10000, 15)
            score += pnl_7d_bonus
        elif pnl_change_7d is not None and pnl_change_7d < 0:
            # 亏损减分
            pnl_7d_penalty = max(pnl_change_7d / 10000, -10)
            score += pnl_7d_penalty
        
        # 长期盈亏变化 (30天)
        if pnl_change_30d is not None and pnl_change_30d > 0:
            pnl_30d_bonus = min(pnl_change_30d / 20000, 10)
            score += pnl_30d_bonus
        elif pnl_change_30d is not None and pnl_change_30d < 0:
            pnl_30d_penalty = max(pnl_change_30d / 20000, -5)
            score += pnl_30d_penalty
        
        # 新上榜奖励
        if rank_change_7d is None:
            score += 10
        
        # 确保分数在 0-100 范围内
        return max(0.0, min(100.0, score))
    
    def get_historical_data(
        self,
        wallet: str,
        days_ago: int
    ) -> Optional[Dict[str, Any]]:
        """
        获取历史数据
        
        从 leaderboard_history 表中获取指定天数前的数据。
        使用最接近指定日期的记录。
        
        Args:
            wallet: 钱包地址
            days_ago: 多少天前的数据 (7 或 30)
            
        Returns:
            包含 rank, pnl, volume 的字典，如果没有数据则返回 None
        """
        cursor = self.conn.cursor()
        
        # 使用窗口函数获取最接近指定日期的记录
        # 查找 days_ago 天前的第一条记录
        cursor.execute('''
            SELECT rank, pnl, volume, recorded_at
            FROM leaderboard_history
            WHERE wallet = ?
              AND recorded_at <= datetime('now', '-{} days')
            ORDER BY recorded_at DESC
            LIMIT 1
        '''.format(days_ago), (wallet,))
        
        row = cursor.fetchone()
        
        if row:
            return {
                'rank': row[0],
                'pnl': row[1],
                'volume': row[2],
                'recorded_at': row[3]
            }
        
        return None
    
    def calculate_trends_for_wallet(
        self,
        wallet: str,
        current_rank: int,
        current_pnl: float,
        current_volume: float
    ) -> TrendMetrics:
        """
        计算单个鲸鱼的趋势指标
        
        获取历史数据并计算所有趋势指标。
        
        Args:
            wallet: 钱包地址
            current_rank: 当前排名
            current_pnl: 当前盈亏
            current_volume: 当前成交量
            
        Returns:
            TrendMetrics 对象包含所有趋势指标
        """
        metrics = TrendMetrics()
        
        # 获取7天前的数据
        data_7d = self.get_historical_data(wallet, 7)
        if data_7d:
            metrics.rank_7d_ago = data_7d['rank']
            metrics.pnl_7d_ago = data_7d['pnl']
            metrics.volume_7d_ago = data_7d['volume']
            
            metrics.rank_change_7d = self.calculate_rank_change(
                current_rank, data_7d['rank']
            )
            metrics.pnl_change_7d = current_pnl - data_7d['pnl']
            metrics.volume_change_7d = current_volume - data_7d['volume']
        
        # 获取30天前的数据
        data_30d = self.get_historical_data(wallet, 30)
        if data_30d:
            metrics.rank_30d_ago = data_30d['rank']
            metrics.pnl_30d_ago = data_30d['pnl']
            metrics.volume_30d_ago = data_30d['volume']
            
            metrics.rank_change_30d = self.calculate_rank_change(
                current_rank, data_30d['rank']
            )
            metrics.pnl_change_30d = current_pnl - data_30d['pnl']
            metrics.volume_change_30d = current_volume - data_30d['volume']
        
        # 判断趋势方向
        is_new = metrics.rank_7d_ago is None
        metrics.trend_direction = self.determine_trend_direction(
            metrics.rank_change_7d,
            metrics.rank_change_30d,
            metrics.pnl_change_7d,
            is_new
        )
        
        # 计算动量评分
        metrics.momentum_score = self.calculate_momentum_score(
            metrics.rank_change_7d,
            metrics.rank_change_30d,
            metrics.pnl_change_7d,
            metrics.pnl_change_30d
        )
        
        return metrics
    
    def save_trends(self, wallet: str, metrics: TrendMetrics) -> bool:
        """
        保存趋势数据到数据库
        
        Args:
            wallet: 钱包地址
            metrics: TrendMetrics 对象
            
        Returns:
            是否保存成功
        """
        try:
            cursor = self.conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO leaderboard_trends (
                    wallet,
                    rank_7d_ago, rank_30d_ago,
                    rank_change_7d, rank_change_30d,
                    pnl_7d_ago, pnl_30d_ago,
                    pnl_change_7d, pnl_change_30d,
                    volume_7d_ago, volume_30d_ago,
                    volume_change_7d, volume_change_30d,
                    trend_direction, momentum_score,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                wallet,
                metrics.rank_7d_ago, metrics.rank_30d_ago,
                metrics.rank_change_7d, metrics.rank_change_30d,
                metrics.pnl_7d_ago, metrics.pnl_30d_ago,
                metrics.pnl_change_7d, metrics.pnl_change_30d,
                metrics.volume_7d_ago, metrics.volume_30d_ago,
                metrics.volume_change_7d, metrics.volume_change_30d,
                metrics.trend_direction.value,
                metrics.momentum_score,
                datetime.now().isoformat()
            ))
            
            self.conn.commit()
            return True
            
        except Exception as e:
            print(f"❌ 保存趋势数据失败 {wallet}: {e}")
            self.conn.rollback()
            return False
    
    def update_all_trends(self, batch_size: int = 50) -> Dict[str, Any]:
        """
        批量更新所有鲸鱼的趋势数据
        
        用于全量更新，性能优化使用批量处理。
        
        Args:
            batch_size: 每批处理的数量
            
        Returns:
            更新统计信息
        """
        cursor = self.conn.cursor()
        
        # 获取所有 leaderboard 鲸鱼
        cursor.execute('''
            SELECT wallet, rank, pnl, volume
            FROM leaderboard_whales
            WHERE watch_status != 'dropped'
        ''')
        
        whales = cursor.fetchall()
        
        stats = {
            'total': len(whales),
            'success': 0,
            'failed': 0,
            'new_entries': 0,
            'rising': 0,
            'falling': 0,
            'stable': 0
        }
        
        for i, whale in enumerate(whales):
            wallet, rank, pnl, volume = whale
            
            try:
                # 计算趋势
                metrics = self.calculate_trends_for_wallet(
                    wallet, rank, pnl, volume
                )
                
                # 保存
                if self.save_trends(wallet, metrics):
                    stats['success'] += 1
                    
                    # 统计趋势分布
                    if metrics.trend_direction == TrendDirection.NEW:
                        stats['new_entries'] += 1
                    elif metrics.trend_direction == TrendDirection.RISING:
                        stats['rising'] += 1
                    elif metrics.trend_direction == TrendDirection.FALLING:
                        stats['falling'] += 1
                    elif metrics.trend_direction == TrendDirection.STABLE:
                        stats['stable'] += 1
                else:
                    stats['failed'] += 1
                    
            except Exception as e:
                print(f"❌ 处理 {wallet} 时出错: {e}")
                stats['failed'] += 1
            
            # 每 batch_size 个提交一次，避免事务过大
            if (i + 1) % batch_size == 0:
                self.conn.commit()
                print(f"   已处理 {i + 1}/{len(whales)}...")
        
        self.conn.commit()
        return stats
    
    def get_trends(
        self,
        trend_type: Optional[str] = None,
        min_momentum: float = 0,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取趋势数据
        
        支持按趋势类型和动量评分筛选。
        
        Args:
            trend_type: 趋势类型 ('rising', 'falling', 'stable', 'new')
            min_momentum: 最小动量评分
            limit: 返回数量限制
            
        Returns:
            趋势数据列表
        """
        cursor = self.conn.cursor()
        
        query = '''
            SELECT 
                t.*,
                w.username, w.rank, w.pnl, w.volume, w.priority_level
            FROM leaderboard_trends t
            JOIN leaderboard_whales w ON t.wallet = w.wallet
            WHERE 1=1
        '''
        params = []
        
        if trend_type:
            query += ' AND t.trend_direction = ?'
            params.append(trend_type)
        
        if min_momentum > 0:
            query += ' AND t.momentum_score >= ?'
            params.append(min_momentum)
        
        query += ' ORDER BY t.momentum_score DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'wallet': row['wallet'],
                'username': row['username'],
                'rank': row['rank'],
                'pnl': row['pnl'],
                'volume': row['volume'],
                'priority_level': row['priority_level'],
                'rank_change_7d': row['rank_change_7d'],
                'rank_change_30d': row['rank_change_30d'],
                'pnl_change_7d': row['pnl_change_7d'],
                'pnl_change_30d': row['pnl_change_30d'],
                'trend_direction': row['trend_direction'],
                'momentum_score': row['momentum_score'],
                'updated_at': row['updated_at']
            })
        
        return results
    
    def get_whale_trend_detail(self, wallet: str) -> Optional[Dict[str, Any]]:
        """
        获取单个鲸鱼的详细趋势数据
        
        包含趋势指标和历史排名变化数据。
        
        Args:
            wallet: 钱包地址
            
        Returns:
            详细趋势数据，如果不存在则返回 None
        """
        cursor = self.conn.cursor()
        
        # 获取趋势数据
        cursor.execute('''
            SELECT t.*, w.username, w.rank, w.pnl, w.volume
            FROM leaderboard_trends t
            JOIN leaderboard_whales w ON t.wallet = w.wallet
            WHERE t.wallet = ?
        ''', (wallet,))
        
        row = cursor.fetchone()
        
        if not row:
            return None
        
        # 获取历史排名变化 (最近30天)
        cursor.execute('''
            SELECT 
                date(recorded_at) as date,
                rank, pnl, volume
            FROM leaderboard_history
            WHERE wallet = ?
              AND recorded_at > datetime('now', '-30 days')
            ORDER BY recorded_at ASC
        ''', (wallet,))
        
        history = []
        for hist_row in cursor.fetchall():
            history.append({
                'date': hist_row['date'],
                'rank': hist_row['rank'],
                'pnl': hist_row['pnl'],
                'volume': hist_row['volume']
            })
        
        return {
            'wallet': row['wallet'],
            'username': row['username'],
            'current_rank': row['rank'],
            'current_pnl': row['pnl'],
            'current_volume': row['volume'],
            'rank_7d_ago': row['rank_7d_ago'],
            'rank_30d_ago': row['rank_30d_ago'],
            'rank_change_7d': row['rank_change_7d'],
            'rank_change_30d': row['rank_change_30d'],
            'pnl_change_7d': row['pnl_change_7d'],
            'pnl_change_30d': row['pnl_change_30d'],
            'volume_change_7d': row['volume_change_7d'],
            'volume_change_30d': row['volume_change_30d'],
            'trend_direction': row['trend_direction'],
            'momentum_score': row['momentum_score'],
            'updated_at': row['updated_at'],
            'history': history
        }
    
    def get_trend_summary(self) -> Dict[str, Any]:
        """
        获取趋势摘要统计
        
        Returns:
            趋势分布统计信息
        """
        cursor = self.conn.cursor()
        
        # 趋势分布
        cursor.execute('''
            SELECT trend_direction, COUNT(*) as count
            FROM leaderboard_trends
            GROUP BY trend_direction
        ''')
        
        direction_dist = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 动量评分分布
        cursor.execute('''
            SELECT 
                CASE 
                    WHEN momentum_score >= 80 THEN 'high'
                    WHEN momentum_score >= 60 THEN 'medium'
                    WHEN momentum_score >= 40 THEN 'low'
                    ELSE 'very_low'
                END as category,
                COUNT(*) as count
            FROM leaderboard_trends
            GROUP BY category
        ''')
        
        momentum_dist = {}
        for row in cursor.fetchall():
            momentum_dist[row[0]] = row[1]
        
        # 平均动量评分
        cursor.execute('SELECT AVG(momentum_score) FROM leaderboard_trends')
        avg_momentum = cursor.fetchone()[0] or 50.0
        
        # 快速上升的鲸鱼数量
        cursor.execute('''
            SELECT COUNT(*) FROM leaderboard_trends
            WHERE trend_direction = 'rising' AND momentum_score >= 70
        ''')
        
        hot_whales = cursor.fetchone()[0]
        
        return {
            'total_tracked': sum(direction_dist.values()),
            'direction_distribution': direction_dist,
            'momentum_distribution': momentum_dist,
            'average_momentum': round(avg_momentum, 2),
            'hot_whales_count': hot_whales
        }
    
    def detect_trend_changes(self) -> List[Dict[str, Any]]:
        """
        检测趋势变化
        
        找出趋势方向发生变化的鲸鱼，用于告警。
        
        Returns:
            趋势变化列表
        """
        cursor = self.conn.cursor()
        
        # 获取上次更新时的趋势数据（通过比较更新时间）
        # 这里简化处理，返回最近更新的且趋势为 rising/falling 的鲸鱼
        cursor.execute('''
            SELECT 
                t.wallet, t.trend_direction, t.momentum_score,
                w.username, w.rank, w.pnl
            FROM leaderboard_trends t
            JOIN leaderboard_whales w ON t.wallet = w.wallet
            WHERE t.trend_direction IN ('rising', 'falling')
              AND t.momentum_score >= 60
              AND t.updated_at > datetime('now', '-1 hour')
            ORDER BY t.momentum_score DESC
        ''')
        
        changes = []
        for row in cursor.fetchall():
            changes.append({
                'wallet': row['wallet'],
                'username': row['username'],
                'current_rank': row['rank'],
                'new_trend': row['trend_direction'],
                'momentum_score': row['momentum_score']
            })
        
        return changes


def main():
    """测试主函数"""
    import os
    
    db_path = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'
    
    analyzer = LeaderboardTrendAnalyzer(db_path)
    
    print("🐋 Leaderboard 趋势分析器")
    print("=" * 70)
    
    # 更新所有趋势
    print("\n1️⃣ 更新所有鲸鱼趋势...")
    stats = analyzer.update_all_trends(batch_size=50)
    print(f"   总计: {stats['total']}")
    print(f"   成功: {stats['success']}")
    print(f"   失败: {stats['failed']}")
    print(f"   新上榜: {stats['new_entries']}")
    print(f"   上升: {stats['rising']}")
    print(f"   下降: {stats['falling']}")
    print(f"   稳定: {stats['stable']}")
    
    # 获取趋势摘要
    print("\n2️⃣ 趋势摘要...")
    summary = analyzer.get_trend_summary()
    print(f"   跟踪总数: {summary['total_tracked']}")
    print(f"   趋势分布: {summary['direction_distribution']}")
    print(f"   平均动量: {summary['average_momentum']}")
    print(f"   热门鲸鱼: {summary['hot_whales_count']}")
    
    # 获取快速上升的鲸鱼
    print("\n3️⃣ 快速上升的鲸鱼...")
    rising = analyzer.get_trends(trend_type='rising', min_momentum=60, limit=5)
    for w in rising:
        print(f"   {w['username'][:20]:20} 排名变化: +{w['rank_change_7d']:3d} 动量: {w['momentum_score']:.1f}")
    
    print("\n" + "=" * 70)
    print("✅ 完成!")


if __name__ == "__main__":
    main()

