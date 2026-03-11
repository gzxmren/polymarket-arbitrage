#!/usr/bin/env python3
"""
pytest 配置文件
"""

import pytest
import sys
from pathlib import Path

# 添加项目路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "analysis"))
sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "monitoring"))


# 测试 fixtures
@pytest.fixture
def sample_market_data():
    """示例市场数据"""
    return {
        "id": "12345",
        "question": "Will Bitcoin reach $100k by 2026?",
        "slug": "will-bitcoin-reach-100k-by-2026",
        "outcomePrices": ["0.52", "0.47"],
        "liquidity": 150000,
        "volume": 500000,
        "endDate": "2026-12-31T23:59:59Z"
    }


@pytest.fixture
def sample_pair_cost_opportunity():
    """示例 Pair Cost 套利机会"""
    return {
        "market_id": "12345",
        "slug": "test-market",
        "question": "Test Market",
        "yes_price": 0.52,
        "no_price": 0.47,
        "pair_cost": 0.99,
        "profit_margin": 0.01,
        "profit_pct": 1.0,
        "liquidity": 150000,
        "volume": 500000,
        "end_date": "2026-12-31T23:59:59Z",
        "is_opportunity": True
    }


@pytest.fixture
def sample_whale_data():
    """示例鲸鱼数据"""
    return {
        "wallet": "0x1234567890abcdef1234567890abcdef12345678",
        "pseudonym": "TestWhale",
        "total_volume": 100000,
        "trade_count": 50,
        "large_trades": 10,
        "markets_traded": 5,
        "last_trade": 1700000000
    }


@pytest.fixture
def mock_telegram_response():
    """Mock Telegram API 响应"""
    return {
        "ok": True,
        "result": {
            "message_id": 123,
            "chat": {"id": 1530224854},
            "text": "Test message"
        }
    }


# 配置 pytest
def pytest_configure(config):
    """pytest 配置"""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "requires_api: marks tests that require external API"
    )
