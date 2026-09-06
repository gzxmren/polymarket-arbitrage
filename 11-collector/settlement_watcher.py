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
import datetime as dt
import time
import uuid

import cycle_state
import rotation

from discovery_service import (
    GAMMA, MARKETS_SCHEMA, REGISTRY_DIR, _atomic_write_parquet, _get,
    batch_lookup_gamma, pack_condition_ids,
    load_registry, parse_market,
)
import pyarrow as pa

from storage_engine import DATA_ROOT

# 批量条数不再由本模块决定 —— 2026-08-16 起改为按 URL 字节打包
# (`discovery_service.pack_condition_ids`)。原因:写死 100 个时 URL 长 8150 字节,
# 而服务端硬限 8192(实测 110 个 = 8960 → HTTP 422)⇒ **余量只有 42 字节**,
# 谁再加一个查询参数就整条链路 422,且 422 长得像"这批市场全查不到"。
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


def select_batch(pending: list[dict], cursor: str, max_check: int) -> rotation.Rotation:
    """按游标取下一片。返回**轮转对象**(切片在 `.batch`,游标由 `.commit()` 给)。

    走到末尾自动回卷到开头继续下一圈 —— 未结算的市场可能后来才结算,须持续复查。
    游标存的是**排序键**而非下标,故 pending 增长(新市场注册)不会让轮转倒退重查。

    ⭐2026-08-07 收进公用的 `rotation.py`:这段骨架原本在本项目里有三份,
    同一个"游标走过没做过的"bug 被写了三遍。见 rotation 模块头。
    """
    return rotation.Rotation(pending, cursor, max_check, key=_sort_key)


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

def _batch_lookup_gamma(cids: list[str], net: dict | None = None,
                        deadline: float | None = None) -> dict[str, dict]:
    """薄适配层:走公用的 `batch_lookup_gamma`,只保留本模块历史上的返回形状。

    ⚠️ **不是第二份实现** —— 打包/两遍查/C4 语义全在公用那份里,这里只丢掉
    `inconclusive` 那一半。丢它是有意的:本模块的 `lookup_fail` 历史上把
    「确认查不到」和「没问到」算作同一类,而那个计数是真值断供守护的分母。
    改这个语义会同时动到守护的判定,是**另一个自变量**,该单独一步单独验证。

    保留这个名字还有一个作用:结算侧十几条既有判据(轮转/时间闸/计数)
    都钉在它上面,它们是这次改动的**回归基线**,不该因为换了个调用路径而失效。
    """
    found, _inconclusive = batch_lookup_gamma(cids, net=net, deadline=deadline)
    return found


def watch_settlements(max_check: int | None = DEFAULT_MAX_CHECK,
                      net: dict | None = None,
                      time_budget_s: float | None = None) -> dict:
    """扫注册表里 resolved_outcome 仍为空、且 end_date 已过的市场,重查 Gamma;
    新拿到干净 0/1 的,append 新注册表行。返回计数(不静默:失败计数)。

    `time_budget_s` 是本段的时间闸(默认 None = 关闭,老行为)。
    个数配额(`max_check`)挡不住延迟退化:同样 800 个,往返从 1.1s 涨到 10s
    就从 18s 变成 160s。实测该段 p50=25s 而 max=441s,差 17.6 倍 —— 那一轮把
    整轮推到 438s,离慢周期告警线只剩 ~100s。闸值推导见 test_settlement_time_gate.py。

    ⭐超预算时**游标只走过真的查过的那些市场**。照旧写 `picked[-1]` 的话,
    被砍的尾巴每圈都被跳过,而跳掉的恰是"延迟退化时排在后面"的那批 —— 丢得与结果相关。
    这条规则 2026-08-07 起由 `rotation.Rotation` 统一保证(原本三处各写一份、只对了一份)。
    """
    reg = load_registry()
    pending = [r for r in reg.values()
               if r["resolved_outcome"] is None and _end_passed(r["end_date"])]
    rot = select_batch(pending, _load_cursor(), max_check or DEFAULT_MAX_CHECK)
    picked = rot.batch

    t0 = time.monotonic()
    deadline = None if time_budget_s is None else t0 + time_budget_s
    now = int(dt.datetime.now(dt.UTC).timestamp())
    rows, fail, checked = [], 0, 0
    by_cid = {r["condition_id"]: r for r in picked}   # 注册表按 cid 唯一,顺序=轮转顺序
    for chunk_cids in pack_condition_ids(list(by_cid)):
        # 钟看在**发起这一批之前**:查完再看必然超出一整批的耗时。
        if deadline is not None and time.monotonic() >= deadline:
            break
        chunk = [by_cid[c] for c in chunk_cids]
        found = _batch_lookup_gamma(chunk_cids, net=net, deadline=deadline)
        for r in chunk:
            m = found.get(r["condition_id"])
            if not m:
                fail += 1
                continue
            parsed = parse_market(m)
            if parsed and parsed["resolved_outcome"] is not None:
                parsed["snapshot_at"] = now
                rows.append(parsed)
        checked += len(chunk)
        for r in chunk:               # 报「这些我真查过了」—— 不报就推不动游标
            rot.done(r)
    if rows:
        dest = REGISTRY_DIR / f"settle-{uuid.uuid4().hex}.parquet"
        _atomic_write_parquet(pa.Table.from_pylist(rows, schema=MARKETS_SCHEMA), dest)
    new_cursor = rot.commit()
    if new_cursor is not None:        # 一个都没查成 → 游标原地不动,整批留给下轮
        _save_cursor(new_cursor)
    # 分母用**实际查过**的个数:被闸砍光 = "没能问",不是"问了没有",
    # 两者处置不同(前者查网络/闸值,后者查真值链路),混在一起会让守护在网络退化时误报。
    streak = next_zero_streak(_load_streak(), len(rows), checked)
    _save_streak(streak)
    return {"pending_settlement": len(pending), "newly_resolved": len(rows),
            "lookup_fail": fail, "checked": checked, "zero_streak": streak,
            "timegate_skipped": len(picked) - checked,
            # 稳态恒 0(整批 chunk 一起报,不可能跳着报)。非 0 = 有人加了新的跳过路径,
            # 或某批每轮都查不成把游标卡住。消费者:run_cycle → rotation_hole_streak → alerts
            "rotation_holes": rot.holes}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-check", type=int, default=DEFAULT_MAX_CHECK)
    args = ap.parse_args()
    print(watch_settlements(args.max_check))
