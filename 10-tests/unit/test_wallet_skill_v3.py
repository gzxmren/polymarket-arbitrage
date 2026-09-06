"""方向三 V3 双臂重测的验收判据(先于实现写成)。

预登记单:docs/PREREG_WALLET_SKILL_V3_2026-08-23.md

⭐本文件重点钉四件事(每一条都对应一个真实踩过的坑):
1. **功效闸必须在红线之前,且不过时判「无判决」而不是 FAIL** —— 08-19 的真正死因。
2. **V 窗必须要求下单也在边界之后** —— 只按结算时间切是事后口径,不可交易。
3. **双臂判读规则必须先写死**,且一臂绿一臂红时判「不可采信」,不许挑好看的那臂。
4. **红线要算出来并接到判决上** —— 本项目犯过「算了红线没接到判决」。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "08-backtests"))

import wallet_skill_v3 as v3  # noqa: E402


# ---------- 1. 分窗:可交易口径 ----------

def test_v_window_requires_the_trade_to_happen_after_the_boundary():
    """⭐排名只能在边界 T 时刻形成 ⇒ T 之前下的注不可能是照它下的。

    只按结算时间切窗会把「边界前就下好的注」算成战果 = 事后口径,不可交易。
    """
    T = v3.BOUNDARY
    # 结算在 T 之后,但下单在 T 之前 ⇒ 不属于 V 窗
    assert v3.window_of(ts=T - v3.ONE_HOUR, closed=T + v3.ONE_HOUR) is None
    # 结算在 T 之后且下单也在 T 之后 ⇒ V
    assert v3.window_of(ts=T + v3.ONE_HOUR, closed=T + 2 * v3.ONE_HOUR) == "V"
    # 结算在 T 之前 ⇒ D(下单时间不限,只要在结算前)
    assert v3.window_of(ts=T - 5 * v3.ONE_HOUR, closed=T - v3.ONE_HOUR) == "D"


def test_trades_after_settlement_are_excluded_by_the_firewall():
    """时间防火墙:下注时答案已揭晓的,一律不算。"""
    T = v3.BOUNDARY
    assert v3.window_of(ts=T + 2 * v3.ONE_HOUR, closed=T + v3.ONE_HOUR) is None


# ---------- 2. 盈亏口径 ----------

@pytest.mark.parametrize("side,oi,resolved,price,expected", [
    # ⚠️ win = (outcome_index != resolved_outcome) —— 与字面读法相反,已用末价独立验证
    ("BUY", 1, 0.0, 0.60, 0.40),    # 买到赢家腿,赚 1-0.60
    ("BUY", 0, 0.0, 0.60, -0.60),   # 买到输家腿,赔掉本金
    ("SELL", 1, 0.0, 0.60, -0.40),  # 卖掉赢家腿 = 少赚
    ("SELL", 0, 0.0, 0.60, 0.60),
])
def test_edge_uses_the_inverted_win_rule(side, oi, resolved, price, expected):
    assert v3.trade_edge(side, oi, resolved, price) == pytest.approx(expected)


def test_edge_is_already_net_of_the_entry_spread():
    """⚠️ 口径更正(预登记单 §3):edge 用的是**实际成交价**,入场价差已含在里面,
    不许再减一次 0.5pp。

    ⚠️ 本判据第一版是**纯文本匹配 docstring**(找"已经扣掉了入场时付出的价差"这句话),
    哪怕有人把公式改成再减一次 0.5pp,只要注释还在它就永远绿(2026-08-23 review 抓到)。
    现在改成**行为断言**:以成交价为基准,不许有任何额外扣减。
    """
    # 以 0.60 买入,最终赢 ⇒ 恰好 1-0.60,不多不少
    assert v3.trade_edge("BUY", 1, 0.0, 0.60) == pytest.approx(0.40)
    # 若有人偷偷再扣 0.5pp,这里会变成 0.395
    assert v3.trade_edge("BUY", 1, 0.0, 0.50) == pytest.approx(0.50)
    assert v3.trade_edge("BUY", 0, 0.0, 0.50) == pytest.approx(-0.50)


# ---------- 3. ⭐功效闸:必须在红线之前,不过时判「无判决」 ----------

def test_power_gate_blocks_verdict_when_the_ruler_is_too_coarse():
    """⭐08-19 的真正死因:零分布 p95=+4.16pp 而赚钱只需约 1pp
    ⇒ 尺子最小刻度是所需的 4 倍 ⇒ 那次的 🔴 什么都没证明。

    本判据钉住:这种情况必须判「无判决」,**不许报 FAIL**。
    """
    g = v3.power_gate(null_p95_pp=4.1646)
    assert g["passes"] is False
    assert g["verdict_if_blocked"] == "NO_VERDICT"
    assert "无分辨力" in g["reason"]


def test_power_gate_passes_when_the_ruler_is_fine_enough():
    g = v3.power_gate(null_p95_pp=0.42)
    assert g["passes"] is True


def test_power_gate_threshold_is_the_economically_meaningful_one():
    """闸的门槛必须是「赚钱所需的量级」,不是拍脑袋的统计惯例。"""
    assert v3.MDE_MAX_PP == 1.0
    assert v3.power_gate(null_p95_pp=1.0001)["passes"] is False
    assert v3.power_gate(null_p95_pp=0.9999)["passes"] is True


def test_underpowered_run_never_reports_a_falsification():
    """⭐整条链路上钉死:功效闸没过时,最终判决不许出现「证伪」字样。"""
    res = v3.decide(
        arm_a=v3.arm_verdict(r_v_pp=0.2, null_p95_pp=4.16, boot_q025_pp=-3.0),
        arm_b=v3.arm_verdict(r_v_pp=0.1, null_p95_pp=4.16, boot_q025_pp=-3.1),
        power=v3.power_gate(null_p95_pp=4.16))
    assert res["verdict"] == "NO_VERDICT"
    assert "证伪" not in res["reading"]


# ---------- 4. 单臂红线:算出来并接到判决上 ----------

def test_arm_needs_all_three_redlines():
    ok = dict(r_v_pp=2.0, null_p95_pp=1.0, boot_q025_pp=0.3)
    assert v3.arm_verdict(**ok)["green"] is True
    # A 不过(没越置换零分布)
    assert v3.arm_verdict(**{**ok, "null_p95_pp": 2.5})["green"] is False
    # A0 不过(低于绝对下限)
    assert v3.arm_verdict(**{**ok, "r_v_pp": 0.3})["green"] is False
    # B 不过(自举下界不为正)
    assert v3.arm_verdict(**{**ok, "boot_q025_pp": -0.1})["green"] is False


def test_absolute_floor_is_a_safety_margin_for_unmodeled_fees():
    assert v3.ABS_FLOOR_PP == 0.5
    d = v3.arm_verdict(r_v_pp=0.49, null_p95_pp=0.1, boot_q025_pp=0.4)
    assert d["green"] is False
    assert any("绝对下限" in x for x in d["failed"])


# ---------- 5. ⭐双臂判读规则:先写死,不许挑好看的那臂 ----------

@pytest.mark.parametrize("a_green,b_green,want", [
    (True, True, "PASS"),
    (False, False, "FALSIFIED"),
    (True, False, "UNRELIABLE"),
    (False, True, "UNRELIABLE"),
])
def test_two_arm_read_rule_is_pre_written(a_green, b_green, want):
    """⭐一臂绿一臂红 ⇒ 「不可采信」,**两个方向都是** ——
    不许因为臂A好看就单独汇报它,也不许反过来。
    """
    res = v3.decide(
        arm_a={"green": a_green, "r_v_pp": 1.0, "failed": []},
        arm_b={"green": b_green, "r_v_pp": 1.0, "failed": []},
        power=v3.power_gate(null_p95_pp=0.3))
    assert res["verdict"] == want


def test_split_arms_reading_names_the_bias_as_the_likely_cause():
    res = v3.decide(arm_a={"green": True, "r_v_pp": 2.0, "failed": []},
                    arm_b={"green": False, "r_v_pp": 0.1, "failed": ["A"]},
                    power=v3.power_gate(null_p95_pp=0.3))
    assert res["verdict"] == "UNRELIABLE"
    assert "单边采集" in res["reading"] or "有偏" in res["reading"]


def test_green_verdict_still_says_it_is_not_yet_an_edge():
    """⭐母单铁律:第一关全绿也**不等于** edge,只是有资格进第二关。"""
    res = v3.decide(arm_a={"green": True, "r_v_pp": 2.0, "failed": []},
                    arm_b={"green": True, "r_v_pp": 1.8, "failed": []},
                    power=v3.power_gate(null_p95_pp=0.3))
    assert res["verdict"] == "PASS"
    assert "第二关" in res["reading"]


# ---------- 6. 两臂口径必须逐条相同 ----------

def test_two_arms_differ_only_in_market_range():
    """⭐一次只动一个自变量。两臂的其它口径必须逐条确认相同。"""
    a = v3.arm_config("A")
    b = v3.arm_config("B")
    diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    assert diff == {"market_range"}, f"两臂差异不止一个自变量: {diff}"
    # ⚠️ 边界(2026-08-23 review 指出):本判据测的是 arm_config() 这份**文档字典**,
    #    它并不驱动 run() 里的 SQL。真正的一致性来自「两臂共用同一段循环体」这个代码结构。
    #    下面这条把那个结构也钉住:run() 里只许出现一处 `for arm in`。
    import inspect
    assert inspect.getsource(v3.run).count("for arm in") == 1, \
        "两臂被拆成了各写各的分支 —— arm_config 这条判据就管不住口径一致了"


# ---------- 7. 排名不许用未来信息 ----------

def test_ranking_cannot_structurally_see_validation_data():
    """⭐排名泄漏要做到「想写都写不出来」,不是靠人记得别写。

    ⚠️ 本判据第一版是**文本检查冒充结构检查**(去源码里找 "D" 这个字符串),
    实现里根本没那个字面量,判据当场红 —— 同一天里第二次犯这个毛病。
    正确做法:检查**函数签名** —— 只接受发掘窗成绩,结构上就拿不到验证窗的任何东西。
    """
    import inspect
    params = list(inspect.signature(v3.rank_wallets).parameters)
    assert params[0] == "d_edges", "排名函数的数据入口必须只有发掘窗成绩"
    data_params = [p for p in params
                   if inspect.signature(v3.rank_wallets).parameters[p].default
                   is inspect.Parameter.empty]
    assert data_params == ["d_edges"], f"排名函数还能拿到别的数据:{data_params}"
    # 行为验证:只喂 D 窗成绩就能算出名单
    got = v3.rank_wallets({"w1": 3.0, "w2": 1.0, "w3": 2.0}, top_fraction=0.34)
    assert got == ["w1"]


# =====================================================================
# 8. 2026-08-23 review 判 Block 的两条 HIGH
# =====================================================================

def _tiny_lake(con):
    """手搭一张最小表,用来跑**生产同款** SQL 表达式。"""
    con.execute("""CREATE TABLE t(outcome_index INTEGER, resolved_outcome DOUBLE,
                   dir INTEGER, price DOUBLE, ts TIMESTAMP, t_close TIMESTAMP)""")
    rows = [
        (1, 0.0, 1, 0.60, "2026-08-01 10:00", "2026-08-02 10:00"),   # D
        (0, 0.0, 1, 0.60, "2026-08-01 10:00", "2026-08-02 10:00"),   # D 输家腿
        (1, 0.0, -1, 0.30, "2026-08-09 10:00", "2026-08-10 10:00"),  # V 卖出
        (0, 1.0, 1, 0.20, "2026-08-07 10:00", "2026-08-10 10:00"),   # 结算在边界后但下单在边界前 ⇒ 不参与
        (1, 0.0, 1, 0.90, "2026-08-11 10:00", "2026-08-10 10:00"),   # 结算后下单 ⇒ 防火墙挡掉
    ]
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?,?)", rows)
    return rows


def test_HIGH1_sql_and_python_agree_on_win_edge_and_window():
    """⭐⭐HIGH1:`window_of()`/`trade_edge()` 曾经是**死代码** —— run() 在 SQL 里另写一份,
    判据测的是没人调用的替身。谁把 SQL 里那个 `<>` 手滑改成 `=`,21 条判据全绿。

    这是 2026-08-06 记下的最贵教训「替身替掉被测对象本身」的重演。
    本判据跑的是**生产用的那三个 SQL 常量本身**,与 Python 版逐行比对。
    """
    import duckdb
    con = duckdb.connect()
    try:
        rows = _tiny_lake(con)
        B = "TIMESTAMP '" + v3.BOUNDARY.strftime("%Y-%m-%d %H:%M:%S") + "'"
        # 生产里 EDGE_SQL 作用在已经算好的 win 列上,所以这里也先算 win 再算 edge
        inner = "SELECT *, " + v3.WIN_SQL + " AS win FROM t"
        sql = ("SELECT win, " + v3.EDGE_SQL + " AS edge, "
               + v3.WINDOW_SQL.format(B=B) + " AS tag FROM (" + inner + ")")
        got = con.execute(sql).fetchall()
    finally:
        con.close()
    assert len(got) == len(rows)
    import datetime as _dt
    for (oi, ro, dr, px, ts, tc), (win, edge, tag) in zip(rows, got):
        side = "BUY" if dr == 1 else "SELL"
        assert edge == pytest.approx(v3.trade_edge(side, oi, ro, px)), \
            f"SQL 与 Python 的 edge 不一致: {oi,ro,dr,px}"
        py_tag = v3.window_of(
            _dt.datetime.fromisoformat(ts).replace(tzinfo=_dt.timezone.utc),
            _dt.datetime.fromisoformat(tc).replace(tzinfo=_dt.timezone.utc))
        assert tag == py_tag, f"SQL 与 Python 的分窗不一致: ts={ts} close={tc}"


def test_HIGH1_the_equivalence_test_would_catch_a_flipped_operator():
    """⭐先在坏数据上验:把 `<>` 改成 `=`(最容易手滑的那一处),必须被抓出来。"""
    import duckdb
    con = duckdb.connect()
    try:
        _tiny_lake(con)
        bad_win = v3.WIN_SQL.replace("<>", "=")
        got = con.execute(f"SELECT {bad_win} FROM t").fetchall()
        good = con.execute(f"SELECT {v3.WIN_SQL} FROM t").fetchall()
    finally:
        con.close()
    assert got != good, "翻转 <> 之后结果没变 —— 这条等价性判据抓不住那个坑"


# ---------- HIGH2:统计核心函数的直接覆盖(原先零覆盖) ----------

def test_pctl_boundaries():
    v = [1.0, 2.0, 3.0, 4.0]
    assert v3.pctl(v, 0.0) == 1.0
    assert v3.pctl(v, 0.95) == 4.0
    assert v3.pctl(v, 1.0) == 4.0, "q=1.0 不许越界"
    assert math_isnan(v3.pctl([], 0.5)), "空输入必须给 nan,不许给 0(0 会被当成真实分位数)"


def math_isnan(x):
    import math
    return math.isnan(x)


def test_weighted_edge_is_dollar_weighted_and_handles_empty():
    assert v3._weighted_edge([]) == 0.0
    # 大仓位应当主导:(+1 on $100) 与 (−1 on $1) ⇒ 接近 +100pp*100/101
    got = v3._weighted_edge([(1.0, 100.0), (-1.0, 1.0)])
    assert got == pytest.approx(100.0 * (1.0 - 1.0) / 101.0)
    assert v3._weighted_edge([(0.5, 100.0)]) == pytest.approx(0.5)


def test_permutation_null_samples_without_replacement():
    """⭐边界用例:k 等于池子大小时,每次抽的必然是**全体** ⇒ 零分布应当退化成一个常数。
    若实现成了有放回抽样,这里会立刻变红(很好抓 off-by-one 与放回/不放回)。
    """
    pool = [f"w{i}" for i in range(6)]
    v = {w: (float(i), 10.0) for i, w in enumerate(pool)}
    null = v3.permutation_null(pool, len(pool), v, n=20)
    assert len(set(round(x, 9) for x in null)) == 1
    assert null[0] == pytest.approx(v3._weighted_edge(list(v.values())))


def test_bootstrap_resamples_markets_not_trades():
    """⭐按**市场**聚类重抽:一个市场占 90% 权重时,它被抽中的次数会波动
    ⇒ 结果分布必须有明显宽度。若错写成按笔重抽,宽度会被严重低估。
    """
    per_market = {"m1": (9.0, 900.0), "m2": (-0.5, 50.0), "m3": (-0.5, 50.0)}
    lo, hi = v3.bootstrap_by_market(per_market, n=500)
    assert hi > lo
    assert hi - lo > 0.1, "重抽几乎没有波动 —— 可能没在按市场重抽"


def test_bootstrap_empty_input_is_not_mistaken_for_a_pass():
    """空输入返回 (0,0);而红线 B 要求 2.5% 分位 **> 0** ⇒ 空数据不会被误判成绿。"""
    lo, hi = v3.bootstrap_by_market({})
    assert (lo, hi) == (0.0, 0.0)
    assert v3.arm_verdict(r_v_pp=5.0, null_p95_pp=1.0, boot_q025_pp=lo)["green"] is False


def test_results_are_reproducible_regardless_of_input_row_order():
    """⭐同一个种子 + 打乱入参顺序 ⇒ 结果必须逐字相同。

    入参来自没有 ORDER BY 的 SQL,DuckDB 并行执行行序不确定;
    不排序的话同种子两次跑会得到不同的零分布(2026-08-23 做回归对照时实测到,
    本项目 2026-08-19 踩过同一个坑)。
    """
    import random as _r
    pool = [f"w{i:03d}" for i in range(50)]
    v = {w: (float(i % 7) - 3, 10.0 + i) for i, w in enumerate(pool)}
    a = v3.permutation_null(pool, 10, v, n=50)
    shuffled = pool[:]
    _r.Random(1).shuffle(shuffled)
    b = v3.permutation_null(shuffled, 10, v, n=50)
    assert a == b, "打乱入参顺序结果就变了 —— 不可复现"

    pm = {f"m{i:03d}": (float(i) - 5, 100.0 + i) for i in range(20)}
    pm_shuf = dict(sorted(pm.items(), key=lambda kv: kv[0][::-1]))
    assert v3.bootstrap_by_market(pm, n=50) == v3.bootstrap_by_market(pm_shuf, n=50)
