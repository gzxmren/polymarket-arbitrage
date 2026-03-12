#!/usr/bin/env python3
"""
Kelly 准则仓位管理
根据胜率和赔率计算最优仓位
"""

import math


def kelly_criterion(win_prob: float, win_loss_ratio: float) -> float:
    """
    计算 Kelly 最优仓位比例
    
    Args:
        win_prob: 胜率 (0-1)
        win_loss_ratio: 盈亏比 (平均盈利/平均亏损)
    
    Returns:
        最优仓位比例 (0-1)
    """
    if win_prob <= 0 or win_prob >= 1:
        return 0
    
    if win_loss_ratio <= 0:
        return 0
    
    # Kelly 公式: f* = (bp - q) / b
    # b = 盈亏比, p = 胜率, q = 败率 = 1-p
    b = win_loss_ratio
    p = win_prob
    q = 1 - p
    
    kelly = (b * p - q) / b
    
    # 限制在 0-1 之间
    return max(0, min(1, kelly))


def fractional_kelly(win_prob: float, win_loss_ratio: float, 
                     fraction: float = 0.25) -> float:
    """
    分数 Kelly（更保守）
    
    Args:
        fraction: Kelly 分数（如 0.25 = 1/4 Kelly）
    """
    kelly = kelly_criterion(win_prob, win_loss_ratio)
    return kelly * fraction


def calculate_position_size(capital: float, win_prob: float, 
                           expected_profit: float, max_loss: float,
                           kelly_fraction: float = 0.25) -> dict:
    """
    计算建议仓位
    
    Args:
        capital: 总资金
        win_prob: 胜率估计
        expected_profit: 预期盈利
        max_loss: 最大亏损
        kelly_fraction: Kelly 分数
    
    Returns:
        {
            'kelly_ratio': Kelly 比例,
            'position_ratio': 建议仓位比例,
            'position_size': 建议仓位金额,
            'risk_amount': 风险金额
        }
    """
    if max_loss <= 0:
        return {'error': 'max_loss must be positive'}
    
    win_loss_ratio = expected_profit / max_loss
    
    kelly = kelly_criterion(win_prob, win_loss_ratio)
    position_ratio = fractional_kelly(win_prob, win_loss_ratio, kelly_fraction)
    
    position_size = capital * position_ratio
    risk_amount = position_size * (max_loss / expected_profit) if expected_profit > 0 else 0
    
    return {
        'kelly_ratio': kelly,
        'position_ratio': position_ratio,
        'position_size': position_size,
        'risk_amount': risk_amount,
        'capital': capital,
        'win_prob': win_prob,
        'win_loss_ratio': win_loss_ratio
    }


def assess_opportunity(profit_margin: float, risk_score: float, 
                      historical_win_rate: float = 0.6) -> dict:
    """
    评估套利机会并给出仓位建议
    
    Args:
        profit_margin: 利润空间 (如 0.02 = 2%)
        risk_score: 风险分数 (0-1, 越低越好)
        historical_win_rate: 历史胜率
    """
    # 根据风险调整胜率估计
    adjusted_win_prob = historical_win_rate * (1 - risk_score)
    
    # 盈亏比 = 利润 / 风险
    win_loss_ratio = profit_margin / max(risk_score, 0.01)
    
    # 计算 Kelly
    kelly = kelly_criterion(adjusted_win_prob, win_loss_ratio)
    
    return {
        'adjusted_win_prob': adjusted_win_prob,
        'win_loss_ratio': win_loss_ratio,
        'kelly_ratio': kelly,
        'suggested_position': fractional_kelly(adjusted_win_prob, win_loss_ratio, 0.25),
        'assessment': 'GOOD' if kelly > 0.1 else 'MARGINAL' if kelly > 0 else 'AVOID'
    }


if __name__ == "__main__":
    # 测试
    result = calculate_position_size(
        capital=10000,
        win_prob=0.7,
        expected_profit=100,
        max_loss=50,
        kelly_fraction=0.25
    )
    print(result)
