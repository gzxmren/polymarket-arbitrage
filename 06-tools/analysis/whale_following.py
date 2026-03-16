#!/usr/bin/env python3
"""
鲸鱼跟随策略 V2
识别聪明钱鲸鱼，跟踪大额调仓，生成跟随信号
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend"))

from app.models.database import db


@dataclass
class Whale:
    """鲸鱼数据类"""
    wallet: str
    pseudonym: str
    total_value: float
    win_rate: float
    sharpe_ratio: float
    strategy_consistency: float


@dataclass
class Trade:
    """交易数据类"""
    market: str
    outcome: str
    size: float
    value: float
    timestamp: datetime
    direction: str  # 'BUY' or 'SELL'


@dataclass
class Signal:
    """信号数据类"""
    type: str
    whale: Whale
    market: str
    direction: str
    confidence: float
    suggested_position: float
    expected_return: float
    risk_level: str
    reasoning: str
    created_at: datetime


class WhaleFollowingStrategy:
    """鲸鱼跟随策略"""
    
    def __init__(self):
        self.min_trade_size = 10000      # $10k
        self.min_win_rate = 0.6          # 60%胜率
        self.min_sharpe = 0.5            # 最小夏普比率
        self.confidence_threshold = 0.7  # 置信度阈值
        
    def identify_smart_money(self, limit: int = 20) -> List[Whale]:
        """
        识别聪明钱鲸鱼
        
        标准:
        - 胜率 >= 60%
        - 交易次数 >= 10
        - 夏普比率 >= 0.5
        - 策略一致性 >= 0.6
        """
        print("🔍 识别聪明钱鲸鱼...")
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 查询鲸鱼胜率统计
        cursor.execute('''
            SELECT 
                wp.wallet,
                w.pseudonym,
                w.total_value,
                wp.win_rate,
                wp.sharpe_ratio,
                wp.strategy_consistency
            FROM whale_performance wp
            JOIN whales w ON wp.wallet = w.wallet
            WHERE wp.win_rate >= ?
              AND wp.total_trades >= 10
              AND wp.sharpe_ratio >= ?
              AND wp.strategy_consistency >= 0.6
            ORDER BY wp.win_rate DESC, wp.sharpe_ratio DESC
            LIMIT ?
        ''', (self.min_win_rate, self.min_sharpe, limit))
        
        whales = []
        for row in cursor.fetchall():
            whales.append(Whale(
                wallet=row[0],
                pseudonym=row[1] or row[0][:10] + "...",
                total_value=row[2] or 0,
                win_rate=row[3] or 0,
                sharpe_ratio=row[4] or 0,
                strategy_consistency=row[5] or 0
            ))
        
        conn.close()
        
        print(f"   找到 {len(whales)} 个聪明钱鲸鱼")
        return whales
    
    def get_recent_changes(self, wallet: str, hours: int = 1) -> List[Dict]:
        """获取鲸鱼最近变动"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        cursor.execute('''
            SELECT 
                c.market,
                c.outcome,
                c.new_size - c.old_size as size_change,
                c.timestamp,
                p.cur_price
            FROM changes c
            JOIN positions p ON c.wallet = p.wallet AND c.market = p.market
            WHERE c.wallet = ?
              AND c.timestamp > ?
            ORDER BY c.timestamp DESC
        ''', (wallet, cutoff.isoformat()))
        
        changes = []
        for row in cursor.fetchall():
            size_change = row[2] or 0
            price = row[4] or 0
            value_change = abs(size_change) * price
            
            changes.append({
                'market': row[0],
                'outcome': row[1],
                'size_change': size_change,
                'value_change': value_change,
                'timestamp': row[3],
                'direction': 'BUY' if size_change > 0 else 'SELL'
            })
        
        conn.close()
        return changes
    
    def calculate_confidence(self, whale: Whale, change: Dict) -> float:
        """
        计算信号置信度
        
        权重:
        - 历史胜率: 40%
        - 行为一致性: 30%
        - 市场规模: 20%
        - 时机: 10%
        """
        # 1. 历史胜率 (40%)
        win_rate_score = min(whale.win_rate, 1.0) * 0.4
        
        # 2. 行为一致性 (30%)
        consistency_score = min(whale.strategy_consistency, 1.0) * 0.3
        
        # 3. 市场规模 (20%)
        # 大额交易更有信心
        value_score = min(change['value_change'] / 50000, 1.0) * 0.2
        
        # 4. 时机 (10%)
        # 连续同方向调仓增加信心
        timing_score = 0.1 if change['direction'] == 'BUY' else 0.05
        
        total_score = win_rate_score + consistency_score + value_score + timing_score
        return min(total_score, 1.0)
    
    def calculate_suggested_position(self, whale: Whale, change: Dict) -> float:
        """计算建议跟随仓位"""
        # 基于鲸鱼仓位比例
        whale_position = change['value_change']
        
        # 建议跟随 1% - 5% 的鲸鱼仓位
        if whale.win_rate >= 0.7:
            ratio = 0.05  # 高胜率，跟随5%
        elif whale.win_rate >= 0.6:
            ratio = 0.03  # 中等胜率，跟随3%
        else:
            ratio = 0.01  # 低胜率，跟随1%
        
        suggested = whale_position * ratio
        
        # 限制范围
        return max(min(suggested, 5000), 100)  # $100 - $5000
    
    def detect_large_trade(self, whale: Whale) -> Optional[Signal]:
        """检测大额调仓信号"""
        changes = self.get_recent_changes(whale.wallet, hours=1)
        
        for change in changes:
            # 过滤小额交易
            if change['value_change'] < self.min_trade_size:
                continue
            
            # 计算置信度
            confidence = self.calculate_confidence(whale, change)
            
            # 过滤低置信度
            if confidence < self.confidence_threshold:
                continue
            
            # 计算建议仓位
            suggested_position = self.calculate_suggested_position(whale, change)
            
            # 确定风险等级
            if confidence >= 0.85:
                risk_level = "低风险"
            elif confidence >= 0.75:
                risk_level = "中风险"
            else:
                risk_level = "高风险"
            
            # 生成推理
            reasoning = self.generate_reasoning(whale, change, confidence)
            
            return Signal(
                type='whale_following',
                whale=whale,
                market=change['market'],
                direction=change['direction'],
                confidence=confidence,
                suggested_position=suggested_position,
                expected_return=0.1,  # 预期10%收益
                risk_level=risk_level,
                reasoning=reasoning,
                created_at=datetime.now(timezone.utc)
            )
        
        return None
    
    def generate_reasoning(self, whale: Whale, change: Dict, confidence: float) -> str:
        """生成信号推理说明"""
        lines = [
            f"🐋 聪明钱跟随信号",
            f"",
            f"鲸鱼: {whale.pseudonym}",
            f"历史胜率: {whale.win_rate*100:.1f}%",
            f"夏普比率: {whale.sharpe_ratio:.2f}",
            f"",
            f"操作: {change['direction']} {change['market'][:50]}",
            f"仓位: ${change['value_change']:,.0f}",
            f"",
            f"置信度: {confidence*100:.1f}%",
            f"风险等级: {'低风险' if confidence >= 0.85 else '中风险' if confidence >= 0.75 else '高风险'}",
            f"",
            f"建议: 考虑跟随，目标仓位 ${self.calculate_suggested_position(whale, change):,.0f}"
        ]
        return "\n".join(lines)
    
    def scan(self) -> List[Signal]:
        """扫描所有聪明钱鲸鱼，生成信号"""
        print("\n🚀 启动鲸鱼跟随策略扫描...")
        
        signals = []
        
        # 1. 识别聪明钱鲸鱼
        smart_whales = self.identify_smart_money(limit=20)
        
        if not smart_whales:
            print("   ⚪ 未找到聪明钱鲸鱼")
            return signals
        
        print(f"   扫描 {len(smart_whales)} 个聪明钱鲸鱼...")
        
        # 2. 检查每个鲸鱼的大额调仓
        for whale in smart_whales:
            signal = self.detect_large_trade(whale)
            if signal:
                signals.append(signal)
                print(f"   ✅ 发现信号: {whale.pseudonym} - {signal.market[:30]}...")
        
        print(f"\n📊 扫描完成: 发现 {len(signals)} 个信号")
        return signals


def format_signal_telegram(signal: Signal) -> str:
    """格式化信号为Telegram消息"""
    confidence_emoji = "🟢" if signal.confidence >= 0.85 else "🟡" if signal.confidence >= 0.75 else "🔴"
    
    return f"""
🐋 *聪明钱跟随信号*

{confidence_emoji} 置信度: {signal.confidence*100:.1f}%
📊 风险等级: {signal.risk_level}

*鲸鱼信息:*
👤 {signal.whale.pseudonym}
📈 胜率: {signal.whale.win_rate*100:.1f}%
⭐ 夏普: {signal.whale.sharpe_ratio:.2f}

*信号详情:*
📍 市场: {signal.market}
📈 方向: {signal.direction}
💰 建议仓位: ${signal.suggested_position:,.0f}
🎯 预期收益: {signal.expected_return*100:.1f}%

*推理:*
{signal.reasoning}

⏰ {signal.created_at.strftime('%Y-%m-%d %H:%M')}
"""


# 测试代码
if __name__ == "__main__":
    print("🧪 测试鲸鱼跟随策略...")
    
    strategy = WhaleFollowingStrategy()
    
    # 测试扫描
    signals = strategy.scan()
    
    if signals:
        print("\n📤 格式化信号:")
        for signal in signals:
            print(format_signal_telegram(signal))
    else:
        print("\n⚪ 未发现信号")
    
    print("\n✅ 测试完成!")