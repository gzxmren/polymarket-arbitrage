"""跟单可交易性检验的验收判据(先于实现写成)。

预登记单:docs/PREREG_FOLLOW_THE_LEADER_2026-08-26.md

⭐本文件重点钉五件事(每条都对应预登记单里一条会被人事后含糊掉的红线):
1. **跟单价必须是信号之后的价** —— 用领先者自己的成交价算,那 5pp 是他的不是你的。
2. **找不到后续成交 = 未成交**,不许当成 0 收益混进平均(静默丢样本是本项目死因)。
3. **一个标的只跟一次** —— 真实跟单者不会因为 5 个人先后买入就买 5 次。
4. **功效闸在红线之前**,不过时判「无判决」不许报 FAIL。
5. **必须有置换零分布** —— 实测买方整体就比付价多赢 0.5~1.6pp,不设零分布必得假绿灯。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "08-backtests"))

import follow_the_leader as fl  # noqa: E402

T0 = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
CLOSE = T0 + timedelta(hours=6)


def _tr(mins, price, size=100.0):
    return {"ts": T0 + timedelta(minutes=mins), "price": price, "size": size}


# ---------- 1. ⭐跟单价必须是信号**之后**的价 ----------

def test_fill_price_comes_from_after_the_signal_not_the_leader():
    """⭐整个检验的支点:那 5pp 是领先者的,不是你的。

    领先者 0 分钟在 0.30 买入;之后有 0.50 / 0.55 两笔。
    Δ=5 分钟 ⇒ 你只能拿到 6 分钟那笔 0.50,**绝不是 0.30**。
    """
    later = [_tr(6, 0.50), _tr(20, 0.55)]
    got = fl.find_fill(signal_ts=T0, later_trades=later, delay_min=5, closed=CLOSE)
    assert got is not None
    assert got["price"] == pytest.approx(0.50), "拿到了领先者自己的价 —— 整个检验就作废了"


def test_trades_inside_the_delay_window_are_not_usable():
    """延迟窗口内的成交你看不到,不许拿来当跟单价。"""
    later = [_tr(2, 0.31), _tr(9, 0.60)]
    got = fl.find_fill(signal_ts=T0, later_trades=later, delay_min=5, closed=CLOSE)
    assert got["price"] == pytest.approx(0.60)


def test_delay_boundary_is_inclusive():
    """恰好 Δ 分钟的那笔算可成交(边界写死,免得实现两处理解不同)。"""
    assert fl.find_fill(T0, [_tr(5, 0.42)], delay_min=5, closed=CLOSE)["price"] == pytest.approx(0.42)


# ---------- 2. ⭐未成交必须出声,不许当 0 收益 ----------

def test_no_later_trade_means_not_filled_not_zero_return():
    """找不到后续成交 = 这笔跟单**没做成**,不是"做了但没赚"。

    混进去当 0 会把平均往 0 拉,而且丢的样本还看不见 —— 本项目的死因。
    """
    assert fl.find_fill(T0, [_tr(1, 0.9)], delay_min=5, closed=CLOSE) is None
    assert fl.find_fill(T0, [], delay_min=5, closed=CLOSE) is None


def test_fill_after_settlement_is_rejected_by_the_firewall():
    """时间防火墙:结算之后的成交不能当跟单价(那时答案已揭晓)。"""
    late = [{"ts": CLOSE + timedelta(minutes=1), "price": 0.99, "size": 10.0}]
    assert fl.find_fill(T0, late, delay_min=5, closed=CLOSE) is None


def test_funnel_counts_reconcile():
    """漏斗必须相加等于总数:信号 = 成交 + 未成交。"""
    r = fl.scan_signals([
        {"cid": "m1", "leg": 0, "t_sig": T0, "closed": CLOSE, "win": 1.0,
         "later": [_tr(6, 0.50)]},
        {"cid": "m2", "leg": 1, "t_sig": T0, "closed": CLOSE, "win": 0.0,
         "later": [_tr(1, 0.9)]},          # 只有延迟窗内的成交 ⇒ 未成交
    ], delay_min=5)
    f = r["funnel"]
    assert f["signals"] == 2
    assert f["filled"] + f["unfilled"] == f["signals"]
    assert f["filled"] == 1 and f["unfilled"] == 1


# ---------- 3. ⭐一个标的只跟一次 ----------

def test_one_follow_per_asset_takes_the_earliest_signal():
    rows = [{"cid": "m1", "leg": 0, "w": "wA", "ts": T0 + timedelta(minutes=30)},
            {"cid": "m1", "leg": 0, "w": "wB", "ts": T0},
            {"cid": "m1", "leg": 1, "w": "wA", "ts": T0 + timedelta(minutes=5)}]
    got = fl.dedupe_signals(rows)
    assert len(got) == 2, "同一个(市场,腿)出了两个信号 —— 会重复下注"
    assert got[("m1", 0)] == T0, "没取最早的那个信号"


# ---------- 4. 收益口径 ----------

def test_follow_return_uses_fill_price_and_is_dollar_weighted():
    """收益 = win − 跟单价;权重 = 你实际投进去的钱(跟单价 × 数量)。"""
    r = fl.scan_signals([
        {"cid": "m1", "leg": 0, "t_sig": T0, "closed": CLOSE, "win": 1.0,
         "later": [_tr(6, 0.40, size=1000.0)]},
        {"cid": "m2", "leg": 0, "t_sig": T0, "closed": CLOSE, "win": 0.0,
         "later": [_tr(6, 0.10, size=100.0)]},
    ], delay_min=5)
    num = (1.0 - 0.40) * 1000.0 + (0.0 - 0.10) * 100.0
    den = 0.40 * 1000.0 + 0.10 * 100.0
    assert r["r_follow_pp"] == pytest.approx(100.0 * num / den)


def test_empty_input_does_not_crash_or_fake_a_number():
    r = fl.scan_signals([], delay_min=5)
    assert r["funnel"]["signals"] == 0
    assert r["r_follow_pp"] == 0.0


# ---------- 5. (已删)原先这里测的是 v3 的 power_gate / arm_verdict ----------
# ⚠️ 2026-08-26 review 抓到:V5b 的闸门与红线是本文件自己的
#    `power_gate_return` / `arm_verdict_return`,而这一段测的是**已被替换掉的旧函数**,
#    会让人以为"功效闸有 7 条判据罩着"。真正保护 V5b 的在第 8 节。
#    连同 follow_the_leader.py 里对那几个旧函数的 import 一并删除。


# ---------- 6. 双臂判读规则先写死 ----------

@pytest.mark.parametrize("a,b,want", [
    (True, True, "PASS"), (False, False, "NOT_FOLLOWABLE"),
    (True, False, "UNRELIABLE"), (False, True, "UNRELIABLE"),
])
def test_two_arm_read_rule_is_pre_written(a, b, want):
    r = fl.decide(arm_a={"green": a, "r_v_pct": 1.0, "failed": []},
                  arm_b={"green": b, "r_v_pct": 1.0, "failed": []},
                  power=fl.power_gate_return(null_p95_pct=0.3))
    assert r["verdict"] == want


def test_red_result_says_the_5pp_belongs_to_the_leader():
    """🔴 的含义必须写清楚:不是"他们没本事",是"那 5pp 你拿不到"。"""
    r = fl.decide(arm_a={"green": False, "r_v_pct": 0.1, "failed": ["A"]},
                  arm_b={"green": False, "r_v_pct": 0.0, "failed": ["A"]},
                  power=fl.power_gate_return(null_p95_pct=0.3))
    assert r["verdict"] == "NOT_FOLLOWABLE"
    assert "领先者" in r["reading"]


def test_green_result_flags_the_optimistic_capacity_assumption():
    """⭐§11 边界:本单默认你能以那个价成交任意规模,这是**乐观假设**。
    绿灯必须自己带上这句话,否则一定会被当成"可以上了"。
    """
    r = fl.decide(arm_a={"green": True, "r_v_pct": 2.0, "failed": []},
                  arm_b={"green": True, "r_v_pct": 1.8, "failed": []},
                  power=fl.power_gate_return(null_p95_pct=0.3))
    assert r["verdict"] == "PASS"
    assert "容量" in r["reading"] and "乐观" in r["reading"]


# ---------- 7. 两臂口径只差一个自变量 ----------

def test_two_arms_differ_only_in_market_range():
    a, b = fl.arm_config("A"), fl.arm_config("B")
    diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    assert diff == {"market_range"}, f"两臂差异不止一个自变量: {diff}"


def test_simultaneous_fills_use_volume_weighted_price_not_the_cheapest():
    """⭐同一时刻多笔成交时,不许挑最便宜的那笔当跟单价。

    挑最便宜 = 白送跟单者一个更好的价,口径不干净(2026-08-26 自查发现)。
    正确做法:那一刻全部成交的成交量加权均价。
    """
    same_moment = [_tr(6, 0.40, size=100.0), _tr(6, 0.60, size=300.0)]
    got = fl.find_fill(T0, same_moment, delay_min=5, closed=CLOSE)
    assert got is not None
    # 若实现挑最便宜,这里会是 0.40
    vwap = (0.40 * 100 + 0.60 * 300) / 400
    assert got["price"] == pytest.approx(vwap), "同刻多笔没有按成交量加权"
    assert got["size"] == pytest.approx(400.0), "同刻多笔的数量没有合并"


def test_sql_and_python_agree_on_fill_selection():
    """⭐口径只许一份:生产走 FILL_SQL,`find_fill()` 是规格。
    V3 栽在两份实现悄悄分叉而全部判据保持绿色 —— 这条把它们焊在一起。
    """
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("""CREATE TABLE src_V(cid VARCHAR, leg INTEGER, w VARCHAR,
                       ts TIMESTAMP, t_close TIMESTAMP, price DOUBLE, size DOUBLE, win DOUBLE)""")
        con.execute("CREATE TABLE leaders(w VARCHAR); INSERT INTO leaders VALUES ('L')")
        rows = [
            ("m1", 0, "L", T0, CLOSE, 0.30, 50.0, 1.0),                       # 信号
            ("m1", 0, "x", T0 + timedelta(minutes=2), CLOSE, 0.31, 10.0, 1.0),  # 延迟窗内,不可用
            ("m1", 0, "x", T0 + timedelta(minutes=6), CLOSE, 0.40, 100.0, 1.0),  # 同刻两笔
            ("m1", 0, "y", T0 + timedelta(minutes=6), CLOSE, 0.60, 300.0, 1.0),
            ("m1", 0, "z", T0 + timedelta(minutes=9), CLOSE, 0.70, 10.0, 1.0),
        ]
        con.executemany("INSERT INTO src_V VALUES (?,?,?,?,?,?,?,?)",
                        [(c, l, w, ts, tc, p, s, wn) for c, l, w, ts, tc, p, s, wn in rows])
        got = con.execute(fl.FILL_SQL.format(delay=5)).fetchall()
    finally:
        con.close()
    assert len(got) == 1
    _, _, px, sz, _ = got[0]
    py = fl.find_fill(T0, [{"ts": r[3], "price": r[5], "size": r[6]} for r in rows[1:]],
                      delay_min=5, closed=CLOSE)
    assert px == pytest.approx(py["price"]), "SQL 与 Python 的跟单价不一致"
    assert sz == pytest.approx(py["size"]), "SQL 与 Python 的成交量不一致"


def test_the_equivalence_test_would_catch_a_cheapest_pick():
    """⭐先在坏数据上验:把 SQL 改回"挑最便宜",这条判据必须红。"""
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("""CREATE TABLE src_V(cid VARCHAR, leg INTEGER, w VARCHAR,
                       ts TIMESTAMP, t_close TIMESTAMP, price DOUBLE, size DOUBLE, win DOUBLE)""")
        con.execute("CREATE TABLE leaders(w VARCHAR); INSERT INTO leaders VALUES ('L')")
        con.executemany("INSERT INTO src_V VALUES (?,?,?,?,?,?,?,?)", [
            ("m1", 0, "L", T0, CLOSE, 0.30, 50.0, 1.0),
            ("m1", 0, "x", T0 + timedelta(minutes=6), CLOSE, 0.40, 100.0, 1.0),
            ("m1", 0, "y", T0 + timedelta(minutes=6), CLOSE, 0.60, 300.0, 1.0)])
        good = con.execute(fl.FILL_SQL.format(delay=5)).fetchall()
        bad_sql = fl.FILL_SQL.replace(
            "CAST(sum(t.size*t.price)/nullif(sum(t.size),0) AS DOUBLE) AS price",
            "CAST(min(t.price) AS DOUBLE) AS price")
        bad = con.execute(bad_sql.format(delay=5)).fetchall()
    finally:
        con.close()
    assert good[0][2] != bad[0][2], "改回挑最便宜之后结果没变 —— 等价性判据抓不住"


# =====================================================================
# V5b:仓位模型改为「每个信号投固定金额」(2026-08-26 用户拍板)
# =====================================================================

def test_return_is_per_dollar_not_per_share():
    """⭐V5 的仓位模型是错的:按**别人成交的规模**加权 —— 没人被迫按别人的规模下注。

    V5b:每个信号投 1 块钱,买 1/p 股,收益率 = win/p − 1。
    """
    assert fl.signal_return(price=0.50, win=1.0) == pytest.approx(1.0)    # 翻倍
    assert fl.signal_return(price=0.50, win=0.0) == pytest.approx(-1.0)   # 归零
    assert fl.signal_return(price=0.01, win=1.0) == pytest.approx(99.0)   # 100 倍
    assert fl.signal_return(price=0.80, win=1.0) == pytest.approx(0.25)


def test_return_rejects_nonpositive_price():
    """价格为 0 或负 ⇒ 不是可执行的成交,必须拒绝而不是算出 inf。"""
    assert fl.signal_return(price=0.0, win=1.0) is None
    assert fl.signal_return(price=-0.1, win=1.0) is None


def test_headline_statistic_is_equal_weight_across_signals():
    """每个信号一票 —— 不许再被少数大仓位主导。"""
    rets = [1.0, -1.0, 0.25, -1.0]
    r = fl.summarize(rets, prices=[0.5, 0.5, 0.8, 0.9])
    assert r["mean_return_pct"] == pytest.approx(100 * sum(rets) / len(rets))


def test_three_numbers_are_all_reported_none_may_be_missing():
    """⭐预登记单强制:平均 / 中位 / 只看 p>=0.10 三个数缺一不可。

    结构检查:任何只报其中一个的汇报都不算数 —— 低价杂束会主导平均值,
    三个数不一致本身就是信息。
    """
    r = fl.summarize([1.0, -1.0, 99.0], prices=[0.5, 0.5, 0.01])
    for k in ("mean_return_pct", "median_return_pct", "mean_return_pct_p_ge_010", "n_signals"):
        assert k in r, f"缺了必报项 {k}"


def test_the_p_ge_010_subset_actually_excludes_cheap_bets():
    """只看 p>=0.10 的那一档,必须真的把便宜下注排除掉。"""
    rets = [99.0, 1.0, -1.0]          # 第一笔是 0.01 的百倍赢家
    prices = [0.01, 0.50, 0.50]
    r = fl.summarize(rets, prices=prices)
    assert r["mean_return_pct"] == pytest.approx(100 * 99.0 / 3)
    assert r["mean_return_pct_p_ge_010"] == pytest.approx(100 * 0.0 / 2), \
        "便宜下注没被排除 —— 那一档就失去了意义"


def test_median_is_not_dragged_by_one_lottery_winner():
    r = fl.summarize([99.0, -1.0, -1.0, -1.0], prices=[0.01, 0.5, 0.5, 0.5])
    assert r["median_return_pct"] == pytest.approx(-100.0)
    assert r["mean_return_pct"] > 0, "构造有误:这个样本的平均值应当被那个百倍赢家拉正"


def test_summarize_handles_empty_without_faking_a_number():
    r = fl.summarize([], prices=[])
    assert r["n_signals"] == 0
    assert r["mean_return_pct"] == 0.0 and r["median_return_pct"] == 0.0


# ---------- V5b 的红线(收益率口径,用户拍板) ----------

def test_power_gate_threshold_is_in_return_terms():
    """⭐红线换算依据:0.5 附近价格上 1 个概率点 ≈ 2% 收益率,与 V3/V4 的 1pp 同源。"""
    assert fl.MDE_MAX_RETURN_PCT == 2.0
    assert fl.power_gate_return(null_p95_pct=2.0001)["passes"] is False
    assert fl.power_gate_return(null_p95_pct=1.9999)["passes"] is True


def test_absolute_floor_is_in_return_terms():
    assert fl.ABS_FLOOR_RETURN_PCT == 1.0
    d = fl.arm_verdict_return(r_pct=0.99, null_p95_pct=0.1, boot_q025_pct=0.5)
    assert d["green"] is False
    assert any("绝对下限" in x for x in d["failed"])


def test_return_arm_needs_all_three_redlines():
    ok = dict(r_pct=5.0, null_p95_pct=2.0, boot_q025_pct=1.0)
    assert fl.arm_verdict_return(**ok)["green"] is True
    assert fl.arm_verdict_return(**{**ok, "null_p95_pct": 6.0})["green"] is False
    assert fl.arm_verdict_return(**{**ok, "r_pct": 0.5})["green"] is False
    assert fl.arm_verdict_return(**{**ok, "boot_q025_pct": -0.1})["green"] is False


def test_underpowered_return_run_never_reports_falsification():
    r = fl.decide(arm_a={"green": False, "r_v_pct": 0.1, "failed": ["A"]},
                  arm_b={"green": False, "r_v_pct": 0.1, "failed": ["A"]},
                  power=fl.power_gate_return(null_p95_pct=9.0))
    assert r["verdict"] == "NO_VERDICT" and "证伪" not in r["reading"]


def test_return_power_gate_blocked_verdict_is_no_verdict_not_falsified():
    """⭐闸不过时判决必须是「无判决」,不许是「证伪」。

    ⚠️ 本条针对的是**收益率口径**的 `power_gate_return`。
    变异实测(2026-08-26):原先只有针对旧 `power_gate` 的判据,
    把 `power_gate_return` 的 NO_VERDICT 改成 FALSIFIED,34 条判据一条不红。
    """
    g = fl.power_gate_return(null_p95_pct=9.0)
    assert g["passes"] is False
    assert g["verdict_if_blocked"] == "NO_VERDICT", "闸不过时报了证伪 —— 那是 08-19 犯的错"
    r = fl.decide(arm_a={"green": False, "r_v_pct": 0.1, "failed": ["A"]},
                  arm_b={"green": False, "r_v_pct": 0.1, "failed": ["A"]}, power=g)
    assert r["verdict"] == "NO_VERDICT"


# =====================================================================
# 9. 2026-08-26 review 判 Block 的两条 HIGH,逐条钉住
# =====================================================================

def _mk_srcv(con, rows):
    con.execute("""CREATE OR REPLACE TABLE src_V(cid VARCHAR, leg INTEGER, w VARCHAR,
                   ts TIMESTAMP, t_close TIMESTAMP, price DOUBLE, size DOUBLE, win DOUBLE)""")
    con.executemany("INSERT INTO src_V VALUES (?,?,?,?,?,?,?,?)", rows)


def test_HIGH1_signal_is_the_earliest_across_all_leaders():
    """⭐HIGH1:`FILL_SQL` 的信号必须取**全体领先者里最早**的那一笔。

    ⚠️ 原等价判据只放了**一个**领先者钱包,从没测过多领先者场景 ⇒
    把 `min(ts)` 改成 `max(ts)`(直接破坏"一个标的只跟一次、取最早信号"这条红线),
    35 条判据一条不红(2026-08-26 review 变异实测)。
    """
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE leaders(w VARCHAR)")
        con.executemany("INSERT INTO leaders VALUES (?)", [("L1",), ("L2",)])
        # ⚠️ 故意打乱插入顺序:晚的先插,早的后插
        _mk_srcv(con, [
            ("m1", 0, "L2", T0 + timedelta(minutes=30), CLOSE, 0.50, 10.0, 1.0),  # 晚的领先者
            ("m1", 0, "L1", T0, CLOSE, 0.30, 10.0, 1.0),                          # 早的领先者
            ("m1", 0, "x", T0 + timedelta(minutes=6), CLOSE, 0.44, 100.0, 1.0),   # 只有取最早才够得着
            ("m1", 0, "y", T0 + timedelta(minutes=40), CLOSE, 0.88, 100.0, 1.0),
        ])
        got = con.execute(fl.FILL_SQL.format(delay=5)).fetchall()
    finally:
        con.close()
    assert len(got) == 1
    assert got[0][2] == pytest.approx(0.44), (
        "跟单价不是从**最早**信号(0 分钟)往后 5 分钟取的 —— "
        "取成了晚信号(30 分钟)之后的价,等于放弃了「一个标的只跟一次、取最早」这条红线")


def test_HIGH1_guard_would_catch_taking_the_latest_signal():
    """⭐先在坏数据上验:把 sig CTE 的 min(ts) 改成 max(ts),上面那条必须红。"""
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE leaders(w VARCHAR)")
        con.executemany("INSERT INTO leaders VALUES (?)", [("L1",), ("L2",)])
        _mk_srcv(con, [
            ("m1", 0, "L2", T0 + timedelta(minutes=30), CLOSE, 0.50, 10.0, 1.0),
            ("m1", 0, "L1", T0, CLOSE, 0.30, 10.0, 1.0),
            ("m1", 0, "x", T0 + timedelta(minutes=6), CLOSE, 0.44, 100.0, 1.0),
            ("m1", 0, "y", T0 + timedelta(minutes=40), CLOSE, 0.88, 100.0, 1.0),
        ])
        good = con.execute(fl.FILL_SQL.format(delay=5)).fetchall()
        bad = con.execute(fl.FILL_SQL.replace("min(ts) AS t_sig", "max(ts) AS t_sig")
                          .format(delay=5)).fetchall()
    finally:
        con.close()
    assert good[0][2] != bad[0][2], "改成取最晚信号后结果没变 —— 这条判据抓不住"


def test_HIGH2_win_expression_is_imported_not_copied():
    """⭐HIGH2:win 判定不许在本文件再抄一份,必须复用 v3 的唯一那处。

    结构检查:源码里不许出现第二份 `outcome_index <> resolved_outcome` 字面量,
    且必须 import WIN_SQL —— 守的是「win 判定全项目只此一份」这条结构不变量。
    ⚠️ review 变异实测:抄第二份之后把 `<>` 改成 `=`(win/lose 完全反转),
    35 条判据一条不红,而输出只是**符号悄悄翻转**、看起来同样合理。
    """
    import ast
    import inspect
    # ⚠️ 本判据第一版只查「源码里有没有 WIN_SQL 这个词」—— 而我新加的那行引用里正好有,
    #    于是导入根本没加上、模块运行时 NameError,判据却是绿的(2026-08-26 当场踩到)。
    #    这与 review 刚抓到的"名字对了但没测到点上"是同一形状。改用语法树查**真实导入**。
    tree = ast.parse(inspect.getsource(fl))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "wallet_skill_v3":
            imported |= {a.name for a in n.names}
    assert "WIN_SQL" in imported, f"没有真的从 wallet_skill_v3 导入 WIN_SQL(实际导入:{sorted(imported)})"
    # 行为验证:导进来的确实能用
    assert isinstance(fl.WIN_SQL, str) and fl.WIN_SQL
    src = inspect.getsource(fl)
    assert "outcome_index <> resolved_outcome" not in src, "又抄了第二份 win 判定"
    assert "outcome_index = resolved_outcome" not in src
    # 已被替换掉的旧函数不许再导进来(否则判据会去测它们,制造"有人罩着"的假象)
    assert not ({"power_gate", "arm_verdict", "ABS_FLOOR_PP", "MDE_MAX_PP"} & imported), \
        f"导入了已被 V5b 替换掉的旧函数:{sorted({'power_gate','arm_verdict','ABS_FLOOR_PP','MDE_MAX_PP'} & imported)}"


def test_HIGH2_the_imported_win_sql_still_uses_the_inverted_rule():
    """⭐复用之后还要确认那一份本身没被改坏(不许只验"有 import")。"""
    import wallet_skill_v3 as v3
    assert "<>" in v3.WIN_SQL, "被复用的 WIN_SQL 里的 <> 没了 —— win/lose 会整体反转"


# ---------- review 的三条 MEDIUM ----------

def test_arm_config_reports_the_actual_delay_not_the_module_constant():
    """⭐记录必须由**实际使用的那个值**产生。

    原实现读模块常量 ⇒ 跑 Δ=15 的敏感性时,结果文件顶层写 15、臂内 config 写 5,
    同一份文件自相矛盾(review 抓到)。
    """
    assert fl.arm_config("A", delay_min=15)["delay_min"] == 15
    assert fl.arm_config("A")["delay_min"] == fl.DELAY_MIN


def test_arm_config_weighting_label_matches_actual_behaviour():
    """⭐`weighting` 曾写 "dollar" —— 那是 V5 遗留,V5b 实际是每信号等权。
    元数据说谎比没有元数据更坏:它会被将来引用。
    """
    assert fl.arm_config("A")["weighting"] == "equal_per_signal"


def test_result_keys_use_pct_not_pp_units():
    """⭐单位命名:V5b 这几个量是 %收益率,不是 V3/V4 的概率点(pp)。
    同名不同单位会让人拿它去跟 1pp 门槛比(review 抓到)。
    """
    d = fl.arm_verdict_return(r_pct=5.0, null_p95_pct=2.0, boot_q025_pct=1.0)
    for k in ("r_v_pct", "null_p95_pct", "boot_q025_pct"):
        assert k in d
    assert not any(k.endswith("_pp") for k in d), f"还有 pp 后缀的键: {sorted(d)}"
