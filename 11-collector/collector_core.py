#!/usr/bin/env python3
"""collector_core.py — 按市场轮询主循环。

兑现《数据契约 v1.1》§3 / §4.1 / §7:
- 逐事件市场 GET /trades?market=<cid> 分页;单市场 offset 上限实测 ~1万,逼近即告警。
- 增量:用 storage.watermark 只收比库内更新的成交(避免重抓,减小去重压力)。
- 解析铁律:asset 在 clobTokenIds 定下标(弃用 outcomeIndex);定位失败 → 拒绝入库 + parse_reject 计数。
- 审计:累计 http_4xx / rate_limit / offset_overflow / parse_reject,每小时落心跳;
  offset_overflow>0 → 抛出(供守护层暂停 + 紧急通知),不带病狂奔。

异常枚举逐条对照 CLAUDE.md 清单(URLError/TimeoutError/OSError/HTTPException/JSONDecodeError)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import storage_engine as se
from discovery_service import refresh_and_registry, resolve_asset_index

_ORIG_GAI = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _ORIG_GAI(h, p, socket.AF_INET, t, pr, fl)

TRADES = "https://data-api.polymarket.com/trades"
UA = {"User-Agent": "polymarket-rebirth-collector/1.1"}
OFFSET_CAP = 10000       # 实测单市场硬顶;offset 逼近即"该市场增量可能溢出"
PAGE = 500               # 每页
OFFSET_WARN = 8000       # 逼近上限的告警线(留余量,提示需压频)


def _get(url: str, counters: dict, tries: int = 5):
    """GET;429/4xx 计数;重试耗尽返回 None(区别于确认空)。"""
    for _ in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            if e.code == 429:
                counters["rate_limit_hits"] += 1
                time.sleep(3)
                continue
            if 400 <= e.code < 500:
                counters["http_4xx_count"] += 1
                return None
            time.sleep(1.5)
        except (URLError, TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError):
            time.sleep(1.5)
    return None


def poll_market(market: dict, counters: dict, wm: int | None) -> list[dict]:
    """轮询单个市场,返回比 watermark 更新的、已解析的 trades 行。

    分页从 offset=0(最新)往回,直到 timestamp <= watermark(增量)或到接口上限。
    wm 由调用方一次性预取(见 run_once),避免每市场开 DuckDB 连接。
    """
    cid = market["condition_id"]
    token_ids = [market["token_id_0"], market["token_id_1"]]
    new_rows, offset = [], 0
    now = int(dt.datetime.now(dt.UTC).timestamp())
    while offset <= OFFSET_CAP:
        d = _get(f"{TRADES}?limit={PAGE}&market={cid}&offset={offset}", counters)
        if not isinstance(d, list) or not d:
            break
        stop = False
        for t in d:
            ts = int(t.get("timestamp", 0))
            if wm is not None and ts <= wm:
                stop = True
                break
            pos = resolve_asset_index(t.get("asset"), token_ids)
            if pos is None:
                counters["parse_reject_count"] += 1   # 定位失败,拒绝入库(§4.1)
                continue
            new_rows.append({
                "transaction_hash": t.get("transactionHash"),
                "proxy_wallet": t.get("proxyWallet"),
                "condition_id": cid,
                "asset": str(t.get("asset")),
                "outcome_index": pos,                 # 我们解析的真实下标(非接口 999)
                "outcome_label": t.get("outcome"),
                "side": t.get("side"),
                "size": float(t.get("size") or 0),
                "price": float(t.get("price") or 0),
                "timestamp": ts,
                "ingested_at": now,
            })
        if stop or len(d) < PAGE:
            break
        offset += PAGE
        if offset >= OFFSET_WARN and (wm is None or (new_rows and new_rows[-1]["timestamp"] > wm)):
            # 逼近 offset 上限仍未追到 watermark → 更早历史够不着(诚实截断)。
            # ★保留已抓的近端成交(有效数据,不丢),计数供守护层压频/告警,只停止再往回翻。
            counters["offset_overflow_count"] += 1
            print(f"    ⚠️ offset 截断: {cid[:14]}.. 保留近端 {len(new_rows)} 笔,更早历史待压频回填",
                  flush=True)
            break
    counters["total_markets_polled"] += 1
    return new_rows


def run_once(limit: int | None = None, sample: int = 5000, max_new: int | None = None) -> dict:
    """发现一轮活跃市场 → 逐市场增量轮询落库 → 写审计心跳。返回本轮计数。

    v1.2:市场来自 Firehose 发现(refresh_and_registry),不再全量枚举。
    """
    markets, disc = refresh_and_registry(sample_limit=sample, max_new=max_new)
    print(f"发现层: {disc}")
    if limit:
        markets = markets[:limit]
    counters = {k: 0 for k in (
        "total_markets_polled", "http_4xx_count", "rate_limit_hits",
        "offset_overflow_count", "dedup_collapse_count", "parse_reject_count")}
    # 一次性预取所有市场 watermark(空湖返回 {})
    con = se.duckdb_conn()
    try:
        wms = se.all_watermarks(con)
    finally:
        con.close()
    total_written = 0
    for m in markets:
        # 溢出时 poll_market 已保留近端并计数(不再抛异常丢批);守护层据心跳 offset_overflow 压频/告警
        rows = poll_market(m, counters, wms.get(m["condition_id"]))
        if rows:
            se.write_trades(rows)
            total_written += len(rows)
        time.sleep(0.1)  # 礼貌节流(全局无 429,仍留余量)
    counters["new_trades"] = total_written
    counters["register_fail"] = disc.get("register_fail", 0)
    se.write_audit_heartbeat(counters)
    print(f"本轮: 市场 {counters['total_markets_polled']} | 新成交 {total_written} | "
          f"4xx {counters['http_4xx_count']} | 限流 {counters['rate_limit_hits']} | "
          f"offset溢出 {counters['offset_overflow_count']} | 解析拒绝 {counters['parse_reject_count']}")
    return counters


# TODO(ops,非核心逻辑):
#  - 守护层:systemd timer(Persistent=true 防 suspend 漏跑)按频率调用 run_once。
#  - 自适应频率:offset_overflow 命中的市场下轮 <5 分钟;已关闭未结算市场升频抓结算窗口。
#  - offset_overflow_count>0 时紧急通知(Telegram/push)+ 暂停开关。
#  - 已结算市场移冷存、不再轮询。
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="发现+轮询一轮")
    ap.add_argument("--limit", type=int, default=None, help="只轮前 N 个市场(试跑)")
    ap.add_argument("--sample", type=int, default=5000, help="firehose 发现采样条数")
    ap.add_argument("--max-new", type=int, default=None, help="本轮最多注册 N 个新市场(试跑)")
    args = ap.parse_args()
    if args.once:
        run_once(limit=args.limit, sample=args.sample, max_new=args.max_new)
    else:
        ap.print_help()
