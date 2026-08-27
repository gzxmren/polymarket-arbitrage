#!/usr/bin/env python3
"""判据焊死:`run_once` 整条路径至少被走过一次(2026-08-04)。

## 由来(我自己当场犯的)

把轮询循环从 `run_once` 里抽成 `poll_markets` 之后,日志行里还留着
`f"新成交 {total_written}"` —— 那个局部变量已经跟着搬走了。
`NameError` 在**真机跑一整轮之后**才炸,而当时:

- 212 条单元测试**全绿**
- `py_compile` 也过(NameError 是运行期的,不是语法错)

因为**没有任何一条测试真的调用过 `run_once`**。它是整个采集器的主干函数,
却是测试覆盖的空洞 —— 各个零件都测得很细,组装起来那一下没人看。

对着那句话:**「如果它现在就是坏的,我看到的会有什么不同?」**
答案曾经是"没有不同"(全绿),直到真机跑了一轮。那不是运气,是这条判据缺席。

## 本测试**不能**回答什么

不验证任何业务正确性 —— 网络全被替身挡掉,没有真数据。
它只回答一件事:**这条路径能从头走到尾吗**。这类"冒烟"判据便宜、
抓的是重构断线这种低级但致命的错,与细粒度判据互补,不能互相替代。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import collector_core as cc  # noqa: E402


@pytest.fixture
def _offline(monkeypatch, tmp_path):
    """把所有出网与落盘的口子堵上,只留控制流。"""
    monkeypatch.setattr(cc, "refresh_and_registry",
                        lambda **kw: ([{"condition_id": "0x" + "ab" * 32,
                                        "token_id_0": "1", "token_id_1": "2"}],
                                      {"firehose_trades": 10, "active_cids": 1,
                                       "new_discovered": 1, "new_registered": 1,
                                       "register_fail": 0, "pollable": 1,
                                       "firehose_newest_ts": 123}))
    monkeypatch.setattr(cc, "poll_market", lambda m, c, wm: [])
    monkeypatch.setattr(cc.se, "duckdb_conn", lambda: _FakeCon())
    monkeypatch.setattr(cc.se, "all_watermarks", lambda con: {})
    monkeypatch.setattr(cc.se, "write_trades", lambda rows, day=None, counts=None: None)
    monkeypatch.setattr(cc, "REGISTER_STREAK_FILE", tmp_path / "reg.json")
    monkeypatch.setattr(cc, "FIREHOSE_WM_FILE", tmp_path / "wm.json")
    # ⚠️ 2026-08-06 实测踩到:新增 POLL_CURSOR_FILE 后忘了在这里重定向,
    # 跑一次单测就把**生产**的 data/state/poll_cursor.json 写成了 {"cursor": ""}
    # (= 把线上轮询轮转位置重置回开头)。测试隔离是硬规矩,不是习惯。
    monkeypatch.setattr(cc, "POLL_CURSOR_FILE", tmp_path / "poll_cursor.json")
    monkeypatch.setattr(cc.time, "sleep", lambda *_: None)


class _FakeCon:
    def close(self):
        pass


def test_run_once_completes_and_returns_counters(_offline):
    """⭐ 这一条就是当时缺的那条:整条主干走通,不留未定义的名字。"""
    counters = cc.run_once(limit=5, sample=1000, max_new=1)
    assert isinstance(counters, dict)
    assert counters["new_trades"] == 0


def test_run_once_fills_every_declared_counter(_offline):
    """声明了却从没被赋值的计数 = 心跳里永远是 0 的字段 = 假绿灯。

    (COUNTER_KEYS 里每一个都必须在真跑一轮后存在,不能靠下游 `.get(k, 0)` 兜底 ——
     兜底会让"真的是 0"和"根本没算"长得一模一样。)
    """
    counters = cc.run_once(limit=5, sample=1000, max_new=1)
    missing = [k for k in cc.COUNTER_KEYS if k not in counters]
    assert not missing, f"这些计数跑完一轮后仍不存在: {missing}"


def test_run_once_advances_firehose_watermark(_offline, tmp_path):
    """watermark 必须真被推进,否则下轮又从头翻(采样白做,且缺口计数失真)。"""
    import cycle_state
    cc.run_once(limit=5, sample=1000, max_new=1)
    assert cycle_state.read_state(cc.FIREHOSE_WM_FILE, "newest_ts", None) == 123


def test_watermark_not_advanced_when_sampling_came_back_empty(monkeypatch, _offline, tmp_path):
    """⭐采空(网络挂了)时**不许**推进 watermark。

    推进了就等于把"网络坏掉的那几分钟"永久跳过 —— 而网络坏的时段很可能不随机,
    那正是"丢得与结果相关"的经典造法(CLAUDE.md 铁律 2)。
    """
    import cycle_state
    monkeypatch.setattr(cc, "refresh_and_registry",
                        lambda **kw: ([], {"firehose_trades": 0, "active_cids": 0,
                                           "new_discovered": 0, "new_registered": 0,
                                           "register_fail": 0, "pollable": 0,
                                           "firehose_newest_ts": None}))
    cycle_state.write_state(cc.FIREHOSE_WM_FILE, "newest_ts", 999, "test")
    cc.run_once(limit=5, sample=1000, max_new=1)
    assert cycle_state.read_state(cc.FIREHOSE_WM_FILE, "newest_ts", None) == 999
