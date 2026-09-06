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
import socket
import time
from urllib.request import urlopen

import cycle_state
import discovery_service as ds
import http_client as hc
import rotation
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
    # 落盘时 timestamp 取不出合法日期而被丢弃的行(2026-08-27)。
    # ⛔ 不并进 parse_reject_count:那是"资产下标定位失败",成因与处置都不同。
    "trade_day_unparsable_count",
    # offset 截断拆成可修/不可修两半(2026-08-06):合成一个数就分辨不出该不该管
    "offset_overflow_cold_count", "offset_overflow_warm_count",
    "poll_truncated_count",          # 网络断掉导致分页提前结束(≠ 翻到底)
    "rate_limit_give_up_count",      # 被 429 打满而放弃(与"隧道坏了"分开,处置不同)
    "poll_budget_skipped_count",     # 本轮**没轮到**轮询的市场数(硬名额 + 时间闸,两者都算)
    "poll_never_polled_count",       # 其中"从来没采过"的有多少 —— 饿死是否在好转的直接量
    # ⭐两种"没轮到"的**处置方向不同**,必须分开:
    #   名额不够  → 结构性,要么让单市场更便宜,要么拆独立 timer
    #   时间闸咬住 → 可变,冷启动回填贵 / 网络慢,下轮会继续
    # 合成一个数会让"轮转片实际完成了几个"变得不可知,而那正是一圈多久的决定量。
    "poll_timegate_skipped_count",   # 其中**被时间闸**砍掉的(名额本来给了它,没跑完)
    # 轮转片里"越过没做成的那个继续报上来"的个数。稳态恒 0(循环是做一个报一个,
    # 中断即中断);非 0 = 有人加了新的跳过路径,或某个市场每轮都做不成把游标卡住了。
    # 消费者:run_cycle 据它推进 rotation_hole_streak → alerts(判据 test_rotation.py)。
    "poll_rotation_holes",
) + ds.NET_COUNTER_KEYS + ds.DISCOVERY_COUNTER_KEYS


def new_counters() -> dict:
    """一轮的全部计数。集中在这里定义,避免"某处忘了初始化 → KeyError 或漏计"。"""
    return dict.fromkeys(COUNTER_KEYS, 0)


# 稀有停法:落 parquet 已满足"出声"的字面要求,但**日志才是人每天真会看的地方**。
# 处置方向各不相同,故必须报出是哪一种,而不是笼统说"有异常"。
ABNORMAL_STOPS = {
    "rate_limit_give_up_count":    "被限流打满 → 压频/拉长间隔,别去查隧道",
    "firehose_rate_limited_count": "采样被限流打断 → 本轮活跃市场集合被缩小",
    "firehose_http_error_count":   "采样撞到**没见过的** HTTP 错误 → 查接口变更",
    "register_inconclusive_count": "查 Gamma 没查成(≠ 查不到)→ 这些市场留在积压、不沉底",
}


def abnormal_stop_note(counters: dict) -> str | None:
    """非零才出声;全零返回 None。

    ⭐两头都要焊死(CLAUDE.md 防洪判据):稳态**完全静默**,否则天天一行 = 没信号;
    真异常**必推**,否则等于没修。判据 test_discovery_rate_limit_and_deadline.py。
    """
    hit = [(k, counters.get(k, 0)) for k in ABNORMAL_STOPS if counters.get(k, 0)]
    if not hit:
        return None
    return "⚠️ 稀有停法: " + " | ".join(f"{ABNORMAL_STOPS[k]}(×{v})" for k, v in hit)


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


# ⚠️ 本模块的网络失败退避是 **1.5 秒**,而发现层那份是 1.2 秒(见 http_client.RETRY_SLEEP_S)。
# 两个值都没有实测支撑,只是历史上各写各的。**故意保留这个差异**:统一它是行为变更
# (退避时长直接进周期耗时 ⇒ 改变慢周期分布),该单独论证、单独验证,
# 不能夹在"纯重构"里偷偷做(CLAUDE.md 铁律 1:一次只动一个自变量)。
# 并入 ⏰2026-08-11 阈值校准一起定。
POLL_RETRY_SLEEP_S = 1.5


def _get(url: str, counters: dict, tries: int = 5, deadline: float | None = None):
    """GET;429/4xx 计数;重试耗尽返回 GIVE_UP(区别于确认空的 [] 和 4xx 的 None)。

    2026-08-06 起这只是 `http_client.request_json` 的**薄适配层** ——
    重试/计数/归因/时间闸的真身在那一个模块里,全项目只此一份。
    合并的理由见 http_client 的模块说明(两份实现已经分叉过一次,而判据不会变红)。

    `deadline`(绝对时刻,`time.monotonic()` 尺度)是这次合并**新拿到的能力**:
    此前本函数根本不收这个参数,于是单市场翻页在结构上就装不上时间闸 ——
    那是全系统唯一还无界的一段。⚠️ 默认 `None` = 老行为,本次没有任何调用方传它;
    真正把闸装上去是**下一步**,要单独出判据、单独验证。
    """
    data, failure = hc.request_json(
        url, urlopen, headers=UA, counters=counters, tries=tries,
        retry_sleep_s=POLL_RETRY_SLEEP_S, count_4xx=True, deadline=deadline)
    if failure is None:
        return data
    if isinstance(failure, int):
        return None              # 4xx:对方明确答复,不是网络断
    return GIVE_UP               # 重试耗尽 / 被限流打满 / 预算用尽


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
            # ★保留已抓的近端成交(有效数据,不丢):不写反而更坏 —— 市场停在"从没采过",
            #   下轮再翻一遍再撞顶,永远拿不到数据还每轮白烧 20 页(判据焊死了这一点)。
            #
            # ⭐两种截断处置完全不同,必须分开数(判据 test_history_truncation.py):
            #   cold = 首次全量回填就超过 10,000 笔 → **接口硬约束,修不掉**
            #   warm = 有水位线却没追上,即两轮之间攒爆了 → **可修**,它等价于
            #          「轮转一圈太久」,是 test_poll_rotation.py 那条红线被踩穿的现场证据
            mode = "cold" if wm is None else "warm"
            counters["offset_overflow_count"] += 1
            counters[f"offset_overflow_{mode}_count"] += 1
            # 留痕。数据丢失修不掉(接口硬顶),但**它此前是静默的**:实测 377 个受影响市场
            # 序列开头空白 p50 199 天(对照组 1 天),而它们长得和"刚开盘的新市场"一模一样。
            counters.setdefault("truncations", []).append({
                "condition_id": cid, "detected_at": now, "mode": mode,
                "offset_reached": offset, "kept_rows": len(new_rows),
                "oldest_kept_ts": new_rows[-1]["timestamp"] if new_rows else None,
                "watermark_before": wm,
            })
            # ⚠️ 这句原本写的是"更早历史待压频回填" —— **那个回填不存在,也不可能存在**
            # (offset 顶 10000 是硬约束,不是频率问题)。假承诺比没说明更坏:
            # 它让读的人以为有人在管,于是没人去管。
            print(f"    ⚠️ offset 截断[{mode}]: {cid[:14]}.. 保留近端 {len(new_rows)} 笔;"
                  f"更早历史**永久取不回**(接口 offset 顶 {OFFSET_CAP}),已留痕",
                  flush=True)
            break
    counters["total_markets_polled"] += 1
    return new_rows


# ---------- 轮询名额的分配(2026-08-06)----------
#
# 病:`run_once` 里原本是一刀裸切片 `markets[:limit]`,而 markets 按 firehose 新鲜度排序
# ⇒ 每轮永远只采「最近成交的前 50 个」,冷门盘每轮都被砍、可能一生轮不到。
# 实测(DuckDB 查全量数据湖):注册表 67,156 个市场里只有 30,430 个采到过成交;
# 进行中的盘 20,340 个里 13,163 个(65%)一笔都没采过。丢得**与结果相关**(专丢低频盘)。
#
# ⚠️ 而 `poll_budget_skipped_count` 当时在**截断之后**才算,对这一刀完全是瞎的 ——
# 心跳里逐轮写着「没轮到 轮询0」。恒为 0 是因为它没在看,不是因为没事。
#
# ⭐为什么不能纯轮转:实测单市场成交率 p99=126/小时、最热 3,954/小时。
# 纯轮转一圈 ~5 小时,最热的盘会攒 ~19,770 笔 > OFFSET_CAP(10,000)→ 分页截断
# = 用一个新的丢数据换掉旧的。故劈成两片(推导与红线见 test_poll_rotation.py)。
# ⚠️ 24 不是拍的,是被两条红线**夹出来**的唯一窗口(判据先写,参数后定):
#   下界:一圈必须短于 OFFSET_CAP / p99.9 成交率 = 10000/859 ≈ 11.6 小时 → 至少 23
#   上界:新鲜片必须占多数(热门盘的保护)→ 至多 24
# ⭐余量很薄(p99.9 一圈攒 ~9,200 / 硬顶 10,000,只剩 8%)。这不是配得巧,
# 是**名额本身就不够**:1029 个可轮询 vs 每轮 50 个,而 50 已被时间闸 170s 顶死。
# 真要松快,得先让单市场轮询变便宜或拆出独立 timer —— 那是另一件事,不在本次。
POLL_ROTATE_SHARE = 24   # 每轮固定留给游标轮转的名额;其余给"最近成交"
POLL_CURSOR_FILE = se.DATA_ROOT / "state" / "poll_cursor.json"


def poll_fresh_share(limit: int) -> int:
    """新鲜片名额。轮转片不许吃掉全部 —— 那会让热门盘等一圈而攒爆分页上限。"""
    return max(0, limit - POLL_ROTATE_SHARE)


def select_poll_targets(markets: list[dict], wms: dict, limit: int | None,
                        cursor: str) -> tuple[list[dict], rotation.Rotation, int]:
    """选出本轮要轮询的市场。返回 (选中列表, **轮转对象**, 被砍掉的个数)。

    `markets` 已按 firehose 新鲜度排序(下标 0 = 最近成交)。

    - **新鲜片**:照旧取最前面几个。热门盘天然一直"新鲜",于是仍每轮被采 ——
      这不是巧合:成交率高 ⇔ 一直排在最前,两者是同一件事。
    - **轮转片**:在剩下的里按 condition_id 游标轮转 + 回卷,给冷门盘一个**有界**的等待。

    ⭐2026-08-07:返回的第二个值从"新游标"改成 `Rotation` 对象。
    旧签名直接交出**规划的最后一个**,而本轮很可能被时间闸砍断只做了一部分 ——
    照它写游标,被砍的尾巴每圈都被跳过(洞 1)。新接口下游标只能由
    `rot.commit()` 给出,而它只认 `rot.done()` 报过的。三处共用同一份规则。
    """
    total = len(markets)
    if limit is None or limit >= total:
        # 全都要 ⇒ 没有轮转这回事。给一个空轮转:commit() 恒为 None ⇒ 调用方不写游标,
        # 游标文件保持原值 —— 与旧行为(原样写回 cursor)效果一致,且少一次无谓写盘。
        return list(markets), rotation.Rotation([], cursor, 0), 0

    n_rot = min(POLL_ROTATE_SHARE, limit)
    picked = list(markets[:limit - n_rot])
    taken = {m["condition_id"] for m in picked}
    rest = [m for m in markets if m["condition_id"] not in taken]
    rot = rotation.Rotation(rest, cursor, n_rot)   # 排序/二分/回卷/防重取都在里面
    picked += rot.batch
    return picked, rot, total - len(picked)


def count_never_polled(markets: list[dict], wms: dict, counters: dict) -> None:
    """本轮可轮询里"从来没采过"的个数(没有 watermark = 一笔都没收过)。

    这是衡量饿死是否在好转的**直接量**。没有它,"65% 从没采过"修没修好
    只能靠人手工去查数据湖 —— 那等于没人会知道。
    """
    counters["poll_never_polled_count"] = sum(
        1 for m in markets if m["condition_id"] not in wms)


def flush_truncations(counters: dict) -> None:
    """把本批攒下的截断痕迹冲进数据湖,并清空缓冲(可重复调用,天然幂等)。

    ⚠️ `poll_market` 有**两个**调用方:本模块的 `poll_markets` 和回填清扫。
    只在一处冲盘 = 痕迹只记一半,而漏掉的恰是回填清扫扫的那批(已关闭且没采过,
    撞顶概率最高)—— 又一次"丢得与结果相关"。故抽成函数,两处都调。
    """
    recs = counters.get("truncations")
    if recs:
        se.write_truncations(recs)
        counters["truncations"] = []


def poll_markets(markets: list[dict], counters: dict, wms: dict,
                 time_budget_s: float | None = None, on_done=None) -> int:
    """逐市场增量轮询落库。返回**实际轮询到的市场数**。

    `on_done(market)` 在**每采完一个**之后调用一次(默认无操作)。
    调用方传 `rotation.Rotation.done` 进来 —— 这是"不报做了什么就推不动游标"的落点:
    被时间闸砍断时,没走到的那些压根没被报过,游标自然停在真做过的地方。
    新鲜片的市场也会被报进来,轮转对象自己会忽略不属于它的(见 rotation.done)。

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
            se.write_trades(rows, counts=counters)   # counts:坏时间戳丢弃数进心跳
            total_written += len(rows)
        if on_done is not None:
            on_done(m)   # 零成交也算做完了:轮转的义务是"轮到过",不是"采到东西"
        time.sleep(0.1)  # 礼貌节流(全局无 429,仍留余量)
    # 截断痕迹冲盘。留在内存里等于没留 —— 每轮是独立进程,退出即失忆。
    flush_truncations(counters)
    counters["new_trades"] = total_written
    timegate_skipped = len(markets) - polled
    counters["poll_timegate_skipped_count"] = timegate_skipped
    # ⚠️ 累加而非赋值:硬名额砍掉的那一批已由 select_poll_targets 记在这里了。
    # 写成 `=` 就会把它抹掉 —— 那正是改之前"没轮到 轮询恒 0"的成因(在截断之后才算)。
    counters["poll_budget_skipped_count"] += timegate_skipped
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
    # 一次性预取所有市场 watermark(空湖返回 {})。
    # ⚠️ 必须在选名额**之前**取:"有没有 watermark" = "从没采过",是可见性的来源。
    con = se.duckdb_conn()
    try:
        wms = se.all_watermarks(con)
    finally:
        con.close()
    # 分母是**截断前**的全部可轮询市场 —— 截断后再统计就是在问"被留下的那些怎么样",
    # 而我们要问的恰恰是被砍掉的那些。
    count_never_polled(markets, counters=counters, wms=wms)
    cursor = cycle_state.read_state(POLL_CURSOR_FILE, "cursor", "")
    markets, rot, skipped = select_poll_targets(markets, wms, limit, cursor)
    counters["poll_budget_skipped_count"] = skipped
    poll_markets(markets, counters, wms, time_budget_s=poll_time_budget_s,
                 on_done=rot.done)
    # 游标只走过**真采过**的那些。一个都没采成(网络全挂/闸值极小)→ commit() 为 None
    # → 不写游标,整片留给下轮。照旧无条件写回的话,被砍的尾巴要多等一整圈(约 10 小时)。
    new_cursor = rot.commit()
    if new_cursor is not None:
        cycle_state.write_state(POLL_CURSOR_FILE, "cursor", new_cursor, "轮询游标")
    counters["poll_rotation_holes"] = rot.holes
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
    note = abnormal_stop_note(counters)
    if note:
        print(note, flush=True)
    print(f"本轮: 市场 {counters['total_markets_polled']} | 新成交 {counters['new_trades']} | "
          f"4xx {counters['http_4xx_count']} | 限流 {counters['rate_limit_hits']} | "
          # 拆开报:冷=接口硬约束(没得治),增量=轮转一圈太久(可治)。
          # 只报总数的话,"该不该动手"这个问题日志答不出来。
          f"offset溢出 {counters['offset_overflow_count']}"
          f"(冷{counters['offset_overflow_cold_count']}/增量{counters['offset_overflow_warm_count']}) | "
          f"解析拒绝 {counters['parse_reject_count']} | "
          f"网络重试 {counters['net_retry_count']}/{counters['net_attempt_count']} | "
          f"重试耗尽 {counters['net_give_up_count']} | 分页截断 {counters['poll_truncated_count']} | "
          f"没轮到 注册{counters['register_budget_skipped_count']}"
          f"/轮询{counters['poll_budget_skipped_count']}"
          # 「从没采过」与「本轮没轮到」必须并排看:前者是**存量欠账**(修复效果的直接量),
          # 后者是本轮流量。只报后者会让人以为"每轮都砍这么多"是稳态而心安。
          f"(其中从没采过 {counters['poll_never_polled_count']})",
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
