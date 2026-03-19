"""
Polymarket CLOB API Service
封装 CLOB API 调用，获取真实订单簿数据，计算实时价格和价差
"""

import json
import ssl
from urllib.request import urlopen, Request
from urllib.error import URLError
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass

# CLOB API 配置
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# 创建SSL上下文，处理TLS问题
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


@dataclass
class OrderBookData:
    """订单簿数据结构"""
    market: str
    token_id: str
    bids: List[List[float]]  # [[price, size], ...]
    asks: List[List[float]]  # [[price, size], ...]
    timestamp: int
    fetched_at: str


@dataclass
class PriceInfo:
    """价格信息结构"""
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    spread_pct: float
    bid_depth: float
    ask_depth: float
    min_depth: float


@dataclass
class MarketInfo:
    """市场信息结构"""
    id: str
    slug: str
    question: str
    yes_token: str
    no_token: str
    liquidity: float
    volume: float
    outcome_prices: List[float]


class CLOBService:
    """CLOB API 服务类"""
    
    def __init__(self):
        self.ssl_context = ssl_context
        self.last_error: Optional[str] = None
    
    def _fetch_clob_api(self, endpoint: str) -> Optional[Dict]:
        """获取CLOB API数据"""
        url = f"{CLOB_API}{endpoint}"
        try:
            req = Request(url, headers={"User-Agent": "PolymarketDashboard/1.0"})
            with urlopen(req, timeout=30, context=self.ssl_context) as resp:
                return json.loads(resp.read().decode())
        except (URLError, json.JSONDecodeError) as e:
            self.last_error = str(e)
            return None
    
    def _fetch_gamma_api(self, endpoint: str) -> Optional[Dict]:
        """获取Gamma API数据"""
        url = f"{GAMMA_API}{endpoint}"
        try:
            req = Request(url, headers={"User-Agent": "PolymarketDashboard/1.0"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except (URLError, json.JSONDecodeError) as e:
            self.last_error = str(e)
            return None
    
    def get_order_book(self, token_id: str) -> Optional[OrderBookData]:
        """
        获取指定token的完整订单簿
        
        Args:
            token_id: CLOB token ID
            
        Returns:
            OrderBookData 对象
        """
        # 获取买单簿
        buy_data = self._fetch_clob_api(f"/book?token_id={token_id}&side=buy")
        if not buy_data:
            return None
        
        # 获取卖单簿
        sell_data = self._fetch_clob_api(f"/book?token_id={token_id}&side=sell")
        if not sell_data:
            return None
        
        # 转换格式
        bids = [[float(b["price"]), float(b["size"])] for b in buy_data.get("bids", [])]
        asks = [[float(a["price"]), float(a["size"])] for a in sell_data.get("asks", [])]
        
        return OrderBookData(
            market=buy_data.get("market", ""),
            token_id=token_id,
            bids=bids,
            asks=asks,
            timestamp=buy_data.get("timestamp", 0),
            fetched_at=datetime.now(timezone.utc).isoformat()
        )
    
    def calculate_price_info(self, order_book: OrderBookData) -> Optional[PriceInfo]:
        """
        从订单簿计算价格信息
        
        Args:
            order_book: 订单簿数据
            
        Returns:
            PriceInfo 对象
        """
        bids = order_book.bids
        asks = order_book.asks
        
        if not bids or not asks:
            return None
        
        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 1
        
        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2
        spread_pct = spread / mid_price if mid_price > 0 else 0
        
        # 计算前5档深度
        bid_depth = sum(b[1] for b in bids[:5])
        ask_depth = sum(a[1] for a in asks[:5])
        
        return PriceInfo(
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            spread=spread,
            spread_pct=spread_pct,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            min_depth=min(bid_depth, ask_depth)
        )
    
    def get_market_prices(self, token_id: str) -> Optional[Dict]:
        """
        获取市场实时价格（简化接口）
        
        Args:
            token_id: CLOB token ID
            
        Returns:
            价格信息字典
        """
        order_book = self.get_order_book(token_id)
        if not order_book:
            return None
        
        price_info = self.calculate_price_info(order_book)
        if not price_info:
            return None
        
        return {
            "token_id": token_id,
            "best_bid": price_info.best_bid,
            "best_ask": price_info.best_ask,
            "mid_price": price_info.mid_price,
            "spread": price_info.spread,
            "spread_pct": price_info.spread_pct,
            "bid_depth": price_info.bid_depth,
            "ask_depth": price_info.ask_depth,
            "min_depth": price_info.min_depth,
            "fetched_at": order_book.fetched_at
        }
    
    def get_markets_with_order_book(self, limit: int = 50) -> List[MarketInfo]:
        """
        获取启用了订单簿的活跃市场
        
        Args:
            limit: 返回市场数量
            
        Returns:
            MarketInfo 列表
        """
        data = self._fetch_gamma_api(
            f"/markets?active=true&closed=false&limit={limit}&enableOrderBook=true"
        )
        if not data:
            return []
        
        markets = []
        for m in data:
            if not m.get("enableOrderBook"):
                continue
            
            token_ids = m.get("clobTokenIds", "[]")
            if isinstance(token_ids, str):
                try:
                    token_ids = json.loads(token_ids)
                except:
                    token_ids = []
            
            if len(token_ids) < 2:
                continue
            
            # 解析价格
            prices = m.get("outcomePrices", "[]")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except:
                    prices = [0.5, 0.5]
            
            markets.append(MarketInfo(
                id=m.get("id"),
                slug=m.get("slug", ""),
                question=m.get("question", ""),
                yes_token=token_ids[0],
                no_token=token_ids[1],
                liquidity=float(m.get("liquidityNum", 0)),
                volume=float(m.get("volumeNum", 0)),
                outcome_prices=prices
            ))
        
        # 按流动性排序
        markets.sort(key=lambda x: x.liquidity, reverse=True)
        return markets
    
    def get_pair_cost_prices(self, market_id: str) -> Optional[Dict]:
        """
        获取Pair Cost套利所需的YES/NO价格
        
        Args:
            market_id: Gamma市场ID
            
        Returns:
            包含yes_price和no_price的字典
        """
        # 先获取市场信息
        data = self._fetch_gamma_api(f"/markets/{market_id}")
        if not data:
            return None
        
        token_ids = data.get("clobTokenIds", "[]")
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except:
                return None
        
        if len(token_ids) < 2:
            return None
        
        yes_token = token_ids[0]
        no_token = token_ids[1]
        
        # 获取YES和NO的订单簿
        yes_book = self.get_order_book(yes_token)
        no_book = self.get_order_book(no_token)
        
        if not yes_book or not no_book:
            return None
        
        yes_price_info = self.calculate_price_info(yes_book)
        no_price_info = self.calculate_price_info(no_book)
        
        if not yes_price_info or not no_price_info:
            return None
        
        # 使用mid_price作为市场价格
        yes_price = yes_price_info.mid_price
        no_price = no_price_info.mid_price
        pair_cost = yes_price + no_price
        profit_potential = 1.0 - pair_cost if pair_cost < 1.0 else 0
        
        return {
            "market_id": market_id,
            "market_name": data.get("question", ""),
            "slug": data.get("slug", ""),
            "yes_token": yes_token,
            "no_token": no_token,
            "yes_price": yes_price,
            "no_price": no_price,
            "pair_cost": pair_cost,
            "profit_potential": profit_potential,
            "min_depth": min(yes_price_info.min_depth, no_price_info.min_depth),
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
    
    def get_market_order_book(self, market_id: str) -> Optional[Dict]:
        """
        获取市场的完整订单簿（YES和NO）
        
        Args:
            market_id: Gamma市场ID
            
        Returns:
            完整订单簿数据
        """
        # 先获取市场信息
        data = self._fetch_gamma_api(f"/markets/{market_id}")
        if not data:
            return None
        
        token_ids = data.get("clobTokenIds", "[]")
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except:
                return None
        
        if len(token_ids) < 2:
            return None
        
        yes_token = token_ids[0]
        no_token = token_ids[1]
        
        # 获取订单簿
        yes_book = self.get_order_book(yes_token)
        no_book = self.get_order_book(no_token)
        
        if not yes_book or not no_book:
            return None
        
        yes_prices = self.calculate_price_info(yes_book)
        no_prices = self.calculate_price_info(no_book)
        
        return {
            "market_id": market_id,
            "market_name": data.get("question", ""),
            "slug": data.get("slug", ""),
            "yes": {
                "token_id": yes_token,
                "order_book": {
                    "bids": yes_book.bids,
                    "asks": yes_book.asks
                },
                "prices": {
                    "best_bid": yes_prices.best_bid if yes_prices else None,
                    "best_ask": yes_prices.best_ask if yes_prices else None,
                    "mid_price": yes_prices.mid_price if yes_prices else None
                }
            },
            "no": {
                "token_id": no_token,
                "order_book": {
                    "bids": no_book.bids,
                    "asks": no_book.asks
                },
                "prices": {
                    "best_bid": no_prices.best_bid if no_prices else None,
                    "best_ask": no_prices.best_ask if no_prices else None,
                    "mid_price": no_prices.mid_price if no_prices else None
                }
            },
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }


# 全局CLOB服务实例
clob_service = CLOBService()


def get_clob_service() -> CLOBService:
    """获取CLOB服务实例"""
    return clob_service
