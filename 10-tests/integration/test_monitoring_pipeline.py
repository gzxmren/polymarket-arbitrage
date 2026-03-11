#!/usr/bin/env python3
"""
监控管道集成测试
测试完整监控流程
"""

import pytest
from unittest.mock import patch, MagicMock
import json


class TestMonitoringPipeline:
    """测试完整监控流程"""
    
    @pytest.mark.integration
    def test_full_scan_workflow(self):
        """测试完整扫描工作流"""
        # 模拟 API 响应
        mock_markets = [
            {
                "id": "1",
                "question": "Test Market 1",
                "slug": "test-1",
                "outcomePrices": ["0.52", "0.47"],
                "liquidity": 50000,
                "volume": 100000,
                "endDate": "2026-06-30T23:59:59Z"
            },
            {
                "id": "2",
                "question": "Test Market 2",
                "slug": "test-2",
                "outcomePrices": ["0.60", "0.40"],
                "liquidity": 100000,
                "volume": 200000,
                "endDate": "2026-06-30T23:59:59Z"
            }
        ]
        
        mock_trades = [
            {"proxyWallet": "0x123", "size": 100, "price": 0.5, "pseudonym": "Whale1"},
            {"proxyWallet": "0x456", "size": 200, "price": 0.6, "pseudonym": "Whale2"}
        ]
        
        # Mock API 调用
        with patch('pair_cost_scanner.fetch_active_markets', return_value=mock_markets):
            with patch('whale_tracker_v2.fetch_recent_trades', return_value=mock_trades):
                # 运行扫描
                from pair_cost_scanner import scan_pair_cost_opportunities
                
                opportunities = scan_pair_cost_opportunities(limit=10)
                
                # 验证结果
                assert isinstance(opportunities, list)
                # 第一个市场应该是机会（0.52+0.47=0.99 < 0.99？不，等于0.99，所以不是）
                # 第二个市场（0.60+0.40=1.0）不是机会
    
    @pytest.mark.integration
    def test_risk_review_integration(self):
        """测试风险评估集成"""
        from pair_cost_scanner import calculate_pair_cost
        from risk_reviewer import review_pair_cost_opportunity
        
        # 创建测试市场
        market = {
            "id": "1",
            "question": "Test",
            "slug": "test",
            "outcomePrices": ["0.50", "0.48"],
            "liquidity": 50000,
            "volume": 100000,
            "endDate": "2026-06-30T23:59:59Z"
        }
        
        # 计算机会
        opp = calculate_pair_cost(market)
        assert opp is not None
        
        # 风险评估
        review = review_pair_cost_opportunity(opp)
        assert "risk_level" in review
        assert "approved" in review
        assert "concerns" in review
    
    @pytest.mark.integration
    def test_notification_integration(self):
        """测试通知集成"""
        from telegram_notifier_v2 import format_pair_cost_alert
        
        opp = {
            "question": "Test Market",
            "slug": "test-market",
            "yes_price": 0.52,
            "no_price": 0.47,
            "pair_cost": 0.99,
            "profit_pct": 1.0,
            "liquidity": 50000,
            "volume": 100000,
            "end_date": "2026-06-30T23:59:59Z"
        }
        
        # 格式化通知
        message = format_pair_cost_alert(opp)
        
        # 验证格式
        assert "🎯" in message
        assert "套利机会" in message
        assert "1.0%" in message
        assert "https://polymarket.com" in message


class TestErrorHandling:
    """测试错误处理"""
    
    @pytest.mark.integration
    def test_api_failure_handling(self):
        """测试 API 失败处理"""
        from pair_cost_scanner import fetch_active_markets
        
        # Mock API 失败
        with patch('pair_cost_scanner.fetch_api', return_value=None):
            markets = fetch_active_markets(limit=10)
            assert markets == []  # 应该返回空列表，不崩溃
    
    @pytest.mark.integration
    def test_malformed_data_handling(self):
        """测试异常数据处理"""
        from pair_cost_scanner import calculate_pair_cost
        
        # 异常数据
        bad_market = {
            "id": "1",
            "question": "Test",
            "outcomePrices": "invalid_data",  # 无效格式
            "liquidity": "not_a_number"
        }
        
        result = calculate_pair_cost(bad_market)
        assert result is None  # 应该返回 None，不崩溃


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
