#!/usr/bin/env python3
"""settlement_watcher.py — 结算守望:抓已关闭未结算市场的官方结算真值。

兑现《数据契约 v1.2》§4.2(已关闭未结算须持续重访抓结算窗口)。

为什么需要它:已关闭市场**停止成交 → 不再出现在 Firehose**,故发现层永远不会重新轮询它。
但它的**官方结算(0/1)= 我们要的地面真值**,只能靠注册表驱动的独立守望去 Gamma 重查。

只增不改:结算写入 = append 一条新的注册表行(resolved_outcome 已填);load_registry 取最新版。

--- 2026-08-03 大修(此前连续 11 天产出 0 条真值,pending 涨到 37,782)---
根因三层,判据见 10-tests/unit/test_settlement_rotation.py:
1. 队头阻塞:旧排序 `r["end_date"] or ""` 把 610 个 end_date=None 顶到队头(空串最小),
   每轮 80 个名额永远耗在同一批上;而 None 恰恰最不可能结算(实测样本是 2027 年的盘)。
2. 无轮转:即使排序修好,队头仍会被"到期但永不关闭"的市场长期占据(实测队首 end_date
   2025-10-31 而 closed=False)。故改为**游标轮转**,保证任何市场都不会被饿死。
3. ⭐Gamma `condition_ids=` 批量查询**默认只返回未关闭市场**:实测请求 100 个只回 72,
   缺的 28 个抽样全部是 closed=True 且已结算 —— 静默丢掉的恰是唯一想要的样本(CLAUDE.md
   铁律 §2:绝不用与结果相关的变量筛样本)。故**必须两遍**(默认 + &closed=true)取并集,
   实测覆盖 100/100、零重叠。批量化顺带把吞吐从 80/轮 提到 800/轮。
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import uuid

import cycle_state

from discovery_service import (
    GAMMA, MARKETS_SCHEMA, REGISTRY_DIR, _atomic_write_parquet, _get,
    load_registry, parse_market,
)
import pyarrow as pa

from storage_engine import DATA_ROOT

# Gamma condition_ids 每批条数(实测 100 稳定:两遍并集 100/100,单遍 ~1.1s)
SETTLEMENT_BATCH = 100
# 每轮检查市场数(8 批 × 2 遍 ≈ 18s,10 分钟周期内绰绰有余)。
# 37,782 存量按此速度 ~4.7 轮/圈… 实为 ~47 轮 ≈ 5 小时轮完一圈,可接受;
# 存量另有 backfill_settlements.py 一次性扫完。
DEFAULT_MAX_CHECK = 800

CURSOR_FILE = DATA_ROOT / "state" / "settlement_cursor.json"
# 真值断供守护:连续多少轮"有市场可查却一个都没结算"。每轮是独立进程,故须落盘累积。
STREAK_FILE = DATA_ROOT / "state" / "truth_supply_streak.json"


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


# ---------- 轮转(保证无饿死) ----------

def _sort_key(r: dict) -> str:
    """排序/游标键。end_date 缺失 → 前缀 '1' 排到**最后**(到期时间未知 = 最低优先级)。

    旧代码用 `end_date or ""` 让 None 排最前,是 11 天 0 产出的直接根因。
    键做成字符串是为了能原样存进游标 JSON,并用 bisect 在有序列表上定位。
    """
    ed = r.get("end_date")
    return f"{'1' if not ed else '0'}|{ed or ''}|{r.get('condition_id') or ''}"


def select_batch(pending: list[dict], cursor: str, max_check: int) -> tuple[list[dict], str]:
    """按游标取下一片(pending 须已按 _sort_key 排序)。返回 (本轮切片, 新游标)。

    走到末尾自动回卷到开头继续下一圈 —— 未结算的市场可能后来才结算,须持续复查。
    游标存的是**排序键**而非下标,故 pending 增长(新市场注册)不会让轮转倒退重查。
    """
    if not pending:
        return [], cursor
    n = min(max_check, len(pending))
    keys = [_sort_key(r) for r in pending]
    i = bisect.bisect_right(keys, cursor)
    picked = pending[i:i + n]
    if len(picked) < n:  # 回卷
        picked = picked + pending[:n - len(picked)]
    return picked, _sort_key(picked[-1])


def _load_cursor() -> str:
    return cycle_state.read_state(CURSOR_FILE, "cursor", "")


def _save_cursor(cursor: str) -> None:
    cycle_state.write_state(CURSOR_FILE, "cursor", cursor, "结算游标")


# ---------- 真值断供守护(底座在 cycle_state,两处守护共用一套实现) ----------

def next_zero_streak(prev: int, newly_resolved: int, checked: int) -> int:
    """连零计数推进规则。判据见 test_truth_supply_guard.py。

    - 有市场可查却一个都没结算 → 进位(正是 2026-08-03 那次静默故障的形态)
    - 拿到任何真值 → 归零(链路通)
    - 没市场可查(pending 空)→ 保持不变:那是成功不是失败,既不误报也不掩盖
    """
    return cycle_state.next_zero_streak(prev, newly=newly_resolved, attempted=checked)


def _load_streak() -> int:
    return cycle_state.read_streak(STREAK_FILE)


def _save_streak(n: int) -> None:
    cycle_state.write_streak(STREAK_FILE, n)


# ---------- 批量查询(必须两遍) ----------

def _batch_lookup_gamma(cids: list[str]) -> dict[str, dict]:
    """批量查 Gamma,返回 {condition_id: market}。查不到的**不出现在返回里**(调用方计数)。

    🔴 必须两遍:`condition_ids=` 默认只返回未关闭市场,已结算的只有加 &closed=true 才拿得到。
    只查一遍 = 静默丢掉全部已结算样本 = 与结果相关的丢样本(实测 100 丢 28,全是已结算)。
    """
    out: dict[str, dict] = {}
    if not cids:
        return out
    q = "&".join(f"condition_ids={c}" for c in cids)
    for suf in ("", "&closed=true"):
        d = _get(f"{GAMMA}?{q}{suf}&limit=500")
        if not isinstance(d, list):
            continue  # 该遍失败(_get 返回 {"__http__": ...});缺的市场由调用方计入 lookup_fail
        for m in d:
            cid = m.get("conditionId")
            if cid and cid not in out:
                out[cid] = m
    return out


def watch_settlements(max_check: int | None = DEFAULT_MAX_CHECK) -> dict:
    """扫注册表里 resolved_outcome 仍为空、且 end_date 已过的市场,重查 Gamma;
    新拿到干净 0/1 的,append 新注册表行。返回计数(不静默:失败计数)。"""
    reg = load_registry()
    pending = [r for r in reg.values()
               if r["resolved_outcome"] is None and _end_passed(r["end_date"])]
    pending.sort(key=_sort_key)
    picked, new_cursor = select_batch(pending, _load_cursor(), max_check or DEFAULT_MAX_CHECK)

    now = int(dt.datetime.now(dt.UTC).timestamp())
    rows, fail = [], 0
    for i in range(0, len(picked), SETTLEMENT_BATCH):
        chunk = picked[i:i + SETTLEMENT_BATCH]
        found = _batch_lookup_gamma([r["condition_id"] for r in chunk])
        for r in chunk:
            m = found.get(r["condition_id"])
            if not m:
                fail += 1
                continue
            parsed = parse_market(m)
            if parsed and parsed["resolved_outcome"] is not None:
                parsed["snapshot_at"] = now
                rows.append(parsed)
    if rows:
        dest = REGISTRY_DIR / f"settle-{uuid.uuid4().hex}.parquet"
        _atomic_write_parquet(pa.Table.from_pylist(rows, schema=MARKETS_SCHEMA), dest)
    _save_cursor(new_cursor)
    streak = next_zero_streak(_load_streak(), len(rows), len(picked))
    _save_streak(streak)
    return {"pending_settlement": len(pending), "newly_resolved": len(rows),
            "lookup_fail": fail, "checked": len(picked), "zero_streak": streak}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-check", type=int, default=DEFAULT_MAX_CHECK)
    args = ap.parse_args()
    print(watch_settlements(args.max_check))
