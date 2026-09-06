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
"""方向三 V4 —— 同市场同腿**配对比较**(2026-08-23)。

预登记单:`docs/PREREG_WALLET_SKILL_V4_MATCHED_2026-08-23.md`(先于本文件写成)
判据:     `10-tests/unit/test_wallet_skill_v4.py`(先于本文件写成)

## 为什么换设计(V3 实测,不是推断)

V3 把观测单位从「钱包组合聚合」换成「逐笔」,样本从千级涨到千万级,
置换零分布 p95 **几乎纹丝不动**(4.1646 → 4.0427pp)。
⇒ 方差**不来自样本量**,来自**组合构成**:一组钱包押在哪些市场、大仓位落在哪几笔。
⇒ 只有换**比较单位**才可能降方差。

## 支点:同一格内 outcome 会被约掉

同一个市场的**同一条腿**上,所有买家拿到的结算结果是**同一个数**,故:

    edge_X − edge_others = (win − p_X) − (win − p_others) = p_others − p_X

`win` 完全约掉 ⇒ 只剩价格差,而价格在 [0,1] 内、同腿内部离散度远小于二元结果。
**这就是降方差的来源,不是靠加样本。**
⇒ 得分函数**结构上不接受** win/resolved_outcome —— 想写进去都写不进去(判据焊死)。

## ⭐留一法(§3.4,跑数之前的修订)

对比用的是**除该钱包之外**的均价。含自己会让占主导的钱包被自己拉平,
且压得多少与仓位大小系统性相关 —— 那是本项目明令禁止的偏差。

## 边界(§11):红了**不等于**方向三被证伪

本单只测「择时/择价」这一条路径。一个"只在有把握的市场下注、但按公允价成交"的钱包
在这里得分为 0。故 🔴 只能关掉这条路径,不能推出"没有 edge"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ⭐复用 V3 已经过 review + 变异验证的那一份,不再抄第二遍
# (「照抄结构而不抽象」是本项目犯过 4 次的形状)
from wallet_skill_v3 import (  # noqa: E402
    ABS_FLOOR_PP, BOOT_N, BOUNDARY, MDE_MAX_PP, MIN_BETS_PER_WINDOW, ONE_HOUR,
    PERM_N, RNG_SEED, TOP_FRACTION, _weighted_edge, arm_verdict,
    bootstrap_by_market, pctl, permutation_null, power_gate, rank_wallets, window_of,
)

OUT_DIR = PROJECT_ROOT / "08-backtests" / "results" / "wallet_skill_v4_2026-08-23"
MIN_OTHER_TRADES = 5
"""单元格里**除该钱包之外**的最少成交数。
没有别人 ⇒ 没有可比对象(不是"没本事")⇒ 该格剔掉并出声计数。"""

# ⭐口径只许一份:生产走这条 SQL,判据也跑这条 SQL(V3 栽在 Python 版是没人调用的替身)。
CELL_SCORE_SQL = """
WITH per_wallet AS (
  SELECT cid, leg, w, sum(size) AS w_sz, sum(size*price) AS w_val, count(*) AS w_n
  FROM {src} GROUP BY 1,2,3),
per_cell AS (
  SELECT cid, leg, sum(w_sz) AS c_sz, sum(w_val) AS c_val, sum(w_n) AS c_n
  FROM per_wallet GROUP BY 1,2)
SELECT p.cid, p.leg, p.w,
       (c.c_val - w_val) / nullif(c.c_sz - w_sz, 0) - w_val / nullif(w_sz, 0) AS s
FROM per_wallet p JOIN per_cell c USING (cid, leg)
WHERE c.c_n - p.w_n >= {min_other}
  AND c.c_sz - p.w_sz > 0 AND p.w_sz > 0
"""


def cell_scores(trades: list[dict[str, Any]],
                min_other_trades: int = MIN_OTHER_TRADES) -> dict[str, float]:
    """一个「市场×腿」单元格内,每个钱包的**留一法**配对得分。

    ⚠️ 参数里**没有** win / resolved_outcome —— 这是设计的支点(见模块 docstring),
    结算结果在同格配对里被约掉,想写进去都写不进去。

    ⚠️ 本函数是**规格实现**;生产路径走 `CELL_SCORE_SQL`。
    两者的一致性由 `test_sql_and_python_agree_on_cell_scores` 机械保证
    (V3 的教训:没有这条判据,两份实现会悄悄分叉而全部判据保持绿色)。
    """
    agg: dict[str, list[float]] = {}
    for t in trades:
        a = agg.setdefault(t["w"], [0.0, 0.0, 0.0])   # [股数, 金额, 笔数]
        a[0] += t["size"]
        a[1] += t["size"] * t["price"]
        a[2] += 1
    tot_sz = sum(a[0] for a in agg.values())
    tot_val = sum(a[1] for a in agg.values())
    tot_n = sum(a[2] for a in agg.values())
    out: dict[str, float] = {}
    for w, (sz, val, n) in agg.items():
        o_sz, o_val, o_n = tot_sz - sz, tot_val - val, tot_n - n
        if o_n < min_other_trades or o_sz <= 0 or sz <= 0:
            continue
        out[w] = o_val / o_sz - val / sz
    return out


def decide(arm_a: dict[str, Any], arm_b: dict[str, Any], power: dict[str, Any]) -> dict[str, Any]:
    """🔴 双臂判读规则 —— 预登记单 §9,先写死。"""
    if not power["passes"]:
        return {"verdict": "NO_VERDICT", "reading":
                f"⚪ 无判决 —— {power['reason']}。产出是关于**设计分辨力**的报告。"}
    a, b = arm_a["green"], arm_b["green"]
    if a and b:
        return {"verdict": "PASS", "reading":
                "🟢 两臂同时通过。⚠️ 仍**不等于 edge**:本单只测「在同一标的上比别人买得便宜」,"
                "不含「挑对市场」那一层(§4),且第二关(成本/容量/成交概率)未跑。"}
    if not a and not b:
        return {"verdict": "PATH_CLOSED", "reading":
                "🔴 两臂都不过 ⇒ **「择时/择价」这条路径不成立**。\n"
                "⛔ 这**不等于**方向三被证伪(§11):一个只在有把握的市场下注、"
                "但按公允价成交的钱包,在本设计里得分为 0。本单答不了那种情形。"}
    good = "A(全部市场)" if a else "B(两腿配平市场)"
    return {"verdict": "UNRELIABLE", "reading":
            f"🟡 不可采信 —— 只有臂 {good} 通过。两臂唯一差异是市场范围,"
            f"故这同时**证伪了 §9 里「本设计对单边采集偏移免疫」这个断言** ⇒ 须回头改设计。"
            f"⛔ 不许只汇报好看的那一臂。"}


def arm_config(name: str) -> dict[str, Any]:
    """两臂完整口径。差异必须有且只有 market_range 一项。"""
    return {"min_bets_per_window": MIN_BETS_PER_WINDOW, "top_fraction": TOP_FRACTION,
            "boundary": BOUNDARY.isoformat(), "min_other_trades": MIN_OTHER_TRADES,
            "weighting": "dollar", "time_firewall": "closed_time", "side": "BUY_only",
            "exclude_wash": True, "perm_n": PERM_N, "boot_n": BOOT_N, "seed": RNG_SEED,
            "market_range": "all" if name == "A" else "leg_balanced"}


def _wallet_scores_sql(src: str, min_other: int) -> str:
    """把格得分按金额加权聚到钱包。⭐格得分来自 `CELL_SCORE_SQL`,不另写一份。"""
    return f"""
    WITH s AS ({CELL_SCORE_SQL.format(src=src, min_other=min_other)}),
         d AS (SELECT cid, leg, w, sum(size*price) AS dollars, count(*) AS n
               FROM {src} GROUP BY 1,2,3)
    SELECT s.w, sum(s.s * d.dollars) AS num, sum(d.dollars) AS den,
           count(*) AS n_cells, sum(d.n) AS n_trades
    FROM s JOIN d USING (cid, leg, w) GROUP BY 1
    """


def run(out_dir: Path | str = OUT_DIR) -> dict[str, Any]:
    """跑完整双臂配对检验。⭐功效闸先跑先判。"""
    import duckdb
    sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "analysis"))
    import wash_trading_detector as wd
    import wallet_skill_v3 as v3

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
        B = "TIMESTAMP '" + BOUNDARY.strftime("%Y-%m-%d %H:%M:%S") + "'"
        # ⭐分窗表达式复用 V3 的 WINDOW_SQL,不另写一份
        con.execute(f"""CREATE TABLE base AS
            SELECT t.proxy_wallet AS w, t.condition_id AS cid, t.outcome_index AS leg,
                   t.size, t.price, t.size*t.price AS dollars,
                   to_timestamp(t.timestamp) AS ts,
                   try_cast(m.closed_time AS TIMESTAMP) AS t_close
            FROM read_parquet(?) t JOIN reg r USING (condition_id)
            JOIN read_parquet(?) m USING (condition_id)
            WHERE r.closed AND r.resolved_outcome IS NOT NULL AND m.closed_time IS NOT NULL
              AND t.side = 'BUY'""", [lake, mt])
        n_join = con.execute("SELECT count(*) FROM base").fetchone()[0]
        if wash:
            con.execute("DELETE FROM base WHERE cid = ANY(?)", [wash])
        n_wash = con.execute("SELECT count(*) FROM base").fetchone()[0]
        con.execute("CREATE TABLE tagged AS SELECT *, "
                    + v3.WINDOW_SQL.format(B=B) + " AS win_tag FROM base")
        funnel = {"buy_after_join": n_join, "after_wash": n_wash,
                  "dropped_by_wash": n_join - n_wash}
        for tag in ("D", "V"):
            funnel[f"tagged_{tag}"] = con.execute(
                f"SELECT count(*) FROM tagged WHERE win_tag='{tag}'").fetchone()[0]
        funnel["tagged_none"] = con.execute(
            "SELECT count(*) FROM tagged WHERE win_tag IS NULL").fetchone()[0]
        assert funnel["tagged_D"] + funnel["tagged_V"] + funnel["tagged_none"] == n_wash, \
            "分窗对账不平"

        con.execute("""CREATE TABLE mb AS SELECT cid,
            abs(sum(CASE WHEN leg=0 THEN size ELSE 0 END)-sum(CASE WHEN leg=1 THEN size ELSE 0 END))
            /nullif(sum(size),0) AS leg_imb FROM tagged GROUP BY 1""")
        cut = con.execute(
            "SELECT quantile_cont(leg_imb,0.333) FROM mb WHERE leg_imb IS NOT NULL").fetchone()[0]

        results: dict[str, Any] = {"boundary": BOUNDARY.isoformat(), "leg_imb_cut": cut,
                                   "funnel": funnel, "min_other_trades": MIN_OTHER_TRADES,
                                   "arms": {}}
        arms_out = {}
        for arm in ("A", "B"):
            where = "TRUE" if arm == "A" else f"m.leg_imb <= {cut}"
            con.execute(f"""CREATE OR REPLACE TABLE a AS SELECT t.* FROM tagged t
                JOIN mb m USING (cid) WHERE {where} AND t.win_tag IS NOT NULL""")
            scores = {}
            for tag in ("D", "V"):
                con.execute(f"CREATE OR REPLACE TABLE src_{tag} AS "
                            f"SELECT * FROM a WHERE win_tag='{tag}'")
                scores[tag] = {r[0]: (r[1], r[2], r[4]) for r in con.execute(
                    _wallet_scores_sql(f"src_{tag}", MIN_OTHER_TRADES)).fetchall()}
            pool = sorted(w for w in scores["D"] if w in scores["V"]
                          and scores["D"][w][2] >= MIN_BETS_PER_WINDOW
                          and scores["V"][w][2] >= MIN_BETS_PER_WINDOW)
            d_edges = {w: 100.0 * scores["D"][w][0] / scores["D"][w][1]
                       for w in pool if scores["D"][w][1]}
            v_pairs = {w: (scores["V"][w][0], scores["V"][w][1]) for w in pool}
            top = rank_wallets(d_edges)
            r_v = _weighted_edge([v_pairs[w] for w in top if w in v_pairs])
            con.execute("CREATE OR REPLACE TABLE topw(w VARCHAR)")
            con.executemany("INSERT INTO topw VALUES (?)", [(w,) for w in top])
            per_market = {r[0]: (r[1], r[2]) for r in con.execute(f"""
                WITH s AS ({CELL_SCORE_SQL.format(src='src_V', min_other=MIN_OTHER_TRADES)}),
                     d AS (SELECT cid, leg, w, sum(size*price) AS dollars FROM src_V GROUP BY 1,2,3)
                SELECT s.cid, sum(s.s*d.dollars), sum(d.dollars)
                FROM s JOIN d USING (cid, leg, w)
                WHERE s.w IN (SELECT w FROM topw) GROUP BY 1""").fetchall()}
            q025, q975 = bootstrap_by_market(per_market)
            null = permutation_null(pool, len(top), v_pairs)
            arms_out[arm] = arm_verdict(r_v, pctl(null, 0.95), q025)
            arms_out[arm].update({"n_pool": len(pool), "n_top": len(top),
                                  "n_markets_v": len(per_market), "boot_q975_pp": q975,
                                  "null_p50_pp": pctl(null, 0.50), "config": arm_config(arm)})
            results["arms"][arm] = arms_out[arm]
            print(f"  臂{arm}: 合格钱包 {len(pool):,} | 前10% {len(top):,} | R_V {r_v:+.4f}pp"
                  f" | 零分布p95 {pctl(null,0.95):+.4f}pp | 自举2.5% {q025:+.4f}pp", flush=True)

        worst = max(arms_out["A"]["null_p95_pp"], arms_out["B"]["null_p95_pp"])
        power = power_gate(worst)
        results["power_gate"] = power
        results.update(decide(arms_out["A"], arms_out["B"], power))
        (out_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return results
    finally:
        con.close()


def main() -> int:
    r = run()
    print("\n=== 功效闸 ===")
    print(f"  零分布p95 {r['power_gate']['null_p95_pp']:+.4f}pp vs 门槛 {MDE_MAX_PP:.1f}pp "
          f"⇒ {'通过' if r['power_gate']['passes'] else '未过'}")
    print(f"\n=== 判决: {r['verdict']} ===\n{r['reading']}")
    for a in ("A", "B"):
        print(f"\n  臂{a}: {r['arms'][a]['detail']}")
    print(f"\n  漏斗: {json.dumps(r['funnel'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
