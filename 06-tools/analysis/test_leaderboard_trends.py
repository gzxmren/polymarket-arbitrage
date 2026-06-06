#!/usr/bin/env python3
"""
Leaderboard 趋势分析模块测试

测试趋势计算逻辑、边界条件和数据验证。

Author: AI Assistant
Date: 2026-04-05
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path

# 导入测试模块
import sys
sys.path.insert(0, str(Path(__file__).parent))
from leaderboard_trends import (
    LeaderboardTrendAnalyzer,
    TrendDirection,
    TrendMetrics
)


class TestTrendDirection:
    """测试趋势方向判断"""
    
    def test_rising_trend(self):
        """测试快速上升趋势"""
        analyzer = self._create_analyzer()
        
        # 排名上升 >= 5 且盈利增加
        result = analyzer.determine_trend_direction(
            rank_change_7d=5,
            rank_change_30d=10,
            pnl_change_7d=50000,
            is_new=False
        )
        
        assert result == TrendDirection.RISING
    
    def test_falling_trend(self):
        """测试下降趋势"""
        analyzer = self._create_analyzer()
        
        # 排名下降 >= 5 且盈利减少
        result = analyzer.determine_trend_direction(
            rank_change_7d=-5,
            rank_change_30d=-10,
            pnl_change_7d=-30000,
            is_new=False
        )
        
        assert result == TrendDirection.FALLING
    
    def test_stable_trend(self):
        """测试稳定趋势"""
        analyzer = self._create_analyzer()
        
        # 排名变化在 ±2 以内
        result = analyzer.determine_trend_direction(
            rank_change_7d=1,
            rank_change_30d=2,
            pnl_change_7d=1000,
            is_new=False
        )
        
        assert result == TrendDirection.STABLE
    
    def test_new_entry(self):
        """测试新上榜"""
        analyzer = self._create_analyzer()
        
        # 新上榜
        result = analyzer.determine_trend_direction(
            rank_change_7d=None,
            rank_change_30d=None,
            pnl_change_7d=None,
            is_new=True
        )
        
        assert result == TrendDirection.NEW
    
    def test_mixed_trend(self):
        """测试混合趋势"""
        analyzer = self._create_analyzer()
        
        # 排名变化 3 (介于稳定和快速上升之间)
        result = analyzer.determine_trend_direction(
            rank_change_7d=3,
            rank_change_30d=5,
            pnl_change_7d=-1000,
            is_new=False
        )
        
        assert result == TrendDirection.MIXED
    
    def _create_analyzer(self):
        """创建临时分析器"""
        db_path = self._create_temp_db()
        return LeaderboardTrendAnalyzer(db_path)
    
    def _create_temp_db(self):
        """创建临时测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        # 创建必要的表
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        
        return path


class TestMomentumScore:
    """测试动量评分计算"""
    
    def test_high_momentum(self):
        """测试高动量评分"""
        analyzer = self._create_analyzer()
        
        score = analyzer.calculate_momentum_score(
            rank_change_7d=10,   # 排名上升 10
            rank_change_30d=20,  # 长期上升 20
            pnl_change_7d=100000,  # 盈利增加 $100k
            pnl_change_30d=200000  # 长期盈利增加 $200k
        )
        
        # 基础分 50 + 排名变化贡献 + 盈利变化贡献
        # 预期应该接近 100
        assert score >= 80
        assert score <= 100
    
    def test_low_momentum(self):
        """测试低动量评分"""
        analyzer = self._create_analyzer()
        
        score = analyzer.calculate_momentum_score(
            rank_change_7d=-10,   # 排名下降 10
            rank_change_30d=-15,  # 长期下降 15
            pnl_change_7d=-50000,  # 盈利减少 $50k
            pnl_change_30d=-80000  # 长期盈利减少 $80k
        )
        
        # 预期应该接近 0
        assert score <= 20
        assert score >= 0
    
    def test_neutral_momentum(self):
        """测试中性动量评分"""
        analyzer = self._create_analyzer()
        
        score = analyzer.calculate_momentum_score(
            rank_change_7d=0,
            rank_change_30d=0,
            pnl_change_7d=0,
            pnl_change_30d=0
        )
        
        # 无变化，基础分 50
        assert score == 50
    
    def test_new_entry_momentum(self):
        """测试新上榜动量奖励"""
        analyzer = self._create_analyzer()
        
        score = analyzer.calculate_momentum_score(
            rank_change_7d=None,  # 新上榜
            rank_change_30d=None,
            pnl_change_7d=None,
            pnl_change_30d=None
        )
        
        # 新上榜奖励 +10，基础分 50 + 10 = 60
        assert score == 60
    
    def test_score_bounds(self):
        """测试评分边界"""
        analyzer = self._create_analyzer()
        
        # 测试上限
        score = analyzer.calculate_momentum_score(
            rank_change_7d=100,
            rank_change_30d=100,
            pnl_change_7d=1000000,
            pnl_change_30d=1000000
        )
        assert score <= 100
        
        # 测试下限
        score = analyzer.calculate_momentum_score(
            rank_change_7d=-100,
            rank_change_30d=-100,
            pnl_change_7d=-1000000,
            pnl_change_30d=-1000000
        )
        assert score >= 0
    
    def _create_analyzer(self):
        """创建临时分析器"""
        db_path = self._create_temp_db()
        return LeaderboardTrendAnalyzer(db_path)
    
    def _create_temp_db(self):
        """创建临时测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        
        return path


class TestRankChange:
    """测试排名变化计算"""
    
    def test_rank_improvement(self):
        """测试排名上升"""
        analyzer = self._create_analyzer()
        
        # #10 → #5 (上升 5 名)
        change = analyzer.calculate_rank_change(5, 10)
        
        assert change == 5
    
    def test_rank_decline(self):
        """测试排名下降"""
        analyzer = self._create_analyzer()
        
        # #5 → #10 (下降 5 名)
        change = analyzer.calculate_rank_change(10, 5)
        
        assert change == -5
    
    def test_no_history(self):
        """测试无历史数据"""
        analyzer = self._create_analyzer()
        
        change = analyzer.calculate_rank_change(10, None)
        
        assert change is None
    
    def test_same_rank(self):
        """测试排名不变"""
        analyzer = self._create_analyzer()
        
        change = analyzer.calculate_rank_change(10, 10)
        
        assert change == 0
    
    def _create_analyzer(self):
        """创建临时分析器"""
        db_path = self._create_temp_db()
        return LeaderboardTrendAnalyzer(db_path)
    
    def _create_temp_db(self):
        """创建临时测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL
            )
        ''')
        
        conn.commit()
        conn.close()
        
        return path


class TestHistoricalData:
    """测试历史数据获取"""
    
    def test_get_historical_data(self):
        """测试获取历史数据"""
        db_path = self._create_temp_db_with_history()
        analyzer = LeaderboardTrendAnalyzer(db_path)
        
        # 获取7天前的数据
        data = analyzer.get_historical_data('test_wallet_1', 7)
        
        assert data is not None
        assert 'rank' in data
        assert 'pnl' in data
        assert 'volume' in data
    
    def test_no_historical_data(self):
        """测试无历史数据"""
        db_path = self._create_temp_db()
        analyzer = LeaderboardTrendAnalyzer(db_path)
        
        # 获取不存在的历史数据
        data = analyzer.get_historical_data('nonexistent', 7)
        
        assert data is None
    
    def _create_temp_db_with_history(self):
        """创建带历史数据的测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        # 创建表
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP
            )
        ''')
        
        # 插入历史数据
        now = datetime.now()
        
        # 7天前的数据
        cursor.execute('''
            INSERT INTO leaderboard_history 
            (wallet, rank, volume, pnl, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            'test_wallet_1',
            15,  # 之前排名15
            500000,
            30000,
            (now - timedelta(days=8)).isoformat()  # 8天前
        ))
        
        # 30天前的数据
        cursor.execute('''
            INSERT INTO leaderboard_history 
            (wallet, rank, volume, pnl, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            'test_wallet_1',
            20,  # 之前排名20
            400000,
            20000,
            (now - timedelta(days=31)).isoformat()  # 31天前
        ))
        
        # 当前数据
        cursor.execute('''
            INSERT INTO leaderboard_whales 
            (wallet, rank, username, volume, pnl)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            'test_wallet_1',
            10,  # 当前排名10 (上升了5名)
            'Test User',
            600000,
            50000
        ))
        
        conn.commit()
        conn.close()
        
        return path
    
    def _create_temp_db(self):
        """创建空测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        
        return path


class TestTrendCalculation:
    """测试完整趋势计算流程"""
    
    def test_full_trend_calculation(self):
        """测试完整趋势计算"""
        db_path = self._create_temp_db_with_data()
        analyzer = LeaderboardTrendAnalyzer(db_path)
        
        # 计算趋势
        metrics = analyzer.calculate_trends_for_wallet(
            'test_wallet_1',
            current_rank=10,
            current_pnl=50000,
            current_volume=600000
        )
        
        # 验证结果
        assert metrics.rank_7d_ago == 15
        assert metrics.rank_change_7d == 5  # 上升5名
        
        assert metrics.trend_direction == TrendDirection.RISING
        assert metrics.momentum_score >= 50
    
    def test_save_and_retrieve_trends(self):
        """测试保存和读取趋势数据"""
        db_path = self._create_temp_db_with_data()
        analyzer = LeaderboardTrendAnalyzer(db_path)
        
        # 计算并保存
        metrics = analyzer.calculate_trends_for_wallet(
            'test_wallet_1',
            current_rank=10,
            current_pnl=50000,
            current_volume=600000
        )
        
        success = analyzer.save_trends('test_wallet_1', metrics)
        assert success
        
        # 直接从数据库读取趋势数据
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM leaderboard_trends WHERE wallet = ?', ('test_wallet_1',))
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None
        assert row['wallet'] == 'test_wallet_1'
        assert row['trend_direction'] == 'rising'
    
    def _create_temp_db_with_data(self):
        """创建带完整数据的测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        # 创建完整的表结构
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL,
                priority_level TEXT DEFAULT 'medium',
                watch_status TEXT DEFAULT 'active'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP
            )
        ''')
        
        # 当前数据
        cursor.execute('''
            INSERT INTO leaderboard_whales 
            (wallet, rank, username, volume, pnl, priority_level, watch_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ('test_wallet_1', 10, 'Test User', 600000, 50000, 'high', 'active'))
        
        # 7天前的历史
        now = datetime.now()
        cursor.execute('''
            INSERT INTO leaderboard_history 
            (wallet, rank, volume, pnl, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            'test_wallet_1',
            15,
            500000,
            30000,
            (now - timedelta(days=8)).isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        return path


class TestEdgeCases:
    """测试边界条件"""
    
    def test_zero_pnl(self):
        """测试零盈亏"""
        analyzer = self._create_analyzer()
        
        # 零盈亏变化
        score = analyzer.calculate_momentum_score(
            rank_change_7d=5,
            rank_change_30d=10,
            pnl_change_7d=0,
            pnl_change_30d=0
        )
        
        # 排名上升应该加分
        assert score > 50
    
    def test_large_rank_change(self):
        """测试大幅排名变化"""
        analyzer = self._create_analyzer()
        
        # 大幅上升
        score = analyzer.calculate_momentum_score(
            rank_change_7d=50,   # 大幅上升
            rank_change_30d=100,
            pnl_change_7d=0,
            pnl_change_30d=0
        )
        
        # 应该有上限
        assert score <= 100
    
    def test_negative_volume_change(self):
        """测试成交量下降"""
        db_path = self._create_temp_db_with_volume_change()
        analyzer = LeaderboardTrendAnalyzer(db_path)
        
        metrics = analyzer.calculate_trends_for_wallet(
            'test_wallet',
            current_rank=10,
            current_pnl=50000,
            current_volume=300000  # 成交量下降
        )
        
        # 成交量变化应该为负
        assert metrics.volume_change_7d < 0
    
    def _create_analyzer(self):
        """创建临时分析器"""
        db_path = self._create_temp_db()
        return LeaderboardTrendAnalyzer(db_path)
    
    def _create_temp_db(self):
        """创建空测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        
        return path
    
    def _create_temp_db_with_volume_change(self):
        """创建带成交量变化的测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP
            )
        ''')
        
        # 当前数据
        cursor.execute('''
            INSERT INTO leaderboard_whales 
            (wallet, rank, username, volume, pnl)
            VALUES (?, ?, ?, ?, ?)
        ''', ('test_wallet', 10, 'Test', 300000, 50000))
        
        # 7天前数据 (成交量更高)
        now = datetime.now()
        cursor.execute('''
            INSERT INTO leaderboard_history 
            (wallet, rank, volume, pnl, recorded_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            'test_wallet',
            15,
            500000,  # 成交量下降
            30000,
            (now - timedelta(days=8)).isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        return path


class TestIntegration:
    """集成测试"""
    
    def test_update_all_trends(self):
        """测试批量更新"""
        db_path = self._create_temp_db_with_multiple_whales()
        analyzer = LeaderboardTrendAnalyzer(db_path)
        
        # 批量更新
        stats = analyzer.update_all_trends(batch_size=10)
        
        assert stats['total'] == 3
        assert stats['success'] >= 0
        assert stats['total'] == stats['success'] + stats['failed']
    
    def test_get_trend_summary(self):
        """测试趋势摘要"""
        db_path = self._create_temp_db_with_trends()
        analyzer = LeaderboardTrendAnalyzer(db_path)
        
        summary = analyzer.get_trend_summary()
        
        assert 'total_tracked' in summary
        assert 'average_momentum' in summary
        assert summary['total_tracked'] >= 0
    
    def _create_temp_db_with_multiple_whales(self):
        """创建多鲸鱼测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        # 完整表结构
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL,
                priority_level TEXT DEFAULT 'medium',
                watch_status TEXT DEFAULT 'active'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                rank INTEGER,
                volume REAL,
                pnl REAL,
                recorded_at TIMESTAMP
            )
        ''')
        
        # 插入多个鲸鱼
        for i in range(3):
            cursor.execute('''
                INSERT INTO leaderboard_whales 
                (wallet, rank, username, volume, pnl, priority_level, watch_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                f'wallet_{i}',
                i + 1,
                f'User {i}',
                100000 * (i + 1),
                10000 * (i + 1),
                'medium',
                'active'
            ))
            
            # 历史数据
            now = datetime.now()
            cursor.execute('''
                INSERT INTO leaderboard_history 
                (wallet, rank, volume, pnl, recorded_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                f'wallet_{i}',
                i + 5,  # 排名变化
                80000 * (i + 1),
                5000 * (i + 1),
                (now - timedelta(days=8)).isoformat()
            ))
        
        conn.commit()
        conn.close()
        
        return path
    
    def _create_temp_db_with_trends(self):
        """创建带趋势数据的测试数据库"""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        # 创建表
        cursor.execute('''
            CREATE TABLE leaderboard_whales (
                wallet TEXT PRIMARY KEY,
                rank INTEGER,
                username TEXT,
                volume REAL,
                pnl REAL,
                priority_level TEXT DEFAULT 'medium',
                watch_status TEXT DEFAULT 'active'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE leaderboard_trends (
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 插入鲸鱼数据
        cursor.execute('''
            INSERT INTO leaderboard_whales 
            (wallet, rank, username, volume, pnl, priority_level, watch_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ('wallet_1', 1, 'Test User', 100000, 50000, 'high', 'active'))
        
        # 插入趋势数据
        cursor.execute('''
            INSERT INTO leaderboard_trends 
            (wallet, trend_direction, momentum_score, rank_change_7d)
            VALUES (?, ?, ?, ?)
        ''', ('wallet_1', 'rising', 75.0, 5))
        
        conn.commit()
        conn.close()
        
        return path


def run_tests():
    """运行所有测试"""
    pytest.main([__file__, '-v', '--tb=short'])


if __name__ == "__main__":
    run_tests()