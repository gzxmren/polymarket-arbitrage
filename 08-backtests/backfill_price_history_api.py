#!/usr/bin/env python3
"""从 Polymarket 官方接口回填逐日历史价 + 权威结算真值。

动机(2026-07-15):库内 daily_price_snapshots 只有 2026-05-09 起 2 个月,且回测的
"是否已结算"用的是近似(engine/data.py:81 终值收敛判据),抽样实测该近似会静默丢掉
~26% 的已结算市场——丢掉的正是终值未收敛(市场看走眼)的盘,方向上会虚高"买热门"类
策略的净利。本脚本用 API 权威源同时解决历史深度与真值口径两个问题。

写入独立库文件,不触碰生产库 dashboard/backend/database/polymarket.db。
可断点续跑:已抓过的 slug 直接跳过。

用法:
    python3 08-backtests/backfill_price_history_api.py            # 全量(库内≥8快照的市场)
    python3 08-backtests/backfill_price_history_api.py --limit 50 # 小批试跑
    python3 08-backtests/backfill_price_history_api.py --test     # 写 /tmp,只跑 20 个
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import socket
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

def force_ipv4() -> None:
    """本机 IPv6 有 AAAA 记录但无出网,Python 默认选 IPv6 会报
    "Name or service not known"(curl -4 则正常)。强制 IPv4 解析。

    进程级副作用,故只在 main() 里调用 —— 被 import 时不应擅自改掉调用方的网络栈。
    """
    orig = socket.getaddrinfo

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return orig(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_only

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/prices-history"
SRC_DB = Path(__file__).resolve().parent.parent / "dashboard/backend/database/polymarket.db"
OUT_DB = Path(__file__).resolve().parent / "data/price_history_api.db"
UA = {"User-Agent": "polymarket-project-backfill/1.0"}


class TransientFetchError(Exception):
    """重试耗尽的网络失败。与"确认不存在"(返回 None)严格区分。

    两者混同会毒化断点续跑:一次网络抖动会把真实市场永久写成 no_market,
    续跑时被 done 集合跳过,再也不会重试。
    """


def fetch_json(url: str, tries: int = 4, timeout: int = 25):
    """GET JSON。返回 None = 确认不存在(404/4xx);抛 TransientFetchError = 重试耗尽。

    异常枚举(逐条对照 CLAUDE.md 清单):
    - resp.read() 的超时直接抛 TimeoutError,**不**裹在 URLError 里。
    - http.client.IncompleteRead(连接在 Content-Length 读满前断开)是 HTTPException
      子类,**不是** OSError —— 必须显式捕获,否则崩掉整批。
    - .decode() 可抛 UnicodeDecodeError(ValueError 子类)。
    - CLOB 实测偶发 TLS 错误(TLSV1_UNRECOGNIZED_NAME / UNEXPECTED_EOF),走 URLError。
    """
    for attempt in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            if e.code == 404 or (400 <= e.code < 500 and e.code != 429):
                return None  # 确认不存在
            time.sleep((5 if e.code == 429 else 1.5) * (attempt + 1))
        except (
            URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):
            time.sleep(1.5 * (attempt + 1))
    raise TransientFetchError(url)


def init_out_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS markets (
            slug         TEXT PRIMARY KEY,
            condition_id TEXT,
            token_yes    TEXT,
            token_no     TEXT,
            start_date   TEXT,
            end_date     TEXT,
            closed       INTEGER,
            resolved_yes REAL,      -- 官方权威真值: 1.0/0.0;未结算为 NULL
            volume       REAL,
            n_points     INTEGER,
            -- ok/no_market/no_token/no_history 是"已确认"的终态;
            -- error = 网络重试耗尽,下次续跑必须重试(见 done 集合的 status 过滤)
            status       TEXT,
            fetched_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS price_history (
            slug      TEXT NOT NULL,
            date      TEXT NOT NULL,
            price_yes REAL NOT NULL,
            PRIMARY KEY (slug, date)
        );
        CREATE INDEX IF NOT EXISTS idx_ph_date ON price_history(date);
        """
    )
    return conn


_SNAPSHOT_SQL = """
    SELECT market FROM daily_price_snapshots WHERE outcome='Yes'
    GROUP BY market HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC
"""

# 入场规则的常数,与 run_calibration_oos 保持一致(那里是预登记口径,此处不得另立)。
ENTRY_MAX_DTE = 14
VOL_FLOOR = 1000.0


def _candidate_slugs() -> list[str]:
    """能产生校准入场点的市场 = 需要权威真值的全集(实测 9858 个)。

    **直接复用引擎的价格序列与入场规则**,而不是另写一份等价 SQL:手写 SQL 实测会差 5 个
    (引擎丢弃同日重复快照、且 end_date 带时区时 julianday 解析为 NULL),这类口径漂移正是
    该被从构造上消灭的。

    注意全集不能按"快照数>=8"筛:9858 个候选里 4196 个在库里只有 1 个快照(监控只瞄到过
    一眼,但那一眼正落在入场窗口内),按快照数筛只剩 767/9858 → "真值口径"与"市场范围"
    两个变量混淆,测试 A 失去意义。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from engine.data import connect, load_price_series  # 延迟导入:仅此模式需要

    conn = connect(None)
    try:
        series = load_price_series(conn)
    finally:
        conn.close()

    out = []
    for ps in series.values():
        if ps.end_date is None:
            continue
        for d, px, cap in zip(ps.dates, ps.yes_prices, ps.cap_usd):
            dte = (ps.end_date - d).days
            if 1 <= dte <= ENTRY_MAX_DTE and cap >= VOL_FLOOR and 0.02 < px < 0.98:
                out.append(ps.market)
                break
    return out


def _whale_slugs() -> list[str]:
    """H6 原生宇宙 = 全量跟鲸鱼(H0 未过滤)信号触及的全部 distinct 市场 slug 之并集。

    为 C1 重测(PREREG_C1_WHALE_RESOLUTION_2026-07-21)准备权威真值。与 candidates 的关键区别:
    **不加任何价格/dte/cap 过滤** —— 直接取 generate_signals 在全库价格序列上产生的所有 BUY
    信号所触及的市场。任何结果相关的过滤(如 candidates 的 0.02<px<0.98 排除"已近定局"盘)
    都会重蹈静默筛样本覆辙(见 truth-source-bias-2026-07-15),故此处一律不加。

    宇宙必须是 **H0 全量并集**(H6 的超集),而非仅"H6 触及" —— 否则 H0/H1 等宽假设的分母不全。
    generate_signals 默认只跟 BUY、且未做 H5 精选,恰是 H0 口径,正确。

    **直接复用引擎的信号生成**,不另写等价 SQL(手写 SQL 会口径漂移,理由同 _candidate_slugs)。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from engine.data import connect, load_price_series  # 延迟导入:仅此模式需要
    from strategies.follow_whale import generate_signals

    conn = connect(None)  # 与 SRC_DB 同为 live DB(config.DASHBOARD_DB_FILE)
    try:
        prices_all = load_price_series(conn)
        signals = generate_signals(conn, prices_all)  # H0:仅 BUY,未精选,未过滤
    finally:
        conn.close()
    # distinct 市场并集;排序令断点续跑的 todo 顺序稳定
    return sorted({sig.market for sig in signals})


def load_universe(limit: int | None, min_snapshots: int, mode: str) -> list[str]:
    """待抓市场全集。candidates=校准全集(默认);snapshots=按库内快照数筛(看长序列用);
    whales=H6 原生宇宙(全量跟鲸鱼信号触及的市场并集,为 C1 重测准备真值)。"""
    if mode == "candidates":
        slugs = _candidate_slugs()
    elif mode == "whales":
        slugs = _whale_slugs()
    else:
        conn = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
        try:
            slugs = [r[0] for r in conn.execute(_SNAPSHOT_SQL, (min_snapshots,)).fetchall()]
        finally:
            conn.close()
    return slugs[:limit] if limit else slugs


def lookup_market(slug: str) -> dict | None:
    """Gamma 查市场。已结算/归档市场默认查询返回**空列表**,必须补 &closed=true。

    先试 closed=true:校准候选集里绝大多数已结算(入场窗口 dte1-14,早已到期),
    先试默认查询等于每个市场白跑一次请求(实测 69/分 vs 反过来后翻倍)。
    """
    for suffix in ("&closed=true", ""):
        res = fetch_json(f"{GAMMA}?slug={slug}{suffix}")
        if isinstance(res, list) and res:
            return res[0]
    return None


def parse_settlement(market: dict) -> float | None:
    """官方结算真值。outcomePrices=["1","0"] -> Yes 赢;["0","1"] -> No 赢。

    只认干净的 0/1;未结算(如 ["0.0135","0.9865"])返回 None。
    """
    if not market.get("closed"):
        return None
    raw = market.get("outcomePrices")
    if not raw:
        return None
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes = float(prices[0])
    except (json.JSONDecodeError, ValueError, TypeError, IndexError):
        return None
    return yes if yes in (0.0, 1.0) else None


def daily_series(token_id: str) -> list[tuple[str, float]]:
    """逐日 Yes 价。interval=max&fidelity=1440 实测可回溯 8+ 个月、日覆盖率 ~95%。

    (startTs/endTs 与 fidelity 同用会 400,故用 interval=max。)
    同一天多点时取**时间戳最大**的那个 —— 不假设 API 按时序返回。
    """
    res = fetch_json(f"{CLOB}?market={token_id}&interval=max&fidelity=1440")
    points = res.get("history") if isinstance(res, dict) else None
    by_day: dict[str, tuple[int, float]] = {}
    for p in points or []:
        try:
            ts = int(p["t"])
            price = float(p["p"])
            day = dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")
        except (KeyError, ValueError, TypeError, OSError):
            continue
        if day not in by_day or ts > by_day[day][0]:
            by_day[day] = (ts, price)
    return [(day, price) for day, (_, price) in sorted(by_day.items())]


def fetch_one(slug: str, sleep: float) -> dict:
    """抓单个市场。**只做网络+解析,不碰 DB** —— 写库统一回主线程串行做。

    返回 dict;status='error' 表示网络重试耗尽(下次续跑会重来),而非关于该市场的事实。
    """
    status, tok, settle, series, market = "ok", None, None, [], None
    try:
        market = lookup_market(slug)
        time.sleep(sleep)
        if not market:
            status = "no_market"
        else:
            settle = parse_settlement(market)
            raw_tok = market.get("clobTokenIds")
            if not raw_tok:
                status = "no_token"
            else:
                try:
                    tok = json.loads(raw_tok) if isinstance(raw_tok, str) else raw_tok
                except (json.JSONDecodeError, TypeError):
                    tok = None
                if not tok:
                    status = "no_token"
                else:
                    series = daily_series(tok[0])
                    time.sleep(sleep)
                    if not series:
                        status = "no_history"
    except TransientFetchError:
        status, tok, settle, series, market = "error", None, None, [], None
    return {"slug": slug, "status": status, "tok": tok, "settle": settle,
            "series": series, "market": market or {}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 个市场")
    ap.add_argument(
        "--universe", choices=["candidates", "snapshots", "whales"], default="candidates",
        help="candidates=能产生校准入场点的市场(默认,正确全集); snapshots=按快照数筛; "
             "whales=H6 原生宇宙(全量跟鲸鱼信号触及的市场并集,C1 重测真值)",
    )
    ap.add_argument("--min-snapshots", type=int, default=8, help="仅 --universe snapshots 用")
    ap.add_argument("--sleep", type=float, default=0.25, help="每次请求间隔秒(每线程内)")
    ap.add_argument("--workers", type=int, default=6, help="并发线程数(串行 ~13/分,太慢)")
    ap.add_argument("--test", action="store_true", help="测试模式:写 /tmp,只跑 20 个")
    args = ap.parse_args()

    force_ipv4()
    out_path = Path("/tmp/price_history_api_test.db") if args.test else OUT_DB
    limit = 20 if args.test else args.limit

    slugs = load_universe(limit, args.min_snapshots, args.universe)
    conn = init_out_db(out_path)
    # 只有"已确认"的终态算完成;status='error'(网络重试耗尽)必须重试,否则一次
    # 网络抖动就把真实市场永久打成缺失。
    done = {
        r[0] for r in conn.execute("SELECT slug FROM markets WHERE status != 'error'").fetchall()
    }
    todo = [s for s in slugs if s not in done]

    print(f"全集 {len(slugs)} 个市场;已完成 {len(done)};本次待抓 {len(todo)}")
    print(f"输出: {out_path}\n")

    stats = {"ok": 0, "no_market": 0, "no_token": 0, "no_history": 0, "error": 0}
    t0 = time.time()

    print(f"并发 {args.workers} 线程\n")
    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = [pool.submit(fetch_one, s, args.sleep) for s in todo]

    for i, fut in enumerate(as_completed(futures), 1):
        r = fut.result()
        slug, status, tok, settle, series, market = (
            r["slug"], r["status"], r["tok"], r["settle"], r["series"], r["market"],
        )
        stats[status] += 1
        conn.execute(
            """INSERT OR REPLACE INTO markets
               (slug, condition_id, token_yes, token_no, start_date, end_date,
                closed, resolved_yes, volume, n_points, status, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                slug,
                (market or {}).get("conditionId"),
                tok[0] if tok else None,
                tok[1] if tok and len(tok) > 1 else None,
                (market or {}).get("startDate"),
                (market or {}).get("endDate"),
                1 if (market or {}).get("closed") else 0,
                settle,
                float((market or {}).get("volumeNum") or 0),
                len(series),
                status,
                dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            ),
        )
        if series:
            conn.executemany(
                "INSERT OR REPLACE INTO price_history (slug, date, price_yes) VALUES (?,?,?)",
                [(slug, d, p) for d, p in series],
            )
        conn.commit()

        if i % 25 == 0 or i == len(todo):
            rate = i / max(time.time() - t0, 1e-9)
            eta = (len(todo) - i) / max(rate, 1e-9) / 60
            print(
                f"  [{i}/{len(todo)}] ok={stats['ok']} 无市场={stats['no_market']} "
                f"无token={stats['no_token']} 无历史={stats['no_history']} "
                f"网络失败={stats['error']} | {rate*60:.0f}/分 ETA {eta:.0f}分",
                flush=True,
            )

    pool.shutdown(wait=True)
    n_pts = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    rng = conn.execute("SELECT MIN(date), MAX(date) FROM price_history").fetchone()
    n_res = conn.execute("SELECT COUNT(*) FROM markets WHERE resolved_yes IS NOT NULL").fetchone()[0]
    conn.close()

    print(f"\n=== 完成 ===")
    print(f"市场: {stats}")
    print(f"日价点数: {n_pts}  日期范围: {rng[0]} .. {rng[1]}")
    print(f"官方已结算(有权威真值)的市场: {n_res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
