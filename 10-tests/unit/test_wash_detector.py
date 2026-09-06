"""刷单市场检测器的验收判据。

⭐按 CLAUDE.md「先写判据再写代码」:本文件先于 wash_trading_detector.py 写成。

判据的设计原则(踩过的坑):
- 门槛不写死在这里,而是 **import 被检验对象自己的常量** —— 复制粘贴会让
  "我没放水"这句话不可复核(2026-07-15 教训)。
- 真值组的数值来自 2026-08-23 对全湖 2,680 万笔的实测,逐个市场列出,
  不是"造一个刚好能过的假样本"。
- ⭐每条断言都要能回答:「如果被检验的东西现在就是坏的,这条断言会红吗?」

🔴 **2026-08-23 review 抓到的教训(本文件自己犯的)**:
原版 `_stats()` 把**每一行**的 condition_id 都写死成同一个占位值,
于是它永远不等于 `KNOWN_WASH_MARKETS` 里的任何一个 ⇒ `known_seen` 恒为 0 ⇒
`positives_all_caught = (0 == 0)` **恒为真**。
实测(reviewer 做的变异):把 `scan_rows()` 里实现主红线的整段代码删掉,
**30 条判据一条都不红**。
⇒ 现在真值组带**真实 id**,并且补了一条**反向判据**(见
`test_verdict_goes_red_when_a_known_positive_is_missed`):
只有当"漏掉已知阳性 ⇒ 判决必须翻红"也被钉住,这条红线才算真的被验证。
"""
import json

import pytest

import wash_trading_detector as wd


# ---------- 实测真值(2026-08-23 全湖测得,详见 docs/DESIGN_WASH_DETECTOR_2026-08-23.md)----------
# 阳性组:用**与判别式无关**的轴挑出(单钱包占笔数 >= 95%),共 4 个。
# ⭐condition_id 用**真实值**,必须与 wd.KNOWN_WASH_MARKETS 对得上,
#   否则 scan_rows 的主红线逻辑压根不会被触发(见模块 docstring 的教训)。
MEASURED_WASH_MARKETS = [
    # (condition_id, 笔数, 钱包数, 单钱包笔数, 规模中位, 价格中位, 名义总额)
    ("0xefa17dee3af09f69f9ddf245b969aa4efbe7c71cdf06ee49d694408bc33e2ed2",
     1452051, 103, 1450600, 5.0, 0.002, 1452051 * 0.0248),
    ("0x5f03bb886142aaac823069e0a4c9b7e0b78dc161b32fd49247097f9907afa8cc",
     113669, 104, 113100, 5.0, 0.001, 113669 * 0.1748),
    ("0xc53955c1303b0ea1d676f99c52ea3b28961fb41d4f04c8cecd0b96d79c0d3bf5",
     15725, 81, 15505, 5.0, 0.999, 15725 * 5.717),
    ("0x6fddebc3d35bb590ab835d9096662e3ebcbd03f4f6af549367b9da78327b53bc",
     1986, 20, 1930, 0.09, 0.001, 1986 * 0.2792),
]

# 阴性对照组:笔数>=1万 且 参与钱包数>=1000 的正常大市场(实测 125 个中的代表样本)。
# ⭐这条轴(钱包数)与判别式的三个维度都不重合 —— 不构成循环论证。
MEASURED_NORMAL_MARKETS = [
    ("0xnormal01", 32458, 2477, 1396, 14.89, 0.987, 32458 * 156.072),
    ("0xnormal02", 30539, 7561, 4031, 19.79, 0.745, 30539 * 450.593),
    ("0xnormal03", 27377, 4114, 15441, 4.0, 0.436, 27377 * 50.962),   # 单钱包 56.4%,规模仅 4 股
    ("0xnormal04", 19076, 3063, 362, 13.2, 0.985, 19076 * 38.340),
    ("0xnormal05", 10209, 1200, 300, 11.27, 0.994, 10209 * 123.700),  # 昨天那版判别式会误伤它
    ("0xnormal06", 10204, 1150, 280, 13.07, 0.993, 10204 * 101.200),  # 同上
    ("0xnormal07", 11057, 1740, 8735, 5.0, 0.985, 11057 * 22.628),    # 占比 79% —— 断层**之下**
]

# 实测的占比断层:82.2% 之下连续,之上直接跳到 91.5%。门槛须落在空档内。
MEASURED_SHARE_GAP_LOW = 0.822
MEASURED_SHARE_GAP_HIGH = 0.915

# 实测盲区上界(2026-08-23,review 要求补测):笔数 <1,000 的市场占全湖 47.29% 的笔数,
# 但剔掉"1~10 笔"那档退化产物后,同形状的只有 23 个市场 / 3,677 笔 = 全湖 0.014%。
MEASURED_SUB_FLOOR_TRADE_SHARE = 0.00014


def _stats(cid, n_trades, n_wallets, top_wallet_trades, med_size, med_price, notional):
    return {
        "condition_id": cid,
        "n_trades": n_trades, "n_wallets": n_wallets,
        "top_wallet_trades": top_wallet_trades,
        "med_size": med_size, "med_price": med_price, "notional": notional,
    }


def _wash_rows():
    return [_stats(*r) for r in MEASURED_WASH_MARKETS]


def _normal_rows():
    return [_stats(*r) for r in MEASURED_NORMAL_MARKETS]


# ---------- 1. 两组真值 ----------

@pytest.mark.parametrize("row", MEASURED_WASH_MARKETS)
def test_known_wash_markets_are_all_caught(row):
    """红线 §6.1:4 个已知刷单市场必须**全部**命中,漏一个即方案作废。"""
    hit, reasons = wd.classify_market(_stats(*row))
    assert hit is True, f"漏掉了已知刷单市场 {row[0]}: {reasons}"


def test_truth_set_ids_actually_match_the_implementation():
    """⭐判据里的真值 id 必须真的等于实现里的那一组。

    这条钉住的正是 2026-08-23 那个假判据的根因:id 对不上 ⇒ 主红线逻辑永不触发,
    而所有断言看起来照样是绿的。
    """
    assert {r[0] for r in MEASURED_WASH_MARKETS} == set(wd.KNOWN_WASH_MARKETS)


@pytest.mark.parametrize("row", MEASURED_NORMAL_MARKETS)
def test_known_normal_markets_are_never_caught(row):
    """红线 §6.2:正常大市场命中数必须为 0。

    ⭐其中两个(价格中位 0.994 / 0.993)正是 2026-08-22 那版判别式的误伤对象,
    留在这里当**回归钉子**:任何把主判据换回"规模+价格"的改动都会让它们变红。
    """
    hit, reasons = wd.classify_market(_stats(*row))
    assert hit is False, f"误伤了正常市场 {row[0]}: {reasons}"


# ---------- 2. 门槛本身 ----------

def test_share_threshold_sits_inside_the_measured_gap():
    """门槛必须落在实测断层里,不许贴着边界切(贴边=拟合,断层内=结构)。"""
    assert MEASURED_SHARE_GAP_LOW < wd.MEASURED_WALLET_SHARE < MEASURED_SHARE_GAP_HIGH


def test_threshold_is_robust_to_small_perturbation():
    """上下浮动 3 个百分点结果不变 —— 否则说明它是拟合出来的,不是断层。"""
    original = wd.MEASURED_WALLET_SHARE
    try:
        for delta in (-0.03, +0.03):
            wd.MEASURED_WALLET_SHARE = original + delta
            got_w = {wd.classify_market(r)[0] for r in _wash_rows()}
            got_n = {wd.classify_market(r)[0] for r in _normal_rows()}
            assert got_w == {True}, f"门槛动 {delta} 就翻了 —— 是拟合不是断层"
            assert got_n == {False}, f"门槛动 {delta} 就开始误伤"
    finally:
        wd.MEASURED_WALLET_SHARE = original


def test_all_four_conditions_are_AND_not_OR():
    """四条必须是 AND。OR 会放大误伤 —— 用一个只满足单条的样本钉住。"""
    hit, _ = wd.classify_market(_stats("0xonly-price", 50000, 3000, 500, 12.0, 0.999, 50000 * 120.0))
    assert hit is False


def test_below_min_trades_never_flagged():
    """笔数下界:极小市场即使形状完全吻合也不判。

    ⚠️ 理由**不是**"影响 <0.01%"(那句话 2026-08-23 被 review 证伪,见实现里的更正),
    而是:占比在低笔数处**退化**(1 笔成交 ⇒ 占比按定义 100%)。
    """
    tiny = wd.MEASURED_MIN_TRADES - 1
    hit, _ = wd.classify_market(_stats("0xtiny", tiny, 3, tiny, 5.0, 0.001, tiny * 0.02))
    assert hit is False


def test_sub_floor_blind_spot_is_documented_with_a_measured_bound():
    """⭐门槛的盲区必须有**实测上界**,不许只写"影响很小"。

    2026-08-23 review 抓到:原注释拿单个市场的 <0.01% 当成了总量的界,
    而被挡在门外的其实是全湖 47.29% 的笔数。实测非退化的同形状盲区是 0.014%。

    结构检查:这两个实测数字必须写在代码里 ——
    守的是「门槛的盲区必须有实测上界留痕」这条结构不变量,不是在验行为。
    """
    src = (wd.MEASURED_MIN_TRADES.__doc__ or "") + (wd.__doc__ or "")
    import inspect
    src = inspect.getsource(wd)
    assert "47.29" in src, "盲区的真实规模(全湖 47.29% 笔数)必须写在代码里"
    assert "0.014" in src, "盲区的实测上界必须写在代码里"


# ---------- 3. 坏输入不许静默 ----------

@pytest.mark.parametrize("bad", [
    {"n_trades": 0, "n_wallets": 0, "top_wallet_trades": 0,
     "med_size": 1.0, "med_price": 0.5, "notional": 0.0},            # 除零
    {"n_trades": 5000, "n_wallets": 2, "top_wallet_trades": 5000,
     "med_size": None, "med_price": 0.001, "notional": 10.0},        # 缺值
    {"n_trades": 5000, "n_wallets": 2, "top_wallet_trades": 5000,
     "med_size": float("nan"), "med_price": 0.001, "notional": 10.0},  # NaN
])
def test_bad_rows_do_not_crash_and_are_not_silently_flagged(bad):
    """坏行既不许让整份体检崩掉,也不许被悄悄判成刷单(那是静默污染名单)。"""
    bad = {"condition_id": "0xdead", **bad}
    hit, reasons = wd.classify_market(bad)
    assert hit is False
    assert reasons, "坏行必须**出声**说明为什么没判 —— 静默丢样本是本项目的死因"


def test_duckdb_exception_families_are_all_covered():
    """⭐实测(2026-08-23)得出:DuckDB 对四类坏输入抛的是四个**不同**的类,
    但共同基类是 duckdb.Error。凭"应该是 IO 错误"想当然会漏掉三种。
    """
    import duckdb
    measured = [duckdb.InvalidInputException,   # 内容不是 parquet
                duckdb.IOException,             # 文件不存在 / glob 空
                duckdb.BinderException,         # 缺列
                duckdb.CatalogException,        # review 补充实测
                duckdb.OutOfMemoryException]
    for exc in measured:
        assert issubclass(exc, wd.LAKE_READ_ERRORS), f"{exc.__name__} 没被 except 覆盖"


def test_set_statement_is_parameterized_not_string_formatted():
    """⭐CRITICAL 回归钉子:`SET memory_limit` 不许用 f-string 拼。

    实测(2026-08-23 review)拼接会执行多条语句,而 DuckDB 的 SQL 能 COPY 到任意路径、
    ATTACH/INSTALL 扩展、read_csv 读任意文件 ⇒ 一次注入等于任意本地文件读写。

    结构检查:断言 SET 语句不是 f-string 且用了参数化 ——
    守的是「写类 SQL 一律参数化」这条结构不变量,不是拿文本当行为的替身。
    """
    import inspect
    src = inspect.getsource(wd.scan_lake)
    assert 'f"SET' not in src and "f'SET" not in src, "SET 语句又被拼成 f-string 了"
    assert 'con.execute("SET memory_limit=?"' in src


# ---------- 4. 对账 + 证伪红线 ----------

def test_audit_counts_reconcile():
    """请求数 vs 返回数对账:扫过的市场数 = 命中 + 未命中 + 坏行,一个不能少。"""
    rows = _wash_rows() + _normal_rows() + [
        {"condition_id": "0xbad", "n_trades": 0, "n_wallets": 0,
         "top_wallet_trades": 0, "med_size": None, "med_price": None, "notional": 0}]
    a = wd.scan_rows(rows)["audit"]
    assert a["scanned"] == len(rows)
    assert a["scanned"] == a["flagged"] + a["clean"] + a["bad_rows"]
    assert a["flagged"] == len(MEASURED_WASH_MARKETS)
    assert a["bad_rows"] == 1


def test_falsification_conditions_are_evaluated_not_just_documented():
    """§6 的证伪条件必须是**代码里会算的**,不是文档里写的。"""
    result = wd.scan_rows(_wash_rows() + _normal_rows())
    v = result["falsification"]
    assert set(v) >= {"positives_all_caught", "control_false_positives", "flagged_trade_share"}
    # ⭐这几行现在**真的**在验东西:id 对得上,所以 known_seen==4 而不是 0
    assert v["known_positives_seen"] == len(MEASURED_WASH_MARKETS)
    assert v["known_positives_caught"] == len(MEASURED_WASH_MARKETS)
    assert v["positives_all_caught"] is True
    assert v["control_false_positives"] == 0
    assert result["verdict"] == "PASS"
    # 样本只有十几个市场 ⇒ 占比红线**不该**参与判决,而且必须**说出来**它没参与。
    assert v["flagged_trade_share_evaluated"] is False
    assert v["flagged_trade_share_skip_reason"]


def test_verdict_goes_red_when_a_known_positive_is_missed():
    """⭐⭐ 反向判据:已知阳性没被抓到时,判决**必须**翻红。

    没有这一条,「主红线的代码整段删掉也没有判据变红」(2026-08-23 review 实测的情形)
    就会重演 —— 那时"绿"与"红线不存在"看起来完全一样。
    """
    cid = MEASURED_WASH_MARKETS[0][0]
    # 同一个已知刷单 id,但把统计数据换成正常市场的形状 ⇒ 应当"见到了却没抓到"
    disguised = _stats(cid, 50000, 3000, 500, 12.0, 0.5, 50000 * 120.0)
    result = wd.scan_rows([disguised] + _normal_rows())
    v = result["falsification"]
    assert v["known_positives_seen"] == 1
    assert v["known_positives_caught"] == 0
    assert v["positives_all_caught"] is False
    assert result["verdict"] == "FAIL"
    assert any("已知刷单市场漏掉" in b for b in result["breaches"])


def test_verdict_turns_red_when_a_false_positive_appears():
    """⭐先在**坏数据**上跑一遍,确认判据真的会红 —— 否则绿灯不可信。"""
    # 每笔名义额必须 < $10,否则它根本不会被命中 —— 那样这条判据就永远是绿的(假判据)
    bad_control = _stats("0xfp", 50000, 5000, 49500, 12.0, 0.999, 50000 * 2.0)
    result = wd.scan_rows(_wash_rows() + [bad_control])
    assert result["falsification"]["control_false_positives"] == 1
    assert result["verdict"] == "FAIL", "对照组被误伤了,判决却还是绿的 —— 判据本身是坏的"


def test_share_redline_does_fire_on_a_full_size_scan():
    """⭐扫描规模够大 + 确实抓过头时,占比红线必须真的红。"""
    over = [_stats(*MEASURED_WASH_MARKETS[0])] * 10
    filler = [_stats("0xfill", 2000, 50, 100, 12.0, 0.5, 2000 * 30.0)] * (
        wd.MIN_MARKETS_FOR_SHARE_REDLINE - 10)
    result = wd.scan_rows(over + filler)
    assert result["falsification"]["flagged_trade_share_evaluated"] is True
    assert result["falsification"]["flagged_trade_share"] > wd.MAX_FLAGGED_TRADE_SHARE
    assert result["verdict"] == "FAIL"
    # 断言只钉「哪条红线响了」,不钉分母的措辞 —— 措辞随分母变,钉死会变成脆判据
    assert any(b.startswith("命中笔数占") for b in result["breaches"])


def test_share_denominator_must_be_the_whole_lake_not_the_scanned_subset():
    """⭐分母陷阱:聚合把小市场滤掉了,拿"扫过的笔数"当分母会把占比虚报近一倍。"""
    rows = _wash_rows() + _normal_rows()
    subset = wd.scan_rows(rows)
    whole = wd.scan_rows(rows, lake_total_trades=26_807_503)
    assert subset["falsification"]["share_denominator_kind"].startswith("扫过")
    assert whole["falsification"]["share_denominator_kind"] == "全湖笔数"
    assert (whole["falsification"]["flagged_trade_share"]
            < subset["falsification"]["flagged_trade_share"])
    assert whole["audit"]["lake_total_trades"] == 26_807_503


def test_report_records_every_constant_that_decides_the_verdict():
    """⭐产物必须能**独立复核**:决定 verdict 的常量一个都不能漏。

    否则半年后常量一改,旧报告从产物本身看不出"对照组 125 个"是按什么定义数的。
    """
    t = wd.scan_rows(_wash_rows())["thresholds"]
    for name in ("MEASURED_MIN_TRADES", "MEASURED_WALLET_SHARE", "MEASURED_EXTREME_LOW",
                 "MEASURED_EXTREME_HIGH", "MEASURED_MAX_NOTIONAL_PER_TRADE",
                 "CONTROL_MIN_TRADES", "CONTROL_MIN_WALLETS",
                 "MAX_CONTROL_FALSE_POSITIVES", "MAX_FLAGGED_TRADE_SHARE",
                 "MIN_MARKETS_FOR_SHARE_REDLINE", "KNOWN_WASH_MARKETS"):
        assert name in t, f"报告里没记 {name},产物无法独立复核"


# ---------- 5. 消费者一侧 ----------

def test_load_excluded_markets_roundtrip(tmp_path):
    p = tmp_path / "wash_markets.json"
    wd.write_report(wd.scan_rows(_wash_rows()), p)
    assert wd.load_excluded_markets(p) == {r[0] for r in MEASURED_WASH_MARKETS}


@pytest.mark.parametrize("content", ["", "not json{", "[]", '{"markets": "wrong type"}'])
def test_load_excluded_markets_survives_corrupt_file(tmp_path, content):
    """整份文件坏掉时降级为空集并**出声**。"""
    p = tmp_path / "x.json"
    p.write_text(content, encoding="utf-8")
    with pytest.warns(UserWarning):
        assert wd.load_excluded_markets(p) == set()


def test_load_excluded_markets_missing_file_warns(tmp_path):
    with pytest.warns(UserWarning):
        assert wd.load_excluded_markets(tmp_path / "nope.json") == set()


def test_load_excluded_markets_warns_on_partial_list_corruption(tmp_path):
    """⭐列表**内部**有坏条目时也必须出声 —— 原实现只对整份文件坏掉 warn,
    对单条坏掉是**静默丢弃**的(2026-08-23 review 抓到:5 进 2 出,0 条告警)。
    这正是 CLAUDE.md「请求数 vs 返回数对账」那一条。
    """
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"markets": [
        {"condition_id": "0xaaa"},
        {"condition_id": 12345},        # 类型不对
        {"no_condition_id_field": 1},   # 缺键
        "not-even-a-dict",              # 整条是垃圾
        {"condition_id": "0xbbb"},
    ]}), encoding="utf-8")
    with pytest.warns(UserWarning, match="3/5"):
        assert wd.load_excluded_markets(p) == {"0xaaa", "0xbbb"}


# ---------- 6. scan_lake 的真实链路(原先零覆盖)----------

def _write_parquet(path, rows):
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE t(condition_id VARCHAR, proxy_wallet VARCHAR, "
                "size DOUBLE, price DOUBLE)")
    con.executemany("INSERT INTO t VALUES (?,?,?,?)", rows)
    con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
    con.close()


def test_scan_lake_end_to_end_on_a_real_parquet(tmp_path, monkeypatch):
    """⭐scan_lake 是唯一真正碰 DuckDB 的函数,原先**零可执行覆盖**。

    这条把 SET TimeZone / 参数化 SET / 两趟查询 / lake_total 传递 全部串起来跑一遍。
    """
    monkeypatch.setattr(wd, "MEASURED_MIN_TRADES", 10)
    f = tmp_path / "part.parquet"
    rows = ([("0xwash", "0xbot", 5.0, 0.001)] * 40          # 单钱包 100%、价格贴 0、每笔 $0.005
            + [("0xwash", "0xother", 5.0, 0.001)] * 2
            + [("0xclean", f"0xw{i}", 20.0, 0.5) for i in range(30)])
    _write_parquet(f, rows)
    result = wd.scan_lake(str(f), memory_limit="512MB")
    assert result["audit"]["lake_total_trades"] == len(rows)
    assert [m["condition_id"] for m in result["markets"]] == ["0xwash"]
    assert result["audit"]["flagged"] == 1


def test_scan_lake_wraps_read_errors_with_a_chained_cause(tmp_path):
    """坏 parquet 必须抛 RuntimeError 且**保留原始异常**(不许吞掉线索)。"""
    bad = tmp_path / "bad.parquet"
    bad.write_text("definitely not parquet", encoding="utf-8")
    with pytest.raises(RuntimeError, match="读数据湖失败") as ei:
        wd.scan_lake(str(bad))
    assert ei.value.__cause__ is not None, "原始异常被吞了,排查时线索就断了"


# ---------- 7. 两条「保险条件」必须真的承重(变异测试补出来的缺口)----------
# 🔴 2026-08-23 变异实测:把「价格贴两端」整条去掉,38 条判据**一条都不红** ——
#    说明那两条保险条件当时根本没有任何判据在验。下面三条把它们钉住。

def test_price_extreme_condition_is_load_bearing():
    """占比高 + 每笔金额小,但价格在**中间** ⇒ 不许判为刷单。

    这正是保险条件要挡的那一类:**合法做市商在冷门盘里占了大多数笔数**。
    去掉「价格贴两端」这一条,本判据必红。
    """
    hit, reasons = wd.classify_market(
        _stats("0xmm-midprice", 50000, 120, 49500, 5.0, 0.50, 50000 * 2.0))
    assert hit is False, f"价格在中间的市场被当成刷单了: {reasons}"


def test_notional_condition_is_load_bearing():
    """占比高 + 价格贴两端,但**每笔金额很大** ⇒ 不许判为刷单。

    大额对敲是另一个形状,本工具明确声明抓不到(设计单 §8);
    误把它当成微额刷单排掉,等于悄悄扔掉真实的大额交易。
    """
    hit, reasons = wd.classify_market(
        _stats("0xbig-notional", 50000, 120, 49500, 5.0, 0.999, 50000 * 500.0))
    assert hit is False, f"大额市场被当成微额刷单了: {reasons}"


def test_min_trades_magnitude_is_pinned_not_just_its_boundary():
    """⭐门槛的**量级**要被钉住,不只是边界行为。

    ⚠️ 这条**故意硬编码 999**,与本文件其它地方"import 被检验对象自己的常量"的做法相反。
    理由:那种写法让判据随常量一起漂移 —— 变异实测把下界从 1,000 悄悄降到 1,
    `tiny = MEASURED_MIN_TRADES - 1` 只是跟着变成 0,**38 条判据一条都不红**。
    要抓住"门槛被悄悄放水",必须有一个不跟着漂的绝对锚点。
    """
    hit, _ = wd.classify_market(_stats("0x999", 999, 3, 999, 5.0, 0.001, 999 * 0.02))
    assert hit is False, "999 笔的市场被判成刷单 —— 笔数下界被放水了"
    assert wd.MEASURED_MIN_TRADES >= 100, (
        "笔数下界低于 100 时占比会退化(1 笔成交 ⇒ 占比按定义 100%),实测会造出上万个假阳性")
