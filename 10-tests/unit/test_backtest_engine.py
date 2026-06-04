#!/usr/bin/env python3
"""
回测引擎单元测试(08-backtests/engine)
设计见 docs/NEXT_STEPS_DESIGN_2026-06-04.md §A。

原则:全部用合成 fixture,不连真实库、不写生产 07-data/。
数值均可手算核对。
"""

from datetime import date

import pytest

from engine.costs import CostModel
from engine.data import PriceSeries, Signal, _to_date, outcome_price
from engine.metrics import _agg, pearson, whale_alpha
from engine.portfolio import simulate_one


# ============================================================
# fixtures(合成)
# ============================================================

def make_series(dates_prices, end_date=None, cap=100.0):
    """[(date_str, yes_price), ...] → PriceSeries(升序)。"""
    ps = PriceSeries(market="m1", end_date=_to_date(end_date) if end_date else None)
    for d, p in dates_prices:
        ps.dates.append(_to_date(d))
        ps.yes_prices.append(p)
        ps.cap_usd.append(cap)
    return ps


def make_signal(outcome="Yes", sig_date="2026-05-09", implied=0.25,
                notional=100.0, shares=400.0, side="BUY"):
    return Signal(wallet="0xabc", market="m1", outcome=outcome, side=side,
                  sig_date=_to_date(sig_date), shares=shares,
                  notional=notional, implied_px=implied)


ZERO = CostModel(fee_bps=0, slippage_bps=0, gas_usd=0.0,
                 capacity_frac=1.0, impact_coef=0.0)
BASE = CostModel()  # 默认 base 档


# ============================================================
# costs.py
# ============================================================

class TestCostModel:
    def test_entry_fill_buy_higher_sell_lower(self):
        c = CostModel(fee_bps=0, slippage_bps=50)  # drag = 0.005
        assert c.entry_fill(0.5, "BUY") == pytest.approx(0.5025)
        assert c.entry_fill(0.5, "SELL") == pytest.approx(0.4975)

    def test_entry_fill_fee_plus_slippage(self):
        c = CostModel(fee_bps=50, slippage_bps=50)  # drag = 0.01
        assert c.entry_fill(0.5, "BUY") == pytest.approx(0.505)

    def test_entry_fill_clips_to_unit_interval(self):
        c = CostModel(slippage_bps=20000)  # drag = 2.0
        assert c.entry_fill(0.99, "BUY") == 1.0   # 上限裁剪
        assert c.entry_fill(0.10, "SELL") == 0.0  # 下限裁剪

    def test_gas_drag(self):
        c = CostModel(gas_usd=0.02)
        assert c.gas_drag(100) == pytest.approx(0.0002)
        assert c.gas_drag(0) == 0.0      # 不除零
        assert c.gas_drag(-5) == 0.0

    def test_deployable_capacity_cap(self):
        c = CostModel(capacity_frac=0.02)
        assert c.deployable(1000, 10000) == pytest.approx(200)   # 受容量限
        assert c.deployable(50, 10000) == pytest.approx(50)      # 想要的更少
        assert c.deployable(1000, 0) == 0.0                      # 无容量

    def test_impact_drag_linear_over_capacity(self):
        c = CostModel(capacity_frac=0.02, impact_coef=0.5)
        # cap_abs = 200; want 1000 > cap → excess=(1000-200)/1000=0.8; *0.5=0.4
        assert c.impact_drag(1000, 10000) == pytest.approx(0.4)
        # 容量内 → 无冲击
        assert c.impact_drag(100, 10000) == 0.0
        assert c.impact_drag(1000, 0) == 0.0


# ============================================================
# data.py
# ============================================================

class TestDataHelpers:
    def test_outcome_price(self):
        assert outcome_price(0.3, "Yes") == 0.3
        assert outcome_price(0.3, "No") == pytest.approx(0.7)
        assert outcome_price(0.3, "TeamX") is None   # 多结果不在宇宙内
        assert outcome_price(None, "Yes") is None

    def test_to_date_variants(self):
        assert _to_date("2026-05-04T07:30:14+00:00") == date(2026, 5, 4)
        assert _to_date("2026-05-09") == date(2026, 5, 9)
        assert _to_date("") is None
        assert _to_date("not-a-date") is None
        assert _to_date(None) is None


class TestPriceSeries:
    def setup_method(self):
        self.ps = make_series(
            [("2026-05-09", 0.2), ("2026-05-11", 0.3), ("2026-05-13", 0.9)],
            end_date="2026-05-20")

    def test_yes_on_or_after_hit_and_boundary(self):
        assert self.ps.yes_on_or_after(date(2026, 5, 9))[:2] == (date(2026, 5, 9), 0.2)
        # 05-10 无快照 → 取之后第一个 05-11
        assert self.ps.yes_on_or_after(date(2026, 5, 10))[:2] == (date(2026, 5, 11), 0.3)

    def test_yes_on_or_after_out_of_range(self):
        assert self.ps.yes_on_or_after(date(2026, 5, 14)) is None

    def test_terminal(self):
        assert self.ps.terminal() == (date(2026, 5, 13), 0.9)

    def test_is_resolved_false_when_not_converged(self):
        # 终值 0.9 未收敛到 0/1 → 未结算
        assert self.ps.is_resolved() is False

    def test_is_resolved_true_when_converged_and_past_enddate(self):
        ps = make_series([("2026-05-09", 0.2), ("2026-05-13", 0.99)],
                         end_date="2026-05-12")
        assert ps.is_resolved() is True

    def test_is_resolved_false_when_converged_but_before_enddate(self):
        ps = make_series([("2026-05-09", 0.2), ("2026-05-13", 0.99)],
                         end_date="2026-05-30")
        assert ps.is_resolved() is False

    def test_n_days(self):
        assert self.ps.n_days == 3


# ============================================================
# portfolio.py
# ============================================================

class TestSimulateOne:
    def setup_method(self):
        # Yes 价 05-09=0.2, 05-13=0.9
        self.ps = make_series([("2026-05-09", 0.2), ("2026-05-13", 0.9)],
                              end_date="2026-05-20")

    def test_realistic_entry_uses_next_snapshot(self):
        sig = make_signal(outcome="Yes", sig_date="2026-05-09", implied=0.25)
        r = simulate_one(sig, self.ps, ZERO, "realistic", "+1d")
        assert r is not None
        assert r.entry_price == pytest.approx(0.2)        # 用快照价非 implied
        assert r.mark_price == pytest.approx(0.9)         # +1d 后首快照=05-13
        assert r.gross_ret == pytest.approx((0.9 - 0.2) / 0.2)  # = 3.5
        assert r.net_ret == pytest.approx(3.5)            # 零成本

    def test_optimistic_entry_uses_whale_price(self):
        sig = make_signal(outcome="Yes", sig_date="2026-05-09", implied=0.25)
        r = simulate_one(sig, self.ps, ZERO, "optimistic", "resolution")
        assert r.entry_price == pytest.approx(0.25)       # 用鲸鱼成交价
        assert r.mark_price == pytest.approx(0.9)
        assert r.gross_ret == pytest.approx((0.9 - 0.25) / 0.25)

    def test_no_outcome_conversion(self):
        # 买 No:入场 1-0.2=0.8,结算 1-0.9=0.1 → 亏
        sig = make_signal(outcome="No", sig_date="2026-05-09", implied=0.75)
        r = simulate_one(sig, self.ps, ZERO, "realistic", "resolution")
        assert r.entry_price == pytest.approx(0.8)
        assert r.mark_price == pytest.approx(0.1)
        assert r.gross_ret == pytest.approx((0.1 - 0.8) / 0.8)

    def test_not_markable_returns_none(self):
        # 信号晚于所有快照 → 无法入场
        sig = make_signal(sig_date="2026-05-25")
        assert simulate_one(sig, self.ps, ZERO, "realistic", "+1d") is None

    def test_costs_reduce_net_return(self):
        sig = make_signal(outcome="Yes", sig_date="2026-05-09", implied=0.25,
                          notional=100.0)
        r = simulate_one(sig, self.ps, BASE, "realistic", "+1d")
        # base 档:入场价被滑点抬高、net 应低于零成本的 gross
        assert r.entry_price > 0.2
        assert r.net_ret < r.gross_ret      # gas 拖累(冲击已不计)
        assert r.deployable == pytest.approx(BASE.capacity_frac * 100)

    def test_capacity_is_throughput_not_impact_haircut(self):
        """2026-06-05 修复回归:大额 notional + 薄容量不再被 impact 罚收益。
        net 只比 gross 少一个 gas(摊到 deployable),而非 ~0.5 的冲击扣血。"""
        # notional 远超容量:cap_abs = 0.02*100 = 2;deployable = min(10000,2)=2
        sig = make_signal(outcome="Yes", sig_date="2026-05-09", notional=10000.0)
        r = simulate_one(sig, self.ps, BASE, "realistic", "+1d")
        assert r.deployable == pytest.approx(2.0)            # 容量上限封顶
        gas = BASE.gas_usd / r.deployable                    # gas 摊到 deployable
        assert r.net_ret == pytest.approx(r.gross_ret - gas) # 仅 gas,无 impact
        # 反证:若仍按旧模型罚冲击,net 会比这低 ~0.5
        assert r.net_ret > r.gross_ret - 0.1


class TestRunBacktest:
    def test_run_backtest_contract(self):
        from engine.portfolio import run_backtest
        ps = make_series([("2026-05-09", 0.2), ("2026-05-13", 0.9)],
                         end_date="2026-05-20")
        prices = {"m1": ps}
        sigs = [
            make_signal(sig_date="2026-05-09"),         # 可标记
            make_signal(sig_date="2026-05-25"),         # 越界,不可标记
            Signal("0xx", "mZ", "Yes", "BUY",           # market 无价格序列 → 跳过
                   _to_date("2026-05-09"), 10, 5, 0.5),
        ]
        out = run_backtest(sigs, prices, ZERO, "realistic", ["+1d", "resolution"])
        assert set(out) == {"+1d", "resolution"}        # 每持有期一组
        assert len(out["+1d"]) == 1                      # 仅 1 个可标记
        assert len(out["resolution"]) == 1


# ============================================================
# metrics.py
# ============================================================

class TestAgg:
    def test_empty(self):
        a = _agg([])
        assert a["n"] == 0 and a["mean"] is None

    def test_basic(self):
        a = _agg([0.1, -0.1, 0.2])
        assert a["n"] == 3
        assert a["mean"] == pytest.approx(0.2 / 3)
        assert a["win_rate"] == pytest.approx(2 / 3)
        assert a["median"] == pytest.approx(0.1)


class TestPearson:
    def test_perfect_positive(self):
        assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)

    def test_zero_variance_none(self):
        assert pearson([1, 2, 3], [5, 5, 5]) is None

    def test_too_few_points_none(self):
        assert pearson([1, 2], [3, 4]) is None


def _tr(wallet, net, deployable=10.0):
    """最小 TradeResult(只填 whale_alpha 用到的字段)。"""
    from engine.portfolio import TradeResult
    return TradeResult(
        wallet=wallet, market="m", outcome="Yes", sig_date=date(2026, 5, 9),
        horizon="resolution", entry_date=date(2026, 5, 9), entry_price=0.5,
        mark_date=date(2026, 5, 13), mark_price=0.6, deployable=deployable,
        gross_ret=net, net_ret=net, markable_days=5, resolved=True)


class TestWhaleAlpha:
    def test_min_trades_filter_and_sort(self):
        results = (
            [_tr("0xWIN", 0.2), _tr("0xWIN", 0.4), _tr("0xWIN", 0.0)]   # n=3 mean .2
            + [_tr("0xLOSE", -0.1), _tr("0xLOSE", -0.3), _tr("0xLOSE", -0.2)]  # n=3 mean -.2
            + [_tr("0xRARE", 0.9)]                                       # n=1 应被过滤
        )
        wa = whale_alpha(results, min_trades=3)
        wallets = [w.wallet for w in wa]
        assert "0xRARE" not in wallets            # 出现次数不足被过滤
        assert wallets == ["0xWIN", "0xLOSE"]     # deployable 均等 → 资金加权=等权,降序
        assert wa[0].mean_net == pytest.approx(0.2)
        assert wa[0].mean_net_cw == pytest.approx(0.2)   # 均等容量下两口径一致
        assert wa[0].win_rate == pytest.approx(2 / 3)

    def test_sort_by_capital_weighted_not_equal_weighted(self):
        """资金加权排序应区别于等权:大赢家若在薄市场(deployable 小)则排名被压低。"""
        results = [
            # 0xTHIN:等权高(+0.5)但赢的那笔只能下 $1,亏的能下 $99 → 资金加权≈亏
            _tr("0xTHIN", 1.0, deployable=1.0), _tr("0xTHIN", 0.0, deployable=99.0),
            # 0xFAT:等权低(+0.1)但都是大额、稳定正 → 资金加权也 +0.1
            _tr("0xFAT", 0.1, deployable=100.0), _tr("0xFAT", 0.1, deployable=100.0),
        ]
        wa = whale_alpha(results, min_trades=2)
        by = {w.wallet: w for w in wa}
        assert by["0xTHIN"].mean_net == pytest.approx(0.5)          # 等权高
        assert by["0xTHIN"].mean_net_cw == pytest.approx(0.01)      # (1*1+0*99)/100
        assert by["0xFAT"].mean_net_cw == pytest.approx(0.1)
        assert [w.wallet for w in wa] == ["0xFAT", "0xTHIN"]        # 资金加权下 FAT 排前


class TestHorizonSummaryAndVerdict:
    def test_horizon_summary_strata_and_resolved(self):
        from engine.metrics import horizon_summary
        # 一笔 7-14d 已结算、一笔 1-2d 未结算
        results = [
            _tr("0xa", 0.1),                      # markable_days=5(default _tr)→3-6d
        ]
        # 自造不同 markable_days/resolved 的结果
        from engine.portfolio import TradeResult
        results = [
            TradeResult("0xa", "m", "Yes", date(2026, 5, 9), "resolution",
                        date(2026, 5, 9), 0.5, date(2026, 5, 13), 0.6,
                        10.0, 0.2, 0.2, markable_days=10, resolved=True),
            TradeResult("0xb", "m", "Yes", date(2026, 5, 9), "resolution",
                        date(2026, 5, 9), 0.5, date(2026, 5, 10), 0.4,
                        5.0, -0.2, -0.2, markable_days=2, resolved=False),
        ]
        s = horizon_summary(results)
        assert s["net"]["n"] == 2
        assert s["resolved_only_net"]["n"] == 1            # 只 1 笔已结算
        assert "7-14d" in s["by_markable_days"]            # 分层键正确
        assert "1-2d" in s["by_markable_days"]
        assert s["total_deployable"] == pytest.approx(15.0)

    def test_capital_weighted_vs_equal_weighted(self):
        """资金加权按 deployable 加权,与等权不同;edge 若在薄市场会被打回。"""
        from engine.metrics import horizon_summary
        from engine.portfolio import TradeResult
        # 一笔小钱大赚(deployable=10, net=+1.0)、一笔大钱小亏(deployable=90, net=-0.1)
        results = [
            TradeResult("0xa", "m", "Yes", date(2026, 5, 9), "resolution",
                        date(2026, 5, 9), 0.5, date(2026, 5, 13), 1.0,
                        10.0, 1.0, 1.0, markable_days=5, resolved=True),
            TradeResult("0xb", "m", "Yes", date(2026, 5, 9), "resolution",
                        date(2026, 5, 9), 0.5, date(2026, 5, 10), 0.45,
                        90.0, -0.1, -0.1, markable_days=5, resolved=True),
        ]
        s = horizon_summary(results)
        # 等权 = (1.0 + -0.1)/2 = 0.45;资金加权 = (1.0*10 + -0.1*90)/100 = 0.01
        assert s["net"]["mean"] == pytest.approx(0.45)
        cw = s["capital_weighted"]
        assert cw["mean_net"] == pytest.approx(0.01)
        assert cw["deployable"] == pytest.approx(100.0)
        # 按资金胜率:只有 deployable=10 的赚 → 10/100
        assert cw["win_rate"] == pytest.approx(0.10)

    def test_cap_stats_zero_deployable(self):
        from engine.metrics import _cap_stats
        from engine.portfolio import TradeResult
        r = TradeResult("0xa", "m", "Yes", date(2026, 5, 9), "resolution",
                        date(2026, 5, 9), 0.5, date(2026, 5, 13), 0.6,
                        0.0, 0.2, 0.2, markable_days=5, resolved=True)
        cw = _cap_stats([r])
        assert cw["mean_net"] is None and cw["deployable"] == 0.0

    def test_verdict_three_states(self):
        from engine.metrics import verdict
        # 样本不足
        assert verdict({"n": 10, "mean": 0.1, "win_rate": 0.5}).startswith("⚠️")
        # 🔴 等权就亏(无论资金加权)
        assert "🔴" in verdict({"n": 50, "mean": -0.05, "win_rate": 0.2}, 0.1)
        # 🟢 资金加权为正 → 可放大
        assert "🟢" in verdict({"n": 50, "mean": 0.05, "win_rate": 0.4}, 0.03)
        # 🟡 等权正但资金加权≤0 → 仅微仓(本次核心发现)
        v = verdict({"n": 50, "mean": 0.18, "win_rate": 0.2}, -0.04)
        assert "🟡" in v and "仅微仓" in v
        # 无容量数据时退回等权(向后兼容旧调用)
        assert "🟢" in verdict({"n": 50, "mean": 0.05, "win_rate": 0.4})


# ============================================================
# data.py — DB 加载(内存 sqlite,验证 SQL 契约)
# ============================================================

class TestDBLoading:
    def _conn(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE daily_price_snapshots(
            market TEXT, outcome TEXT, price REAL, volume_24h REAL,
            end_date TEXT, snapshot_date TEXT)""")
        conn.execute("""CREATE TABLE changes(
            wallet TEXT, market TEXT, outcome TEXT, side TEXT, timestamp TEXT,
            old_size REAL, new_size REAL, change_amount REAL)""")
        return conn

    def test_load_price_series_filters_yes_only(self):
        from engine.data import load_price_series
        conn = self._conn()
        conn.executemany(
            "INSERT INTO daily_price_snapshots VALUES(?,?,?,?,?,?)",
            [("m1", "Yes", 0.2, 100, "2026-05-20", "2026-05-09"),
             ("m1", "Yes", 0.3, 120, "2026-05-20", "2026-05-11"),
             ("m1", "No", 0.8, 100, "2026-05-20", "2026-05-09"),   # 非 Yes,应忽略
             ("m2", "Yes", 0.5, 50, "2026-05-25", "2026-05-10")])
        ps = load_price_series(conn)
        assert set(ps) == {"m1", "m2"}
        assert ps["m1"].dates == [date(2026, 5, 9), date(2026, 5, 11)]  # 升序、无 No
        assert ps["m1"].yes_prices == [0.2, 0.3]
        assert ps["m1"].cap_usd == [100, 120]

    def test_load_signals_universe_and_implied_px(self):
        from engine.data import load_price_series, load_signals
        conn = self._conn()
        conn.execute("INSERT INTO daily_price_snapshots VALUES(?,?,?,?,?,?)",
                     ("m1", "Yes", 0.2, 100, "2026-05-20", "2026-05-09"))
        prices = load_price_series(conn)
        conn.executemany(
            "INSERT INTO changes VALUES(?,?,?,?,?,?,?,?)",
            [
                # 有效 BUY,market 在价格表,Yes → implied=200/400=0.5
                ("0xa", "m1", "Yes", "BUY", "2026-05-09T00:00:00+00:00", 0, 400, 200),
                # SELL,默认 sides=BUY → 应被排除
                ("0xa", "m1", "Yes", "SELL", "2026-05-09T00:00:00+00:00", 400, 0, 200),
                # 多结果 outcome → 排除
                ("0xb", "m1", "TeamX", "BUY", "2026-05-09T00:00:00+00:00", 0, 10, 5),
                # market 不在价格表 → 排除
                ("0xc", "mZ", "Yes", "BUY", "2026-05-09T00:00:00+00:00", 0, 10, 5),
                # shares<=0 → 排除
                ("0xd", "m1", "No", "BUY", "2026-05-09T00:00:00+00:00", 5, 5, 5),
                # 脏价(implied>1):200/100=2 → 信号保留但 implied_px=None
                ("0xe", "m1", "No", "BUY", "2026-05-09T00:00:00+00:00", 0, 100, 200),
            ])
        sigs = load_signals(conn, prices)
        assert len(sigs) == 2                       # 0xa(BUY) + 0xe(脏价仍保留)
        by_w = {s.wallet: s for s in sigs}
        assert by_w["0xa"].implied_px == pytest.approx(0.5)
        assert by_w["0xe"].implied_px is None       # 脏价归 None

    def test_load_signals_min_notional(self):
        from engine.data import load_price_series, load_signals
        conn = self._conn()
        conn.execute("INSERT INTO daily_price_snapshots VALUES(?,?,?,?,?,?)",
                     ("m1", "Yes", 0.2, 100, "2026-05-20", "2026-05-09"))
        prices = load_price_series(conn)
        conn.executemany(
            "INSERT INTO changes VALUES(?,?,?,?,?,?,?,?)",
            [("0xa", "m1", "Yes", "BUY", "2026-05-09T00:00:00+00:00", 0, 400, 50),
             ("0xb", "m1", "Yes", "BUY", "2026-05-09T00:00:00+00:00", 0, 400, 500)])
        sigs = load_signals(conn, prices, min_notional=100)
        assert [s.wallet for s in sigs] == ["0xb"]   # 50 被过滤,500 保留


class TestConnect:
    def test_connect_missing_db_raises(self):
        from engine.data import connect
        with pytest.raises(FileNotFoundError):
            connect("/nonexistent/path/to.db")


# ============================================================
# strategies/selective_whale.py — 精选过滤(全部入场时已知条件)
# ============================================================

class TestSelectiveWhale:
    def _tr(self, wallet="0xa", entry=0.5, entry_date="2026-05-09", market="m1"):
        from engine.portfolio import TradeResult
        return TradeResult(
            wallet=wallet, market=market, outcome="Yes",
            sig_date=_to_date(entry_date), horizon="resolution",
            entry_date=_to_date(entry_date), entry_price=entry,
            mark_date=_to_date("2026-05-20"), mark_price=0.6,
            deployable=10.0, gross_ret=0.1, net_ret=0.1,
            markable_days=5, resolved=True)

    def _prices(self, end_date="2026-05-20"):
        return {"m1": make_series([("2026-05-09", 0.5)], end_date=end_date)}

    def test_entry_price_bounds(self):
        from strategies.selective_whale import passes
        pr = self._prices()
        assert passes(self._tr(entry=0.5), pr, max_entry_price=0.85)
        assert not passes(self._tr(entry=0.90), pr, max_entry_price=0.85)  # 太贵剔除
        assert not passes(self._tr(entry=0.02), pr, min_entry_price=0.05)  # 太便宜剔除
        # 边界:max 为开区间,min 为闭区间
        assert not passes(self._tr(entry=0.85), pr, max_entry_price=0.85)
        assert passes(self._tr(entry=0.05), pr, min_entry_price=0.05)

    def test_dte_filter_uses_end_date_not_lookahead(self):
        from strategies.selective_whale import passes, days_to_resolution
        tr = self._tr(entry_date="2026-05-09")
        # end_date=05-20 → dte=11 天
        assert days_to_resolution(tr, self._prices("2026-05-20")) == 11
        assert passes(tr, self._prices("2026-05-20"), max_dte=14)      # 11<=14 通过
        assert not passes(tr, self._prices("2026-06-30"), max_dte=14)  # 52>14 剔除

    def test_dte_none_end_date_excluded(self):
        from strategies.selective_whale import passes, days_to_resolution
        tr = self._tr()
        pr = {"m1": make_series([("2026-05-09", 0.5)], end_date=None)}
        assert days_to_resolution(tr, pr) is None
        assert not passes(tr, pr, max_dte=14)   # 到期日未知 → 不算短周期,剔除
        assert passes(tr, pr)                   # 无 dte 约束时不受影响

    def test_wallet_allowlist(self):
        from strategies.selective_whale import passes
        pr = self._prices()
        assert passes(self._tr(wallet="0xWIN"), pr, wallets={"0xWIN"})
        assert not passes(self._tr(wallet="0xBAD"), pr, wallets={"0xWIN"})

    def test_filter_results_combines_conditions(self):
        from strategies.selective_whale import filter_results
        pr = self._prices("2026-05-20")
        rs = [
            self._tr(wallet="0xa", entry=0.5),     # 通过
            self._tr(wallet="0xa", entry=0.95),    # 太贵
        ]
        out = filter_results(rs, pr, max_entry_price=0.85, max_dte=14)
        assert len(out) == 1 and out[0].entry_price == 0.5
