#!/usr/bin/env python3
"""判据焊死:已关闭市场的回填清扫(2026-08-06)。

## 由来

轮询层饿死导致 **23,675 个已关闭市场一笔成交都没采到**,而它们已被 `pollable`
过滤(`closed=True`)⇒ 活体链路**永远不会**再采它们。
可行性评估(docs/PREREG_CLOSED_MARKET_BACKFILL_2026-08-06.md,预登记单先写):
分层随机抽 48 个,**四层全部 100% 有返回**、解析留存率 100%、单市场 p50=1.5s。

⭐**而漏还在漏**:实测每天新关闭且从没采过 1,873~3,273 个,
而游标轮转吞吐上限 2,304/天 —— 基本持平,08-05 那天在漏。
故本模块**不是一次性脚本,是常设清扫**:目标集声明式定义为「已关闭 ∧ 成交湖里没有」,
于是它同时吃掉存量 23,675 和每天的新增,不需要两套东西。

## 设计要点(每条都配判据)

1. **目标集不许用与结果相关的条件筛**。只能是"已关闭 ∧ 没采过",
   不许加"只补最近 N 天"之类 —— 那等于用年龄筛样本,而年龄与市场性质相关。
2. **游标轮转 + 回卷**。一轮补不完(23,675 × 1.5s ≈ 11 小时),被时间闸中断后
   必须从断点继续。每次从头 = 队尾永远补不到 = 把刚修好的饿死病换个地方复发。
3. **必须给采集器让路**。两者抢同一条代理隧道,而 08-04 正是被隧道拖垮杀了 12 轮。
4. **对账出声**:请求 / 有返回 / 零返回 / 溢出,全部计数;稳态零返回应极少(实测 0/48)。
5. **只追加,不碰别人的东西**:走 `se.write_trades`(append-only,uuid 文件名,
   与采集器并发写安全),不碰注册表、不碰采集器的任何状态文件。

## 本判据**不能**回答什么

1. **不验证真实网络行为** —— 取数被替身挡掉。真机证据在预登记单的结果段。
2. **不回答"补完要多久"** —— 那取决于实际时间闸与让路频率,由心跳数据事后复核。
3. **不保证补回的数据完整** —— 只保证"取到什么就写什么、丢了会出声"。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import backfill_closed_markets as bf  # noqa: E402


def _t(i: int) -> dict:
    return {"condition_id": f"0x{i:064x}", "token_id_0": "1", "token_id_1": "2"}


def _targets(n: int) -> list[dict]:
    """目标集。⭐顺序故意**不按** condition_id —— 真实 SQL 返回顺序不保证,
    而"轮转有没有真按排序键走"只有在这两者不一致时才测得出来
    (test_poll_rotation 那边踩过一模一样的坑:替身"恰好"有序 = 判据失去分辨力)。"""
    return [_t((i * 7919) % n) for i in range(n)]


# ============ 判据组 A:目标集不许被结果相关的条件筛 ============

def test_target_sql_selects_only_closed_and_never_polled():
    """目标集的定义只能是「已关闭 ∧ 成交湖里没有」。

    加任何"只补最近 N 天""只补成交量大的"之类的条件,都是**用与结果相关的变量筛样本**
    —— 补回来的会系统性偏向某一类,而基于它的任何结论结构性作废(CLAUDE.md 铁律 2)。
    """
    sql = bf.TARGET_SQL.lower()
    assert "closed" in sql and "is null" in sql, "目标集定义变了"
    for banned in ("end_date >", "end_date <", "limit ", "volume", "order by rand"):
        assert banned not in sql, f"目标集里出现了与结果相关的筛选:{banned}"


# ============ 判据组 B:游标轮转(补不完必须能接着补) ============

def test_batch_advances_across_runs():
    """⭐第二轮必须从第一轮停的地方继续,不是重头再来。

    每次从头 = 前 N 个补完之后队尾永远轮不到 —— 那正是刚在轮询层修掉的饿死,
    换个地方原样复发。
    """
    targets = _targets(100)
    b1, c1 = bf.select_batch(targets, cursor="", max_n=30)
    b2, _ = bf.select_batch(targets, cursor=c1, max_n=30)
    assert not (set(_ids(b1)) & set(_ids(b2))), "第二轮又补了第一轮补过的"


def test_every_target_is_reached_within_one_lap():
    """⭐⭐一圈之内每个目标都要被取到 —— 否则"23,675 个都能补上"这句话是假的。"""
    total, n = 100, 30
    targets = _targets(total)
    seen, cursor = set(), ""
    for _ in range(-(-total // n) + 1):
        batch, cursor = bf.select_batch(targets, cursor=cursor, max_n=n)
        seen |= set(_ids(batch))
    assert not set(_ids(targets)) - seen, "转了一圈仍有目标没被取到"


def test_cursor_wraps_at_the_end():
    """走到末尾要回卷(否则下一轮空转,新关闭的市场永远排在后面补不到)。"""
    targets = _targets(50)
    batch, _ = bf.select_batch(targets, cursor=max(_ids(targets)), max_n=10)
    assert len(batch) == 10, "游标到末尾后没有回卷"


def test_cursor_is_a_sort_key_not_an_index():
    """目标集每轮都在变(补完的会消失、新关闭的会加入),存下标会乱跳。"""
    cursor = ""
    seen = set()
    for i in range(12):
        targets = _targets(80)[i % 4:]      # 集合每轮变动
        batch, cursor = bf.select_batch(targets, cursor=cursor, max_n=10)
        seen |= set(_ids(batch))
        assert isinstance(cursor, str)
    assert len(seen) > 60, f"集合变动下轮转卡住(12 轮只覆盖 {len(seen)} 个)"


def test_empty_target_set_is_not_an_error():
    """全补完了 = 成功,不是故障。必须静默返回,不许崩也不许告警。"""
    batch, cursor = bf.select_batch([], cursor="abc", max_n=10)
    assert batch == [] and cursor == "abc"


# ============ 判据组 C:必须给采集器让路(它们抢同一条代理隧道) ============

def test_run_yields_when_collector_is_active(monkeypatch):
    """⭐采集器在跑时必须一个市场都不取。

    08-04 的事故就是隧道被拖垮 → 周期 3 倍慢 → 12 轮被 systemd 杀。
    回填是**可以等**的(数据已经在那儿,晚几小时无所谓),采集器不能等。
    """
    monkeypatch.setattr(bf, "collector_is_running", lambda: True)
    fetched = []
    counts = bf.backfill_once(_targets(10), cursor="", max_markets=10,
                              time_budget_s=100,
                              fetch=lambda m: fetched.append(m) or [],
                              write=lambda rows: None)
    assert fetched == [], "采集器在跑,回填却照样发请求 —— 会把它拖垮"
    assert counts["yielded"] == 1, "让路必须出声(否则'为什么一直没补'无从解释)"


def test_run_proceeds_when_collector_is_idle(monkeypatch):
    """反面焊死:采集器空闲时必须真的干活,否则让路逻辑写死成"永远让"也照样绿。"""
    monkeypatch.setattr(bf, "collector_is_running", lambda: False)
    fetched = []
    bf.backfill_once(_targets(5), cursor="", max_markets=5, time_budget_s=100,
                     fetch=lambda m: fetched.append(m) or [_row(1)],
                     write=lambda rows: None)
    assert len(fetched) == 5


def test_run_aborts_midway_if_collector_starts(monkeypatch):
    """采集器**中途**启动也要立刻停手 —— 只在开头判一次等于没判:
    一轮回填最长要跑好几分钟,而采集器每 15 分钟就起来一次。"""
    state = {"n": 0}

    def running():
        state["n"] += 1
        return state["n"] > 3      # 前 3 次空闲,之后采集器起来了

    monkeypatch.setattr(bf, "collector_is_running", running)
    fetched = []
    counts = bf.backfill_once(_targets(50), cursor="", max_markets=50, time_budget_s=100,
                              fetch=lambda m: fetched.append(m) or [_row(1)],
                              write=lambda rows: None,
                              yield_check_every=1)
    assert len(fetched) < 50, "采集器起来了还在继续发请求"
    assert counts["yielded"] == 1


@pytest.mark.parametrize("state,running", [
    # 🔴 这一行是 2026-08-06 真栽的那个:采集器是 Type=oneshot,**运行期间是 activating**
    #    而不是 active,于是 `is-active --quiet` 退出码 3,原实现判成"没在跑" → 互锁失效。
    ("activating", True),
    ("active", True),
    ("deactivating", True),      # 还在收尾,仍占着隧道
    ("reloading", True),
    ("inactive", False),
    ("failed", False),
    ("", True),                  # 问不出来 → 当它在跑(方向必须偏保守)
    ("some-future-state", True),  # 没见过的状态同理
])
def test_systemd_state_is_parsed_by_string_not_exit_code(state, running):
    """⭐⭐直接喂 systemd **真实会吐的字符串**,不许 mock 掉被测的那一行。

    此前 4 条让路判据全绿,而互锁其实是坏的 —— 因为它们把 `collector_is_running`
    或 `subprocess.run` 整个替换掉了,**恰好绕开了唯一出错的那一行**
    (`returncode == 0`)。替身替掉了被测对象本身 = 判据在验证自己的替身。
    """
    assert bf._is_running_state(state) is running


def test_unknown_collector_state_defaults_to_yielding(monkeypatch):
    """⭐问不出采集器状态时,必须**当它在跑**。

    两边的代价不对称:回填晚几小时毫无损失(数据已经在那儿),
    而把采集器拖垮会丢正在发生的成交流,且 08-04 实测会被 systemd 杀。
    默认值选错的方向 = 在最糟的时刻(systemd 出问题时)恰好去抢资源。
    """
    def boom(*a, **k):
        raise OSError("systemctl 不见了")

    monkeypatch.setattr(bf.subprocess, "run", boom)
    assert bf.collector_is_running() is True, "问不出来却默认放行 —— 方向反了"


# ============ 判据组 D:时间闸(不许把机器占满,也不许无界) ============

def test_time_budget_stops_before_starting_another_market(monkeypatch):
    """时间闸必须在**取下一个市场之前**判 —— 取完再判必然超出一整个市场的耗时。"""
    monkeypatch.setattr(bf, "collector_is_running", lambda: False)
    clock = _Clock()
    monkeypatch.setattr(bf.time, "monotonic", clock)

    def slow(m):
        clock.t += 10.0
        return [_row(1)]

    counts = bf.backfill_once(_targets(100), cursor="", max_markets=100,
                              time_budget_s=25, fetch=slow, write=lambda rows: None)
    assert counts["attempted"] == 3, f"预算 25s/每个 10s,应做 3 个,实际 {counts['attempted']}"


def test_max_markets_caps_the_batch(monkeypatch):
    """两道闸都要有:时间闸挡延迟退化,个数闸挡"网络特别快时一口气打爆接口"。"""
    monkeypatch.setattr(bf, "collector_is_running", lambda: False)
    counts = bf.backfill_once(_targets(500), cursor="", max_markets=7,
                              time_budget_s=10_000,
                              fetch=lambda m: [_row(1)], write=lambda rows: None)
    assert counts["attempted"] == 7


# ============ 判据组 E:对账必须出声(丢了要看得见) ============

def test_counts_reconcile_requested_against_returned(monkeypatch):
    """请求数 vs 有返回数必须分开报。只报"补了多少笔"会让"有一批查不到"完全隐形。"""
    monkeypatch.setattr(bf, "collector_is_running", lambda: False)
    seq = [[], [_row(1), _row(2)], [], [_row(3)]]
    counts = bf.backfill_once(_targets(4), cursor="", max_markets=4, time_budget_s=100,
                              fetch=lambda m: seq.pop(0), write=lambda rows: None)
    assert counts["attempted"] == 4
    assert counts["with_trades"] == 2
    assert counts["empty"] == 2, "零返回的市场没出声 —— 系统性查不到会完全隐形"
    assert counts["trades_written"] == 3


def test_nothing_written_when_nothing_fetched(monkeypatch):
    """防洪另一头:什么都没取到时不许写空文件(会污染分区、把 compaction 拖垮)。"""
    monkeypatch.setattr(bf, "collector_is_running", lambda: False)
    wrote = []
    bf.backfill_once(_targets(3), cursor="", max_markets=3, time_budget_s=100,
                     fetch=lambda m: [], write=lambda rows: wrote.append(rows))
    assert wrote == []


def test_zero_streak_guards_a_silently_dead_link(monkeypatch):
    """⭐「有活干却一个都没补到」必须能被发现(连零守护)。

    这正是 08-03 结算真值静默断供 11 天的形态:进程正常、日志照打、只有产出恒为 0。
    ⚠️ 且必须区分「有活没干成」(异常)与「没活可干」(正常,= 全补完了)。
    """
    assert bf.next_zero_streak(prev=2, written=0, attempted=50) == 3, "有活没干成要进位"
    assert bf.next_zero_streak(prev=3, written=10, attempted=50) == 0, "补到了要归零"
    assert bf.next_zero_streak(prev=3, written=0, attempted=0) == 3, \
        "没活可干是成功不是失败,不许进位(否则补完之后天天误报)"


# ============ 判据组 F:不许碰别人的东西 ============

def test_uses_the_shared_append_only_write_path():
    """必须走 se.write_trades:append-only + uuid 文件名 = 与采集器并发写安全。
    自己另写一套落盘 = schema 漂移 + 可能覆盖采集器正在写的文件。"""
    import inspect
    src = inspect.getsource(bf)
    assert "se.write_trades" in src or "write_trades" in src
    for banned in ("REGISTRY_DIR", "firehose_watermark", "poll_cursor", "registration_backlog"):
        assert banned not in src, f"回填碰了采集器的 {banned} —— 只读的东西不许写"


def test_has_its_own_cursor_file():
    """自己的游标文件,不许和采集器共用(共用 = 互相把对方的进度冲掉)。"""
    assert "backfill" in str(bf.CURSOR_FILE).lower()
    assert bf.CURSOR_FILE.name not in ("poll_cursor.json", "settlement_cursor.json")


# ============ 判据组 G:排班不许撞车(两者抢同一条代理隧道) ============
#
# 🔴 权威副本是**仓库里的** deploy/systemd/ —— 重装/换机会用它覆盖本机。
# 只查本机 = 绿灯而仓库是坏的(2026-08-04 真栽过)。

import re  # noqa: E402

REPO_SYSTEMD = PROJECT_ROOT / "deploy" / "systemd"
LIVE_SYSTEMD = Path.home() / ".config" / "systemd" / "user"
BF_TIMER = REPO_SYSTEMD / "polymarket-backfill-closed.timer"
BF_SERVICE = REPO_SYSTEMD / "polymarket-backfill-closed.service"
COLLECTOR_TIMER = REPO_SYSTEMD / "polymarket-rebirth-collector.timer"

# 实测采集器一轮耗时(2026-08-06,连续 6 轮):320/345/345/379/403/345 秒
OBSERVED_COLLECTOR_CYCLE_MAX_S = 403


def _oncalendar(path: Path) -> tuple[int, int]:
    """解析 `OnCalendar=*:OFFSET/PERIOD` → (offset 分, period 分)。"""
    m = re.search(r"^OnCalendar=\*:(\d+)/(\d+)", path.read_text(), re.M)
    assert m, f"{path.name} 的 OnCalendar 不是 *:偏移/周期 形式"
    return int(m.group(1)), int(m.group(2))


def test_backfill_starts_after_the_collector_finishes():
    """⭐回填的起跑点必须晚于采集器**实测**收工时间。

    排在采集器还在跑的时候起来 = 两个进程抢同一条代理隧道,
    而 08-04 正是隧道被拖垮 → 周期 3 倍慢 → 12 轮被 systemd 杀。
    """
    bf_off, bf_period = _oncalendar(BF_TIMER)
    col_off, col_period = _oncalendar(COLLECTOR_TIMER)
    assert bf_period == col_period, "两个 timer 周期不同 → 相位会漂移,撞车只是时间问题"
    gap_s = (bf_off - col_off) * 60
    assert gap_s >= OBSERVED_COLLECTOR_CYCLE_MAX_S, (
        f"回填在采集器起跑后 {gap_s}s 就开工,而实测采集器最长要 "
        f"{OBSERVED_COLLECTOR_CYCLE_MAX_S}s —— 会抢隧道")


def test_backfill_finishes_before_the_next_collector_cycle():
    """回填必须在下一轮采集器起来之前收工(时间闸 + 起跑偏移 < 周期)。"""
    bf_off, period = _oncalendar(BF_TIMER)
    assert bf_off * 60 + bf.DEFAULT_TIME_BUDGET_S < period * 60, (
        "回填会活到下一轮采集器起跑之后 —— 让路逻辑虽然兜得住,但排班本身就不该这么排")


def test_backfill_hard_timeout_is_below_the_collector_interval():
    """systemd 硬杀线也要小于采集器间隔 —— 卡死的回填不能横跨两轮。"""
    m = re.search(r"^TimeoutStartSec=(\d+)", BF_SERVICE.read_text(), re.M)
    assert m, "回填 service 没设 TimeoutStartSec —— 卡死就永远占着隧道"
    _, period = _oncalendar(COLLECTOR_TIMER)
    assert int(m.group(1)) < period * 60


def test_hard_timeout_leaves_room_above_the_time_gate():
    """硬杀线要高于脚本自己的时间闸,否则每轮都被 systemd 杀 = 时间闸形同虚设,
    且被杀的进程不会写游标 → 每轮从同一处重来(又一个饿死)。"""
    m = re.search(r"^TimeoutStartSec=(\d+)", BF_SERVICE.read_text(), re.M)
    assert int(m.group(1)) > bf.DEFAULT_TIME_BUDGET_S


@pytest.mark.parametrize("name", ["polymarket-backfill-closed.service",
                                  "polymarket-backfill-closed.timer"])
def test_live_unit_matches_repo_unit(name):
    """本机生效副本 与 仓库副本 不许漂移(仓库是权威,重装会覆盖本机)。"""
    live = LIVE_SYSTEMD / name
    if not live.exists():
        pytest.skip("本机未安装该单元(CI/他机)")
    assert live.read_text() == (REPO_SYSTEMD / name).read_text(), \
        f"{name}:本机与仓库副本不一致 —— 重装会把本机改动冲掉"


# ============ 测试替身 ============

class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _ids(ms) -> list[str]:
    return [m["condition_id"] for m in ms]


def _row(i: int) -> dict:
    return {"condition_id": "0xc", "timestamp": 1_700_000_000 + i}
