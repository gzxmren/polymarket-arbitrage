#!/usr/bin/env python3
"""backfill_market_times.py — 把「答案何时揭晓」这条时间坐标取回来(2026-08-23)。

设计单:`docs/DESIGN_TIME_FIREWALL_2026-08-23.md`
判据:   `10-tests/unit/test_market_times.py`(先于本文件写成)

## 为什么需要它

母预登记单从立项就要求「时间防火墙」——判断一个人**预测得准**,前提是他下注时
答案还没揭晓。而注册表里一直没有可信的结算时刻:实测 15.7% 的成交落在 `end_date` 之后。

⭐**根因(2026-08-23 实测)**:`end_date` 根本不是市场结束时刻,它是**开赛时刻** ——
`endDate` 与 `gameStartTime` 的中位差是 **0 分钟**。三分之二的成交发生在开赛之后
(盘中交易),被 `end_date` 一刀切成了"结算后交易"这个假象。

## ⭐一个被当场拦下的错误做法(定义 vs 后果)

本工具**不许**用「价格第一次贴到 0 或 1」反推结算时刻。
CLAUDE.md 明令:*不许用与结果相关的变量筛样本或贴标签*。
价格贴边是「答案公开」的**后果**,不是定义;拿它当分界线会切掉
**价格正在移动的那一段**,而信息恰恰在那里 ⇒ 只剩没信息的下注。
判据 `test_no_price_derived_firewall_anywhere` 在源码层面钉死这一条。

## 边界(本工具答不了什么)

- `closedTime` 是平台**判定**的时刻,不是答案在现实中变已知的时刻
  (终场哨 → UMA 判定实测中位 243 分钟)⇒ 它是上界,不是精确值。
- 非体育盘没有 `gameStartTime`(实测覆盖 90.8%),那部分只有硬线可用。
- 只提供时间坐标,不判断任何一笔成交是否"利用了内幕"。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import http_client as hc            # noqa: E402  ⭐复用全项目唯一的 GET+重试+计数实现


def _ds():
    """⭐**延迟**导入 discovery_service,不在模块顶层。

    两个约束在这里打架,延迟导入同时满足两边:
    - 结构守卫(`test_nobody_else_hand_rolls_the_batch_url`)要求
      `condition_ids=` 的拼接**全项目只此一处** —— 本项目"照抄结构而不抽象"已犯 3 次,
      这个脚本的第一版又犯了第 4 次,是被那条判据当场抓住的。⇒ 必须复用它的 URL 构造。
    - review M4:那个模块在**导入时**就 `socket.getaddrinfo = ...` 全局强制 IPv4,
      顶层导入会让每一次 pytest 会话的 DNS 行为被悄悄改掉。
    ⇒ 延迟到真要发请求时才导入:测试导入本模块不受污染,而实际取数时 IPv4 照常生效
      (这个平台确实需要它,见 memory/api-backfill-2026-07-15)。
    """
    import discovery_service as ds
    return ds

GAMMA = "https://gamma-api.polymarket.com/markets"
# ⚠️ 三个硬事实,全部实测过(见 pitfall-gamma-batch-limit-truncates):
#   ① 不给 `limit=` 请求 85 个只回 20 —— **静默截断**
#   ② URL 硬限 8192 字节,110 个 cid 就整批 422
#   ③ 默认只回**未关闭**的市场 ⇒ 必须查两遍取并集
BATCH_LIMIT = 500
URL_BUDGET_BYTES = 7000
CLOSED_SUFFIXES = ("", "&closed=true")
OUT_DIR = PROJECT_ROOT / "11-collector" / "data" / "market_times"

# 红线(设计单 §5):用 closedTime 切,落在其后的成交占比必须 < 0.1%。实测 0.01%。
MEASURED_POST_CLOSED_REDLINE = 0.001

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
               "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
               "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z")


def parse_ts(v: Any) -> str | None:
    """把接口给的各种时间写法统一成 ISO8601(UTC)。

    ⚠️ 解析不出来一律 None,**不许**退化成 1970 或 now() ——
    那会让"没拿到时刻"和"拿到了一个时刻"看起来一样(静默失败的经典造法)。
    实测接口至少有三种写法:`2026-08-23 04:02:09.886203+00`、
    `2026-08-23T01:30:00Z`、`2026-08-23 01:30:00+00`。
    """
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    # `+00` 不是合法的 %z(需要 +0000);`+00:00:00` 是接口的畸形写法,截掉多余的秒
    if s.endswith("+00:00:00"):
        s = s[:-3]
    if s.endswith("+00"):
        s = s + ":00"
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return None


def parse_market(m: dict[str, Any]) -> dict[str, Any]:
    """从接口返回里抽出时间坐标。**只碰时间字段,一个价格字段都不读。**"""
    return {
        "condition_id": m.get("conditionId"),
        "closed_time": parse_ts(m.get("closedTime")),
        "game_start": parse_ts(m.get("gameStartTime")),
        "end_date": parse_ts(m.get("endDate")),
        "uma_status": m.get("umaResolutionStatus"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def hard_firewall(row: dict[str, Any]) -> str | None:
    """硬防火墙 = 平台判定结算的时刻。

    ⚠️ **不是** `end_date` —— 实测那是开赛时刻(与 gameStartTime 中位差 0 分钟)。
    """
    return row.get("closed_time")


def conservative_firewall(row: dict[str, Any]) -> str | None:
    """保守防火墙 = 赛前。信息完全对称,但只保留约三分之一的成交。"""
    return row.get("game_start")


class ReconcileLedger:
    """请求数 vs 返回数对账。⭐缺口必须**点名**,不许只记个数 ——
    只记个数答不了"丢的是哪一类",而丢得与结果相关才是致命的那种。"""

    def __init__(self) -> None:
        self.requested = 0
        self.returned = 0
        self.missing: set[str] = set()
        self.unexpected: set[str] = set()
        self.missing_reasons: dict[str, int] = {}

    def record(self, requested: Iterable[str], returned: Iterable[str],
               failures: Sequence[str] | None = None) -> None:
        req, ret = set(requested), set(returned)
        self.requested += len(req)
        self.returned += len(ret)
        miss = req - ret
        self.missing |= miss
        self.unexpected |= (ret - req)      # 回来的比请求的多 = 接口串行,同样是异常
        # ⭐2026-08-23 review(M3):原版只知道"谁丢了",答不出"为什么丢"。
        # 设计单 §5 红线 1 要求"缺口必须抽样归因" —— 光点名答不了
        # "是不是又踩到某个与结果相关的过滤器"这个真正要紧的问题。
        if miss:
            reason = ";".join(sorted(set(failures))) if failures else "接口返回里没有它(非网络失败)"
            self.missing_reasons[reason] = self.missing_reasons.get(reason, 0) + len(miss)

    def as_dict(self) -> dict[str, Any]:
        return {"requested": self.requested, "returned": self.returned,
                "missing_count": len(self.missing), "missing_sample": sorted(self.missing)[:20],
                "missing_reasons": self.missing_reasons,
                "unexpected_count": len(self.unexpected)}


def chunk_cids(cids: Sequence[str], budget: int = URL_BUDGET_BYTES) -> list[list[str]]:
    """按 URL 字节预算切批。⚠️ 用**最长**的后缀算,否则第一遍过、第二遍整批 422。"""
    ds = _ds()
    out: list[list[str]] = []
    for batch in ds.pack_condition_ids(list(cids), budget=budget):
        # ⭐字节预算由 discovery_service 负责;**BATCH_LIMIT 这一层由本函数补上**。
        # review H4:只挡字节数时,谁把预算调大就会切出超过 URL 里 `limit=` 的批
        # ⇒ 上游**静默截断**,多出来的只显示成"缺失",看起来跟普通丢包一样,
        # 不会有任何信号指向"是 limit 配错了"。
        for i in range(0, len(batch), ds.GAMMA_BATCH_LIMIT):
            out.append(list(batch[i:i + ds.GAMMA_BATCH_LIMIT]))
    assert all(len(b) <= ds.GAMMA_BATCH_LIMIT for b in out), "切批超过了 URL 里的 limit"
    return out


CID_RE = __import__("re").compile(r"^0x[0-9a-fA-F]{64}$")


def split_valid_cids(cids: Iterable[str]) -> tuple[list[str], list[str]]:
    """把 cid 分成合法/非法两堆。**非法的要出声计数,不是静默转义掉。**

    2026-08-23 review(M6):原版把 cid 直接拼进 URL,没有校验也没有转义。
    一个脏 cid(比如意外带了 `&`)会**无声破坏整条查询字符串**,而报错不会指向它。
    选择校验而不是转义:脏 cid 是数据质量问题,该被看见,转义只会把它藏起来。
    """
    good, bad = [], []
    for c in cids:
        (good if isinstance(c, str) and CID_RE.match(c) else bad).append(c)
    return good, bad


def batch_urls(cids: Sequence[str]) -> list[str]:
    """一批 cid → 两条 URL(默认 + closed=true)。两遍的并集才是完整答案。

    ⚠️ URL 由 `discovery_service.build_gamma_batch_url` 构造,**本文件不自己拼** ——
    见 `_ds()` 的说明:自己拼是本项目犯过 4 次的"照抄结构而不抽象"。
    """
    ds = _ds()
    return [ds.build_gamma_batch_url(cids, suf) for suf in ds.GAMMA_CLOSED_SUFFIXES]


def fetch_batch(cids: Sequence[str], ledger: ReconcileLedger,
                counters: dict | None = None, deadline: float | None = None
                ) -> tuple[list[dict[str, Any]], list[str]]:
    """取一批。返回 (解析好的行, 本批失败的原因列表)。"""
    seen: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for url in batch_urls(cids):
        data, failure = hc.request_json(url, urlopen, headers=_ds().UA, counters=counters,
                                        deadline=deadline, count_4xx=True)
        if failure is not None:
            failures.append(str(failure))
            continue
        if not isinstance(data, list):
            failures.append(f"返回不是列表: {type(data).__name__}")
            continue
        for m in data:
            if isinstance(m, dict) and m.get("conditionId"):
                seen[m["conditionId"]] = m
    ledger.record(cids, seen.keys(), failures)
    return [parse_market(m) for m in seen.values()], failures


SCHEMA_COLS = ("condition_id", "closed_time", "game_start", "end_date", "uma_status", "fetched_at")


def _as_str(v: Any) -> str | None:
    """强制净化成 str|None。

    ⭐2026-08-23 review(H2)用**真实坏输入**复现:`uma_status` 是唯一原样透传的字段
    (其余都过了 parse_ts / isoformat),上游哪天把它变成 dict/list/数字,
    `pa.array(..., type=pa.string())` 就抛 ArrowTypeError,整批(最多 1720 个市场)陪葬。
    更糟的是它会一路传到 `finally: flush()`,**在保命的兜底里再抛一次同样的异常**,
    于是"无论如何都落盘"这句承诺在这个具体故障下失效。
    """
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def write_rows(rows: Sequence[dict[str, Any]], path: str | Path) -> Path:
    """写一个 parquet 分片。

    ⚠️ append-only:每次跑写新文件,不改旧文件。
    ⚠️ **原子写**(review M5):先写临时文件再 os.replace ——
    直接写最终文件名时,进程在写一半被硬杀会在磁盘上留下损坏分片,
    虽然 load_done 能跳过它,但垃圾会随多次续跑累积。
    """
    import os

    import pyarrow as pa
    import pyarrow.parquet as pq
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({c: pa.array([_as_str(r.get(c)) for r in rows], type=pa.string())
                      for c in SCHEMA_COLS})
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)
    return path


def scan_shards(out_dir: str | Path) -> tuple[set[str], set[str]]:
    """扫已落盘分片,返回 (全部见过的 cid, 其中 closed_time 解析失败的 cid)。

    ⭐2026-08-23 review(H3):原版 `load_done` 只看 cid 在不在,**不看字段解析成没成功**。
    于是上游哪天换了个时间写法,`parse_market` 正确地记成 `closed_time=None`,
    落盘时 cid 有值 ⇒ 被标成"done" ⇒ **续跑机制会永久阻止它被重抓**,而对账层
    (只管"请求的 cid 有没有回来")对此**完全干净、看不出任何异常**。
    这是"记录事实 vs 使用事实只接了一头"在本文件里的第二处。
    """
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return set(), set()
    import pyarrow as pa
    import pyarrow.parquet as pq
    seen: set[str] = set()
    has_closed: set[str] = set()
    for f in sorted(out_dir.glob("*.parquet")):
        try:
            t = pq.read_table(f, columns=["condition_id", "closed_time"])
        except (pa.lib.ArrowException, OSError) as e:
            print(f"⚠️ 分片读不出来,已跳过(会被重新请求): {f.name} ({type(e).__name__}: {e})",
                  file=sys.stderr)
            continue
        for cid, ct in zip(t["condition_id"].to_pylist(), t["closed_time"].to_pylist()):
            if not cid:
                continue
            seen.add(cid)
            if ct:
                has_closed.add(cid)
    # 见过、但**没有任何一个分片**给出可解析的 closed_time ⇒ 硬防火墙值缺失
    return seen, seen - has_closed


def load_done(out_dir: str | Path) -> set[str]:
    """已经取到的 cid。断点续跑靠它 —— 16 万市场 × 两遍请求,重复跑等于几小时白费。

    ⚠️ 单个坏分片不该让整次续跑失效:pyarrow 的异常分属三个家族
    (ArrowInvalid→ValueError / ArrowTypeError→TypeError / ArrowIOError→OSError),
    共同基类是 pa.lib.ArrowException(2026-08-17 实测,凭"应该是 IO 错误"会漏两种)。
    """
    return scan_shards(out_dir)[0]


def select_todo(cids: Iterable[str], done: set[str]) -> list[str]:
    return [c for c in cids if c not in done]


def verify_against_lake(out_dir: str | Path, sample_markets: int = 3000) -> dict[str, Any]:
    """⭐红线 #2 的**真实执行**:拿刚取回的时刻去切真成交湖,数有多少笔落在结算之后。

    2026-08-23 review(H1)抓到:`evaluate_firewall` 只被单元测试用假数字调用过,
    生产代码一次都没调,而它的 docstring 却写着「接到判决上(不是文档里写写)」——
    那句话本身就是"文档里写写"。这个函数补上缺的那一头。

    抽样而不是全量:全湖 2,700 万笔 × join 十几万个市场太重,
    而红线判的是**比例**,抽样足够(抽样数写进结果,可复核)。
    """
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC'")          # ⚠️ 不设会静默用本机时区(JST)
        con.execute("SET memory_limit='6GB'")
        mt = str(Path(out_dir) / "*.parquet")
        lake = str(PROJECT_ROOT / "11-collector" / "data" / "raw" / "dt=*" / "*.parquet")
        row = con.execute(f"""
            WITH t AS (
              SELECT condition_id, try_cast(closed_time AS TIMESTAMP) AS t_closed
              FROM read_parquet('{mt}')
              WHERE closed_time IS NOT NULL
              USING SAMPLE {int(sample_markets)} ROWS (reservoir, 20260823))
            SELECT count(*),
                   count(*) FILTER (WHERE to_timestamp(l.timestamp) > t.t_closed)
            FROM read_parquet('{lake}') l JOIN t USING (condition_id)
        """).fetchone()
    except (duckdb.Error, OSError) as e:
        return {"error": f"{type(e).__name__}: {e}", "passes": None}
    finally:
        con.close()
    out = evaluate_firewall(int(row[0] or 0), int(row[1] or 0))
    out["sampled_markets"] = int(sample_markets)
    return out


def evaluate_firewall(trades_total: int, trades_after_closed: int) -> dict[str, Any]:
    """设计单 §5 红线 2 的**算式**。真实执行见 `verify_against_lake`(它是唯一调用方之一,
    另一个是判据)。"""
    frac = (trades_after_closed / trades_total) if trades_total else 0.0
    return {"trades_total": trades_total, "trades_after_closed": trades_after_closed,
            "fraction_after": frac, "redline": MEASURED_POST_CLOSED_REDLINE,
            "passes": frac < MEASURED_POST_CLOSED_REDLINE}


def settled_cids(limit: int | None = None) -> list[str]:
    """从注册表取已结算市场的 cid。只读,不改注册表。"""
    import duckdb
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    reg = str(PROJECT_ROOT / "11-collector" / "data" / "registry" / "*.parquet")
    sql = f"""
      SELECT condition_id FROM (
        SELECT condition_id, closed, end_date,
               row_number() OVER (PARTITION BY condition_id ORDER BY snapshot_at DESC) rn
        FROM read_parquet('{reg}', union_by_name=true))
      WHERE rn = 1 AND closed ORDER BY end_date DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    try:
        return [r[0] for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _write_summary(out_dir: str | Path, summary: dict[str, Any]) -> Path:
    """把本轮的运行记录落盘。**每一条 return 路径都要经过它**。

    ⭐它是侧表的"活着"信号:`collector_watchdog._market_times_problems` 靠这份记录的
    `run_at_utc` 判断侧表有没有真的跑成过(timer 是 active 但每轮都崩,单看 systemctl
    是看不出来的 —— 例如 SIGKILL 之后 `.backfill.lock` 残留,此后每轮立刻 return 2)。
    ⇒ 只写"干成了活"的那条路径 = 追平积压之后守护必然误报。

    ⚠️ **原子写**,理由与同文件 `write_rows`(review M5)逐字相同,而且在这里更要紧:
    service 有 `TimeoutStartSec=100`,超时会被 SIGKILL。若正好停在写一半,磁盘上会留下
    **截断的 JSON**;而**同一次 SIGKILL 也会跳过 `finally` 里的 `lock.unlink()`**
    ⇒ 锁残留 ⇒ 此后每轮都 `return 2`、再也走不到这里 ⇒ 那份坏记录**永远不会被刷新**,
    上一轮的诊断信息(reconcile/failures)一并丢掉。
    """
    import os

    path = Path(out_dir) / "last_run_summary.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填市场时间坐标(时间防火墙)")
    ap.add_argument("--test", action="store_true", help="测试模式:写 /tmp,绝不碰生产目录")
    ap.add_argument("--limit", type=int, default=None, help="只取最近 N 个已结算市场")
    ap.add_argument("--out", default=None)
    ap.add_argument("--sleep", type=float, default=0.25, help="批间隔(秒),对上游友好")
    ap.add_argument("--max-batches", type=int, default=None)
    # ⭐分片落盘间隔。断点续跑的**写入端** —— 原版只在全部跑完时写一次,
    # 于是 2 小时的任务中途挂掉就全丢,而 load_done 无从可续。
    # 这正是本项目犯过 4 次的「记录事实 vs 使用事实,只接了一头」。
    ap.add_argument("--flush-every", type=int, default=20, help="每 N 批落一次盘")
    ap.add_argument("--retry-null", action="store_true",
                    help="把「拿到记录但 closed_time 解析失败」的市场重新抓一遍")
    ap.add_argument("--skip-verify", action="store_true", help="跳过收尾时的红线实跑")
    args = ap.parse_args(argv)

    _ds()   # ⭐在这里触发延迟导入:强制 IPv4 的副作用只发生在**运行时**,不在 import 时
            #     (getaddrinfo 由 discovery_service 设置,见 _ds() 的说明)

    out_dir = Path(args.out) if args.out else (Path("/tmp/market_times_test") if args.test else OUT_DIR)
    # ⭐重入锁(review M6):两个进程同时跑会各自读到旧的 done 快照、重复请求。
    lock = out_dir / ".backfill.lock"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = open(lock, "x")
        lock_fd.write(str(__import__("os").getpid()))
        lock_fd.flush()
    except FileExistsError:
        print(f"🔴 已有一个实例在跑(锁文件 {lock})。确认没有后删掉它再来。", file=sys.stderr)
        return 2

    try:
        done, null_closed = scan_shards(out_dir)
        # ⭐H3:「拿到记录但 closed_time 解析失败」必须出声,不许被"done"两个字盖过去
        if null_closed:
            print(f"⚠️ 有 {len(null_closed):,} 个市场已落盘但 **closed_time 解析失败**"
                  f"(硬防火墙值缺失)。{'本次会重抓' if args.retry_null else '加 --retry-null 可重抓'}")
        if args.retry_null:
            done = done - null_closed

        raw_todo = select_todo(settled_cids(args.limit), done)
        todo, bad_cids = split_valid_cids(raw_todo)
        if bad_cids:
            print(f"⚠️ 跳过 {len(bad_cids)} 个格式不合法的 condition_id(样例 {bad_cids[:3]})")
        print(f"已结算市场待取 {len(todo):,} 个(已完成 {len(done):,} 个,跳过)")
        if not todo:
            # ⭐无活可干**也要留痕**。原版这里直接 return、什么都不写 ⇒ 侧表一旦追平
            # 积压(实测 12,246 个、每小时 1,700 个 ⇒ 约 7 小时),记录就永远停在追平
            # 那一刻 ⇒ 看门狗从那天起天天误报,而误报会把真信号淹掉。
            # 形状 = 本项目犯过 4 次的「记录事实 vs 使用事实,只接一头」。
            # ⚠️「没活可干」(正常)与「有活没干成」(异常)靠 pending 区分,
            #    不许靠"产出为 0" —— 回填任务追平后产出本来就该是 0。
            print("没有要做的。")
            _write_summary(out_dir, {
                "run_at_utc": datetime.now(timezone.utc).isoformat(),
                "pending": 0, "fetched": 0, "written": 0,
                "invalid_cids": len(bad_cids), "no_work": True,
            })
            return 0

        ledger = ReconcileLedger()
        counters: dict[str, int] = {}
        batches = chunk_cids(todo)
        if args.max_batches:
            batches = batches[:args.max_batches]
        rows: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        all_failures: list[str] = []
        written = 0
        t0 = time.time()

        def flush() -> None:
            nonlocal pending, written
            if not pending:
                return
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            try:
                write_rows(pending, out_dir / f"market_times-{stamp}.parquet")
                written += len(pending)
            except Exception as e:      # noqa: BLE001
                # ⭐review H2:若这批里有畸形数据,直接抛会连 `finally` 的兜底一起打翻,
                # 于是"无论如何都落盘"这句承诺在最需要它的时候失效。
                # 出声、丢掉这批、让它下次被重抓 —— 但**绝不**让它拖垮整次运行。
                print(f"🔴 落盘失败,本批 {len(pending)} 条丢弃(下次会重抓): "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
            finally:
                pending = []

        try:
            for i, batch in enumerate(batches, 1):
                got, failures = fetch_batch(batch, ledger, counters)
                rows.extend(got)
                pending.extend(got)
                all_failures.extend(failures)
                if i % args.flush_every == 0:
                    flush()
                if i % 20 == 0 or i == len(batches):
                    print(f"  批 {i}/{len(batches)} | 已取 {len(rows):,}(已落盘 {written:,}) | 对账 "
                          f"{ledger.returned:,}/{ledger.requested:,} | 用时 {time.time()-t0:.0f}s",
                          flush=True)
                if args.sleep:
                    time.sleep(args.sleep)
        finally:
            flush()

        have_closed = sum(1 for r in rows if r["closed_time"])
        have_game = sum(1 for r in rows if r["game_start"])
        summary: dict[str, Any] = {
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
            # ⭐本轮**开始时**待取的总数(不是本轮切出来的那一片:单轮有 --max-batches
            #   上限,追不平是正常的)。守护据此区分「有活没干成」与「没活可干」。
            "pending": len(todo),
            "reconcile": ledger.as_dict(),
            "fetched": len(rows), "written": written,
            "have_closed_time": have_closed, "have_game_start": have_game,
            "invalid_cids": len(bad_cids),
            "network_counters": counters,
            "failures": dict(__import__("collections").Counter(all_failures)),
        }
        print(f"\n=== 对账 ===\n  {json.dumps(ledger.as_dict(), ensure_ascii=False)}")
        print(f"=== 覆盖 ===\n  closed_time {have_closed:,}/{len(rows):,} "
              f"({100*have_closed/max(len(rows),1):.1f}%) | "
              f"game_start {have_game:,}/{len(rows):,} ({100*have_game/max(len(rows),1):.1f}%)")
        if all_failures:
            print(f"=== 失败 {len(all_failures)} 次 ===\n  {summary['failures']}")
        print(f"=== 网络计数 ===\n  {counters}")

        # ⭐红线 #2 在这里**真的被执行**(review H1:原先只有单元测试拿假数字调过它)
        if not args.skip_verify:
            v = verify_against_lake(out_dir)
            summary["firewall_check"] = v
            if v.get("error"):
                print(f"=== 红线#2 没跑成 ===\n  {v['error']}")
            else:
                flag = "✅ 过" if v["passes"] else "🔴 未过"
                # ⚠️ 一律 .get:汇总的打印不该因为少一个键就把整次运行打死
                print(f"=== 红线#2 防火墙有效性({flag}) ===\n"
                      f"  抽 {v.get('sampled_markets', 0):,} 个市场共 {v.get('trades_total', 0):,} 笔,"
                      f"落在 closedTime 之后 {v.get('trades_after_closed', 0):,} 笔 "
                      f"= {100*v.get('fraction_after', 0):.4f}%(红线 <{100*MEASURED_POST_CLOSED_REDLINE:.1f}%)")

        _write_summary(out_dir, summary)
        print(f"=== 写到 ===\n  {out_dir}")
    finally:
        lock_fd.close()
        lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
