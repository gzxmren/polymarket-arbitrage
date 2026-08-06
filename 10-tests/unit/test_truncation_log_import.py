#!/usr/bin/env python3
"""判据焊死:历史 offset 截断的日志导入(2026-08-06)。

留痕是今天才加的,而截断从 2026-07-22 就在发生 —— 实测 **377 个市场**已经有永久空洞。
只给新发生的留痕的话,"有痕迹的"和"有洞的"是两批,分析层照样踩旧的那批。

## 三条要焊死的纪律

1. **权威来源是日志的因果现场**,不是"开头空白 > N 天"这种反推。
   反推 = 拿与结果相关的量贴标签,会把"真的开盘很久没人交易"的市场一并冤枉。
2. **不许编不知道的字段。** 旧日志行没有 offset、没有 cold/warm ——
   那就留 null / `unknown`,不许拿 `OFFSET_WARN` 之类"填一个"。
   编出来的数长得和实测一模一样,而后人分辨不出。
3. **幂等**要靠已落盘的记录判定,不靠"我记得跑过没有"。
"""
import sys
from pathlib import Path

import pytest

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import import_truncations_from_log as imp  # noqa: E402

LOG_TEXT = """=== 采集周期 2026-07-22 15:20:45Z ===
发现层: {'firehose_trades': 11000}
    ⚠️ offset 截断: 0x01dffa7abae7.. 保留近端 8123 笔,更早历史待压频回填
本轮: 市场 50
=== 采集周期 2026-08-06 03:30:24Z ===
    ⚠️ offset 截断[warm]: 0x00ad3bb5284b.. 保留近端 9001 笔;更早历史**永久取不回**
    ⚠️ offset 截断[cold]: 0xdeadbeefdead.. 保留近端 77 笔;更早历史**永久取不回**
"""


@pytest.fixture
def logfile(tmp_path):
    p = tmp_path / "collector.log"
    p.write_text(LOG_TEXT, encoding="utf-8")
    return p


def test_parses_both_the_old_and_the_new_line_format(logfile):
    """旧行没有 [cold]/[warm] —— 导入脚本自己不能变成一个新的盲区。"""
    hits = imp.parse_log(logfile)
    assert len(hits) == 3
    by_prefix = {h["prefix"]: h for h in hits}
    assert by_prefix["0x01dffa7abae7"]["kept_rows"] == 8123
    assert by_prefix["0x00ad3bb5284b"]["mode"] == "warm"


def test_missing_mode_is_unknown_not_guessed(logfile):
    """⭐旧行不知道是冷是热 → 记 `unknown`,不许瞎猜成 cold。

    猜出来的标签会被后人当实测用,而它和真的长得一模一样
    (CLAUDE.md:用与实测相同的语气陈述推断,是最危险的那一种)。
    """
    hits = {h["prefix"]: h for h in imp.parse_log(logfile)}
    assert hits["0x01dffa7abae7"]["mode"] == "unknown"
    assert all(h["mode"] in ("cold", "warm", "unknown") for h in hits.values())


def test_each_hit_keeps_its_own_timestamp(logfile):
    """时刻取**所在周期**的,不是文件时间 —— 否则所有记录挤在同一秒,分布全废。"""
    hits = {h["prefix"]: h for h in imp.parse_log(logfile)}
    assert hits["0x01dffa7abae7"]["detected_at"] < hits["0x00ad3bb5284b"]["detected_at"]


def test_a_hit_before_any_cycle_header_is_dropped_not_dated_zero(tmp_path):
    """⭐日志被轮转/截断时,开头可能有一条没有周期头的孤儿行。

    给它安一个 0 时刻会在分布最左端插一根假柱子。宁可不导入。
    """
    p = tmp_path / "c.log"
    p.write_text("    ⚠️ offset 截断: 0xabcdef012345.. 保留近端 5 笔\n", encoding="utf-8")
    assert imp.parse_log(p) == []


def test_offset_is_null_not_fabricated(monkeypatch, logfile, tmp_path):
    """⭐日志里没有 offset ⇒ 落 null。填一个"合理的数"就是造数据。"""
    import storage_engine as se

    class _Con:
        def execute(self, *a, **k): return self
        def executemany(self, *a, **k): return self
        def sql(self, q):
            class R:
                @staticmethod
                def fetchall():
                    return [("0x01dffa7abae7", "0x01dffa7abae7" + "0" * 51, 1700000000)]
            return R()
        def close(self): pass

    hits = imp.parse_log(logfile)
    ok, missed = imp.resolve(hits, _Con())
    assert ok and all(r["offset_reached"] is None for r in ok), \
        "给不知道的 offset 编了个数 —— 编出来的和实测长得一模一样"
    assert len(missed) == 2, "对不上的必须出声计数,不许静默丢掉"


def test_unmatched_hits_are_reported_not_swallowed(logfile, capsys, monkeypatch):
    """对不上数据湖的记录要**出声**:静默丢弃就是又造一个盲区。"""
    import storage_engine as se
    monkeypatch.setattr(se, "duckdb_conn", lambda: _EmptyCon())
    monkeypatch.setattr(se, "write_truncations", lambda rows: None)
    monkeypatch.setattr(se, "TRUNCATIONS_DIR", Path("/nonexistent-for-test"))
    stats = imp.run(logfile, dry_run=True)
    assert stats["没对上"] == 3
    assert "找不到对应市场" in capsys.readouterr().out


class _EmptyCon:
    def execute(self, *a, **k): return self
    def executemany(self, *a, **k): return self
    def sql(self, q):
        class R:
            @staticmethod
            def fetchall(): return []
        return R()
    def close(self): pass


def test_dry_run_writes_nothing(logfile, monkeypatch):
    """--dry-run 必须真的不写 —— 「先看看」不能有副作用。"""
    import storage_engine as se
    wrote = []
    monkeypatch.setattr(se, "duckdb_conn", lambda: _EmptyCon())
    monkeypatch.setattr(se, "write_truncations", lambda rows: wrote.append(rows))
    monkeypatch.setattr(se, "TRUNCATIONS_DIR", Path("/nonexistent-for-test"))
    imp.run(logfile, dry_run=True)
    assert wrote == []


def test_does_not_reverse_engineer_holes_from_gaps():
    """⛔ 焊死"不许按开头空白反推被截断的市场"。

    那是拿一个**与结果相关**的量贴标签:真的开盘很久没人交易的市场会被一并冤枉,
    而这些标签之后会被当成实测事实使用。权威只能是日志里的因果现场。
    """
    import inspect
    src = inspect.getsource(imp)
    for banned in ("start_date", "datediff", "gap"):
        assert banned not in src.replace("# ", "").split('"""')[0] + \
            "".join(src.split('"""')[2:]), \
            f"导入脚本里出现了 {banned} —— 疑似在按空白反推,而不是读日志现场"
