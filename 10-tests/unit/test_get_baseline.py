#!/usr/bin/env python3
"""两份 `_get` 的行为基线 —— **合并前的对照标准**(2026-08-06,设计梳理第 1 步)。

## 这份判据是干什么的

项目里有**两份**"GET + 重试 + 计数"的实现:`collector_core._get` 与
`discovery_service._get`。设计梳理(docs/DESIGN_COLLECTOR_INVARIANTS.md 重复形状 #2)
认定它们必须合并,理由不是"代码重复难看",而是:

1. 两份已经悄悄分叉过一次(429 的处置),**而当时没有任何判据会因此变红**;
2. 只有一份接受"截止时刻"参数 ⇒ 另一份的调用方(单市场翻页)**结构上装不上时间闸**
   —— 那正是全系统唯一还无界的一段(洞 2)。

合并是**纯重构**:行为必须逐项不变。而"行为不变"这句话要能被检验,
就得先有一份**把现有行为钉死**的基线 —— 就是本文件。

⚠️ 铁律(CLAUDE.md 第 4 条):回归对照必须**固定数据、只变代码**。
本文件用脚本化的假传输层(`_Script`)喂固定响应序列,不碰网络 ——
所以"同一份数据"这个前提是结构上成立的,不是靠人记得别改。

## 怎么用

- **合并之前**:本文件必须全绿(它描述的就是今天的行为)。
- **合并之后**:
  - **第一~七节(行为)必须原样全绿,一条都不许改。**
    改了任何一条 = 行为变了 = 那就不是重构,必须单独说明并单独验证。
  - **第八节(差异清单)必须逐条退休** —— 每条从"记录分歧"改写成"记录决定"。
    ⚠️ 2026-08-06 补记:第一版这里写的是"全文件一条都不许改",**那句是错的** ——
    第八节里有一条断言"轮询版没有 deadline 参数",而给它装上 deadline
    恰恰是本次合并的目的。规则和目的自相矛盾时,错的通常是规则。

## 本判据**不**回答什么

- 不回答"合并后的接口长什么样"(那是下一步的事)。
- 不回答"两份的差异哪个更对"。差异在最后一节被**逐条列出来钉死**,
  合并时必须逐条给出处置(保留/统一/参数化),而不是悄悄挑一个。
"""
import http.client
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import collector_core as cc  # noqa: E402
import discovery_service as ds  # noqa: E402


# ---------- 假传输层:固定脚本,不碰网络 ----------

class _Resp:
    """urlopen 的返回值:上下文管理器 + read() → bytes。"""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Script:
    """按序回放一串"动作":dict/list = 成功返回;异常实例 = 抛出。

    同时记录每次调用拿到的 `timeout`,用来验证截止时刻有没有夹住 socket 超时。
    脚本用尽后重复最后一个动作 —— 这样"重试 5 次全失败"只需写一个动作。
    """

    def __init__(self, *actions):
        self.actions = list(actions)
        self.timeouts = []
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        act = self.actions[min(self.calls - 1, len(self.actions) - 1)]
        if isinstance(act, BaseException):
            raise act
        return _Resp(act)


@pytest.fixture
def sleeps(monkeypatch):
    """虚拟时钟:吞掉真实 sleep、记录时长,**并让它推进时钟**。

    ⭐ 为什么不能只把 sleep 换成空操作(第一版就是这么写的,当场红了一条):
    "截止时刻"这个机制**部分正是靠退避消耗预算来生效的** ——
    sleep 不推进时钟,就等于把被测对象的一半换成了替身,
    于是"重试到一半预算用尽会停手"这条永远验不到(5 次全跑满还以为是正常)。

    这是 2026-08-06 那条教训的同一个形状(判据把 `collector_is_running` 整个 mock 掉,
    四条全绿而互锁其实是坏的)。**凡是被测逻辑读的量,都不许由判据凭空提供。**
    """
    class _Rec(list):     # 普通 list 挂不上属性
        pass

    rec = _Rec()
    now = [1000.0]        # 起点随便取,只有差值有意义

    def fake_sleep(s):
        s = float(s)
        rec.append(round(s, 3))
        now[0] += s       # ← 关键:时间真的过去了

    def fake_monotonic():
        return now[0]

    # 两个模块都 `import time` 后走全局查找,故只能打全局 time 模块(monkeypatch 会还原)
    monkeypatch.setattr(cc.time, "sleep", fake_sleep)
    monkeypatch.setattr(cc.time, "monotonic", fake_monotonic)
    rec.now = now         # 个别判据要拿当前虚拟时刻来算 deadline
    return rec


def _run_cc(monkeypatch, script, counters=None, **kw):
    monkeypatch.setattr(cc, "urlopen", script)
    counters = cc.new_counters() if counters is None else counters
    return cc._get("http://x/y", counters, **kw), counters


def _run_ds(monkeypatch, script, net=None, **kw):
    monkeypatch.setattr(ds, "urlopen", script)
    net = ds.new_net_stats() if net is None else net
    return ds._get("http://x/y", net=net, **kw), net


def _http_error(code):
    return HTTPError("http://x/y", code, "boom", {}, None)


# 项目 CLAUDE.md 异常清单里点名必须接住的全部类型。
# ⭐ 逐个喂而不是只喂一个:合并时只要漏掉其中一类,整轮就会崩 ——
# 而 UnicodeDecodeError 不是 OSError 子类,正是最容易在合并时漏掉的那个。
NETWORK_EXCEPTIONS = [
    URLError("unreachable"),
    TimeoutError("timed out"),
    OSError("broken pipe"),
    http.client.HTTPException("bad status line"),
    json.JSONDecodeError("nope", "", 0),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
]
NETWORK_EXCEPTION_IDS = [type(e).__name__ for e in NETWORK_EXCEPTIONS]


# ======================================================================
# 一、成功路径
# ======================================================================

def test_cc_success_returns_payload_and_counts_one_attempt(monkeypatch, sleeps):
    """一次成功 = 1 次尝试、0 重试、0 耗尽、0 睡眠。分母(attempt)必须计。"""
    d, c = _run_cc(monkeypatch, _Script([{"a": 1}]))
    assert d == [{"a": 1}]
    assert c["net_attempt_count"] == 1
    assert c["net_retry_count"] == 0
    assert c["net_give_up_count"] == 0
    assert sleeps == []


def test_ds_success_returns_payload_and_counts_one_attempt(monkeypatch, sleeps):
    d, n = _run_ds(monkeypatch, _Script([{"a": 1}]))
    assert d == [{"a": 1}]
    assert n["net_attempt_count"] == 1
    assert n["net_retry_count"] == 0
    assert sleeps == []


def test_cc_confirmed_empty_is_not_a_failure(monkeypatch, sleeps):
    """确认空(接口真返回 [])必须原样返回,**不许**被当成失败重试。

    这是"翻到底"与"网络断了"分野的起点 —— 分不清就会静默截断分页。
    """
    d, c = _run_cc(monkeypatch, _Script([]))
    assert d == []
    assert c["net_attempt_count"] == 1
    assert c["net_give_up_count"] == 0


def test_ds_confirmed_empty_is_not_a_failure(monkeypatch, sleeps):
    d, n = _run_ds(monkeypatch, _Script([]))
    assert d == []
    assert n["net_give_up_count"] == 0


# ======================================================================
# 二、4xx:对方明确答复 —— 不重试
# ======================================================================

def test_cc_4xx_returns_none_immediately_without_retry(monkeypatch, sleeps):
    """400 = 柜台答"查无此人":立刻返回,不重试、不计网络失败,但**要计 4xx**。"""
    s = _Script(_http_error(400))
    d, c = _run_cc(monkeypatch, s)
    assert d is None
    assert s.calls == 1                      # 一次就走,不重试
    assert c["http_4xx_count"] == 1
    assert c["net_give_up_count"] == 0
    assert c["net_retry_count"] == 0
    assert sleeps == []


def test_ds_4xx_returns_the_code_and_counts_nothing(monkeypatch, sleeps):
    """⚠️ 与 collector 版**不同**:这里返回状态码本身,且不计任何计数器。

    返回码是**有用的信息**,不是可以丢的:调用方靠 400 区分"撞 offset 硬顶"
    与"翻到底"(见 sample_firehose 的四种停法)。合并时不许把它退化成 None。
    """
    s = _Script(_http_error(400))
    d, n = _run_ds(monkeypatch, s)
    assert d == {"__http__": 400}
    assert s.calls == 1
    assert n["net_give_up_count"] == 0
    assert n["net_retry_count"] == 0
    assert sleeps == []
    # ⭐ 这一行是变异测试逼出来的(2026-08-06):原版只断言"没计网络失败",
    # 于是把 `count_4xx` 从 False 翻成 True **全套判据一条都不红** ——
    # 而那正是我在设计文档里写着"必须单独做、不许混进纯重构"的那个行为增强。
    # 「没断言到的地方就是没在验」:差异①要真被焊住,得直接断言这个键不存在。
    assert "http_4xx_count" not in n, \
        "发现层历史上**不计** 4xx;开始计是行为增强,须单独论证、单独验证"


@pytest.mark.parametrize("code", [401, 403, 404, 422])
def test_ds_all_4xx_codes_are_passed_through(monkeypatch, sleeps, code):
    """没见过的 4xx 也要原样带出来 —— 白名单式判断挡不住"还没见过的那一个"。"""
    d, _ = _run_ds(monkeypatch, _Script(_http_error(code)))
    assert d == {"__http__": code}


# ======================================================================
# 三、429 限流:等会儿再来 —— 要重试、要计数、耗尽后归因给限流
# ======================================================================

def test_cc_429_is_retried_then_succeeds(monkeypatch, sleeps):
    """限流一次后成功:计 1 次限流命中、睡 3 秒、最终拿到数据、不计耗尽。"""
    d, c = _run_cc(monkeypatch, _Script(_http_error(429), [{"ok": 1}]))
    assert d == [{"ok": 1}]
    assert c["rate_limit_hits"] == 1
    assert c["rate_limit_give_up_count"] == 0
    assert c["net_give_up_count"] == 0
    assert sleeps == [3.0]


def test_cc_429_exhausted_is_blamed_on_rate_limit_not_network(monkeypatch, sleeps):
    """纯 429 打满 ≠ 隧道坏了。归错因会让告警文案把人指向错的方向。"""
    s = _Script(_http_error(429))
    d, c = _run_cc(monkeypatch, s)
    assert d is cc.GIVE_UP
    assert s.calls == 5                       # TRIES
    assert c["rate_limit_hits"] == 5
    assert c["rate_limit_give_up_count"] == 1
    assert c["net_give_up_count"] == 0
    assert sleeps == [3.0] * 5


def test_ds_429_is_retried_then_succeeds(monkeypatch, sleeps):
    d, n = _run_ds(monkeypatch, _Script(_http_error(429), [{"ok": 1}]))
    assert d == [{"ok": 1}]
    assert n["rate_limit_hits"] == 1
    assert sleeps == [3.0]


def test_ds_429_exhausted_returns_rate_limited_sentinel(monkeypatch, sleeps):
    """⚠️ 与 collector 版**不同**:这里返回专门的限流哨兵,不是通用的"放弃"。

    调用方据此把"被限流打断分页"与"隧道坏了"分开计数 —— 处置一个是压频、
    一个是查代理,合并时不许合成一个。
    """
    s = _Script(_http_error(429))
    d, n = _run_ds(monkeypatch, s)
    assert d == {"__http__": ds.RATE_LIMITED}
    assert s.calls == ds.TRIES
    assert n["rate_limit_hits"] == ds.TRIES
    assert n["rate_limit_give_up_count"] == 1
    assert n["net_give_up_count"] == 0


# ======================================================================
# 四、5xx:对端暂时挂了 —— 重试有意义,且必须与"隧道坏了"分开计
# ======================================================================

def test_cc_5xx_is_counted_as_server_error_and_retried(monkeypatch, sleeps):
    d, c = _run_cc(monkeypatch, _Script(_http_error(503), [{"ok": 1}]))
    assert d == [{"ok": 1}]
    assert c["net_server_error_count"] == 1
    assert c["net_retry_count"] == 1
    assert sleeps == [1.5]


def test_ds_5xx_is_counted_as_server_error_and_retried(monkeypatch, sleeps):
    d, n = _run_ds(monkeypatch, _Script(_http_error(503), [{"ok": 1}]))
    assert d == [{"ok": 1}]
    assert n["net_server_error_count"] == 1
    assert n["net_retry_count"] == 1
    assert sleeps == [ds.RETRY_SLEEP_S]


def test_cc_5xx_exhausted_is_a_network_give_up(monkeypatch, sleeps):
    d, c = _run_cc(monkeypatch, _Script(_http_error(500)))
    assert d is cc.GIVE_UP
    assert c["net_server_error_count"] == 5
    assert c["net_give_up_count"] == 1
    assert c["rate_limit_give_up_count"] == 0


def test_ds_5xx_exhausted_is_a_network_give_up(monkeypatch, sleeps):
    d, n = _run_ds(monkeypatch, _Script(_http_error(500)))
    assert d == {"__http__": ds.RETRY_EXHAUSTED}
    assert n["net_give_up_count"] == 1


# ======================================================================
# 五、网络层异常:CLAUDE.md 点名的六类,一类都不许漏
# ======================================================================

@pytest.mark.parametrize("exc", NETWORK_EXCEPTIONS, ids=NETWORK_EXCEPTION_IDS)
def test_cc_every_network_exception_is_caught_and_counted(monkeypatch, sleeps, exc):
    """六类异常全部落进网络分支:计 retry、退避、耗尽后计 give_up。

    ⭐ 逐类喂的意义:`UnicodeDecodeError` 是 ValueError 子类、**不是 OSError**,
    合并时最容易漏 —— 漏了会让整轮崩掉,而这正是当前代理的真实形态。
    """
    d, c = _run_cc(monkeypatch, _Script(exc))
    assert d is cc.GIVE_UP
    assert c["net_retry_count"] == 5
    assert c["net_give_up_count"] == 1
    assert c["http_4xx_count"] == 0
    assert sleeps == [1.5] * 5


@pytest.mark.parametrize("exc", NETWORK_EXCEPTIONS, ids=NETWORK_EXCEPTION_IDS)
def test_ds_every_network_exception_is_caught_and_counted(monkeypatch, sleeps, exc):
    d, n = _run_ds(monkeypatch, _Script(exc))
    assert d == {"__http__": ds.RETRY_EXHAUSTED}
    assert n["net_retry_count"] == ds.TRIES
    assert n["net_give_up_count"] == 1


@pytest.mark.parametrize("exc", NETWORK_EXCEPTIONS, ids=NETWORK_EXCEPTION_IDS)
def test_cc_transient_network_failure_self_heals(monkeypatch, sleeps, exc):
    """瞬时失败被重试吸收 = 不计耗尽。这是"重试率高但 give_up 为 0"的正常形态,
    也是 08-04 那场事故里 give-up 告警抓不住的原因。"""
    d, c = _run_cc(monkeypatch, _Script(exc, [{"ok": 1}]))
    assert d == [{"ok": 1}]
    assert c["net_retry_count"] == 1
    assert c["net_give_up_count"] == 0


# ======================================================================
# 六、混合失败的归因:429 + 网络 → 算网络,不算限流
# ======================================================================

def test_cc_mixed_failures_are_blamed_on_network(monkeypatch, sleeps):
    """既被限流又有网络错 → 归给网络。理由:限流会自愈,隧道坏了不会,
    指向后者的排查动作代价更高但更该做。"""
    d, c = _run_cc(monkeypatch, _Script(_http_error(429), URLError("x")))
    assert d is cc.GIVE_UP
    assert c["net_give_up_count"] == 1
    assert c["rate_limit_give_up_count"] == 0
    assert c["rate_limit_hits"] == 1


def test_ds_mixed_failures_are_blamed_on_network(monkeypatch, sleeps):
    d, n = _run_ds(monkeypatch, _Script(_http_error(429), URLError("x")))
    assert d == {"__http__": ds.RETRY_EXHAUSTED}
    assert n["net_give_up_count"] == 1
    assert n["rate_limit_give_up_count"] == 0


# ======================================================================
# 七、截止时刻(只有 discovery 版有)—— 洞 2 的要害
# ======================================================================

def test_ds_deadline_already_passed_makes_no_request_at_all(monkeypatch, sleeps):
    """预算在进门前就用尽 → 一次请求都不发。

    ⭐ 且**既不计限流也不计网络失败** —— 主动停手不是故障,
    记进任何一个 give_up 都是往归因里掺假。
    """
    s = _Script([{"never": "called"}])
    d, n = _run_ds(monkeypatch, s, deadline=ds.time.monotonic() - 1)
    assert d == {"__http__": ds.DEADLINE_HIT}
    assert s.calls == 0
    assert n["net_attempt_count"] == 0
    assert n["net_give_up_count"] == 0
    assert n["rate_limit_give_up_count"] == 0


def test_ds_deadline_clamps_the_socket_timeout(monkeypatch, sleeps):
    """剩 3 秒就只能用 3 秒超时。

    只夹"还发不发下一次"是不够的 —— 剩 3 秒仍用 25 秒超时,
    **一次请求就能超预算 22 秒**,时间闸等于没装。
    """
    s = _Script([{"ok": 1}])
    _run_ds(monkeypatch, s, deadline=ds.time.monotonic() + 3)
    assert s.timeouts[0] <= 3.0
    assert s.timeouts[0] > 0


def test_ds_without_deadline_uses_the_full_timeout(monkeypatch, sleeps):
    """不给截止时刻 = 老行为:整超时。"""
    s = _Script([{"ok": 1}])
    _run_ds(monkeypatch, s)
    assert s.timeouts[0] == ds.TIMEOUT_S


def test_ds_deadline_also_clamps_the_backoff_sleep(monkeypatch, sleeps):
    """退避也要看钟 —— 否则"预算用尽"会被一次 sleep 拖到预算之外。"""
    _run_ds(monkeypatch, _Script(URLError("x")),
            deadline=ds.time.monotonic() + 0.5)
    assert all(s <= 0.5 for s in sleeps), sleeps


def test_ds_deadline_stops_retrying_midway(monkeypatch, sleeps):
    """截止时刻到了就停,不把 5 次重试跑满。"""
    s = _Script(URLError("x"))
    d, _ = _run_ds(monkeypatch, s, deadline=ds.time.monotonic() + 0.01)
    assert s.calls < ds.TRIES
    assert isinstance(d, dict)


# ======================================================================
# 八、两份实现的差异 —— 逐条钉死,合并时必须逐条处置
# ======================================================================
#
# 这一节不是"测代码",是**把分歧写成可执行的清单**。
# 合并时每一条都必须给出明确处置(保留 / 统一 / 参数化),不许悄悄挑一个。
# 上一次分叉(429 的处置)之所以能潜伏,正是因为分歧只存在于两份源码之间,
# 没有任何一处把它**并排写出来**。

def test_difference_1_only_one_side_counts_4xx():
    """差异①:4xx 的计数。collector 计 http_4xx_count,discovery 一个都不计。

    合并时的处置:必须**保留计数**(否则 08-04 之后建立的 4xx 可见性会退化),
    但要让 discovery 的调用方也开始计 —— 那是**行为增强**,须单独说明、单独验证,
    不能混在"纯重构"里。
    """
    assert "http_4xx_count" in cc.COUNTER_KEYS
    assert "http_4xx_count" not in ds.NET_COUNTER_KEYS


def test_difference_2_retry_backoff_stays_explicit_and_parameterised():
    """差异②【已处置:参数化,**故意不统一**】网络失败退避 1.5s vs 1.2s。

    两个值都没有实测支撑,只是历史上各写各的。统一它们是**行为变更**
    (退避时长直接进周期耗时 ⇒ 改变慢周期分布),该单独论证、单独验证,
    不能夹在"纯重构"里偷偷做。⏰ 并入 2026-08-11 阈值校准一起定。

    本判据从"记录分歧"改写成"记录决定":差异仍在,但现在它是**一个显式参数**,
    而不是藏在两份函数体里的两个字面量 —— 后者才是分叉的温床。
    """
    import http_client as hc
    assert cc.POLL_RETRY_SLEEP_S == 1.5
    assert hc.RETRY_SLEEP_S == 1.2
    assert ds.RETRY_SLEEP_S is hc.RETRY_SLEEP_S      # 发现层走默认值
    # 两个值都必须是模块常量,不许再写死在函数体里
    # (退避是否还留在适配层里,由 test_there_is_now_exactly_one_retry_loop 按语法树焊住)


def test_difference_3_rate_limit_backoff_now_has_a_single_definition():
    """差异③【已处置:收成一处】限流退避两边本来就都是 3 秒,现在只定义一次。

    一致的地方也要被焊住 —— 否则"两处碰巧相同"随时会变成"两处悄悄不同",
    而那正是 429 处置分叉那次的成因。
    """
    import http_client as hc
    assert hc.RATE_LIMIT_SLEEP_S == 3.0
    assert ds.RATE_LIMIT_SLEEP_S is hc.RATE_LIMIT_SLEEP_S


def test_difference_4_both_sides_now_accept_a_deadline():
    """差异④【已处置:这是本次合并要收的主账】截止时刻现在两边都收。

    合并前只有 discovery 版收 deadline,而单市场翻页调用的是 collector 版 ——
    于是"给翻页装时间闸"在**结构上**就做不到,那是全系统唯一还无界的一段(洞 2)。

    ⚠️ 但**参数装上 ≠ 洞补上**:本次没有任何调用方传它,
    `poll_market` 仍然一次钟都不看。"零件合格 ≠ 装上车"(08-05 栽过一次)。
    真正把闸装上去是下一步,由 test_registration_budget.py::
    test_known_unbounded_segments_are_pinned 继续钉住那个洞。
    """
    import inspect
    assert "deadline" in inspect.signature(ds._get).parameters
    assert "deadline" in inspect.signature(cc._get).parameters
    assert cc._get.__defaults__[-1] is None, "默认必须是 None = 老行为"
    # 洞还在:调用方没传
    assert "deadline" not in inspect.getsource(cc.poll_market)


def _code_of(fn):
    """取函数体的语法树,**剥掉 docstring**。

    ⚠️ 为什么不用正则查源码:第一版就是那么写的,当场被 docstring 里的
    「429/4xx 计数」这句**说明文字**判定成"又长出了限流判断"。
    查散文得不出关于代码的结论 —— 这跟项目里"用结构性判据、别拿症状当身份"
    (串关剔除那条)是同一条道理。
    """
    import ast
    import inspect
    import textwrap
    fn_node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    body = fn_node.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return [n for stmt in body for n in ast.walk(stmt)]


def test_there_is_now_exactly_one_retry_loop():
    """⭐【合并的核心断言】全项目只剩**一份**重试实现。

    这条才是合并的目的。前面几条记录的是"差异怎么处置",这条焊死的是
    **分歧不可能再发生** —— 两个适配层里都不许再出现重试循环、异常分支、限流判断。

    上一次分叉(429 的处置改了一份、另一份没跟上)之所以能潜伏几十轮,
    正是因为没有任何一条判据在管"到底有几份实现"。
    """
    import ast
    import http_client as hc

    core = _code_of(hc.request_json)
    assert any(isinstance(n, ast.For) for n in core), "真身里的重试循环没了?"
    assert any(isinstance(n, ast.ExceptHandler) for n in core), "真身里的异常分支没了?"

    for fn in (cc._get, ds._get):
        body = _code_of(fn)
        who = fn.__module__
        assert not any(isinstance(n, (ast.For, ast.While)) for n in body), \
            f"{who} 又长出了自己的重试循环"
        assert not any(isinstance(n, ast.ExceptHandler) for n in body), \
            f"{who} 又长出了自己的异常分支"
        assert not any(isinstance(n, ast.Constant) and n.value == 429 for n in body), \
            f"{who} 又长出了自己的限流判断"
        assert not any(isinstance(n, ast.Call) and "sleep" in ast.dump(n.func)
                       for n in body), f"{who} 又长出了自己的退避"


def test_difference_5_return_conventions_are_incompatible():
    """差异⑤:同一件事有两套编码 —— 哨兵对象 vs `{"__http__": ...}` 字典。

    两边都能表达"没问到答案",但表达方式不同 ⇒ 每加一条新链路都要重新想一遍
    该用哪种、调用方怎么判。合并时的处置:**统一成一种**,并给旧调用方留适配层,
    让调用方的迁移可以单独验证(一次只动一个自变量)。
    """
    assert cc.GIVE_UP is not None and bool(cc.GIVE_UP) is False
    assert ds.INCONCLUSIVE is not None and bool(ds.INCONCLUSIVE) is False
    assert ds.RETRY_EXHAUSTED == "retry_exhausted"


def test_difference_6_tries_count_is_the_same():
    """差异⑥(实为一致):重试次数都是 5。同样为了避免合并时误以为要参数化。"""
    import inspect
    assert ds.TRIES == 5
    assert inspect.signature(cc._get).parameters["tries"].default == 5
