#!/usr/bin/env python3
"""判据焊死:发现层对「被限流」和「预算用尽」必须与轮询层同治(2026-08-06)。

## 由来(code review 挖出 + 日志实测,非假想)

项目里有**两份 `_get`**,对同一个 HTTP 状态码的判断不一样:

| | 429 重试? | 429 计数? | 归因(耗尽时) |
|---|---|---|---|
| `collector_core._get`(轮询链路) | ✅ 退避 3s 重试 | ✅ `rate_limit_hits` | ✅ 分「限流打满」vs「隧道坏了」 |
| `discovery_service._get`(发现/注册链路) | ❌ 一次就返回 | ❌ 完全不计 | ❌ 无从归因 |

发现层那份的注释写着「4xx = 对方明确答复,不是网络断 → 不重试、不计」。
**但 429 不是「对方明确答复」,是「对方让你等会儿再来」。**
400 是柜台说"查无此人",429 是柜台说"现在人太多,等五分钟" —— 被当成同一句话听了。

实测(2026-08-06 查 1791 轮日志):**7 轮真的撞了限流,其中一轮撞了 15 次**。
所以 429 不是假想敌,只是罕见。而这 7 次**全部由轮询链路报出** ——
因为只有它计数。发现层被限流过几次,我们**无从知道**。
对着 CLAUDE.md 那一问「如果它现在就是坏的,我看到的会有什么不同?」——
答案是**没有任何不同**。

## 三个下游后果(本文件逐条焊死)

1. **采样翻页**:`sample_firehose` 只专门认了 400(offset 硬顶),其余任何 4xx
   都掉进 `not isinstance(d, list)` → `covered = True` → **缺口计数写 0 = 有洞而心跳全绿**。
   ⚠️ 这是同一个病的**第四次**发作(08-03 结算断供 / 08-04 网络零计数 /
   08-04 硬顶被当翻到底)。前三次都是**逐个状态码打补丁**,所以第四次照样发生。
   故本轮不再加白名单,而是**把判断倒过来**:只有确认拿到 list 才可能算翻到底。
2. **查市场**:`_lookup_gamma` 429 时返回 `None`,调用方当成"这个市场查不到"→
   `attempts += 1` 沉底 → 攒够 `MAX_ATTEMPTS` 被**永久丢弃**。
   而 `registration_backlog` 的设计文档里我自己写着:「被预算跳过 ≠ 尝试失败……
   否则『系统忙了几轮』会被当成『这东西有问题』而沉底」——
   **被限流恰恰就是「系统忙」的另一种形式,却从旁边绕过去了。**
3. **告警归因**:`alerts` 判断"慢是不是限流引起的"只看 `rate_limit_give_up_count`,
   而它只有轮询链路会写 → 发现层被限流限死,告警仍会说"不像限流",把人指错方向。

## 顺带还的债 1:时间闸打不穿单次重试风暴

时间闸只在**工作单元之间**看钟,`_get` 内部的重试循环完全不知道外面还剩多少预算:

- 单次 `_get` 最坏 = `tries(5) × timeout(25s) + 4 × sleep(1.2s)` ≈ **130s** > firehose 闸 **90s**
- `_lookup_gamma` 顺序试两个后缀且**不共享 deadline** → 单个市场最坏 ≈ **260s** > 注册闸 **180s**

即"三道闸保证周期有界"这句话在重试风暴下是假的。真兜底只有 systemd `TimeoutStartSec=900`
硬杀 —— 那正是 08-04 被杀 12 轮的死法,只是触发条件从"持续退化"降级成"一次不巧的抖动"。

## 计数器粒度的判据:按**处置方向**分,不是越细越好

新增 `firehose_rate_limited_count`(压频)和 `firehose_http_error_count`(未知形态,兜底),
因为这两种停法的处置与既有三种都不同。
而 deadline 命中**不新增计数器** —— 它的处置("加预算/降间隔")与既有的时间闸完全相同,
分开计不会带来任何新的动作,只会稀释信号。

## 本测试**不能**回答什么

1. **不回答阈值定多少。** 限流实测只有 7/1791 轮,样本撑不起任何比率红线。
   本轮只焊死「必须重试、必须计数、必须可见、必须别当翻到底」。
   429 比率的告警线留到 08-11 攒够心跳分布后定(与慢周期线同批)。
2. **不回答发现层历史上被限流过几次。** 那件事**永久不可知** —— 因为当时没有计数器,
   而日志里也不留痕。本判据只保证**从今往后**可知。
3. **不证明重试风暴在真机上发生过。** 上界是从代码常量算出来的(推的),
   不是实测跑出来的;判据焊的是"上界必须小于闸",不是"它曾经溢出过"。
"""
import json
import ssl
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import collector_core as cc  # noqa: E402
import discovery_service as ds  # noqa: E402
import registration_backlog as rb  # noqa: E402
import storage_engine as se  # noqa: E402

from urllib.error import HTTPError  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ds.time, "sleep", lambda *_: None)
    monkeypatch.setattr(cc.time, "sleep", lambda *_: None)


# ============ 判据组 A:429 必须重试 + 计数 + 归因 ============

def test_rate_limit_is_retried_not_answered_once(monkeypatch):
    """⭐429 是"等会儿再来",不是"查无此人" —— 必须重试满 tries,不许一次就放弃。

    改之前:`return {"__http__": 429}` 直接走人,一次重试都没有。
    """
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    monkeypatch.setattr(ds, "urlopen", _always_fail(_http(429)))
    ds._get("http://x", net=net)
    assert net["net_attempt_count"] == 5, "429 一次就返回了,没重试"


def test_rate_limit_hits_are_counted(monkeypatch):
    """每次撞限流都要留痕,否则"发现层被限流了吗"这个问题永远无法回答。"""
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    monkeypatch.setattr(ds, "urlopen", _always_fail(_http(429)))
    ds._get("http://x", net=net)
    assert net["rate_limit_hits"] == 5


def test_rate_limit_exhaustion_is_not_blamed_on_network(monkeypatch):
    """全 429 打满 = 被限流,**不是**隧道坏了。两者处置相反(压频 vs 查代理)。

    与 collector_core 已有的同名判据是同一条 —— 焊的是同一个病在另一条链路上的复发。
    """
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    monkeypatch.setattr(ds, "urlopen", _always_fail(_http(429)))
    ds._get("http://x", net=net)
    assert net["rate_limit_give_up_count"] == 1
    assert net["net_give_up_count"] == 0, "限流被记成了网络故障 → 告警会让人去查代理"


def test_mixed_failures_are_blamed_on_the_network(monkeypatch):
    """混合失败(既有 429 又有 SSL 错)归到网络 —— 与 collector_core 的归因规则一致。

    理由:隧道坏了会**同时**表现为限流和网络错;而纯限流不会伴随 SSL 错。
    故"有网络错"是更强的证据,优先级更高。
    """
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    seq = [_http(429), ssl.SSLError("boom")] * 3
    monkeypatch.setattr(ds, "urlopen", _fail_sequence(seq))
    ds._get("http://x", net=net)
    assert net["net_give_up_count"] == 1
    assert net["rate_limit_give_up_count"] == 0


def test_real_4xx_still_returns_immediately(monkeypatch):
    """反面焊死:400/404 **确实**是对方明确答复,不许因为修 429 就把它们也改成重试。

    把这条去掉的话,"429 要重试"很容易被过度泛化成"所有 4xx 都重试",
    于是撞 offset 硬顶时每页白等 5 次 × 25s —— 用一个新病换掉旧病。
    """
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    monkeypatch.setattr(ds, "urlopen", _always_fail(_http(400)))
    assert ds._get("http://x", net=net) == {"__http__": 400}
    assert net["net_attempt_count"] == 1, "400 不该重试"
    assert net.get("rate_limit_hits", 0) == 0


def test_both_get_implementations_agree_on_429(monkeypatch):
    """⭐⭐根因判据:同一个项目里两份 `_get` 对同一个状态码的判断必须一致。

    这条焊的不是某个具体行为,而是**"下次别又只改一份"**。
    整件事的根因就是 08-04 修 429 归因时只改了 `collector_core`,
    而 `discovery_service` 那份并行实现没跟上 —— 而且没有任何判据会因此变红。
    """
    ds_net, cc_net = {}, cc.new_counters()
    monkeypatch.setattr(ds, "urlopen", _always_fail(_http(429)))
    monkeypatch.setattr(cc, "urlopen", _always_fail(_http(429)))
    ds._get("http://x", net=ds_net)
    cc._get("http://x", cc_net)
    for k in ("net_attempt_count", "rate_limit_give_up_count", "net_give_up_count"):
        assert ds_net.get(k, 0) == cc_net.get(k, 0), \
            f"两份 _get 对 429 的 {k} 不一致:ds={ds_net.get(k)} cc={cc_net.get(k)}"


# ============ 判据组 B:只有「确认拿到 list」才可能算翻到底 ============
#
# ⭐形状说明:下面第一条是**参数化的全称判据**,不是逐个状态码的白名单。
# 前三次发作都是打补丁(补 GIVE_UP、补 400),第四次(429/403/422)照样中招。
# 把判断倒过来焊死,才能挡住第五次 —— 包括今天还不存在的状态码。

_NON_LIST_RESPONSES = [
    pytest.param({"__http__": 429}, id="429-限流"),
    pytest.param({"__http__": 403}, id="403-禁止"),
    pytest.param({"__http__": 422}, id="422-参数错"),
    pytest.param({"__http__": 401}, id="401-未授权"),
    pytest.param({"__http__": 418}, id="418-今天还不存在的状态码"),
    pytest.param({"__http__": ds.RETRY_EXHAUSTED}, id="重试耗尽"),
    pytest.param({"__http__": 400}, id="400-offset硬顶"),
    pytest.param({}, id="空 dict"),
    pytest.param(None, id="None"),
    pytest.param("", id="空字符串"),
]


@pytest.mark.parametrize("resp", _NON_LIST_RESPONSES)
def test_only_a_confirmed_empty_list_marks_covered(monkeypatch, resp):
    """⭐⭐核心:**任何**非 list 的返回都不许被当成"翻到底了"。

    `covered=True` 直接决定"本轮有没有覆盖缺口",它一旦为真,缺口计数写 0
    → 有洞而心跳全绿。这是 08-03/08-04 那个病的同一张脸。
    """
    monkeypatch.setattr(ds, "_get", lambda url, **kw: resp)
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds.sample_firehose(limit=5000, since_ts=1_000_000, net=net)
    assert net.get("firehose_gap_uncovered_count") == 1, \
        f"{resp!r} 被当成了「确认翻到底」→ 缺口静默"


def test_confirmed_empty_list_does_mark_covered(monkeypatch):
    """防洪的另一头:真的翻到底(空 list)就必须**完全静默**。

    只焊上面那条会让这个计数天天非零 = 没信号。两头都要焊死。
    """
    monkeypatch.setattr(ds, "_get", lambda url, **kw: [])
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds.sample_firehose(limit=5000, since_ts=1_000_000, net=net)
    assert net.get("firehose_gap_uncovered_count") == 0


def test_rate_limited_paging_has_its_own_counter(monkeypatch):
    """限流打断分页 → 专属计数(处置是压频,与"查隧道"/"降间隔"都不同)。"""
    monkeypatch.setattr(ds, "_get", lambda url, **kw: {"__http__": ds.RATE_LIMITED})
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds.sample_firehose(limit=5000, since_ts=1_000_000, net=net)
    assert net.get("firehose_rate_limited_count") == 1
    assert net.get("firehose_truncated_count") == 0, "限流不是隧道坏了"
    assert net.get("firehose_offset_ceiling_count") == 0, "限流不是撞硬顶"


def test_unknown_http_error_is_counted_not_silent(monkeypatch):
    """未知形态的 4xx 必须有兜底计数 —— 否则"没见过的错"= 完全无声。

    这个计数器稳态恒 0;它一旦非零就说明**出现了我们没想到的东西**,
    那正是最该被看见的时刻。
    """
    monkeypatch.setattr(ds, "_get", lambda url, **kw: {"__http__": 403})
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds.sample_firehose(limit=5000, since_ts=1_000_000, net=net)
    assert net.get("firehose_http_error_count") == 1


def test_pages_collected_before_the_stop_are_kept(monkeypatch):
    """截断前已抓到的成交是有效数据,必须保留(诚实截断,不是丢批)。"""
    pages = [_page(1_000_000, n=1000), {"__http__": 429}]
    monkeypatch.setattr(ds, "_get", lambda url, **kw: pages.pop(0))
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    out = ds.sample_firehose(limit=5000, since_ts=1, net=net)
    assert len(out) == 1000, "限流把已经拿到的那一页也丢了"


# ============ 判据组 C:被限流 ≠ 这个市场查不到(别污染积压) ============

def _wire(monkeypatch, registry: dict, firehose):
    """把 refresh_and_registry 的外部依赖换成假的,只留真逻辑跑。"""
    monkeypatch.setattr(ds, "sample_firehose", lambda *a, **k: firehose())
    monkeypatch.setattr(ds, "load_registry", lambda *a, **k: dict(registry))
    monkeypatch.setattr(ds, "newest_trade_ts", lambda *a, **k: 1)
    monkeypatch.setattr(ds, "_atomic_write_parquet", lambda *a, **k: None)
    monkeypatch.setattr(ds, "parse_market", lambda m: {"condition_id": m["slug"]})


def _cid(i: int) -> str:
    return "0x" + f"{i:064x}"


def test_rate_limited_lookup_does_not_sink_the_market(tmp_path, monkeypatch):
    """⭐⭐端到端:被限流时 `attempts` **不许 +1**。

    积压模块的设计文档白纸黑字写着「被预算跳过 ≠ 尝试失败……否则『系统忙了几轮』
    会被当成『这东西有问题』而沉底」。被限流就是"系统忙"的另一种形式。
    攒够 MAX_ATTEMPTS 会把一个**真实存在的市场永久丢弃**,而它只是撞上了限流。
    """
    p = tmp_path / "backlog.json"
    trades = [{"conditionId": _cid(1), "slug": "m-1", "title": "M1"}]
    _wire(monkeypatch, {}, lambda: trades)
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **kw: ds.INCONCLUSIVE)

    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds.refresh_and_registry(max_new=10, net=net, backlog_file=p)
    bl = rb.load(p)
    assert _cid(1) in bl, "被限流的市场直接从积压里消失了"
    assert bl[_cid(1)]["attempts"] == 0, "被限流被记成了「这个市场有问题」→ 会沉底并最终被丢弃"


def test_genuine_not_found_does_sink_the_market(tmp_path, monkeypatch):
    """⭐反面焊死:**真的**查不到时 `attempts` 必须 +1。

    只焊上面那条的话,最省事的"修法"是让所有失败都不 +1 —— 于是死号永远不沉底、
    永远堵在队头,新市场永远轮不到。那是用一个更糟的饿死换掉这个病(积压设计文档第 1 条)。
    """
    p = tmp_path / "backlog.json"
    trades = [{"conditionId": _cid(1), "slug": "m-1", "title": "M1"}]
    _wire(monkeypatch, {}, lambda: trades)
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **kw: None)   # 确认查不到

    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds.refresh_and_registry(max_new=10, net=net, backlog_file=p)
    bl = rb.load(p)
    assert bl[_cid(1)]["attempts"] == 1, "确认查不到却没沉底 → 死号会堵死队头"


def test_inconclusive_lookup_is_counted(tmp_path, monkeypatch):
    """"没查成"必须出声 —— 否则它与"查不到"在心跳里长得一模一样。"""
    p = tmp_path / "backlog.json"
    trades = [{"conditionId": _cid(i), "slug": f"m-{i}", "title": "M"} for i in range(3)]
    _wire(monkeypatch, {}, lambda: trades)
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **kw: ds.INCONCLUSIVE)

    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds.refresh_and_registry(max_new=10, net=net, backlog_file=p)
    assert net.get("register_inconclusive_count") == 3


def test_inconclusive_is_not_counted_as_register_fail(tmp_path, monkeypatch):
    """"没查成"不许混进 `register_fail`。

    `register_fail` 喂的是「注册连零守护」那条告警;把限流混进去 =
    告警说"注册失败率高",而真相是"被限流了",处置方向完全不同。
    """
    p = tmp_path / "backlog.json"
    trades = [{"conditionId": _cid(1), "slug": "m-1", "title": "M1"}]
    _wire(monkeypatch, {}, lambda: trades)
    monkeypatch.setattr(ds, "_lookup_gamma", lambda slug, **kw: ds.INCONCLUSIVE)

    _, stats = ds.refresh_and_registry(max_new=10, net={}, backlog_file=p)
    assert stats["register_fail"] == 0


def test_lookup_returns_inconclusive_on_rate_limit(monkeypatch):
    """单元层:`_lookup_gamma` 撞限流必须报"没查成",不许报"查不到"。"""
    monkeypatch.setattr(ds, "_get", lambda url, **kw: {"__http__": ds.RATE_LIMITED})
    assert ds._lookup_gamma("s", net={}) is ds.INCONCLUSIVE


def test_lookup_returns_inconclusive_on_retry_exhaustion(monkeypatch):
    """网络重试耗尽同理 —— 也是"没查成",不是"这个市场不存在"。"""
    monkeypatch.setattr(ds, "_get", lambda url, **kw: {"__http__": ds.RETRY_EXHAUSTED})
    assert ds._lookup_gamma("s", net={}) is ds.INCONCLUSIVE


def test_lookup_returns_none_on_confirmed_absence(monkeypatch):
    """反面:两个后缀都确认返回空 list = 真的查不到,必须报 None(要沉底的那种)。"""
    monkeypatch.setattr(ds, "_get", lambda url, **kw: [])
    assert ds._lookup_gamma("s", net={}) is None


# ============ 判据组 D:时间闸必须能打穿重试风暴(债 1) ============

class _Clock:
    """可控单调钟。真实钟没法在测试里制造"一次请求耗时 25 秒"。"""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_get_does_not_start_a_new_try_past_the_deadline(monkeypatch):
    """预算已经用尽时,连**下一次重试都不许发起**。

    改之前:`_get` 内部的重试循环完全不看钟,外面的时间闸只在两次调用**之间**判 ——
    于是一次调用就能把整段预算打穿。
    """
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)

    def slow_fail(req, timeout=None):
        clock.t += 10.0
        raise ssl.SSLError("boom")

    monkeypatch.setattr(ds, "urlopen", slow_fail)
    net = cc.new_counters()   # 与生产同形:所有键预初始化,缺键会直接 KeyError 而不是被兜成 0
    ds._get("http://x", net=net, deadline=25.0)
    assert net["net_attempt_count"] == 3, \
        f"预算 25s、每次 10s,最多发 3 次;实际发了 {net['net_attempt_count']} 次"


def test_get_shrinks_socket_timeout_to_the_remaining_budget(monkeypatch):
    """单次 socket 超时必须被剩余预算夹住。

    只在重试之间看钟还不够:剩 3 秒时仍用 25 秒超时,**一次请求**就超预算 22 秒。
    """
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)
    seen = []

    def record(req, timeout=None):
        seen.append(timeout)
        raise ssl.SSLError("boom")

    monkeypatch.setattr(ds, "urlopen", record)
    ds._get("http://x", net={}, deadline=3.0)
    assert seen[0] <= 3.0, f"剩 3 秒却用了 {seen[0]} 秒的超时"


def test_get_without_deadline_keeps_the_old_behaviour(monkeypatch):
    """向后兼容:不传 deadline 时行为不变(手工试跑/独立脚本仍能用)。"""
    seen = []

    def record(req, timeout=None):
        seen.append(timeout)
        raise ssl.SSLError("boom")

    monkeypatch.setattr(ds, "urlopen", record)
    ds._get("http://x", net={})
    assert len(seen) == 5 and seen[0] == 25


def test_lookup_gamma_shares_one_deadline_across_both_suffixes(monkeypatch):
    """⭐`_lookup_gamma` 顺序试两个后缀,两次必须**共享同一个 deadline**。

    各给满预算的话,单个市场最坏 = 2 × 130s ≈ 260s > 注册闸 180s ——
    "闸保证有界"这句话在这里就是假的。

    ⚠️ 判据形状说明:第一个后缀必须**确认返回空 list**(而不是失败)。
    若两个后缀都失败,`_lookup_gamma` 会在第一个就短路返回 INCONCLUSIVE,
    于是"只发了 3 次请求"这个观察**与共享 deadline 无关** —— 判据变成同义反复。
    只有走到第二个后缀,才真的在检验预算有没有被重新拿满。
    """
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)
    calls = []

    def by_suffix(req, timeout=None):
        calls.append(req.full_url)
        clock.t += 10.0
        if "closed=true" not in req.full_url:
            return _Resp([])           # 后缀一:确认没有 → 必须继续试后缀二
        raise ssl.SSLError("boom")     # 后缀二:网络烂,会重试

    monkeypatch.setattr(ds, "urlopen", by_suffix)
    ds._lookup_gamma("s", net={}, deadline=25.0)
    # 精确算:共享 deadline → 1(后缀一,t=0→10)+ 2(后缀二只剩 15s:t=10→20→30)= **3**
    #         各自重开预算  → 1 + 3~5 = **4 起步**
    # ⚠️ 这里必须写死 3 而不是"<= 4":变异测试实测「各自重开 25s 预算」正好落在 4,
    #    容差写 <=4 时这条判据**一次都不会红** —— 松断言等于没在验证(CLAUDE.md 第 1 条)。
    assert len(calls) == 3, \
        f"两个后缀各自重新拿满预算了(总共发了 {len(calls)} 次请求,共享 deadline 应是 3 次)"


def test_sample_firehose_is_bounded_under_a_retry_storm(monkeypatch):
    """⭐⭐端到端:重试风暴下,采样段总耗时必须真的被预算夹住。

    这条才是"周期有界"的判据。前面几条都是零件,只有这条证明装上车了。
    容差 = 一次请求的最坏耗时(闸只能在请求边界生效,不能腰斩正在飞的请求)。
    """
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)

    def slow_fail(req, timeout=None):
        clock.t += (timeout or 25)
        raise ssl.SSLError("boom")

    monkeypatch.setattr(ds, "urlopen", slow_fail)
    ds.sample_firehose(limit=11000, since_ts=1, net={}, time_budget_s=90)
    assert clock.t <= 90 + 25, f"采样段跑了 {clock.t}s,预算只有 90s"


def test_register_is_bounded_under_a_retry_storm(monkeypatch):
    """⭐⭐同上,注册段。`_lookup_gamma` 每个市场两次请求,最容易穿底的就是这里。"""
    clock = _Clock()
    monkeypatch.setattr(ds.time, "monotonic", clock)

    def slow_fail(req, timeout=None):
        clock.t += (timeout or 25)
        raise ssl.SSLError("boom")

    monkeypatch.setattr(ds, "urlopen", slow_fail)
    stubs = {_cid(i): {"slug": f"m-{i}"} for i in range(50)}
    ds.register_new_markets(stubs, max_new=None, net={}, time_budget_s=180)
    assert clock.t <= 180 + 25, f"注册段跑了 {clock.t}s,预算只有 180s"


def test_naked_retry_storm_would_still_blow_the_budget_without_a_deadline():
    """⭐焊死本判据组的**前提**:裸重试上界确实大于最紧的闸。

    `tries × timeout + sleep` ≈ 130s > firehose 闸 90s —— 正因为如此,
    "在两次调用之间看钟"才不足以让周期有界,deadline 才是必需的。

    这条不是重复上面几条,而是**防止前提被悄悄抽掉**:
    如果有人把 `tries` 调到 2、`timeout` 调到 10,裸上界降到 ~21s < 90s,
    上面那些端到端判据就算删掉 deadline 也照样绿 —— 它们会变成同义反复而无人知晓。
    那时这条会红,逼人重新面对"deadline 还需要吗"这个问题。
    """
    import run_cycle as rc
    tightest = min(rc.FIREHOSE_TIME_BUDGET_S, rc.REGISTER_TIME_BUDGET_S)
    worst_single = ds.TRIES * ds.TIMEOUT_S + (ds.TRIES - 1) * ds.RETRY_SLEEP_S
    assert worst_single > tightest, (
        "前提失效:单次 _get 的裸上界已经小于最紧的闸,本判据组失去意义 —— "
        "说明有人改了 tries/timeout,请重新审视 deadline 是否还必要")


# ============ 判据组 E:计数必须活到心跳(否则等于没计) ============

def test_new_counters_reach_the_heartbeat():
    """只加 counter 不进心跳 = 进程一退就蒸发,事后照样无从归因。"""
    for k in ("firehose_rate_limited_count", "firehose_http_error_count",
              "register_inconclusive_count", "rate_limit_hits",
              "rate_limit_give_up_count"):
        assert k in se.AUDIT_FIELDS, f"{k} 没进心跳字段,计了也留不下"
        assert k in cc.new_counters(), f"{k} 没进 COUNTER_KEYS,主干不会初始化它"


def test_steady_state_prints_nothing_about_rare_stops():
    """防洪一头:稳态(全零)必须**完全静默**。

    天天一行"稀有停法: 0 0 0"= 噪音,而噪音会让真信号无处可显 ——
    降噪不是体验优化,是可靠性工作。
    """
    assert cc.abnormal_stop_note(cc.new_counters()) is None


@pytest.mark.parametrize("key", sorted(cc.ABNORMAL_STOPS))
def test_every_rare_stop_really_gets_reported(key):
    """防洪另一头:每一种稀有停法真发生时都**必须**出声,且报得出是哪一种。

    逐个参数化而不是只测一个 —— 否则"漏了其中一种"这件事不会有任何判据变红。
    """
    counters = cc.new_counters()
    counters[key] = 3
    note = cc.abnormal_stop_note(counters)
    assert note and "×3" in note, f"{key} 非零却没出声"


def test_rare_stop_keys_are_all_real_counters():
    """报表里的键必须真的是计数器 —— 否则打错一个字就永远静默,而没人会发现。"""
    for k in cc.ABNORMAL_STOPS:
        assert k in cc.new_counters(), f"{k} 不是真计数器 → 这一路永远不会出声"
        assert k in se.AUDIT_FIELDS, f"{k} 不落 parquet = 事后无从复核"


def test_discovery_net_stats_initialises_the_rate_limit_keys():
    """独立跑发现层时(new_net_stats)也要有这些键 ——
    「0 也要写」:键缺失与"真的是 0"长得一模一样,那正是要消灭的模糊。"""
    s = ds.new_net_stats()
    assert s["rate_limit_hits"] == 0
    assert s["rate_limit_give_up_count"] == 0


# ============ 测试替身 ============

class _Resp:
    """urlopen 的返回替身(上下文管理器 + read())。"""

    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code):
    return HTTPError("http://x", code, "boom", {}, None)


def _always_fail(exc):
    def f(req, timeout=None):
        raise exc
    return f


def _fail_sequence(excs):
    seq = list(excs)

    def f(req, timeout=None):
        raise seq.pop(0) if seq else excs[-1]
    return f


def _page(newest_ts, n=1000, step=1):
    return [{"conditionId": _cid(i), "slug": f"s{i}", "title": "t",
             "timestamp": newest_ts - i * step} for i in range(n)]
