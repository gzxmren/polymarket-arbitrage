#!/usr/bin/env python3
"""
鲸鱼综合分类器
基于多维度指标对鲸鱼进行智能分类和评分
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

# 数据库路径
DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'


class WhaleTier(Enum):
    """鲸鱼级别"""
    S = "S级"      # 超级鲸鱼 (综合评分 >= 90)
    A = "A级"      # 重点关注 (综合评分 >= 70)
    B = "B级"      # 普通跟踪 (综合评分 >= 50)
    C = "C级"      # 观察名单 (综合评分 >= 30)
    D = "D级"      # 普通用户 (综合评分 < 30)


@dataclass
class WhaleMetrics:
    """鲸鱼指标数据类"""
    wallet: str
    pseudonym: str
    
    # 资金规模维度
    total_value: float = 0.0              # 总持仓价值
    avg_position_value: float = 0.0       # 平均持仓价值
    max_position_value: float = 0.0       # 最大持仓价值
    capital_score: float = 0.0            # 资金规模得分 (0-100)
    
    # 活跃度维度
    trade_count_30d: int = 0              # 30天交易次数
    trade_count_7d: int = 0               # 7天交易次数
    active_days_30d: int = 0              # 30天活跃天数
    daily_avg_trades: float = 0.0         # 日均交易次数
    activity_score: float = 0.0           # 活跃度得分 (0-100)
    
    # 盈利能力维度
    total_pnl: float = 0.0                # 总盈亏
    win_rate: float = 0.0                 # 胜率
    profit_factor: float = 0.0            # 盈亏比
    avg_profit_per_trade: float = 0.0     # 平均每笔盈利
    profitability_score: float = 0.0      # 盈利能力得分 (0-100)
    
    # 策略维度
    market_count: int = 0                 # 参与市场数
    top_market_concentration: float = 0.0 # 持仓集中度
    avg_holding_days: float = 0.0         # 平均持仓天数
    strategy_score: float = 0.0           # 策略得分 (0-100)
    
    # 趋势维度
    value_trend: str = ""                 # 价值趋势 (上升/下降/稳定)
    activity_trend: str = ""              # 活跃度趋势
    pnl_trend: str = ""                   # 盈亏趋势
    trend_score: float = 0.0              # 趋势得分 (0-100)
    
    # 综合评分
    overall_score: float = 0.0            # 综合评分 (0-100)
    tier: WhaleTier = WhaleTier.D         # 级别
    
    # 元数据
    last_updated: datetime = None         # 最后更新时间
    data_quality: str = ""                # 数据质量 (高/中/低)


class WhaleClassifier:
    """鲸鱼分类器"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        
    def __del__(self):
        if hasattr(self, 'conn'):
            self.conn.close()
    
    def _get_cursor(self):
        return self.conn.cursor()
    
    def calculate_capital_score(self, total_value: float, avg_position: float) -> float:
        """
        计算资金规模得分
        
        评分标准:
        - $5M+: 100分
        - $1M-$5M: 80-99分
        - $500k-$1M: 60-79分
        - $100k-$500k: 40-59分
        - $50k-$100k: 20-39分
        - $10k-$50k: 10-19分
        - <$10k: 0-9分
        """
        if total_value >= 5_000_000:
            return 100.0
        elif total_value >= 1_000_000:
            return 80 + (total_value - 1_000_000) / 4_000_000 * 19
        elif total_value >= 500_000:
            return 60 + (total_value - 500_000) / 500_000 * 19
        elif total_value >= 100_000:
            return 40 + (total_value - 100_000) / 400_000 * 19
        elif total_value >= 50_000:
            return 20 + (total_value - 50_000) / 50_000 * 19
        elif total_value >= 10_000:
            return 10 + (total_value - 10_000) / 40_000 * 9
        else:
            return max(0, total_value / 10_000 * 10)
    
    def calculate_activity_score(self, trade_count_30d: int, active_days: int) -> float:
        """
        计算活跃度得分
        
        评分标准:
        - 日均 > 50笔: 100分 (高频做市商)
        - 日均 20-50笔: 70-99分
        - 日均 5-20笔: 40-69分
        - 日均 1-5笔: 20-39分
        - 日均 < 1笔: 0-19分
        """
        daily_avg = trade_count_30d / 30 if trade_count_30d > 0 else 0
        
        if daily_avg >= 50:
            return 100.0
        elif daily_avg >= 20:
            return 70 + (daily_avg - 20) / 30 * 29
        elif daily_avg >= 5:
            return 40 + (daily_avg - 5) / 15 * 29
        elif daily_avg >= 1:
            return 20 + (daily_avg - 1) / 4 * 19
        else:
            return max(0, daily_avg * 20)
    
    def calculate_profitability_score(
        self, 
        total_pnl: float, 
        win_rate: float,
        profit_factor: float
    ) -> float:
        """
        计算盈利能力得分
        
        评分标准:
        - 胜率 > 60% + 正收益: 90-100分
        - 胜率 50-60% + 正收益: 70-89分
        - 胜率 40-50% + 正收益: 50-69分
        - 胜率 < 40% 但收益为正: 30-49分
        - 亏损: 0-29分
        """
        if total_pnl <= 0:
            # 亏损情况
            return max(0, 30 + total_pnl / 10000 * 30)
        
        # 正收益情况
        base_score = 50
        
        # 胜率加成
        if win_rate >= 0.60:
            base_score += 40
        elif win_rate >= 0.50:
            base_score += 20 + (win_rate - 0.50) / 0.10 * 19
        elif win_rate >= 0.40:
            base_score += (win_rate - 0.40) / 0.10 * 19
        
        # 盈亏比加成
        if profit_factor >= 2.0:
            base_score += 10
        elif profit_factor >= 1.5:
            base_score += 5
        
        return min(100, base_score)
    
    def calculate_strategy_score(
        self,
        market_count: int,
        concentration: float,
        avg_holding_days: float
    ) -> float:
        """
        计算策略得分
        
        评分标准:
        - 分散投资 (市场数 > 20 + 集中度 < 50%): 高分
        - 集中投资 (市场数 < 5 + 集中度 > 80%): 中等
        - 持仓周期适中 (7-30天): 加分
        """
        score = 50.0  # 基础分
        
        # 分散度评分
        if market_count >= 20 and concentration <= 0.5:
            score += 30  # 高度分散
        elif market_count >= 10 and concentration <= 0.7:
            score += 20  # 中度分散
        elif market_count >= 5:
            score += 10  # 轻度分散
        
        # 集中度评分 (反向)
        if concentration >= 0.9:
            score -= 20  # 过度集中
        elif concentration >= 0.7:
            score -= 10
        
        # 持仓周期评分
        if 7 <= avg_holding_days <= 30:
            score += 20  # 适中周期
        elif avg_holding_days > 30:
            score += 10  # 长线
        elif avg_holding_days < 3:
            score -= 10  # 超短线
        
        return max(0, min(100, score))
    
    def calculate_trend_score(
        self,
        value_change_7d: float,
        value_change_30d: float,
        activity_change: float
    ) -> float:
        """
        计算趋势得分
        
        评分标准:
        - 价值持续上升: 高分
        - 活跃度增加: 加分
        - 综合趋势判断
        """
        score = 50.0
        
        # 价值趋势
        if value_change_7d > 0.1 and value_change_30d > 0:
            score += 30  # 短期和长期都上升
        elif value_change_30d > 0.2:
            score += 20  # 长期上升
        elif value_change_7d > 0:
            score += 10  # 短期上升
        elif value_change_7d < -0.1:
            score -= 20  # 短期下降
        
        # 活跃度趋势
        if activity_change > 0.5:
            score += 10  # 活跃度大增
        elif activity_change < -0.5:
            score -= 10  # 活跃度大降
        
        return max(0, min(100, score))
    
    def get_wallet_metrics(self, wallet: str) -> Optional[WhaleMetrics]:
        """获取单个钱包的综合指标"""
        cursor = self._get_cursor()
        
        # 基础信息
        cursor.execute('''
            SELECT pseudonym, total_value, total_pnl, position_count, top5_ratio
            FROM whales
            WHERE wallet = ?
        ''', (wallet,))
        
        whale_row = cursor.fetchone()
        if not whale_row:
            return None
        
        pseudonym = whale_row['pseudonym'] or wallet[:10] + '...'
        total_value = whale_row['total_value'] or 0
        total_pnl = whale_row['total_pnl'] or 0
        position_count = whale_row['position_count'] or 0
        top5_ratio = whale_row['top5_ratio'] or 0
        
        # 30天交易统计
        cursor.execute('''
            SELECT 
                COUNT(*) as trade_count,
                COUNT(DISTINCT date(timestamp)) as active_days,
                SUM(change_amount) as total_volume,
                AVG(change_amount) as avg_trade
            FROM changes
            WHERE wallet = ?
              AND timestamp > datetime('now', '-30 days')
        ''', (wallet,))
        
        trade_30d = cursor.fetchone()
        trade_count_30d = trade_30d['trade_count'] or 0
        active_days_30d = trade_30d['active_days'] or 0
        
        # 7天交易统计
        cursor.execute('''
            SELECT COUNT(*) as trade_count
            FROM changes
            WHERE wallet = ?
              AND timestamp > datetime('now', '-7 days')
        ''', (wallet,))
        
        trade_7d = cursor.fetchone()
        trade_count_7d = trade_7d['trade_count'] or 0
        
        # 计算日均交易
        daily_avg_trades = trade_count_30d / 30 if trade_count_30d > 0 else 0
        
        # 计算平均持仓价值
        avg_position_value = total_value / position_count if position_count > 0 else 0
        
        # 获取最大持仓
        cursor.execute('''
            SELECT MAX(value) as max_value
            FROM positions
            WHERE wallet = ?
        ''', (wallet,))
        
        max_pos = cursor.fetchone()
        max_position_value = max_pos['max_value'] or 0
        
        # 计算胜率 (简化版：假设盈利为正向变动)
        # 实际应该根据 outcome 计算，这里用变动金额作为近似
        cursor.execute('''
            SELECT 
                COUNT(CASE WHEN change_amount > 0 THEN 1 END) as wins,
                COUNT(*) as total
            FROM changes
            WHERE wallet = ?
              AND timestamp > datetime('now', '-30 days')
        ''', (wallet,))
        
        win_row = cursor.fetchone()
        win_rate = win_row['wins'] / win_row['total'] if win_row['total'] > 0 else 0
        
        # 计算盈亏比 (简化)
        cursor.execute('''
            SELECT 
                AVG(CASE WHEN change_amount > 0 THEN change_amount END) as avg_win,
                AVG(CASE WHEN change_amount < 0 THEN ABS(change_amount) END) as avg_loss
            FROM changes
            WHERE wallet = ?
              AND timestamp > datetime('now', '-30 days')
        ''', (wallet,))
        
        pl_row = cursor.fetchone()
        avg_win = pl_row['avg_win'] or 0
        avg_loss = pl_row['avg_loss'] or 1  # 避免除零
        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0
        
        # 计算平均每笔盈利
        avg_profit_per_trade = total_pnl / trade_count_30d if trade_count_30d > 0 else 0
        
        # 计算市场数
        cursor.execute('''
            SELECT COUNT(DISTINCT market) as market_count
            FROM positions
            WHERE wallet = ?
        ''', (wallet,))
        
        market_row = cursor.fetchone()
        market_count = market_row['market_count'] or 0
        
        # 计算趋势 (7天 vs 30天价值变化)
        cursor.execute('''
            SELECT 
                SUM(CASE WHEN timestamp > datetime('now', '-7 days') 
                    THEN change_amount ELSE 0 END) as change_7d,
                SUM(change_amount) as change_30d
            FROM changes
            WHERE wallet = ?
              AND timestamp > datetime('now', '-30 days')
        ''', (wallet,))
        
        trend_row = cursor.fetchone()
        change_7d = trend_row['change_7d'] or 0
        change_30d = trend_row['change_30d'] or 0
        
        value_change_7d = change_7d / total_value if total_value > 0 else 0
        value_change_30d = change_30d / total_value if total_value > 0 else 0
        
        # 活跃度趋势 (7天 vs 前23天)
        activity_change = (trade_count_7d / 7 - (trade_count_30d - trade_count_7d) / 23) / ((trade_count_30d - trade_count_7d) / 23 + 0.001)
        
        # 创建指标对象
        metrics = WhaleMetrics(
            wallet=wallet,
            pseudonym=pseudonym,
            total_value=total_value,
            avg_position_value=avg_position_value,
            max_position_value=max_position_value,
            trade_count_30d=trade_count_30d,
            trade_count_7d=trade_count_7d,
            active_days_30d=active_days_30d,
            daily_avg_trades=daily_avg_trades,
            total_pnl=total_pnl,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_profit_per_trade=avg_profit_per_trade,
            market_count=market_count,
            top_market_concentration=top5_ratio,
            avg_holding_days=0,  # 需要更复杂计算
            value_trend='up' if value_change_7d > 0 else 'down',
            activity_trend='up' if activity_change > 0 else 'down',
            pnl_trend='up' if total_pnl > 0 else 'down',
            last_updated=datetime.now(),
            data_quality='high' if trade_count_30d >= 10 else 'medium' if trade_count_30d >= 3 else 'low'
        )
        
        # 计算各项得分
        metrics.capital_score = self.calculate_capital_score(total_value, avg_position_value)
        metrics.activity_score = self.calculate_activity_score(trade_count_30d, active_days_30d)
        metrics.profitability_score = self.calculate_profitability_score(total_pnl, win_rate, profit_factor)
        metrics.strategy_score = self.calculate_strategy_score(market_count, top5_ratio, 0)
        metrics.trend_score = self.calculate_trend_score(value_change_7d, value_change_30d, activity_change)
        
        # 计算综合评分 (加权)
        metrics.overall_score = (
            metrics.capital_score * 0.30 +      # 资金规模 30%
            metrics.activity_score * 0.20 +     # 活跃度 20%
            metrics.profitability_score * 0.30 + # 盈利能力 30%
            metrics.strategy_score * 0.10 +     # 策略 10%
            metrics.trend_score * 0.10          # 趋势 10%
        )
        
        # 确定级别
        if metrics.overall_score >= 90:
            metrics.tier = WhaleTier.S
        elif metrics.overall_score >= 70:
            metrics.tier = WhaleTier.A
        elif metrics.overall_score >= 50:
            metrics.tier = WhaleTier.B
        elif metrics.overall_score >= 30:
            metrics.tier = WhaleTier.C
        else:
            metrics.tier = WhaleTier.D
        
        return metrics
    
    def classify_all_whales(self, limit: int = 100) -> List[WhaleMetrics]:
        """分类所有鲸鱼"""
        cursor = self._get_cursor()
        
        cursor.execute('''
            SELECT wallet
            FROM whales
            WHERE total_value > 0
            ORDER BY total_value DESC
            LIMIT ?
        ''', (limit,))
        
        wallets = [row['wallet'] for row in cursor.fetchall()]
        
        results = []
        for wallet in wallets:
            metrics = self.get_wallet_metrics(wallet)
            if metrics:
                results.append(metrics)
        
        # 按综合评分排序
        results.sort(key=lambda x: x.overall_score, reverse=True)
        
        return results
    
    def generate_report(self, metrics_list: List[WhaleMetrics]) -> str:
        """生成分类报告"""
        lines = []
        lines.append("=" * 100)
        lines.append("鲸鱼综合分类报告")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 100)
        
        # 统计
        tier_counts = {}
        for m in metrics_list:
            tier_counts[m.tier] = tier_counts.get(m.tier, 0) + 1
        
        lines.append("\n级别分布:")
        for tier in [WhaleTier.S, WhaleTier.A, WhaleTier.B, WhaleTier.C, WhaleTier.D]:
            count = tier_counts.get(tier, 0)
            lines.append(f"  {tier.value}: {count} 个")
        
        # Top 20 详情
        lines.append("\n" + "=" * 100)
        lines.append("Top 20 鲸鱼详情:")
        lines.append("=" * 100)
        lines.append(f"{'排名':<6} {'级别':<6} {'综合分':<8} {'名称':<25} {'持仓':<12} {'30天交易':<10} {'胜率':<8} {'盈亏':<12}")
        lines.append("-" * 100)
        
        for i, m in enumerate(metrics_list[:20], 1):
            lines.append(
                f"{i:<6} {m.tier.value:<6} {m.overall_score:<8.1f} "
                f"{m.pseudonym[:23]:<25} ${m.total_value/1000:>8.1f}k "
                f"{m.trade_count_30d:<10} {m.win_rate*100:>6.1f}% "
                f"${m.total_pnl:>10,.0f}"
            )
        
        # 详细指标
        lines.append("\n" + "=" * 100)
        lines.append("详细指标 (Top 10):")
        lines.append("=" * 100)
        
        for i, m in enumerate(metrics_list[:10], 1):
            lines.append(f"\n{i}. {m.pseudonym} ({m.tier.value})")
            lines.append(f"   资金规模: {m.capital_score:.1f}分 | 持仓: ${m.total_value:,.0f}")
            lines.append(f"   活跃度: {m.activity_score:.1f}分 | 30天交易: {m.trade_count_30d}次 | 日均: {m.daily_avg_trades:.1f}")
            lines.append(f"   盈利能力: {m.profitability_score:.1f}分 | 胜率: {m.win_rate*100:.1f}% | 盈亏比: {m.profit_factor:.2f}")
            lines.append(f"   策略: {m.strategy_score:.1f}分 | 市场数: {m.market_count} | 集中度: {m.top_market_concentration*100:.1f}%")
            lines.append(f"   趋势: {m.trend_score:.1f}分 | 价值趋势: {m.value_trend} | 活跃度趋势: {m.activity_trend}")
            lines.append(f"   数据质量: {m.data_quality}")
        
        lines.append("\n" + "=" * 100)
        return "\n".join(lines)


def main():
    """主函数"""
    print("🐋 启动鲸鱼分类器...")
    
    classifier = WhaleClassifier()
    
    print("📊 分析前 100 个鲸鱼...")
    metrics = classifier.classify_all_whales(limit=100)
    
    print(f"✅ 完成分析: {len(metrics)} 个鲸鱼")
    
    # 生成报告
    report = classifier.generate_report(metrics)
    print(report)
    
    # 保存到文件
    output_file = Path('/tmp/whale_classification_report.txt')
    with open(output_file, 'w') as f:
        f.write(report)
    
    print(f"\n📄 报告已保存: {output_file}")
    
    # 输出 S级和A级鲸鱼
    print("\n" + "=" * 80)
    print("🌟 S级和A级鲸鱼列表 (建议重点关注):")
    print("=" * 80)
    
    top_whales = [m for m in metrics if m.tier in [WhaleTier.S, WhaleTier.A]]
    for m in top_whales[:10]:
        print(f"  {m.tier.value} | {m.pseudonym:<25} | 评分: {m.overall_score:.1f}")
    
    return metrics


if __name__ == "__main__":
    main()