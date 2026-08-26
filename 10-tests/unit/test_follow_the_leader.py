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


# =====================================================================
# 10. V6:主判决量改为「仅 p>=0.10 子集」(预登记单 PREREG_FOLLOW_V6_PFLOOR_2026-08-26)
# =====================================================================

def test_V6_headline_uses_only_the_p_floor_subset():
    """⭐V6 主判决量 = 只保留 p>=P_FLOOR 的信号,取平均收益率。

    砍掉低价是因为等额投入下买 0.01 赢了是 +9900%,少数彩票中奖主导平均值
    ⇒ V5b 实测零分布 p95 高达 +9.04%,功效闸直接失效。
    """
    rets = [99.0, 1.0, -1.0]          # 第一笔是 0.01 的百倍赢家
    prices = [0.01, 0.50, 0.50]
    r = fl.summarize_v6(rets, prices, p_floor=0.10)
    assert r["n_signals"] == 2, "低价信号没被排除"
    assert r["mean_return_pct"] == pytest.approx(0.0)
    assert r["n_dropped_below_floor"] == 1, "被砍掉多少必须出声计数"


def test_V6_p_floor_is_inclusive_at_the_boundary():
    """边界写死:恰好等于门槛的算保留(免得两处实现理解不同)。"""
    r = fl.summarize_v6([1.0], [0.10], p_floor=0.10)
    assert r["n_signals"] == 1


def test_V6_reports_all_three_mandatory_numbers():
    """⭐平均 / 中位 / 样本量,缺一不可。
    V5b 证明了只看平均会漏掉「中位是 −100%」这种要命的事实。
    """
    r = fl.summarize_v6([1.0, -1.0, -1.0], [0.5, 0.5, 0.5], p_floor=0.10)
    for k in ("mean_return_pct", "median_return_pct", "n_signals", "n_dropped_below_floor"):
        assert k in r, f"缺了必报项 {k}"
    assert r["median_return_pct"] == pytest.approx(-100.0)


def test_V6_p_floor_default_is_the_uncontaminated_one():
    """⭐`P_FLOOR = 0.10` 来自 V5b 的**配套报告项**,是在跑 V5b 任何数字**之前**定的
    (理由:10 倍以内杠杆)⇒ 门槛本身没被结果污染。

    结构检查:默认值必须仍是 0.10 —— 守的是「不许事后调门槛去凑一个好看的结果」
    这条红线(预登记单 §4 明令不许试 0.05/0.15/0.20 再挑)。
    """
    assert fl.P_FLOOR_FOR_SUBSET == 0.10


def test_V6_empty_after_floor_does_not_fake_a_number():
    r = fl.summarize_v6([99.0], [0.01], p_floor=0.10)
    assert r["n_signals"] == 0
    assert r["mean_return_pct"] == 0.0 and r["median_return_pct"] == 0.0
    assert r["n_dropped_below_floor"] == 1


# ---------- V6 判决版的开跑硬门槛 ----------

def test_V6_verdict_run_is_blocked_until_data_is_sufficient():
    """⭐预登记单 §6:数据攒够之前跑 = 小样本噪声,跑了也不算。

    三条硬门槛任一不满足就必须拒绝开跑,而不是跑出一个没意义的数。
    """
    ok = dict(n_markets=20_000, n_wallets=8_000, n_fills=10_000)
    assert fl.verdict_run_allowed(**ok)["allowed"] is True
    for k in ok:
        bad = dict(ok); bad[k] = ok[k] - 1
        g = fl.verdict_run_allowed(**bad)
        assert g["allowed"] is False, f"{k} 少 1 竟然还允许开跑"
        # ⚠️ 只断言 missing 非空是不够的:空字符串列表也非空(变异实测抓到)。
        #    必须断言它**真的说出了**是哪一条、差多少 —— 否则拒绝信息等于没有。
        assert any(x.strip() for x in g["missing"]), "拒绝时没说清是哪一条不满足"
        assert any(str(ok[k] - 1) in x.replace(",", "") for x in g["missing"]), \
            f"拒绝信息里没写出实际值,人看不出差多少:{g['missing']}"


def test_V6_gate_thresholds_match_the_prereg():
    assert fl.VERDICT_MIN_MARKETS == 20_000
    assert fl.VERDICT_MIN_WALLETS == 8_000
    assert fl.VERDICT_MIN_FILLS == 10_000


def test_V6_descriptive_run_is_labelled_and_cannot_be_quoted_as_verdict():
    """⭐描述版的结论栏必须自带「不构成判决」,否则日后一定被当判决引用。"""
    r = fl.label_result({"verdict": "PASS", "reading": "两臂通过"}, descriptive=True)
    assert r["verdict"] == "DESCRIPTIVE_ONLY", "描述版的 verdict 字段没被改掉"
    assert "不构成判决" in r["reading"]
    assert r["original_verdict"] == "PASS", "原判读要保留,便于日后对照"


def test_V6_verdict_run_label_is_untouched():
    r = fl.label_result({"verdict": "PASS", "reading": "两臂通过"}, descriptive=False)
    assert r["verdict"] == "PASS" and "不构成判决" not in r["reading"]


def test_V6_redlines_are_not_relaxed_for_the_subset():
    """⛔换了子集不许放松红线 —— 与 V5b 同源,不重新拍数。"""
    assert fl.MDE_MAX_RETURN_PCT == 2.0
    assert fl.ABS_FLOOR_RETURN_PCT == 1.0


def test_V6_machinery_is_actually_wired_into_run(tmp_path, monkeypatch):
    """⭐红线只算不用等于没有 —— 本项目犯过多次。

    孤儿守卫当场抓到过一次:`verdict_run_allowed` / `label_result` 写好了但 `run()` 没调用。

    结构检查:断言三个 V6 函数出现在 `run()` 的执行路径里,且 `run()` 收得下三个开关 ——
    守的是「红线必须接到生产路径上」这条结构不变量。
    ⚠️ 但**光有结构检查不够**:2026-08-26 实测,这条绿着而真跑 KeyError
    (打印仍用 V5b 的字段名)。行为侧由下面 `test_V6_summary_tail_handles_both_shapes` 补齐。
    """
    called = {"gate": 0}
    real = fl.verdict_run_allowed

    def spy(**kw):
        called["gate"] += 1
        return real(**kw)

    monkeypatch.setattr(fl, "verdict_run_allowed", spy)
    import inspect
    src = inspect.getsource(fl.run)
    assert "verdict_run_allowed(" in src, "开跑门槛没接进 run()"
    assert "label_result(" in src, "描述版标签没接进 run()"
    assert "summarize_v6(" in src, "V6 主判决量没接进 run()"
    # run() 的签名必须收得下这三个开关,否则外面根本没法选口径
    params = set(inspect.signature(fl.run).parameters)
    assert {"p_floor", "descriptive", "boundary"} <= params, f"run() 少了开关:{sorted(params)}"


def test_V6_summary_tail_handles_both_shapes():
    """⭐行为验证:V5b 与 V6 的 summary 字段不同,打印必须两种都认。

    2026-08-26 真跑踩到:`run()` 的打印写死了 V5b 的键,
    给了 p_floor 之后直接 KeyError —— 而那时"接线在不在"的结构判据是绿的。
    ⇒ 结构检查管"接上了没有",行为检查管"接上之后能不能跑",两者缺一不可。
    """
    v6 = fl.summarize_v6([1.0, -1.0], [0.5, 0.5], p_floor=0.10)
    v5b = fl.summarize([1.0, -1.0], [0.5, 0.5])
    for summ, tag in ((v6, "V6"), (v5b, "V5b")):
        tail = fl._summary_tail(summ)      # 不许抛 KeyError
        assert isinstance(tail, str) and tail, f"{tag} 形状的 summary 没生成尾巴"
    assert "砍掉低价" in fl._summary_tail(v6)
    assert "p>=0.10 子集" in fl._summary_tail(v5b)


# =====================================================================
# 11. 2026-08-26 review 判 Block 的两条 CRITICAL,逐条钉住
# =====================================================================

def test_CRITICAL1_refuses_to_produce_a_verdict_on_an_already_seen_window():
    """⭐⭐CRITICAL 1:危险路径不许是默认。

    `run(p_floor=0.10)` 这个**最自然的调用**原本会拿到旧边界(V5b 已看过的窗)
    + `descriptive=False` ⇒ 产出一个**未标注的真判决**,
    正是预登记单 §2 明令禁止的「结果出来后挑标准」;而 §6 的数据量门槛救不了它
    (旧窗数据量绰绰有余)。⇒ 必须**直接拒绝**,不是 warning、不是默认值。
    """
    import datetime as _dt
    with pytest.raises(ValueError, match="拒绝在已看过的窗上出判决"):
        fl.run(p_floor=0.10)                       # 少传两个参数 = 危险路径
    with pytest.raises(ValueError):
        fl.run(p_floor=0.10, boundary=fl.BOUNDARY)  # 显式传旧边界也不行
    # 描述版放行(它本来就不是判决)—— 这里只验"没在边界上被拦",不跑完
    assert fl.VERDICT_BOUNDARY == _dt.datetime(2026, 8, 27, tzinfo=_dt.timezone.utc)


def test_CRITICAL1_boundary_check_happens_before_any_expensive_work(monkeypatch):
    """拦截必须在**跑起来之前** —— 否则等它算完 400 次置换才报错毫无意义。"""
    hit = {"n": 0}
    import duckdb
    real_connect = duckdb.connect
    monkeypatch.setattr(duckdb, "connect",
                        lambda *a, **k: (hit.__setitem__("n", hit["n"] + 1),
                                         real_connect(*a, **k))[1])
    with pytest.raises(ValueError):
        fl.run(p_floor=0.10)
    # 允许开了连接(建表在前),但绝不许跑到置换/自举那一步 —— 用耗时间接验证
    assert hit["n"] <= 1


def test_CRITICAL2_gate_actually_blocks_not_just_records(monkeypatch, tmp_path):
    """⭐⭐CRITICAL 2:`verdict_run_allowed` 的**拦截**必须真的阻断流程。

    review 变异实测:只删掉 `if not gate["allowed"]: return`(保留计算与记录),
    50 条判据一条不红 —— 因为没有任何判据真的调用过 `run()`。

    ⚠️ 本判据第一版**又是空过的**:它用 try/except 兜住 run() 的异常,
    而 run() 在够到门槛之前就先崩在 rank_wallets 上(验证窗为空)⇒
    except 分支断言"没走到 decide"(成立,因为压根没走到)⇒ 变异照样全绿。
    ⇒ 现在:① 门槛已前置到两臂之前(review MEDIUM 7);
           ② **断言门槛真的被调用过**,没调用过就是本判据没测到点上,必须红;
           ③ 不再吞异常。
    """
    import datetime as _dt
    seen = {"gate": 0, "decide": 0}
    monkeypatch.setattr(fl, "decide",
                        lambda *a, **k: (seen.__setitem__("decide", 1), {})[1])
    monkeypatch.setattr(fl, "verdict_run_allowed",
                        lambda **k: (seen.__setitem__("gate", seen["gate"] + 1),
                                     {"allowed": False, "missing": ["造出来的不足"],
                                      "checked": {}, "fills_checked": False})[1])
    r = fl.run(out_dir=tmp_path, p_floor=0.10,
               boundary=_dt.datetime(2026, 9, 1, tzinfo=_dt.timezone.utc))
    assert seen["gate"] > 0, "门槛压根没被调用 —— 本判据没测到点上(第一版就是这么空过的)"
    assert r["verdict"] == "NOT_YET_ENOUGH_DATA", f"门槛说不许跑,判决却是 {r['verdict']}"
    assert seen["decide"] == 0, "门槛说不许跑,却仍然走到了 decide()"
    assert "power_gate" in r, "早退分支没给出 power_gate 键,下游会 KeyError"


def test_CRITICAL2_gate_is_checked_before_the_expensive_work(monkeypatch, tmp_path):
    """⭐门槛必须在**两臂 + 400 次置换之前**跑(review MEDIUM 7)。

    否则数据太少时会先崩在 rank_wallets 上,根本走不到那句干净的「数据未攒够」。
    """
    import datetime as _dt
    seen = {"rank": 0}
    monkeypatch.setattr(fl, "rank_wallets",
                        lambda *a, **k: (seen.__setitem__("rank", 1), [])[1])
    monkeypatch.setattr(fl, "verdict_run_allowed",
                        lambda **k: {"allowed": False, "missing": ["造出来的不足"],
                                     "checked": {}, "fills_checked": False})
    fl.run(out_dir=tmp_path, p_floor=0.10,
           boundary=_dt.datetime(2026, 9, 1, tzinfo=_dt.timezone.utc))
    assert seen["rank"] == 0, "门槛拦下之后仍然跑了排名 —— 说明它排在昂贵计算之后"


def test_skipped_fills_check_leaves_a_trace(monkeypatch):
    """⚠️ 前置阶段跳过 fills 那一项时必须**留痕** ——
    静默当成通过与真的通过看起来一样,那正是本项目的死因。
    """
    g = fl.verdict_run_allowed(n_markets=99_999, n_wallets=99_999)
    assert g["fills_checked"] is False
    full = fl.verdict_run_allowed(n_markets=99_999, n_wallets=99_999, n_fills=99_999)
    assert full["fills_checked"] is True


def test_HIGH3_arm_config_reports_the_actual_boundary():
    """⭐HIGH 3:`arm_config` 的 boundary 也必须用**实际值**。

    这与本函数 docstring 里已经记过一次的 `delay_min` 是**同一 bug 类**,当时没有推广;
    结果 V6 新加的 boundary 又栽了一遍(review 抓到)。
    """
    import datetime as _dt
    b = _dt.datetime(2026, 9, 1, tzinfo=_dt.timezone.utc)
    assert fl.arm_config("A", 5, b)["boundary"] == b.isoformat()
    assert fl.arm_config("A")["boundary"] == fl.BOUNDARY.isoformat()


def test_MEDIUM5_descriptive_label_reaches_every_arm():
    """⭐只打顶层标签不够:有人直接读 arms[..]['green'] 就绕过去了。

    ⚠️ 本判据第一版传的是**自己造的、带 arms 的字典**,所以它绿着;
    而生产路径喂给 label_result 的是 `decide()` 的返回值 —— 那里面**没有 arms**
    ⇒ 每臂的标签根本没打上(2026-08-26 真跑 `descriptive_only=None` 才发现)。
    ⇒ 下面同时钉住生产路径的调用形状。
    """
    r = fl.label_result({"verdict": "PASS", "reading": "x",
                         "arms": {"A": {"green": True}, "B": {"green": True}}},
                        descriptive=True)
    for a in r["arms"].values():
        assert a.get("descriptive_only") is True, "描述版标签没打到臂上"


def test_MEDIUM5_production_path_feeds_the_whole_results_to_label():
    """结构检查:`run()` 必须把**含 arms 的整个 results** 喂给 label_result,
    而不是只喂 `decide()` 的返回值 —— 守的是「标签必须够得着每一臂」这条不变量。
    """
    import inspect
    src = inspect.getsource(fl.run)
    assert "label_result(results," in src, (
        "run() 没把整个 results 喂给 label_result ⇒ 每臂的标签打不上")
    assert "label_result(decide(" not in src, "又退回只喂 decide() 的返回值了"
