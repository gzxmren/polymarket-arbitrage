#!/usr/bin/env python3
"""settlement_watcher.py — 结算守望:抓已关闭未结算市场的官方结算真值。

兑现《数据契约 v1.2》§4.2(已关闭未结算须持续重访抓结算窗口)。

为什么需要它:已关闭市场**停止成交 → 不再出现在 Firehose**,故发现层永远不会重新轮询它。
但它的**官方结算(0/1)= 我们要的地面真值**,只能靠注册表驱动的独立守望去 Gamma 重查。

只增不改:结算写入 = append 一条新的注册表行(resolved_outcome 已填);load_registry 取最新版。
"""
from __future__ import annotations

import argparse
import datetime as dt

from discovery_service import (
    MARKETS_SCHEMA, REGISTRY_DIR, _atomic_write_parquet, _lookup_gamma,
    load_registry, parse_market,
)
import pyarrow as pa
import uuid


def _end_passed(end_date: str | None) -> bool:
    """end_date 是否已过(该结算却还没结算的才值得重查)。解析失败按'已过'处理(宁可多查)。"""
    if not end_date:
        return True
    try:
        d = dt.datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.UTC)
        return d <= dt.datetime.now(dt.UTC)
    except ValueError:
        return True


def watch_settlements(max_check: int | None = 80) -> dict:
    """扫注册表里 resolved_outcome 仍为空、且 end_date 已过的市场,重查 Gamma;
    新拿到干净 0/1 的,append 新注册表行。返回计数(不静默:失败计数)。"""
    reg = load_registry()
    pending = [r for r in reg.values()
               if r["resolved_outcome"] is None and _end_passed(r["end_date"])]
    pending.sort(key=lambda r: r["end_date"] or "")  # 先查最早到期的
    now = int(dt.datetime.now(dt.UTC).timestamp())
    rows, newly, fail = [], 0, 0
    for r in pending[:max_check]:
        m = _lookup_gamma(r["slug"]) if r["slug"] else None
        if not m:
            fail += 1
            continue
        parsed = parse_market(m)
        if parsed and parsed["resolved_outcome"] is not None:
            parsed["snapshot_at"] = now
            rows.append(parsed)
            newly += 1
    if rows:
        dest = REGISTRY_DIR / f"settle-{uuid.uuid4().hex}.parquet"
        _atomic_write_parquet(pa.Table.from_pylist(rows, schema=MARKETS_SCHEMA), dest)
    return {"pending_settlement": len(pending), "newly_resolved": newly, "lookup_fail": fail}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-check", type=int, default=80)
    args = ap.parse_args()
    print(watch_settlements(args.max_check))
