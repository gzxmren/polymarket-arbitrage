#!/usr/bin/env python3
"""一次性:把**历史上已经发生过**的 offset 截断从采集器日志导进 `truncations/`。

## 为什么需要它

留痕是 2026-08-06 才加的,而截断从 2026-07-22 就在发生 —— 实测 **377 个市场**
已经有了永久空洞(序列开头空白 p50 199 天 / p90 327 天;对照组 1 天 / 6 天)。
只给新发生的留痕 = 数据湖里"有痕迹的"和"有洞的"是两批,分析层照样会踩到旧的那批。

## 为什么日志是**权威**而不是启发式

`collector_core.poll_market` 撞顶时逐个点名打印过:

    ⚠️ offset 截断: 0x01dffa7a.. 保留近端 8123 笔,更早历史待压频回填

这是**因果现场**,不是"看起来像有洞"的猜测。
⛔ 反面做法(明确不采用):按"开头空白 > N 天"去反推哪些市场被截断了 ——
那是拿一个与结果相关的量贴标签,会把"真的开盘很久没人交易"的市场一并冤枉进来。

## 幂等

按 `(condition_id, detected_at)` 去重;重复跑不会写第二遍。

判据:10-tests/unit/test_truncation_log_import.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

import storage_engine as se

LOG = se.DATA_ROOT / "collector.log"
CYCLE_RE = re.compile(r"=== 采集周期 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})Z ===")
# 旧格式(无 mode)与新格式(有 [cold]/[warm])都要认 —— 导入脚本自己不能是个新的盲区
HIT_RE = re.compile(r"offset 截断(?:\[(cold|warm)\])?: (0x[0-9a-f]+)\.\. 保留近端 (\d+) 笔")


def parse_log(path: Path) -> list[dict]:
    """扫日志,返回 (cid前缀, 发生时刻, 保留笔数, mode)。mode 缺失记 'unknown'。

    ⚠️ 旧日志行没有 mode —— 不许瞎猜成 'cold'。猜出来的标签会被后人当实测用。
    """
    out, ts = [], None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = CYCLE_RE.search(line)
            if m:
                ts = int(dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                         .replace(tzinfo=dt.UTC).timestamp())
                continue
            m = HIT_RE.search(line)
            if m and ts:
                out.append({"prefix": m.group(2), "detected_at": ts,
                            "kept_rows": int(m.group(3)),
                            "mode": m.group(1) or "unknown"})
    return out


def resolve(hits: list[dict], con) -> tuple[list[dict], list[dict]]:
    """日志里只有 cid 前 14 位 → 去数据湖补全整条 cid,并取"保留到的最旧一笔"。

    返回 (补全的, **没对上的**)。没对上的要出声:静默丢弃就是又造一个盲区。
    """
    if not hits:
        return [], []
    con.execute("create temp table _pfx(p varchar)")
    con.executemany("insert into _pfx values (?)", [(h["prefix"],) for h in set_of(hits)])
    rows = con.sql("""
        select p, min(t.condition_id), min(t.timestamp)
        from _pfx join trades t on t.condition_id like p || '%'
        group by p
    """).fetchall()
    found = {p: (cid, oldest) for p, cid, oldest in rows}
    ok, missed = [], []
    for h in hits:
        hit = found.get(h["prefix"])
        if not hit:
            missed.append(h)
            continue
        cid, oldest = hit
        ok.append({"condition_id": cid, "detected_at": h["detected_at"],
                   "mode": h["mode"],
                   # ⛔ 日志里**没有**当时的 offset ⇒ 留 null,不许拿 OFFSET_WARN 之类"填一个"。
                   # 编出来的数会被后人当实测用,而它长得和真的一模一样。
                   "offset_reached": None,
                   "kept_rows": h["kept_rows"],
                   # 空洞的新端 = 该市场目前最旧的一笔(截断当时保留到哪儿,今天仍是它)
                   "oldest_kept_ts": int(oldest), "watermark_before": None})
    return ok, missed


def set_of(hits: list[dict]) -> list[dict]:
    seen, out = set(), []
    for h in hits:
        if h["prefix"] not in seen:
            seen.add(h["prefix"])
            out.append(h)
    return out


def existing_keys(con) -> set[tuple[str, int]]:
    """已导入过的 (cid, detected_at) —— 幂等靠它,不靠"跑没跑过"的人肉记忆。"""
    if not any(se.TRUNCATIONS_DIR.glob("*.parquet")):
        return set()
    rows = con.sql(
        f"select condition_id, detected_at from read_parquet('{se.TRUNCATIONS_DIR}/*.parquet')"
    ).fetchall()
    return {(c, int(t)) for c, t in rows}


def run(log: Path = LOG, dry_run: bool = False) -> dict:
    hits = parse_log(log)
    con = se.duckdb_conn()
    try:
        ok, missed = resolve(hits, con)
        have = existing_keys(con)
    finally:
        con.close()
    fresh = [r for r in ok if (r["condition_id"], r["detected_at"]) not in have]
    stats = {"日志命中": len(hits), "去重后市场": len(set_of(hits)),
             "补全成功": len(ok), "没对上": len(missed),
             "已存在": len(ok) - len(fresh), "本次写入": 0 if dry_run else len(fresh),
             "mode未知": sum(1 for r in fresh if r["mode"] == "unknown")}
    if not dry_run and fresh:
        se.write_truncations(fresh)
    if missed:
        # 出声:没对上的多半是"截断了但那批数据后来没进湖",本身就值得看一眼
        print(f"⚠️ 有 {len(missed)} 条日志记录在数据湖里找不到对应市场,未导入:"
              f"{[m['prefix'] for m in missed[:5]]}…", flush=True)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="把历史 offset 截断从日志导入 truncations/(幂等)")
    ap.add_argument("--log", type=Path, default=LOG)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(run(a.log, a.dry_run))
