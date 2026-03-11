#!/usr/bin/env python3
"""
Pair Cost 扫描器单元测试
"""

import pytest
import json
from unittest.mock import patch, MagicMock

# 导入被测模块
from pair_cost_scanner import (
    calculate_pair_cost,
    PAIR_COST_THRESHOLD,
    MIN_LIQUIDITY
)


class TestCalculatePairCost:
    """测试 calculate_pair_cost 函数"""
    
    def test_valid_market(self, sample_market_data):
        """测试正常市场数据"""
        result = calculate_pair_cost(sample_market_data)
        
        assert result is not None
        assert result["yes_price"] == 0.52
        assert result["no_price"] == 0.47
        assert result["pair_cost"] == 0.99
        assert abs(result["profit_margin"] - 0.01) < 0.001
        assert abs(result["profit_pct"] - 1.0) < 0.001
        assert result["is_opportunity"] is False  # 0.99 < 0.99 is False
    
    def test_no_opportunity(self):
        """测试无套利机会的情况"""
        market = {
            "id": "123",
            "question": "Test",
            "slug": "test",
            "outcomePrices": ["0.60", "0.40"],  # Pair Cost = 1.0
            "liquidity": 100000,
            "volume": 50000,
            "endDate": "2026-12-31"
        }
        
        result = calculate_pair_cost(market)
        
        assert result is not None
        assert result["pair_cost"] == 1.0
        assert result["is_opportunity"] is False  # 1.0 >= 0.99
    
    def test_low_liquidity(self):
        """测试低流动性市场"""
        market = {
            "id": "123",
            "question": "Test",
            "slug": "test",
            "outcomePrices": ["0.50", "0.48"],  # Pair Cost = 0.98
            "liquidity": 500,  # 低于 MIN_LIQUIDITY
            "volume": 1000,
            "endDate": "2026-12-31"
        }
        
        result = calculate_pair_cost(market)
        
        assert result is not None
        assert result["pair_cost"] == 0.98
        assert result["is_opportunity"] is False  # 流动性不足
    
    def test_invalid_prices(self):
        """测试无效价格数据"""
        market = {
            "id": "123",
            "question": "Test",
            "slug": "test",
            "outcomePrices": "invalid",  # 无效格式
            "liquidity": 100000
        }
        
        result = calculate_pair_cost(market)
        
        assert result is None  # 应该返回 None
    
    def test_missing_prices(self):
        """测试缺少价格数据"""
        market = {
            "id": "123",
            "question": "Test",
            "slug": "test",
            "outcomePrices": [],  # 空数组
            "liquidity": 100000
        }
        
        result = calculate_pair_cost(market)
        
        assert result is None
    
    def test_string_prices(self):
        """测试字符串格式的价格"""
        market = {
            "id": "123",
            "question": "Test",
            "slug": "test",
            "outcomePrices": '["0.52", "0.47"]',  # JSON 字符串
            "liquidity": 100000,
            "volume": 50000,
            "endDate": "2026-12-31"
        }
        
        result = calculate_pair_cost(market)
        
        assert result is not None
        assert result["yes_price"] == 0.52
        assert result["no_price"] == 0.47


class TestThresholds:
    """测试阈值常量"""
    
    def test_pair_cost_threshold(self):
        """测试 Pair Cost 阈值"""
        assert PAIR_COST_THRESHOLD == 0.99
        assert isinstance(PAIR_COST_THRESHOLD, float)
    
    def test_min_liquidity(self):
        """测试最低流动性"""
        assert MIN_LIQUIDITY == 1000
        assert isinstance(MIN_LIQUIDITY, (int, float))


class TestEdgeCases:
    """测试边界情况"""
    
    def test_exact_threshold(self):
        """测试刚好在阈值上"""
        market = {
            "id": "123",
            "question": "Test",
            "slug": "test",
            "outcomePrices": ["0.505", "0.485"],  # Pair Cost = 0.99
            "liquidity": 100000,
            "volume": 50000,
            "endDate": "2026-12-31"
        }
        
        result = calculate_pair_cost(market)
        
        # 0.99 < 0.99 为 False，所以不是机会
        assert result["is_opportunity"] is False
    
    def test_just_below_threshold(self):
        """测试刚好低于阈值"""
        market = {
            "id": "123",
            "question": "Test",
            "slug": "test",
            "outcomePrices": ["0.50", "0.48"],  # Pair Cost = 0.98
            "liquidity": 100000,
            "volume": 50000,
            "endDate": "2026-12-31"
        }
        
        result = calculate_pair_cost(market)
        
        assert result["is_opportunity"] is True
        assert abs(result["profit_pct"] - 2.0) < 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
