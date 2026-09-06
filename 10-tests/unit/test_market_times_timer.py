"""时间防火墙侧表定时任务的验收判据(先于单元文件写成)。

## 由来

`backfill_market_times.py` 是**一次性脚本,没有定时任务** ——
2026-08-26 发现侧表最新只到 08-23,已陈旧 3 天。
后果:新结算的市场拿不到 `closed_time` ⇒ 任何需要时间防火墙的分析
(V4/V5b/V6 全部依赖它)对新数据**跑不了**。

🔴 权威副本是**仓库里的** `deploy/systemd/` —— 重装/换机会用它覆盖本机。
只查本机 = 绿灯而仓库是坏的(项目 2026-08-04 真栽过)。
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_SYSTEMD = PROJECT_ROOT / "deploy" / "systemd"
TIMER = REPO_SYSTEMD / "polymarket-market-times.timer"
SERVICE = REPO_SYSTEMD / "polymarket-market-times.service"
COLLECTOR_TIMER = REPO_SYSTEMD / "polymarket-rebirth-collector.timer"
BF_TIMER = REPO_SYSTEMD / "polymarket-backfill-closed.timer"

# 实测(2026-08-26,连续多轮):采集器一轮 113~142s;回填清扫时间闸 300s
OBSERVED_COLLECTOR_CYCLE_MAX_S = 200
BACKFILL_CLOSED_BUDGET_S = 300


def _oncalendar(path: Path) -> tuple[int, int]:
    m = re.search(r"^OnCalendar=\*:(\d+)(?:/(\d+))?", path.read_text(encoding="utf-8"), re.M)
    assert m, f"{path.name} 的 OnCalendar 不是 *:偏移[/周期] 形式"
    return int(m.group(1)), int(m.group(2) or 60)


def test_units_exist_in_the_repo_not_only_on_this_machine():
    """⭐单元文件必须在仓库里。只在本机 = 重装即丢(项目栽过)。"""
    assert TIMER.exists(), f"仓库里没有 {TIMER.name}"
    assert SERVICE.exists(), f"仓库里没有 {SERVICE.name}"


def test_schedule_does_not_collide_with_the_collector():
    """⭐起跑点必须避开采集器 —— 两者抢同一条代理隧道。

    08-04 正是隧道被拖垮 → 周期 3 倍慢 → 12 轮被 systemd 杀。

    ⚠️ 本判据第一版是**恒真式**(2026-08-26 review 抓到):
    写成 `phase >= busy_end % period or phase + period >= busy_end`,
    而第二个析取项对任意 phase 都成立 ⇒ 连 `off=0`(与采集器同一瞬间起跑)都判"不撞车"。
    更糟的是它**掩盖了一个真实风险**:按本文件自己的常量算,原定的 :48 落在忙区内。
    改用与下面那条兄弟判据相同的区间包含写法。
    """
    off, _ = _oncalendar(TIMER)
    col_off, col_period = _oncalendar(COLLECTOR_TIMER)
    busy_end = col_off + OBSERVED_COLLECTOR_CYCLE_MAX_S / 60.0
    phase = off % col_period
    assert not (col_off <= phase < busy_end), (
        f"起跑点 :{off}(相位 :{phase})落在采集器忙区 [:{col_off}, :{busy_end:.2f}) 内 —— "
        f"采集器 :{col_off} 起、最坏 {OBSERVED_COLLECTOR_CYCLE_MAX_S}s 收工")


def test_the_collision_guard_would_catch_a_real_collision():
    """⭐先在坏数据上验:与采集器同一瞬间起跑必须被判为撞车。

    结构检查:不做这一步,恒真式与真判据看起来完全一样(上面那条就是这么绿了一整轮)。
    """
    col_off, col_period = 0, 15
    busy_end = col_off + OBSERVED_COLLECTOR_CYCLE_MAX_S / 60.0

    def collides(off):
        return col_off <= (off % col_period) < busy_end

    assert collides(0) and collides(1) and collides(3), "同瞬间/忙区内起跑竟然不算撞车"
    assert not collides(50) and not collides(10), "空档被误判成撞车"


def test_run_finishes_before_the_next_occupied_slot():
    """⭐光看"起跑点不撞车"不够 —— 还得**跑得完**才不会撞上下一个任务。

    review 实测:采集器一轮曾达 148s(注释里写的是 113~142s),
    而原定 :48 起跑距回填清扫 :52 只剩 4 分钟,而批数上限允许跑 170s ⇒ 余量太薄。
    """
    off, _ = _oncalendar(TIMER)
    bf_off, bf_period = _oncalendar(BF_TIMER)
    svc = SERVICE.read_text(encoding="utf-8")
    timeout = int(re.search(r"^TimeoutStartSec=(\d+)", svc, re.M).group(1))
    # 下一个被占用的槽位(回填清扫)相对本任务起跑点还有多久
    nxt = min((bf_off + k * bf_period - off) for k in range(1, 5)
              if bf_off + k * bf_period - off > 0)
    assert timeout <= nxt * 60, (
        f"最坏跑 {timeout}s,而距下一个占用槽位(回填清扫 :{bf_off}/{bf_period}分)只有 "
        f"{nxt} 分钟 = {nxt*60}s ⇒ 会撞上去")


def test_schedule_does_not_collide_with_the_closed_backfill():
    """也要避开回填清扫(它有 300s 时间闸)。"""
    off, _ = _oncalendar(TIMER)
    bf_off, bf_period = _oncalendar(BF_TIMER)
    bf_end = bf_off + BACKFILL_CLOSED_BUDGET_S / 60.0
    phase = off % bf_period
    assert not (bf_off <= phase < bf_end), (
        f"起跑点 :{off} 落在回填清扫忙区 [:{bf_off}, :{bf_end:.0f}) 内")


def test_run_is_bounded_so_it_cannot_eat_the_next_slot():
    """⭐必须有批数上限 + 超时,否则积压时会一直跑到下一轮起来。

    脚本本身可断点续跑,所以"跑不完"不是问题;"跑过界"才是。
    """
    svc = SERVICE.read_text(encoding="utf-8")
    m = re.search(r"--max-batches\s+(\d+)", svc)
    assert m, "ExecStart 没有 --max-batches,积压时会跑到没边"
    t = re.search(r"^TimeoutStartSec=(\d+)", svc, re.M)
    assert t, "没有 TimeoutStartSec 兜底"
    off, period = _oncalendar(TIMER)
    assert int(t.group(1)) < period * 60, (
        f"超时 {t.group(1)}s 不小于调度周期 {period*60}s ⇒ 会活到下一轮起来")


def test_exec_start_is_a_single_command_with_absolute_paths():
    """⭐编排层也会静默失败(CLAUDE.md 静默失败第 10 条)。

    - 绝对路径:cron/systemd 的工作目录不是项目目录
    - 单条 ExecStart:多条串起来时,前一条失败会让后面的**都不执行**
    """
    svc = SERVICE.read_text(encoding="utf-8")
    execs = re.findall(r"^ExecStart=(.*)$", svc, re.M)
    assert len(execs) == 1, f"有 {len(execs)} 条 ExecStart —— 前一条失败会让后面的不执行"
    cmd = execs[0]
    assert cmd.startswith("/"), f"ExecStart 不是绝对路径: {cmd}"
    for tok in cmd.split():
        if tok.endswith(".py"):
            assert tok.startswith("/"), f"脚本路径不是绝对的: {tok}"
            assert Path(tok).exists(), f"ExecStart 指向的脚本不存在: {tok}"


def test_persistent_is_on_so_a_suspended_machine_catches_up():
    """⭐机器休眠错过的那一轮必须补跑。

    项目 2026-07-06 的教训:固定时间点的 cron 撞上 systemd suspend 会**整轮漏跑**,
    已迁 systemd timer + Persistent=true。这条不许倒退。
    """
    assert re.search(r"^Persistent=true", TIMER.read_text(encoding="utf-8"), re.M), \
        "没有 Persistent=true —— 机器休眠错过的那一轮不会补跑"


def test_service_file_is_syntactically_loadable():
    """结构检查:交给 systemd 自己解析一遍,不靠我肉眼看对不对。"""
    r = subprocess.run(["systemd-analyze", "verify", "--user", str(SERVICE)],
                       capture_output=True, text=True)
    # verify 对 unit 之间的依赖会报 warning,只把"解析失败"当错
    bad = [ln for ln in (r.stderr or "").splitlines()
           if "Failed to parse" in ln or "Invalid" in ln or "Unknown lvalue" in ln]
    assert not bad, f"systemd 解析不了这个 service:\n" + "\n".join(bad)


def test_the_live_copy_matches_the_repo_copy_if_installed():
    """⭐仓库与本机若都存在则必须一致 —— 不一致 = 重装就会静默回退。"""
    live_dir = Path.home() / ".config" / "systemd" / "user"
    for f in (TIMER, SERVICE):
        live = live_dir / f.name
        if not live.exists():
            pytest.skip(f"{f.name} 尚未安装到本机(仓库副本已就位)")
        assert live.read_text(encoding="utf-8") == f.read_text(encoding="utf-8"), \
            f"{f.name} 本机与仓库不一致 —— 重装会用仓库副本覆盖,本机改动会丢"
