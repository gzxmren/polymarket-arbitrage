#!/usr/bin/env python3
"""collector_core.py — 按市场轮询主循环。

兑现《数据契约 v1.1》§3 / §4.1 / §7:
- 逐事件市场 GET /trades?market=<cid> 分页;单市场 offset 上限实测 ~1万,逼近即告警。
- 增量:用 storage.watermark 只收比库内更新的成交(避免重抓,减小去重压力)。
- 解析铁律:asset 在 clobTokenIds 定下标(弃用 outcomeIndex);定位失败 → 拒绝入库 + parse_reject 计数。
- 审计:累计 http_4xx / rate_limit / offset_overflow / parse_reject / 网络健康计数,
  **由 run_cycle 在整轮末尾落一条心跳**(2026-08-04 起,不再由 run_once 自己写)。

⚠️ `python3 collector_core.py --once` 是**调试入口**,它只采集、**不写审计心跳** ——
   心跳的语义是"一整轮真的跑完了"(看门狗据其新鲜度判停摆),手工试跑不该伪造这个凭证。
   要产生心跳请走 `run_cycle.py`。判据:test_net_failure_counting.py 末两条。

异常枚举逐条对照 CLAUDE.md 清单
(URLError/TimeoutError/OSError/HTTPException/JSONDecodeError/UnicodeDecodeError)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cycle_state
import discovery_service as ds
import storage_engine as se
from discovery_service import refresh_and_registry, resolve_asset_index

# 注册链路连零计数(跨周期落盘;每轮是独立进程)
REGISTER_STREAK_FILE = se.DATA_ROOT / "state" / "register_streak.json"
# firehose 采样 watermark:上一轮采到的最新成交时间。发现层据此翻页到"接上"为止
# (不是翻满写死的条数)—— 见 discovery_service.sample_firehose。
FIREHOSE_WM_FILE = se.DATA_ROOT / "state" / "firehose_watermark.json"

_ORIG_GAI = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _ORIG_GAI(h, p, socket.AF_INET, t, pr, fl)

TRADES = "https://data-api.polymarket.com/trades"
UA = {"User-Agent": "polymarket-rebirth-collector/1.1"}
OFFSET_CAP = 10000       # 实测单市场硬顶;offset 逼近即"该市场增量可能溢出"
PAGE = 500               # 每页
OFFSET_WARN = 8000       # 逼近上限的告警线(留余量,提示需压频)


COUNTER_KEYS = (
    "total_markets_polled", "http_4xx_count", "rate_limit_hits",
    "offset_overflow_count", "dedup_collapse_count", "parse_reject_count",
    "poll_truncated_count",          # 网络断掉导致分页提前结束(≠ 翻到底)
    "rate_limit_give_up_count",      # 被 429 打满而放弃(与"隧道坏了"分开,处置不同)
    "poll_budget_skipped_count",     # 轮询超时间闸而没轮到的市场数(下轮 watermark 接续)
) + ds.NET_COUNTER_KEYS + ds.DISCOVERY_COUNTER_KEYS


def new_counters() -> dict:
    """一轮的全部计数。集中在这里定义,避免"某处忘了初始化 → KeyError 或漏计"。"""
    return dict.fromkeys(COUNTER_KEYS, 0)


class _GiveUp:
    """「重试耗尽」的哨兵 —— 必须与「确认空」「4xx」三者互相区分。

    为什么不能都返回 None:调用方 `if not d: break` 会把**网络断了**当成**翻到底**,
    于是该市场的分页悄悄提前结束、少收的成交没人知道(CLAUDE.md 静默失败清单第 3 条:
    任何降级/剔除/回退必须出声计数)。2026-08-04 前的代码正是如此 —— docstring 写着
    「区别于确认空」,而调用方根本没区别。
    """
    __slots__ = ()

    def __repr__(self) -> str:
        return "<GIVE_UP: 重试耗尽>"

    def __bool__(self) -> bool:
        return False


GIVE_UP = _GiveUp()


def _get(url: str, counters: dict, tries: int = 5):
    """GET;429/4xx 计数;重试耗尽返回 GIVE_UP(区别于确认空的 [] 和 4xx 的 None)。

    ⚠️ 网络异常分支必须计数:`ssl.SSLError`/`socket.timeout` 都是 `OSError` 子类,
    全落在下面那个 except 里。2026-08-04 实测 23% 请求走这条路被静默吞掉,
    而心跳 `4xx 0 | 限流 0` 看着一切正常 —— 判据 test_net_failure_counting.py。
    """
    n_429 = n_net = 0                # 记「因为什么而耗尽」——两者的处置完全不同
    for _ in range(tries):
        counters["net_attempt_count"] += 1
        try:
            with urlopen(Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            if e.code == 429:
                counters["rate_limit_hits"] += 1
                n_429 += 1
                time.sleep(3)
                continue
            if 400 <= e.code < 500:
                counters["http_4xx_count"] += 1
                return None          # 对方明确答复,不是网络断 → 不重试、不计 net_give_up
            counters["net_server_error_count"] += 1   # 5xx:对方暂时挂了,重试有意义
            counters["net_retry_count"] += 1
            n_net += 1
            time.sleep(1.5)
        except (URLError, TimeoutError, OSError, http.client.HTTPException,
                json.JSONDecodeError, UnicodeDecodeError):
            # UnicodeDecodeError(ValueError 子类,非 OSError)必须显式列:代理返回半截/乱码
            # 字节时 r.read().decode() 就抛它 —— 漏了会**整轮崩掉**,而这正是当前代理的形态。
            counters["net_retry_count"] += 1
            n_net += 1
            time.sleep(1.5)
    # 归因:纯 429 打满 ≠ 隧道坏了。混为一谈会让告警文案(「先查代理隧道」)把人指错方向。
    if n_net == 0 and n_429 > 0:
        counters["rate_limit_give_up_count"] += 1
    else:
        counters["net_give_up_count"] += 1
    return GIVE_UP


def poll_market(market: dict, counters: dict, wm: int | None) -> list[dict]:
    """轮询单个市场,返回比 watermark 更新的、已解析的 trades 行。

    分页从 offset=0(最新)往回,直到 timestamp <= watermark(增量)或到接口上限。
    wm 由调用方一次性预取(见 run_once),避免每市场开 DuckDB 连接。
    """
    cid = market["condition_id"]
    token_ids = [market["token_id_0"], market["token_id_1"]]
    new_rows, offset = [], 0
    now = int(dt.datetime.now(dt.UTC).timestamp())
    while offset <= OFFSET_CAP:
        d = _get(f"{TRADES}?limit={PAGE}&market={cid}&offset={offset}", counters)
        if d is GIVE_UP:
            # 网络重试耗尽 ≠ 翻到底。此处**必须出声**:这一页(及更早的页)本轮没收到,
            # 当"确认空"处理就是静默少收数据。近端已抓的照常保留(诚实截断)。
            counters["poll_truncated_count"] += 1
            break
        if not isinstance(d, list) or not d:
            break                      # 确认空 = 正常翻到底
        stop = False
        for t in d:
            ts = int(t.get("timestamp", 0))
            if wm is not None and ts <= wm:
                stop = True
                break
            pos = resolve_asset_index(t.get("asset"), token_ids)
            if pos is None:
                counters["parse_reject_count"] += 1   # 定位失败,拒绝入库(§4.1)
                continue
            new_rows.append({
                "transaction_hash": t.get("transactionHash"),
                "proxy_wallet": t.get("proxyWallet"),
                "condition_id": cid,
                "asset": str(t.get("asset")),
                "outcome_index": pos,                 # 我们解析的真实下标(非接口 999)
                "outcome_label": t.get("outcome"),
                "side": t.get("side"),
                "size": float(t.get("size") or 0),
                "price": float(t.get("price") or 0),
                "timestamp": ts,
                "ingested_at": now,
            })
        if stop or len(d) < PAGE:
            break
        offset += PAGE
        if offset >= OFFSET_WARN and (wm is None or (new_rows and new_rows[-1]["timestamp"] > wm)):
            # 逼近 offset 上限仍未追到 watermark → 更早历史够不着(诚实截断)。
            # ★保留已抓的近端成交(有效数据,不丢),计数供守护层压频/告警,只停止再往回翻。
            counters["offset_overflow_count"] += 1
            print(f"    ⚠️ offset 截断: {cid[:14]}.. 保留近端 {len(new_rows)} 笔,更早历史待压频回填",
                  flush=True)
            break
    counters["total_markets_polled"] += 1
    return new_rows


def poll_markets(markets: list[dict], counters: dict, wms: dict,
                 time_budget_s: float | None = None) -> int:
    """逐市场增量轮询落库。返回**实际轮询到的市场数**。

    ⭐为什么这一段也必须有时间闸:2026-08-04 晚给发现层/注册层加了闸之后,判据里写下
    「最坏周期 = 各闸之和 + 实测其余部分」—— 而轮询压根没有闸。代理一退化它就无界增长,
    那个"最坏"根本不是最坏。**只要还有一段无界,"周期有界"就是假的。**

    没轮到的市场不丢数据:watermark 驱动,下轮从上次断点继续。但必须**出声计数**,
    否则"每轮只轮询前 N 个"又会变成第 N+1 个永远轮不到而无人知晓。
    """
    total_written = 0
    polled = 0
    t0 = time.monotonic()
    for m in markets:
        if time_budget_s is not None and time.monotonic() - t0 >= time_budget_s:
            break                # 查询前判:查完再判必然超出一整个市场的分页时长
        polled += 1
        # 溢出时 poll_market 已保留近端并计数(不再抛异常丢批);守护层据心跳 offset_overflow 压频/告警
        rows = poll_market(m, counters, wms.get(m["condition_id"]))
        if rows:
            se.write_trades(rows)
            total_written += len(rows)
        time.sleep(0.1)  # 礼貌节流(全局无 429,仍留余量)
    counters["new_trades"] = total_written
    counters["poll_budget_skipped_count"] = len(markets) - polled
    return polled


def run_once(limit: int | None = None, sample: int = 5000, max_new: int | None = None,
             sample_time_budget_s: float | None = None,
             register_time_budget_s: float | None = None,
             poll_time_budget_s: float | None = None) -> dict:
    """发现一轮活跃市场 → 逐市场增量轮询落库 → 写审计心跳。返回本轮计数。

    v1.2:市场来自 Firehose 发现(refresh_and_registry),不再全量枚举。
    """
    # 计数先建、再发现 —— 发现层的网络失败(firehose/注册查 Gamma)也要计进同一份 net_*,
    # 因为三条链路共用同一条代理隧道(见 discovery_service.new_net_stats 的说明)。
    counters = new_counters()
    # 发现层与轮询层分开计时:实测这两段成本完全不同(firehose ~10s + 注册 40 个 ~52s
    # vs 轮询 50 个市场 ~84s),混在一起报数等于没报 —— 慢的时候仍然不知道该查哪一段。
    t_disc = time.monotonic()
    # watermark 驱动采样:翻页到接上上一轮为止。首轮(无 watermark)退化为翻满 sample。
    since_ts = cycle_state.read_state(FIREHOSE_WM_FILE, "newest_ts", None)
    markets, disc = refresh_and_registry(
        sample_limit=sample, max_new=max_new, net=counters, since_ts=since_ts,
        sample_time_budget_s=sample_time_budget_s,
        register_time_budget_s=register_time_budget_s)
    # 只在真采到东西时推进 watermark:采空(网络挂了)不许推进,否则那段时间永久跳过 ——
    # 「静默丢样本」的经典造法(丢得与结果相关才致命,而"网络坏的那几分钟"很可能不随机)。
    if disc.get("firehose_newest_ts"):
        cycle_state.write_state(FIREHOSE_WM_FILE, "newest_ts",
                                disc["firehose_newest_ts"], "firehose watermark")
    counters["discovery_seconds"] = time.monotonic() - t_disc
    print(f"发现层: {disc} | 耗时 {counters['discovery_seconds']:.0f}s", flush=True)
    if limit:
        markets = markets[:limit]
    # 一次性预取所有市场 watermark(空湖返回 {})
    con = se.duckdb_conn()
    try:
        wms = se.all_watermarks(con)
    finally:
        con.close()
    poll_markets(markets, counters, wms, time_budget_s=poll_time_budget_s)
    counters["register_fail"] = disc.get("register_fail", 0)
    counters["new_discovered"] = disc.get("new_discovered", 0)
    # 注册链路断供守护(静默失败):关心的是"成功登记了几个",不是"报了几个错"。
    # 单次注册失败会自愈(市场还在交易,下轮会被重新登记);真事故是接口挂掉 → 全部失败
    # → 新市场再也进不来、宇宙悄悄停止增长。尝试数 = 成功 + 失败;为 0 表示本轮没新市场
    # 可登记(正常,保持中立)。判据:10-tests/unit/test_register_supply_guard.py
    n_reg = counters["new_registered"] = disc.get("new_registered", 0)
    counters["register_zero_streak"] = cycle_state.next_zero_streak(
        cycle_state.read_streak(REGISTER_STREAK_FILE),
        newly=n_reg, attempted=n_reg + counters["register_fail"])
    cycle_state.write_streak(REGISTER_STREAK_FILE, counters["register_zero_streak"])
    # firehose 抽风检测:Polymarket 永远有成交,采样 0 笔 = 我们抓取失败,非"真没成交"。
    # 不能静默空转(§7)——计为异常供告警;数据不丢(下轮自愈:per-market 轮询会回填这段)。
    counters["firehose_fail"] = 1 if disc.get("firehose_trades", 0) == 0 else 0
    # ⚠️ 心跳**不在这里写**了(2026-08-04 移到 run_cycle 末尾)。原因:
    #   1. cycle_seconds/各阶段耗时要等整轮跑完才知道,写在这里只能填 0 —— 而"恒为 0 的
    #      字段"正是 CLAUDE.md 点名的强可疑信号;
    #   2. 旧位置在结算守望**之前**,于是"结算阶段被杀"仍会留下新鲜心跳 → 看门狗对那一段
    #      天然是瞎的。移到末尾后,心跳 = "整轮真的跑完了",新鲜度才真的有分辨力。
    # 代价:被杀的周期不再留下部分计数 —— 这是**对的**语义(没跑完就是没跑完),
    # 由看门狗的心跳新鲜度(HEARTBEAT_STALE_MIN)负责发现。
    print(f"本轮: 市场 {counters['total_markets_polled']} | 新成交 {counters['new_trades']} | "
          f"4xx {counters['http_4xx_count']} | 限流 {counters['rate_limit_hits']} | "
          f"offset溢出 {counters['offset_overflow_count']} | 解析拒绝 {counters['parse_reject_count']} | "
          f"网络重试 {counters['net_retry_count']}/{counters['net_attempt_count']} | "
          f"重试耗尽 {counters['net_give_up_count']} | 分页截断 {counters['poll_truncated_count']} | "
          f"没轮到 注册{counters['register_budget_skipped_count']}/轮询{counters['poll_budget_skipped_count']}",
          flush=True)
    return counters


# TODO(ops,非核心逻辑):
#  - 守护层:systemd timer(Persistent=true 防 suspend 漏跑)按频率调用 run_once。
#  - 自适应频率:offset_overflow 命中的市场下轮 <5 分钟;已关闭未结算市场升频抓结算窗口。
#  - offset_overflow_count>0 时紧急通知(Telegram/push)+ 暂停开关。
#  - 已结算市场移冷存、不再轮询。
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="发现+轮询一轮")
    ap.add_argument("--limit", type=int, default=None, help="只轮前 N 个市场(试跑)")
    ap.add_argument("--sample", type=int, default=5000, help="firehose 发现采样条数")
    ap.add_argument("--max-new", type=int, default=None, help="本轮最多注册 N 个新市场(试跑)")
    args = ap.parse_args()
    if args.once:
        run_once(limit=args.limit, sample=args.sample, max_new=args.max_new)
    else:
        ap.print_help()
