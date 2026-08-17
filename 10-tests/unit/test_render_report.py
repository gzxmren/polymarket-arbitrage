#!/usr/bin/env python3
"""判据焊死:趋势报表 —— 一张静态单页,且**它自己过期时必须一眼看得出来**(2026-08-17)。

## 由来(实测)

本项目上一个监控网页(2026-03-20 提交「Phase 3: 质量监控与自动化」)死了至少 26 天
没人发现:两个 systemd 单元 disabled+inactive、journal 零条记录、
数据源 `07-data/` 停写 26 天、3000 端口还被别的项目占着。

⭐**最危险的不是它死了,是它死了还能打开** —— 一个显示着 26 天前数字的看板,
看上去和正常的一模一样。所以本页最要紧的判据不是"图画得好看",而是
**页面必须显示数据本身的最新时刻,而不只是生成时刻**。

## 三条设计约束(与日报同源)

1. **不起服务、不占端口**:生成一份自包含 HTML 落盘,和日报同一个 systemd service
   一起跑 —— 两者不可能各自漂移。上一版死于"服务没人拉起来"。
2. **只做读者**:数据源只有心跳 parquet。判据把"读的正是写的那份"焊死。
3. **它是下钻不是首页**:日报是第一眼,这页是"日报某行不对劲时才点开"。
   故不做实时刷新、不做告警 —— 那些是日报和 alerts 的活。

## 本判据**不能**回答什么

- 不回答"图表该长什么样"。那取决于日报跑一段时间后你真正会去下钻什么。
- 不回答"有没有人会打开它"。判据管不了习惯;能管的是**打开时不会被骗**。
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import render_report as rr  # noqa: E402


def _hb(ts, day, **kw):
    base = {"ts": ts, "dt": day, "new_trades": 5000, "total_markets_polled": 50,
            "firehose_fail": 0, "new_registered": 100, "new_discovered": 100,
            "pending_registration_count": 0, "pending_registration_dropped_count": 0,
            "newly_resolved": 20, "settlement_checked": 800, "settlement_lookup_fail": 5,
            "truth_supply_zero_streak": 0, "cycle_seconds": 180.0,
            "slow_cycle_streak": 0, "offset_overflow_count": 0,
            "rotation_hole_streak": 0, "alert_queue_depth": 0, "alert_dropped_count": 0}
    base.update(kw)
    return base


def _days(n=5, per_day=96, **kw):
    out = []
    for d in range(n):
        day = f"2026-08-{10 + d:02d}"
        for i in range(per_day):
            out.append(_hb(1_786_000_000 + d * 86400 + i * 900, day, **kw))
    return out


# ---------- 判据组 A:⭐过期必须一眼看得出来 ----------

def test_page_shows_the_data_timestamp_not_only_the_render_time():
    """⭐核心:必须显示**数据最新时刻**,不能只显示"生成于"。

    只显示生成时刻的话,一个每天准时生成、但底层数据早已停更的页面,
    看上去永远是新鲜的 —— 那正是上一个 dashboard 的死法。
    """
    html = rr.render(_days())
    assert "数据最新" in html, "页面没显示数据本身的最新时刻"
    assert "2026-08-14" in html, "没把最后一天的日期摆出来"


def test_stale_data_is_flagged_on_the_page_itself():
    """数据明显过期时,页面自己要喊出来 —— 不能等人去心算。"""
    old = _days(n=2)
    for h in old:
        h["ts"] = 1_700_000_000        # 很久以前
    html = rr.render(old, now=1_786_953_000)
    assert "过期" in html or "陈旧" in html, "数据都过期这么久了,页面一声不吭"


def test_fresh_data_is_not_flagged():
    """防洪的另一头:新鲜数据不许挂"过期"字样,否则这个标记会被无视。"""
    fresh = _days(n=2)
    now = max(h["ts"] for h in fresh) + 600
    html = rr.render(fresh, now=now)
    assert "数据已过期" not in html


def test_empty_input_is_loud_not_a_pretty_empty_page():
    """没有数据要响亮地说没有,不许渲染成一张漂亮的全 0 图表。"""
    html = rr.render([])
    assert "没有心跳" in html
    assert "<svg" not in html, "没数据还画图 = 画的是 0,和'系统很闲'无法区分"


# ---------- 判据组 B:自包含(CSP/离线都要能看) ----------

def test_page_is_self_contained():
    """不许引外部资源:没有网/离线打开也要完整。"""
    html = rr.render(_days())
    bad = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+', html)
    assert not bad, f"页面引了外部资源:{bad[:3]}"
    assert "<script" not in html.lower() or "cdn" not in html.lower()


def test_page_declares_both_themes():
    """浅色/深色都要能读 —— 半夜出事的时候打开是深色。"""
    html = rr.render(_days())
    assert "prefers-color-scheme" in html


# ---------- 判据组 C:只做读者,读的正是写的那份 ----------

def test_consumed_fields_exist_in_the_heartbeat_schema():
    """⭐字段改名时本条先红 —— 上一个 dashboard 死于数据源搬家而它不知道。"""
    import storage_engine as se
    missing = [f for f in rr.CONSUMED_FIELDS if f not in se.AUDIT_FIELDS]
    assert not missing, f"报表读的字段不在心跳 schema 里:{missing}"


def test_it_reuses_the_digest_leg_logic_instead_of_reimplementing():
    """⭐不许把"腿怎么算"再写一遍 —— 两份实现必然分叉,只有一份会拿到日后的修正。

    (本项目已犯 3 次:游标轮转抄 3 遍、时间闸抄 5 遍、连零守护抄 4 遍。)
    """
    src = Path(rr.__file__).read_text(encoding="utf-8")
    assert "daily_digest" in src, "报表没复用日报的腿计算,自己又写了一份"
    assert "_longest_outage_run" not in src.replace("dd._longest_outage_run", ""), (
        "报表自己重写了断供连续段的算法")


# ---------- 判据组 D:数字要对得上 ----------

def test_daily_totals_match_the_input():
    """图上的数字必须等于心跳里的数字 —— 报表算错比没有报表更糟。"""
    hbs = _days(n=3, per_day=10, new_trades=7)
    rows = rr.daily_rows(hbs)
    assert len(rows) == 3
    for r in rows:
        assert r["trades"] == 70, f"当日成交合计算错:{r}"
        assert r["cycles"] == 10


def test_a_bad_day_is_visible_in_the_page():
    """出过事的那天必须在页面上显眼,而不是混在一片绿里。"""
    hbs = _days(n=2, per_day=10) + _days(n=1, per_day=10,
                                         pending_registration_dropped_count=500)
    html = rr.render(hbs)
    assert "500" in html or "5,000" in html


def test_corrupt_heartbeat_does_not_crash_the_page():
    """字段缺失/类型不对时降级出图,不许崩 —— 崩了就等于当天没有报表。"""
    assert rr.render([{"ts": 1, "dt": "2026-08-17"}, {"ts": 2}])


# ---------- 判据组 E:接线 —— 真的有人生成它 ----------

def test_the_service_actually_renders_the_report():
    """⭐和日报同一个 service 一起跑:两者不可能各自漂移,也不必再问"谁拉起它"。"""
    svc = (Path(__file__).resolve().parents[2] / "deploy" / "systemd"
           / "polymarket-daily-digest.service").read_text(encoding="utf-8")
    assert "render_report.py" in svc, (
        "没有任何地方会生成这张页面 ⇒ 它永远是第一次生成时那份,"
        "而那正是上一个 dashboard 的死法")


def test_both_exec_lines_tolerate_each_others_failure():
    """⭐两条 ExecStart 必须都带 `-` 前缀,否则前一条失败会掐掉后一条。

    systemd 原文:「If one of the commands fails (and is not prefixed with "-"),
    other lines are not executed」。日报"发不出去进队列"是**经常发生**的自愈情形,
    没有 `-` 的话当天的趋势报表根本不会生成 —— 而这页正是本次要立起来的下钻能力。
    """
    svc = (Path(__file__).resolve().parents[2] / "deploy" / "systemd"
           / "polymarket-daily-digest.service").read_text(encoding="utf-8")
    execs = [l for l in svc.splitlines() if l.startswith("ExecStart=")]
    assert len(execs) >= 2
    bare = [l for l in execs if not l.startswith("ExecStart=-")]
    assert not bare, f"这些 ExecStart 没带 `-`,失败会连坐掐掉后面的:{bare}"


def test_a_truly_corrupt_parquet_does_not_kill_the_page(tmp_path, monkeypatch):
    """同日报那条:磁盘上真的坏掉的 parquet 只该少那一块,不该让整张页面消失。"""
    import pyarrow as pa, pyarrow.parquet as pq
    import storage_engine as se
    d = tmp_path / "dt=2026-08-17"; d.mkdir(parents=True)
    good = {k: v for k, v in _hb(1_786_000_000, "2026-08-17").items() if k != "dt"}
    pq.write_table(pa.Table.from_pylist([good]), d / "ok.parquet")
    (d / "bad.parquet").write_bytes(b"garbage not parquet")
    monkeypatch.setattr(se, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(rr.se, "AUDIT_DIR", tmp_path)
    rows = rr.load_days(5)
    assert len(rows) == 1, f"坏文件把好文件也拖没了(拿到 {len(rows)} 行)"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
