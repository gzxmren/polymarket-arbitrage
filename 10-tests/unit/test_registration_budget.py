#!/usr/bin/env python3
"""判据焊死:注册预算 —— 上限必须出声、必须有时间闸、`0` 必须真的是零(2026-08-04)。

## 由来(实测,非假想)

`DEFAULT_MAX_NEW = 40`,而实测**每轮涌入 90~92 个**新市场(活跃 616 / 已注册 57730)。
于是 `new_registered` 逐轮 33/34/32/38/34/36/38/36/31/29/32/36 ——
**这个"恒定不变的计数"不是自然产出,是被上限削平的**
(CLAUDE.md 静默失败清单第 4 条点名的信号)。

注册滞后实测(只取采集器运行期间才诞生的市场,排掉"老市场变热"混淆):
**p50=94 分钟(6 个周期)/ p90=17.8 小时 / 只有 30% 在一轮内注册上。**

而旧代码对"没轮到的那 50 个"**一个字都没说** —— 正是清单第 3 条
(「每轮取前 N 个」必须能回答「第 N+1 个何时轮到」)的原形。

## ⭐ 一个方向反了的 bug(实测)

`run_cycle.py` 注释写着「可传参覆盖(**0=不注册新市场**)」,而实现是:

    if max_new and i >= max_new:   # max_new=0 → `0 and ...` → 假 → 永不 break

→ 传 `0` 的实际效果是**取消上限、全量注册**,与文档写的正好相反。
这是最危险的一类:**急刹车踩下去是全油门**。冷启动时想"这轮先别注册"用它,
会直接把周期撑爆(实测冷启动 register 112 个即 >10 分钟)。

## 为什么加"时间闸"而不是只把 40 调大

发现层耗时 ≈ 常数 + `max_new` × (一次 Gamma 往返),而**那个往返时长不由我们决定**:
实测同一天里从 ~1.2s 漂到 1.90s(代理隧道退化,见 collector-proxy-degradation)。
纯计数上限在延迟翻倍时会让周期跟着翻倍 → 撞 systemd 超时被杀
(2026-08-04 当天已经真的发生过 12 次)。

时间闸让它**降级而不是崩**:延迟变差时本轮少注册几个(并出声计数),
下轮继续 —— 而不是整轮被杀、连已干完的活一起丢。

## 本测试**不能**回答什么

不回答「积压多久能清空」。那取决于新市场到达率(实测 ~90/轮,但只有一天的样本),
和 Gamma 延迟(剧烈抖动、无稳态基线)。本轮只焊死**机制**:
上限可配、超限必出声、时间闸真能刹住、`0` 真的是零。
到达率与清空速度等心跳攒够一周后再回来定(与网络重试率阈值同批)。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import discovery_service as ds  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """铁律:测试**绝不**碰生产注册表。写入重定向到 tmp,并且断言真没写出去。"""
    monkeypatch.setattr(ds, "REGISTRY_DIR", tmp_path / "registry")
    written = []
    monkeypatch.setattr(ds, "_atomic_write_parquet",
                        lambda tbl, dest: written.append(dest))
    monkeypatch.setattr(ds.time, "sleep", lambda *_: None)
    return written


def _stubs(n, prefix="m"):
    return {f"0x{i:064x}": {"slug": f"{prefix}-{i}"} for i in range(n)}


def _fake_market(slug):
    """一个能通过 parse_market 的最小 Gamma 市场对象。"""
    return {"conditionId": "0x" + "ab" * 32, "slug": slug, "question": "Q?",
            "clobTokenIds": '["1","2"]', "outcomes": '["Yes","No"]', "closed": False}


# ---------- 判据 1:没轮到的必须出声 ----------

def test_skipped_markets_are_counted(monkeypatch):
    """⭐核心:「每轮取前 N 个」必须能回答「第 N+1 个何时轮到」—— 起码得先说有几个没轮到。"""
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **kw: _fake_market(slug))
    net = {}
    ds.register_new_markets(_stubs(90), max_new=40, net=net)
    assert net.get("register_budget_skipped_count") == 50


def test_no_skip_no_count():
    """防洪的另一头:活干完了就必须**完全静默**(计数为 0),否则这个量天天非零=没信号。"""
    net = {}
    ds.register_new_markets({}, max_new=40, net=net)
    assert net.get("register_budget_skipped_count") == 0


def test_skipped_count_reaches_heartbeat():
    import storage_engine as se
    import collector_core as cc
    assert "register_budget_skipped_count" in cc.new_counters()
    assert "register_budget_skipped_count" in se.AUDIT_FIELDS, "不落 parquet = 等于没计"


# ---------- 判据 2:`0` 必须真的是零 ----------

def test_zero_max_new_registers_nothing(monkeypatch):
    """⭐ 文档写「0=不注册新市场」,实现就必须是零 —— 而不是反过来取消上限。"""
    calls = []
    monkeypatch.setattr(ds, "_lookup_gamma",
                        lambda slug, **kw: calls.append(slug) or _fake_market(slug))
    net = {}
    n_reg, n_fail = ds.register_new_markets(_stubs(20), max_new=0, net=net)
    assert calls == [], "max_new=0 仍然发起了 Gamma 查询 —— 急刹车变成了全油门"
    assert n_reg == 0
    assert net.get("register_budget_skipped_count") == 20, "全跳过了却不出声"


def test_none_max_new_means_unlimited(monkeypatch):
    """`None` 才是「不设上限」。两个语义必须分开,否则没法表达"这轮别注册"。"""
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **kw: _fake_market(slug))
    n_reg, _ = ds.register_new_markets(_stubs(7), max_new=None, net={})
    assert n_reg == 7


# ---------- 判据 3:时间闸 ----------

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_time_budget_stops_the_loop(monkeypatch):
    """延迟变差时必须**降级**(少注册几个),而不是把整轮拖到被 systemd 杀掉。"""
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)

    def slow_lookup(slug, **kw):
        clock.t += 10.0          # 每个查询"耗时"10 秒
        return _fake_market(slug)

    monkeypatch.setattr(ds, "_lookup_gamma", slow_lookup)
    net = {}
    n_reg, _ = ds.register_new_markets(_stubs(100), max_new=100,
                                       time_budget_s=50, net=net)
    assert n_reg == 5, f"时间闸没刹住:预算 50s / 每个 10s,应只做 5 个,实际 {n_reg}"
    assert net.get("register_budget_skipped_count") == 95


def test_time_budget_is_checked_before_the_lookup(monkeypatch):
    """必须**查询前**判,不能查完再判 —— 否则总会超出预算一整个请求的时长。

    在代理退化(单次 6s+)时,"多做一个"就是多 6 秒,这正是被杀那 12 轮的构成方式。
    """
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)

    def slow_lookup(slug, **kw):
        clock.t += 30.0
        return _fake_market(slug)

    monkeypatch.setattr(ds, "_lookup_gamma", slow_lookup)
    n_reg, _ = ds.register_new_markets(_stubs(10), max_new=10, time_budget_s=30, net={})
    assert n_reg == 1, f"预算 30s、单个 30s:第 2 个开查前就该停,实际做了 {n_reg} 个"


def test_no_time_budget_means_no_time_gate(monkeypatch):
    """不传时间闸时行为不变 —— 老调用方(脚本/试跑)不受影响。"""
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)

    def slow_lookup(slug, **kw):
        clock.t += 1000.0
        return _fake_market(slug)

    monkeypatch.setattr(ds, "_lookup_gamma", slow_lookup)
    n_reg, _ = ds.register_new_markets(_stubs(4), max_new=4, net={})
    assert n_reg == 4


def test_budget_counts_attempts_not_successes(monkeypatch):
    """上限管的是**成本**(发出去的查询),不是成果。

    否则失败的市场不占额度 → 接口挂掉时会疯狂重试到超时,正是要防的那件事。
    """
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **kw: None)  # 全失败
    net = {}
    n_reg, n_fail = ds.register_new_markets(_stubs(50), max_new=10, net=net)
    assert (n_reg, n_fail) == (0, 10), "失败的市场没占额度 → 会一直查下去"
    assert net.get("register_budget_skipped_count") == 40


# ---------- 判据 4:预算必须装得进周期 ----------

def test_every_network_stage_has_a_time_gate():
    """⭐ 「周期装得下」这句话只有在**每一段都有闸**时才成立。

    反例就是我自己 2026-08-04 晚犯的:给发现层和注册层加了时间闸,就在判据里写下
    「最坏周期 = 各闸之和 + 实测其余部分」—— 而**轮询层压根没有闸**。
    代理一退化轮询就无界增长,那个"最坏"根本不是最坏,判据给的是假信心。

    (这正是 CLAUDE.md 第 1 条那句话对准判据本身:
     「如果它现在就是坏的,我看到的会有什么不同?」—— 轮询涨到 600s 时这条测试照样绿。)
    """
    import run_cycle as rc
    for name in ("FIREHOSE_TIME_BUDGET_S", "REGISTER_TIME_BUDGET_S", "POLL_TIME_BUDGET_S"):
        assert getattr(rc, name, None), f"{name} 缺失 → 该段无界 → 周期不是有界的"


def test_poll_budget_stops_and_counts(monkeypatch):
    """轮询超预算要停、要出声 —— 没轮到的市场下轮由 watermark 接着来,不丢数据。"""
    import collector_core as cc
    clock = _Clock()
    monkeypatch.setattr(cc.time, "monotonic", clock)
    monkeypatch.setattr(cc.time, "sleep", lambda *_: None)

    def slow_poll(market, counters, wm):
        clock.t += 10.0
        return []

    monkeypatch.setattr(cc, "poll_market", slow_poll)
    counters = cc.new_counters()
    markets = [{"condition_id": f"0x{i:064x}"} for i in range(50)]
    done = cc.poll_markets(markets, counters, {}, time_budget_s=50)
    assert done == 5, f"预算 50s / 每个 10s,应只轮询 5 个,实际 {done}"
    assert counters["poll_budget_skipped_count"] == 45


def test_declared_budgets_sum_under_the_alert_line():
    """各段时间闸的**声明值**之和 + 实测固定开销,必须仍在慢周期告警线以内。

    直接 import 被检验对象自己的红线(而非复制粘贴数字),改了那边这里会跟着红。

    实测固定开销(2026-08-04 晚,新配置真机两轮:499s / 453s):
      结算守望 61s(未设闸,受 checked=800 固定配额约束)+ compaction 2s + 注册表读取 3s

    ⚠️ **这条不证明周期有界** —— 它证明的是"闸的参数配得不至于稳态就天天告警"。
    真正的最坏情况见 `test_known_unbounded_segments_are_pinned`:时间闸只在**工作单元
    之间**看钟,单个 `_get` 的重试风暴和 `poll_market` 的分页循环都能打穿它。
    这个测试原名 `test_worst_case_cycle_fits_under_the_alert_line`,那个名字是过度声称
    (2026-08-05 改名):把"参数之和"说成"最坏周期",正是 CLAUDE.md 第 1 条要防的东西。
    """
    import alerts
    import run_cycle as rc

    MEASURED_FIXED_S = 61 + 2 + 3
    declared_s = (rc.FIREHOSE_TIME_BUDGET_S + rc.REGISTER_TIME_BUDGET_S
                  + rc.POLL_TIME_BUDGET_S + MEASURED_FIXED_S)
    assert declared_s < alerts.slow_cycle_threshold_s(), (
        f"闸声明值之和 {declared_s}s 已越过慢周期告警线 "
        f"{alerts.slow_cycle_threshold_s():.0f}s → 稳态就会天天告警(防洪失效)")
    assert declared_s < alerts.CYCLE_BUDGET_S, "闸声明值之和就会被 systemd 杀"


def test_known_unbounded_segments_are_pinned():
    """⭐ 把「时间闸打不穿的那部分」钉住,让它不能悄悄变大(2026-08-05 立,08-06 升级)。

    ## 为什么需要这条

    三道时间闸原本都只在**工作单元之间**看钟(翻一页 / 注册一个 / 轮询一个市场之后),
    单个工作单元内部没有任何时钟检查。而一次 `_get` 在网络全程超时的情况下是
    `tries × timeout + (tries-1) × sleep` ≈ 130s,**远大于**最小的那道闸(90s)
    —— 即"三道闸保证周期有界"这句话在重试风暴下是假的。

    ## 2026-08-06 的变化:两段已经真有界,一段仍然没有

    把 deadline 接进了 `discovery_service._get`(重试之间 + 单次 socket 超时都看钟),
    并穿透到 `sample_firehose` / `register_new_markets` / `_lookup_gamma`。
    故 **firehose 段与注册段现在是真有界的**,端到端判据见
    `test_discovery_rate_limit_and_deadline.py::test_*_bounded_under_a_retry_storm`。

    **仍然无界的两处**(本判据钉的就是它们):

    1. `collector_core.poll_market` 的 `while offset <= OFFSET_CAP` 分页循环
       **一次钟都不看** → 单市场最坏 20 × 131s ≈ 44 分钟
    2. **结算段**:`settlement_watcher` 共用 `discovery_service._get` 却**不传 deadline**,
       且整段没有任何时间闸(它靠 `checked=800` 的配额约束,不是时间)。
       ⚠️ 08-06 给 429 加重试后,这一段的最坏耗时**变大了**(限流从"立即返回"变成"重试 5 次")
       —— 改动的副作用必须写下来,而不是留在无人知晓处。

    所以「周期结构性有界」这句话**仍然是假的**,真正的兜底还是 systemd 的
    `TimeoutStartSec=900` 硬杀。这条判据不假装那个洞不存在,而是把它量出来钉住。

    ## 本判据**不能**回答什么

    不回答"最坏情况多久发生一次"(那要一周的心跳分布,见 ⏰2026-08-11 阈值校准),
    也不断言这个洞可接受 —— 只断言它**没有在无人注意时变大**。
    """
    import collector_core as cc
    import discovery_service as ds
    import run_cycle as rc
    import alerts
    import inspect
    import re

    # discovery 侧读模块常量(源码正则会在"把字面量改成变量"的那天悄悄失效 ——
    # 08-06 就真的这样红了一次,那正是这种读法该被淘汰的证据)。
    worst_disc_get = ds.TRIES * ds.TIMEOUT_S + (ds.TRIES - 1) * ds.RETRY_SLEEP_S
    tries = inspect.signature(cc._get).parameters["tries"].default
    timeout = int(re.search(r"timeout=(\d+)", inspect.getsource(cc._get)).group(1))
    worst_core_get = tries * timeout + (tries - 1) * 1.5
    assert (worst_disc_get, worst_core_get) == (129.8, 131.0), (
        f"单次 _get 最坏耗时变了(discovery {worst_disc_get}s / core {worst_core_get}s)"
        " —— 调 tries/timeout 会同步放大所有无闸段的溢出量,请重算下面几条并更新本判据")

    # --- 已经有界的两段:焊住"deadline 真的被传下去了" ---
    # 光有 `_get(deadline=...)` 这个参数不算数,得调用方真的传。
    # 这是"零件合格 ≠ 装上车"的同一条教训(08-05 栽过一次)。
    for fn in (ds.sample_firehose, ds.register_new_markets, ds._lookup_gamma):
        assert "deadline" in inspect.getsource(fn), \
            f"{fn.__name__} 没把 deadline 传进 _get → 该段又变回无界"

    # --- 仍然无界的两段:钉住"它们仍然无界"这个事实本身 ---
    # 哪天有人把这里也修了,这两条会红 → 那是好事,提醒把它们升级成真有界断言。
    assert "monotonic" not in inspect.getsource(cc.poll_market), (
        "poll_market 里出现了时钟检查 → 分页循环可能已有界,请改写本判据")
    import settlement_watcher as sw
    assert "deadline" not in inspect.getsource(sw._batch_lookup_gamma), (
        "结算段开始传 deadline 了 → 请把它也升级成真有界断言并更新本判据叙述")

    # 真正的兜底只有 systemd 硬杀,不是这些闸。这条焊死三者的一致性。
    assert alerts.CYCLE_BUDGET_S >= alerts.slow_cycle_threshold_s(), "告警线反而高过硬杀线"


def test_default_max_new_covers_measured_arrival_rate():
    """上限必须**够得着实测到达率**,否则积压只会越滚越大。

    实测到达 ~90 个/轮(2026-08-04,活跃 616 / 新市场 90~92)。
    """
    import run_cycle as rc
    MEASURED_ARRIVAL_PER_CYCLE = 90
    assert rc.DEFAULT_MAX_NEW >= MEASURED_ARRIVAL_PER_CYCLE, (
        f"上限 {rc.DEFAULT_MAX_NEW} < 实测到达 {MEASURED_ARRIVAL_PER_CYCLE}/轮 "
        f"→ 积压永远清不掉,new_registered 会继续是被削平的常数")
