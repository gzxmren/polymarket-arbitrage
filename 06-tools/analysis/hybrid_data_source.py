#!/usr/bin/env python3
"""
混合数据源管理器
整合多个数据源，提供统一的持仓数据接口

数据源优先级:
1. Trades 重建 (实时，最准确)
2. JSON 文件 (历史备份)
3. Leaderboard (总价值参考)
4. 标记为无数据

作者: 虾头 🦐
日期: 2026-04-05
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

from position_rebuilder import PositionRebuilder, Position

# 数据库路径
DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'
WHALE_STATES_DIR = Path('/home/xmren/.openclaw/workspace/polymarket-project/07-data/whale_states')


@dataclass
class WhaleData:
    """统一鲸鱼数据格式"""
    wallet: str
    pseudonym: str
    total_value: float
    position_count: int
    positions: List[Position]
    top5_ratio: float
    total_pnl: float
    data_source: str  # 'trades', 'json', 'leaderboard', 'none'
    data_freshness: str  # 'fresh', 'stale', 'deprecated'
    last_updated: str


class HybridDataSource:
    """混合数据源"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.rebuilder = PositionRebuilder(db_path)
    
    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
    
    def get_whale_data(self, wallet: str, use_json_fallback: bool = True) -> Optional[WhaleData]:
        """
        获取鲸鱼数据 (按优先级)
        
        优先级:
        1. Trades 重建 (实时)
        2. JSON 文件 (历史)
        3. Leaderboard (参考)
        4. 无数据
        
        Args:
            wallet: 钱包地址
            use_json_fallback: 是否使用 JSON 作为 fallback
            
        Returns:
            鲸鱼数据或 None
        """
        print(f"🔍 获取 {wallet[:20]}... 的数据")
        
        # 尝试 1: Trades 重建
        positions = self.rebuilder.rebuild_positions(wallet, days=30)
        if positions:
            return self._build_from_positions(wallet, positions, 'trades', 'fresh')
        
        # 尝试 2: JSON 文件
        if use_json_fallback:
            json_data = self._read_json(wallet)
            if json_data:
                return self._build_from_json(wallet, json_data)
        
        # 尝试 3: Leaderboard
        leaderboard_data = self._read_leaderboard(wallet)
        if leaderboard_data:
            return self._build_from_leaderboard(wallet, leaderboard_data)
        
        # 无数据
        print(f"   ❌ 无数据源可用")
        return None
    
    def _build_from_positions(self, wallet: str, positions: List[Position], 
                              source: str, freshness: str) -> WhaleData:
        """从重建的 positions 构建数据"""
        total_value = sum(p.value for p in positions)
        top5_ratio = self.rebuilder.calculate_concentration(positions)
        
        # 获取用户名
        pseudonym = self._get_pseudonym(wallet)
        
        return WhaleData(
            wallet=wallet,
            pseudonym=pseudonym,
            total_value=total_value,
            position_count=len(positions),
            positions=positions,
            top5_ratio=top5_ratio,
            total_pnl=sum(p.pnl for p in positions),
            data_source=source,
            data_freshness=freshness,
            last_updated=datetime.now().isoformat()
        )
    
    def _read_json(self, wallet: str) -> Optional[Dict]:
        """读取 JSON 文件"""
        json_file = WHALE_STATES_DIR / f'{wallet}.json'
        if not json_file.exists():
            return None
        
        try:
            with open(json_file) as f:
                return json.load(f)
        except Exception as e:
            print(f"   ⚠️  读取 JSON 失败: {e}")
            return None
    
    def _build_from_json(self, wallet: str, json_data: Dict) -> WhaleData:
        """从 JSON 构建数据"""
        positions_data = json_data.get('positions', {})
        
        positions = []
        for market, pos in positions_data.items():
            size = float(pos.get('size', 0))
            cur_price = float(pos.get('curPrice', pos.get('currentPrice', 0)))
            value = size * cur_price
            
            positions.append(Position(
                market=market,
                outcome=pos.get('outcome', '?'),
                size=size,
                avg_price=float(pos.get('avgPrice', 0)),
                cur_price=cur_price,
                value=value,
                pnl=float(pos.get('cashPnl', 0)),
                end_date=pos.get('endDate', '')
            ))
        
        total_value = sum(p.value for p in positions)
        top5_ratio = self.rebuilder.calculate_concentration(positions)
        
        # 检查 JSON 是否陈旧 (> 7天)
        last_check = json_data.get('last_check', '')
        try:
            check_time = datetime.fromisoformat(last_check.replace('Z', '+00:00'))
            is_stale = (datetime.now() - check_time) > timedelta(days=7)
            freshness = 'stale' if is_stale else 'fresh'
        except:
            freshness = 'unknown'
        
        pseudonym = self._get_pseudonym(wallet) or json_data.get('pseudonym', wallet[:10] + '...')
        
        return WhaleData(
            wallet=wallet,
            pseudonym=pseudonym,
            total_value=total_value,
            position_count=len(positions),
            positions=positions,
            top5_ratio=top5_ratio,
            total_pnl=json_data.get('total_pnl', 0),
            data_source='json',
            data_freshness=freshness,
            last_updated=last_check
        )
    
    def _read_leaderboard(self, wallet: str) -> Optional[Dict]:
        """从数据库读取 Leaderboard 数据"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT volume, pnl, rank
            FROM leaderboard_whales
            WHERE wallet = ?
        ''', (wallet,))
        
        row = cursor.fetchone()
        if row:
            return {
                'volume': row[0],
                'pnl': row[1],
                'rank': row[2]
            }
        return None
    
    def _build_from_leaderboard(self, wallet: str, lb_data: Dict) -> WhaleData:
        """从 Leaderboard 构建数据 (简化版)"""
        pseudonym = self._get_pseudonym(wallet)
        
        # Leaderboard 不提供持仓明细，只能提供总量
        return WhaleData(
            wallet=wallet,
            pseudonym=pseudonym,
            total_value=lb_data.get('volume', 0),  # 用成交量近似
            position_count=0,  # 未知
            positions=[],  # 无明细
            top5_ratio=0,  # 无法计算
            total_pnl=lb_data.get('pnl', 0),
            data_source='leaderboard',
            data_freshness='reference',  # 参考数据
            last_updated=datetime.now().isoformat()
        )
    
    def _get_pseudonym(self, wallet: str) -> str:
        """从数据库获取用户名"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT pseudonym FROM whales WHERE wallet = ?', (wallet,))
        row = cursor.fetchone()
        if row and row[0] and not row[0].startswith('0x'):
            return row[0]
        return wallet[:10] + '...'


def test_hybrid_source():
    """测试混合数据源"""
    print("=" * 80)
    print("混合数据源测试")
    print("=" * 80)
    
    source = HybridDataSource()
    
    # 测试不同情况的钱包
    test_wallets = [
        ('0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1', 'Leaderboard #1, 有交易'),  # 有交易
        ('0x0979bad57d7a1403db89cbcd9c52bf43f2138d9b', '有 JSON, 无交易'),  # 有 JSON，无交易
        ('0x1234567890123456789012345678901234567890', '无数据'),  # 无数据
    ]
    
    for wallet, desc in test_wallets:
        print(f"\n{'='*80}")
        print(f"测试: {desc}")
        print(f"钱包: {wallet}")
        print(f"{'='*80}")
        
        data = source.get_whale_data(wallet)
        
        if data:
            print(f"\n✅ 获取成功:")
            print(f"  数据源: {data.data_source}")
            print(f"  新鲜度: {data.data_freshness}")
            print(f"  用户名: {data.pseudonym}")
            print(f"  总价值: ${data.total_value:,.0f}")
            print(f"  持仓数: {data.position_count}")
            print(f"  集中度: {data.top5_ratio*100:.1f}%")
            print(f"  盈亏: ${data.total_pnl:,.0f}")
            
            if data.positions:
                print(f"\n  Top 5 持仓:")
                for p in data.positions[:5]:
                    print(f"    {p.market[:40]}...: ${p.value:,.0f}")
        else:
            print(f"\n❌ 无数据可用")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    test_hybrid_source()
