#!/usr/bin/env python3
#
# ============================================================================
# 🛑 已归档 · 2026-09-05 —— 方向三(跟单领先者)已于 2026-09-04 判决关闭
# ============================================================================
# 判决:V6 判决版跑出 `NOT_FOLLOWABLE`(功效闸通过:零分布 p95 +1.45% < 2.0%,
#       ⇒ 这次的红是真结论,不是「尺子看不见」)。两臂全红:
#         臂A 平均 +1.248% / 中位 +0.100% / n=16,879  ← 连随机挑钱包的 +1.4516% 都打不过
#         臂B 平均 -0.112% / 中位 **-100.000%** / n=11,281
# 依据:memory `verdict-v6-not-followable-2026-09-04.md`
#       关闭设计单 `docs/DESIGN_CLOSE_DIRECTION3_2026-09-05.md`
#
# 🔴 验证窗已烧掉:预登记单规定它**只跑一次**,2026-09-04 跑了、也看了结果。
#    ⛔ 任何后续方案**不许**再拿 boundary>=2026-08-27 那段数据当「干净的一次检验」——
#       那是在已看过的数据上试第二个方案(多重比较)。要出新判决必须另立验证窗,
#       或在新预登记单里明确登记为第二次并加严红线。
#
# ⛔ 不许做的事(预登记单 §4 / 关闭设计单 §三点五 明文禁止):
#    调 p_floor(0.05/0.15/0.20)挑好看的 / 改「前 10%」比例 / 看到结果后改 Δ 或持有期。
#
# 本文件保留不删:它是「这个方向怎么被否掉的」完整记录,判据也全部保留。
# ============================================================================
"""方向三 V3 —— 钱包战绩持续性,双臂对照重测(2026-08-23)。

预登记单:`docs/PREREG_WALLET_SKILL_V3_2026-08-23.md`(先于本文件写成)
判据:     `10-tests/unit/test_wallet_skill_v3.py`(先于本文件写成)

## 为什么重测(V2 那次判决为什么作废)

2026-08-19 判 🔴,主判 +3.6782pp 未越置换零分布 p95 = **+4.1646pp**。
**但赚钱只需要约 1pp** ⇒ 那把尺子的最小可分辨效果是所需的 4 倍,
对能赚钱的量级**结构性失明**。「未越红线」在那个设计下不构成证伪,只说明看不见。

⭐所以本版把**功效闸**放在所有红线**之前**:零分布若仍然太宽,
判「无判决」并输出一份关于设计缺陷的报告 —— **不许再报一个误导性的 FAIL**。

## 三个前置条件(缺一不可,都已具备)

1. 时间防火墙(`closed_time` 覆盖 99.9915%)⇒ 保证下注时答案还没揭晓
2. 刷单市场可排除
3. 单边采集偏移已检验 ⇒ 判 🟡 部分污染 ⇒ **必须双臂对照**

## 口径更正(V2 未写清)

edge = `win − 成交价`,用的是**实际成交价**,所以它**已经扣掉了入场时付出的价差**。
持有到结算无出场成本 ⇒ **edge > 0 即为赚**,不需要再减一次 0.5pp。
⚠️ 但平台手续费未建模,故绝对下限保留 +0.5pp 作为安全边际。
"""
from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "08-backtests" / "results" / "wallet_skill_v3_2026-08-23"

# ---------------- 预登记常量(先写死,看到结果后不许改)----------------
BOUNDARY = datetime(2026, 8, 8, 0, 0, 0, tzinfo=timezone.utc)
ONE_HOUR = timedelta(hours=1)

MIN_BETS_PER_WINDOW = 20     # 两窗各自的最少下注数
TOP_FRACTION = 0.10          # 主判决对象:D 窗前 10%
PERM_N = 400                 # 置换次数
BOOT_N = 1000                # 自举次数(按**市场**聚类重抽)
RNG_SEED = 20260823          # 唯一随机种子(复现用)。⚠️ 不许各处各写一遍

MDE_MAX_PP = 1.0
"""⭐功效闸门槛:零分布 p95 必须 ≤ 这个值,否则判「无判决」。

取 1.0pp 的依据不是统计惯例,而是**经济意义** —— 每天可部署约 $566K,
1pp 的 edge ≈ 每天 $2,830。一把最小刻度大于 1pp 的尺子,
对"能不能赚钱"这个问题是结构性失明的(08-19 就是 4.16pp)。"""

ABS_FLOOR_PP = 0.5
"""绝对下限。edge 已扣入场价差,理论上 >0 即赚;
留 0.5pp 是对**未建模的平台手续费**的安全边际(实测部分体育盘有 taker 费)。"""


def window_of(ts: datetime, closed: datetime) -> str | None:
    """一笔成交属于哪个窗。返回 'D' / 'V' / None(不参与)。

    ⭐**V 窗要求下单也在边界之后** —— 排名只能在边界 T 时刻形成,
    T 之前下的注不可能是照它下的。只按结算时间切窗是**事后口径**,不可交易。
    ⚠️ 时间防火墙:下注时答案已揭晓的(ts > closed)一律不算。
    """
    if ts > closed:
        return None                      # 答案已揭晓,不是预测
    if closed < BOUNDARY:
        return "D"
    return "V" if ts >= BOUNDARY else None


def trade_edge(side: str, outcome_index: int, resolved_outcome: float, price: float) -> float:
    """一笔成交每股赚了多少(概率点,小数)。

    ⚠️ `win = (outcome_index != resolved_outcome)` —— **与字面读法正好相反**,
    `resolved_outcome` 是赔付值不是赢家编号。已用末价独立验证
    (resolved_outcome=0 时 outcome_index=1 的末价中位 0.999)。
    """
    win = 1.0 if outcome_index != resolved_outcome else 0.0
    return (win - price) if side == "BUY" else (price - win)


def rank_wallets(d_edges: dict[str, float], top_fraction: float = TOP_FRACTION) -> list[str]:
    """按发掘窗成绩排名,取前 top_fraction。

    ⚠️ 入参**只有发掘窗的成绩** —— 结构上就拿不到验证窗的任何信息,
    泄漏「想写都写不出来」,而不是靠人记得别写。
    """
    if not d_edges:
        raise ValueError("发掘窗没有任何合格钱包 —— 检查窗口边界或最少下注数门槛,"
                         "不要让它一路传到 rnd.sample 才炸(报错会指不到真正的原因)")
    ordered = sorted(d_edges, key=lambda w: (-d_edges[w], w))
    k = max(1, int(len(ordered) * top_fraction))
    return ordered[:k]


def arm_config(name: str) -> dict[str, Any]:
    """两臂的完整口径。⭐差异必须**有且只有** market_range 一项(判据焊死)。"""
    return {
        "min_bets_per_window": MIN_BETS_PER_WINDOW,
        "top_fraction": TOP_FRACTION,
        "boundary": BOUNDARY.isoformat(),
        "weighting": "dollar",
        "time_firewall": "closed_time",
        "exclude_wash": True,
        "win_rule": "outcome_index != resolved_outcome",
        "perm_n": PERM_N,
        "boot_n": BOOT_N,
        "market_range": "all" if name == "A" else "leg_balanced",
    }


def power_gate(null_p95_pp: float) -> dict[str, Any]:
    """⭐⭐ 功效闸 —— 在所有红线**之前**跑,先判。

    08-19 的真正死因:零分布 p95 = +4.1646pp,而赚钱只需约 1pp。
    用一把最小刻度 4 倍于所需的尺子量出"读数为零",然后当成"没有厚度" ——
    那次的 🔴 什么都没证明。
    ⇒ 闸不过时,产出是**一份关于设计缺陷的报告**,判决栏写 NO_VERDICT,
       **绝不允许**报 FAIL/证伪。
    """
    passes = null_p95_pp <= MDE_MAX_PP
    return {
        "null_p95_pp": null_p95_pp,
        "mde_max_pp": MDE_MAX_PP,
        "passes": passes,
        "verdict_if_blocked": None if passes else "NO_VERDICT",
        "reason": "" if passes else (
            f"置换零分布 p95 = {null_p95_pp:+.4f}pp > 门槛 {MDE_MAX_PP:.1f}pp ⇒ "
            f"本设计对能赚钱的量级**无分辨力**,不出判决"),
    }


def arm_verdict(r_v_pp: float, null_p95_pp: float, boot_q025_pp: float) -> dict[str, Any]:
    """单臂三条红线,**算出来并接到判决上**(本项目犯过「算了红线没接上」)。"""
    checks = [
        ("A·置换", r_v_pp > null_p95_pp,
         f"{r_v_pp:+.4f}pp vs 零分布p95 {null_p95_pp:+.4f}pp"),
        ("A0·绝对下限", r_v_pp >= ABS_FLOOR_PP,
         f"{r_v_pp:+.4f}pp vs 下限 {ABS_FLOOR_PP:+.1f}pp"),
        ("B·自举稳健", boot_q025_pp > 0,
         f"2.5%分位 {boot_q025_pp:+.4f}pp > 0"),
    ]
    failed = [f"{n}({d})" for n, ok, d in checks if not ok]
    return {"green": not failed, "r_v_pp": r_v_pp, "null_p95_pp": null_p95_pp,
            "boot_q025_pp": boot_q025_pp, "failed": failed,
            "detail": " | ".join(f"{n}:{'PASS' if ok else 'FAIL'}({d})" for n, ok, d in checks)}


def decide(arm_a: dict[str, Any], arm_b: dict[str, Any], power: dict[str, Any]) -> dict[str, Any]:
    """🔴 双臂判读规则 —— 预登记单 §8,先写死。

    ⛔ 一臂绿一臂红 ⇒ 「不可采信」,**两个方向都是**;
       不许因为某一臂好看就单独汇报它。
    """
    if not power["passes"]:
        return {"verdict": "NO_VERDICT", "reading":
                f"⚪ 无判决 —— {power['reason']}。"
                f"本次产出是关于**设计分辨力**的报告,不是关于方向三的结论。"}
    a, b = arm_a["green"], arm_b["green"]
    if a and b:
        return {"verdict": "PASS", "reading":
                "🟢 两臂同时通过第一关。⚠️ **这不等于 edge** —— 只是有资格进"
                "**第二关**(成本、容量、真实成交概率)。母单铁律:🟢 才是风险区,应受更严审视。"}
    if not a and not b:
        return {"verdict": "FALSIFIED", "reading":
                "🔴 两臂都不过 ⇒ 证伪。⭐前提是功效闸已过(本设计看得见 1pp 量级),"
                "故本次的 🔴 与 08-19 那次不同,是真的没信号。"}
    good = "A(全部市场)" if a else "B(两腿配平市场)"
    return {"verdict": "UNRELIABLE", "reading":
            f"🟡 不可采信 —— 只有臂 {good} 通过,另一臂不过。"
            f"两臂唯一差异是市场范围,故最可能的解释是**单边采集偏移**在起作用"
            f"(数据只记单边,买入股数落在赢家腿的比例高于应有的 50%;"
            f"当次实测值见本次结果的 leg_win_share 字段),不是真信号。"
            f"⛔ 不许只汇报好看的那一臂。"}


# ---------------- 以下是真正跑数据的部分 ----------------

def _weighted_edge(rows: Sequence[tuple[float, float]]) -> float:
    """金额加权 edge(pp)。rows = [(edge*size, dollars), ...]"""
    num = sum(r[0] for r in rows)
    den = sum(r[1] for r in rows)
    return 100.0 * num / den if den else 0.0


def bootstrap_by_market(per_market: dict[str, tuple[float, float]], n: int = BOOT_N,
                        seed: int = RNG_SEED) -> tuple[float, float]:
    """按**市场**聚类重抽(赛事内多笔成交相关,按笔重抽会低估方差)。"""
    keys = list(per_market)
    if not keys:
        return (0.0, 0.0)
    keys.sort()      # ⭐SQL 行序不确定(并行 GROUP BY),不排序则同种子两次跑结果不同
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        pick = [per_market[keys[rnd.randrange(len(keys))]] for _ in range(len(keys))]
        out.append(_weighted_edge(pick))
    out.sort()
    return (out[int(0.025 * len(out))], out[int(0.975 * len(out))])


def permutation_null(pool: Sequence[str], k: int,
                     v_by_wallet: dict[str, tuple[float, float]],
                     n: int = PERM_N, seed: int = RNG_SEED) -> list[float]:
    """零分布:**随机**抽同样多的钱包(不看 D 窗成绩),算它们的 R_V。"""
    rnd = random.Random(seed)
    # ⭐必须先排序:入参来自没有 ORDER BY 的 SQL,DuckDB 并行执行行序不确定 ⇒
    # 同一个种子会抽到不同的钱包,结果不可复现。本项目 2026-08-19 踩过同一个坑。
    pool = sorted(pool)
    out = []
    for _ in range(n):
        grp = rnd.sample(pool, k)
        out.append(_weighted_edge([v_by_wallet[w] for w in grp if w in v_by_wallet]))
    out.sort()
    return out


def pctl(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    i = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[i]


# ⭐三处口径抽成具名常量:**判据跑的就是生产跑的那一份表达式**。
# 2026-08-23 review(HIGH1)抓到:原版 run() 在 SQL 里另写了一份 win/edge/分窗,
# 而判据测的是 window_of()/trade_edge() —— 那两个函数**从未被 run() 调用**,是替身。
# 谁把下面的 `<>` 手滑改成 `=`(正是本项目反复强调最易改错的那个符号),21 条判据全绿。
# 这是 2026-08-06 记下的最贵教训「替身替掉被测对象本身」的重演。
WIN_SQL = "CASE WHEN outcome_index <> resolved_outcome THEN 1.0 ELSE 0.0 END"
EDGE_SQL = "(CASE WHEN dir=1 THEN win-price ELSE price-win END)"
WINDOW_SQL = ("CASE WHEN ts > t_close THEN NULL "
              "WHEN t_close < {B} THEN 'D' "
              "WHEN ts >= {B} THEN 'V' ELSE NULL END")
"""⚠️ WINDOW_SQL 自带时间防火墙(`ts > t_close` → NULL),与 `window_of()` **逐字同义**;
不许依赖外面 WHERE 子句来实现防火墙,否则两份实现的语义又会分叉。"""

AGG_SQL = """
CREATE OR REPLACE TABLE bets AS
SELECT t.proxy_wallet AS w, t.condition_id AS cid,
       to_timestamp(t.timestamp) AS ts,
       try_cast(m.closed_time AS TIMESTAMP) AS t_close,
       t.size, t.price, t.outcome_index,
       CASE WHEN t.side='BUY' THEN 1 ELSE -1 END AS dir,
       {WIN} AS win,
       t.size*t.price AS dollars
FROM read_parquet(?) t
JOIN reg r USING (condition_id)
JOIN read_parquet(?) m USING (condition_id)
WHERE r.closed AND r.resolved_outcome IS NOT NULL AND m.closed_time IS NOT NULL
  AND to_timestamp(t.timestamp) <= try_cast(m.closed_time AS TIMESTAMP)   -- 时间防火墙
"""


def run(out_dir: Path | str = OUT_DIR) -> dict[str, Any]:
    """跑完整双臂检验。⭐功效闸先跑先判(见 power_gate)。"""
    import sys
    import duckdb
    sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "analysis"))
    import wash_trading_detector as wd

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wash = sorted(wd.load_excluded_markets())
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC'")          # ⚠️ 不设会静默用本机时区
        con.execute("SET memory_limit='9GB'")
        con.execute("SET threads=4")
        lake = str(PROJECT_ROOT / "11-collector" / "data" / "raw" / "dt=*" / "*.parquet")
        regg = str(PROJECT_ROOT / "11-collector" / "data" / "registry" / "*.parquet")
        mt = str(PROJECT_ROOT / "11-collector" / "data" / "market_times" / "*.parquet")
        con.execute(f"""CREATE VIEW reg AS SELECT * FROM (
        SELECT condition_id, resolved_outcome, closed,
               row_number() OVER (PARTITION BY condition_id ORDER BY snapshot_at DESC) rn
        FROM read_parquet('{regg}', union_by_name=true)) WHERE rn=1""")
        con.execute(AGG_SQL.replace("{WIN}", WIN_SQL.replace("outcome_index", "t.outcome_index")
                                          .replace("resolved_outcome", "r.resolved_outcome")),
                [lake, mt])
        n_after_join = con.execute("SELECT count(*) FROM bets").fetchone()[0]
        if wash:
            # ⚠️ 不许 f-string 拼 SQL(今天第二次犯,上一次是 wash_trading_detector 的 SET 注入)
            con.execute("DELETE FROM bets WHERE cid = ANY(?)", [wash])
        n_after_wash = con.execute("SELECT count(*) FROM bets").fetchone()[0]
        # ⭐「进 vs 出」漏斗:每一层滤掉多少必须看得见。
        # review(5)抓到:原版四层过滤一个计数都没有 ——
        # 若 try_cast(closed_time) 因格式问题静默变 NULL,`NULL <= x` 在 WHERE 里等价 false,
        # 那批数据会**无声消失**且没有任何信号能让人发现。
        funnel = {
            "raw_trades": con.execute(f"SELECT count(*) FROM read_parquet('{lake}')").fetchone()[0],
            "after_join_and_firewall": n_after_join,
            "after_wash_exclusion": n_after_wash,
            "dropped_by_wash": n_after_join - n_after_wash,
        }

        B = "TIMESTAMP '" + BOUNDARY.strftime("%Y-%m-%d %H:%M:%S") + "'"
        con.execute(f"""ALTER TABLE bets ADD COLUMN win_win DOUBLE;""")
        con.execute(f"UPDATE bets SET win_win = {EDGE_SQL}*size")
        con.execute("CREATE OR REPLACE TABLE tagged AS SELECT *, "
                + WINDOW_SQL.format(B=B) + " AS win_tag FROM bets")
        # 臂 B 用的市场配平度(与钱包本事无关的轴)
        con.execute("""CREATE OR REPLACE TABLE mb AS
          SELECT cid, abs(sum(CASE WHEN outcome_index=0 THEN size*dir ELSE 0 END)
                     -sum(CASE WHEN outcome_index=1 THEN size*dir ELSE 0 END))
                 /nullif(abs(sum(CASE WHEN outcome_index=0 THEN size*dir ELSE 0 END)
                           +sum(CASE WHEN outcome_index=1 THEN size*dir ELSE 0 END)),0) AS leg_imb
          FROM tagged GROUP BY 1""")
        cut = con.execute("SELECT quantile_cont(leg_imb,0.333) FROM mb WHERE leg_imb IS NOT NULL").fetchone()[0]

        funnel["tagged_D"] = con.execute("SELECT count(*) FROM tagged WHERE win_tag='D'").fetchone()[0]
        funnel["tagged_V"] = con.execute("SELECT count(*) FROM tagged WHERE win_tag='V'").fetchone()[0]
        funnel["tagged_none"] = con.execute("SELECT count(*) FROM tagged WHERE win_tag IS NULL").fetchone()[0]
        assert (funnel["tagged_D"] + funnel["tagged_V"] + funnel["tagged_none"]
                == n_after_wash), "分窗对账不平:D+V+未参与 != 总数"
        # ⭐当次实测的赢家腿股数占比(应为 50%);UNRELIABLE 文案引用的就是这个,不许硬编码历史值
        leg_win_share = con.execute(
            "SELECT sum(CASE WHEN win=1 AND dir=1 THEN size ELSE 0 END)"
            "/nullif(sum(CASE WHEN dir=1 THEN size ELSE 0 END),0) FROM tagged").fetchone()[0]
        results: dict[str, Any] = {"boundary": BOUNDARY.isoformat(), "leg_imb_cut": cut,
                               "wash_excluded": len(wash), "funnel": funnel,
                               "leg_win_share": leg_win_share, "arms": {}}
        arms_out = {}
        for arm in ("A", "B"):
            where = "TRUE" if arm == "A" else f"m.leg_imb <= {cut}"
            con.execute(f"""CREATE OR REPLACE TABLE a AS
              SELECT t.* FROM tagged t JOIN mb m USING (cid) WHERE {where} AND t.win_tag IS NOT NULL""")
            con.execute(f"""CREATE OR REPLACE TABLE wsum AS
              SELECT w, win_tag, count(*) n, sum(win_win) num, sum(dollars) den
              FROM a GROUP BY 1,2""")
            elig = con.execute(f"""SELECT d.w FROM
                (SELECT w,num,den,n FROM wsum WHERE win_tag='D' AND n>={MIN_BETS_PER_WINDOW}) d
                JOIN (SELECT w,n FROM wsum WHERE win_tag='V' AND n>={MIN_BETS_PER_WINDOW}) v USING (w)
                """).fetchall()
            pool = [r[0] for r in elig]
            d_edges = dict(con.execute(f"""SELECT w, 100.0*num/nullif(den,0) FROM wsum
                WHERE win_tag='D' AND n>={MIN_BETS_PER_WINDOW}""").fetchall())
            v_by_wallet = {r[0]: (r[1], r[2]) for r in con.execute(
                f"SELECT w,num,den FROM wsum WHERE win_tag='V' AND n>={MIN_BETS_PER_WINDOW}").fetchall()}
            top = rank_wallets({w: d_edges[w] for w in pool if w in d_edges})
            r_v = _weighted_edge([v_by_wallet[w] for w in top if w in v_by_wallet])
            con.execute("CREATE OR REPLACE TABLE topw(w VARCHAR)")
            con.executemany("INSERT INTO topw VALUES (?)", [(w,) for w in top])
            per_market = {r[0]: (r[1], r[2]) for r in con.execute("""
                SELECT cid, sum(win_win), sum(dollars) FROM a
                WHERE win_tag='V' AND w IN (SELECT w FROM topw) GROUP BY 1""").fetchall()}
            q025, q975 = bootstrap_by_market(per_market)
            null = permutation_null(pool, len(top), v_by_wallet)
            arms_out[arm] = arm_verdict(r_v, pctl(null, 0.95), q025)
            arms_out[arm].update({"n_pool": len(pool), "n_top": len(top),
                                  "n_markets_v": len(per_market), "boot_q975_pp": q975,
                                  "null_p50_pp": pctl(null, 0.50), "config": arm_config(arm)})
            results["arms"][arm] = arms_out[arm]
            print(f"  臂{arm}: 合格钱包 {len(pool):,} | 前10% {len(top):,} | "
                  f"R_V {r_v:+.4f}pp | 零分布p95 {pctl(null,0.95):+.4f}pp | 自举2.5% {q025:+.4f}pp")

        # ⭐功效闸用**两臂中更宽的那个**零分布(更保守)
        worst = max(arms_out["A"]["null_p95_pp"], arms_out["B"]["null_p95_pp"])
        power = power_gate(worst)
        results["power_gate"] = power
        results.update(decide(arms_out["A"], arms_out["B"], power))
        (out_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return results
    finally:
        con.close()      # ⚠️ review(3):原版只在正常路径 close,中途抛异常就泄漏 9GB 上限的连接


if __name__ == "__main__":
    r = run()
    print("\n=== 功效闸 ===")
    print(f"  零分布p95 {r['power_gate']['null_p95_pp']:+.4f}pp vs 门槛 {MDE_MAX_PP:.1f}pp "
          f"⇒ {'通过' if r['power_gate']['passes'] else '未过'}")
    if r["power_gate"]["reason"]:
        print(f"  {r['power_gate']['reason']}")
    print(f"\n=== 判决: {r['verdict']} ===\n{r['reading']}")
    for a in ("A", "B"):
        print(f"\n  臂{a}: {r['arms'][a]['detail']}")
