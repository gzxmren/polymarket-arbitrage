#!/usr/bin/env python3
"""判据焊死:周期时间预算 —— 「慢周期」守护 + systemd 红线不许漂移(2026-08-04)。

## 由来(实测事故,非假想)

2026-08-04 当天 12 个采集周期被 systemd `TimeoutStartSec=480` 杀掉(SIGTERM)。
被杀的周期在日志里只留一行光秃秃的 `=== 采集周期 ... ===` —— Python 块缓冲还没 flush
就被杀,**诊断信息全丢**。同期吞吐可见下滑(17h 那小时入库 14,376 行,邻近小时 20k~30k)。

根因不在代码:实测各本地阶段成本全是小头(compaction 1.0s / load_registry 1.2s /
all_watermarks 0.1s),时间全在网络。当时实测 gamma-api 连打 30 次 **7 次失败**
(23%,全是 `SSLError: wrong version number`,流量走 v2rayN/sing-box TUN 代理)。
`_get` 的 `tries=5 + sleep(1.5)` 把每个失败请求从 0.7s 拉到 ~6s → 周期 3 倍慢 → 撞超时。

## 为什么监控「周期耗时」而不是「网络失败率」

先过 CLAUDE.md 那一问:**「这个被阈值化的量,本身该被监控吗?」**

- **重试耗尽(give-up)率**:实测 60 次 `_get` 调用 **0 次**耗尽(单次失败率 4.8% 时)。
  重试把瞬时失败全吸收了。**故 give-up 告警根本抓不住 08-04 这场事故** —— 它当天大概率
  也是 0。用它 = 在错的量上定阈值。
- **单次失败率**:是真因,但没有稳态基线(从来没计过数,见 [[test_net_failure_counting]]),
  今天现测 4.8%~23% 剧烈抖动,此刻定阈值等于拍脑袋。故本轮**只计数入心跳、不告警**,
  攒够一周再回来校准。
- **周期耗时**:✅ 它变坏 = 我关心的事(周期被杀、吞吐下滑)确实变坏,且红线是**结构性**的
  —— 超过 systemd 上限就是真被杀,不是调出来的参数。故选它。

## 阈值依据(实测分布,非拍脑袋)

journalctl 实测 148 个成功周期(08-01~08-04):
    p50=119s  p75=155s  p90=228s  p95=377s  p99=438s  max=480s(撞上限被杀)
剔除已受本次事故污染的退化周期(>300s)后,**健康稳态 n=134**:
    p50=115s  p90=167s  p95=182s  **p99=221s  max=228s**

取 `SLOW_CYCLE_RATIO = 0.6` → 900s × 0.6 = **540s**:
  - 对实测健康 max(228s)有 **2.4 倍余量** → 稳态完全静默(防洪的一头)
  - 距被杀(900s)仍留 360s 余量 → 是「预警」不是「讣告」(真异常必推的另一头)

## 防洪(CLAUDE.md:新增告警必须自带防洪判据,两头都要焊死)

单个慢周期是自愈噪声(网络抖一下),**不推**。只有**连续 N 轮**持续慢才是真退化。
N=4(15 分钟/轮 → 1 小时)。之后按整数倍复述 → 退化期间每小时一条,不是每轮一条。
"""
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import alerts  # noqa: E402
import cycle_state  # noqa: E402

# 🔴 权威副本是**仓库里的** deploy/systemd/ —— deploy/systemd/README.md 记载的安装步骤是
#    `cp deploy/systemd/*.service *.timer ~/.config/systemd/user/`,即重装/换机/灾恢时
#    仓库副本会**覆盖**本机文件。
# 差点栽在这上面(2026-08-04,python-reviewer 抓出):本次只手改了本机文件,仓库副本仍是
# 事故值 480s/10min;而本测试当时只查本机 → **绿灯,但一重装就把事故原样装回去**。
# 「如果它现在就是坏的,我看到的会有什么不同?」—— 当时答案是"没有不同"。故改为查仓库副本。
REPO_SYSTEMD_DIR = PROJECT_ROOT / "deploy" / "systemd"
SERVICE_FILE = REPO_SYSTEMD_DIR / "polymarket-rebirth-collector.service"
TIMER_FILE = REPO_SYSTEMD_DIR / "polymarket-rebirth-collector.timer"

# 本机实际生效的副本(只用于查"仓库 vs 本机"漂移,不作权威)
LIVE_SYSTEMD_DIR = Path.home() / ".config" / "systemd" / "user"

# ---- 实测分布(journalctl,08-01~08-04,148 个成功周期;健康子集 n=134)----
OBSERVED_HEALTHY_P99_S = 221
OBSERVED_HEALTHY_MAX_S = 228


# ---------- 红线不许漂移:Python 常量 ↔ systemd 单元文件 ----------
# CLAUDE.md:红线要「逐字沿用/import 被检验对象自己的红线」,而非复制粘贴。
# 这里两边天然分居(一个 .py 一个 .service),无法 import,故用测试把它们焊死:
# 谁单独改了都会红。

def test_service_timeout_matches_python_budget():
    """仓库 `.service` 的 TimeoutStartSec 必须等于 alerts.CYCLE_BUDGET_S。

    否则:Python 以为预算 900s、systemd 实际 480s 杀 —— 告警线算在一个不存在的预算上,
    「快到上限了」永远不会响,而周期照样被杀。这正是 08-04 的形态。
    **不加 skipif**:仓库副本永远存在,跳过 = 又把判据变成摆设。
    """
    m = re.search(r"^TimeoutStartSec=(\d+)", SERVICE_FILE.read_text(), re.M)
    assert m, "TimeoutStartSec 未设置 —— 没有上限 = 卡死的周期会一直占着"
    assert int(m.group(1)) == alerts.CYCLE_BUDGET_S


def test_timer_interval_matches_budget():
    """timer 间隔必须 == 预算。间隔 < 预算 → 上一轮还没跑完下一轮就叫醒 = 排队打转。"""
    m = re.search(r"^OnCalendar=\*:0/(\d+)", TIMER_FILE.read_text(), re.M)
    assert m, "OnCalendar 未按 *:0/N 分钟形式配置"
    assert int(m.group(1)) * 60 == alerts.CYCLE_BUDGET_S


@pytest.mark.parametrize("name", ["polymarket-rebirth-collector.service",
                                  "polymarket-rebirth-collector.timer"])
def test_live_unit_matches_repo_unit(name):
    """本机生效副本 与 仓库副本 不许漂移。

    两边分居时,「改了一边忘了另一边」是必然而非偶然 —— 本次就是这么发生的。
    仓库副本是权威(重装会覆盖本机),但本机跑的是本机那份:**只有两边一致,
    「我验证过它在跑」和「它下次还会这么跑」才同时成立。**
    """
    live = LIVE_SYSTEMD_DIR / name
    if not live.exists():
        pytest.skip("本机未安装该单元(CI/他机)")
    assert live.read_text() == (REPO_SYSTEMD_DIR / name).read_text(), \
        f"{name}:本机与仓库副本不一致 —— 重装会把本机改动冲掉"


# ---------- 阈值必须高于实测健康分布(防洪:稳态静默那一头) ----------

def test_slow_line_above_observed_healthy_max():
    """告警线必须高于实测健康 max(228s),否则正常波动就误报。"""
    assert alerts.slow_cycle_threshold_s() > OBSERVED_HEALTHY_MAX_S


def test_slow_line_has_real_headroom_over_p99():
    """要有真余量(≥2 倍 p99),不是刚好压线 —— 压线阈值等于把噪声当信号。"""
    assert alerts.slow_cycle_threshold_s() >= 2 * OBSERVED_HEALTHY_P99_S


def test_slow_line_below_budget():
    """告警线必须**低于**预算,否则等它响时周期已经被杀了(讣告不是预警)。"""
    assert alerts.slow_cycle_threshold_s() < alerts.CYCLE_BUDGET_S


def test_observed_healthy_cycles_are_all_silent():
    """把实测健康分位数逐个喂进去,一条都不许触发(稳态完全静默)。"""
    for dur in (115, 167, 182, OBSERVED_HEALTHY_P99_S, OBSERVED_HEALTHY_MAX_S):
        assert not alerts.is_slow_cycle(dur), f"{dur}s 属实测健康区间,不该判为慢"


# ---------- 真异常必推那一头 ----------

def test_degraded_cycle_is_flagged():
    """08-04 退化期的量级(3 倍于健康)必须判为慢。"""
    assert alerts.is_slow_cycle(3 * OBSERVED_HEALTHY_MAX_S)


def test_cycle_at_budget_is_flagged():
    """跑满预算 = 正要被杀,必须判为慢。"""
    assert alerts.is_slow_cycle(alerts.CYCLE_BUDGET_S)


# ---------- 防洪:单发静默,持续才推 ----------

def test_single_slow_cycle_does_not_alert():
    """单个慢周期 = 网络抖一下,自愈噪声,不推(否则又是 1340 条洪水)。"""
    counts = {"cycle_seconds": alerts.CYCLE_BUDGET_S, "slow_cycle_streak": 1}
    assert not alerts.maybe_alert(counts)


def test_sustained_slowness_alerts(monkeypatch):
    """连续 N 轮持续慢 = 真退化,必须推。"""
    sent = {}
    monkeypatch.setattr(alerts, "_send", lambda m: sent.setdefault("body", m) or True)
    counts = {"cycle_seconds": alerts.CYCLE_BUDGET_S,
              "slow_cycle_streak": alerts.SLOW_CYCLE_ALERT_CYCLES}
    assert alerts.maybe_alert(counts)
    assert "周期耗时" in sent["body"]


def test_alert_repeats_only_on_multiples(monkeypatch):
    """退化持续期间按整数倍复述(每小时一条),不是每轮一条。"""
    monkeypatch.setattr(alerts, "_send", lambda m: True)
    n = alerts.SLOW_CYCLE_ALERT_CYCLES
    base = {"cycle_seconds": alerts.CYCLE_BUDGET_S}
    assert alerts.maybe_alert({**base, "slow_cycle_streak": n})
    assert not alerts.maybe_alert({**base, "slow_cycle_streak": n + 1})
    assert alerts.maybe_alert({**base, "slow_cycle_streak": 2 * n})


# ---------- 连续计数推进规则(纯函数) ----------

def test_streak_increments_on_slow():
    assert cycle_state.next_hit_streak(3, hit=True) == 4


def test_streak_resets_on_healthy_cycle():
    """一旦跑回健康速度就归零 —— 不许把历史慢轮次攒着凑数触发。"""
    assert cycle_state.next_hit_streak(9, hit=False) == 0
