#!/usr/bin/env python3
"""
Polymarket CLOB API 封装
获取真实订单簿数据
"""

import json
import ssl
from urllib.request import urlopen, Request
from urllib.error import URLError
from typing import Dict, List, Optional

CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# 创建SSL上下文，处理TLS问题
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


def fetch_clob_api(endpoint: str) -> Optional[Dict]:
    """获取CLOB API数据"""
    url = f"{CLOB_API}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "PolymarketTrader/1.0"})
        with urlopen(req, timeout=30, context=ssl_context) as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        # 静默错误，避免spam日志
        return None


def fetch_gamma_api(endpoint: str) -> Optional[Dict]:
    """获取Gamma API数据"""
    url = f"{GAMMA_API}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "PolymarketTrader/1.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        print(f"Error fetching Gamma {url}: {e}", file=__import__('sys').stderr)
        return None


def get_order_book(token_id: str) -> Optional[Dict]:
    """
    获取指定token的完整订单簿
    
    Args:
        token_id: CLOB token ID
        
    Returns:
        {
            "bids": [[price, size], ...],
            "asks": [[price, size], ...],
            "market": condition_id
        }
    """
    # 获取买单簿
    buy_data = fetch_clob_api(f"/book?token_id={token_id}&side=buy")
    if not buy_data:
        return None
    
    # 获取卖单簿
    sell_data = fetch_clob_api(f"/book?token_id={token_id}&side=sell")
    if not sell_data:
        return None
    
    # 转换格式
    # 注意：API返回的是完整订单簿，包含bids和asks
    bids = [[float(b["price"]), float(b["size"])] for b in buy_data.get("bids", [])]
    asks = [[float(a["price"]), float(a["size"])] for a in buy_data.get("asks", [])]
    
    return {
        "market": buy_data.get("market", ""),
        "token_id": token_id,
        "bids": bids,
        "asks": asks,
        "timestamp": buy_data.get("timestamp", 0)
    }


def get_markets_with_order_book(limit: int = 50) -> List[Dict]:
    """
    获取启用了订单簿的活跃市场
    
    Returns:
        市场列表，包含clobTokenIds
    """
    data = fetch_gamma_api(f"/markets?active=true&closed=false&limit={limit}&enableOrderBook=true")
    if not data:
        return []
    
    markets = []
    for m in data:
        if m.get("enableOrderBook"):
            token_ids = m.get("clobTokenIds", "[]")
            if isinstance(token_ids, str):
                try:
                    token_ids = json.loads(token_ids)
                except:
                    token_ids = []
            
            if len(token_ids) >= 2:
                markets.append({
                    "id": m.get("id"),
                    "slug": m.get("slug", ""),
                    "question": m.get("question", ""),
                    "yes_token": token_ids[0],
                    "no_token": token_ids[1],
                    "liquidity": float(m.get("liquidityNum", 0)),
                    "volume": float(m.get("volumeNum", 0)),
                    "outcomePrices": m.get("outcomePrices", "[0.5, 0.5]")
                })
    
    # 按流动性排序
    markets.sort(key=lambda x: x["liquidity"], reverse=True)
    return markets


def calculate_spread_from_order_book(order_book: Dict) -> Dict:
    """
    从订单簿计算价差
    
    Returns:
        {
            "best_bid": float,
            "best_ask": float,
            "spread": float,
            "spread_pct": float,
            "bid_depth": float,
            "ask_depth": float
        }
    """
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    
    if not bids or not asks:
        return {}
    
    best_bid = bids[0][0] if bids else 0
    best_ask = asks[0][0] if asks else 1
    
    spread = best_ask - best_bid
    mid_price = (best_bid + best_ask) / 2
    spread_pct = spread / mid_price if mid_price > 0 else 0
    
    bid_depth = sum(b[1] for b in bids[:5])
    ask_depth = sum(a[1] for a in asks[:5])
    
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "min_depth": min(bid_depth, ask_depth)
    }


if __name__ == "__main__":
    # 测试
    print("获取启用了订单簿的市场...")
    markets = get_markets_with_order_book(limit=5)
    
    for m in markets[:3]:
        print(f"\n{m['question'][:60]}...")
        print(f"  YES Token: {m['yes_token'][:20]}...")
        
        # 获取订单簿
        ob = get_order_book(m['yes_token'])
        if ob:
            spread_info = calculate_spread_from_order_book(ob)
            print(f"  最优买价: {spread_info.get('best_bid', 0):.4f}")
            print(f"  最优卖价: {spread_info.get('best_ask', 0):.4f}")
            print(f"  价差: {spread_info.get('spread_pct', 0):.2%}")
            print(f"  深度: {spread_info.get('min_depth', 0):.0f}")
