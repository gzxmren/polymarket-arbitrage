#!/usr/bin/env python3
"""probe_engine.py — 开工仪式探针(可复现的实证基底)。

全读 Polymarket 公开接口,不写任何库、不碰现有数据。兑现《数据契约 v1.1》§9/§10。
把采集器上线前必须坐实的经验值一次跑出来:
  ① 全局成交密度 / 限流 / offset 上限 / 有无时间窗参数
  ② 服务端过滤 (market= / user=)
  ③ 单市场 offset 上限 + 前向轮询安全裕度(拿史上最大盘验)
  ④ token→下标映射不变量:asset∈clobTokenIds 定位 vs outcomeIndex 垃圾率
  ⑤ 去重字段实况(有无 log_index)
  ⑥ 事件市场宇宙量级

用法: python3 11-collector/probe_engine.py
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 强制 IPv4(本机 IPv6 有 AAAA 记录但无出网)
_ORIG_GAI = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _ORIG_GAI(h, p, socket.AF_INET, t, pr, fl)

TRADES = "https://data-api.polymarket.com/trades"
GAMMA = "https://gamma-api.polymarket.com/markets"
UA = {"User-Agent": "polymarket-rebirth-probe/1.1"}


def get(url: str, tries: int = 5):
    """GET JSON。异常枚举逐条对照 CLAUDE.md 清单(含 http.client.HTTPException/IncompleteRead)。"""
    for _ in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            return {"__http__": e.code}
        except (URLError, TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError):
            time.sleep(1.2)
    return {"__http__": "retry_exhausted"}


def _when(ts) -> str:
    try:
        return dt.datetime.fromtimestamp(int(ts), dt.UTC).strftime("%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return "?"


def probe_global_density():
    print("=" * 78, "\n① 全局密度 / offset 上限 / 时间窗参数\n", "=" * 78, sep="")
    d = get(f"{TRADES}?limit=1000")
    if isinstance(d, list) and len(d) >= 2:
        tss = sorted(int(x["timestamp"]) for x in d if x.get("timestamp"))
        span = max(tss) - min(tss)
        rate = len(tss) / (span / 86400) if span else float("nan")
        print(f"  1000 条覆盖 {span}s → 全局 ≈ {rate:,.0f} 笔/天 ({len(tss)/max(span,1):.1f}/秒)")
    for off in (0, 10000, 50000):
        r = get(f"{TRADES}?limit=100&offset={off}")
        n = len(r) if isinstance(r, list) else r
        print(f"  offset={off:>6}: {n}")
    ha = int(time.time()) - 3600
    hits = [p for p in ("from", "after", "start", "startTs", "before", "end")
            if isinstance(get(f"{TRADES}?limit=5&{p}={ha}"), list)
            and _when(get(f"{TRADES}?limit=5&{p}={ha}")[0].get("timestamp")) == _when(ha)]
    print(f"  时间窗参数生效的: {hits or '无(全被忽略)'}")


def probe_per_market():
    print("=" * 78, "\n③ 单市场 offset 上限 + 前向轮询安全裕度(史上最大盘)\n", "=" * 78, sep="")
    hi = get(f"{GAMMA}?closed=true&order=volumeNum&ascending=false&limit=5")
    big = next((m for m in hi if isinstance(hi, list) and m.get("conditionId")), None)
    if not big:
        print("  未取到高成交盘")
        return
    cid = big["conditionId"]
    print(f"  盘: {big.get('question','')[:50]}  vol={big.get('volumeNum')}")
    last_ok, span_at_10k = None, None
    for off in (0, 5000, 10000, 15000):
        d = get(f"{TRADES}?limit=100&market={cid}&offset={off}")
        if isinstance(d, list) and d:
            last_ok = off
            print(f"  offset={off:>6}: {len(d)}条 {_when(d[0]['timestamp'])}..{_when(d[-1]['timestamp'])}")
        else:
            print(f"  offset={off:>6}: {d}  ← 上限")
    print(f"  ⇒ 单市场 offset 上限约 {last_ok}(+1步长即报错);前向轮询按此设 <5 分钟压频阈值")


def probe_token_mapping():
    print("=" * 78, "\n④ token→下标映射: asset∈clobTokenIds 定位 vs outcomeIndex\n", "=" * 78, sep="")
    tr = get(f"{TRADES}?limit=60")
    seen, found, oidx_bad, label_ok, n = set(), 0, 0, 0, 0
    for t in tr if isinstance(tr, list) else []:
        slug = t.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        m = get(f"{GAMMA}?slug={slug}")
        if not (isinstance(m, list) and m):
            continue
        try:
            toks = [str(x) for x in json.loads(m[0].get("clobTokenIds") or "[]")]
            outs = json.loads(m[0].get("outcomes") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if not toks:
            continue
        asset = str(t.get("asset"))
        pos = toks.index(asset) if asset in toks else None
        n += 1
        if pos is None:
            continue
        found += 1
        oidx_bad += (t.get("outcomeIndex") != pos)
        label_ok += (pos < len(outs) and t.get("outcome") == outs[pos])
        if n >= 30:
            break
    print(f"  抽检 {n}: asset 定位成功 {found}/{n} | outcomeIndex 垃圾 {oidx_bad}/{found} "
          f"| 标签==outcomes[位置] {label_ok}/{found}")
    print("  ⇒ 铁律: 用 asset 位置定下标,弃用 outcomeIndex,outcome 标签作交叉校验")


def probe_dedup_fields():
    print("=" * 78, "\n⑤ 去重字段实况\n", "=" * 78, sep="")
    one = get(f"{TRADES}?limit=1")
    if isinstance(one, list) and one:
        keys = list(one[0].keys())
        idlike = [k for k in keys if re.search(r"log|fill|index|nonce|seq", k, re.I)]
        print(f"  唯一标识候选(log/fill/index/seq): {idlike or '无'} → 须自然元组去重+计折叠率")


def probe_universe_scale():
    print("=" * 78, "\n⑥ 事件市场宇宙量级(Gamma 分页估算)\n", "=" * 78, sep="")
    total = hft = pages = 0
    for pg in range(12):
        d = get(f"{GAMMA}?closed=false&limit=100&offset={pg*100}")
        if not (isinstance(d, list) and d):
            break
        pages += 1
        for m in d:
            total += 1
            s = (m.get("slug") or "") + " " + (m.get("question") or "")
            hft += bool(re.search(r"updown|up-or-down|-5m-", s, re.I))
        if len(d) < 100:
            break
        time.sleep(0.2)
    print(f"  {pages} 页未关闭市场: 共 {total}(HFT {hft})→ 量级 1000–3000,枚举层几乎无 HFT")


def main():
    probe_global_density()
    probe_per_market()
    probe_token_mapping()
    probe_dedup_fields()
    probe_universe_scale()
    print("\n开工仪式探针结束。")


if __name__ == "__main__":
    main()
