"""方向三 V4「同市场同腿配对比较」的验收判据(先于实现写成)。

预登记单:docs/PREREG_WALLET_SKILL_V4_MATCHED_2026-08-23.md

⭐本文件重点钉五件事:
1. **留一法** —— 拿"这一格里别人的均价"比,不许把钱包自己算进去(§3.4 修订)。
2. **outcome 必须约掉** —— 同腿内比较,结算结果不许出现在得分里。
3. **无信息的"早买型"必须净得 0** —— 否则设计从根上就有偏。
4. **功效闸在红线之前**,不过时判「无判决」不许报 FAIL。
5. **口径实现只许一份** —— V3 栽在 `trade_edge`/`window_of` 是没人调用的替身。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "08-backtests"))

import wallet_skill_v4_matched as v4  # noqa: E402


def _cell(trades):
    """trades = [(wallet, shares, price), ...] 全是同一格(同市场同腿)的 BUY。"""
    return [{"w": w, "size": s, "price": p} for w, s, p in trades]


# ---------- 1. 留一法 ----------

def test_score_compares_against_others_not_including_self():
    """⭐§3.4 修订:必须用**除自己以外**的均价做对比。

    含自己会让占主导的钱包得分被机械压向 0(它在跟自己比),
    且压得多少与仓位大小系统性相关 —— 那正是本项目明令禁止的偏差。
    """
    # w1 占了 9 成成交:含自己时 O 会被它自己拉到 0.30,得分几乎为 0
    cell = _cell([("w1", 90, 0.30), ("w2", 10, 0.50)])
    got = v4.cell_scores(cell, min_other_trades=1)
    assert got["w1"] == pytest.approx(0.50 - 0.30), "没有排除自己 —— 主导钱包被自己拉平了"
    assert got["w2"] == pytest.approx(0.30 - 0.50)


def test_cell_with_no_other_trades_is_dropped_not_scored_zero():
    """格里没有别人 ⇒ **没有可比对象**,该剔掉;判成 0 分等于把"没数据"当成"没本事"。"""
    cell = _cell([("w1", 10, 0.4), ("w1", 5, 0.6)])
    assert v4.cell_scores(cell, min_other_trades=1) == {}


def test_min_other_trades_threshold_is_enforced():
    cell = _cell([("w1", 10, 0.4), ("w2", 1, 0.9), ("w3", 1, 0.9)])
    assert v4.cell_scores(cell, min_other_trades=5) == {}
    assert "w1" in v4.cell_scores(cell, min_other_trades=2)


# ---------- 2. outcome 必须约掉 ----------

def test_score_does_not_depend_on_which_leg_won():
    """⭐整个设计的支点:同一格内所有买家拿到同一个结算结果 ⇒ 它在配对里被约掉。

    得分函数**根本不接受** win/resolved_outcome 参数 —— 结构上就写不进去。
    """
    import inspect
    params = set(inspect.signature(v4.cell_scores).parameters)
    for banned in ("win", "resolved_outcome", "outcome", "payout"):
        assert banned not in params, f"配对得分不该看到结算结果,却收了 {banned}"


# ---------- 3. 无信息的"早买型"必须净得 0 ----------

def test_uninformed_early_buyer_nets_to_zero_across_win_and_lose():
    """⭐若"早买"本身就能得分,这个设计从根上有偏。

    构造:同一个钱包在两个市场都比别人早买(便宜 0.2),一个腿最终赢、一个最终输。
    因为得分只看价差、不看结果,两格都是 +0.2 —— 这**看起来**像有本事,
    但真实世界里"早"在输的那条腿上会体现为**买得贵**(晚买的人只花 0.05)。
    故这里用真实形状:赢的那条腿早买便宜(+),输的那条腿早买**贵**(−),净为 0。
    """
    win_leg = _cell([("early", 10, 0.30), ("late", 10, 0.90)])    # 价格上行,早买便宜
    lose_leg = _cell([("early", 10, 0.70), ("late", 10, 0.10)])   # 价格下行,早买贵
    s1 = v4.cell_scores(win_leg, min_other_trades=1)["early"]
    s2 = v4.cell_scores(lose_leg, min_other_trades=1)["early"]
    assert s1 + s2 == pytest.approx(0.0), "无信息的早买型没有净得 0 —— 设计有偏"


# ---------- 4. 聚合与单位 ----------

def test_wallet_score_is_dollar_weighted_across_cells_and_in_pp():
    """⚠️ 我起初在 V4 里另写了一个 `aggregate()` —— 与 V3 的 `_weighted_edge` 是同一件事的
    **第二份实现**,而 `run()` 用的是 V3 那份。孤儿守卫当场抓住,已删。
    这里直接用生产路径上的那一份。
    """
    per_cell = [(0.02 * 1000.0, 1000.0), (-0.01 * 100.0, 100.0)]   # (格得分×金额, 金额)
    assert v4._weighted_edge(per_cell) == pytest.approx(
        100 * (0.02 * 1000 - 0.01 * 100) / 1100)
    assert v4._weighted_edge([]) == 0.0


# ---------- 5. 功效闸(沿用 V3 的形状与门槛) ----------

def test_power_gate_blocks_and_says_no_verdict():
    g = v4.power_gate(null_p95_pp=4.16)
    assert g["passes"] is False and g["verdict_if_blocked"] == "NO_VERDICT"


def test_power_gate_threshold_is_the_economically_meaningful_one():
    assert v4.MDE_MAX_PP == 1.0


def test_underpowered_run_never_reports_a_falsification():
    r = v4.decide(arm_a={"green": False, "r_v_pp": 0.1, "failed": ["A"]},
                  arm_b={"green": False, "r_v_pp": 0.1, "failed": ["A"]},
                  power=v4.power_gate(null_p95_pp=4.16))
    assert r["verdict"] == "NO_VERDICT" and "证伪" not in r["reading"]


# ---------- 6. 双臂判读 + §11 边界必须写进结论 ----------

@pytest.mark.parametrize("a,b,want", [
    (True, True, "PASS"), (False, False, "PATH_CLOSED"),
    (True, False, "UNRELIABLE"), (False, True, "UNRELIABLE"),
])
def test_two_arm_read_rule_is_pre_written(a, b, want):
    r = v4.decide(arm_a={"green": a, "r_v_pp": 1.0, "failed": []},
                  arm_b={"green": b, "r_v_pp": 1.0, "failed": []},
                  power=v4.power_gate(null_p95_pp=0.3))
    assert r["verdict"] == want


def test_red_result_explicitly_says_it_is_not_a_falsification_of_direction_three():
    """⭐§11 写死:本单测的是「择时/择价」这一条路径,红了**不等于**方向三被证伪。

    一个"只在有把握的市场下注、但按公允价成交"的钱包在本设计里得分为 0。
    这句话必须出现在结论里,否则将来一定会被误读。
    """
    r = v4.decide(arm_a={"green": False, "r_v_pp": 0.1, "failed": ["A"]},
                  arm_b={"green": False, "r_v_pp": 0.0, "failed": ["A"]},
                  power=v4.power_gate(null_p95_pp=0.3))
    assert r["verdict"] == "PATH_CLOSED"
    assert "不等于" in r["reading"] and "证伪" in r["reading"]


def test_split_arms_reading_flags_the_immunity_claim_as_falsified():
    """§9:一臂绿一臂红 ⇒ 同时**证伪了"本设计对单边采集偏移免疫"这个断言**。"""
    r = v4.decide(arm_a={"green": True, "r_v_pp": 2.0, "failed": []},
                  arm_b={"green": False, "r_v_pp": 0.1, "failed": ["A"]},
                  power=v4.power_gate(null_p95_pp=0.3))
    assert r["verdict"] == "UNRELIABLE"
    assert "免疫" in r["reading"]


# ---------- 7. 分窗沿用 V3 的可交易口径 ----------

def test_v_window_requires_trade_after_boundary():
    T = v4.BOUNDARY
    assert v4.window_of(ts=T - v4.ONE_HOUR, closed=T + v4.ONE_HOUR) is None
    assert v4.window_of(ts=T + v4.ONE_HOUR, closed=T + 2 * v4.ONE_HOUR) == "V"
    assert v4.window_of(ts=T - 5 * v4.ONE_HOUR, closed=T - v4.ONE_HOUR) == "D"
    assert v4.window_of(ts=T + 2 * v4.ONE_HOUR, closed=T + v4.ONE_HOUR) is None


# ---------- 8. ⭐口径只许一份:SQL 与 Python 等价(V3 的 HIGH1 教训) ----------

def test_sql_and_python_agree_on_cell_scores():
    """⭐V3 栽在这里:Python 版是替身,生产走 SQL,两份会悄悄分叉。

    本判据跑**生产用的那份 SQL**,与 `cell_scores()` 逐格比对。
    """
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE t(cid VARCHAR, leg INTEGER, w VARCHAR, size DOUBLE, price DOUBLE)")
        rows = [("m1", 0, "w1", 90.0, 0.30), ("m1", 0, "w2", 10.0, 0.50),
                ("m1", 1, "w1", 5.0, 0.70), ("m1", 1, "w3", 5.0, 0.60),
                ("m2", 0, "w2", 20.0, 0.10), ("m2", 0, "w3", 20.0, 0.20)]
        con.executemany("INSERT INTO t VALUES (?,?,?,?,?)", rows)
        got = {(c, l, w): s for c, l, w, s in
               con.execute(v4.CELL_SCORE_SQL.format(src="t", min_other=1)).fetchall()}
    finally:
        con.close()
    import collections
    cells = collections.defaultdict(list)
    for cid, leg, w, sz, px in rows:
        cells[(cid, leg)].append({"w": w, "size": sz, "price": px})
    for (cid, leg), trades in cells.items():
        for w, s in v4.cell_scores(trades, min_other_trades=1).items():
            assert got[(cid, leg, w)] == pytest.approx(s), f"SQL 与 Python 在 {cid}/{leg}/{w} 不一致"
    assert len(got) == sum(len(v4.cell_scores(t, min_other_trades=1)) for t in cells.values())


def test_the_equivalence_test_would_catch_a_broken_leave_one_out():
    """⭐先在坏数据上验:把 SQL 的留一法改回"含自己",这条判据必须红。"""
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE t(cid VARCHAR, leg INTEGER, w VARCHAR, size DOUBLE, price DOUBLE)")
        con.executemany("INSERT INTO t VALUES (?,?,?,?,?)",
                        [("m1", 0, "w1", 90.0, 0.30), ("m1", 0, "w2", 10.0, 0.50)])
        good = con.execute(v4.CELL_SCORE_SQL.format(src="t", min_other=1)).fetchall()
        bad_sql = v4.CELL_SCORE_SQL.replace("- w_sz", "- 0*w_sz").replace("- w_val", "- 0*w_val")
        bad = con.execute(bad_sql.format(src="t", min_other=1)).fetchall()
    finally:
        con.close()
    assert sorted(good) != sorted(bad), "把留一法改回含自己之后结果没变 —— 等价性判据抓不住"
