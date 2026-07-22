#!/usr/bin/env python3
"""universe_fetcher.py — 事件市场宇宙枚举 + HFT 分类 + token/结算解析。

兑现《数据契约 v1.1》§2.2 / §4.2 / §7:
- 每日独立 Gamma 全量枚举(不依赖成交活跃度)→ 发现零成交新市场,补 §7 宇宙覆盖闭环。
- market_class 分类器(先宽后严):确定 hft_crypto / 疑似 hft_suspect / 其余 event。
- 解析每个市场的 clobTokenIds / outcomes / 官方结算,产出采集器要用的 token 映射与 resolved_outcome。
- 落"每日点位快照"markets/dt=YYYY-MM-DD/markets.parquet —— 天然的点位时刻宇宙注册表。

铁律(实测,契约 §4.1):asset→阵营用 **asset 在 clobTokenIds 的位置**定下标,
弃用接口 outcomeIndex(93% 是垃圾 999)。此模块导出 resolve_asset_index 供采集器调用。
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
UA = {"User-Agent": "polymarket-rebirth-universe/1.1"}
MARKETS_DIR = DATA_ROOT / "markets"

# --- HFT 分类器正则(契约 §2.2,先宽后严)---
_HFT_CORE = re.compile(r"updown|up-or-down", re.I)
_HFT_WINDOW = re.compile(r"-5m-|5-?min|\b\d{1,2}:\d{2}\s?(?:AM|PM)\b", re.I)
_HFT_SUSPECT = re.compile(r"-1h-|hourly|-15m-|-30m-", re.I)


def classify_market(m: dict) -> tuple[str, bool]:
    """返回 (market_class, hft_suspect)。
    hft_crypto = 确定排除(updown 且带 5 分钟/时段窗);其余 event。
    hft_suspect = 疑似短周期(入库但分析默认排除)。
    """
    s = f"{m.get('slug','')} {m.get('question','')}"
    if _HFT_CORE.search(s) and _HFT_WINDOW.search(s):
        return "hft_crypto", True
    return "event", bool(_HFT_SUSPECT.search(s))


def resolve_asset_index(asset: str, clob_token_ids: list[str]) -> int | None:
    """🔴 铁律:asset 在 clobTokenIds 的位置 = outcome 下标。定位失败返回 None(调用方须拒绝入库+计数)。"""
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


def parse_market(m: dict) -> dict | None:
    """把 Gamma 市场对象解析成注册表行。非二元 / 缺 token 的返回 None。"""
    try:
        toks = [str(x) for x in json.loads(m.get("clobTokenIds") or "[]")]
        outs = json.loads(m.get("outcomes") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if len(toks) != 2 or len(outs) != 2:
        return None  # 仅二元(多候选事件本就是二元集合,逐腿在别处采)
    market_class, suspect = classify_market(m)
    # 官方结算(干净 0/1);未结算为空
    resolved = None
    if m.get("closed") and m.get("outcomePrices"):
        try:
            op = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
            v = float(op[0])           # 下标 0 侧的结算值
            resolved = v if v in (0.0, 1.0) else None
        except (json.JSONDecodeError, ValueError, TypeError, IndexError):
            resolved = None
    return {
        "condition_id": m.get("conditionId"),
        "slug": m.get("slug"),
        "title": (m.get("question") or "")[:300],
        "event_slug": m.get("eventSlug") or m.get("slug"),
        "market_class": market_class,
        "hft_suspect": suspect,
        "token_id_0": toks[0],
        "token_id_1": toks[1],
        "outcomes_json": json.dumps(outs, ensure_ascii=False),
        "closed": bool(m.get("closed")),
        "resolved_outcome": resolved,     # 下标0侧 0/1;未结算为空。写入后不可改(§4.2)
        "start_date": m.get("startDate"),
        "end_date": m.get("endDate"),
    }


def fetch_all_markets(closed: bool = False, max_pages: int = 400) -> list[dict]:
    """Gamma 分页全量枚举。失败重试+计数,不静默当'无新市场'(§7)。"""
    out, page_fail = [], 0
    for pg in range(max_pages):
        d = _get(f"{GAMMA}?closed={'true' if closed else 'false'}&limit=100&offset={pg*100}")
        if not isinstance(d, list):
            page_fail += 1
            if page_fail >= 3:
                raise RuntimeError(f"Gamma 枚举连续失败,中止(已抓 {len(out)});不得静默视为无新市场")
            time.sleep(3)
            continue
        page_fail = 0
        if not d:
            break
        out.extend(d)
        if len(d) < 100:
            break
        time.sleep(0.2)
    return out


MARKETS_SCHEMA = pa.schema([
    ("condition_id", pa.string()), ("slug", pa.string()), ("title", pa.string()),
    ("event_slug", pa.string()), ("market_class", pa.string()), ("hft_suspect", pa.bool_()),
    ("token_id_0", pa.string()), ("token_id_1", pa.string()), ("outcomes_json", pa.string()),
    ("closed", pa.bool_()), ("resolved_outcome", pa.float64()),
    ("start_date", pa.string()), ("end_date", pa.string()), ("snapshot_at", pa.int64()),
])


def build_universe(include_closed: bool = False) -> Path:
    """枚举 → 解析 → 落当日点位快照 markets/dt=YYYY-MM-DD/markets.parquet。返回快照路径。"""
    raw = fetch_all_markets(closed=False)
    if include_closed:
        raw += fetch_all_markets(closed=True)
    now = int(dt.datetime.now(dt.UTC).timestamp())
    rows, n_event, n_hft = [], 0, 0
    for m in raw:
        r = parse_market(m)
        if not r or not r["condition_id"]:
            continue
        r["snapshot_at"] = now
        rows.append(r)
        n_event += (r["market_class"] == "event")
        n_hft += (r["market_class"] == "hft_crypto")
    day = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
    dest = MARKETS_DIR / f"dt={day}" / "markets.parquet"
    _atomic_write_parquet(pa.Table.from_pylist(rows, schema=MARKETS_SCHEMA), dest)
    print(f"宇宙快照: {len(rows)} 二元市场(event {n_event} / hft {n_hft})→ {dest}")
    return dest


def active_event_markets(snapshot_path: Path | None = None) -> list[dict]:
    """从最新快照取"该采的市场"= 未关闭 且 market_class!=hft_crypto。供采集器轮询。"""
    if snapshot_path is None:
        snaps = sorted(MARKETS_DIR.glob("dt=*/markets.parquet"))
        if not snaps:
            return []
        snapshot_path = snaps[-1]
    tbl = pq.read_table(snapshot_path)
    rows = tbl.to_pylist()
    return [r for r in rows if not r["closed"] and r["market_class"] != "hft_crypto"]


# TODO(§7 闭环,ops):与已知市场表 LEFT JOIN 找新增缺口 → 告警 + 入采集队列。
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="枚举一次并落快照")
    ap.add_argument("--include-closed", action="store_true", help="连已关闭市场一并枚举(回填用)")
    args = ap.parse_args()
    if args.once:
        p = build_universe(include_closed=args.include_closed)
        print(f"可采事件市场数: {len(active_event_markets(p))}")
    else:
        ap.print_help()
