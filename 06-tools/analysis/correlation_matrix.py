#!/usr/bin/env python3
"""
相关性矩阵分析器
监控多个市场价格联动，发现偏离正常相关性的套利机会
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class CorrelationSignal:
    """相关性套利信号"""
    market_a: str
    market_b: str
    normal_correlation: float
    current_correlation: float
    divergence: float
    direction: str  # 'A_UP_B_DOWN' or 'A_DOWN_B_UP'
    confidence: float
    timestamp: datetime


class CorrelationMatrix:
    """市场价格相关性分析器"""
    
    def __init__(self, lookback_period: int = 100, correlation_threshold: float = 0.8):
        """
        初始化
        
        Args:
            lookback_period: 历史数据回看周期（默认100个数据点）
            correlation_threshold: 正常相关性阈值（默认0.8）
        """
        self.lookback_period = lookback_period
        self.correlation_threshold = correlation_threshold
        self.price_history = {}  # {market_id: [prices]}
        self.normal_correlations = {}  # {(a, b): correlation}
        
    def update_price(self, market_id: str, price: float, timestamp: datetime = None):
        """
        更新市场价格
        
        Args:
            market_id: 市场唯一标识
            price: 当前价格
            timestamp: 时间戳
        """
        if market_id not in self.price_history:
            self.price_history[market_id] = []
        
        self.price_history[market_id].append({
            'price': price,
            'timestamp': timestamp or datetime.now()
        })
        
        # 保持固定长度
        if len(self.price_history[market_id]) > self.lookback_period:
            self.price_history[market_id] = self.price_history[market_id][-self.lookback_period:]
    
    def calculate_correlation(self, market_a: str, market_b: str) -> float:
        """
        计算两个市场的价格相关系数
        
        Returns:
            皮尔逊相关系数 (-1 到 1)
        """
        if market_a not in self.price_history or market_b not in self.price_history:
            return 0.0
        
        prices_a = [p['price'] for p in self.price_history[market_a]]
        prices_b = [p['price'] for p in self.price_history[market_b]]
        
        # 确保长度相同
        min_len = min(len(prices_a), len(prices_b))
        if min_len < 10:  # 数据不足
            return 0.0
        
        prices_a = prices_a[-min_len:]
        prices_b = prices_b[-min_len:]
        
        # 计算皮尔逊相关系数
        correlation = np.corrcoef(prices_a, prices_b)[0, 1]
        
        return correlation if not np.isnan(correlation) else 0.0
    
    def build_correlation_matrix(self, markets: List[str]) -> pd.DataFrame:
        """
        构建相关性矩阵
        
        Args:
            markets: 市场ID列表
            
        Returns:
            DataFrame 形式的相关性矩阵
        """
        matrix = pd.DataFrame(index=markets, columns=markets)
        
        for i, market_a in enumerate(markets):
            for j, market_b in enumerate(markets):
                if i == j:
                    matrix.loc[market_a, market_b] = 1.0
                elif i < j:
                    corr = self.calculate_correlation(market_a, market_b)
                    matrix.loc[market_a, market_b] = corr
                    matrix.loc[market_b, market_a] = corr
        
        return matrix.astype(float)
    
    def detect_correlation_breakdown(self, markets: List[str]) -> List[CorrelationSignal]:
        """
        检测相关性断裂的套利机会
        
        Args:
            markets: 市场ID列表
            
        Returns:
            相关性套利信号列表
        """
        signals = []
        
        for i, market_a in enumerate(markets):
            for j, market_b in enumerate(markets[i+1:], i+1):
                # 计算当前相关性
                current_corr = self.calculate_correlation(market_a, market_b)
                
                # 获取或计算正常相关性
                pair_key = tuple(sorted([market_a, market_b]))
                if pair_key in self.normal_correlations:
                    normal_corr = self.normal_correlations[pair_key]
                else:
                    # 首次计算，设为当前值
                    self.normal_correlations[pair_key] = current_corr
                    continue
                
                # 检测相关性断裂
                if normal_corr > self.correlation_threshold:
                    # 原本高度相关
                    divergence = abs(current_corr - normal_corr)
                    
                    if divergence > 0.3:  # 相关性下降超过0.3
                        # 确定方向
                        prices_a = [p['price'] for p in self.price_history[market_a][-5:]]
                        prices_b = [p['price'] for p in self.price_history[market_b][-5:]]
                        
                        trend_a = (prices_a[-1] - prices_a[0]) / prices_a[0] if prices_a[0] > 0 else 0
                        trend_b = (prices_b[-1] - prices_b[0]) / prices_b[0] if prices_b[0] > 0 else 0
                        
                        if trend_a > 0 and trend_b < 0:
                            direction = 'A_UP_B_DOWN'
                        elif trend_a < 0 and trend_b > 0:
                            direction = 'A_DOWN_B_UP'
                        else:
                            direction = 'DIVERGED'
                        
                        signal = CorrelationSignal(
                            market_a=market_a,
                            market_b=market_b,
                            normal_correlation=normal_corr,
                            current_correlation=current_corr,
                            divergence=divergence,
                            direction=direction,
                            confidence=min(divergence, 1.0),
                            timestamp=datetime.now()
                        )
                        signals.append(signal)
        
        # 按置信度排序
        signals.sort(key=lambda x: x.confidence, reverse=True)
        return signals
    
    def get_correlation_clusters(self, markets: List[str], min_correlation: float = 0.7) -> List[List[str]]:
        """
        获取高相关性市场聚类
        
        Args:
            markets: 市场ID列表
            min_correlation: 最小相关性阈值
            
        Returns:
            市场聚类列表
        """
        matrix = self.build_correlation_matrix(markets)
        clusters = []
        visited = set()
        
        for market in markets:
            if market in visited:
                continue
            
            # 找到与当前市场高度相关的所有市场
            cluster = [market]
            visited.add(market)
            
            for other in markets:
                if other not in visited and matrix.loc[market, other] >= min_correlation:
                    cluster.append(other)
                    visited.add(other)
            
            if len(cluster) > 1:
                clusters.append(cluster)
        
        return clusters


def format_correlation_signal(signal: CorrelationSignal) -> str:
    """格式化相关性信号"""
    emoji = {
        'A_UP_B_DOWN': '📈📉',
        'A_DOWN_B_UP': '📉📈',
        'DIVERGED': '↔️'
    }.get(signal.direction, '↔️')
    
    return f"""
{emoji} *相关性断裂信号*

📊 {signal.market_a} vs {signal.market_b}

📉 相关性变化:
   正常: {signal.normal_correlation:.2f}
   当前: {signal.current_correlation:.2f}
   偏离: {signal.divergence:.2f}

📈 方向: {signal.direction}
✅ 置信度: {signal.confidence:.1%}

💡 建议: 关注两个市场的价格回归
"""


if __name__ == "__main__":
    # 测试
    analyzer = CorrelationMatrix(lookback_period=50)
    
    # 模拟数据
    import random
    random.seed(42)
    
    base_price = 0.5
    for i in range(60):
        # 市场A和B原本高度相关
        price_a = base_price + random.gauss(0, 0.02)
        price_b = base_price + random.gauss(0, 0.02)  # 相似走势
        
        if i > 40:  # 后期开始偏离
            price_b = base_price + random.gauss(0.1, 0.02)  # B上涨
        
        analyzer.update_price('BTC_100k', price_a)
        analyzer.update_price('ETH_5k', price_b)
    
    # 检测信号
    signals = analyzer.detect_correlation_breakdown(['BTC_100k', 'ETH_5k'])
    
    for signal in signals:
        print(format_correlation_signal(signal))
