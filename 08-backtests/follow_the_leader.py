#!/usr/bin/env python3
"""跟单可交易性检验(2026-08-26)。

预登记单:`docs/PREREG_FOLLOW_THE_LEADER_2026-08-26.md`(先于本文件写成)
判据:     `10-tests/unit/test_follow_the_leader.py`(先于本文件写成)

## 问的是什么

V4 证明了「这批钱包比同格其他买方买得便宜约 5pp」是已验证的行为事实。
本单问那句要命的追问:**这 5pp 是他们的,还是你能拿到的?**

⭐**主判决量是跟单者的成交价,不是领先者的。**
「他们买完之后价格动不动」是**后果**不是定义 —— 价格动了但你追不上,等于没动。

## 支点:信号延迟

跟单价 = 该 (市场, 腿) 上、信号时刻 **+ Δ 分钟之后**的下一笔真实成交。
Δ 是"从别人成交到你能下单"的时间。主口径 Δ=5 分钟。
找不到这样的成交 ⇒ **该信号未成交**,不计收益,但必须出声计数
(混进去当 0 收益会把平均往 0 拉,而且丢的样本还看不见)。

## 为什么必须有置换零分布

2026-08-24 实测:**买方整体普遍比自己付的价多赢 0.5~1.6pp**,横跨所有价位。
⇒ 跟任何人买账面上都会显得赚。不设零分布,一定得出假绿灯。
"""
from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ⭐复用 V3/V4 已经过 review + 变异验证的那一份,不再抄第二遍
from wallet_skill_v3 import (  # noqa: E402
    BOOT_N, BOUNDARY, MIN_BETS_PER_WINDOW, PERM_N, RNG_SEED, TOP_FRACTION,
    WIN_SQL, _weighted_edge, bootstrap_by_market, pctl, rank_wallets,
)
# ⚠️ 只导入生产路径**真正用到**的。V5b 的闸门与红线是本文件自己的
#    `power_gate_return` / `arm_verdict_return`;把 v3 的 `power_gate`/`arm_verdict`
#    也导进来会让判据去测那几个**已被替换掉的旧函数**,制造"红线有人罩着"的假象
#    (2026-08-26 review 抓到)。

OUT_DIR = PROJECT_ROOT / "08-backtests" / "results" / "follow_2026-08-26"
DELAY_MIN = 5
"""信号延迟(分钟)。主口径 5 分钟 = 一个不赶时间的实现能做到的水平。
敏感性 1 / 15(15 = 采集器当前轮询间隔)。"""


def find_fill(signal_ts: datetime, later_trades: Sequence[dict[str, Any]],
              delay_min: int, closed: datetime) -> dict[str, Any] | None:
    """信号之后你**真正拿得到**的那一笔成交。拿不到返回 None。

    ⭐这是整个检验的支点:绝不许返回领先者自己那笔 —— 那 5pp 是他的不是你的。
    ⚠️ 返回 None 表示「未成交」,调用方必须把它计进漏斗,**不许当成 0 收益**。
    """
    cutoff = signal_ts + timedelta(minutes=delay_min)
    usable = [t for t in later_trades if cutoff <= t["ts"] <= closed]
    if not usable:
        return None
    t_fill = min(t["ts"] for t in usable)
    # ⚠️ 同一时刻可能有多笔成交 —— 用**成交量加权均价**,不许挑最便宜那笔
    #    (挑最便宜 = 白送跟单者一个更好的价)。与 FILL_SQL 逐字同义。
    same = [t for t in usable if t["ts"] == t_fill]
    sz = sum(t["size"] for t in same)
    if sz <= 0:
        return None
    return {"ts": t_fill, "price": sum(t["size"] * t["price"] for t in same) / sz, "size": sz}


def dedupe_signals(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, int], datetime]:
    """一个 (市场, 腿) 只留**最早**的那个信号。

    真实跟单者不会因为 5 个人先后买入同一个标的就买 5 次。
    """
    out: dict[tuple[str, int], datetime] = {}
    for r in rows:
        k = (r["cid"], r["leg"])
        if k not in out or r["ts"] < out[k]:
            out[k] = r["ts"]
    return out


def scan_signals(signals: Sequence[dict[str, Any]], delay_min: int = DELAY_MIN) -> dict[str, Any]:
    """把信号跑成跟单结果 + 漏斗。

    ⭐漏斗必须相加对账:signals == filled + unfilled。
    """
    filled: list[tuple[float, float]] = []      # (收益×金额, 金额)
    per_market: dict[str, list[float]] = {}
    n_unfilled = 0
    for s in signals:
        f = find_fill(s["t_sig"], s["later"], delay_min, s["closed"])
        if f is None:
            n_unfilled += 1
            continue
        dollars = f["price"] * f["size"]
        num = (s["win"] - f["price"]) * f["size"]
        filled.append((num, dollars))
        m = per_market.setdefault(s["cid"], [0.0, 0.0])
        m[0] += num
        m[1] += dollars
    return {
        "r_follow_pp": _weighted_edge(filled),
        "per_market": {k: (v[0], v[1]) for k, v in per_market.items()},
        "filled_pairs": filled,
        "funnel": {"signals": len(signals), "filled": len(filled), "unfilled": n_unfilled},
    }


# ---------------- V5b:仓位模型改为「每个信号投固定金额」(2026-08-26 用户拍板)----------------
#
# 🔴 V5 的仓位模型是错的:按**别人成交的规模**加权 —— 现实中没人被迫按别人的规模下注。
#    后果实测:最大 100 笔占总投入 35%,有效样本掉到几百,自举区间宽 22.7pp。
#    ⭐那不是"精度是物理极限",是仓位模型定错了。
#
# V5b:每个成交信号投 1 块钱,在跟单价 p 买 1/p 股,持有到结算。

MDE_MAX_RETURN_PCT = 2.0
"""功效闸(收益率口径)。换算依据:在 0.5 附近的价格上,**1 个概率点 ≈ 2% 收益率**
—— 与 V3/V4 的 1pp 门槛同源,不是另拍一个数。"""

ABS_FLOOR_RETURN_PCT = 1.0
"""绝对下限:平均收益率 ≥ +1.0%,给未建模的平台手续费留边际。"""

P_FLOOR_FOR_SUBSET = 0.10
"""强制配套子集的价格下限。低价下注收益率极端不对称(买 0.01 赢了 +9900%、输了 −100%)
⇒ 少数便宜的赢家会主导平均值。p ≥ 0.10 时收益率被限制在 10 倍以内,杂音小得多。"""


def signal_return(price: float, win: float) -> float | None:
    """投 1 块钱在价格 `price` 上,结算后的收益率。

    赢 ⇒ 1/p − 1;输 ⇒ −1(本金归零)。
    ⚠️ 价格 ≤ 0 不是可执行的成交,返回 None 而不是算出 inf —— 静默产生 inf
    会让下游的平均值变成 nan,而 nan 一路传下去比崩溃更难查。
    """
    if price is None or price <= 0:
        return None
    return win / price - 1.0


def summarize(returns: Sequence[float], prices: Sequence[float]) -> dict[str, Any]:
    """⭐预登记单强制:平均 / 中位 / 只看 p≥0.10 三个数**缺一不可**。

    任何只报其中一个的汇报都不算数 —— 三个数不一致本身就是信息
    (说明盈亏集中在某个价格区间)。
    """
    import statistics
    n = len(returns)
    if n == 0:
        return {"n_signals": 0, "mean_return_pct": 0.0, "median_return_pct": 0.0,
                "mean_return_pct_p_ge_010": 0.0, "n_p_ge_010": 0}
    sub = [r for r, p in zip(returns, prices) if p >= P_FLOOR_FOR_SUBSET]
    return {
        "n_signals": n,
        "mean_return_pct": 100.0 * statistics.fmean(returns),
        "median_return_pct": 100.0 * statistics.median(returns),
        "mean_return_pct_p_ge_010": 100.0 * statistics.fmean(sub) if sub else 0.0,
        "n_p_ge_010": len(sub),
    }


# ---------------- V6:主判决量改为「仅 p>=P_FLOOR 子集」----------------
# 预登记单:docs/PREREG_FOLLOW_V6_PFLOOR_2026-08-26.md
#
# 为什么砍掉低价:等额投入下买 0.01 赢了是 +9900%,少数彩票中奖主导平均值
# ⇒ V5b 实测零分布 p95 高达 +9.04%,功效闸直接失效。
# p>=0.10 时收益率被限制在 10 倍以内,噪声底应当大幅下降。
# ⚠️ 这是**预期不是保证** —— 功效闸仍放在所有红线之前,压不下来仍判「无判决」。

# 判决版的开跑硬门槛(预登记单 §6,不到不许跑)。
# 依据:实测 7 天窗 ≈ 40,668 市场 / 9,907 合格钱包;取其约七成作下限,留余量。
VERDICT_BOUNDARY = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
"""🔴 判决版的**最早**边界。验证窗必须整个落在这之后 —— 那是我一个数都没看过的区间。

⚠️ 2026-08-26 review 抓到的 CRITICAL:原实现里 `run(p_floor=0.10)` 这个**最自然的调用**
会拿到 `boundary=None → 旧边界 2026-08-08`(V5b 已看过的窗)+ `descriptive=False`
⇒ 直接产出一个**未标注的真判决**,正是本单 §2 明令禁止的「结果出来后挑标准」。
而 §6 的数据量门槛救不了它 —— 旧窗数据量绰绰有余。
⇒ 危险路径不许是默认:见 `run()` 里的硬拦截。"""

VERDICT_MIN_MARKETS = 20_000
VERDICT_MIN_WALLETS = 8_000
VERDICT_MIN_FILLS = 10_000


def summarize_v6(returns: Sequence[float], prices: Sequence[float],
                 p_floor: float = P_FLOOR_FOR_SUBSET) -> dict[str, Any]:
    """V6 主判决量:只保留 `p >= p_floor` 的信号,取平均收益率。

    ⚠️ 三个必报的数缺一不可(平均 / 中位 / 样本量),外加**被砍掉多少**要出声计数 ——
    V5b 证明了只看平均会漏掉「中位是 −100%」这种要命的事实。
    ⚠️ 边界取**闭区间**(恰好等于门槛的保留),写死免得两处实现理解不同。
    """
    import statistics
    kept = [r for r, p in zip(returns, prices) if p >= p_floor]
    dropped = len(returns) - len(kept)
    if not kept:
        return {"n_signals": 0, "mean_return_pct": 0.0, "median_return_pct": 0.0,
                "n_dropped_below_floor": dropped, "p_floor": p_floor}
    return {"n_signals": len(kept),
            "mean_return_pct": 100.0 * statistics.fmean(kept),
            "median_return_pct": 100.0 * statistics.median(kept),
            "n_dropped_below_floor": dropped, "p_floor": p_floor}


def verdict_run_allowed(n_markets: int, n_wallets: int,
                        n_fills: int | None = None) -> dict[str, Any]:
    """判决版能不能开跑(预登记单 §6)。

    ⭐数据攒够之前跑 = 小样本噪声,**跑了也不算**。三条全部满足才许跑,
    且拒绝时必须说清是哪一条不满足 —— 只说"不许跑"等于把人挡在门外还不给理由。
    """
    checks = [("已结算市场", n_markets, VERDICT_MIN_MARKETS),
              ("合格钱包", n_wallets, VERDICT_MIN_WALLETS)]
    # ⚠️ n_fills=None 表示"这一项还不知道"(前置检查阶段),**跳过它但要留痕** ——
    #    静默当成通过与真的通过看起来一样,那正是本项目的死因。
    if n_fills is not None:
        checks.append(("p>=门槛的成交跟单", n_fills, VERDICT_MIN_FILLS))
    missing = [f"{name} {got:,} < {need:,}" for name, got, need in checks if got < need]
    return {"allowed": not missing, "missing": missing,
            "fills_checked": n_fills is not None,
            "checked": {name: (got, need) for name, got, need in checks}}


def label_result(result: dict[str, Any], descriptive: bool) -> dict[str, Any]:
    """给结果贴上「描述性 / 判决」的标签。

    ⭐`p>=0.10` 这个数在 V5b 里我**已经看过**;用旧验证窗再跑一遍只能是描述性的,
    不能当判决(那就是"结果出来后挑标准")。标签写进 verdict 字段本身,
    而不是靠人记得 —— 只写在文档里的免责声明,日后一定会被跳过。
    """
    if not descriptive:
        return result
    out = dict(result)
    out["original_verdict"] = result.get("verdict")
    out["verdict"] = "DESCRIPTIVE_ONLY"
    # ⭐标签要打到**每一臂**:有人直接读 arms[..]["green"] 就绕过顶层了(review MEDIUM 5)
    for _a in out.get("arms", {}).values():
        if isinstance(_a, dict):
            _a["descriptive_only"] = True
    out["reading"] = ("⚠️ **描述性结果,不构成判决** —— 本次验证窗的数据在 V5b 已被看过,"
                      "拿它定标准即「结果出来后挑标准」。真判决须用今天之后结算的市场,"
                      "见 PREREG_FOLLOW_V6_PFLOOR_2026-08-26 §2。\n\n原判读(仅供参考):"
                      + str(result.get("reading", "")))
    return out


def power_gate_return(null_p95_pct: float) -> dict[str, Any]:
    """功效闸(收益率口径)。闸不过 ⇒ 判「无判决」,⛔ 不许报 FAIL/证伪。"""
    passes = null_p95_pct <= MDE_MAX_RETURN_PCT
    return {"null_p95_pct": null_p95_pct, "mde_max_pct": MDE_MAX_RETURN_PCT,
            "passes": passes, "verdict_if_blocked": None if passes else "NO_VERDICT",
            "reason": "" if passes else (
                f"置换零分布 p95 = {null_p95_pct:+.4f}% > 门槛 {MDE_MAX_RETURN_PCT:.1f}% ⇒ "
                f"本设计对能赚钱的量级**无分辨力**,不出判决")}


def arm_verdict_return(r_pct: float, null_p95_pct: float,
                       boot_q025_pct: float) -> dict[str, Any]:
    """单臂三条红线(收益率口径),算出来并接到判决上。"""
    checks = [
        ("A·置换", r_pct > null_p95_pct, f"{r_pct:+.4f}% vs 零分布p95 {null_p95_pct:+.4f}%"),
        ("A0·绝对下限", r_pct >= ABS_FLOOR_RETURN_PCT,
         f"{r_pct:+.4f}% vs 下限 {ABS_FLOOR_RETURN_PCT:+.1f}%"),
        ("B·自举稳健", boot_q025_pct > 0, f"2.5%分位 {boot_q025_pct:+.4f}% > 0"),
    ]
    failed = [f"{n}({d})" for n, ok, d in checks if not ok]
    # ⚠️ 键名一律用 _pct:这几个量的单位是**%收益率**,不是 V3/V4 的概率点(pp)。
    #    同名不同单位会让以后读 results.json 的人拿它去跟 1pp 门槛比(review 抓到)。
    return {"green": not failed, "r_v_pct": r_pct, "null_p95_pct": null_p95_pct,
            "boot_q025_pct": boot_q025_pct, "failed": failed,
            "detail": " | ".join(f"{n}:{'PASS' if ok else 'FAIL'}({d})" for n, ok, d in checks)}


def _summary_tail(summ: dict[str, Any]) -> str:
    """summary 的尾巴。⚠️ V5b 与 V6 字段不同,两种都要认 ——
    原版写死 V5b 的键,给了 p_floor 之后真跑直接 KeyError(2026-08-26 踩到)。"""
    if "n_dropped_below_floor" in summ:
        return f"n={summ['n_signals']:,} (砍掉低价 {summ['n_dropped_below_floor']:,})"
    return (f"p>=0.10 子集 {summ['mean_return_pct_p_ge_010']:+.3f}% "
            f"(n={summ['n_p_ge_010']:,})")


def arm_config(name: str, delay_min: int = DELAY_MIN,
               boundary: datetime | None = None) -> dict[str, Any]:
    """两臂完整口径。差异必须有且只有 market_range 一项。

    ⚠️ 2026-08-26 review 抓到两处**记录说谎**:
      · `weighting` 曾写 "dollar" —— 那是 V5 遗留;V5b 实际是**每信号等权**;
      · `delay_min` 曾读模块常量而非 `run()` 收到的参数 ⇒ 跑 Δ=15 的敏感性时,
        结果文件顶层写 15、臂内 config 写 5,同一份文件自相矛盾。
    ⇒ 记录必须由**实际使用的那个值**产生,不许各写各的。
    """
    return {"delay_min": delay_min, "min_bets_per_window": MIN_BETS_PER_WINDOW,
            "top_fraction": TOP_FRACTION, "boundary": (boundary or BOUNDARY).isoformat(),
            "dedupe": "one_per_asset", "weighting": "equal_per_signal", "side": "BUY_only",
            "time_firewall": "closed_time", "exclude_wash": True,
            "perm_n": PERM_N, "boot_n": BOOT_N, "seed": RNG_SEED,
            "market_range": "all" if name == "A" else "leg_balanced"}


def decide(arm_a: dict[str, Any], arm_b: dict[str, Any], power: dict[str, Any]) -> dict[str, Any]:
    """🔴 双臂判读规则 —— 预登记单 §8,先写死。"""
    if not power["passes"]:
        return {"verdict": "NO_VERDICT",
                "reading": f"⚪ 无判决 —— {power['reason']}。产出是关于**设计分辨力**的报告。"}
    a, b = arm_a["green"], arm_b["green"]
    if a and b:
        return {"verdict": "PASS", "reading":
                "🟢 两臂同时通过 ⇒ **跟单在数据上成立**。\n"
                "⚠️ 但本单默认你能以那个价成交**任意规模** —— 这是**乐观假设**,"
                "因为没有订单簿历史,不知道那笔成交能不能容下你的钱。"
                "⇒ 绿灯要打折看,**容量**与真实成交概率是第二关,本单答不了。"}
    if not a and not b:
        return {"verdict": "NOT_FOLLOWABLE", "reading":
                "🔴 两臂都不过 ⇒ **跟单不成立**。\n"
                "含义要说清:这**不是**「他们没本事」—— V4 已验证他们确实买得比别人便宜;"
                "而是那 5pp 是**领先者**的,等你看到信号再进场时已经拿不到了。"}
    good = "A(全部市场)" if a else "B(两腿配平市场)"
    return {"verdict": "UNRELIABLE", "reading":
            f"🟡 不可采信 —— 只有臂 {good} 通过。两臂唯一差异是市场范围,"
            f"故这同时**证伪了「本设计对单边采集偏移免疫」这个断言**。⛔ 不许只报好看的那臂。"}


# ⭐口径只许一份:生产走这条 SQL,判据里的 Python 版是规格(等价性由判据保证)。
FILL_SQL = """
WITH sig AS (
  SELECT cid, leg, min(ts) AS t_sig FROM src_V
  WHERE w IN (SELECT w FROM leaders) GROUP BY 1,2),
fill AS (
  SELECT s.cid, s.leg, s.t_sig, min(v.ts) AS t_fill
  FROM sig s JOIN src_V v ON v.cid = s.cid AND v.leg = s.leg
   AND v.ts >= s.t_sig + INTERVAL {delay} MINUTE
   AND v.ts <= v.t_close
  GROUP BY 1,2,3)
-- ⚠️ 同一时刻可能有多笔成交。原版按**价格最低**那笔取 —— 那是偏向跟单者的
--    (白送一个更好的价),口径不干净。改用那一刻**全部成交的成交量加权均价**,中性。
SELECT f.cid, f.leg,
       CAST(sum(t.size*t.price)/nullif(sum(t.size),0) AS DOUBLE) AS price,
       CAST(sum(t.size) AS DOUBLE) AS size,
       CAST(max(t.win) AS DOUBLE) AS win
FROM fill f JOIN src_V t ON t.cid=f.cid AND t.leg=f.leg AND t.ts=f.t_fill
GROUP BY f.cid, f.leg
"""


def run(out_dir: Path | str = OUT_DIR, delay_min: int = DELAY_MIN,
        p_floor: float | None = None, boundary: datetime | None = None,
        descriptive: bool = False) -> dict[str, Any]:
    """跑完整双臂跟单检验。⭐功效闸先跑先判。"""
    import duckdb
    sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "analysis"))
    import wallet_skill_v3 as v3
    import wallet_skill_v4_matched as v4
    import wash_trading_detector as wd

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wash = sorted(wd.load_excluded_markets())
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC'")
        con.execute("SET memory_limit='9GB'")
        con.execute("SET threads=4")
        lake = str(PROJECT_ROOT / "11-collector" / "data" / "raw" / "dt=*" / "*.parquet")
        regg = str(PROJECT_ROOT / "11-collector" / "data" / "registry" / "*.parquet")
        mt = str(PROJECT_ROOT / "11-collector" / "data" / "market_times" / "*.parquet")
        con.execute(f"""CREATE VIEW reg AS SELECT * FROM (
            SELECT condition_id, resolved_outcome, closed,
                   row_number() OVER (PARTITION BY condition_id ORDER BY snapshot_at DESC) rn
            FROM read_parquet('{regg}', union_by_name=true)) WHERE rn=1""")
        bnd = boundary or BOUNDARY
        # ⭐硬拦截(review CRITICAL 1):要出**判决**就必须用今天之后的窗。
        #    不是 warning、不是默认值 —— 直接拒绝,免得有人少传一个参数就静默出假判决。
        if p_floor is not None and not descriptive and bnd < VERDICT_BOUNDARY:
            raise ValueError(
                f"拒绝在已看过的窗上出判决:boundary={bnd.isoformat()} < "
                f"{VERDICT_BOUNDARY.isoformat()}。\n"
                f"`p>=0.10` 这个子集的结果在 V5b 已被看过,拿旧窗再跑一遍只能是描述性的。\n"
                f"⇒ 要描述:传 descriptive=True;要判决:传 boundary>=VERDICT_BOUNDARY。")
        B = "TIMESTAMP '" + bnd.strftime("%Y-%m-%d %H:%M:%S") + "'"
        con.execute("""CREATE TABLE base AS
            SELECT t.proxy_wallet AS w, t.condition_id AS cid, t.outcome_index AS leg,
                   t.size, t.price, to_timestamp(t.timestamp) AS ts,
                   try_cast(m.closed_time AS TIMESTAMP) AS t_close,
                   -- ⚠️ 必须显式 DOUBLE:DuckDB 会把字面量 1.0 推成 DECIMAL,
                   --    取回 Python 后与 float 相减直接抛 TypeError
                   -- ⭐win 判定**不许在这里再抄一份**:复用 wallet_skill_v3.WIN_SQL 那唯一一处。
                   --    2026-08-26 review 变异实测:抄第二份之后把 `<>` 改成 `=`
                   --    (win/lose 完全反转),35 条判据一条不红 —— 与 V3 当年同一形状。
                   CAST({WIN} AS DOUBLE) AS win
            FROM read_parquet(?) t JOIN reg r USING (condition_id)
            JOIN read_parquet(?) m USING (condition_id)
            WHERE r.closed AND r.resolved_outcome IS NOT NULL AND m.closed_time IS NOT NULL
              AND t.side='BUY' AND to_timestamp(t.timestamp) <= try_cast(m.closed_time AS TIMESTAMP)
            """.replace("{WIN}", WIN_SQL.replace("outcome_index", "t.outcome_index")
                                        .replace("resolved_outcome", "r.resolved_outcome")),
            [lake, mt])
        n_raw = con.execute("SELECT count(*) FROM base").fetchone()[0]
        if wash:
            con.execute("DELETE FROM base WHERE cid = ANY(?)", [wash])
        n_wash = con.execute("SELECT count(*) FROM base").fetchone()[0]
        con.execute("CREATE TABLE tagged AS SELECT *, "
                    + v3.WINDOW_SQL.format(B=B) + " AS win_tag FROM base")
        con.execute("CREATE TABLE mb AS SELECT cid, abs(sum(CASE WHEN leg=0 THEN size ELSE 0 END)"
                    "-sum(CASE WHEN leg=1 THEN size ELSE 0 END))/nullif(sum(size),0) AS leg_imb "
                    "FROM tagged GROUP BY 1")
        cut = con.execute(
            "SELECT quantile_cont(leg_imb,0.333) FROM mb WHERE leg_imb IS NOT NULL").fetchone()[0]
        funnel = {"raw_buy": n_raw, "after_wash": n_wash,
                  "dropped_by_wash": n_raw - n_wash,
                  "tagged_D": con.execute("SELECT count(*) FROM tagged WHERE win_tag='D'").fetchone()[0],
                  "tagged_V": con.execute("SELECT count(*) FROM tagged WHERE win_tag='V'").fetchone()[0],
                  "tagged_none": con.execute("SELECT count(*) FROM tagged WHERE win_tag IS NULL").fetchone()[0]}
        assert funnel["tagged_D"] + funnel["tagged_V"] + funnel["tagged_none"] == n_wash, "分窗对账不平"

        # ⭐门槛前置(review MEDIUM 7):原来跑在两臂 + 400 次置换**之后**,
        #    既浪费,又会在数据太少时先崩在 rank_wallets 上、根本走不到门槛。
        if p_floor is not None and not descriptive:
            pre = verdict_run_allowed(
                n_markets=con.execute(
                    "SELECT count(DISTINCT cid) FROM tagged WHERE win_tag='V'").fetchone()[0],
                n_wallets=con.execute(f"""SELECT count(*) FROM (
                    SELECT w FROM tagged WHERE win_tag='D' GROUP BY 1
                      HAVING count(*)>={MIN_BETS_PER_WINDOW}
                    INTERSECT
                    SELECT w FROM tagged WHERE win_tag='V' GROUP BY 1
                      HAVING count(*)>={MIN_BETS_PER_WINDOW})""").fetchone()[0])
            if not pre["allowed"]:
                out = {"boundary": bnd.isoformat(), "delay_min": delay_min,
                       "p_floor": p_floor, "descriptive": descriptive, "funnel": funnel,
                       "verdict_run_gate": pre,
                       "power_gate": {"passes": None, "null_p95_pct": None,
                                      "reason": "数据未攒够,未跑到功效闸"},
                       "verdict": "NOT_YET_ENOUGH_DATA",
                       "reading": "⚪ 数据未攒够,按预登记单 §6 **不出判决** —— "
                                  "小样本跑了也不算。缺:" + "; ".join(pre["missing"])}
                (out_dir / "results.json").write_text(
                    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                return out

        results: dict[str, Any] = {"boundary": bnd.isoformat(), "delay_min": delay_min,
                                   "p_floor": p_floor, "descriptive": descriptive,
                                   "leg_imb_cut": cut, "funnel": funnel, "arms": {}}
        arms = {}
        for arm in ("A", "B"):
            where = "TRUE" if arm == "A" else f"m.leg_imb <= {cut}"
            con.execute(f"""CREATE OR REPLACE TABLE a AS SELECT t.* FROM tagged t
                JOIN mb m USING (cid) WHERE {where} AND t.win_tag IS NOT NULL""")
            for tag in ("D", "V"):
                con.execute(f"CREATE OR REPLACE TABLE src_{tag} AS SELECT * FROM a WHERE win_tag='{tag}'")
            sc = {t: {r[0]: (r[1], r[2], r[4]) for r in con.execute(
                v4._wallet_scores_sql(f"src_{t}", v4.MIN_OTHER_TRADES)).fetchall()} for t in ("D", "V")}
            pool = sorted(w for w in sc["D"] if w in sc["V"]
                          and sc["D"][w][2] >= MIN_BETS_PER_WINDOW
                          and sc["V"][w][2] >= MIN_BETS_PER_WINDOW)
            top = rank_wallets({w: 100.0 * sc["D"][w][0] / sc["D"][w][1]
                                for w in pool if sc["D"][w][1]})

            def follow_for(wallets: Sequence[str]) -> tuple[float, dict[str, tuple[float, float]], dict[str, int]]:
                con.execute("CREATE OR REPLACE TABLE leaders(w VARCHAR)")
                con.executemany("INSERT INTO leaders VALUES (?)", [(x,) for x in wallets])
                # ⭐漏斗补上「去重前」这一级(预登记单 §9 要求,原实现直接跳过了):
                #    没有它就核实不了"多少笔原始买入坍缩成了多少个信号"
                n_raw_buys = con.execute("""SELECT count(*) FROM src_V
                    WHERE w IN (SELECT w FROM leaders)""").fetchone()[0]
                n_sig = con.execute("""SELECT count(*) FROM (SELECT cid,leg FROM src_V
                    WHERE w IN (SELECT w FROM leaders) GROUP BY 1,2)""").fetchone()[0]
                rows = con.execute(FILL_SQL.format(delay=delay_min)).fetchall()
                # ⭐V5b:每个信号投 1 块钱 —— 规模由**跟单者自己**决定,不跟别人的规模
                rets, prices = [], []
                pm: dict[str, list[float]] = {}
                for cid, _, px, _sz, win in rows:
                    r = signal_return(px, win)
                    if r is None:      # 价格 ≤0 不是可执行成交;出声计数,不静默丢
                        continue
                    rets.append(r); prices.append(px)
                    m = pm.setdefault(cid, [0.0, 0.0])
                    m[0] += r; m[1] += 1.0        # 按市场聚类:分子=收益率和,分母=笔数
                # ⭐V6:给了 p_floor 就走子集口径,否则沿用 V5b 全量口径
                summ = (summarize_v6(rets, prices, p_floor) if p_floor is not None
                        else summarize(rets, prices))
                # ⚠️ unfilled 用**独立 SQL** 数,不用 n_sig-filled 那种恒等式减法 ——
                #    减法永远对得上,发现不了 FILL_SQL 把信号算重或漏算(review 抓到)
                n_unfilled = con.execute(f"""
                    WITH sig AS (SELECT cid, leg, min(ts) AS t_sig FROM src_V
                                 WHERE w IN (SELECT w FROM leaders) GROUP BY 1,2)
                    SELECT count(*) FROM sig s WHERE NOT EXISTS (
                      SELECT 1 FROM src_V v WHERE v.cid=s.cid AND v.leg=s.leg
                        AND v.ts >= s.t_sig + INTERVAL {delay_min} MINUTE
                        AND v.ts <= v.t_close)""").fetchone()[0]
                assert n_sig == len(rows) + n_unfilled, (
                    f"漏斗对账不平:信号 {n_sig} != 成交 {len(rows)} + 未成交 {n_unfilled}")
                return (summ, {k: (v[0], v[1]) for k, v in pm.items()},
                        {"raw_buys_before_dedupe": n_raw_buys, "signals": n_sig,
                         "filled": len(rows), "unfilled": n_unfilled,
                         "bad_price": len(rows) - len(rets)})

            summ, per_market, fn = follow_for(top)
            r_follow = summ["mean_return_pct"]
            # 自举按**市场**聚类;bootstrap_by_market 返回的是 100*Σ分子/Σ分母,
            # 这里分子=收益率和、分母=笔数 ⇒ 得到的正是"平均收益率(%)"
            q025, q975 = bootstrap_by_market(per_market)
            import random
            rnd = random.Random(RNG_SEED)
            null = sorted(follow_for(rnd.sample(pool, len(top)))[0]["mean_return_pct"]
                          for _ in range(PERM_N))
            arms[arm] = arm_verdict_return(r_follow, pctl(null, 0.95), q025)
            arms[arm].update({"n_pool": len(pool), "n_top": len(top), "follow_funnel": fn,
                              "n_markets": len(per_market), "boot_q975_pct": q975,
                              "null_p50_pct": pctl(null, 0.50), "summary": summ,
                              "config": arm_config(arm, delay_min, bnd)})
            results["arms"][arm] = arms[arm]
            print(f"  臂{arm}: 池 {len(pool):,} 前10% {len(top):,} | 信号 {fn['signals']:,} "
                  f"→ 成交 {fn['filled']:,} (未成交 {fn['unfilled']:,}, 坏价 {fn['bad_price']}) \n"
                  f"        平均收益率 {summ['mean_return_pct']:+.3f}% | "
                  f"中位 {summ['median_return_pct']:+.3f}% | "
                  f"{_summary_tail(summ)}\n"
                  f"        零分布p95 {pctl(null,0.95):+.3f}% | 自举2.5% {q025:+.3f}%", flush=True)

        # ⭐开跑硬门槛(预登记单 §6)真的接在这里 —— 只算不用等于没有。
        if p_floor is not None and not descriptive:
            gate = verdict_run_allowed(
                n_markets=con.execute(
                    "SELECT count(DISTINCT cid) FROM tagged WHERE win_tag='V'").fetchone()[0],
                n_wallets=min(arms[a]["n_pool"] for a in ("A", "B")),
                n_fills=min(arms[a]["summary"]["n_signals"] for a in ("A", "B")))
            results["verdict_run_gate"] = gate
            if not gate["allowed"]:
                # ⚠️ 早退分支也要给出 power_gate 键,否则下游读它直接 KeyError
                results["power_gate"] = {"passes": None, "null_p95_pct": None,
                                         "reason": "数据未攒够,未跑到功效闸"}
                results.update({"verdict": "NOT_YET_ENOUGH_DATA",
                                "reading": "⚪ 数据未攒够,按预登记单 §6 **不出判决** —— "
                                           "小样本跑了也不算。缺:" + "; ".join(gate["missing"])})
                (out_dir / "results.json").write_text(
                    json.dumps(results, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
                return results
        worst = max(arms["A"]["null_p95_pct"], arms["B"]["null_p95_pct"])
        power = power_gate_return(worst)
        results["power_gate"] = power
        # ⭐描述版的标签写进 verdict 字段本身,不靠人记得。
        # ⚠️ 必须把**整个 results**(含 arms)喂给 label_result —— 原来喂的是 decide() 的
        #    返回值,那里面没有 arms ⇒ 每臂的标签根本没打上(2026-08-26 真跑发现,
        #    而判据传的是自造的带 arms 的字典所以绿着:测试没走生产路径,同一形状第六次)。
        results.update(decide(arms["A"], arms["B"], power))
        results = label_result(results, descriptive)
        (out_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return results
    finally:
        con.close()


def main() -> int:
    r = run()
    print("\n=== 功效闸 ===")
    print(f"  零分布p95 {r['power_gate']['null_p95_pct']:+.4f}% vs 门槛 {MDE_MAX_RETURN_PCT:.1f}% "
          f"⇒ {'通过' if r['power_gate']['passes'] else '未过'}")
    print(f"\n=== 判决: {r['verdict']} ===\n{r['reading']}")
    for a in ("A", "B"):
        print(f"\n  臂{a}: {r['arms'][a]['detail']}")
    print(f"\n  漏斗: {json.dumps(r['funnel'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
