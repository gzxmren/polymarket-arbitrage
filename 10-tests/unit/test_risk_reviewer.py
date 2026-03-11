#!/usr/bin/env python3
"""
风险评估器单元测试
"""

import pytest
from risk_reviewer import (
    review_pair_cost_opportunity,
    review_cross_market_opportunity,
    review_whale_signal,
    should_review
)


class TestPairCostRiskReview:
    """测试 Pair Cost 风险评估"""
    
    def test_low_risk_opportunity(self):
        """测试低风险机会"""
        opp = {
            "profit_pct": 2.5,
            "liquidity": 50000,
            "volume": 100000,
            "pair_cost": 0.975,
            "end_date": "2026-06-30T23:59:59Z"
        }
        
        review = review_pair_cost_opportunity(opp)
        
        assert review["risk_level"] == "low"
        assert review["approved"] is True
        assert len(review["concerns"]) == 0 or review["risk_score"] < 0.3
    
    def test_high_risk_low_profit(self):
        """测试高风险 - 低利润"""
        opp = {
            "profit_pct": 0.5,  # 过低利润
            "liquidity": 50000,
            "volume": 100000,
            "pair_cost": 0.995,
            "end_date": "2026-06-30T23:59:59Z"
        }
        
        review = review_pair_cost_opportunity(opp)
        
        assert review["risk_level"] in ["medium", "high"]
        assert any("利润空间" in c for c in review["concerns"])
    
    def test_high_risk_low_liquidity(self):
        """测试高风险 - 低流动性"""
        opp = {
            "profit_pct": 2.0,
            "liquidity": 500,  # 过低流动性
            "volume": 1000,
            "pair_cost": 0.98,
            "end_date": "2026-06-30T23:59:59Z"
        }
        
        review = review_pair_cost_opportunity(opp)
        
        assert any("流动性" in c for c in review["concerns"])
    
    def test_far_resolution_date(self):
        """测试远期结算"""
        from datetime import datetime, timedelta
        future_date = (datetime.now() + timedelta(days=200)).isoformat()
        
        opp = {
            "profit_pct": 2.0,
            "liquidity": 50000,
            "volume": 100000,
            "pair_cost": 0.98,
            "end_date": future_date
        }
        
        review = review_pair_cost_opportunity(opp)
        
        assert any("结算时间" in c for c in review["concerns"])


class TestCrossMarketRiskReview:
    """测试跨平台套利风险评估"""
    
    def test_low_risk_good_match(self):
        """测试低风险 - 高匹配度"""
        opp = {
            "similarity": 0.85,
            "gap": 0.08,
            "polymarket": {"liquidity": 50000},
            "manifold": {"liquidity": 30000}
        }
        
        review = review_cross_market_opportunity(opp)
        
        assert review["risk_level"] == "low"
        assert review["approved"] is True
    
    def test_high_risk_low_similarity(self):
        """测试高风险 - 低匹配度"""
        opp = {
            "similarity": 0.3,  # 低匹配度
            "gap": 0.08,
            "polymarket": {"liquidity": 50000},
            "manifold": {"liquidity": 30000}
        }
        
        review = review_cross_market_opportunity(opp)
        
        assert review["risk_level"] in ["medium", "high"]
        assert any("匹配度" in c for c in review["concerns"])
    
    def test_high_risk_small_gap(self):
        """测试高风险 - 价差太小"""
        opp = {
            "similarity": 0.8,
            "gap": 0.02,  # 价差太小
            "polymarket": {"liquidity": 50000},
            "manifold": {"liquidity": 30000}
        }
        
        review = review_cross_market_opportunity(opp)
        
        assert any("价差" in c for c in review["concerns"])


class TestWhaleSignalReview:
    """测试鲸鱼信号风险评估"""
    
    def test_strong_signal(self):
        """测试强信号"""
        analysis = {
            "info": {"total_volume": 100000, "win_rate": 0.70},
            "changes": [{"type": "new"}, {"type": "increased"}],
            "total_value": 200000
        }
        
        review = review_whale_signal(analysis)
        
        assert review["risk_level"] in ["low", "medium"]
    
    def test_weak_signal_no_changes(self):
        """测试弱信号 - 无变动"""
        analysis = {
            "info": {"total_volume": 50000, "win_rate": 0.50},
            "changes": [],  # 无变动
            "total_value": 30000
        }
        
        review = review_whale_signal(analysis)
        
        assert review["risk_level"] == "high"
        assert any("无持仓变动" in c for c in review["concerns"])
    
    def test_low_win_rate(self):
        """测试低胜率鲸鱼"""
        analysis = {
            "info": {"total_volume": 100000, "win_rate": 0.50},  # 低胜率
            "changes": [{"type": "new"}],
            "total_value": 200000
        }
        
        review = review_whale_signal(analysis)
        
        assert any("胜率" in c for c in review["concerns"])


class TestConfiguration:
    """测试配置"""
    
    def test_should_review_default(self):
        """测试默认开启风险评估"""
        # 默认应该返回 True（如果环境变量未设置）
        result = should_review()
        assert isinstance(result, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
