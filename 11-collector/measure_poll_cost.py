#!/usr/bin/env python3
"""measure_poll_cost.py — 单市场轮询耗时的**分层**实测(2026-08-06)。

预登记单:`docs/PREREG_POLL_QUOTA_2026-08-06.md`(红线与判决表在那里,**先写后测**)。

## 干什么

回答一个问题:**一轮 170 秒的时间闸内,实际能轮询多少个市场?**
`DEFAULT_POLL_LIMIT = 50` 来自冷启动年代的一句注释(「~11s/个」),从没复核过。

## 为什么必须分层

耗时的主因是"要翻几页",而它由**有没有 watermark** 决定:

- **A 增量层**(采过):翻到追上上次即停 —— 通常 1 页
- **B 冷启动层**(一笔没采过):从 offset=0 全量回填 —— 可能翻到 offset 上限

混成一个均值会掩盖真相:轮转片里冷盘扎堆,新鲜片几乎全是增量层。

## 三条抽样纪律(⭐都是防"丢得与结果相关")

1. **两层交替抽**(A、B、A、B…):预算用尽时砍掉的是**两层的尾巴**,
   而不是"便宜的全测完、贵的只测了几个"—— 后者会把成本系统性低估。
2. **慢样本/失败样本照记不弃**,按实际耗时计入并单独计数。
   剔掉慢的 = 用与结果相关的变量筛样本(2026-07-15 栽过的那个坑)。
3. **只读**:调 `poll_market` 取行后**丢弃**,不写数据湖、不动游标/水位线。

## 给采集器让路

两者抢同一条代理隧道(08-04 正是被隧道拖垮杀了 12 轮)。开工前判 + 过程中每 10 个再判,
理由与 `backfill_closed_markets` 完全相同 —— 判活用 systemd 而非 pgrep(自匹配坑)。
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time

import collector_core as cc
import discovery_service as ds
import storage_engine as se
from backfill_closed_markets import collector_is_running

YIELD_CHECK_EVERY = 10
DEFAULT_BUDGET_S = 240.0
DEFAULT_PER_STRATUM = 40
FIREHOSE_SAMPLE = 3000     # 只为拿到"活跃集"这个抽样框,不必翻满


def build_frame(sample_limit: int = FIREHOSE_SAMPLE) -> tuple[list[dict], list[dict], dict]:
    """抽样框 = firehose 活跃集 ∩ 注册表 ∩ 未关闭 ∩ event,再按有无 watermark 分两层。

    ⭐用活跃集而不是注册表全集:后者混入早已停摆的盘,量出来的冷启动成本
    不代表**真实名额消耗**(那是在给一个不存在的分布定参数)。
    """
    net = ds.new_net_stats()
    trades = ds.sample_firehose(limit=sample_limit, net=net)
    active = {t.get("conditionId") for t in trades if t.get("conditionId")}
    registry = ds.load_registry()
    pollable = [registry[c] for c in active
                if c in registry and not registry[c]["closed"]
                and registry[c]["market_class"] == "event"]
    con = se.duckdb_conn()
    try:
        wms = se.all_watermarks(con)
    finally:
        con.close()
    warm = [m for m in pollable if m["condition_id"] in wms]
    cold = [m for m in pollable if m["condition_id"] not in wms]
    meta = {"firehose_trades": len(trades), "active": len(active),
            "pollable": len(pollable), "warm": len(warm), "cold": len(cold),
            "cold_share": round(len(cold) / len(pollable), 4) if pollable else None}
    return warm, cold, {"frame": meta, "wms": wms}


def interleave(warm: list[dict], cold: list[dict], per_stratum: int,
               seed: int) -> list[tuple[str, dict]]:
    """两层各随机抽 per_stratum 个,然后**交替**排列。

    交替是本脚本最要紧的一行:预算用尽时截断落在两层同一深度上,
    截断因此与"哪一层贵"无关 —— 否则就是与结果相关的丢样本。
    """
    rng = random.Random(seed)
    a = rng.sample(warm, min(per_stratum, len(warm)))
    b = rng.sample(cold, min(per_stratum, len(cold)))
    out: list[tuple[str, dict]] = []
    for i in range(max(len(a), len(b))):
        if i < len(a):
            out.append(("warm", a[i]))
        if i < len(b):
            out.append(("cold", b[i]))
    return out


def measure(plan: list[tuple[str, dict]], wms: dict, budget_s: float) -> dict:
    """逐个计时。返回每层的耗时样本 + 出声计数(让路/超预算截断/失败)。"""
    counters = cc.new_counters()
    samples: dict[str, list[float]] = {"warm": [], "cold": []}
    rows_seen = {"warm": [], "cold": []}
    done, yielded = 0, 0
    t0 = time.monotonic()
    for i, (stratum, m) in enumerate(plan):
        if time.monotonic() - t0 >= budget_s:
            break
        if i and i % YIELD_CHECK_EVERY == 0 and collector_is_running():
            yielded = 1
            break
        t = time.monotonic()
        rows = cc.poll_market(m, counters, wm=wms.get(m["condition_id"]))
        dt_s = time.monotonic() - t
        samples[stratum].append(dt_s)          # ⭐慢的/空的照记,不弃
        rows_seen[stratum].append(len(rows))
        done += 1
    return {"samples": samples, "rows": rows_seen, "measured": done,
            "planned": len(plan), "cut": len(plan) - done, "yielded": yielded,
            "elapsed_s": round(time.monotonic() - t0, 1), "net": counters}


def _q(v: list[float], p: float) -> float | None:
    if not v:
        return None
    s = sorted(v)
    return round(s[min(len(s) - 1, int(len(s) * p))], 2)


def summarize(res: dict, meta: dict) -> dict:
    out = {"frame": meta["frame"], "measured": res["measured"], "planned": res["planned"],
           "cut": res["cut"], "yielded": res["yielded"], "elapsed_s": res["elapsed_s"]}
    for k, v in res["samples"].items():
        out[k] = {"n": len(v), "p50": _q(v, .5), "p90": _q(v, .9), "max": _q(v, 1.0),
                  "rows_p50": _q([float(x) for x in res["rows"][k]], .5),
                  "rows_max": _q([float(x) for x in res["rows"][k]], 1.0)}
    n = res["net"]
    out["net"] = {k: n[k] for k in ("net_attempt_count", "net_retry_count",
                                    "net_give_up_count", "rate_limit_hits",
                                    "offset_overflow_count", "poll_truncated_count")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="单市场轮询耗时分层实测(只读,不写数据湖)")
    ap.add_argument("--per-stratum", type=int, default=DEFAULT_PER_STRATUM)
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_S)
    ap.add_argument("--seed", type=int, default=20260806)
    ap.add_argument("--out", help="把结果 JSON 写到这里(默认只打印)")
    a = ap.parse_args()

    if collector_is_running():
        print("采集器正在跑 —— 让路,本次不测(两者抢同一条代理隧道)", flush=True)
        return 2
    warm, cold, meta = build_frame()
    print(f"抽样框: {meta['frame']}", flush=True)
    plan = interleave(warm, cold, a.per_stratum, a.seed)
    res = measure(plan, meta["wms"], a.budget)
    summary = summarize(res, meta)
    summary["seed"] = a.seed
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
