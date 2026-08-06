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
import datetime as dt
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

# ⭐目标集**只能**是「已关闭 ∧ 没有完成标记」。
# 不许加"只补最近 N 天""只补成交量大的"之类 —— 那是**用与结果相关的变量筛样本**
# (CLAUDE.md 铁律 2):补回来的会系统性偏向某一类,基于它的结论结构性作废。
# 判据 test_target_sql_selects_only_closed_and_unswept 焊死这一点。
#
# 🔴 2026-08-06 改:旧定义是「已关闭 ∧ 成交湖里**一笔都没有**」,
# 于是**采过一半的市场被排除在外,谁也不管** —— 实测 24,772 个已关闭市场
# 最后一次采集发生在我们看到它关闭之前,缺的正是结算前最后一段。
# 新定义直接实现不变量 A4:每个市场关闭后必须有且至少有一次完整采集(见 storage_engine)。
#
# `wm`(水位线)一起取回来:缺的只是"上次采集到关闭"这一截尾巴,
# 按水位线增量拉就够,不必从头全拉 —— 从头拉会重复写回已有的几百万笔,
# 白烧接口配额和磁盘(compaction 虽会去重,但那是事后)。
# 从没采过的市场 `wm` 为 NULL ⇒ `poll_market` 退化成全量回填,与旧行为一致。
TARGET_SQL = """
with reg1 as (
  select * from (
    select condition_id, token_id_0, token_id_1, closed,
           row_number() over (partition by condition_id order by snapshot_at desc) rn
    from read_parquet('{registry}/*.parquet')
  ) where rn = 1
),
swept as ({swept_src}),
wm as ({wm_src})
select r.condition_id, r.token_id_0, r.token_id_1, w.wm
from reg1 r
left join swept s using (condition_id)
left join wm w using (condition_id)
where r.closed = true and s.condition_id is null
"""

# 目录为空时不能 read_parquet 空 glob(DuckDB 直接抛 IOException)—— 造 0 行的同构 CTE。
# ⚠️ 成交湖那一份是**既有的隐藏 bug**:旧代码的 docstring 写着"空湖时退化为全部已关闭",
# 而实际会崩。生产环境湖永远非空所以从没触发,是 2026-08-06 新加的判据挖出来的。
# 「注释声称的行为」和「代码真实的行为」又一次不一致 —— 这正是判据存在的意义。
_EMPTY_SWEPT = "select null::VARCHAR as condition_id where false"
_EMPTY_WM = "select null::VARCHAR as condition_id, null::BIGINT as wm where false"


def load_targets() -> list[dict]:
    """目标集:已关闭、且**没有完成标记**的市场,连同它已有的水位线。"""
    con = se.duckdb_conn()
    try:
        swept_src = (
            f"select distinct condition_id from read_parquet('{se.SWEPT_DIR}/*.parquet')"
            if se.has_swept_data() else _EMPTY_SWEPT)
        wm_src = (
            f"select condition_id, max(timestamp) wm from"
            f" read_parquet('{se.RAW_DIR}/**/*.parquet', union_by_name=true) group by 1"
            if se.has_data() else _EMPTY_WM)
        sql = TARGET_SQL.format(registry=se.DATA_ROOT / "registry",
                                swept_src=swept_src, wm_src=wm_src)
        rows = con.sql(sql).fetchall()
    finally:
        con.close()
    return [{"condition_id": c, "token_id_0": t0, "token_id_1": t1, "wm": wm}
            for c, t0, t1, wm in rows]


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
    """取一个市场缺的那截成交。走采集器自己的那条路径(不另造一份轮询逻辑)。

    `wm` 来自目标集查询:有水位线 ⇒ 只补"上次采到"到"关闭"这一截尾巴;
    没有(从没采过)⇒ `poll_market` 退化成从 offset 0 全量回填,与旧行为一致。
    """
    return cc.poll_market(market, _fetch.counters, wm=market.get("wm"))


_fetch.counters = cc.new_counters()


def backfill_once(targets: list[dict], cursor: str, max_markets: int,
                  time_budget_s: float, fetch=None, write=None,
                  yield_check_every: int = YIELD_CHECK_EVERY,
                  mark=None, net=None) -> dict:
    """补一批。返回计数(不静默:请求/有返回/零返回/写入笔数/让路/落标记,全部出声)。

    `fetch` / `write` / `mark` 可注入,便于判据离线跑(不碰网络、不落盘)。
    """
    fetch = fetch or _fetch
    write = write or se.write_trades
    mark = mark or se.write_swept
    net = _fetch.counters if net is None else net
    counts = {"targets": len(targets), "attempted": 0, "with_trades": 0,
              "empty": 0, "trades_written": 0, "yielded": 0,
              "swept_marked": 0, "sweep_incomplete": 0}
    if collector_is_running():
        counts["yielded"] = 1        # 出声:否则"为什么一直没补"无从解释
        return counts

    batch, _planned_cursor = select_batch(targets, cursor, max_markets)
    t0 = time.monotonic()
    now = int(dt.datetime.now(dt.UTC).timestamp())
    swept: list[dict] = []
    last_done = None                 # ⭐游标只认**真做过**的,见下方说明
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
        cut_before = net.get("poll_truncated_count", 0)
        rows = fetch(m)
        if rows:
            counts["with_trades"] += 1
            counts["trades_written"] += len(rows)
            write(rows)              # 空 rows 不写:空文件会污染分区、拖垮 compaction
        else:
            counts["empty"] += 1     # 零返回要出声:系统性查不到否则完全隐形
        # ⭐落 A4 完成标记 —— 但**只在这一遍真的走完了**的时候。
        # 网络中途断掉时 `poll_market` 会保留近端并记 `poll_truncated_count`,
        # 此时若照样落标记,就等于宣布"这个市场我们扫过了"而实际上没扫完 ——
        # 而这个市场从此再也不会进目标集,**丢的那一段永久没人管**。
        # 不对称原则:不落标记只是白跑一次接口;错落标记是永久丢数据。
        if net.get("poll_truncated_count", 0) > cut_before:
            counts["sweep_incomplete"] += 1   # 出声:否则"为什么它老在目标集里"无从解释
        else:
            # 零成交也要落标记:A4 的义务是"扫过一遍",不是"扫到东西"。
            # 不落的话,真没成交的市场会永远留在目标集里被反复重扫。
            swept.append({"condition_id": m["condition_id"], "swept_at": now,
                          "source": "backfill", "trades_written": len(rows)})
        last_done = m
    # 本清扫扫的正是"已关闭且一笔没采过"的盘 —— **撞 offset 硬顶概率最高的那一类**。
    # 不冲痕迹 = 历史空洞只记一半,而漏掉的恰是最容易出洞的那一半。
    cc.flush_truncations(_fetch.counters)
    # 标记冲盘。⚠️ 留在内存里等于没留 —— 每轮是独立进程,退出即失忆
    # (与 truncations 同一条教训)。
    if swept:
        mark(swept)
        counts["swept_marked"] = len(swept)
    # 🔴 2026-08-06:此前这里写的是 `counts["cursor"] = new_cursor`,即**规划的最后一个**。
    # 而一轮规划 400 个、时间闸(300s)实际只做得完中位 240 个 ⇒ 每轮约 160 个
    # 压根没被碰过就被游标跳过,一圈只覆盖目标集的约六成,补完存量的时间因此多出六成。
    #
    # 打个比方:银行叫号一次叫 400 人进来,下班只办完 240 个,剩下的被请出去,
    # **而叫号机照样跳到 400 号** —— 他们的号作废,明天重新排队尾。
    #
    # ⭐ 这与 `settlement_watcher.watch_settlements` 里"游标只走过真查过的市场"
    # 是**同一个形状**,那边 08-06 已修 —— 当时三处只修了一处。
    # 一个都没做成 → 不写游标,整批留给下轮(否则等于"什么都没干却宣布这批过去了")。
    if last_done is not None:
        counts["cursor"] = last_done["condition_id"]
    return counts


def run(max_markets: int = DEFAULT_MAX_MARKETS,
        time_budget_s: float = DEFAULT_TIME_BUDGET_S) -> dict:
    """一次清扫。目标集声明式计算 ⇒ 补完的自动消失(天然幂等)。"""
    targets = load_targets()
    cursor = cycle_state.read_state(CURSOR_FILE, "cursor", "")
    counts = backfill_once(targets, cursor, max_markets, time_budget_s)
    if counts.get("cursor"):
        cycle_state.write_state(CURSOR_FILE, "cursor", counts["cursor"], "回填游标")
    # ⚠️ 2026-08-06 换了"产出"的口径:从「补到几笔成交」改成「落了几个完成标记」。
    # 立起 A4 之后,目标集里大量市场的尾巴本来就是空的(市场最后那段真没成交),
    # 于是"补到 0 笔"变成**正常**结果 —— 再拿它当零产出就会天天误报,
    # 而误报会让真信号无处可显。A4 的义务是"扫过一遍",不是"扫到东西",
    # 所以守护该盯的是**有没有扫成**:网络全挂时 swept_marked 才会是 0。
    streak = next_zero_streak(cycle_state.read_streak(STREAK_FILE),
                              counts["swept_marked"], counts["attempted"])
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
