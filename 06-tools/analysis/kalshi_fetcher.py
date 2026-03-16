#!/usr/bin/env python3
"""
Kalshi 数据获取器
接入 Kalshi 预测市场数据
"""

import json
import os
import urllib.request
from urllib.error import URLError
from datetime import datetime

KALSHI_API_BASE = "https://trading-api.kalshi.com/trade-api/v2"

# API Key 配置（需要从 Kalshi 获取）
KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_API_SECRET = os.getenv("KALSHI_API_SECRET", "")


def fetch_kalshi_markets(limit=100, status="open"):
    """
    获取 Kalshi 活跃市场
    
    Args:
        limit: 返回市场数量
        status: 市场状态 (open, closed, all)
    
    Returns:
        list: 市场列表
    """
    try:
        url = f"{KALSHI_API_BASE}/markets?limit={limit}&status={status}"
        
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            markets = data.get("markets", [])
            
            # 格式化数据
            formatted = []
            for m in markets:
                formatted.append({
                    "id": m.get("ticker", ""),
                    "title": m.get("title", ""),
                    "description": m.get("description", ""),
                    "category": m.get("category", ""),
                    "yes_price": m.get("yes_ask", 0) / 100 if m.get("yes_ask") else 0,
                    "no_price": m.get("no_ask", 0) / 100 if m.get("no_ask") else 0,
                    "volume": m.get("volume", 0),
                    "close_time": m.get("close_time", ""),
                    "status": m.get("status", ""),
                    "source": "kalshi"
                })
            
            return formatted
            
    except URLError as e:
        print(f"Error fetching Kalshi markets: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error: {e}")
        return []


def test_kalshi_connection():
    """测试 Kalshi API 连接"""
    print("Testing Kalshi API connection...")
    markets = fetch_kalshi_markets(limit=10)
    
    if markets:
        print(f"✅ Success! Fetched {len(markets)} markets from Kalshi")
        print(f"\nSample market:")
        m = markets[0]
        print(f"  Title: {m['title'][:60]}...")
        print(f"  Yes: ${m['yes_price']:.2f}, No: ${m['no_price']:.2f}")
        print(f"  Volume: ${m['volume']:,.0f}")
        return True
    else:
        print("❌ Failed to fetch markets")
        return False


if __name__ == "__main__":
    test_kalshi_connection()