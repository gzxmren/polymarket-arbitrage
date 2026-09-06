#!/usr/bin/env python3
"""discovery_service.py — Firehose 发现驱动的宇宙发现 + 按需注册。

兑现《数据契约 v1.2》§3(发现层/注册层)/§7(覆盖闭环):
- 现实毒打(2026-07-22):开放市场数万级、Gamma offset 硬顶 2100、keyset 每页 100 且 ~3.7s
  → 全量枚举不可行且稀释信号。改为**采样全局 /trades 发现活跃市场**(近 2 分钟 ~622 个)。
- 发现层:sample_firehose → extract_active(去重 conditionId,§2.2 剔 HFT)。无金额/排序截断。
- 注册层:对不在注册表的新市场按需查 Gamma(?slug=),解析 token/结算,append-only 落注册表。
- 铁律不变(§4.1):asset 在 clobTokenIds 定下标(弃 outcomeIndex),此模块导出 resolve_asset_index。

注册表 = append-only Parquet(registry/*.parquet);load_registry 按 condition_id 取最新一版。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import socket
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from urllib.request import urlopen

import http_client as hc  # noqa: E402
import registration_backlog as rb  # noqa: E402
from storage_engine import DATA_ROOT, _atomic_write_parquet  # noqa: E402

_ORIG_GAI = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _ORIG_GAI(h, p, socket.AF_INET, t, pr, fl)

GAMMA = "https://gamma-api.polymarket.com/markets"
TRADES = "https://data-api.polymarket.com/trades"
UA = {"User-Agent": "polymarket-rebirth-discovery/1.2"}
REGISTRY_DIR = DATA_ROOT / "registry"

# --- HFT 分类器(契约 §2.2,先宽后严)---
_HFT_CORE = re.compile(r"updown|up-or-down", re.I)
_HFT_WINDOW = re.compile(r"-5m-|5-?min|\b\d{1,2}:\d{2}\s?(?:AM|PM)\b", re.I)
_HFT_SUSPECT = re.compile(r"-1h-|hourly|-15m-|-30m-", re.I)


def classify_market(m: dict) -> tuple[str, bool]:
    """(market_class, hft_suspect)。读 slug + question|title(兼容 Gamma 与 firehose 两种对象)。"""
    s = f"{m.get('slug','')} {m.get('question') or m.get('title') or ''}"
    if _HFT_CORE.search(s) and _HFT_WINDOW.search(s):
        return "hft_crypto", True
    return "event", bool(_HFT_SUSPECT.search(s))


def resolve_asset_index(asset: str, clob_token_ids: list[str]) -> int | None:
    """🔴 铁律:asset 在 clobTokenIds 的位置 = outcome 下标。定位失败返回 None(调用方拒绝入库+计数)。"""
    a = str(asset)
    toks = [str(x) for x in clob_token_ids]
    return toks.index(a) if a in toks else None


# 失败归因常量与重试参数:2026-08-06 起**只在 http_client 里定义一份**,这里只是别名。
# 为什么保留别名而不是让调用方直接引 http_client:调用方的迁移是**另一个自变量**,
# 该单独一步、单独验证(CLAUDE.md 铁律 1)。别名保证了这期间两处不可能再分叉 ——
# 上一次分叉(429 的处置)正是"各写各的常量"埋下的。
RETRY_EXHAUSTED = hc.RETRY_EXHAUSTED   # 重试耗尽:处置是查代理隧道
RATE_LIMITED = hc.RATE_LIMITED         # 被 429 打满:处置是压频,不是查隧道
DEADLINE_HIT = hc.DEADLINE_HIT         # 预算用尽主动停手:既不是限流也不是故障,不许污染归因
TRIES = hc.TRIES
TIMEOUT_S = hc.TIMEOUT_S
RETRY_SLEEP_S = hc.RETRY_SLEEP_S
RATE_LIMIT_SLEEP_S = hc.RATE_LIMIT_SLEEP_S

NET_COUNTER_KEYS = ("net_attempt_count", "net_retry_count", "net_give_up_count",
                    "net_server_error_count", "firehose_truncated_count",
                    # 限流两项:发现/注册/结算三条链路此前**完全不计** ——
                    # "发现层被限流过几次"这个问题以前无从回答(alerts 的限流归因也因此对这三条链路失明)。
                    "rate_limit_hits", "rate_limit_give_up_count")

# 发现层自己的计数(与网络健康分开:处置方向不同 —— 这几个是"预算/覆盖"问题,
# 不是"隧道坏了"问题。混在一起报数,告警只会把人指向错的地方)。
DISCOVERY_COUNTER_KEYS = (
    "excluded_parlay_count",           # 剔掉的串关组合盘(见 is_parlay_cid)
    "excluded_hft_count",              # 剔掉的 HFT 加密盘(契约 §2.2;旧代码此处静默)
    "new_discovered",                  # 本轮涌入的新市场数(分母:没有它就看不出上限在削平)
    "register_budget_skipped_count",   # 本轮没轮到注册的新市场数(回答"第 N+1 个何时轮到")
    "firehose_gap_uncovered_count",    # 采样没接上上一轮 = 这段时间的市场本轮看不见
    "firehose_gap_seconds",            # 缺口**多长**(布尔量答不了"是否在恶化")
    "firehose_offset_ceiling_count",   # 撞接口 offset 硬顶(≠ 翻到底,处置完全不同)
    "firehose_window_seconds",         # 本轮采样实际覆盖的时长(25% 覆盖率盲区的解药)
    # --- 2026-08-05 新增:注册积压。补的是「跳过 ≠ 延后,而是永久丢」这个盲区 ---
    "pending_registration_count",           # 积压规模(等着被登记的市场数)
    "pending_registration_dropped_count",   # 超尝试上限/超容量而丢弃 —— 丢必须出声
    "pending_registration_oldest_age_s",    # 最老条目年龄:「在涨」和「卡住了」是两种病
    # --- 2026-08-06 新增:限流/未知错误。粒度按**处置方向**分,不是越细越好 ---
    "firehose_rate_limited_count",   # 被限流打断分页(处置=压频)
    "firehose_http_error_count",     # 未知形态的 HTTP 错误(兜底;稳态恒 0,非零即"出了没想到的事")
    "register_inconclusive_count",   # 查 Gamma **没查成**(≠ 查不到)—— 不许让积压把它当死号沉底
)

# 正常市场的 condition_id = "0x" + 64 位 hex。
_CONDITION_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def is_parlay_cid(cid) -> bool:
    """这个 condition_id 是否**不是**一个可注册的独立市场(串关组合盘 / 畸形 ID)。

    2026-08-04 实测:注册积压里稳定有 15~16% 的市场 `slug` 为空,`_lookup_gamma`
    对它们永远返回 None —— 每轮占掉注册名额、每轮失败、每轮原样重来(实测吃掉 25% 吞吐)。

    它们是**串关组合盘**:title 形如 `"A vs B AND C vs D AND E vs F"`,
    condition_id 却是另一套体制 —— 长 64 字符(正常 66)且右侧补零:
        0x0379047733ac11e756e1408b9c4380ca020000000000000000000000000000
    Gamma 用 `?condition_ids=` 也查不到(实测 0/8),因为它压根不是独立市场。

    ⭐判据选型:实测 n=448 时三个判据完全等价(`无 slug` ≡ `len!=66` ≡ `title 含 " AND "`),
    既然等价就选**结构性**的那个 —— ID 体制的差别与文案无关、与上游哪天补上 slug 无关。
    用 title 会误伤 `"Will X AND Y both happen?"`;用 slug 缺失则是拿**症状**当**身份**,
    上游一补字段判据就悄悄失效,而我们不会知道。

    判据:10-tests/unit/test_parlay_exclusion.py
    """
    return not (isinstance(cid, str) and _CONDITION_ID_RE.match(cid))


def _is_retry_exhausted(d) -> bool:
    """区分「重试耗尽」与「确认空」/「4xx 明确答复」—— 分不清就会静默截断(§静默失败)。"""
    return isinstance(d, dict) and d.get("__http__") == RETRY_EXHAUSTED


def _count_page_stop(d, net: dict | None) -> None:
    """分页因**非 list 返回**而停:按停法出声。调用方负责"绝不置 covered"。

    计数器粒度按**处置方向**分,不是越细越好:
      - 重试耗尽 → 查隧道
      - 被限流   → 压频/拉长间隔
      - offset 硬顶 → 只能降间隔或拆分发现层
      - 未知错误 → 兜底(稳态恒 0;非零 = 出现了没想到的东西,那正是最该被看见的时刻)
      - 预算用尽 → **不单独计数**:处置("加预算")与既有的时间闸完全相同,
        分开计不会带来任何新动作,只会稀释信号。它照常体现为覆盖缺口。
    """
    code = d.get("__http__") if isinstance(d, dict) else None
    if code == RETRY_EXHAUSTED:
        # 重试耗尽 ≠ 翻到底。这里悄悄 break 会**缩小本轮发现的活跃市场集合**
        # (少发现 = 少轮询 = 少收数据),而 firehose_fail 只抓"总数为 0",抓不住部分截断。
        _bump(net, "firehose_truncated_count")
    elif code == RATE_LIMITED:
        _bump(net, "firehose_rate_limited_count")
    elif code == 400:
        # 撞 offset 硬顶。**不是**翻到底 —— 更早的成交确实存在,只是接口不给。
        _bump(net, "firehose_offset_ceiling_count")
    elif code != DEADLINE_HIT:
        _bump(net, "firehose_http_error_count")


def _bump_by(net: dict | None, key: str, n: int) -> None:
    """按量累加。**n=0 时也要写**(而不是跳过)—— 「本轮一个都没跳过」是要被看见的信息,
    键缺失会让下游 `.get(k, 0)` 与"真的是 0"长得一模一样,那正是要消灭的那种模糊。
    """
    hc.bump(net, key, n)


def new_net_stats() -> dict:
    """一轮的网络健康计数。三个都要:比率的分子分母缺一不可。

    为什么发现/轮询/结算三条链路**共用同一份**(而非 CLAUDE.md「独立链路分开判定」):
    它们不是独立子系统,而是**同一根管子的三个出口** —— 同一条代理隧道、同一个出口 IP。
    隧道烂掉时三条一起烂,分开计数只会把同一个信号劈成三份、每份都不够触发。
    """
    return dict.fromkeys(NET_COUNTER_KEYS, 0)


def _bump(net: dict | None, key: str) -> None:
    """计数是可选的(net=None 时静默跳过),但**一旦传了就必须计满** —— 半计数比不计更坏。"""
    hc.bump(net, key)


def _sleep_within(seconds: float, deadline: float | None) -> None:
    """退避也要看钟 —— 否则"预算用尽"会被一次 sleep 拖到预算之外。"""
    hc.sleep_within(seconds, deadline)


def _get(url: str, tries: int = TRIES, net: dict | None = None,
         deadline: float | None = None):
    """GET。返回 list/dict(成功)或 `{"__http__": ...}`(四种失败,必须分得清)。

    ⚠️ 网络异常分支必须计数:`ssl.SSLError`/`socket.timeout` 都是 `OSError` 子类,
    全落在下面那个 except 里。2026-08-04 实测 23% 的请求走这条路被静默吞掉,
    而心跳里 `4xx 0 | 限流 0` 看着一切正常 —— 判据 test_net_failure_counting.py。

    ## 429 单独一路(2026-08-06)

    旧代码 `return {"__http__": e.code}` 把 429 和 400/404 归成一类:**不重试、不计数**。
    而 `collector_core._get`(同一个项目里的另一份实现)一直是重试+计数的 ——
    08-04 修限流归因时只改了那一份,这份并行实现没跟上,**且没有任何判据会因此变红**。
    后果不止"少采一点":`sample_firehose` 会把 429 当成"翻到底"(缺口静默),
    `_lookup_gamma` 会把 429 当成"这个市场查不到"(积压沉底 → 最终永久丢弃)。

    ## deadline:重试之间也要看钟(2026-08-06,还的是"债 1")

    时间闸原先只在**工作单元之间**看钟,`_get` 内部的重试循环对预算一无所知 →
    单次调用最坏 `TRIES × TIMEOUT_S + …` ≈ 130s,**一次就能打穿 90s 的采样闸**。
    传了 deadline 后:剩余时间既夹住"还发不发下一次",也夹住**单次 socket 超时本身**
    (只夹前者不够 —— 剩 3 秒仍用 25 秒超时,一次请求就超预算 22 秒)。

    判据:test_discovery_rate_limit_and_deadline.py

    ## 2026-08-06:这只剩一层薄适配

    重试/计数/归因/时间闸的真身搬进了 `http_client.request_json`,全项目只此一份。
    本函数只负责把 `(data, failure)` 翻译成本模块历史上的 `{"__http__": ...}` 约定 ——
    保留这层翻译是**有意的**:调用方的迁移是另一个自变量,该单独一步、单独验证。
    """
    data, failure = hc.request_json(
        url, urlopen, headers=UA, counters=net, tries=tries,
        timeout_s=TIMEOUT_S, retry_sleep_s=RETRY_SLEEP_S,
        rate_limit_sleep_s=RATE_LIMIT_SLEEP_S, deadline=deadline,
        count_4xx=False)     # ⚠️ 本链路历史上不计 4xx;开始计是行为增强,须单独做
    return data if failure is None else {"__http__": failure}


# ---------- Gamma 批量查(注册层 + 结算守望共用,2026-08-16)----------
#
# 由来:注册层逐个查 slug 实测 950~2350 ms/个,180s 闸下只做得了 76~100 个;
# 而**同一份代码库里**的结算守望早就在批量查(实测 800 个 / 22 秒)。一个模块
# 学会了,另一个从来没拿到这个教训 —— 「照抄结构而不抽象」的反面:该抄的没抄。
# 后果见 test_registration_backlog.py 里那条容量闸饿死判据(实测采不到的比例 8.5% → 35.7%)。

# 实测边界(2026-08-16,对生产 Gamma 实发请求):
#   100 个 cid → URL 8150 字节 → 200 OK
#   110 个     → URL 8960 字节 → HTTP 422(120 / 130 / 150 同样 422)
# ⇒ 服务端硬限是 8192。**按字节打包,不按个数**:写死 100 个时余量只有 42 字节,
#   谁再加一个查询参数就全线 422,而 422 的表现是"整批查不到" —— 静默且致命。
GAMMA_URL_BUDGET_BYTES = 7000

# 🔴 必须两遍:`condition_ids=` 默认只返回**未关闭**市场。
# 实测请求 100 个:默认回 52(未关闭)/ 加 `&closed=true` 回 48(已关闭),并集才 100。
# 只查一遍 = 静默丢掉全部已结算样本 = **与结果相关地丢样本**(2026-08-03 那场事故)。
GAMMA_CLOSED_SUFFIXES = ("", "&closed=true")

# 🔴 `limit=` **承重,不是摆设**。实测(2026-08-16):同样请求 85 个 cid,
#    不给 limit → 只回 **20 个**(接口默认分页,静默截断);给 limit=500 → 回全。
#    即"看着数字差不多、其实丢了四分之三",且丢的那批没有任何出声。
#    ⇒ 它必须 ≥ 单批最大 cid 数,由 test_gamma_batch_lookup.py 焊死;谁想清理掉它,
#    判据会先红。(2026-08-16 code review 曾建议删掉它,实测证明方向反了。)
GAMMA_BATCH_LIMIT = 500

# 铁律「改共享引擎必须默认关闭 + 回归证明」:新查法做成开关,代码里默认**关**,
# 由部署侧显式打开(deploy/systemd/polymarket-rebirth-collector.service 的 Environment=)。
# 这样回滚 = 删一行环境变量,不必改代码、不必等发版。
# 回归证明:test_gamma_batch_lookup.py::test_batch_and_per_slug_produce_identical_rows
# (同一份数据两条路径逐字段相同),以及 2026-08-16 在 8 个生产市场上的实测 8/8 一致。
BATCH_REGISTER = os.environ.get("REGISTER_BATCH", "0") == "1"


def build_gamma_batch_url(cids, suffix: str = "") -> str:
    """拼一次批量查的 URL。全项目只此一处拼 `condition_ids=`(判据焊死)。"""
    q = "&".join(f"condition_ids={c}" for c in cids)
    return f"{GAMMA}?{q}{suffix}&limit={GAMMA_BATCH_LIMIT}"


def pack_condition_ids(cids, budget: int = GAMMA_URL_BUDGET_BYTES):
    """按 URL 字节把 cid 切成若干批,顺序不变、不重不漏。

    ⚠️ 按**最长的那个后缀**算,不按当前这一遍算 —— 否则第一遍刚好卡线、
    第二遍加上 `&closed=true` 就越界,而越界的表现是整批 422(= 整批查不到)。
    """
    longest = max(GAMMA_CLOSED_SUFFIXES, key=len)
    batch: list[str] = []
    for cid in cids:
        if batch and len(build_gamma_batch_url(batch + [cid], longest)) > budget:
            yield batch
            batch = [cid]
        else:
            batch.append(cid)
    if batch:
        yield batch


def batch_lookup_gamma(cids, net: dict | None = None,
                       deadline: float | None = None) -> tuple[dict[str, dict], set[str]]:
    """批量查 Gamma。返回 `({cid: market}, 没问到答案的 cid 集合)`。

    ## 三种归宿必须分得开(不变量 C4)

    - 在 `found` 里 → 查到了
    - 不在 `found`、也不在返回的集合里 → **确认查不到**(两遍都查成了、两遍都没有它)
    - 在返回的集合里 → **没问到答案**(该批至少一遍 HTTP 失败,而它没在成功那遍出现)

    ⭐最容易写错的是第三格:第一遍成功、第二遍(`&closed=true`)失败时,
    第一遍没找到的那些**可能正躺在挂掉的那一遍里**。判成"确认查不到"就是把
    网络故障翻译成"这市场不存在",而且专门错杀**已关闭**的那批 —— 与结果相关。

    ## 对账在调用方做,不在这里

    「请求数 vs 返回数」必须出声(静默失败清单第 1 条),但**本函数不自己计数** ——
    调用方各有一套已经进了心跳、且真有人读的计数器(注册层 `register_fail` /
    `register_inconclusive_count`,结算层 `settlement_lookup_fail` / `settlement_checked`)。
    在这里再记一份只会多出四个没人读的心跳字段,那正是本项目反复发作的
    「记录事实 vs 使用事实,只接一头」。返回值把三种归宿分开,对账所需的料就齐了。
    """
    found: dict[str, dict] = {}
    inconclusive: set[str] = set()
    for batch in pack_condition_ids(cids):
        a_pass_failed = False
        for suf in GAMMA_CLOSED_SUFFIXES:
            d = _get(build_gamma_batch_url(batch, suf), net=net, deadline=deadline)
            if not isinstance(d, list):
                a_pass_failed = True   # 网络/限流/预算/422 —— 都没资格宣判"不存在"
                continue
            for m in d:
                cid = m.get("conditionId")
                if cid and cid not in found:
                    found[cid] = m
        if a_pass_failed:
            # 已经找到的不受影响(答案拿到手了);没找到的只能算没问到。
            inconclusive |= {c for c in batch if c not in found}
    return found, inconclusive


# ---------- 发现层 ----------

def sample_firehose(limit: int = 5000, net: dict | None = None,
                    since_ts: int | None = None,
                    time_budget_s: float | None = None) -> list[dict]:
    """取最新全局成交(每页 1000),**翻到接上上一轮的 since_ts 为止**。

    2026-08-04 实测:5000 笔只覆盖 **3.8 分钟**,而采集间隔 15 分钟
    → 发现层的时间覆盖率只有 **25%**,其余 11 分钟只在别处成交过的市场本轮看不见。
    (⚠️ 这是当天把间隔 10→15 分钟改出来的副作用:`OnCalendar` 同时是发现层的采样率,
     当时只想到"周期别被杀"那一面。)

    为什么用 watermark 而不是"把 limit 调大":写死的条数在成交清淡时白翻、
    在爆量时仍然盖不住 —— 那是在错的框架内调参。按"上一轮采到哪儿了"翻页则自适应,
    且**盖不住时出声**(`firehose_gap_uncovered_count`),而不是悄悄留个洞。

    ⚠️ **接口硬顶**:实测 offset > 10000 一律 HTTP 400
    (`max historical trades offset of 10000 exceeded`)→ 最多拿到 11000 笔 ≈ **8.4 分钟**
    (按实测峰值 1316 笔/分钟)。**这是能力上限,翻页翻不出来** —— 间隔大于它就必然留缺口。

    四种停法必须分得清(处置方向完全不同):
      - 接上了 / 翻到确认空 → 正常,静默
      - 网络重试耗尽        → `firehose_truncated_count`(查隧道)
      - 撞 offset 硬顶(400)→ `firehose_offset_ceiling_count`(只能降间隔或拆分发现层)
      - 撞 limit / 时间闸    → `firehose_gap_uncovered_count`(加预算)

    ⭐⭐ **判断必须倒过来:只有确认拿到 list 才可能是"翻到底"**(2026-08-06 重写)。

    旧写法是白名单式的 —— 先挑出 `RETRY_EXHAUSTED`、再挑出 `400`,
    剩下的一律 `not isinstance(d, list) → covered = True`。于是 429/401/403/422
    以及任何将来才出现的状态码,全都被当成"再没有更早的成交了"
    → `covered=True` → 缺口计数写 0 → **有洞而心跳全绿**。

    这是同一个病的**第四次**发作(08-03 结算断供 / 08-04 网络零计数 / 08-04 硬顶被当翻到底),
    而前三次的修法都是"再补一个状态码"—— 所以第四次照样发生。
    补白名单挡不住"还没见过的那一个",倒过来判才能。

    判据:10-tests/unit/test_firehose_coverage.py
         10-tests/unit/test_discovery_rate_limit_and_deadline.py
    """
    out: list[dict] = []
    t0 = time.monotonic()
    # deadline 传进 _get:时间闸只在页与页之间看钟的话,单次重试风暴(~130s)
    # 就能打穿整段 90s 预算 —— "周期有界"那句话在重试风暴下是假的。
    deadline = t0 + time_budget_s if time_budget_s is not None else None
    newest = oldest = None
    covered = since_ts is None      # 没给 watermark 就无所谓"接上"(冷启动/手工试跑)
    for off in range(0, limit, 1000):
        if deadline is not None and time.monotonic() >= deadline:
            break                  # 时间闸:延迟退化时降级而不是把整轮拖到被杀
        d = _get(f"{TRADES}?limit=1000&offset={off}", net=net, deadline=deadline)
        if not isinstance(d, list):
            _count_page_stop(d, net)   # 出声,且**绝不置 covered**
            break
        if not d:
            covered = True         # 确认翻到底 = 再没有更早的了,缺口自然被盖住
            break
        out.extend(d)
        ts = [int(t["timestamp"]) for t in d if t.get("timestamp")]
        if ts:
            newest = max(ts) if newest is None else max(newest, max(ts))
            oldest = min(ts) if oldest is None else min(oldest, min(ts))
        if since_ts is not None and oldest is not None and oldest <= since_ts:
            covered = True
            break
        time.sleep(0.2)
    if newest is not None and oldest is not None:
        _bump_by(net, "firehose_window_seconds", int(newest - oldest))
    _bump_by(net, "firehose_gap_uncovered_count", 0 if covered else 1)
    # 缺口报**多长**,不只报"有":布尔量回答不了"这周比上周严重了吗",
    # 而阈值校准要的正是分布(CLAUDE.md:阈值必须有实测分布支撑)。
    gap = 0
    if not covered and since_ts is not None and oldest is not None:
        gap = max(0, int(oldest - since_ts))
    _bump_by(net, "firehose_gap_seconds", gap)
    return out


def newest_trade_ts(trades: list[dict]) -> int | None:
    """本批最新成交时间戳 —— 下一轮的 since_ts。取不到返回 None(调用方保持旧 watermark)。"""
    ts = [int(t["timestamp"]) for t in trades if t.get("timestamp")]
    return max(ts) if ts else None


def extract_active(trades: list[dict], counts: dict | None = None) -> dict[str, dict]:
    """去重 conditionId → {cid: 首见 stub(slug/title)};当场剔除串关与 hft_crypto。

    ⚠️ 两类剔除都必须**出声计数**(CLAUDE.md 第 3 条)。旧代码对 hft 是 `continue` 无计数、
    对串关则根本不认识(放它们进去,到注册层再静默失败)—— 两个都是静默丢样本。

    按**市场**计数而非按成交笔数:一个高频串关能刷出上千笔,按笔数计会把计数刷爆、
    淹掉真信号(降噪是可靠性工作,不是体验优化)。
    """
    active: dict[str, dict] = {}
    parlay: set[str] = set()
    hft: set[str] = set()
    for t in trades:
        cid = t.get("conditionId")
        if not cid or cid in active:
            continue
        if is_parlay_cid(cid):
            parlay.add(cid)
            continue
        if classify_market(t)[0] == "hft_crypto":
            hft.add(cid)
            continue
        active[cid] = {"slug": t.get("slug"), "title": t.get("title")}
    _bump_by(counts, "excluded_parlay_count", len(parlay))
    _bump_by(counts, "excluded_hft_count", len(hft))
    return active


# ---------- 注册层 ----------

MARKETS_SCHEMA = pa.schema([
    ("condition_id", pa.string()), ("slug", pa.string()), ("title", pa.string()),
    ("event_slug", pa.string()), ("market_class", pa.string()), ("hft_suspect", pa.bool_()),
    ("token_id_0", pa.string()), ("token_id_1", pa.string()), ("outcomes_json", pa.string()),
    ("closed", pa.bool_()), ("resolved_outcome", pa.float64()),
    ("start_date", pa.string()), ("end_date", pa.string()), ("snapshot_at", pa.int64()),
])


def parse_market(m: dict) -> dict | None:
    """Gamma 市场对象 → 注册表行。非二元 / 缺 token 返回 None。"""
    try:
        toks = [str(x) for x in json.loads(m.get("clobTokenIds") or "[]")]
        outs = json.loads(m.get("outcomes") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if len(toks) != 2 or len(outs) != 2:
        return None
    market_class, suspect = classify_market(m)
    resolved = None
    if m.get("closed") and m.get("outcomePrices"):
        try:
            op = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
            v = float(op[0])
            resolved = v if v in (0.0, 1.0) else None
        except (json.JSONDecodeError, ValueError, TypeError, IndexError):
            resolved = None
    return {
        "condition_id": m.get("conditionId"), "slug": m.get("slug"),
        "title": (m.get("question") or "")[:300], "event_slug": m.get("eventSlug") or m.get("slug"),
        "market_class": market_class, "hft_suspect": suspect,
        "token_id_0": toks[0], "token_id_1": toks[1],
        "outcomes_json": json.dumps(outs, ensure_ascii=False),
        "closed": bool(m.get("closed")), "resolved_outcome": resolved,
        "start_date": m.get("startDate"), "end_date": m.get("endDate"),
    }


def load_registry() -> dict[str, dict]:
    """读注册表所有 Parquet,按 condition_id 取 snapshot_at 最新一版。空注册表返回 {}。

    ⭐`schema=MARKETS_SCHEMA` 不是可有可无的参数,是**必须**(2026-08-07 实测):
    注册表 append-only,加字段之后必然出现老文件 14 列、新文件 18 列并存。
    不显式给 schema 时,`pq.read_table(目录)` **只按其中一份格式来,多出来的列
    静默丢掉、不报错**(换文件顺序、改用 pyarrow.dataset 都一样丢 —— 三种写法实测两种丢)。
    后果:采集器照常跑、日志照常打、看门狗照常绿,而新字段永远是空的 ——
    "一切正常,只有某个东西恒为空",本项目反复发作的那个病。

    给了 schema 之后:老文件缺的列补成 null,新文件的值原样保留,行数不变。
    ⚠️ 反过来也成立:**文件里有而 MARKETS_SCHEMA 里没有的列会被丢弃** —— 这是有意的,
    schema 是契约。加字段必须同时改 `parse_market` 与 `MARKETS_SCHEMA`,
    漏一个会被 `test_registry_schema_evolution.py` 当场抓住。
    """
    if not any(REGISTRY_DIR.glob("*.parquet")):
        return {}
    tbl = pq.read_table(REGISTRY_DIR, schema=MARKETS_SCHEMA)
    reg: dict[str, dict] = {}
    for r in tbl.to_pylist():
        cid = r["condition_id"]
        if cid not in reg or r["snapshot_at"] > reg[cid]["snapshot_at"]:
            reg[cid] = r
    return reg


class _Inconclusive:
    """「没查成」的哨兵 —— 必须与「确认查不到」(None)分开。

    与 `collector_core.GIVE_UP` 同一个套路,理由也一样:调用方 `if not m:` 会把
    **没问到答案**当成**答案是"没有"**。这里的后果特别重 —— 见 `register_new_markets`。
    """
    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "<INCONCLUSIVE: 没查成,≠ 查不到>"


INCONCLUSIVE = _Inconclusive()


def _lookup_gamma(slug: str, net: dict | None = None,
                  deadline: float | None = None):
    """查 Gamma。返回 市场 dict / None(确认查不到)/ INCONCLUSIVE(没查成)。

    ⚠️ 两个后缀**共享同一个 deadline**:各自重新拿满预算的话,单个市场最坏
    ≈ 2 × 130s = 260s > 注册闸 180s —— "闸保证有界"在这里就是假的。

    ⚠️ 非 list 一律 INCONCLUSIVE(不只限流/重试耗尽,也包括没见过的 4xx):
    "没问到答案"和"答案是没有"必须分开,而未知错误属于前者。
    """
    for suf in ("", "&closed=true"):
        m = _get(f"{GAMMA}?slug={slug}{suf}", net=net, deadline=deadline)
        if not isinstance(m, list):
            return INCONCLUSIVE   # 网络/限流/预算/未知错误 —— 都没资格宣判"这市场不存在"
        if m:
            return m[0]
    return None                   # 两个后缀都确认返回空 = 真的查不到


def register_new_markets(stubs: dict[str, dict], max_new: int | None = None,
                         net: dict | None = None,
                         time_budget_s: float | None = None,
                         outcome: dict | None = None) -> tuple[int, int]:
    """对新市场按需查 Gamma 注册。返回 (注册成功数, 查询失败数)。失败计数不静默丢。

    `stubs` 的**顺序即优先级**(dict 保插入顺序),由 `registration_backlog.order` 决定。
    `outcome` 是可选出参,填 `{"attempted": [...], "registered": [...]}` ——
    积压层要靠它区分「真发出去查了但没成」与「压根没轮到」:前者才该 `attempts+1` 沉底,
    后者原样等下轮。混为一谈的话,"系统忙了几轮"会被当成"这东西有问题"。

    ## 两道闸,一个都不能少

    - **计数闸 `max_new`**:`None` = 不设上限;`0` = 一个都不注册。
      ⚠️ 旧代码写的是 `if max_new and i >= max_new`,于是 `max_new=0` 落进假值分支
      → **不设上限、全量注册**,与 `run_cycle.py` 注释写的「0=不注册新市场」正好相反。
      这是最危险的一类 bug:**急刹车踩下去是全油门**(冷启动实测 register 112 个 >10 分钟)。
    - **时间闸 `time_budget_s`**:发现层耗时 ≈ 常数 + `max_new` × 一次 Gamma 往返,
      而往返时长**不由我们决定**(实测同一天从 ~1.2s 漂到 1.90s)。纯计数闸在延迟翻倍时
      让周期跟着翻倍 → 撞 systemd 超时被杀(2026-08-04 当天真的发生了 12 次)。
      有时间闸就变成**降级**:本轮少注册几个并出声,下轮继续。

    ## 没轮到的必须出声

    实测每轮涌入 ~90 个新市场而上限是 40 → `new_registered` 逐轮恒在 33~38
    (「恒定不变的计数」= 被上限削平,不是自然产出)。旧代码对没轮到的那 50 个
    **一个字都没说** —— 正是「『每轮取前 N 个』必须能回答『第 N+1 个何时轮到』」的原形。

    判据:10-tests/unit/test_registration_budget.py
    """
    now = int(dt.datetime.now(dt.UTC).timestamp())
    rows, fail, attempted, inconclusive = [], 0, 0, 0
    tried_cids, ok_cids = [], []
    t0 = time.monotonic()
    deadline = t0 + time_budget_s if time_budget_s is not None else None
    items = list(stubs.items())

    def _take(cid, m) -> bool:
        """收下一个**已经问到答案**的市场(`m is None` = 确认查不到)。登记成功返回 True。

        两条查法共用这一段 —— 不许各写一份,否则只有一份会拿到日后的修正
        (项目里"两份实现并存、只改了一份"已经发生过:两个 `_get` 的 429 归因)。
        """
        nonlocal fail
        tried_cids.append(cid)   # 真问到答案了(不论答案是"有"还是"没有")
        if not m:
            fail += 1
            return False
        row = parse_market(m)
        if not row or not row["condition_id"]:
            fail += 1
            return False
        row["snapshot_at"] = now
        rows.append(row)
        ok_cids.append(cid)
        return True

    if BATCH_REGISTER:
        # 计数闸在打包**之前**切,于是"额度"数的是市场数而不是批数 —— 与旧路同义。
        # ⚠️ `max_new=0` 必须真的是零:`cids[:0] == []` → 一批都不发。
        cids = [cid for cid, _ in items]
        if max_new is not None:
            cids = cids[:max_new]
        for batch in pack_condition_ids(cids):
            # 时间闸在**发出请求之前**判(查完再判必然超预算一整批)。
            # 一批 ≈ 2.7s(实测),粒度比整段闸细得多,够用。
            if deadline is not None and time.monotonic() >= deadline:
                break
            attempted += len(batch)
            found, inconc = batch_lookup_gamma(batch, net=net, deadline=deadline)
            inconclusive += len(inconc)
            for cid in batch:
                if cid in inconc:
                    continue   # ⭐没查成 ≠ 查不到 → 不进 tried_cids → attempts 不变
                _take(cid, found.get(cid))
            time.sleep(0.15)   # 礼貌间隔按**批**给,不按市场 —— 按市场会加出 300s
    else:
        for cid, stub in items:
            # 两道闸都必须在**发起查询之前**判。查完再判必然超出预算一整个请求的时长,
            # 而代理退化时单次就是 6s+ —— "多做一个"正是被杀那 12 轮的构成方式。
            if max_new is not None and attempted >= max_new:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            attempted += 1     # 闸管的是**成本**(发出去的查询),不是成果 ——
                               # 否则失败的市场不占额度,接口挂掉时会一直查到超时
            slug = stub.get("slug")
            m = _lookup_gamma(slug, net=net, deadline=deadline) if slug else None
            if m is INCONCLUSIVE:
                # ⭐**没查成 ≠ 查不到**,故不进 tried_cids → 积压里 attempts 不变。
                # 混为一谈的后果:被限流/网络抖动几轮 → 积压把它当"这东西有问题"逐格沉底 →
                # 攒够 MAX_ATTEMPTS 就**永久丢弃一个真实存在的市场**。
                # 这与 registration_backlog 里「被预算跳过 ≠ 尝试失败」是同一条原则,
                # 只是当初只堵了"预算跳过"这一个入口,限流从旁边绕了过去。
                inconclusive += 1
                continue
            if _take(cid, m):
                time.sleep(0.15)   # 旧路原样:只在**登记成功**后歇一下
    if outcome is not None:
        outcome["attempted"] = tried_cids
        outcome["registered"] = ok_cids
    _bump_by(net, "register_budget_skipped_count", len(items) - attempted)
    # 「0 也要写」:稳态该恒 0,而键缺失与"真的是 0"长得一模一样 —— 那正是要消灭的模糊。
    _bump_by(net, "register_inconclusive_count", inconclusive)
    if rows:
        dest = REGISTRY_DIR / f"{uuid.uuid4().hex}.parquet"
        _atomic_write_parquet(pa.Table.from_pylist(rows, schema=MARKETS_SCHEMA), dest)
    return len(rows), fail


def refresh_and_registry(sample_limit: int = 5000, max_new: int | None = None,
                         net: dict | None = None, since_ts: int | None = None,
                         sample_time_budget_s: float | None = None,
                         register_time_budget_s: float | None = None,
                         backlog_file=None) -> tuple[list[dict], dict]:
    """发现 → 注册新市场 → 返回 (可轮询市场行列表, 计数)。

    可轮询 = 当前活跃(firehose 出现)且已注册、market_class=event、未关闭。

    ## 候选集 = 本轮新发现 ∪ **积压**(2026-08-05)

    改之前候选集只有本轮 firehose 窗口里的新市场,而 watermark 每轮照常推进 ——
    被预算跳过的 cid 下一轮就不在窗口里了,**只有再成交一次**才回得来。
    低频盘既排在后面先被砍、又最不可能再成交 ⇒ 丢得**与结果相关**(致命形态)。
    现在跳过的进 `registration_backlog`,下轮按 (attempts, first_seen) 优先取。
    """
    bl_path = backlog_file or rb.BACKLOG_FILE

    trades = sample_firehose(sample_limit, net=net, since_ts=since_ts,
                             time_budget_s=sample_time_budget_s)
    active = extract_active(trades, net)
    registry = load_registry()
    fresh = {cid: s for cid, s in active.items() if cid not in registry}

    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    # 积压里若有已经注册上的(别的路径登记的),先摘掉再合并,免得白占预算。
    backlog = {c: e for c, e in rb.load(bl_path).items() if c not in registry}
    backlog = rb.merge(backlog, fresh, now_ts)
    candidates = rb.order(backlog)

    outcome: dict = {}
    n_reg, n_fail = register_new_markets(candidates, max_new=max_new, net=net,
                                         time_budget_s=register_time_budget_s,
                                         outcome=outcome)
    backlog = rb.settle(backlog, attempted=outcome.get("attempted", ()),
                        registered=outcome.get("registered", ()),
                        now=now_ts, counts=net)
    rb.save(bl_path, backlog)

    if n_reg:
        registry = load_registry()
    pollable = [registry[cid] for cid in active
                if cid in registry and not registry[cid]["closed"]
                and registry[cid]["market_class"] == "event"]
    stats = {"firehose_trades": len(trades), "active_cids": len(active),
             # 涌入量 = **本轮新发现**(不含积压),与 new_registered 并排看才知道闸是否在削平。
             # 若把积压算进来,涌入量会被自己上一轮的欠账顶高 —— 那就再也看不出真实涌入了。
             "new_discovered": len(fresh),
             "pending_registration": len(backlog),   # 欠账规模,与涌入量分开报
             "new_registered": n_reg, "register_fail": n_fail, "pollable": len(pollable),
             "firehose_newest_ts": newest_trade_ts(trades)}
    return pollable, stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="发现一轮并注册新市场")
    ap.add_argument("--sample", type=int, default=5000, help="firehose 采样条数")
    ap.add_argument("--max-new", type=int, default=None, help="本轮最多注册 N 个新市场(试跑用)")
    args = ap.parse_args()
    if args.once:
        pollable, stats = refresh_and_registry(args.sample, max_new=args.max_new)
        print(f"发现: {stats}")
        for r in pollable[:5]:
            print(f"  - {r['title'][:50]}  class={r['market_class']}")
    else:
        ap.print_help()
