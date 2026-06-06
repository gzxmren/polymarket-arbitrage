#!/usr/bin/env python3
"""
C-2 跨市场套利 — premise 快检(非回测,发现型脚本)
用法:
  python3 08-backtests/cross_market_premise.py            # 拉两边活跃市场→候选配对→并排价格
  python3 08-backtests/cross_market_premise.py --json     # 结果落 results/
  python3 08-backtests/cross_market_premise.py --limit-pm 400 --limit-kalshi 800

设计见 docs/NEXT_STEPS_DESIGN_2026-06-04.md §B。
目的:回答 premise —— 真钱场(唯一对 = Polymarket ↔ Kalshi)里到底存不存在
"同一事件 + 同结算标准 + 同时点、可同时持仓、能收敛获利"的配对?

铁律:不复用 semantic_arbitrage 的 LLM 模糊匹配(已证生成垃圾)。这里只做
token 重叠粗筛,把候选并排打出来供"人工核结算等价性",不自动判定等价。
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.error import URLError

RESULTS_DIR = Path(__file__).resolve().parent / "results"

GAMMA_API = "https://gamma-api.polymarket.com"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"  # elections host 现可用
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

DISCLAIMER = [
    "本脚本只做存在性粗筛(token 重叠),不判定结算等价 —— 等价性须人工核对结算规则。",
    "价格为快照单点(PM=outcomePrices 中间价,Kalshi=yes_bid/ask 中点),非可成交深度。",
    "Manifold(虚拟币)、Metaculus(无交易)已排除,真钱可套利对仅 Polymarket↔Kalshi。",
    "可执行关另算:Kalshi 受监管(KYC/地域/独立资金),本账户能否两边建仓未在此验证。",
]

# 归一化用停用词:撮合无信息量、跨平台措辞差异大的词
STOPWORDS = {
    "will", "the", "a", "an", "be", "to", "of", "in", "on", "at", "by", "for",
    "and", "or", "is", "are", "before", "after", "by", "than", "this", "that",
    "who", "what", "when", "which", "next", "any", "with", "from", "as", "his",
    "her", "their", "have", "has", "become", "first", "ever", "us", "u.s.",
    "yes", "no", "market", "above", "below", "over", "under", "between",
}

# 月份→数字,便于日期对齐
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"], 1)}


def _get(url: str, timeout: int = 25, retry: int = 3) -> dict | list:
    """带重试(两端 API 偶发 SSL hostname-mismatch / 瞬断,重试即恢复)。"""
    last: Exception | None = None
    for _ in range(retry):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (URLError, TimeoutError, OSError, ssl.SSLError) as e:
            last = e
    raise last if last else RuntimeError("unreachable")


def tokenize(title: str) -> set[str]:
    """标题→有信息量的小写 token 集合(去停用词、保留年份/数字)。"""
    title = title.lower().replace("’", "'")
    raw = re.findall(r"[a-z0-9$.%]+", title)
    toks = set()
    for t in raw:
        t = t.strip(".$")
        if not t or t in STOPWORDS or len(t) <= 1:
            continue
        toks.add(_MONTHS.get(t[:3], t) if t[:3] in _MONTHS and t[:3] == t else t)
    return toks


# ---------------------------------------------------------------- fetchers
def fetch_polymarket(limit: int) -> list[dict]:
    """Gamma 活跃二元市场,按成交量降序分页。"""
    out, offset, page = [], 0, 100
    while len(out) < limit:
        url = (f"{GAMMA_API}/markets?closed=false&active=true&limit={page}"
               f"&offset={offset}&order=volumeNum&ascending=false")
        try:
            batch = _get(url)
        except (URLError, TimeoutError, OSError) as e:
            print(f"  ! Polymarket 拉取中断 @offset={offset}: {e}", file=sys.stderr)
            break
        if not batch:
            break
        for m in batch:
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = json.loads(m.get("outcomePrices") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if {o.lower() for o in outcomes} != {"yes", "no"}:
                continue  # 只看二元 Yes/No
            yi = next((i for i, o in enumerate(outcomes) if o.lower() == "yes"), None)
            if yi is None or yi >= len(prices):
                continue
            q = m.get("question", "")
            out.append({
                "venue": "polymarket",
                "id": m.get("conditionId") or m.get("slug", ""),
                "title": q,
                "tokens": tokenize(q),
                "yes_price": float(prices[yi]),
                "volume": float(m.get("volumeNum") or m.get("volume") or 0),
                "close": m.get("endDate", ""),
                "slug": m.get("slug", ""),
            })
        offset += page
        if len(batch) < page:
            break
    return out[:limit]


def _fp(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_kalshi(max_events: int) -> list[dict]:
    """Kalshi open 市场:先分页取非体育事件(轻量),再逐事件拉市场取价。

    注意(elections host 字段坑):列表端点的价格字段为 `yes_bid_dollars`/
    `yes_ask_dollars`(字符串美元),旧的 `yes_bid`(分整数)恒为 None;且 events 嵌套
    表示不带价,必须按 event_ticker 单独拉 /markets 才有盘口。
    """
    # 1) 收集非体育事件(轻量,带 category/title/ticker)
    events, cursor = [], None
    while len(events) < max_events:
        url = f"{KALSHI_API}/events?status=open&with_nested_markets=false&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = _get(url)
        except (URLError, TimeoutError, OSError) as e:
            print(f"  ! Kalshi events 拉取中断: {e}", file=sys.stderr)
            break
        for ev in d.get("events", []):
            if ev.get("category", "").lower() == "sports":
                continue
            events.append(ev)
        cursor = d.get("cursor")
        if not cursor:
            break
    events = events[:max_events]

    # 2) 逐事件拉市场取盘口(线程池并行,几百次调用压到几十秒)
    def _one(ev: dict) -> list[dict]:
        et = ev.get("event_ticker") or ev.get("ticker")
        if not et:
            return []
        try:
            md = _get(f"{KALSHI_API}/markets?event_ticker={et}", timeout=15)
        except (URLError, TimeoutError, OSError):
            return []
        cat, ev_title = ev.get("category", ""), ev.get("title", "")
        rows = []
        for m in md.get("markets", []):
            if m.get("status") != "active":
                continue
            yb, ya = _fp(m.get("yes_bid_dollars")), _fp(m.get("yes_ask_dollars"))
            if yb is None and ya is None:
                continue
            mid = ((yb if yb is not None else ya) +
                   (ya if ya is not None else yb)) / 2
            if not 0 < mid < 1:
                continue
            sub = m.get("yes_sub_title") or m.get("subtitle") or ""
            title = f"{ev_title} {sub}".strip() if sub else ev_title
            rows.append({
                "venue": "kalshi",
                "id": m.get("ticker", ""),
                "title": title,
                "tokens": tokenize(title),
                "yes_price": round(mid, 4),
                "volume": float(_fp(m.get("liquidity_dollars")) or 0),
                "close": m.get("close_time", ""),
                "category": cat,
            })
        return rows

    out = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for rows in ex.map(_one, events):
            out.extend(rows)
    return out


# ---------------------------------------------------------------- matching
def overlap_score(a: set[str], b: set[str]) -> tuple[float, set[str]]:
    """Jaccard,外加共享 token 中"稀有词"(长度≥4 的非数字)加权。"""
    if not a or not b:
        return 0.0, set()
    shared = a & b
    if not shared:
        return 0.0, set()
    jac = len(shared) / len(a | b)
    rare = {t for t in shared if len(t) >= 4 and not t.isdigit()}
    return jac + 0.15 * len(rare), shared


def find_candidates(pm: list[dict], ks: list[dict], min_shared: int,
                    top: int) -> list[dict]:
    cands = []
    for p in pm:
        best = None
        for k in ks:
            shared = p["tokens"] & k["tokens"]
            if len(shared) < min_shared:
                continue
            score, sh = overlap_score(p["tokens"], k["tokens"])
            if best is None or score > best["score"]:
                best = {"score": score, "shared": sorted(sh), "k": k}
        if best:
            spread = abs(p["yes_price"] - best["k"]["yes_price"])
            cands.append({
                "score": round(best["score"], 3),
                "shared": best["shared"],
                "spread": round(spread, 4),
                "pm_title": p["title"], "pm_yes": p["yes_price"],
                "pm_vol": p["volume"], "pm_close": p["close"], "pm_slug": p["slug"],
                "ks_title": best["k"]["title"], "ks_yes": best["k"]["yes_price"],
                "ks_vol": best["k"]["volume"], "ks_close": best["k"]["close"],
                "ks_cat": best["k"].get("category", ""), "ks_id": best["k"]["id"],
            })
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands[:top]


def main():
    ap = argparse.ArgumentParser(description="C-2 跨市场套利 premise 快检")
    ap.add_argument("--limit-pm", type=int, default=400)
    ap.add_argument("--limit-kalshi", type=int, default=350,
                    help="展开多少个非体育 Kalshi 事件取盘口(每个=1次HTTP)")
    ap.add_argument("--min-shared", type=int, default=2,
                    help="候选最少共享 token 数(降低=更宽松更多噪声)")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    print("拉取 Polymarket 活跃二元市场 …")
    pm = fetch_polymarket(args.limit_pm)
    print(f"  Polymarket: {len(pm)} 个二元 Yes/No 市场")
    print("拉取 Kalshi open 市场(剔除 Sports) …")
    ks = fetch_kalshi(args.limit_kalshi)
    print(f"  Kalshi: {len(ks)} 个市场")

    cands = find_candidates(pm, ks, args.min_shared, args.top)

    print("\n" + "=" * 72)
    print("📊 C-2 跨市场 premise 快检 — 候选同事件配对(token 重叠粗筛,待人工核等价)")
    print("=" * 72)
    print(f"生成: {datetime.now():%Y-%m-%d %H:%M}  |  候选 {len(cands)} 对"
          f"  (PM={len(pm)} × Kalshi={len(ks)}, min_shared={args.min_shared})")

    for i, c in enumerate(cands, 1):
        print(f"\n[{i:2d}] score={c['score']}  共享: {', '.join(c['shared'])}")
        print(f"     PM   : {c['pm_title'][:68]}")
        print(f"            Yes={c['pm_yes']:.3f}  vol=${c['pm_vol']:,.0f}  截止 {c['pm_close'][:10]}")
        print(f"     Kalshi: {c['ks_title'][:68]}  [{c['ks_cat']}]")
        print(f"            Yes={c['ks_yes']:.3f}  vol={c['ks_vol']:,.0f}  截止 {c['ks_close'][:10]}")
        print(f"     → 价差 |ΔYes| = {c['spread']:.3f}")

    print("\n" + "=" * 72)
    print("⚠️ 诚实声明:")
    for d in DISCLAIMER:
        print(f"  · {d}")
    print("=" * 72)

    if args.json:
        RESULTS_DIR.mkdir(exist_ok=True)
        out = {
            "generated_at": datetime.now().isoformat(),
            "params": vars(args),
            "counts": {"polymarket": len(pm), "kalshi": len(ks),
                       "candidates": len(cands)},
            "disclaimer": DISCLAIMER,
            "candidates": cands,
        }
        path = RESULTS_DIR / f"cross_market_premise_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {path}")


if __name__ == "__main__":
    main()
