#!/usr/bin/env python3
"""判据焊死:网络失败必须出声计数 + 分页截断不许静默(2026-08-04)。

## 由来(实测,非假想)

08-04 实测 gamma-api 连打 30 次 **7 次失败**(23%,全是 `SSLError: wrong version number`,
流量强制走 v2rayN/sing-box TUN 代理,绕不过去:直连绑物理网卡 0/15 全失败)。
而同期采集器日志逐轮写着 `4xx 0 | 限流 0 | 解析拒绝 0` —— **看着一切正常**。

原因在 `collector_core._get`:

    except (URLError, TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError):
        time.sleep(1.5)          # ← 没有任何 counter += 1

`ssl.SSLError` 是 `OSError` 子类(本文件断言之),所以 23% 的失败**全落进这个分支,
被重试、被吞、不计数**。心跳里没有任何一个字段会因为网络烂掉而变化。

对着 CLAUDE.md 那一问 —— **「如果它现在就是坏的,我看到的会有什么不同?」**
答案是:**没有不同**。故「一切正常」这个结论压根不成立。这就是要修的东西。

## 第二个盲点:`_get` 的返回值分不清「网络挂了」和「确认空」

`_get` 的 docstring 自己写着「重试耗尽返回 None(**区别于确认空**)」,
但调用方 `poll_market` 根本没区别:

    d = _get(...)
    if not isinstance(d, list) or not d:   # ← 网络挂了 和 翻到底 走同一条路
        break

→ 网络一抖,该市场的分页**悄悄提前结束**,少收的成交没人知道、没人计数。
正是 CLAUDE.md 静默失败清单第 3 条(「任何降级/剔除/回退,必须出声计数」)的原形。

## 本测试**不能**回答什么

不回答「阈值定多少」。实测 60 次 `_get` 调用 **0 次**重试耗尽(单次失败率 4.8% 时)
—— 重试把瞬时失败吸收了,give-up 是稀有事件,今天这点样本撑不起比率阈值。
故本轮**只焊死「必须计数、必须可见」**;告警线留给 [[test_cycle_budget]] 里
有实测分布支撑的「周期耗时」。计数攒够一周后再回来定 give-up 比率线。
"""
import http.client
import json
import socket
import ssl
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import collector_core as cc  # noqa: E402
import discovery_service as ds  # noqa: E402
import storage_engine as se  # noqa: E402


@pytest.fixture
def counters():
    return cc.new_counters()


# ---------- 前提:今天打爆采集器的那个异常,确实落在被吞的分支里 ----------

def test_sslerror_is_oserror_subclass():
    """08-04 实测失败全是 SSLError。它是 OSError 子类 → 落进 _get 的网络分支。
    这条断言是整个修复的前提:前提若不成立,计的就不是那 23%。"""
    assert issubclass(ssl.SSLError, OSError)


def test_socket_timeout_is_oserror_subclass():
    """socket.timeout(= TimeoutError)同样落网络分支 —— 项目 CLAUDE.md 异常清单第 2 条。"""
    assert issubclass(socket.timeout, OSError)


# ---------- 计数:瞬时失败(自愈)与 重试耗尽(真丢数据)必须分开 ----------

def test_successful_request_counts_attempt_only(monkeypatch, counters):
    """一次成功 = 1 次尝试、0 重试、0 耗尽。分母必须计,否则比率没法算。"""
    monkeypatch.setattr(cc, "urlopen", _ok_urlopen([]))
    assert cc._get("http://x", counters) == []
    assert counters["net_attempt_count"] == 1
    assert counters["net_retry_count"] == 0
    assert counters["net_give_up_count"] == 0


def test_transient_failure_is_counted_then_recovers(monkeypatch, counters):
    """失败 2 次后成功:必须留下 2 次重试的痕迹,而不是当无事发生。

    这正是 08-04 被吞掉的那 23% —— 它自愈了,但它**有代价**(每次 sleep 1.5s + 延迟),
    而那代价就是周期从 130s 涨到 480s 的原因。不计数 = 事后无从归因。
    """
    monkeypatch.setattr(cc, "urlopen", _fail_then_ok(2, ssl.SSLError("wrong version number")))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    assert cc._get("http://x", counters) == []
    assert counters["net_retry_count"] == 2
    assert counters["net_attempt_count"] == 3
    assert counters["net_give_up_count"] == 0, "自愈了就不算丢数据"


def test_retry_exhaustion_counts_give_up(monkeypatch, counters):
    """5 次全败 = 真的丢了这一页数据,必须单独计数(与自愈区分开)。"""
    monkeypatch.setattr(cc, "urlopen", _always_fail(ssl.SSLError("boom")))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    assert cc._get("http://x", counters) is cc.GIVE_UP
    assert counters["net_give_up_count"] == 1
    assert counters["net_retry_count"] == 5
    assert counters["net_attempt_count"] == 5


def test_4xx_is_not_counted_as_network_give_up(monkeypatch, counters):
    """4xx 是对方明确答复(已有 http_4xx_count),不是网络断 —— 不许混进 give_up。
    混进去 = 比率被稀释,真网络故障反而看不出来。"""
    monkeypatch.setattr(cc, "urlopen", _always_fail(
        HTTPError("http://x", 404, "nf", {}, None)))
    assert cc._get("http://x", counters) is None
    assert counters["http_4xx_count"] == 1
    assert counters["net_give_up_count"] == 0


def test_discovery_get_counts_into_shared_net_stats(monkeypatch, counters):
    """discovery/settlement 链路走的是另一个 _get(discovery_service),同样必须计数。

    两条链路共用**同一个物理资源**(同一条代理隧道),故合并计数是对的形状 ——
    它们不是独立子系统,而是同一根管子的两个出口。
    """
    monkeypatch.setattr(ds, "urlopen", _always_fail(ssl.SSLError("boom")))
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)
    ds._get("http://x", net=counters)
    assert counters["net_give_up_count"] == 1
    assert counters["net_attempt_count"] == 5


# ---------- 分页截断:网络挂了 ≠ 翻到底 ----------

def test_poll_stops_silently_on_confirmed_empty(monkeypatch, counters):
    """接口确认返回空 = 正常翻到底,不算截断(不许误报)。"""
    monkeypatch.setattr(cc, "_get", lambda url, c, **kw: [])
    rows = cc.poll_market(_market(), counters, wm=None)
    assert rows == []
    assert counters["poll_truncated_count"] == 0


def test_poll_counts_truncation_when_network_gives_up(monkeypatch, counters):
    """重试耗尽导致分页中断 = **静默少收数据**,必须出声计数(不许当翻到底)。"""
    monkeypatch.setattr(cc, "_get", lambda url, c, **kw: cc.GIVE_UP)
    rows = cc.poll_market(_market(), counters, wm=None)
    assert rows == []
    assert counters["poll_truncated_count"] == 1, "网络断掉的分页必须留痕"


def test_poll_keeps_rows_collected_before_truncation(monkeypatch, counters):
    """截断前已抓到的成交是有效数据,必须保留(诚实截断,不是丢批)。"""
    pages = [_page(2), cc.GIVE_UP]
    monkeypatch.setattr(cc, "_get", lambda url, c, **kw: pages.pop(0))
    monkeypatch.setattr(cc, "PAGE", 2)
    rows = cc.poll_market(_market(), counters, wm=None)
    assert len(rows) == 2
    assert counters["poll_truncated_count"] == 1


# ---------- 可见性:计数必须真的进心跳,否则等于没计 ----------

def test_new_counters_reach_the_heartbeat_schema():
    """新计数必须写进审计心跳的字段白名单。

    只加 counter 不进心跳 = 进程一退就蒸发,事后照样无从归因 —— 等于没修。
    """
    for k in ("net_attempt_count", "net_retry_count", "net_give_up_count",
              "net_server_error_count", "rate_limit_give_up_count",
              "poll_truncated_count", "firehose_truncated_count", "cycle_seconds"):
        assert k in se.AUDIT_FIELDS, f"{k} 没进心跳字段,计了也留不下"


def test_every_cycle_counter_is_persisted():
    """run_once 维护的每个计数都必须能落到心跳里。

    防的是"加了 counter 却忘了加进 AUDIT_FIELDS" —— 那等于没计。
    """
    missing = [k for k in cc.COUNTER_KEYS if k not in se.AUDIT_FIELDS]
    assert not missing, f"这些计数没进心跳:{missing}"


# ---------- 归因:别把限流说成隧道坏了 ----------

def test_rate_limit_exhaustion_is_not_blamed_on_network(monkeypatch, counters):
    """5 次全 429 = 被限流,不是隧道坏了。两者处置相反(压频 vs 查代理),不许混。"""
    monkeypatch.setattr(cc, "urlopen", _always_fail(
        HTTPError("http://x", 429, "too many", {}, None)))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    assert cc._get("http://x", counters) is cc.GIVE_UP
    assert counters["rate_limit_give_up_count"] == 1
    assert counters["net_give_up_count"] == 0, "限流不该记成网络故障"


def test_server_error_is_retried_and_counted(monkeypatch, counters):
    """5xx 是对方暂时挂了:必须重试(有意义)且必须计数(否则 5xx 风暴不留痕)。"""
    monkeypatch.setattr(cc, "urlopen", _always_fail(
        HTTPError("http://x", 503, "unavailable", {}, None)))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    assert cc._get("http://x", counters) is cc.GIVE_UP
    assert counters["net_server_error_count"] == 5
    assert counters["net_give_up_count"] == 1


def test_unicode_decode_error_does_not_crash_the_cycle(monkeypatch, counters):
    """代理返回半截/乱码字节 → r.read().decode() 抛 UnicodeDecodeError(ValueError 子类,
    **不是** OSError)。漏了它会整轮崩掉 —— 而这正是当前代理的形态。"""
    assert not issubclass(UnicodeDecodeError, OSError), "前提:它不在 OSError 家族里"
    boom = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(cc, "urlopen", _always_fail(boom))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    assert cc._get("http://x", counters) is cc.GIVE_UP   # 不抛 = 没崩
    assert counters["net_give_up_count"] == 1


# ---------- 发现层:同病必须同治 ----------

def test_firehose_counts_truncation_on_give_up(monkeypatch, counters):
    """firehose 分页中途重试耗尽 → 本轮**活跃市场集合被缩小**(少发现=少收数据)。
    firehose_fail 只抓"总数为 0",抓不住这种部分截断,故必须单独出声。"""
    monkeypatch.setattr(ds, "_get", lambda url, net=None: {"__http__": ds.RETRY_EXHAUSTED})
    out = ds.sample_firehose(5000, net=counters)
    assert out == []
    assert counters["firehose_truncated_count"] == 1


def test_firehose_confirmed_empty_is_not_truncation(monkeypatch, counters):
    """接口确认返回空 = 正常翻到底,不算截断(不许误报)。"""
    monkeypatch.setattr(ds, "_get", lambda url, net=None: [])
    assert ds.sample_firehose(5000, net=counters) == []
    assert counters["firehose_truncated_count"] == 0


# ---------- 调试入口不许伪造"整轮跑完"的凭证 ----------

def test_run_once_does_not_write_heartbeat():
    """心跳语义 = "一整轮真跑完了"(看门狗据其新鲜度判停摆)。
    run_once 是调试入口(`collector_core.py --once`),它自己写心跳 = 伪造凭证,
    且会让"结算阶段被杀"仍留下新鲜心跳 → 看门狗对那一段变瞎。"""
    src = (COLLECTOR_DIR / "collector_core.py").read_text()
    assert "write_audit_heartbeat" not in src, \
        "心跳必须只由 run_cycle 在整轮末尾写"


def test_run_cycle_writes_heartbeat():
    """反面焊死:真正的整轮入口必须写心跳(否则看门狗永远看到"停摆")。"""
    src = (COLLECTOR_DIR / "run_cycle.py").read_text()
    assert "write_audit_heartbeat" in src


# ---------- 测试替身 ----------

class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_urlopen(payload):
    def f(req, timeout=None):
        return _Resp(payload)
    return f


def _always_fail(exc):
    def f(req, timeout=None):
        raise exc
    return f


def _fail_then_ok(n, exc, payload=()):
    state = {"n": 0}

    def f(req, timeout=None):
        state["n"] += 1
        if state["n"] <= n:
            raise exc
        return _Resp(list(payload))
    return f


def _market():
    return {"condition_id": "0xc", "token_id_0": "1", "token_id_1": "2"}


def _page(n):
    return [{"transactionHash": f"0x{i}", "proxyWallet": "0xw", "asset": "1",
             "outcome": "Yes", "side": "BUY", "size": "1", "price": "0.5",
             "timestamp": str(2_000_000_000 + i)} for i in range(n)]
