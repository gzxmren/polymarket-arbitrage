#!/usr/bin/env python3
"""快速测试价差扫描"""

import sys
import json
import ssl
from urllib.request import urlopen, Request

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

def fetch(url):
    req = Request(url, headers={"User-Agent": "PolymarketTrader/1.0"})
    with urlopen(req, timeout=30, context=ssl_context) as resp:
        return json.loads(resp.read().decode())

# 获取市场
markets = fetch("https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=20")

print("🔍 扫描市场价差")
print("=" * 70)

found = 0
for m in markets[:10]:
    slug = m.get('slug', '')
    question = m.get('question', '')[:40]
    
    token_ids = m.get('clobTokenIds', '[]')
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except:
            continue
    
    if not token_ids:
        continue
    
    try:
        # 获取订单簿
        data = fetch(f"https://clob.polymarket.com/book?token_id={token_ids[0]}&side=buy")
        bids = data.get('bids', [])
        asks = data.get('asks', [])
        
        if not bids or not asks:
            continue
        
        best_bid = float(bids[0]['price'])
        best_ask = float(asks[0]['price'])
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2
        spread_pct = spread / mid if mid > 0 else 0
        
        # 计算深度
        bid_depth = sum(float(b['price']) * float(b['size']) for b in bids[:5])
        ask_depth = sum(float(a['price']) * float(a['size']) for a in asks[:5])
        min_depth = min(bid_depth, ask_depth)
        
        status = "✅" if spread_pct >= 0.015 and min_depth >= 5000 else "⚪"
        print(f"{status} {question}...")
        print(f"   价差: {spread_pct:>6.2%} | 深度: ${min_depth:>10,.0f}")
        
        if spread_pct >= 0.015 and min_depth >= 5000:
            found += 1
            
    except Exception as e:
        pass

print(f"\n{'=' * 70}")
print(f"发现 {found} 个 >1.5% 且深度>$5000 的机会")
