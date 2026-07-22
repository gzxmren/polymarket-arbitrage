#!/usr/bin/env python3
"""discovery_service.py — Firehose 发现驱动的宇宙发现 + 按需注册。

兑现《数据契约 v1.2》§3(发现层/注册层)/§7(覆盖闭环):
- 现实毒打(2026-07-22):开放市场数万级、Gamma offset 硬顶 2100、keyset 每页 100 且 ~3.7s
  → 全量枚举不可行且稀释信号。改为**采样全局 /trades 发现活跃市场**(近 2 分钟 ~622 个)。
- 发现层:sample_firehose → extract_active(去重 conditionId,§2.2 剔 HFT)。无金额/排序截断。
- 注册层:对不在注册表的新市场按需查 Gamma(?slug=),解析 token/结算,append-only 落注册表。
- 铁律不变(§4.1):asset 在 clobTokenIds 定下标(弃 outcomeIndex),此模块导出 resolve_asset_index。

注册表 = append-only Parquet(registry/*.parquet);load_registry 按 condition_id 取最新一版。
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import re
import socket
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from storage_engine import DATA_ROOT, _atomic_write_parquet  # noqa: E402

_ORIG_GAI = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _ORIG_GAI(h, p, socket.AF_INET, t, pr, fl)

GAMMA = "https://gamma-api.polymarket.com/markets"
TRADES = "https://data-api.polymarket.com/trades"
UA = {"User-Agent": "polymarket-rebirth-discovery/1.2"}
REGISTRY_DIR = DATA_ROOT / "registry"

# --- HFT 分类器(契约 §2.2,先宽后严)---
_HFT_CORE = re.compile(r"updown|up-or-down", re.I)
_HFT_WINDOW = re.compile(r"-5m-|5-?min|\b\d{1,2}:\d{2}\s?(?:AM|PM)\b", re.I)
_HFT_SUSPECT = re.compile(r"-1h-|hourly|-15m-|-30m-", re.I)


def classify_market(m: dict) -> tuple[str, bool]:
    """(market_class, hft_suspect)。读 slug + question|title(兼容 Gamma 与 firehose 两种对象)。"""
    s = f"{m.get('slug','')} {m.get('question') or m.get('title') or ''}"
    if _HFT_CORE.search(s) and _HFT_WINDOW.search(s):
        return "hft_crypto", True
    return "event", bool(_HFT_SUSPECT.search(s))


def resolve_asset_index(asset: str, clob_token_ids: list[str]) -> int | None:
    """🔴 铁律:asset 在 clobTokenIds 的位置 = outcome 下标。定位失败返回 None(调用方拒绝入库+计数)。"""
    a = str(asset)
    toks = [str(x) for x in clob_token_ids]
    return toks.index(a) if a in toks else None


def _get(url: str, tries: int = 5):
    for _ in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            return {"__http__": e.code}
        except (URLError, TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError):
            time.sleep(1.2)
    return {"__http__": "retry_exhausted"}


# ---------- 发现层 ----------

def sample_firehose(limit: int = 5000) -> list[dict]:
    """取最新 ~limit 条全局成交(每页 1000)。offset 只够 ~4 分钟,故仅作'发现',不作采集。"""
    out = []
    for off in range(0, limit, 1000):
        d = _get(f"{TRADES}?limit=1000&offset={off}")
        if not isinstance(d, list) or not d:
            break
        out.extend(d)
        time.sleep(0.2)
    return out


def extract_active(trades: list[dict]) -> dict[str, dict]:
    """去重 conditionId → {cid: 首见 stub(slug/title)};当场剔除 hft_crypto。"""
    active: dict[str, dict] = {}
    for t in trades:
        cid = t.get("conditionId")
        if not cid or cid in active:
            continue
        cls, _ = classify_market(t)
        if cls == "hft_crypto":
            continue
        active[cid] = {"slug": t.get("slug"), "title": t.get("title")}
    return active


# ---------- 注册层 ----------

MARKETS_SCHEMA = pa.schema([
    ("condition_id", pa.string()), ("slug", pa.string()), ("title", pa.string()),
    ("event_slug", pa.string()), ("market_class", pa.string()), ("hft_suspect", pa.bool_()),
    ("token_id_0", pa.string()), ("token_id_1", pa.string()), ("outcomes_json", pa.string()),
    ("closed", pa.bool_()), ("resolved_outcome", pa.float64()),
    ("start_date", pa.string()), ("end_date", pa.string()), ("snapshot_at", pa.int64()),
])


def parse_market(m: dict) -> dict | None:
    """Gamma 市场对象 → 注册表行。非二元 / 缺 token 返回 None。"""
    try:
        toks = [str(x) for x in json.loads(m.get("clobTokenIds") or "[]")]
        outs = json.loads(m.get("outcomes") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if len(toks) != 2 or len(outs) != 2:
        return None
    market_class, suspect = classify_market(m)
    resolved = None
    if m.get("closed") and m.get("outcomePrices"):
        try:
            op = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
            v = float(op[0])
            resolved = v if v in (0.0, 1.0) else None
        except (json.JSONDecodeError, ValueError, TypeError, IndexError):
            resolved = None
    return {
        "condition_id": m.get("conditionId"), "slug": m.get("slug"),
        "title": (m.get("question") or "")[:300], "event_slug": m.get("eventSlug") or m.get("slug"),
        "market_class": market_class, "hft_suspect": suspect,
        "token_id_0": toks[0], "token_id_1": toks[1],
        "outcomes_json": json.dumps(outs, ensure_ascii=False),
        "closed": bool(m.get("closed")), "resolved_outcome": resolved,
        "start_date": m.get("startDate"), "end_date": m.get("endDate"),
    }


def load_registry() -> dict[str, dict]:
    """读注册表所有 Parquet,按 condition_id 取 snapshot_at 最新一版。空注册表返回 {}。"""
    if not any(REGISTRY_DIR.glob("*.parquet")):
        return {}
    tbl = pq.read_table(REGISTRY_DIR)
    reg: dict[str, dict] = {}
    for r in tbl.to_pylist():
        cid = r["condition_id"]
        if cid not in reg or r["snapshot_at"] > reg[cid]["snapshot_at"]:
            reg[cid] = r
    return reg


def _lookup_gamma(slug: str) -> dict | None:
    for suf in ("", "&closed=true"):
        m = _get(f"{GAMMA}?slug={slug}{suf}")
        if isinstance(m, list) and m:
            return m[0]
    return None


def register_new_markets(stubs: dict[str, dict], max_new: int | None = None) -> tuple[int, int]:
    """对新市场按需查 Gamma 注册。返回 (注册成功数, 查询失败数)。失败计数不静默丢。"""
    now = int(dt.datetime.now(dt.UTC).timestamp())
    rows, fail = [], 0
    for i, (cid, stub) in enumerate(stubs.items()):
        if max_new and i >= max_new:
            break
        slug = stub.get("slug")
        m = _lookup_gamma(slug) if slug else None
        if not m:
            fail += 1
            continue
        row = parse_market(m)
        if not row or not row["condition_id"]:
            fail += 1
            continue
        row["snapshot_at"] = now
        rows.append(row)
        time.sleep(0.15)
    if rows:
        dest = REGISTRY_DIR / f"{uuid.uuid4().hex}.parquet"
        _atomic_write_parquet(pa.Table.from_pylist(rows, schema=MARKETS_SCHEMA), dest)
    return len(rows), fail


def refresh_and_registry(sample_limit: int = 5000, max_new: int | None = None) -> tuple[list[dict], dict]:
    """发现 → 注册新市场 → 返回 (可轮询市场行列表, 计数)。

    可轮询 = 当前活跃(firehose 出现)且已注册、market_class=event、未关闭。
    """
    trades = sample_firehose(sample_limit)
    active = extract_active(trades)
    registry = load_registry()
    new = {cid: s for cid, s in active.items() if cid not in registry}
    n_reg, n_fail = register_new_markets(new, max_new=max_new)
    if n_reg:
        registry = load_registry()
    pollable = [registry[cid] for cid in active
                if cid in registry and not registry[cid]["closed"]
                and registry[cid]["market_class"] == "event"]
    stats = {"firehose_trades": len(trades), "active_cids": len(active),
             "new_registered": n_reg, "register_fail": n_fail, "pollable": len(pollable)}
    return pollable, stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="发现一轮并注册新市场")
    ap.add_argument("--sample", type=int, default=5000, help="firehose 采样条数")
    ap.add_argument("--max-new", type=int, default=None, help="本轮最多注册 N 个新市场(试跑用)")
    args = ap.parse_args()
    if args.once:
        pollable, stats = refresh_and_registry(args.sample, max_new=args.max_new)
        print(f"发现: {stats}")
        for r in pollable[:5]:
            print(f"  - {r['title'][:50]}  class={r['market_class']}")
    else:
        ap.print_help()
