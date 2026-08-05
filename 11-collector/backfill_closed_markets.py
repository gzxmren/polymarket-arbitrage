#!/usr/bin/env python3
"""已关闭市场的成交回填清扫(2026-08-06)。

## 病

轮询层每轮只采「最近成交的前 N 个」,冷门盘排在后面;等它 `closed=True` 就被
`pollable` 过滤掉 ⇒ **活体链路永远不会再采它**。实测存量 **23,675 个**已关闭市场
一笔成交都没采到,占注册表的一大块。丢得**与结果相关**(专丢低频盘)。

## 为什么是"常设清扫"而不是"一次性脚本"

实测每天新关闭且从没采过的有 **1,873~3,273 个**,而游标轮转吞吐上限 2,304/天
—— 基本持平,08-05 那天在漏。名额受轮询时间闸(170s)顶死,活体链路结构上覆盖不过来。

故目标集做成**声明式**的:「已关闭 ∧ 成交湖里没有」。
于是同一个模块同时吃掉存量和每天的新增,补完的自动从目标集消失(天然幂等),
不需要"一次性回填"和"每日清扫"两套东西。

## 可行性(实测,预登记单先写:docs/PREREG_CLOSED_MARKET_BACKFILL_2026-08-06.md)

分层随机抽 48 个走真实 `poll_market` 路径:**四层全部 100% 有返回**,
报错/4xx/重试耗尽/溢出合计 0,**无年龄相关缺失**;
第二层对账 444 笔 → 444 笔(解析拒绝 0、去重塌缩 0)。单市场 p50=1.5s。

## 三条硬约束

1. **给采集器让路**。两者抢同一条代理隧道,而 08-04 正是被隧道拖垮杀了 12 轮。
   回填**可以等**(数据已经在那儿,晚几小时无所谓),采集器不能等。
   故开工前判 + 每 N 个市场再判一次(只在开头判 = 没判,一轮要跑好几分钟)。
2. **两道闸**:时间闸(挡延迟退化)+ 个数闸(挡网络特别快时打爆接口)。
3. **只追加**:走 `se.write_trades`(append-only + uuid 文件名,与采集器并发写安全),
   不碰注册表、不碰采集器的任何状态文件。

判据:10-tests/unit/test_backfill_closed.py
"""
from __future__ import annotations

import argparse
import bisect
import subprocess
import time

import collector_core as cc
import cycle_state
import storage_engine as se

CURSOR_FILE = se.DATA_ROOT / "state" / "backfill_closed_cursor.json"
STREAK_FILE = se.DATA_ROOT / "state" / "backfill_closed_streak.json"
COLLECTOR_UNIT = "polymarket-rebirth-collector.service"

# 默认值:采集器在 :00/:15/:30/:45 跑约 345s,故清扫排在 :07/:22/:37/:52,
# 预算 300s → 在下一轮采集器起来(还有 ~8 分钟)之前收工,且中途它若提前起来会让路。
DEFAULT_TIME_BUDGET_S = 300
DEFAULT_MAX_MARKETS = 400        # 实测 p50=1.5s/个 → 300s 大约做得完 200 个;留余量
YIELD_CHECK_EVERY = 25           # 每 N 个市场回头看一眼采集器起来没有

# ⭐目标集**只能**是「已关闭 ∧ 成交湖里没有」。
# 不许加"只补最近 N 天""只补成交量大的"之类 —— 那是**用与结果相关的变量筛样本**
# (CLAUDE.md 铁律 2):补回来的会系统性偏向某一类,基于它的结论结构性作废。
# 判据 test_target_sql_selects_only_closed_and_never_polled 焊死这一点。
TARGET_SQL = """
with reg1 as (
  select * from (
    select condition_id, token_id_0, token_id_1, closed,
           row_number() over (partition by condition_id order by snapshot_at desc) rn
    from read_parquet('{registry}/*.parquet')
  ) where rn = 1
),
traded as (
  select distinct condition_id from read_parquet('{raw}/**/*.parquet', union_by_name=true)
)
select r.condition_id, r.token_id_0, r.token_id_1
from reg1 r left join traded t using (condition_id)
where r.closed = true and t.condition_id is null
"""


def load_targets() -> list[dict]:
    """目标集:已关闭且成交湖里一笔都没有的市场。空湖时退化为"全部已关闭"。"""
    con = se.duckdb_conn()
    try:
        sql = TARGET_SQL.format(registry=se.DATA_ROOT / "registry", raw=se.RAW_DIR)
        rows = con.sql(sql).fetchall()
    finally:
        con.close()
    return [{"condition_id": c, "token_id_0": t0, "token_id_1": t1} for c, t0, t1 in rows]


def select_batch(targets: list[dict], cursor: str, max_n: int) -> tuple[list[dict], str]:
    """按游标取下一片 + 回卷。返回 (本轮切片, 新游标)。

    游标存**排序键**(condition_id)而非下标:目标集每轮都在变
    —— 补完的消失、新关闭的加入 —— 存下标会乱跳。
    与 `settlement_watcher.select_batch` / `collector_core.select_poll_targets` 同一套。
    """
    if not targets:
        return [], cursor
    ordered = sorted(targets, key=lambda m: m["condition_id"])
    keys = [m["condition_id"] for m in ordered]
    n = min(max_n, len(ordered))
    i = bisect.bisect_right(keys, cursor)
    picked = ordered[i:i + n]
    if len(picked) < n:                      # 回卷:不然队尾永远补不到
        picked = picked + ordered[:n - len(picked)]
    return picked, picked[-1]["condition_id"]


# systemd 里**明确表示"没在跑"**的状态。其余一律当成在跑(含没见过的新状态)——
# 两边代价不对称:回填晚几小时毫无损失,把采集器拖垮会丢正在发生的成交流。
NOT_RUNNING_STATES = frozenset({"inactive", "failed"})


def _is_running_state(state: str) -> bool:
    """systemctl is-active 的输出 → 是否算"正在跑"。

    🔴 2026-08-06 实测踩到的坑:采集器是 `Type=oneshot`,**运行期间的状态是
    `activating` 而不是 `active`**,于是 `is-active --quiet` 返回退出码 **3**。
    原实现写的是 `returncode == 0`,结果**采集器正在跑时它返回 False** ——
    让路互锁完全失效,两个进程抢同一条代理隧道。

    ⚠️ 而当时 4 条让路判据全绿,因为它们**把 `collector_is_running` 或 `subprocess.run`
    本身 mock 掉了** —— 恰好绕开了唯一出错的那一行。
    故这里拆出纯函数,判据直接喂 systemd 真实会吐的字符串,不许再 mock 掉它。
    """
    return state.strip() not in NOT_RUNNING_STATES


def collector_is_running() -> bool:
    """采集器是否正在跑。跑着就让路 —— 两者抢同一条代理隧道。

    判活用 systemd 而不是 `pgrep -f run_cycle.py`:后者会匹配到调用方自己那条
    含该字样的命令(自匹配坑,2026-08-06 也真踩过)。
    """
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", COLLECTOR_UNIT],
                           capture_output=True, text=True, timeout=10)
        return _is_running_state(r.stdout)
    except (subprocess.SubprocessError, OSError):
        # 问不出来就**当它在跑**:回填可以等,把采集器拖垮不可以。
        return True


def next_zero_streak(prev: int, written: int, attempted: int) -> int:
    """连零守护:「有活干却一个都没补到」要进位。

    ⚠️ 必须区分「有活没干成」(异常)与「没活可干」(正常 = 全补完了),
    否则补完之后天天误报 —— 而误报会让真信号无处可显。
    """
    return cycle_state.next_zero_streak(prev, newly=written, attempted=attempted)


def _fetch(market: dict) -> list[dict]:
    """取一个市场的全部成交(wm=None → 从 offset 0 全回填)。走采集器自己的那条路径。"""
    return cc.poll_market(market, _fetch.counters, wm=None)


_fetch.counters = cc.new_counters()


def backfill_once(targets: list[dict], cursor: str, max_markets: int,
                  time_budget_s: float, fetch=None, write=None,
                  yield_check_every: int = YIELD_CHECK_EVERY) -> dict:
    """补一批。返回计数(不静默:请求/有返回/零返回/写入笔数/让路,全部出声)。

    `fetch` / `write` 可注入,便于判据离线跑(不碰网络、不落盘)。
    """
    fetch = fetch or _fetch
    write = write or se.write_trades
    counts = {"targets": len(targets), "attempted": 0, "with_trades": 0,
              "empty": 0, "trades_written": 0, "yielded": 0}
    if collector_is_running():
        counts["yielded"] = 1        # 出声:否则"为什么一直没补"无从解释
        return counts

    batch, new_cursor = select_batch(targets, cursor, max_markets)
    t0 = time.monotonic()
    for i, m in enumerate(batch):
        # 两道闸都在**发起请求之前**判。查完再判必然超出一整个市场的耗时。
        if time.monotonic() - t0 >= time_budget_s:
            break
        # 采集器可能**中途**起来(每 15 分钟一次,而本轮要跑好几分钟)。
        # 只在开头判一次 = 等于没判。
        if i and i % yield_check_every == 0 and collector_is_running():
            counts["yielded"] = 1
            break
        counts["attempted"] += 1
        rows = fetch(m)
        if rows:
            counts["with_trades"] += 1
            counts["trades_written"] += len(rows)
            write(rows)              # 空 rows 不写:空文件会污染分区、拖垮 compaction
        else:
            counts["empty"] += 1     # 零返回要出声:系统性查不到否则完全隐形
    counts["cursor"] = new_cursor
    return counts


def run(max_markets: int = DEFAULT_MAX_MARKETS,
        time_budget_s: float = DEFAULT_TIME_BUDGET_S) -> dict:
    """一次清扫。目标集声明式计算 ⇒ 补完的自动消失(天然幂等)。"""
    targets = load_targets()
    cursor = cycle_state.read_state(CURSOR_FILE, "cursor", "")
    counts = backfill_once(targets, cursor, max_markets, time_budget_s)
    if counts.get("cursor"):
        cycle_state.write_state(CURSOR_FILE, "cursor", counts["cursor"], "回填游标")
    streak = next_zero_streak(cycle_state.read_streak(STREAK_FILE),
                              counts["trades_written"], counts["attempted"])
    cycle_state.write_streak(STREAK_FILE, streak)
    counts["zero_streak"] = streak
    counts.pop("cursor", None)
    net = _fetch.counters
    print(f"[回填清扫] {counts} | 4xx {net['http_4xx_count']} "
          f"限流 {net['rate_limit_hits']} 重试耗尽 {net['net_give_up_count']} "
          f"溢出 {net['offset_overflow_count']} 解析拒绝 {net['parse_reject_count']}",
          flush=True)
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="已关闭市场成交回填清扫(只追加,不改旧数据)")
    ap.add_argument("--max-markets", type=int, default=DEFAULT_MAX_MARKETS)
    ap.add_argument("--budget", type=float, default=DEFAULT_TIME_BUDGET_S)
    ap.add_argument("--dry-run", action="store_true", help="只报目标集规模,不发任何请求")
    a = ap.parse_args()
    if a.dry_run:
        t = load_targets()
        b, c = select_batch(t, cycle_state.read_state(CURSOR_FILE, "cursor", ""), a.max_markets)
        print(f"目标集 {len(t)} 个;本轮会取 {len(b)} 个;"
              f"采集器在跑={collector_is_running()};游标→{c[-8:] if c else '(空)'}")
    else:
        run(max_markets=a.max_markets, time_budget_s=a.budget)
