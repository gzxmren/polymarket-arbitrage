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
        """通知链路的**键契约**:产出方给的字段,必须刚好够消费方用。

        ## 这条判据长期是红的,而两个红因都在判据自己身上(2026-08-06 修)

        1. 手写的假 `opp` 缺 `profit_margin` → `format_pair_cost_alert` 抛 KeyError。
           真正的产出方 `pair_cost_scanner.calculate_pair_cost` **两个键都给**,
           所以生产没崩 —— **是假数据漂移了,不是代码坏了**。
        2. 断言写的是 `"1.0%"`,而格式化用 `.2f` → 实际输出 `"1.00%"`。
           即**修好第一个它照样红**。一条判据同时有两个错而长期无人动,
           正是"永远红的判据 = 没人再看的判据"。

        ## 修法:输入不再手写,而是**由真正的产出方生成**

        手写假数据的问题不是"这次写错了",而是它**与真实产出之间没有任何约束** ——
        上游改个键名,判据不会红,而生产会在发第一条告警时崩。
        改成走 `calculate_pair_cost` 之后,这条判据才真的在检验
        「产出方与消费方的键对不对得上」这件唯一值得检验的事。

        ⚠️ **本判据绿 ≠ 监控系统在跑。** 这个子系统(06-tools/monitoring)
        实测最后一次产出是 2026-06-26,已无 cron / systemd timer / 进程。
        它只保证"若有朝一日重启,这条链路的键是对得上的"。
        """
        from pair_cost_scanner import (MIN_LIQUIDITY, PAIR_COST_THRESHOLD,
                                       calculate_pair_cost)
        from telegram_notifier_v2 import format_pair_cost_alert

        # 价格取到刚好跨过系统**自己的**阈值(不写死 0.90:阈值改了这里要跟着动)
        yes_price = PAIR_COST_THRESHOLD / 2 - 0.005
        market = {
            "id": "m-test", "slug": "test-market", "question": "Test Market",
            "outcomePrices": f'["{yes_price}", "{yes_price}"]',
            "liquidity": MIN_LIQUIDITY * 500, "volume": 100000,
            "endDate": "2026-06-30T23:59:59Z",
        }
        opp = calculate_pair_cost(market)
        assert opp["is_opportunity"], "构造的样本没够上系统自己的机会判据,测的就不是告警路径"

        message = format_pair_cost_alert(opp)   # 键对不上会在这里抛 KeyError

        assert "🎯" in message
        assert "套利机会" in message
        # 用产出方自己的数值断言,而不是写死一个数 —— 写死的那个正是上面第 2 个红因
        assert f"{opp['profit_pct']:.2f}%" in message, "利润率没流进消息"
        assert f"https://polymarket.com/event/{opp['slug']}" in message


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
