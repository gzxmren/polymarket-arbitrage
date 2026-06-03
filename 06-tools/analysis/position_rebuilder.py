#!/usr/bin/env python3
"""
持仓重建模块
从 Trades (changes 表) 重建当前持仓

原理:
1. 获取某钱包最近 N 天的所有交易
2. 按 market + outcome 分组
3. 计算净持仓 (买入 - 卖出)
4. 计算当前价值 (size * current_price)

作者: 虾头 🦐
日期: 2026-04-05
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

# 数据库路径
DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'


@dataclass
class Position:
    """持仓数据类"""
    market: str
    outcome: str
    size: float
    avg_price: float
    cur_price: float
    value: float
    pnl: float
    end_date: str


class PositionRebuilder:
    """持仓重建器"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
    
    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
    
    def get_wallet_trades(self, wallet: str, days: int = 30) -> List[Dict]:
        """
        获取钱包最近 N 天的交易记录
        
        Args:
            wallet: 钱包地址
            days: 查询天数
            
        Returns:
            交易记录列表
        """
        cursor = self.conn.cursor()
        
        since = (datetime.now() - timedelta(days=days)).isoformat()
        
        cursor.execute('''
            SELECT 
                market,
                outcome,
                change_amount,
                timestamp,
                type
            FROM changes
            WHERE wallet = ?
              AND timestamp > ?
            ORDER BY timestamp ASC
        ''', (wallet, since))
        
        trades = []
        for row in cursor.fetchall():
            trades.append({
                'market': row['market'],
                'outcome': row['outcome'],
                'amount': row['change_amount'],
                'timestamp': row['timestamp'],
                'type': row['type']
            })
        
        return trades
    
    def calculate_net_positions(self, trades: List[Dict]) -> Dict[Tuple[str, str], float]:
        """
        计算净持仓
        
        Args:
            trades: 交易记录列表
            
        Returns:
            {(market, outcome): net_size}
        """
        positions = {}
        
        for trade in trades:
            key = (trade['market'], trade['outcome'])
            if key not in positions:
                positions[key] = 0
            
            # 假设 change_amount > 0 是买入, < 0 是卖出
            # 但实际上 changes 表只记录变动, 不记录方向
            # 这里简化处理: 累加变动
            positions[key] += trade['amount']
        
        # 过滤掉平仓的 (size = 0)
        return {k: v for k, v in positions.items() if abs(v) > 0.01}
    
    def estimate_current_price(self, market: str, outcome: str) -> float:
        """
        估算当前价格
        
        策略:
        1. 从最近交易获取价格
        2. 如果没有, 返回 0.5 (中性估计)
        
        Args:
            market: 市场名称
            outcome: 预测结果
            
        Returns:
            估算价格 (0-1)
        """
        cursor = self.conn.cursor()
        
        # 从最近交易获取价格
        cursor.execute('''
            SELECT new_size, change_amount
            FROM changes
            WHERE market = ? AND outcome = ?
            ORDER BY timestamp DESC
            LIMIT 1
        ''', (market, outcome))
        
        row = cursor.fetchone()
        if row and row['new_size'] > 0:
            # 价格 = 金额 / 数量
            return row['change_amount'] / row['new_size']
        
        # 默认返回 0.5
        return 0.5
    
    def rebuild_positions(self, wallet: str, days: int = 30) -> List[Position]:
        """
        重建持仓
        
        Args:
            wallet: 钱包地址
            days: 查询天数 (默认30, 可扩展到90或全部)
            
        Returns:
            持仓列表
        """
        print(f"🔄 重建 {wallet[:20]}... 的持仓 (最近{days}天)")
        
        # 1. 获取交易
        trades = self.get_wallet_trades(wallet, days)
        print(f"   获取 {len(trades)} 条交易记录")
        
        # 如果30天不够，尝试90天
        if not trades and days == 30:
            print(f"   ⚠️  30天无记录，尝试90天...")
            trades = self.get_wallet_trades(wallet, 90)
            print(f"   获取 {len(trades)} 条交易记录 (90天)")
        
        # 如果90天还不够，尝试全部历史
        if not trades and days <= 90:
            print(f"   ⚠️  90天无记录，尝试全部历史...")
            trades = self.get_wallet_trades(wallet, 365)  # 1年
            print(f"   获取 {len(trades)} 条交易记录 (1年)")
        
        if not trades:
            print(f"   ❌ 无交易记录，无法重建")
            print(f"   建议: 使用 JSON 文件数据或 Leaderboard 数据")
            return []
        
        # 2. 计算净持仓
        net_positions = self.calculate_net_positions(trades)
        print(f"   计算 {len(net_positions)} 个持仓")
        
        # 3. 构建 Position 对象
        positions = []
        for (market, outcome), size in net_positions.items():
            # 估算当前价格
            cur_price = self.estimate_current_price(market, outcome)
            
            # 计算价值
            value = size * cur_price
            
            # 估算平均成本 (简化: 使用最近交易价格)
            avg_price = cur_price  # 简化处理
            
            # 估算盈亏
            pnl = value - (size * avg_price)  # 简化: 假设成本=当前价
            
            # 估算结束日期 (简化: 从市场名称提取)
            end_date = self._extract_end_date(market)
            
            positions.append(Position(
                market=market,
                outcome=outcome,
                size=size,
                avg_price=avg_price,
                cur_price=cur_price,
                value=value,
                pnl=pnl,
                end_date=end_date
            ))
        
        # 4. 按价值排序
        positions.sort(key=lambda x: abs(x.value), reverse=True)
        
        print(f"   ✅ 重建完成: {len(positions)} 个持仓")
        return positions
    
    def _extract_end_date(self, market: str) -> str:
        """从市场名称提取结束日期 (简化)"""
        # 尝试提取日期模式, 如 2026-04-05
        import re
        match = re.search(r'(\d{4}-\d{2}-\d{2})', market)
        if match:
            return match.group(1)
        return ''
    
    def calculate_concentration(self, positions: List[Position]) -> float:
        """
        计算集中度 (Top5 占比)
        
        Args:
            positions: 持仓列表
            
        Returns:
            Top5 占比 (0-1)
        """
        if not positions:
            return 0
        
        total = sum(abs(p.value) for p in positions)
        if total == 0:
            return 0
        
        top5 = sum(abs(p.value) for p in positions[:5])
        return top5 / total
    
    def compare_with_json(self, wallet: str) -> Dict:
        """
        对比重建数据与 JSON 数据
        
        Args:
            wallet: 钱包地址
            
        Returns:
            对比结果
        """
        # 重建数据
        rebuilt = self.rebuild_positions(wallet)
        rebuilt_total = sum(p.value for p in rebuilt)
        rebuilt_concentration = self.calculate_concentration(rebuilt)
        
        # JSON 数据
        json_file = Path(f'/home/xmren/.openclaw/workspace/polymarket-project/07-data/whale_states/{wallet}.json')
        if json_file.exists():
            import json
            with open(json_file) as f:
                json_data = json.load(f)
            
            json_positions = json_data.get('positions', {})
            json_total = sum(
                float(p.get('size', 0)) * float(p.get('curPrice', p.get('currentPrice', 0)))
                for p in json_positions.values()
            )
            
            return {
                'wallet': wallet,
                'rebuilt_positions': len(rebuilt),
                'json_positions': len(json_positions),
                'rebuilt_total': rebuilt_total,
                'json_total': json_total,
                'total_diff': abs(rebuilt_total - json_total),
                'rebuilt_concentration': rebuilt_concentration,
                'match': len(rebuilt) == len(json_positions)
            }
        else:
            return {
                'wallet': wallet,
                'rebuilt_positions': len(rebuilt),
                'json_positions': 0,
                'rebuilt_total': rebuilt_total,
                'json_total': 0,
                'total_diff': rebuilt_total,
                'rebuilt_concentration': rebuilt_concentration,
                'match': False,
                'note': 'JSON 文件不存在'
            }


def test_rebuilder():
    """测试重建器"""
    print("=" * 80)
    print("持仓重建器测试")
    print("=" * 80)
    
    rebuilder = PositionRebuilder()
    
    # 测试钱包
    test_wallets = [
        '0x0979bad57d7a1403db89cbcd9c52bf43f2138d9b',  # 有 JSON 的
        '0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1',  # Leaderboard #1
    ]
    
    for wallet in test_wallets:
        print(f"\n{'='*80}")
        print(f"测试钱包: {wallet}")
        print(f"{'='*80}")
        
        # 对比测试
        result = rebuilder.compare_with_json(wallet)
        
        print(f"\n对比结果:")
        print(f"  重建持仓数: {result['rebuilt_positions']}")
        print(f"  JSON 持仓数: {result['json_positions']}")
        print(f"  重建总价值: ${result['rebuilt_total']:,.0f}")
        print(f"  JSON 总价值: ${result['json_total']:,.0f}")
        print(f"  价值差异: ${result['total_diff']:,.0f}")
        print(f"  集中度: {result['rebuilt_concentration']*100:.1f}%")
        print(f"  是否匹配: {'✅' if result['match'] else '❌'}")
        
        if 'note' in result:
            print(f"  备注: {result['note']}")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    test_rebuilder()
