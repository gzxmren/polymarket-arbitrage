#!/usr/bin/env python3
"""
鲸鱼跟随策略单元测试
"""

import unittest
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent / "06-tools/analysis"))

from whale_following import WhaleFollowingStrategy, Whale, Trade, Signal


class TestWhaleFollowingStrategy(unittest.TestCase):
    """测试鲸鱼跟随策略"""
    
    def setUp(self):
        """测试前准备"""
        self.strategy = WhaleFollowingStrategy()
    
    def test_calculate_confidence(self):
        """测试置信度计算"""
        whale = Whale(
            wallet="0x123",
            pseudonym="TestWhale",
            total_value=100000,
            win_rate=0.7,
            sharpe_ratio=1.5,
            strategy_consistency=0.8
        )
        
        change = {
            'market': 'Test Market',
            'value_change': 50000,
            'direction': 'BUY'
        }
        
        confidence = self.strategy.calculate_confidence(whale, change)
        
        # 验证置信度在合理范围
        self.assertGreaterEqual(confidence, 0)
        self.assertLessEqual(confidence, 1)
        
        # 高胜率鲸鱼应该有较高置信度
        self.assertGreater(confidence, 0.6)
        
        print(f"✅ 置信度计算测试通过: {confidence*100:.1f}%")
    
    def test_calculate_suggested_position(self):
        """测试建议仓位计算"""
        whale = Whale(
            wallet="0x123",
            pseudonym="TestWhale",
            total_value=100000,
            win_rate=0.7,
            sharpe_ratio=1.5,
            strategy_consistency=0.8
        )
        
        change = {
            'market': 'Test Market',
            'value_change': 50000,
            'direction': 'BUY'
        }
        
        position = self.strategy.calculate_suggested_position(whale, change)
        
        # 验证仓位在合理范围
        self.assertGreaterEqual(position, 100)
        self.assertLessEqual(position, 5000)
        
        print(f"✅ 建议仓位计算测试通过: ${position:,.0f}")
    
    def test_generate_reasoning(self):
        """测试推理生成"""
        whale = Whale(
            wallet="0x123",
            pseudonym="TestWhale",
            total_value=100000,
            win_rate=0.7,
            sharpe_ratio=1.5,
            strategy_consistency=0.8
        )
        
        change = {
            'market': 'Will Iran attack Israel?',
            'value_change': 50000,
            'direction': 'BUY'
        }
        
        reasoning = self.strategy.generate_reasoning(whale, change, 0.75)
        
        # 验证推理包含关键信息
        self.assertIn("TestWhale", reasoning)
        self.assertIn("70.0%", reasoning)
        self.assertIn("Iran", reasoning)
        
        print(f"✅ 推理生成测试通过")
    
    def test_signal_format(self):
        """测试信号格式化"""
        from whale_following import format_signal_telegram
        
        signal = Signal(
            type='whale_following',
            whale=Whale(
                wallet="0x123",
                pseudonym="TestWhale",
                total_value=100000,
                win_rate=0.7,
                sharpe_ratio=1.5,
                strategy_consistency=0.8
            ),
            market='Will Iran attack Israel?',
            direction='BUY',
            confidence=0.75,
            suggested_position=2500,
            expected_return=0.1,
            risk_level='中风险',
            reasoning='Test reasoning',
            created_at=datetime.now(timezone.utc)
        )
        
        message = format_signal_telegram(signal)
        
        # 验证消息包含关键信息
        self.assertIn("聪明钱跟随信号", message)
        self.assertIn("TestWhale", message)
        self.assertIn("75.0%", message)
        
        print(f"✅ 信号格式化测试通过")


if __name__ == "__main__":
    print("🧪 运行鲸鱼跟随策略单元测试...\n")
    unittest.main(verbosity=2)
