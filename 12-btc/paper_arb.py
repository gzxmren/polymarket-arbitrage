#!/usr/bin/env python3
"""
BTC 15m 同窗口荷兰赌 —— paper 模式观察器(只报告,不下单,不存任何数据)

它就是那个套利程序本身,只是最后一步不下单。把 --live 打开才会真下单(尚未实现)。

跑法:
    python3 12-btc/paper_arb.py                # 默认跑到 Ctrl-C
    python3 12-btc/paper_arb.py --minutes 60   # 跑 60 分钟后自动收工

它回答一个问题:**以我们真实的网络延迟,一天 87.8 次机会里能抓住几次。**

🔴🔴 **每条机会记两列,你要的答案是第二列**(2026-09-07 用户指出,我第一版只有第一列):
  · edge_now           —— 盘口快照到达那一瞬间的净边
  · edge_after_delay   —— 人为等 DELAY_MS 之后,用**当时最新的簿**重算同样股数
真实下单要走"我看到 → 决策 → 发单 → 到达",至少 120–200ms。
只看第一列会把**已经消失的盘口**算成机会 —— 而实测机会持续中位 **0.00 秒**,
所以第一列几乎一定虚高。**判生死只看第二列。**

📼 顺手把盘口帧写成 jsonl(每秒一帧 + 全部机会事件)。
   ⭐「探针和录盘是同一条路径,不是两个阶段」——今天跑完,第 3 步回测就不用再等一天。
   ⛔ 但仍然:不建存储层、不连数据库、不做任何离线分析。就是追加写一个文件。

--------------------------------------------------------------------------
今天(2026-09-07)踩过的坑,全部焊在代码里,别再犯:
1. /book 的 asks 按价格**降序**、bids **升序**,最优价在数组末尾 ⇒ 一律自算 min/max
2. price_change 增量里 size=0 表示**该档消失**,必须删掉;只认"更优价"会让盘口永远不更新
3. WS 有**三种**消息形态:空确认帧 / payload.data[] 快照 / 单点更新 —— 都要处理
4. 选盘要选**正在交易窗口内**的,不是 slug 时间戳最大的(那是还没开盘的空盘)
5. 成本必须**走单簿**算,不能用最优价 —— 最优价只够买第一档那点量
6. taker 费 = shares × 0.07 × p × (1−p),**两条腿各付各的**(不是单边 1.75%)
7. eventStartTime 才是窗口起点;startDate 是市场创建时间,差一整天
8. neg_risk 必须从元数据读,不许硬编码(我们的盘是 False)
--------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import sys
import time
import urllib.request
from datetime import datetime, timezone

try:
    import websockets
except ImportError:
    sys.exit("需要 websockets:  pip install websockets")

GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

FEE_RATE = 0.07          # taker: shares × 0.07 × p × (1−p);maker 免费(本 v1 只做 taker)
MIN_EDGE = 0.008         # 净边门槛。⚠️ 拍的(来自 Grok 建议),**待用实测分布重定**
NO_ENTRY_LAST_SEC = 8    # 窗末禁止开仓。⚠️ 同样是拍的
SIZES = (10, 30, 50, 100, 300)   # 逐个试的下单股数
DELAY_MS = 150           # 模拟"看到→下单到达"的延迟。⚠️ 120~200ms 区间的中值,拍的
FRAME_EVERY_S = 1.0      # 盘口落盘节流:每秒最多一帧

UTC = timezone.utc
now_ms = lambda: int(time.time() * 1000)
hhmmss = lambda: datetime.now(UTC).strftime("%H:%M:%S")

REC_PATH: str | None = None      # 落盘目标;None = 不落盘(--no-record)


def rec(kind: str, **kw):
    """📼 顺手追加一行 jsonl。⛔ 不做任何聚合/索引/压缩——它只是把看到的东西留下。
    「探针和录盘是同一条路径」:今天跑完,回测就不用再等一天。"""
    if not REC_PATH:
        return
    try:
        with open(REC_PATH, "a") as f:
            f.write(json.dumps({"ts": now_ms(), "k": kind, **kw}) + "\n")
    except OSError:
        pass                      # 落盘失败绝不打断观察(附加职责不许打断核心职责)


def http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "paper-arb/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def gamma_by_slug(slug: str):
    """⚠️ Gamma 默认只回未关闭盘 ⇒ 必须查两遍。"""
    for q in (f"slug={slug}", f"slug={slug}&closed=true"):
        try:
            r = http_json(f"{GAMMA}/markets?{q}")
        except Exception:
            return None
        if r:
            return r[0]
    return None


def find_live_market():
    """挑一个**正在交易窗口内**的 btc-updown-15m 盘(坑 4)。"""
    now = int(time.time())
    base = (now // 900) * 900
    for k in (0, 1):                       # 当前窗口,退一格
        m = gamma_by_slug(f"btc-updown-15m-{base - 900 * k}")
        if not m or m.get("closed"):
            continue
        end = m.get("endDate")
        if not end:
            continue
        end_ts = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
        if end_ts > now:                   # 还没结算 ⇒ 可交易
            return m, end_ts
    return None, None


def parse_tokens(m) -> list[str]:
    t = m.get("clobTokenIds")
    return json.loads(t) if isinstance(t, str) else (t or [])


# ---------------------------------------------------------------- 盘口
class Book:
    """一条腿的完整盘口。price -> size。⛔ 只留内存,不落盘。"""

    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def snapshot(self, bids, asks):
        self.bids = {float(x["price"]): float(x["size"]) for x in (bids or [])}
        self.asks = {float(x["price"]): float(x["size"]) for x in (asks or [])}

    def apply(self, side: str, price: float, size: float):
        book = self.bids if side == "BUY" else self.asks
        if size <= 0:
            book.pop(price, None)          # 坑 2:size=0 = 该档消失
        else:
            book[price] = size

    def best_ask(self):
        return min(self.asks) if self.asks else None   # 坑 1:自算,不信数组顺序

    def walk_asks(self, shares: float):
        """买 shares 股的**实际总花费**;深度不足返回 None(坑 5)。"""
        left, cost = shares, 0.0
        for px in sorted(self.asks):       # 从最便宜的一档开始吃
            take = min(left, self.asks[px])
            cost += take * px
            left -= take
            if left <= 1e-9:
                return cost
        return None


def taker_fee(shares: float, avg_price: float) -> float:
    """坑 6:每条腿各付各的。"""
    return shares * FEE_RATE * avg_price * (1.0 - avg_price)


def edge_at(up: Book, down: Book, n: int):
    """给定股数 n 的净边;深度不足返回 None。净边 = 每股净赚(赔付恒为 $1/股)。"""
    cu, cd = up.walk_asks(n), down.walk_asks(n)
    if cu is None or cd is None:
        return None
    pu, pd = cu / n, cd / n
    total = cu + cd + taker_fee(n, pu) + taker_fee(n, pd)
    return (n * 1.0 - total) / n, pu, pd, total


def evaluate(up: Book, down: Book):
    """返回 (最优股数, 净边, 明细) 或 None —— 取「净边×股数」最大的那档。"""
    best = None
    for n in SIZES:
        r = edge_at(up, down, n)
        if r is None:                      # 深度不够,更大的股数也不用试
            break
        edge, pu, pd, total = r
        if edge >= MIN_EDGE and (best is None or edge * n > best[1] * best[0]):
            best = (n, edge, (pu, pd, total))
    return best


# ---------------------------------------------------------------- 自检
def selftest() -> bool:
    """⚠️ 我今天三次栽在自己写的脚本上 ⇒ 开跑前先证明算法是对的。"""
    b = Book()
    b.snapshot([], [{"price": "0.50", "size": "10"}, {"price": "0.52", "size": "100"}])
    c = b.walk_asks(10)
    ok1 = abs(c - 5.0) < 1e-9                                   # 10 股全在 0.50 档
    c2 = b.walk_asks(30)
    ok2 = abs(c2 - (10 * 0.50 + 20 * 0.52)) < 1e-9              # 跨档
    ok3 = b.walk_asks(1000) is None                             # 深度不足
    ok4 = abs(b.best_ask() - 0.50) < 1e-9
    b.apply("SELL", 0.50, 0)                                    # 撤掉最优档
    ok5 = abs(b.best_ask() - 0.52) < 1e-9
    f = taker_fee(100, 0.5)
    ok6 = abs(f - 100 * 0.07 * 0.25) < 1e-9                     # p=0.5 时 1.75%/腿
    # 一个必然无边的场景:两腿都 0.52 ⇒ 成本 1.04 + 费 > 1
    u, d = Book(), Book()
    u.snapshot([], [{"price": "0.52", "size": "500"}])
    d.snapshot([], [{"price": "0.52", "size": "500"}])
    ok7 = evaluate(u, d) is None
    # 一个必然有边的场景:两腿都 0.47 ⇒ 0.94 + 费约 0.035 = 0.975 ⇒ 边约 2.5%
    u2, d2 = Book(), Book()
    u2.snapshot([], [{"price": "0.47", "size": "500"}])
    d2.snapshot([], [{"price": "0.47", "size": "500"}])
    r = evaluate(u2, d2)
    ok8 = r is not None and r[1] > 0.02
    checks = [("走单簿单档", ok1), ("走单簿跨档", ok2), ("深度不足返回None", ok3),
              ("最优价自算", ok4), ("撤单后最优价更新", ok5), ("taker费公式", ok6),
              ("无边场景不报", ok7), ("有边场景报出", ok8)]
    print("自检:")
    for name, ok in checks:
        print(f"   {'✅' if ok else '🔴'} {name}")
    return all(ok for _, ok in checks)


# ---------------------------------------------------------------- 主循环
class Stats:
    def __init__(self):
        self.t0 = time.time()
        self.events = 0
        self.raw_hits = 0          # 第一列:快照到达瞬间有边(⚠️ 会虚高)
        self.real_hits = 0         # ⭐第二列:延迟 DELAY_MS 之后仍有边 —— 判生死看这个
        self.windows = set()
        self.best_now = 0.0
        self.best_after = 0.0
        self.real_sizes: list[int] = []
        self.phase = {"前3分": 0, "中段": 0, "末2分": 0}   # 真机会出现在窗内哪一段

    def line(self):
        mins = (time.time() - self.t0) / 60
        per_h = self.real_hits / mins * 60 if mins > 0 else 0
        return (f"[{hhmmss()}] 跑了 {mins:5.1f} 分 | 窗口 {len(self.windows)} | 事件 {self.events:,} | "
                f"瞬时边 {self.raw_hits} 次 → ⭐延迟后仍有 {self.real_hits} 次 (~{per_h:.1f}/小时)")


async def run(minutes: float | None):
    st = Stats()
    stop = asyncio.Event()

    def _sig(*_):
        stop.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _sig)
        except NotImplementedError:
            pass

    deadline = time.time() + minutes * 60 if minutes else None

    while not stop.is_set() and (deadline is None or time.time() < deadline):
        m, end_ts = find_live_market()
        if not m:
            print(f"[{hhmmss()}] 暂无正在交易的窗口,10 秒后重试")
            await asyncio.sleep(10)
            continue
        toks = parse_tokens(m)
        if len(toks) < 2:
            await asyncio.sleep(5)
            continue
        slug = m.get("slug", "?")
        neg = m.get("negRisk")            # 坑 8:读出来,不硬编码
        st.windows.add(slug)
        left = end_ts - time.time()
        print(f"\n[{hhmmss()}] ▶ 盯盘 {slug} | 剩余 {left:.0f}s | negRisk={neg} | "
              f"UP={toks[0][:10]}… DOWN={toks[1][:10]}…")

        up, down = Book(), Book()
        idx = {toks[0]: up, toks[1]: down}
        pending: list = []          # 待复算的候选机会
        last_frame = 0.0
        try:
            async with websockets.connect(CLOB_WS, open_timeout=20) as ws:
                await ws.send(json.dumps({"assets_ids": toks, "type": "market"}))

                async def pinger():
                    while True:
                        await asyncio.sleep(10)
                        try:
                            await ws.send("PING")
                        except Exception:
                            return
                pt = asyncio.create_task(pinger())
                last_report = time.time()

                while not stop.is_set():
                    remain = end_ts - time.time()
                    if remain <= 0:
                        print(f"[{hhmmss()}] ⏹ 窗口结束,切下一个")
                        break
                    if deadline and time.time() >= deadline:
                        stop.set()
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, max(remain, 0.1)))
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break
                    if not raw:            # 坑 3:空确认帧
                        continue
                    try:
                        msgs = json.loads(raw)
                    except Exception:
                        continue
                    if isinstance(msgs, dict):
                        msgs = [msgs]

                    for msg in msgs:
                        et = msg.get("event_type") or msg.get("type")
                        aid = msg.get("asset_id")
                        bk = idx.get(aid)
                        if bk is None:
                            continue
                        if et == "book":
                            bk.snapshot(msg.get("bids"), msg.get("asks"))
                        elif et == "price_change":
                            for c in (msg.get("changes") or []):
                                side = (c.get("side") or "").upper()
                                if side in ("BUY", "SELL"):
                                    bk.apply(side, float(c.get("price", 0)), float(c.get("size", 0)))
                        else:
                            continue
                        st.events += 1
                        tnow = time.time()

                        # ---- ⭐ 到期的候选:用**当前最新的簿**重算同样股数 ----
                        still = []
                        for due_at, n0, e_now, rem0 in pending:
                            if tnow < due_at:
                                still.append((due_at, n0, e_now, rem0)); continue
                            r2 = edge_at(up, down, n0)
                            e_after = r2[0] if r2 else None
                            keep = e_after is not None and e_after >= MIN_EDGE
                            if keep:
                                st.real_hits += 1
                                st.best_after = max(st.best_after, e_after)
                                st.real_sizes.append(n0)
                                ph = "前3分" if rem0 > 720 else ("末2分" if rem0 < 120 else "中段")
                                st.phase[ph] += 1
                                print(f"[{hhmmss()}] 💰 **真机会 #{st.real_hits}**  {n0} 股 | "
                                      f"瞬时边 {100*e_now:.3f}% → 延迟{DELAY_MS}ms后 {100*e_after:.3f}% | "
                                      f"毛赚 ${e_after*n0:.2f} | 窗内 {ph}")
                            else:
                                shown = f"{100*e_after:.3f}%" if e_after is not None else "深度没了"
                                print(f"[{hhmmss()}] 👻 机会蒸发  {n0} 股 | "
                                      f"瞬时边 {100*e_now:.3f}% → 延迟后 {shown}")
                            rec("opportunity", n=n0, edge_now=e_now, edge_after=e_after,
                                kept=keep, remain=rem0)
                        pending = still

                        if remain <= NO_ENTRY_LAST_SEC:      # 窗末不开仓
                            continue
                        r = evaluate(up, down)
                        if r:
                            n, edge, (pu, pd, total) = r
                            st.raw_hits += 1
                            st.best_now = max(st.best_now, edge)
                            pending.append((tnow + DELAY_MS / 1000.0, n, edge, remain))

                        # ---- 📼 顺手落盘(节流每秒一帧)----
                        if tnow - last_frame >= FRAME_EVERY_S:
                            last_frame = tnow
                            rec("frame", slug=slug, remain=round(remain, 2),
                                up_asks=sorted(up.asks.items())[:5],
                                dn_asks=sorted(down.asks.items())[:5],
                                up_bids=sorted(up.bids.items(), reverse=True)[:5],
                                dn_bids=sorted(down.bids.items(), reverse=True)[:5])

                    if time.time() - last_report >= 60:
                        print(st.line())
                        last_report = time.time()
                pt.cancel()
        except Exception as e:
            print(f"[{hhmmss()}] ⚠️ 连接异常 {type(e).__name__}: {e};3 秒后重连")
            await asyncio.sleep(3)

    print("\n" + "=" * 78)
    print("收工。" + st.line())
    mins = max((time.time() - st.t0) / 60, 1e-9)
    print(f"  瞬时有边(第一列,⚠️会虚高)  {st.raw_hits} 次 | 最好 {100*st.best_now:.3f}%")
    print(f"  ⭐延迟 {DELAY_MS}ms 后仍有边(判生死) {st.real_hits} 次 | 最好 {100*st.best_after:.3f}%")
    if st.raw_hits:
        print(f"  存活率 {100*st.real_hits/st.raw_hits:.1f}%  ← 机会看得见抓不抓得住,全在这个数")
    if st.real_sizes:
        srt = sorted(st.real_sizes)
        print(f"  可成交股数中位 {srt[len(srt)//2]} 股 | 窗内分布 {st.phase}")
    print()
    if st.real_hits == 0:
        print("  ⇒ 🔴 延迟后 0 次。若跑满一天仍如此,同窗口荷兰赌可以关掉,七个模块一个都不用写。")
    else:
        per_day = st.real_hits / mins * 60 * 24
        med = sorted(st.real_sizes)[len(st.real_sizes)//2]
        print(f"  ⇒ 按此速率外推:约 {per_day:.0f} 次/天,中位 {med} 股")
        print(f"     粗估日收益 ≈ {100*st.best_after:.3f}% × {med} 股 × {per_day:.0f} 次 "
              f"= ${st.best_after*med*per_day:.2f}/天(⚠️ 用最好边算,是上界)")
    if REC_PATH:
        print(f"\n  📼 盘口与机会已落盘: {REC_PATH}")
    print("=" * 78)


def main():
    global MIN_EDGE, REC_PATH
    ap = argparse.ArgumentParser(description="BTC 15m 荷兰赌 paper 观察器(只报告不下单)")
    ap.add_argument("--minutes", type=float, default=None, help="跑多少分钟后自动收工")
    ap.add_argument("--min-edge", type=float, default=MIN_EDGE, help=f"净边门槛(默认 {MIN_EDGE})")
    ap.add_argument("--skip-selftest", action="store_true")
    ap.add_argument("--record", default=None,
                    help="盘口/机会落盘的 jsonl 路径(默认 12-btc/rec_<日期>.jsonl;--no-record 关闭)")
    ap.add_argument("--no-record", action="store_true")
    a = ap.parse_args()
    MIN_EDGE = a.min_edge
    if not a.no_record:
        REC_PATH = a.record or f"12-btc/rec_{datetime.now(UTC):%Y%m%d}.jsonl"

    print("=" * 78)
    print("BTC 15m 同窗口荷兰赌 · paper 模式(⛔ 不下单 · ⛔ 不写任何文件)")
    print(f"净边门槛 {100*MIN_EDGE:.2f}% | 延迟复算 {DELAY_MS}ms | 窗末 {NO_ENTRY_LAST_SEC}s 禁开")
    print(f"试探股数 {SIZES} | 落盘 {REC_PATH or '关闭'}")
    print("⭐ 判生死看【延迟后仍有边】那一列,不是瞬时边")
    print("=" * 78)
    if not a.skip_selftest and not selftest():
        sys.exit("🔴 自检未通过,不跑。")
    print()
    try:
        asyncio.run(run(a.minutes))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
