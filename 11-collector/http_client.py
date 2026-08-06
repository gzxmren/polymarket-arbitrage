#!/usr/bin/env python3
"""http_client.py — 全项目**唯一**的"GET + 重试 + 计数 + 时间闸"实现(2026-08-06)。

## 为什么要有这个模块

在此之前,同一件事有**两份**实现:`collector_core._get` 与 `discovery_service._get`。
合并的理由不是"代码重复难看",而是它已经造成过两类实际损害
(详见 docs/DESIGN_COLLECTOR_INVARIANTS.md 重复形状 #2 与洞 2):

1. **两份会悄悄分叉,而判据不会变红。** 2026-08-04 修限流归因时只改了一份,
   另一份直到 08-06 才被发现还是旧写法 —— 期间 429 被当成"翻到底"(缺口静默)、
   被当成"这市场查不到"(积压沉底 → 最终永久丢弃一个真实存在的市场)。
2. **只有一份接受截止时刻 ⇒ 另一份的调用方结构上装不上时间闸。**
   单市场翻页(`poll_market`)至今无界,最坏 21 页 × 5 次重试 × 25 秒 ≈ 46 分钟,
   而整轮硬杀线只有 15 分钟。这不是"欠债没还",是**两份实现直接长出来的结构缺陷** ——
   所以合并本身就是那个 bug 的修法。

## 设计要点

- **调用方注入 opener**:核心不写死 `urlopen`,由调用方传进来。
  这样既便于判据在不打全局补丁的情况下喂固定响应,也让"到底用哪个传输层"
  成为调用方的显式选择,而不是藏在模块里。
- **返回 `(data, failure)` 二元组**:`failure is None` 才是成功。
  四种失败必须分得清 —— 混为一谈正是静默失败的经典造法:
    - `int`(4xx)= 对方明确答复("查无此人"),不重试
    - `RATE_LIMITED` = 被 429 打满(处置:压频,**不是**查隧道)
    - `RETRY_EXHAUSTED` = 网络/5xx 重试耗尽(处置:查代理隧道)
    - `DEADLINE_HIT` = 预算用尽主动停手(**既不是限流也不是故障**,
      记进任何一个 give_up 都是往归因里掺假)
- **参数化两处差异而不是擅自统一**(退避时长、要不要计 4xx)。
  统一它们是**行为变更**,该单独论证、单独验证 —— 本次合并是纯重构,
  一次只动一个自变量(CLAUDE.md 铁律 1)。

## 异常清单(逐条对照 CLAUDE.md)

`URLError / TimeoutError / OSError / http.client.HTTPException /
json.JSONDecodeError / UnicodeDecodeError` —— 一类都不许漏。
⚠️ `UnicodeDecodeError` 是 `ValueError` 子类、**不是 `OSError`**,
必须显式列出:代理返回半截/乱码字节时 `read().decode()` 就抛它,
漏了会**整轮崩掉**,而那正是当前代理隧道的真实形态。
⚠️ `ssl.SSLError` / `socket.timeout` 都是 `OSError` 子类,落在同一分支(判据断言之)。

判据:10-tests/unit/test_get_baseline.py(行为基线,合并前后必须一致)
     10-tests/unit/test_http_client_merge.py(合并本身的判据)
"""
from __future__ import annotations

import http.client
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

# 失败归因常量。⚠️ 值必须与 discovery_service 里的历史取值一致 ——
# 它们已经落在调用方的判断里(`d.get("__http__") == RETRY_EXHAUSTED`),改值即行为变更。
RETRY_EXHAUSTED = "retry_exhausted"
RATE_LIMITED = "rate_limited"
DEADLINE_HIT = "deadline"

# 重试参数的默认值。集中在这里定义,判据要按它们算最坏耗时上界 ——
# 而从源码里正则抠 `timeout=25` 会在改成变量的那天悄悄失效。
TRIES = 5
TIMEOUT_S = 25
RETRY_SLEEP_S = 1.2
RATE_LIMIT_SLEEP_S = 3.0    # 限流要退得比网络抖动更久

# 网络层异常:必须**全部**接住且**分别**归因。集中成元组,免得两处写岔。
NETWORK_ERRORS = (
    URLError, TimeoutError, OSError, http.client.HTTPException,
    json.JSONDecodeError, UnicodeDecodeError,
)


def bump(counters: dict | None, key: str, n: int = 1) -> None:
    """计数是可选的(counters=None 时静默跳过),但**一旦传了就必须计满** ——
    半计数比不计更坏:它让"看着有数"和"真的在数"分不开。
    """
    if counters is not None:
        counters[key] = counters.get(key, 0) + n


def sleep_within(seconds: float, deadline: float | None) -> None:
    """退避也要看钟 —— 否则"预算用尽"会被一次 sleep 拖到预算之外。"""
    if deadline is not None:
        seconds = min(seconds, max(0.0, deadline - time.monotonic()))
    if seconds > 0:
        time.sleep(seconds)


def request_json(url: str, opener, *, headers: dict | None = None,
                 counters: dict | None = None, tries: int = TRIES,
                 timeout_s: float = TIMEOUT_S,
                 retry_sleep_s: float = RETRY_SLEEP_S,
                 rate_limit_sleep_s: float = RATE_LIMIT_SLEEP_S,
                 deadline: float | None = None,
                 count_4xx: bool = False) -> tuple[object, object]:
    """GET 一个 JSON 接口。返回 `(data, failure)`;`failure is None` 才是成功。

    `deadline` 是**绝对时刻**(`time.monotonic()` 尺度),`None` = 不设限。
    它同时夹住两件事,缺一不可:

    1. **还发不发下一次请求** —— 只夹这个不够;
    2. **单次 socket 超时本身** —— 剩 3 秒仍用 25 秒超时的话,
       一次请求就能超预算 22 秒,时间闸等于没装。

    ⭐ 时间闸只在"工作单元之间"看钟是**假的有界**:一次调用最坏
    `tries × (timeout_s + retry_sleep_s)` ≈ 130 秒,比整段的预算还大。
    这就是 2026-08-04 那 12 轮被 systemd 杀掉的构成方式。
    """
    n_429 = n_net = 0        # 记「因为什么而耗尽」—— 限流与隧道故障的处置完全相反
    for _ in range(tries):
        timeout = timeout_s
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            timeout = min(timeout_s, remaining)
        bump(counters, "net_attempt_count")
        try:
            with opener(Request(url, headers=headers or {}), timeout=timeout) as r:
                return json.loads(r.read().decode()), None
        except HTTPError as e:
            if e.code == 429:
                # 限流 = "现在人太多,等会儿再来",重试有意义。
                # 与 400("查无此人")完全不同 —— 归成一类会让分页把限流当成翻到底。
                bump(counters, "rate_limit_hits")
                n_429 += 1
                sleep_within(rate_limit_sleep_s, deadline)
                continue
            if 400 <= e.code < 500:
                if count_4xx:
                    bump(counters, "http_4xx_count")
                # 对方明确答复,不是网络断 → 不重试、不计 net_*。
                # 返回状态码本身而不是 None:调用方要靠 400 区分"撞 offset 硬顶"
                # 与"翻到底",丢掉码就丢掉了这个区分。
                return None, e.code
            # 5xx 是对方**暂时**挂了,重试有意义,且必须与"隧道坏了"分开计
            bump(counters, "net_server_error_count")
            bump(counters, "net_retry_count")
            n_net += 1
            sleep_within(retry_sleep_s, deadline)
        except NETWORK_ERRORS:
            bump(counters, "net_retry_count")
            n_net += 1
            sleep_within(retry_sleep_s, deadline)
    # 归因三选一。⚠️ 一次都没发出去时(预算在进门前就用尽)既不是限流也不是网络故障,
    # 记进任何一个 give_up 都是往归因里掺假 —— 那正是"限流被说成隧道坏了"的同一个病。
    if n_429 and not n_net:
        bump(counters, "rate_limit_give_up_count")
        return None, RATE_LIMITED
    if n_net:
        bump(counters, "net_give_up_count")
        return None, RETRY_EXHAUSTED
    return None, DEADLINE_HIT
