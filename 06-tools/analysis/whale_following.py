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

# 添加项目路径 - 直接导入 database 模块，避免 Flask 依赖
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend/app/models"))

try:
    from database import db
except ImportError:
    # 备选：如果 database.py 不在预期位置，使用相对路径
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend"))
    from app.models.database import db


# 信号建议持有 48 小时（与 polymarket_monitor_v2.py::save_signals_to_db 的
# suggested_holding_hours 保持一致）。市场必须在这之后仍未关闭，才有机会拿到出场价。
SIGNAL_HOLDING_HOURS = 48


def _is_settleable_by_end_date(end_date: Optional[str], min_hours: int = SIGNAL_HOLDING_HOURS) -> bool:
    """市场距其 end_date（关闭时间）需 >= min_hours，否则信号到期前市场就已关闭/
    结算，settle_signals.py 永远拿不到出场价（表现为永久 pending → no_data）。

    用 daily_price_snapshots.end_date 数据驱动判定，取代手写前缀黑名单——黑名单
    覆盖不全（新品类、新联赛层出不穷）且容易和其他模块的同类名单分叉。
    """
    if not end_date:
        return False
    try:
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except ValueError:
        return False
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return end_dt - datetime.now(timezone.utc) >= timedelta(hours=min_hours)


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
        self.min_trade_size = 10         # $10 最小跟踪交易额（头部鲸鱼分散小注是正常模式）
        self.confidence_threshold = 0.7  # 置信度阈值

    def identify_smart_money(self, limit: int = 20) -> List[Whale]:
        """
        识别聪明钱鲸鱼

        数据源: leaderboard_whales（Polymarket 官方排行榜真实盈亏）
        标准:
        - PnL > $50k（真实盈利）
        - volume > $100k（足够交易深度）
        """
        print("🔍 识别聪明钱鲸鱼...")

        conn = db.get_connection()
        cursor = conn.cursor()

        # 用 leaderboard_whales 取真实历史盈亏，INNER JOIN changes 确保本地有行为记录
        # 只跟踪在我们 DB 里实际出现过的鲸鱼（没有 changes 记录无法产生信号）
        cursor.execute('''
            SELECT
                lw.wallet,
                lw.username,
                lw.pnl,
                lw.volume,
                COUNT(c.id) as recent_changes
            FROM leaderboard_whales lw
            JOIN changes c ON c.wallet = lw.wallet
              AND c.timestamp > datetime('now', '-30 days')
            WHERE lw.pnl > 50000
              AND lw.volume > 100000
            GROUP BY lw.wallet
            HAVING recent_changes > 0
            ORDER BY lw.pnl DESC
            LIMIT ?
        ''', (limit,))

        whales = []
        for row in cursor.fetchall():
            pnl = row[2] or 0
            volume = row[3] or 1
            recent_changes = row[4] or 0
            return_rate = pnl / volume  # 真实收益率（如 108万/193万 = 56%）
            whales.append(Whale(
                wallet=row[0],
                pseudonym=row[1] or row[0][:10] + "...",
                total_value=pnl,
                win_rate=return_rate,
                sharpe_ratio=return_rate,
                strategy_consistency=min(recent_changes / 50, 1.0)  # 50笔/30天为满分
            ))

        conn.close()

        print(f"   找到 {len(whales)} 个聪明钱鲸鱼")
        return whales

    def get_recent_changes(self, wallet: str, hours: int = 24) -> List[Dict]:
        """获取鲸鱼最近变动（直接查 changes 表，不依赖 positions JOIN）

        每条记录额外带 'is_settleable' 字段（见 _is_settleable_by_end_date）。
        """
        conn = db.get_connection()
        cursor = conn.cursor()

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        cursor.execute('''
            SELECT market, market_title, side, change_amount, timestamp
            FROM changes
            WHERE wallet = ?
              AND timestamp > ?
            ORDER BY ABS(change_amount) DESC
        ''', (wallet, cutoff.isoformat()))
        rows = cursor.fetchall()

        # 批量取每个市场"最新一条快照"的 end_date（不是 MAX(end_date)——end_date 会
        # 随时间推移变化，例如临近截止日下调，或电竞赛程重新排期，MAX() 会捡到历史
        # 上出现过的、已经过期的偏大值，导致市场被误判为"还很远才关闭"）。
        markets = {row[0] for row in rows}
        end_dates: Dict[str, str] = {}
        if markets:
            placeholders = ','.join('?' for _ in markets)
            cursor.execute(f'''
                SELECT market, end_date FROM (
                    SELECT market, end_date,
                           ROW_NUMBER() OVER (PARTITION BY market ORDER BY snapshot_date DESC) AS rn
                    FROM daily_price_snapshots
                    WHERE market IN ({placeholders})
                )
                WHERE rn = 1
            ''', tuple(markets))
            end_dates = {m: ed for m, ed in cursor.fetchall()}

        changes = []
        for row in rows:
            market = row[0]
            changes.append({
                'market': market,
                'market_title': row[1] or market,
                'direction': row[2],           # 'BUY' or 'SELL'
                'value_change': abs(row[3]),   # 直接用 change_amount，准确
                'timestamp': row[4],
                'is_settleable': _is_settleable_by_end_date(end_dates.get(market)),
            })

        conn.close()
        return changes

    def calculate_confidence(self, whale: Whale, change: Dict) -> float:
        """
        计算信号置信度

        权重:
        - 历史收益率: 40%（以 30% 收益率为满分基准）
        - 行为一致性: 30%（50笔交易为满分）
        - 交易规模: 20%（$5k 为满分）
        - 方向加成: 10%
        """
        # 1. 历史收益率 (40%)：30% return 以上算满分
        win_rate_score = min(whale.win_rate / 0.3, 1.0) * 0.4

        # 2. 行为一致性 (30%)
        consistency_score = min(whale.strategy_consistency, 1.0) * 0.3

        # 3. 交易规模 (20%)：$5k 以上为满分
        value_score = min(change['value_change'] / 5000, 1.0) * 0.2

        # 4. 方向加成 (10%)
        timing_score = 0.1 if change['direction'] == 'BUY' else 0.05

        total_score = win_rate_score + consistency_score + value_score + timing_score
        return min(total_score, 1.0)
    
    def calculate_suggested_position(self, whale: Whale, change: Dict) -> float:
        """计算建议跟随仓位"""
        # 基于鲸鱼仓位比例
        whale_position = change['value_change']
        
        # 建议跟随 1% - 5% 的鲸鱼仓位（按收益率判断）
        if whale.win_rate >= 0.4:   # 收益率 40%+
            ratio = 0.05
        elif whale.win_rate >= 0.2:  # 收益率 20%+
            ratio = 0.03
        else:
            ratio = 0.01
        
        suggested = whale_position * ratio
        
        # 限制范围
        return max(min(suggested, 5000), 100)  # $100 - $5000
    
    def detect_large_trade(self, whale: Whale, hours: int = 24) -> Optional[Signal]:
        """检测大额调仓信号"""
        changes = self.get_recent_changes(whale.wallet, hours=hours)
        
        for change in changes:
            # 过滤小额交易
            if change['value_change'] < self.min_trade_size:
                continue

            # 过滤距关闭时间不足持有期的市场（体育单场/加密短线/电竞等），信号无法结算
            if not change['is_settleable']:
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
            f"历史收益率: {whale.win_rate*100:.1f}%",
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