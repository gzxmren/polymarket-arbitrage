#!/usr/bin/env python3
"""
Polymarket CLOB API 封装 (V2 兼容)

获取真实订单簿数据。

更新历史:
- 2026-04-30: 升级为 CLOB V2 API，单次请求获取完整订单簿（bids+asks），
              新增 min_order_size / tick_size / neg_risk / last_trade_price 字段。
              参考: https://docs.polymarket.com/
"""

import json
import ssl
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from http.client import IncompleteRead
from typing import Dict, List, Optional

CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# 创建 SSL 上下文（Polymarket CLOB 在部分环境需要跳过验证）
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


def fetch_clob_api(endpoint: str) -> Optional[Dict]:
    """获取 CLOB API 数据（使用自定义 SSL 上下文）"""
    url = f"{CLOB_API}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "PolymarketTrader/2.0"})
        with urlopen(req, timeout=30, context=ssl_context) as resp:
            return json.loads(resp.read().decode())
    except (URLError, TimeoutError, OSError, IncompleteRead, json.JSONDecodeError) as e:
        print(f"[clob_api] CLOB request failed: {url} — {e}", file=sys.stderr)
        return None


def fetch_gamma_api(endpoint: str) -> Optional[Dict]:
    """获取 Gamma API 数据"""
    url = f"{GAMMA_API}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "PolymarketTrader/2.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (URLError, TimeoutError, OSError, IncompleteRead, json.JSONDecodeError) as e:
        print(f"[clob_api] Gamma request failed: {url} — {e}", file=sys.stderr)
        return None


def get_order_book(token_id: str) -> Optional[Dict]:
    """
    获取指定 token 的完整订单簿 (CLOB V2: 单次请求)

    V2 变更:
    - 不再需要分 side=buy / side=sell 两次请求
    - 新增字段: min_order_size, tick_size, neg_risk, last_trade_price

    Args:
        token_id: CLOB token ID

    Returns:
        {
            "bids": [[price, size], ...],
            "asks": [[price, size], ...],
            "market": condition_id,
            "token_id": str,
            "timestamp": int,
            "min_order_size": float | None,
            "tick_size": float | None,
            "neg_risk": bool | None,
            "last_trade_price": float | None,
        }
    """
    # CLOB V2: 单次请求返回完整 bids + asks
    data = fetch_clob_api(f"/book?token_id={token_id}")
    if not data:
        return None

    bids = [[float(b["price"]), float(b["size"])] for b in data.get("bids", [])]
    asks = [[float(a["price"]), float(a["size"])] for a in data.get("asks", [])]

    # 解析 V2 新增字段（向后兼容，缺失或空字符串时为 None）
    def _safe_float(val) -> Optional[float]:
        """API 可能返回空字符串 '' 或 None，统一处理"""
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    return {
        "market": data.get("market", ""),
        "token_id": token_id,
        "bids": bids,
        "asks": asks,
        "timestamp": data.get("timestamp", 0),
        # V2 新增
        "min_order_size": _safe_float(data.get("min_order_size")),
        "tick_size": _safe_float(data.get("tick_size")),
        "neg_risk": data.get("neg_risk"),
        "last_trade_price": _safe_float(data.get("last_trade_price")),
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
                except (json.JSONDecodeError, ValueError):
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
    从订单簿计算价差与深度指标

    Returns:
        {
            "best_bid": float,
            "best_ask": float,
            "mid_price": float,
            "spread": float,
            "spread_pct": float,
            "bid_depth": float,
            "ask_depth": float,
            "min_depth": float,
            "last_trade_price": float | None,   # V2 新增
        }
    """
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])

    if not bids or not asks:
        return {}

    best_bid = bids[0][0]
    best_ask = asks[0][0]

    spread = best_ask - best_bid
    mid_price = (best_bid + best_ask) / 2
    spread_pct = spread / mid_price if mid_price > 0 else 0

    bid_depth = sum(b[1] for b in bids[:5])
    ask_depth = sum(a[1] for a in asks[:5])

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid_price,
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "min_depth": min(bid_depth, ask_depth),
        "last_trade_price": order_book.get("last_trade_price"),
    }


if __name__ == "__main__":
    # 自测：验证 V2 API 兼容性
    print("=== CLOB V2 兼容性测试 ===")

    # 1. 检查 API 版本
    version_data = fetch_clob_api("/version")
    print(f"API 版本: {version_data}")

    # 2. 获取市场列表
    print("\n获取启用了订单簿的市场...")
    markets = get_markets_with_order_book(limit=5)
    print(f"找到 {len(markets)} 个市场")

    for m in markets[:3]:
        print(f"\n{m['question'][:60]}...")
        print(f"  YES Token: {m['yes_token'][:20]}...")

        ob = get_order_book(m["yes_token"])
        if ob:
            spread_info = calculate_spread_from_order_book(ob)
            print(f"  最优买价: {spread_info.get('best_bid', 0):.4f}")
            print(f"  最优卖价: {spread_info.get('best_ask', 0):.4f}")
            print(f"  价差: {spread_info.get('spread_pct', 0):.2%}")
            print(f"  深度: {spread_info.get('min_depth', 0):.0f}")
            # V2 新增字段
            print(f"  最近成交价: {ob.get('last_trade_price')}")
            print(f"  最小订单: {ob.get('min_order_size')}")
            print(f"  Tick Size: {ob.get('tick_size')}")
            print(f"  Neg Risk: {ob.get('neg_risk')}")
        else:
            print("  ⚠️ 无法获取订单簿")
