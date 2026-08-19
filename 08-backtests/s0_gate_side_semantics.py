#!/usr/bin/env python3
"""S0 前置门 · 第二版。第一版没过门(SELL 仅 1 笔 < 50),且暴露三个设计缺陷:

1. 10 个市场轮一圈 36 秒 ⇒ 每市场盘口快照约 1 分钟一次 ⇒ 弃用率 70%。
   改:市场减到 5 个、轮询加密。
2. 只取 token0 的成交。而想赌"否"的人多半是**买入 token1**,不是卖出 token0
   ⇒ 天然少收 SELL。改:两个 token 都抓,各自对各自的盘口。
3. **没有对账**:不知道 SELL 是本来就少,还是被匹配过程筛掉了。
   改:全部成交的 side 分布 vs 匹配上的 side 分布,一并报出(CLAUDE.md 静默失败清单 #1)。

⚠️ 上一版还踩了一个执行链的坑:`python x.py | tail` 会让管道退出码取 tail 的,
   把 sys.exit(1) 吃掉。本版不许再用管道跑。
"""
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

_o = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _o(h, p, socket.AF_INET, t, pr, fl)
UA = {"User-Agent": "polymarket-rebirth-s0/2.0"}

MIN_PER_CLASS = 50
MIN_AGREE = 0.90
N_MARKETS = 5
DURATION_S = 900

net = defaultdict(int)


def get(u, tries=3, timeout=12):
    for i in range(tries):
        net["attempt"] += 1
        try:
            return json.loads(urllib.request.urlopen(
                urllib.request.Request(u, headers=UA), timeout=timeout).read())
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError):
            net["retry"] += 1
            if i == tries - 1:
                net["giveup"] += 1
                return None
            time.sleep(0.8 * (i + 1))
    return None


mk = get("https://gamma-api.polymarket.com/markets?limit=60&closed=false"
         "&order=volume24hr&ascending=false") or []
targets = []
for m in mk:
    try:
        toks = [str(x) for x in json.loads(m["clobTokenIds"])]
    except (KeyError, json.JSONDecodeError, TypeError):
        continue
    if len(toks) != 2 or not m.get("conditionId"):
        continue
    targets.append({"cid": m["conditionId"], "toks": toks,
                    "slug": (m.get("slug") or "")[:36]})
    if len(targets) >= N_MARKETS:
        break
print(f"选中 {len(targets)} 个市场,两个 token 都抓;跑 {DURATION_S}s", flush=True)

books = defaultdict(list)      # token_id -> [(ts, bid, ask)]
seen = set()
all_trades = []                # 全部见到的成交(用于对账)

t0 = time.time()
rounds = 0
while time.time() - t0 < DURATION_S:
    rounds += 1
    for t in targets:
        for tok in t["toks"]:
            ob = get(f"https://clob.polymarket.com/book?token_id={tok}", tries=2, timeout=10)
            now = time.time()
            if ob:
                bids = [float(x["price"]) for x in (ob.get("bids") or [])]
                asks = [float(x["price"]) for x in (ob.get("asks") or [])]
                if bids and asks:
                    bb, ba = max(bids), min(asks)
                    if ba >= bb:
                        books[tok].append((now, bb, ba))
        qs = urllib.parse.urlencode({"market": t["cid"], "limit": 100})
        tr = get(f"https://data-api.polymarket.com/trades?{qs}", tries=2, timeout=10)
        for x in (tr or []):
            key = (x.get("transactionHash"), x.get("asset"), x.get("side"),
                   x.get("size"), x.get("price"), x.get("timestamp"))
            if key in seen:
                continue
            seen.add(key)
            all_trades.append(x)
    if rounds % 5 == 0:
        print(f"  轮 {rounds:>3} | {int(time.time()-t0):>3}s | 盘口 "
              f"{sum(len(v) for v in books.values()):>5} | 成交 {len(all_trades):>5}", flush=True)

print(f"\n[网络] 尝试 {net['attempt']} 重试 {net['retry']} 放弃 {net['giveup']}")

# ---- 对账:全部成交 vs 匹配上的成交,side 分布是否一致 ----
all_side = defaultdict(int)
for x in all_trades:
    all_side[str(x.get("side", "")).upper()] += 1

tab = defaultdict(int)
drop = defaultdict(int)
matched_side = defaultdict(int)
for x in all_trades:
    side = str(x.get("side", "")).upper()
    tok = str(x.get("asset"))
    try:
        ts, px = float(x["timestamp"]), float(x["price"])
    except (TypeError, ValueError, KeyError):
        drop["字段不可解析"] += 1
        continue
    bl = sorted(books.get(tok, []))
    before = [b for b in bl if b[0] <= ts]
    after = [b for b in bl if b[0] >= ts]
    if not before or not after:
        drop["无夹住快照"] += 1
        continue
    b0, b1 = before[-1], after[0]
    if (b0[1], b0[2]) != (b1[1], b1[2]):
        drop["盘口变动"] += 1
        continue
    bb, ba = b0[1], b0[2]
    if abs(px - ba) < 1e-9:
        tab[(side, "at_ask")] += 1
        matched_side[side] += 1
    elif abs(px - bb) < 1e-9:
        tab[(side, "at_bid")] += 1
        matched_side[side] += 1
    else:
        drop["价格既非买一也非卖一"] += 1

tot_all = sum(all_side.values())
tot_m = sum(matched_side.values())
print(f"\n[对账] 见到成交 {tot_all} 笔 → 匹配上 {tot_m} 笔")
for k, v in sorted(drop.items(), key=lambda x: -x[1]):
    print(f"    弃用 {k}: {v}")
print(f"  {'side':<8}{'全部占比':>12}{'匹配后占比':>12}{'差(pp)':>10}")
bias = []
for s in sorted(set(all_side) | set(matched_side)):
    pa = all_side[s] / tot_all * 100 if tot_all else 0
    pm = matched_side[s] / tot_m * 100 if tot_m else 0
    print(f"  {s:<8}{pa:>11.1f}%{pm:>11.1f}%{pm-pa:>10.1f}")
    bias.append(abs(pm - pa))
if bias and max(bias) > 10:
    print("  🔴 匹配前后 side 分布差 >10pp ⇒ 匹配过程在偏心地筛样本,结论不可用")

print("\n交叉表(行=side,列=成交价落在哪一侧):")
print(f"  {'side':<8}{'落在卖一(ask)':>16}{'落在买一(bid)':>16}{'合计':>8}")
res = {}
for side in ("BUY", "SELL"):
    a, b = tab[(side, "at_ask")], tab[(side, "at_bid")]
    res[side] = (a, b, a + b)
    print(f"  {side:<8}{a:>16}{b:>16}{a+b:>8}")

buy_a, buy_b, buy_n = res["BUY"]
sell_a, sell_b, sell_n = res["SELL"]
buy_rate = buy_a / buy_n if buy_n else 0.0
sell_rate = sell_b / sell_n if sell_n else 0.0
print(f"\n假设 H:side = 吃单方方向")
print(f"  BUY  落在卖一: {buy_rate*100:6.2f}%  (n={buy_n})")
print(f"  SELL 落在买一: {sell_rate*100:6.2f}%  (n={sell_n})")

fails = []
if buy_n < MIN_PER_CLASS:
    fails.append(f"BUY 样本 {buy_n} < {MIN_PER_CLASS}")
if sell_n < MIN_PER_CLASS:
    fails.append(f"SELL 样本 {sell_n} < {MIN_PER_CLASS}")
if buy_n and buy_rate < MIN_AGREE:
    fails.append(f"BUY 一致率 {buy_rate*100:.1f}% < {MIN_AGREE*100:.0f}%")
if sell_n and sell_rate < MIN_AGREE:
    fails.append(f"SELL 一致率 {sell_rate*100:.1f}% < {MIN_AGREE*100:.0f}%")

if fails:
    print("\n🔴 S0 未过门:")
    for f in fails:
        print(f"   - {f}")
    sys.exit(1)
print(f"\n✅ S0 过门:side = 吃单方方向")
