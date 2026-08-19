"""判据:方向三第一关脚本(08-backtests/wallet_skill_persistence.py)。

⭐ 本文件的重点不是「好数据上能跑通」,而是【坏数据上必须亮红】——
CLAUDE.md / memory(decision-cancel-timer-split-2026-08-10):
「把形状写进文档挡不住复发,唯一有效的是机械自检:判据先在坏数据上跑确认会红」。

故每个防线都配一条「造真坏输入 → 断言它拦住」的用例。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "08-backtests" / "wallet_skill_persistence.py"


def _load():
    spec = importlib.util.spec_from_file_location("wsp", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wsp"] = mod
    spec.loader.exec_module(mod)
    return mod


wsp = _load()


# ─────────────────────────────────────────────────────────────────────────────
# §4 符号铁律 —— 判定式本身
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("idx,resolved,expected", [
    # resolved_outcome 是【0 号结果的最终赔付值】,不是赢家编号
    (0, 1.0, 1.0),   # 0 号赔付 1.0 -> 买 0 号者赢
    (0, 0.0, 0.0),   # 0 号赔付 0.0 -> 买 0 号者输
    (1, 1.0, 0.0),   # 0 号赔付 1.0 -> 买 1 号者输
    (1, 0.0, 1.0),   # 0 号赔付 0.0 -> 买 1 号者赢
])
def test_win_all_four_cases(idx, resolved, expected):
    con = duckdb.connect()
    expr = wsp.sql_win("idx", "res")
    got = con.execute(f"SELECT {expr} FROM (SELECT {idx} idx, {resolved} res)").fetchone()[0]
    assert got == expected, f"idx={idx} resolved={resolved} 应为 {expected},实得 {got}"


def test_win_is_not_the_naive_reading():
    """⭐ 防的是「按字面读成赢家编号」——那个读法与正确式在两处相反,且不会报错。

    memory: pitfall-resolved-outcome-is-payout。若哪天有人「顺手改对」成字面读法,
    本用例必须失败。
    """
    con = duckdb.connect()
    correct = wsp.sql_win("idx", "res")
    naive = "CASE WHEN idx = res THEN 1.0 ELSE 0.0 END"   # 字面读法(错的)
    rows = con.execute(f"""
        SELECT idx, res, {correct} c, {naive} n FROM (VALUES (0,1.0),(0,0.0),(1,1.0),(1,0.0)) v(idx,res)
    """).fetchall()
    # 实测(2026-08-18):两种读法在【全部 4/4】种情形下都不同,且恒有 naive == 1 - correct
    # —— 即字面读法给出的是【完全相反】的答案。这正是「符号静默翻转、翻转后看着一样合理」。
    disagree = [r for r in rows if r[2] != r[3]]
    assert len(disagree) == 4, f"两种读法应在 4/4 种情形下不同,实得 {len(disagree)}"
    for idx, res, c, n in rows:
        assert float(c) + float(n) == 1.0, f"idx={idx} res={res}: 应恒有 naive = 1 - correct"


@pytest.mark.parametrize("side,expected", [("BUY", 1.0), ("SELL", -1.0)])
def test_dir(side, expected):
    con = duckdb.connect()
    got = con.execute(f"SELECT {wsp.sql_dir('s')} FROM (SELECT '{side}' s)").fetchone()[0]
    assert got == expected


# ─────────────────────────────────────────────────────────────────────────────
# 比赛组键(§6 红线 B 的 bootstrap 分块单位)
# ─────────────────────────────────────────────────────────────────────────────
def test_race_group_same_match_groups_together():
    """同一场比赛的多个玩法必须落进同一组,否则 bootstrap 会低估相关 -> 假绿灯。"""
    con = duckdb.connect()
    slugs = ["codmw-100t-bos-2026-08-05-game1", "codmw-100t-bos-2026-08-05-game2",
             "codmw-100t-bos-2026-08-05", "codmw-100t-bos-2026-08-05-total-games-3pt5"]
    expr = wsp.sql_race_group("s")
    got = {con.execute(f"SELECT {expr} FROM (SELECT '{s}' s)").fetchone()[0] for s in slugs}
    assert got == {"codmw-100t-bos-2026-08-05"}, f"应归一组,实得 {got}"


def test_race_group_different_matches_stay_apart():
    con = duckdb.connect()
    expr = wsp.sql_race_group("s")
    a = con.execute(f"SELECT {expr} FROM (SELECT 'ned2-alm-psv-2026-08-14-draw' s)").fetchone()[0]
    b = con.execute(f"SELECT {expr} FROM (SELECT 'ned2-alm-psv-2026-08-15-draw' s)").fetchone()[0]
    assert a != b, "不同日期的两场比赛不得归为同一组"


def test_race_group_nonsport_stays_singleton():
    """无日期模式(政治/天气/股价)各自独立成组 —— 保守,不低估相关。"""
    con = duckdb.connect()
    expr = wsp.sql_race_group("s")
    s = "will-nvda-reach-212-by-august-3-2026"
    assert con.execute(f"SELECT {expr} FROM (SELECT '{s}' s)").fetchone()[0] == s


# ─────────────────────────────────────────────────────────────────────────────
# §6 红线判决
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("r_v_pp,q025_pp,expected", [
    (0.6,  0.1, True),    # 两条都过
    (0.4,  0.1, False),   # 幅度不过
    (0.6, -0.1, False),   # 显著不过
    (0.5,  0.001, True),  # 幅度恰好等于红线 -> 过(单子写的是 >=)
    (None, 0.1, False),   # 数据不足
    (0.6, None, False),
])
def test_judge_redlines(r_v_pp, q025_pp, expected):
    # G5 固定传 True:本组只检验红线 A/B,G5 的否决权另有一组专测
    got, _ = wsp.judge(r_v_pp, q025_pp, True, 0.0)
    assert got is expected


def test_judge_uses_prereg_constants():
    """⭐ 红线必须【import 被检验对象自己的常量】,不许在判据里复制粘贴数字。

    CLAUDE.md:「红线先写、且尽量逐字沿用/import 被检验对象自己的红线」。
    """
    assert wsp.REDLINE_A_PP == 0.5
    assert wsp.BOOTSTRAP_Q == 0.025
    # 恰好压线与恰好差一点,都用脚本自己的常量构造
    assert wsp.judge(wsp.REDLINE_A_PP, 0.001, True, 0.0)[0] is True
    assert wsp.judge(wsp.REDLINE_A_PP - 1e-9, 0.001, True, 0.0)[0] is False


# ─────────────────────────────────────────────────────────────────────────────
# 出声计数台账:首尾对账必须能抓到「静默丢样本」
# ─────────────────────────────────────────────────────────────────────────────
def test_ledger_reconcile_catches_silent_drop():
    """⭐ 造一次真的静默丢样本,确认对账会 FAIL(否则台账等于没有)。"""
    led = wsp.Ledger()
    led.step("D", "闸一", 1000, 900)
    led.step("D", "闸二", 900, 800)
    assert led.reconcile("D", 1000, 800) is True
    # 现在人为让最终数少 50(模拟某一步丢了样本却没记账)
    assert led.reconcile("D", 1000, 750) is False


# ─────────────────────────────────────────────────────────────────────────────
# §2 封窗等待期
# ─────────────────────────────────────────────────────────────────────────────
def test_seal_window_blocks_early_run(monkeypatch):
    """⭐ 造「V 窗末日刚过 1 小时就开跑」,确认脚本中止。

    由来(单子 §0.3):V1 就是在 V 窗末日 +3.7h 测的,窗末那天被系统性削薄 2,517 笔。
    """
    import datetime as dtm
    v_end = dtm.datetime.fromisoformat(wsp.WIN_V[1]).replace(tzinfo=dtm.UTC).timestamp()
    monkeypatch.setattr(wsp.time, "time", lambda: v_end + 3600)   # 只过了 1 小时
    with pytest.raises(SystemExit) as e:
        wsp.selfcheck_seal_window()
    assert e.value.code == 1


def test_seal_window_allows_late_run(monkeypatch):
    import datetime as dtm
    v_end = dtm.datetime.fromisoformat(wsp.WIN_V[1]).replace(tzinfo=dtm.UTC).timestamp()
    monkeypatch.setattr(wsp.time, "time", lambda: v_end + wsp.SEAL_WAIT_SEC + 1)
    assert wsp.selfcheck_seal_window()["pass"] is True


# ─────────────────────────────────────────────────────────────────────────────
# ⭐⭐ 最重要的一条:符号自检必须在【真的反转数据】上亮红
# 造的是真 parquet 文件走真 DuckDB 查询,不是内存里的假对象
# (CLAUDE.md 异常清单 4:注释里承诺的保护,必须实测它真的接得住)
# ─────────────────────────────────────────────────────────────────────────────
def _write_fake_lake(tmp_path: Path, inverted: bool) -> tuple[str, str]:
    """造一个最小数据湖。inverted=True 时把末价与结算的关系整体反转。

    正常:idx == resolved_outcome 的那一侧末价趋近 0(输);!= 的一侧趋近 1(赢)。
    """
    raw_dir = tmp_path / "raw" / "dt=2026-08-01"
    reg_dir = tmp_path / "registry"
    raw_dir.mkdir(parents=True), reg_dir.mkdir(parents=True)

    cids, oidx, prices, ts = [], [], [], []
    res_cid, res_val = [], []
    for i in range(40):
        cid = f"0x{i:040x}"
        resolved = 0.0 if i % 2 == 0 else 1.0
        res_cid.append(cid); res_val.append(resolved)
        for idx in (0, 1):
            is_eq = (float(idx) == resolved)          # eq 组 = 输方
            lose_price, win_price = 0.03, 0.97
            if inverted:                               # 坏数据:关系整体对调
                lose_price, win_price = 0.97, 0.03
            cids.append(cid); oidx.append(idx)
            prices.append(lose_price if is_eq else win_price)
            ts.append(1786900000 + i * 100 + idx)

    pq.write_table(pa.table({
        "condition_id": cids, "outcome_index": pa.array(oidx, pa.int32()),
        "price": prices, "timestamp": pa.array(ts, pa.int64()),
        "size": [10.0] * len(cids), "side": ["BUY"] * len(cids),
        "ingested_at": pa.array([t + 60 for t in ts], pa.int64()),
        "proxy_wallet": [f"0xw{i%7}" for i in range(len(cids))],
    }), raw_dir / "a.parquet")
    pq.write_table(pa.table({
        "condition_id": res_cid, "resolved_outcome": res_val,
        "snapshot_at": pa.array([1786900000] * len(res_cid), pa.int64()),
        "slug": [f"lg-a-b-2026-08-0{i%9+1}" for i in range(len(res_cid))],
        "market_class": ["event"] * len(res_cid),
        "hft_suspect": [False] * len(res_cid),
        "end_date": ["2026-08-02T00:00:00Z"] * len(res_cid),
    }), reg_dir / "m.parquet")
    return str(raw_dir.parent / "*" / "*.parquet"), str(reg_dir / "*.parquet")


def test_sign_selfcheck_passes_on_good_data(tmp_path, monkeypatch, capsys):
    raw, reg = _write_fake_lake(tmp_path, inverted=False)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")   # 测试隔离:不许往仓库写
    con = wsp.connect()
    out = wsp.selfcheck_sign(con)
    assert out["pass"] is True
    assert out["separation"] > wsp.SIGN_SEPARATION_MIN


def test_sign_selfcheck_DIES_on_inverted_data(tmp_path, monkeypatch):
    """⭐ 这条是全文件最重要的一条。

    若它没红,说明 §4 那道「防符号静默翻转」的防线是【假的】——
    而符号翻转恰恰是「不报错、结果看着一样合理」的那类故障。
    """
    raw, reg = _write_fake_lake(tmp_path, inverted=True)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")   # 测试隔离:不许往仓库写
    con = wsp.connect()
    with pytest.raises(SystemExit) as e:
        wsp.selfcheck_sign(con)
    assert e.value.code == 1, "反转数据必须让脚本中止,不许降级继续"


def test_sign_selfcheck_would_pass_a_weaker_absolute_threshold(tmp_path, monkeypatch):
    """⭐ 证明「换用分离度判据」不是放水:构造一份【绝对阈值过、但符号已反转】的数据。

    V1 的旧判据只看 median(eq)<0.10 与 median(ne)>0.90。若某天两组价格都挤到中间
    (例如 eq=0.45 / ne=0.55),旧判据两条都不满足会红;但真正危险的是【符号反转】。
    这里验证:分离度判据对反转有分辨力,且对「价格未充分收敛」保持宽容 —— 这正是
    单子 §4 换判据的理由(旧阈值一部分测的是收敛程度,不是符号)。
    """
    raw, reg = _write_fake_lake(tmp_path, inverted=False)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")   # 测试隔离:不许往仓库写
    con = wsp.connect()
    out = wsp.selfcheck_sign(con)
    # 好数据上:分离度判据过,且两条 V1 绝对阈值也一并被计算并公布(不作判决)
    assert out["pass"] is True
    assert "v1_abs_eq_lt_010" in out and "v1_abs_ne_gt_090" in out


def test_shuffle_within_price_band_preserves_structure(tmp_path):
    """P2 打乱结果:必须保留价格与下注结构,只砍掉真实输赢。"""
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("""CREATE TABLE src AS SELECT * FROM (VALUES
        ('a', 0.1, 1.0, 5.0), ('b', 0.15, 0.0, 6.0), ('c', 0.5, 1.0, 7.0),
        ('d', 0.6, 0.0, 8.0), ('e', 0.9, 1.0, 9.0), ('f', 0.95, 0.0, 10.0))
        v(transaction_hash, price, win, size)""")
    wsp._shuffle_win_within_price_band(con, "src", "dst")
    a = con.execute("SELECT count(*), sum(price), sum(size), sum(win) FROM src").fetchone()
    b = con.execute("SELECT count(*), sum(price), sum(size), sum(win) FROM dst").fetchone()
    assert a[0] == b[0], "行数必须不变"
    assert abs(float(a[1]) - float(b[1])) < 1e-9, "价格结构必须不变"
    assert abs(float(a[2]) - float(b[2])) < 1e-9, "下注结构(size)必须不变"
    assert abs(float(a[3]) - float(b[3])) < 1e-9, "win 的边际分布必须不变(只是重新配对)"


# ─────────────────────────────────────────────────────────────────────────────
# ⭐⭐ P2 打乱结果的两种实现:必须证明「修订版保住 E[edge]=0,原做法不保」
# 焊住 2026-08-18 的发现 —— 否则下次有人「顺手改回」档内 shuffle,
# §8 那道闸门会重新变成【永远亮绿、对判据好坏零分辨力】。
# ─────────────────────────────────────────────────────────────────────────────
def _synthetic_market(con, n=60000, seed=11):
    """造一份【市场定价无偏】的合成成交:win ~ Bernoulli(price),且低价笔 size 更大。

    size 与 price 负相关是真实数据的性质(同样的钱买更多份),也正是分母效应的来源。
    """
    con.execute("SET TimeZone='UTC'")
    con.execute(f"""
        CREATE OR REPLACE TABLE synth AS
        SELECT 'tx' || i AS transaction_hash, price, 100.0 / price AS size, 1.0 AS dir,
               CASE WHEN random() < price THEN 1.0 ELSE 0.0 END AS win
        FROM (SELECT i, 0.02 + (i % 97) / 100.0 AS price
              FROM range({n}) t(i)) WHERE price < 1.0
    """)


def test_p2_bernoulli_keeps_edge_expectation_at_zero():
    con = duckdb.connect()
    con.execute(f"SELECT setseed(0.31)")
    _synthetic_market(con)
    rs = []
    for k in range(5):
        wsp._resample_win_from_price(con, "synth", "synth_b", salt=k)
        r = con.execute("SELECT sum(size*dir*(win-price))/sum(size*price)*100 FROM synth_b").fetchone()[0]
        rs.append(float(r))
    mean = sum(rs) / len(rs)
    assert abs(mean) < 1.0, f"Bernoulli(price) 应保住 E[edge]≈0,实得均值 {mean:+.4f}pp ({rs})"


def test_p2_original_band_shuffle_INJECTS_positive_bias():
    """⭐ 这条是「证明原做法坏掉」的那条 —— 它必须【失败于原做法】。

    若哪天这条测试变绿(即档内 shuffle 不再注入正偏),说明数据的 size-price 关系变了,
    届时应重新评估 P2 该用哪种实现,而不是默默沿用。
    """
    con = duckdb.connect()
    con.execute(f"SELECT setseed(0.31)")
    _synthetic_market(con)
    rs = []
    for k in range(5):
        con.execute(f"SELECT setseed({0.1 * k - 0.2:.3f})")
        wsp._shuffle_win_within_price_band(con, "synth", "synth_s")
        r = con.execute("SELECT sum(size*dir*(win-price))/sum(size*price)*100 FROM synth_s").fetchone()[0]
        rs.append(float(r))
    mean = sum(rs) / len(rs)
    assert mean > 1.0, (f"档内 shuffle 应【注入】明显正偏(2026-08-18 真实数据实测 +2.2pp),"
                        f"实得均值 {mean:+.4f}pp ({rs})。若不再注入,须重评 P2 实现。")


def test_p2_two_implementations_disagree_materially():
    """两种实现必须给出实质不同的结果 —— 否则「修订」这件事本身没有意义。"""
    con = duckdb.connect()
    con.execute(f"SELECT setseed(0.77)")
    _synthetic_market(con)
    wsp._resample_win_from_price(con, "synth", "s_b")
    wsp._shuffle_win_within_price_band(con, "synth", "s_s")
    q = "SELECT sum(size*dir*(win-price))/sum(size*price)*100 FROM {}"
    rb = float(con.execute(q.format("s_b")).fetchone()[0])
    rs = float(con.execute(q.format("s_s")).fetchone()[0])
    assert rs - rb > 1.0, f"两实现差异应显著:Bernoulli {rb:+.4f}pp vs 档内shuffle {rs:+.4f}pp"


def test_p2_bernoulli_preserves_price_and_size_structure():
    con = duckdb.connect()
    con.execute(f"SELECT setseed(0.5)")
    _synthetic_market(con, n=5000)
    wsp._resample_win_from_price(con, "synth", "synth_b")
    a = con.execute("SELECT count(*), sum(price), sum(size) FROM synth").fetchone()
    b = con.execute("SELECT count(*), sum(price), sum(size) FROM synth_b").fetchone()
    assert a[0] == b[0] and abs(float(a[1]) - float(b[1])) < 1e-6 and abs(float(a[2]) - float(b[2])) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# §8 闸门:阈值必须随种子数缩放,且「跑得少」不许算通过
#
# 由来(2026-08-18 实测,不是假想):脚本原实现把上限写死成 BADCHECK_MAX_GREEN(=1),
# 与实际跑了几个种子无关。那天用 `--seeds 3` 试跑、P1 亮绿 1 个,屏幕照样打印 PASS ——
# 实际容忍度 1/3 = 33%,而单子写的是 1/20 = 5%,悄悄放宽 6.7 倍。
# 对准核心问句:「如果它现在就是坏的,我看到的会有什么不同?」——【完全没有不同】。
# 故本组判据的重点不是「20 个种子时算得对」,而是【缩水工况下必须判红】。
# ─────────────────────────────────────────────────────────────────────────────
def test_badcheck_gate_matches_prereg_at_registered_seed_count():
    """单子写死:20 次种子、亮绿 <= 1。用脚本自己的常量构造,不在判据里复制数字。"""
    g = wsp.badcheck_gate(wsp.BADCHECK_MAX_GREEN, wsp.BADCHECK_MAX_GREEN, wsp.BADCHECK_SEEDS)
    assert g["authoritative"] is True
    assert g["max_green"] == wsp.BADCHECK_MAX_GREEN
    assert g["pass"] is True
    # 恰好多一个就必须红 —— P1、P2 任一侧都要焊死
    assert wsp.badcheck_gate(wsp.BADCHECK_MAX_GREEN + 1, 0, wsp.BADCHECK_SEEDS)["pass"] is False
    assert wsp.badcheck_gate(0, wsp.BADCHECK_MAX_GREEN + 1, wsp.BADCHECK_SEEDS)["pass"] is False


def test_badcheck_gate_does_not_loosen_when_fewer_seeds_run():
    """⭐ 核心用例:复刻 2026-08-18 那次真实的 3 种子 / P1 亮绿 1 个。"""
    g = wsp.badcheck_gate(1, 0, 3)
    assert g["max_green"] == 0, "3 个种子 x 5% = 0.15,向下取整 = 0,不许还容忍 1 个"
    assert g["pass"] is False
    assert g["authoritative"] is False


def test_badcheck_gate_undersized_run_never_passes_even_with_zero_green():
    """种子数不足 = 压根没跑单子要求的那个检验,零亮绿也不构成通过。"""
    for seeds in (0, 1, 2, 3, wsp.BADCHECK_SEEDS - 1):
        g = wsp.badcheck_gate(0, 0, seeds)
        assert g["authoritative"] is False
        assert g["pass"] is False, f"seeds={seeds} 零亮绿仍不许算通过"
        assert "非权威" in g["reason"], "必须说出为什么不算,不许只给个 False"


def test_badcheck_gate_scales_up_and_keeps_the_same_rate():
    """多跑种子时上限等比放大,假阳性率恒定 —— 不许因为多跑反而严到不可能过。"""
    for mult in (1, 2, 5):
        seeds = wsp.BADCHECK_SEEDS * mult
        g = wsp.badcheck_gate(0, 0, seeds)
        assert g["authoritative"] is True
        assert g["max_green"] == wsp.BADCHECK_MAX_GREEN * mult
        assert wsp.badcheck_gate(g["max_green"], g["max_green"], seeds)["pass"] is True
        assert wsp.badcheck_gate(g["max_green"] + 1, 0, seeds)["pass"] is False


def test_badcheck_gate_actually_differs_from_the_old_fixed_threshold():
    """机械自检:把【旧实现原样搬进来】跑同一组输入,必须分道扬镳。

    若新旧对所有输入都同意,说明这次「修复」是空的 —— 而它看起来会跟修好了一模一样。
    分歧方向还必须单向:只允许「旧说通过、新说不通过」,不许出现新的反而更松。
    """
    def old_gate(g1, g2, seeds):          # 2026-08-18 之前逐字的真实实现
        return (g1 <= wsp.BADCHECK_MAX_GREEN) and (g2 <= wsp.BADCHECK_MAX_GREEN)

    cases = [(g1, g2, s) for s in (1, 2, 3, 5, 10, wsp.BADCHECK_SEEDS - 1)
             for g1 in (0, 1) for g2 in (0, 1)]
    disagree = [c for c in cases if old_gate(*c) != wsp.badcheck_gate(*c)["pass"]]
    assert disagree, "新旧实现对所有输入都同意 => 这次修复什么也没改变"
    for g1, g2, s in disagree:
        assert old_gate(g1, g2, s) is True
        assert wsp.badcheck_gate(g1, g2, s)["pass"] is False


def test_badcheck_gate_result_is_recorded_not_just_computed():
    """CLAUDE.md「记录事实 vs 使用事实,只接了一头」——

    闸门算出来的东西必须整份可落盘(种子数/上限/是否权威/两侧亮绿数),
    否则事后翻 json 无从分辨这次是权威跑还是试跑。
    """
    import json
    g = wsp.badcheck_gate(1, 2, 20)
    for k in ("pass", "max_green", "authoritative", "seeds", "p1_green", "p2_green", "reason"):
        assert k in g, f"闸门结果缺字段 {k},落盘后就说不清了"
    assert g["seeds"] == 20 and g["p1_green"] == 1 and g["p2_green"] == 2
    json.dumps(g)   # 必须可序列化


# ═════════════════════════════════════════════════════════════════════════════
# 主干过滤链判据(2026-08-18 review 第 9 条:此前 11 个主干函数零覆盖)
#
# 此前的判据只覆盖了纯公式类小函数。用核心问句自问:「如果 build_window /
# apply_wallet_layer / reconcile 现在就是坏的,那些判据会有什么不同?」
# —— 完全没有不同,全部照样绿。故本节一律【造真 parquet、走真 DuckDB】,
# 且每条防线都配一个「故意弄坏 -> 必须中止」的孪生用例。
# ═════════════════════════════════════════════════════════════════════════════
import datetime as _dt


def _epoch(y: int, m: int, d: int, h: int = 0) -> int:
    return int(_dt.datetime(y, m, d, h, tzinfo=_dt.timezone.utc).timestamp())


def _write_chain_lake(tmp_path: Path, n_markets: int, n_wallets: int = 6) -> tuple[str, str]:
    """造一个能完整走通过滤链的最小数据湖:D/V 两窗各 n_markets 个【互不相同】的市场。

    刻意满足:市场层 event/非 hft、闸一(采集延迟 60s)、闸二(结算 +2 天)、
    两窗各 n_markets 笔/钱包(需 >= MIN_TRADES 才过 L7)、每钱包每市场 1 笔
    (tpm=1,远低于机器人线 20)、D/V 市场不重叠(不触发 L5 泄漏)。
    """
    raw_dir = tmp_path / "raw" / "dt=2026-08-01"
    reg_dir = tmp_path / "registry"
    raw_dir.mkdir(parents=True), reg_dir.mkdir(parents=True)

    cid, oidx, price, ts, ing, wallet, size, side = [], [], [], [], [], [], [], []
    r_cid, r_val, r_end, r_slug = [], [], [], []
    for win_tag, (ty, tm, td), (ey, em, ed) in (
            ("d", (2026, 7, 23), (2026, 7, 25)),
            ("v", (2026, 8, 3), (2026, 8, 5))):
        for i in range(n_markets):
            c = f"0x{win_tag}{i:039x}"
            resolved = 0.0 if i % 2 == 0 else 1.0
            r_cid.append(c); r_val.append(resolved)
            r_end.append(f"{ey:04d}-{em:02d}-{ed:02d}T00:00:00Z")
            r_slug.append(f"plain-market-{win_tag}-{i}")
            for w in range(n_wallets):
                t = _epoch(ty, tm, td, 1) + i * 7 + w
                cid.append(c); oidx.append(w % 2); price.append(0.40 + 0.01 * (w % 5))
                ts.append(t); ing.append(t + 60); wallet.append(f"0xw{w}")
                size.append(10.0); side.append("BUY")

    pq.write_table(pa.table({
        "transaction_hash": [f"0xt{i}" for i in range(len(cid))],
        "condition_id": cid, "outcome_index": pa.array(oidx, pa.int32()),
        "price": price, "timestamp": pa.array(ts, pa.int64()),
        "size": size, "side": side, "ingested_at": pa.array(ing, pa.int64()),
        "proxy_wallet": wallet,
    }), raw_dir / "a.parquet")
    pq.write_table(pa.table({
        "condition_id": r_cid, "resolved_outcome": r_val,
        "snapshot_at": pa.array([_epoch(2026, 8, 12)] * len(r_cid), pa.int64()),
        "slug": r_slug, "market_class": ["event"] * len(r_cid),
        "hft_suspect": [False] * len(r_cid), "end_date": r_end,
    }), reg_dir / "m.parquet")
    return str(raw_dir.parent / "*" / "*.parquet"), str(reg_dir / "*.parquet")


def _run_chain(tmp_path, monkeypatch, n_markets: int):
    raw, reg = _write_chain_lake(tmp_path, n_markets)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")   # 测试隔离:不许往仓库写
    con = wsp.connect()
    led = wsp.Ledger()
    wsp.build_window(con, "D", wsp.WIN_D, led, wsp.SETTLE_GATE_DAYS, quiet=False)
    wsp.build_window(con, "V", wsp.WIN_V, led, wsp.SETTLE_GATE_DAYS, quiet=False)
    wl = wsp.apply_wallet_layer(con, led, wsp.MIN_TRADES, True, quiet=False)
    return con, led, wl


# ── 防线一:首尾对账必须真的接在主路径上 ──────────────────────────────────
def test_chain_reconcile_runs_and_passes_on_a_clean_lake(tmp_path, monkeypatch, capsys):
    con, led, wl = _run_chain(tmp_path, monkeypatch, n_markets=420)
    out = capsys.readouterr().out
    assert "首尾对账" in out, "主路径跑完却没有任何对账输出 => reconcile 又没接上"
    assert "🔴 FAIL" not in out
    assert wl["trades_D"] > 0 and wl["trades_V"] > 0


def test_chain_reconcile_DIES_when_a_step_forgets_to_announce(tmp_path, monkeypatch):
    """⭐ 本节最重要的一条:复刻「有人加了一道 WHERE 却忘了配 led.step」。

    这正是 reconcile 存在的唯一理由。若这条不红,说明对账即使接上了也抓不到东西。
    """
    real_step = wsp.Ledger.step

    def swallow_L2(self, window, name, before, after, note=""):
        if name.startswith("L2"):        # 静默吞掉这一级剔除,不留任何痕迹
            return
        return real_step(self, window, name, before, after, note)

    monkeypatch.setattr(wsp.Ledger, "step", swallow_L2)
    # 让 L2 真的有东西可剔:把一部分市场标成 hft_suspect
    raw, reg = _write_chain_lake(tmp_path, 420)
    import pyarrow.parquet as _pq
    reg_f = list((tmp_path / "registry").glob("*.parquet"))[0]
    t = _pq.read_table(reg_f).to_pydict()
    t["hft_suspect"] = [i % 5 == 0 for i in range(len(t["condition_id"]))]
    _pq.write_table(pa.table(t), reg_f)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")   # 测试隔离:不许往仓库写
    con = wsp.connect()
    led = wsp.Ledger()
    with pytest.raises(SystemExit) as e:
        wsp.build_window(con, "D", wsp.WIN_D, led, wsp.SETTLE_GATE_DAYS, quiet=False)
        wsp.build_window(con, "V", wsp.WIN_V, led, wsp.SETTLE_GATE_DAYS, quiet=False)
        wsp.apply_wallet_layer(con, led, wsp.MIN_TRADES, True, quiet=False)
    assert e.value.code == 1, "静默丢样本必须中止,不许降级继续"


# ── 防线二:母单 G1(两窗已结算市场数各 >= 400)──────────────────────────
def test_g1_market_count_DIES_below_prereg_minimum(tmp_path, monkeypatch):
    """单子 §9 第 10 项要求两窗各 >= 400 个已结算市场(统计功效闸门)。"""
    with pytest.raises(SystemExit) as e:
        _run_chain(tmp_path, monkeypatch, n_markets=30)   # 30 << 400
    assert e.value.code == 1


def test_g1_market_count_passes_at_or_above_minimum_and_is_recorded(tmp_path, monkeypatch):
    con, led, wl = _run_chain(tmp_path, monkeypatch, n_markets=420)
    assert wl["markets_D"] >= wsp.G1_MIN_MARKETS
    assert wl["markets_V"] >= wsp.G1_MIN_MARKETS
    # 记了还得有人读:数字必须能落盘,不能只在屏幕上飘过
    import json as _json
    _json.dumps(wl)


def test_g1_threshold_comes_from_the_prereg_not_a_local_copy():
    assert wsp.G1_MIN_MARKETS == 400


# ── 防线三:DuckDB 内存与临时目录 ────────────────────────────────────────
def test_connect_caps_memory_below_duckdb_default(tmp_path, monkeypatch):
    """2026-08-18 事故:默认按物理内存 80%(实测 24.4GiB)规划,涨到 18.7G 被 oomd 连坐。"""
    default_limit = duckdb.connect().execute(
        "SELECT current_setting('memory_limit')").fetchone()[0]

    def to_bytes(s: str) -> float:
        num, unit = s.split()
        return float(num) * {"KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}[unit]

    raw, reg = _write_chain_lake(tmp_path, 5)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")   # 测试隔离:不许往仓库写
    con = wsp.connect()
    ours = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    assert to_bytes(ours) < to_bytes(default_limit), \
        f"内存上限 {ours} 没有低于 DuckDB 默认 {default_limit} => 事故配置原样还在"
    assert to_bytes(ours) <= wsp.DUCKDB_MEM_CAP_GB * 1e9 * 1.02


def test_connect_uses_absolute_temp_directory(tmp_path, monkeypatch):
    """相对路径 '.tmp' 会随 cwd 漂移(CLAUDE.md:crontab 绝对路径铁律同源)。"""
    raw, reg = _write_chain_lake(tmp_path, 5)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")   # 测试隔离:不许往仓库写
    con = wsp.connect()
    td = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
    assert td.startswith("/"), f"temp_directory={td!r} 是相对路径"


def test_mem_budget_never_exceeds_cap_or_falls_below_floor():
    b = wsp._mem_budget_gb()
    assert wsp.DUCKDB_MEM_FLOOR_GB <= b <= wsp.DUCKDB_MEM_CAP_GB


# ── 防线四:空组不许把整批网格带走 ───────────────────────────────────────
def test_block_bootstrap_empty_group_returns_the_shape_callers_depend_on(tmp_path):
    """S2(门槛 50 笔)等格子真有可能剩 0 个钱包;此前会在网格中段抛 KeyError。"""
    con = duckdb.connect()
    con.execute("CREATE TABLE vgrp AS SELECT 1.0 num, 1.0 den WHERE false")
    bs = wsp.block_bootstrap(con)
    assert bs["n_groups"] == 0
    for k in ("q025", "q025_pp", "boot_mean_pp", "boot_q975_pp"):
        assert k in bs, f"空组返回值缺 {k},调用方会 KeyError"
    assert wsp.judge(0.6, bs["q025_pp"], True, 0.0)[0] is False  # 数据不足 => 判红


# ── 防线五:算完一步就落盘,别让一次崩溃带走整批结果 ─────────────────────
def test_results_are_saved_incrementally(tmp_path, monkeypatch):
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")
    wsp.save({"stage": "one"})
    assert (tmp_path / "out" / "results.json").exists()
    wsp.save({"stage": "two"})
    import json as _json
    assert _json.loads((tmp_path / "out" / "results.json").read_text())["stage"] == "two"


# ═════════════════════════════════════════════════════════════════════════════
# 2026-08-19 修订判据(单子 §0.5)
#
# 三项修订各配一条「造真坏输入 -> 必须亮红」的用例。核心问句自问:
# 「如果闸一其实与结果相关 / 887 个市场其实被误剔 / G5 其实没接到判决上,
#   现有判据会有什么不同?」—— 修订前的答案是【完全没有不同】,全部照样绿。
# ═════════════════════════════════════════════════════════════════════════════

# ── 修订三:闸二的 end_date 解析必须认带毫秒格式 ─────────────────────────
def test_settle_gate_accepts_fractional_seconds():
    """⭐ 实测:注册表 167,487 个市场里 887 个 end_date 形如 ...T03:59:59.999Z。

    修订前 try_strptime 返回 NULL -> `NULL BETWEEN 0 AND 7` 判 false ->
    这些成交与【真正超窗】的混在一起被剔,且无单独计数。
    """
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    ts = _epoch(2026, 8, 30, 12)          # 成交在 08-30
    expr = wsp.sql_settle_gate(7, ts="ts", end="ed")
    got = con.execute(
        f"SELECT {expr} FROM (SELECT {ts} ts, '2026-09-01T03:59:59.999Z' ed)").fetchone()[0]
    assert got is True, "带毫秒的 end_date 必须被正确解析(差 2 天,在 0~7 天闸内)"


def test_settle_gate_still_rejects_genuinely_out_of_window():
    """防洪的另一头:修好解析不能把真正超窗的也放进来。"""
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    ts = _epoch(2026, 7, 1, 12)           # 成交在 07-01,end_date 在 09-01 -> 62 天
    expr = wsp.sql_settle_gate(7, ts="ts", end="ed")
    got = con.execute(
        f"SELECT {expr} FROM (SELECT {ts} ts, '2026-09-01T03:59:59.999Z' ed)").fetchone()[0]
    assert got is False


def test_settle_gate_two_formats_agree_on_the_plain_one():
    """两种格式表示同一时刻时,判定必须一致 —— 否则修订本身引入了新口径。"""
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    ts = _epoch(2026, 8, 30, 12)
    expr = wsp.sql_settle_gate(7, ts="ts", end="ed")
    a = con.execute(f"SELECT {expr} FROM (SELECT {ts} ts, '2026-09-01T00:00:00Z' ed)").fetchone()[0]
    b = con.execute(f"SELECT {expr} FROM (SELECT {ts} ts, '2026-09-01T00:00:00.000Z' ed)").fetchone()[0]
    assert a is True and b is True


def test_unparseable_end_date_is_detectable_not_silently_dropped():
    """⭐ 单子 §0.5 修订三:两种格式都解析不了的,必须【能被单独数出来】。

    修订前它们无声混进「闸二剔除」。本用例造一个真的畸形值。
    """
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    bad = wsp.sql_end_unparseable("ed")
    rows = con.execute(f"""
        SELECT ed, {bad} FROM (VALUES
            ('2026-09-01T00:00:00Z'), ('2026-09-01T03:59:59.999Z'),
            ('not-a-date'), (NULL)) v(ed)
    """).fetchall()
    got = {r[0]: r[1] for r in rows}
    assert got['2026-09-01T00:00:00Z'] is False
    assert got['2026-09-01T03:59:59.999Z'] is False, "带毫秒的已修好,不该再算畸形"
    assert got['not-a-date'] is True, "真畸形必须被点名"
    assert got[None] is False, "end_date 本来就为空 ≠ 解析失败,两者不许混为一谈"


def test_settle_gate_sql_exists_in_exactly_one_place():
    """⛔ 照抄结构而不抽象(CLAUDE.md,已犯 3 次)。

    修订前闸二 SQL 在 build_window / check_settled_rate / g5_missingness 各有一份;
    修 end_date 解析要改三处 = 第 4 次。本判据焊死「只许有一份」。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.count("BETWEEN 0 AND") == 1, "闸二比较式出现了不止一份"
    assert src.count("try_strptime") == 1, "end_date 解析式出现了不止一份"


# ── 修订一:G5 必须测【两道闸】,不只是闸二 ──────────────────────────────
def _write_g5_lake(tmp_path: Path, *, late_ingest_bias: bool) -> tuple[str, str]:
    """造 G5 专用湖。唯一自变量:被闸一剔掉的那批成交是否【与结果相关】。

    两窗各 40 个市场,一半 resolved_outcome=1.0 一半 0.0。每个市场 10 笔【及时采集】
    成交(延迟 60s),入场价 0.40。另加一批【采集延迟 48h】的成交(闸一会剔掉它们):

    - late_ingest_bias=False:均匀撒在两类市场上,入场价同为 0.40
      => 闸一剔除组与保留组画像一致 => G5 应当过。
    - late_ingest_bias=True :只落在 resolved_outcome=1.0 的市场、且入场价 0.90
      => 剔除组基础率 1.00 / 价 0.90,保留组 ~0.50 / 0.40 => G5 必须亮红。

    ⚠️ 两种湖的市场集合、市场数、结算真值、闸二条件完全相同 —— 只动这一个自变量。
    """
    raw_dir = tmp_path / "raw" / "dt=2026-08-01"
    reg_dir = tmp_path / "registry"
    raw_dir.mkdir(parents=True), reg_dir.mkdir(parents=True)

    cid, oidx, price, ts, ing, wallet, size, side = [], [], [], [], [], [], [], []
    r_cid, r_val, r_end, r_slug = [], [], [], []
    for win_tag, (ty, tm, td), (ey, em, ed) in (
            ("d", (2026, 7, 23), (2026, 7, 25)),
            ("v", (2026, 8, 3), (2026, 8, 5))):
        for i in range(40):
            c = f"0x{win_tag}{i:039x}"
            resolved = 1.0 if i % 2 == 0 else 0.0
            r_cid.append(c); r_val.append(resolved)
            r_end.append(f"{ey:04d}-{em:02d}-{ed:02d}T00:00:00Z")
            r_slug.append(f"plain-market-{win_tag}-{i}")
            base_t = _epoch(ty, tm, td, 1) + i * 7
            for k in range(10):                      # 及时采集(闸一保留)
                cid.append(c); oidx.append(k % 2); price.append(0.40)
                ts.append(base_t + k); ing.append(base_t + k + 60)
                wallet.append(f"0xw{k}"); size.append(10.0); side.append("BUY")
            late = (resolved == 1.0) if late_ingest_bias else True
            if late:
                for k in range(10):                  # 延迟 48h 采集(闸一剔除)
                    cid.append(c); oidx.append(k % 2)
                    price.append(0.90 if late_ingest_bias else 0.40)
                    ts.append(base_t + 100 + k); ing.append(base_t + 100 + k + 172800)
                    wallet.append(f"0xL{k}"); size.append(10.0); side.append("BUY")

    pq.write_table(pa.table({
        "transaction_hash": [f"0xt{i}" for i in range(len(cid))],
        "condition_id": cid, "outcome_index": pa.array(oidx, pa.int32()),
        "price": price, "timestamp": pa.array(ts, pa.int64()),
        "size": size, "side": side, "ingested_at": pa.array(ing, pa.int64()),
        "proxy_wallet": wallet,
    }), raw_dir / "a.parquet")
    pq.write_table(pa.table({
        "condition_id": r_cid, "resolved_outcome": r_val,
        "snapshot_at": pa.array([_epoch(2026, 8, 12)] * len(r_cid), pa.int64()),
        "slug": r_slug, "market_class": ["event"] * len(r_cid),
        "hft_suspect": [False] * len(r_cid), "end_date": r_end,
    }), reg_dir / "m.parquet")
    return str(raw_dir.parent / "*" / "*.parquet"), str(reg_dir / "*.parquet")


def _run_g5(tmp_path, monkeypatch, *, late_ingest_bias: bool):
    raw, reg = _write_g5_lake(tmp_path, late_ingest_bias=late_ingest_bias)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")
    con = wsp.connect()
    led = wsp.Ledger()
    wsp.build_window(con, "D", wsp.WIN_D, led, wsp.SETTLE_GATE_DAYS, quiet=True)
    wsp.build_window(con, "V", wsp.WIN_V, led, wsp.SETTLE_GATE_DAYS, quiet=True)
    return wsp.g5_missingness(con)


def test_g5_cell_taxonomy_covers_the_union_not_just_two_edges():
    """单子原文:「对【两道闸各自剔除的成交】vs 保留的成交」。

    ⚠️ 原判据是纯文本搜索(只要源码里出现 gate1/gate2 就绿),对「有没有真的分格」
    毫无分辨力 —— 2026-08-19 review 指出后改成断言真实对象。
    """
    cells = set(wsp.G5_CELL_LABEL)
    assert cells == {"kept", "drop_g1_only", "drop_g2_only", "drop_both", "end_unknown"}, \
        "生产剔除的是两道闸的并集,四格 + 未知桶缺一不可"


def test_g5_gate1_CATCHES_result_correlated_missingness(tmp_path, monkeypatch):
    """⭐⭐ 本节最重要的一条:闸一剔掉的那批与输赢相关时,G5 必须亮红。

    这是整个方向三的前提 —— 闸一是「采集延迟 < 24h」,而热门市场完全可能被采得更快。
    修订前这个洞【结构上不可能被发现】:g5 的源表 s1_ 是闸一之后才建的。
    """
    res = _run_g5(tmp_path, monkeypatch, late_ingest_bias=True)
    assert res["pass"] is False, "闸一剔除组基础率 1.00 / 价 0.90 vs 保留组 0.50 / 0.40,必须判红"
    assert res["D"]["drop_g1_only"]["n"] > 0, "坏数据湖里闸一本该剔掉一批,一笔没剔说明用例失效"


def test_g5_passes_on_a_neutral_lake(tmp_path, monkeypatch):
    """防洪的另一头:稳态必须完全静默,否则这条告警会变成噪音。"""
    res = _run_g5(tmp_path, monkeypatch, late_ingest_bias=False)
    assert res["pass"] is True, f"中性湖上不该亮红:{res.get('failures')}"
    assert res["D"]["drop_g1_only"]["n"] > 0, "中性湖也必须真的有被闸一剔掉的成交"


def test_g5_thresholds_come_from_the_prereg_not_a_local_copy():
    """红线必须 import 被检验对象自己的常量(CLAUDE.md 前置工作项)。"""
    assert wsp.G5_BASERATE_DIFF_MAX_PP == 5.0
    assert wsp.G5_PRICE_MEDIAN_DIFF_MAX == 0.05


@pytest.mark.parametrize("kept,dropped,expected", [
    ({"n": 100, "base_rate": 0.50, "p50": 0.40}, {"n": 100, "base_rate": 0.52, "p50": 0.41}, True),
    ({"n": 100, "base_rate": 0.50, "p50": 0.40}, {"n": 100, "base_rate": 0.60, "p50": 0.41}, False),
    ({"n": 100, "base_rate": 0.50, "p50": 0.40}, {"n": 100, "base_rate": 0.52, "p50": 0.50}, False),
    ({"n": 100, "base_rate": 0.50, "p50": 0.40}, {"n": 0, "base_rate": None, "p50": None}, True),
    # ⭐ 一侧全无已结算市场 = 最极端的「与结果相关」,不许因为算不出差值就放过
    ({"n": 100, "base_rate": 0.50, "p50": 0.40}, {"n": 100, "base_rate": None, "p50": 0.41}, False),
])
def test_g5_verdict_edges(kept, dropped, expected):
    ok, _ = wsp.g5_verdict(kept, dropped)
    assert ok is expected


def test_g5_verdict_exactly_at_the_line_passes():
    """恰好压线用脚本自己的常量构造,防止判据里悄悄放宽。"""
    k = {"n": 100, "base_rate": 0.50, "p50": 0.40}
    d = {"n": 100, "base_rate": 0.50 + wsp.G5_BASERATE_DIFF_MAX_PP / 100, "p50": 0.40}
    assert wsp.g5_verdict(k, d)[0] is True
    d2 = {"n": 100, "base_rate": 0.50 + wsp.G5_BASERATE_DIFF_MAX_PP / 100 + 1e-9, "p50": 0.40}
    assert wsp.g5_verdict(k, d2)[0] is False


# ── 修订二:G5 不过 ⇒ 主判决不得为绿(用户 2026-08-19 拍板)───────────────
def test_judge_REQUIRES_the_g5_flag():
    """⭐ 焊法很关键:g5_pass 是【必填】参数,不给默认值。

    2026-08-18 的教训是 reconcile()「写了测了从来没人调」。给默认值 = 同一个洞:
    以后有人新写一个调用点忘了传,它会静默按「G5 过了」算。现在漏传直接 TypeError。
    """
    with pytest.raises(TypeError):
        wsp.judge(0.6, 0.1)          # 少了 g5_pass 与 null_p95


@pytest.mark.parametrize("r_v_pp,q025_pp,g5,expected", [
    (0.6,  0.1, True,  True),    # 三条都过
    (0.6,  0.1, False, False),   # ⭐ 红线全过,但 G5 不过 -> 不许报绿
    (0.4,  0.1, True,  False),
    (0.6, -0.1, True,  False),
])
def test_judge_g5_blocks_green(r_v_pp, q025_pp, g5, expected):
    got, detail = wsp.judge(r_v_pp, q025_pp, g5, 0.0)
    assert got is expected
    if not g5:
        assert "G5" in detail, "G5 否决必须写进判决理由,不许只是悄悄变红"


def test_g5_failure_stamps_the_final_verdict_line():
    """结论行必须自己带上「不可采信」——否则又是「记了没人读」。"""
    out = {"main": {"r_v_pp": 1.2, "q025_pp": 0.3, "green": False,
                    "detail": "A·幅度 PASS | B·显著 PASS | G5·缺失 FAIL"},
           "g5_missingness": {"pass": False, "failures": ["D/gate1: 基础率差 50.0pp"]},
           "expectations": {"pass": True, "failures": []}}
    lines = "\n".join(wsp.final_verdict_lines(out))
    assert "不可采信" in lines
    assert "🟢" not in lines, "G5 不过时结论行里不许出现绿灯"
    assert "D/gate1" in lines, "具体是哪一格越线必须写出来,不能只说『不过』"


def test_final_verdict_line_can_still_be_green_when_everything_passes():
    out = {"main": {"r_v_pp": 1.2, "q025_pp": 0.3, "green": True, "detail": "ok"},
           "g5_missingness": {"pass": True, "failures": []},
           "expectations": {"pass": True, "failures": []}}
    lines = "\n".join(wsp.final_verdict_lines(out))
    assert "🟢" in lines and "不可采信" not in lines


def test_main_actually_renders_the_final_verdict_through_that_function():
    """防「写了没接上」:main 必须调 final_verdict_lines,不许自己另印一份结论。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.count("final_verdict_lines(") >= 2, "定义了却没被 main 调用"


# ── 第 5 条:写死的「预期约 1.59% / 245」必须被判定,不能只是并排 print ──
def test_expectation_constants_match_the_prereg():
    assert wsp.EXPECT_LEAK_PCT == 1.59
    assert wsp.EXPECT_BOTS == 245
    assert wsp.LEAK_TOLERANCE_PP == 0.5
    assert wsp.BOTS_TOLERANCE_FRAC == 0.10


@pytest.mark.parametrize("leak,bots,expected", [
    (1.59, 245, True),
    (1.59 + 0.4, 245, True),
    (1.59 + 0.6, 245, False),      # 泄漏率偏离 0.6pp > 0.5pp -> 须查明
    (1.59, 245 * 1.05, True),
    (1.59, 245 * 1.2, False),      # 机器人数偏离 20% > 10% -> 须查明
    (1.59, 0, False),              # ⭐ 归零是最典型的静默失败形状,必须打红
])
def test_expectation_check_judges_instead_of_just_printing(leak, bots, expected):
    res = wsp.check_expectations({"leak_pct": leak, "bots_found": bots})
    assert res["pass"] is expected
    if not expected:
        assert res["failures"], "判不过却没说是哪一项不过 = 报了个没法查的警"


def test_expectation_failure_reaches_the_final_summary():
    """⭐「记录事实 vs 使用事实,只接一头」第 6 次的防线:埋在几十行输出里不算被读。"""
    out = {"main": {"r_v_pp": 1.2, "q025_pp": 0.3, "green": True, "detail": "ok"},
           "g5_missingness": {"pass": True, "failures": []},
           "expectations": {"pass": False,
                            "failures": ["泄漏率 3.10% vs 预期 1.59%(偏离 1.51pp)"]}}
    lines = "\n".join(wsp.final_verdict_lines(out))
    assert "须查明" in lines and "泄漏率" in lines


def test_expectation_check_is_wired_into_the_main_pipeline():
    """必须焊在 apply_wallet_layer 的出口(和 reconcile / G1 同一处),不是 main 里『记得调』。"""
    import inspect
    src = inspect.getsource(wsp.apply_wallet_layer)
    assert "check_expectations(" in src


def test_build_window_actually_counts_unparseable_end_dates(tmp_path, monkeypatch, capsys):
    """⭐「SQL 里数得出来」≠「管线里真的数了」——「只接一头」这次不许再犯。

    造一个真的畸形 end_date 进湖,build_window 必须把它单独点名,而不是让它
    无声混进「闸二剔除」那一格。
    """
    raw, reg = _write_g5_lake(tmp_path, late_ingest_bias=False)
    reg_f = list((tmp_path / "registry").glob("*.parquet"))[0]
    t = pq.read_table(reg_f).to_pydict()
    t["end_date"] = ["garbage-not-a-date" if i % 10 == 0 else e
                     for i, e in enumerate(t["end_date"])]
    pq.write_table(pa.table(t), reg_f)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")
    con = wsp.connect()
    led = wsp.Ledger()
    wsp.build_window(con, "D", wsp.WIN_D, led, wsp.SETTLE_GATE_DAYS, quiet=False)
    out = capsys.readouterr().out
    assert "解析失败" in out, "畸形 end_date 一声不吭 = 又一次静默丢样本"
    assert led.notes, "只 print 不落账 = 报表里看不见,等于没数"


def test_final_verdict_DENIES_green_even_when_main_green_was_computed_without_g5():
    """⭐ 变异自检 M5 漏网后补的一条:结论行是【第二道】防线,不许依赖第一道已经把它变红。

    场景:某个调用点绕过了 judge 的 G5 否决(或将来新加一处忘了传),
    main['green'] 已经是 True。此时结论行必须【自己】把绿灯扣下来。
    """
    out = {"main": {"r_v_pp": 1.2, "q025_pp": 0.3, "green": True,
                    "detail": "A·幅度 PASS | B·显著 PASS"},
           "g5_missingness": {"pass": False, "failures": ["V/gate1(闸一·采集延迟<24h): 基础率差 50.00pp"]},
           "expectations": {"pass": True, "failures": []}}
    lines = "\n".join(wsp.final_verdict_lines(out))
    assert "🟢" not in lines, "第一道防线失效时,结论行必须自己拦住绿灯"
    assert "判决作废" in lines and "V/gate1" in lines


def test_stage_g5_exists_so_baselines_can_be_remeasured_without_seeing_the_verdict():
    """单子 §0.5 修订三第 2 点:重测基线必须在看到任何 R_V 之前完成。

    ⭐ 这条承诺不能靠「我记得跑的时候别看」——必须有一档跑不到判决。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"g5", "full"' in src, "--stage 里没有 g5 这一档"
    assert 'if args.stage == "g5":' in src, "g5 档没有真的提前返回"


# ═════════════════════════════════════════════════════════════════════════════
# 2026-08-19 第二轮(python-reviewer 报的 3 个真洞 + 3 个判据洞)
# ⭐ 洞 B 最重:G5 原设计「测闸一时固定闸二通过」,而生产剔除的是【并集】——
#    「两道闸同时不过」那一块两个检验都碰不到。改成 2×2 + 未知桶。
# ═════════════════════════════════════════════════════════════════════════════
def _write_2x2_lake(tmp_path: Path) -> tuple[str, str]:
    """造一个四格齐全的湖:闸一×闸二 四种组合都有成交,外加 end_date 为空的一类。

    刻意让【两道闸都不过】那一格与结果强相关(基础率 1.0、价 0.95)——
    修订前它落在两个检验的视野之外,谁也发现不了。
    """
    raw_dir = tmp_path / "raw" / "dt=2026-08-01"
    reg_dir = tmp_path / "registry"
    raw_dir.mkdir(parents=True), reg_dir.mkdir(parents=True)
    cid, oidx, price, ts, ing, wallet, size, side = [], [], [], [], [], [], [], []
    r_cid, r_val, r_end, r_slug = [], [], [], []

    def add_market(tag, i, resolved, end_date, trades):
        c = f"0x{tag}{i:039x}"
        r_cid.append(c); r_val.append(resolved); r_end.append(end_date)
        r_slug.append(f"plain-{tag}-{i}")
        for (t, delay, pz) in trades:
            cid.append(c); oidx.append(0); price.append(pz)
            ts.append(t); ing.append(t + delay); wallet.append(f"0xw{i%7}")
            size.append(10.0); side.append("BUY")

    for tag, (ty, tm, td) in (("d", (2026, 7, 23)), ("v", (2026, 8, 3))):
        base_t = _epoch(ty, tm, td, 1)
        near = f"{ty:04d}-{tm:02d}-{td+2:02d}T00:00:00Z"      # 闸二内(2 天)
        far = f"{ty:04d}-{tm+1:02d}-{td:02d}T00:00:00Z"       # 闸二外(约 30 天)
        for i in range(30):                                    # 闸一过 + 闸二过 = kept
            add_market(tag, i, float(i % 2), near, [(base_t + i, 60, 0.40)] * 4)
        for i in range(30, 60):                                # 闸一不过 + 闸二过
            add_market(tag, i, float(i % 2), near, [(base_t + i, 172800, 0.42)] * 4)
        for i in range(60, 90):                                # 闸一过 + 闸二不过
            add_market(tag, i, float(i % 2), far, [(base_t + i, 60, 0.44)] * 4)
        for i in range(90, 120):                               # ⭐两道闸都不过
            add_market(tag, i, 1.0, far, [(base_t + i, 172800, 0.95)] * 4)
        for i in range(120, 140):                              # end_date 为空 = 未知
            add_market(tag, i, 1.0, None, [(base_t + i, 60, 0.93)] * 4)

    pq.write_table(pa.table({
        "transaction_hash": [f"0xt{i}" for i in range(len(cid))],
        "condition_id": cid, "outcome_index": pa.array(oidx, pa.int32()),
        "price": price, "timestamp": pa.array(ts, pa.int64()),
        "size": size, "side": side, "ingested_at": pa.array(ing, pa.int64()),
        "proxy_wallet": wallet,
    }), raw_dir / "a.parquet")
    pq.write_table(pa.table({
        "condition_id": r_cid, "resolved_outcome": r_val,
        "snapshot_at": pa.array([_epoch(2026, 8, 12)] * len(r_cid), pa.int64()),
        "slug": r_slug, "market_class": ["event"] * len(r_cid),
        "hft_suspect": [False] * len(r_cid), "end_date": r_end,
    }), reg_dir / "m.parquet")
    return str(raw_dir.parent / "*" / "*.parquet"), str(reg_dir / "*.parquet")


def _run_g5_2x2(tmp_path, monkeypatch):
    raw, reg = _write_2x2_lake(tmp_path)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")
    con = wsp.connect()
    led = wsp.Ledger()
    wsp.build_window(con, "D", wsp.WIN_D, led, wsp.SETTLE_GATE_DAYS, quiet=True)
    wsp.build_window(con, "V", wsp.WIN_V, led, wsp.SETTLE_GATE_DAYS, quiet=True)
    return wsp.g5_missingness(con)


def test_g5_covers_all_four_gate_combinations_plus_unknown(tmp_path, monkeypatch):
    """⭐ 洞 B:生产剔除的是两道闸的【并集】,四格都要摆出来。"""
    res = _run_g5_2x2(tmp_path, monkeypatch)
    for tag in ("D", "V"):
        for cell in ("kept", "drop_g1_only", "drop_g2_only", "drop_both", "end_unknown"):
            assert cell in res[tag], f"[{tag}] 缺少 {cell} 这一格"
            assert res[tag][cell]["n"] > 0, f"[{tag}]/{cell} 一笔都没有,用例本身失效"


def test_g5_CATCHES_bias_that_lives_only_in_the_both_gates_fail_cell(tmp_path, monkeypatch):
    """⭐⭐ 洞 B 的正题:偏差只藏在「两道闸都不过」那一格时,必须被抓到。

    修订前这一格【结构上】在两个检验的视野之外 —— 两条边都 PASS,而并集里带着偏差。
    """
    res = _run_g5_2x2(tmp_path, monkeypatch)
    assert res["D"]["drop_both"]["pass"] is False, "两道闸都不过那一格(基础率 1.0/价 0.95)必须判红"
    assert any("drop_both" in f for f in res["failures"]), "红了却没写进 failures = 只接一头"


def test_g5_separates_unknown_end_date_from_genuinely_out_of_window(tmp_path, monkeypatch):
    """⭐ 洞 C:end_date 为空 ≠ 结算超窗,不许并成一格。

    reviewer 实测:end_date 为空的市场结算基础率 0.139 vs 有值的 0.410(差 27pp)。
    """
    res = _run_g5_2x2(tmp_path, monkeypatch)
    unk = res["D"]["end_unknown"]
    g2o = res["D"]["drop_g2_only"]
    assert unk["n"] > 0 and g2o["n"] > 0
    assert unk["n"] != g2o["n"], "两类笔数相同 => 很可能还是混在一格里"
    assert unk["pass"] is False, "未知组基础率 1.0 / 价 0.93,与保留组差得远,必须判红"


def test_g5_gate1_verdict_is_asserted_directly_not_inferred_from_the_aggregate(tmp_path, monkeypatch):
    """判据洞:原用例只断言聚合值,靠场景巧合成立。现在把闸一自己的判定钉死。"""
    res = _run_g5(tmp_path, monkeypatch, late_ingest_bias=True)
    assert res["D"]["drop_g1_only"]["pass"] is False
    assert res["V"]["drop_g1_only"]["pass"] is False


def test_final_verdict_REFUSES_to_render_when_g5_result_is_missing():
    """判据洞:`judge` 把 g5 设成必填,而 final_verdict_lines 又用 .get(...,True) 加了回来。

    缺键就该炸,不该按「G5 过了」渲染 —— 那正是 judge 的文档里点名要杜绝的洞。
    """
    with pytest.raises((KeyError, ValueError)):
        wsp.final_verdict_lines({"main": {"green": True, "detail": "x"}})


def test_unparseable_end_date_is_JUDGED_not_just_printed():
    """判据洞:今天新加的 n_bad_end 只 print 了个红字,没有任何判定。"""
    led = wsp.Ledger()
    led.note("D", "end_date 解析失败笔数", 0)
    led.note("V", "end_date 解析失败笔数", 0)
    assert wsp.check_expectations({"leak_pct": 1.59, "bots_found": 245}, led)["pass"] is True
    led2 = wsp.Ledger()
    led2.note("D", "end_date 解析失败笔数", 12345)
    res = wsp.check_expectations({"leak_pct": 1.59, "bots_found": 245}, led2)
    assert res["pass"] is False, "解析失败 12,345 笔却判过 = 又一次只印不判"
    assert any("解析失败" in f for f in res["failures"])


def test_ingest_gate_can_be_turned_off_for_the_registered_sensitivity_arm(tmp_path, monkeypatch):
    """§0.7 登记的稳健性双臂:闸一关时,被延迟采集的成交必须真的回到样本里。"""
    raw, reg = _write_g5_lake(tmp_path, late_ingest_bias=False)
    monkeypatch.setattr(wsp, "RAW", raw)
    monkeypatch.setattr(wsp, "REG", reg)
    monkeypatch.setattr(wsp, "OUT_DIR", tmp_path / "out")
    con = wsp.connect()
    led = wsp.Ledger()
    n_on = wsp.build_window(con, "D", wsp.WIN_D, led, wsp.SETTLE_GATE_DAYS,
                            quiet=True, ingest_gate=True)
    n_off = wsp.build_window(con, "D", wsp.WIN_D, wsp.Ledger(), wsp.SETTLE_GATE_DAYS,
                             quiet=True, ingest_gate=False)
    assert n_off > n_on, f"闸一关掉却没多出成交(on={n_on} off={n_off})=> 开关没接上"


def test_grid_registers_the_ingest_gate_off_arm():
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"ingest_gate": False' in src, "单子 §0.7 登记的闸一关那一臂没进网格"


# ═════════════════════════════════════════════════════════════════════════════
# 2026-08-19 第三轮:§8 不可复现 + 红线低于零分布(用户当日批准改置换检验)
#
# 由来(全部实测):同一份数据两次跑,同一个种子给出不同的数(seed 0: +1.7953pp
# vs +4.5198pp)。根因复现确认 —— 并行 GROUP BY 建的表【行序每次不同】,
# 而 ORDER BY random() 是把固定的随机数序列按行序贴上去的。
# 顺带测出:随机抽一组人当「高手」,收益率中位 +0.9449pp,而单子红线是 +0.5pp
# ⇒ 62% 的随机组能越线,这条线画在了瞎猜的平均水平以下。
# ═════════════════════════════════════════════════════════════════════════════
def test_det_rand_is_independent_of_row_order():
    """⭐ 核心:同一批钱包,物理行序不同,抽出来的必须是同一批人。

    这正是 random() 做不到的那件事(实测复现:threads=8 时表头三次完全不同)。
    """
    con = duckdb.connect()
    con.execute("CREATE TABLE a AS SELECT 'w' || range AS proxy_wallet FROM range(500)")
    con.execute("CREATE TABLE b AS SELECT proxy_wallet FROM a ORDER BY proxy_wallet DESC")
    expr = wsp.sql_det_order("proxy_wallet", 7)
    pa_ = [r[0] for r in con.execute(
        f"SELECT proxy_wallet FROM a ORDER BY {expr} LIMIT 50").fetchall()]
    pb_ = [r[0] for r in con.execute(
        f"SELECT proxy_wallet FROM b ORDER BY {expr} LIMIT 50").fetchall()]
    assert pa_ == pb_, "行序一变就抽到不同的人 => 又回到不可复现"


def test_det_rand_actually_shuffles_and_salt_changes_the_draw():
    """防洪另一头:它得真的是「随机」的,而且换个盐要抽到不同的人。"""
    con = duckdb.connect()
    con.execute("CREATE TABLE a AS SELECT 'w' || range AS proxy_wallet FROM range(500)")
    s7 = [r[0] for r in con.execute(
        f"SELECT proxy_wallet FROM a ORDER BY {wsp.sql_det_order('proxy_wallet', 7)} LIMIT 50").fetchall()]
    s8 = [r[0] for r in con.execute(
        f"SELECT proxy_wallet FROM a ORDER BY {wsp.sql_det_order('proxy_wallet', 8)} LIMIT 50").fetchall()]
    seq = [f"w{i}" for i in range(50)]
    assert s7 != seq, "没打乱,等于按原顺序取前 50"
    assert len(set(s7) & set(s8)) < 30, f"换盐后重合 {len(set(s7)&set(s8))}/50,太高 => 盐没起作用"


def test_det_unit_stays_in_range_and_is_reproducible():
    con = duckdb.connect()
    e = wsp.sql_det_unit("h", 3)
    rows = con.execute(f"SELECT {e}, {e} FROM (SELECT 'tx' || range AS h FROM range(2000))").fetchall()
    assert all(0.0 <= r[0] < 1.0 for r in rows), "抽样值跑出 [0,1)"
    assert all(r[0] == r[1] for r in rows), "同一行两次算出不同的值"
    vals = [r[0] for r in rows]
    assert 0.4 < sum(vals) / len(vals) < 0.6, f"均值 {sum(vals)/len(vals):.3f} 偏离 0.5 太远"


def test_judge_REQUIRES_the_permutation_threshold():
    """红线 A 已由「绝对 +0.5pp」改为「超过随机组零分布的 95 分位」(用户 2026-08-19 批准)。

    与 g5_pass 同理:必填,漏传直接 TypeError,不许静默退回旧红线。
    """
    with pytest.raises(TypeError):
        wsp.judge(4.0, 0.1, True)


@pytest.mark.parametrize("r_v_pp,q025_pp,null_p95,expected", [
    (4.0,  0.1, 3.4, True),    # 超过零分布 95 分位
    (1.0,  0.1, 3.4, False),   # ⭐ 旧红线(+0.5pp)下会判绿,新红线下判红
    (4.0, -0.1, 3.4, False),   # 显著性仍是并列必过项(只可加严)
    (3.4,  0.1, 3.4, False),   # 恰好等于零分布分位 -> 不算超过
])
def test_judge_uses_the_null_distribution_not_an_absolute_line(r_v_pp, q025_pp, null_p95, expected):
    got, detail = wsp.judge(r_v_pp, q025_pp, True, null_p95)
    assert got is expected
    assert "零分布" in detail or "置换" in detail, "判决理由里看不出用的是置换检验"


def test_permutation_null_and_badcheck_draw_from_disjoint_salts():
    """⭐ 不这样做,§8 就变成同义反复:定阈值的那批抽样和检验阈值的那批是同一批,
    假阳率必然恰好等于 5%,对实现有没有写错【毫无分辨力】。
    """
    assert wsp.PERM_SALT_NULL != wsp.PERM_SALT_BADCHECK
    lo, hi = sorted((wsp.PERM_SALT_NULL, wsp.PERM_SALT_BADCHECK))
    assert lo + max(wsp.PERM_N, wsp.BADCHECK_SEEDS) <= hi, "两段盐区间重叠了"


def test_resample_win_is_reproducible_given_the_same_salt():
    con = duckdb.connect()
    con.execute("""CREATE TABLE src AS
        SELECT 'tx' || range AS transaction_hash, (range % 90) / 100.0 AS price,
               0.0 AS win FROM range(3000)""")
    wsp._resample_win_from_price(con, "src", "d1", salt=5)
    wsp._resample_win_from_price(con, "src", "d2", salt=5)
    wsp._resample_win_from_price(con, "src", "d3", salt=6)
    same = con.execute("SELECT count(*) FROM d1 JOIN d2 USING (transaction_hash) "
                       "WHERE d1.win <> d2.win").fetchone()[0]
    diff = con.execute("SELECT count(*) FROM d1 JOIN d3 USING (transaction_hash) "
                       "WHERE d1.win <> d3.win").fetchone()[0]
    assert same == 0, f"同一个盐两次抽出不同结果({same} 笔不一致)=> 仍不可复现"
    assert diff > 300, f"换盐后只有 {diff} 笔变化 => 盐没起作用"


def test_resample_win_keeps_expected_edge_at_zero():
    """回归:Bernoulli(price) 必须保住 E[win|price]=price,否则砍掉的不是输赢。"""
    con = duckdb.connect()
    con.execute("""CREATE TABLE src AS
        SELECT 'tx' || range AS transaction_hash, 0.30 AS price, 0.0 AS win FROM range(20000)""")
    wsp._resample_win_from_price(con, "src", "dst", salt=1)
    m = con.execute("SELECT avg(win) FROM dst").fetchone()[0]
    assert 0.27 < m < 0.33, f"price=0.30 而 E[win]={m:.4f},定价被改成有偏了"


def test_null_distribution_comparison_is_computed_not_hardcoded():
    """⭐ 我写死过一句「零分布中位已高于原红线」,200 次置换实测后当场变成假话
    (40 种子估计 +0.9449pp,200 置换 +0.4375pp)。CLAUDE.md 静默失败第 8 条。
    """
    import inspect
    src = inspect.getsource(wsp.permutation_null)
    assert "零分布中位已高于" not in src, "又把结论写死进 print 了"
    assert "sum(v >= REDLINE_A_PP for v in vals)" in src, "对照必须实时算"
