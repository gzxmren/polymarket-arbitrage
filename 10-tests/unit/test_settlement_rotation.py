#!/usr/bin/env python3
"""判据焊死:结算守望的轮转覆盖 + 批量查询不得丢已结算样本(2026-08-03)。

背景(实测,非推测):结算守望连续 11 天 `newly_resolved` 恒为 0,pending 单调涨到 37,782,
而注册表 51,044 个市场只有 504 个有结算真值。根因两层:

1. **队头阻塞**:`pending.sort(key=lambda r: r["end_date"] or "")` 把 610 个 end_date=None
   的市场(空串)顶到队头,而每轮只查前 80 个 → 名额永远消耗在同一批 None 上,其余 37,700
   个真正到期的市场一次都没被查过。且 None 恰恰是**最不可能结算**的(样本:2027 年的盘)。
2. **无轮转**:即使排序修好,队头仍会被"end_date 已过但永不关闭"的市场永久占据(实测队首
   `will-israel-launch-a-major-ground-offensive-in-gaz` end_date=2025-10-31 但 closed=False)。
   故必须有游标轮转,保证任何市场都不会被饿死。

3. **⭐最危险的一条**:Gamma `condition_ids=` 批量查询**默认只返回未关闭市场**。实测请求
   100 个返回 72 个,缺的 28 个抽样 8 个**全部**是 closed=True 且已结算(0.0/1.0)——
   即批量路径静默丢掉的恰恰是"我们唯一想要的那部分样本"。这是 CLAUDE.md 铁律第 2 条
   (绝不用与结果相关的变量筛样本)的原样重现。实测两遍(默认 + &closed=true)取并集
   覆盖 100/100、零重叠。**本文件把"必须两遍"焊死成判据。**

铁律:先写判据再改代码(见 CLAUDE.md)。断言直接 import 被检验对象的常量,不复制数字。
"""
import sys
from pathlib import Path

import pytest

# 11-collector 未在 conftest 路径里,本测试自带
COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import discovery_service as ds  # noqa: E402
import settlement_watcher as sw  # noqa: E402


def _row(cid, end_date, resolved=None, slug=None):
    return {"condition_id": cid, "end_date": end_date, "resolved_outcome": resolved,
            "slug": slug or f"slug-{cid}"}


# ---------- 1. end_date=None 不得占据队头 ----------

def test_none_end_date_sorts_last():
    """end_date 缺失 = 到期时间未知 → 最低优先级,绝不能排在真正已到期的市场前面。"""
    rows = [_row("c-none", None), _row("c-old", "2025-01-01T00:00:00Z"),
            _row("c-new", "2026-07-01T00:00:00Z")]
    ordered = sorted(rows, key=sw._sort_key)
    assert [r["condition_id"] for r in ordered] == ["c-old", "c-new", "c-none"], \
        "None 必须排最后;排到队头就是 11 天 0 产出的根因"


def _lap(rot) -> str | None:
    """把一片全部做完并提交,返回新游标。

    ⭐2026-08-07 起 `select_batch` 返回 `rotation.Rotation`:游标不再由"规划了哪些"给出,
    而是由"真做完了哪些"给出(报一个 `done()` 才算一个)。本轮转的核心规则见 rotation.py。
    这些判据关心的是"一圈能不能覆盖全部",故此处显式把整片做完 —— 与旧行为等价。
    """
    for r in rot.batch:
        rot.done(r)
    return rot.commit()


def test_many_none_do_not_block_head():
    """复现实测形态:610 个 None + 真到期市场 → 首轮切片必须查到真到期的。"""
    pending = [_row(f"n{i}", None) for i in range(610)]
    pending += [_row(f"d{i}", f"2025-1{i%2}-01T00:00:00Z") for i in range(100)]
    pending.sort(key=sw._sort_key)
    picked = sw.select_batch(pending, cursor="", max_check=80).batch
    assert all(not r["condition_id"].startswith("n") for r in picked), \
        "首轮不该把名额全花在 end_date=None 上(原 bug 正是如此)"


# ---------- 2. 轮转:任何市场都不得被饿死 ----------

def test_rotation_covers_everything():
    """连续跑满一圈,必须覆盖全部 pending —— 无饿死是本次修复的核心承诺。"""
    pending = sorted([_row(f"c{i:05d}", f"2025-01-01T00:00:{i%60:02d}Z")
                      for i in range(1000)], key=sw._sort_key)
    max_check, cursor, seen = 80, "", set()
    for _ in range((len(pending) // max_check) + 1):
        rot = sw.select_batch(pending, cursor, max_check)
        seen.update(r["condition_id"] for r in rot.batch)
        cursor = _lap(rot)
    assert len(seen) == len(pending), f"应覆盖全部 {len(pending)},实覆盖 {len(seen)}"


def test_rotation_advances_not_restart():
    """第二轮必须接着第一轮往后走,不能每轮都从头查同一批(原 bug 的本质)。"""
    pending = sorted([_row(f"c{i:05d}", f"2025-01-01T00:00:{i%60:02d}Z")
                      for i in range(500)], key=sw._sort_key)
    r1 = sw.select_batch(pending, "", 80)
    first = r1.batch
    second = sw.select_batch(pending, _lap(r1), 80).batch
    assert not ({r["condition_id"] for r in first} & {r["condition_id"] for r in second}), \
        "连续两轮不得重叠,否则就是原地打转"


def test_rotation_wraps_around():
    """游标走到末尾要回卷到开头,继续下一圈(持续复查:未结算的可能后来结算了)。"""
    pending = sorted([_row(f"c{i:03d}", f"2025-01-01T00:00:{i%60:02d}Z")
                      for i in range(100)], key=sw._sort_key)
    picked = sw.select_batch(pending, sw._sort_key(pending[-1]), 30).batch
    assert len(picked) == 30, "越过末尾必须回卷,而不是返回空"
    assert picked[0]["condition_id"] == pending[0]["condition_id"]


def test_cursor_survives_list_growth():
    """pending 会持续变长(新市场注册)。游标基于排序键而非下标,新增元素不得导致重头开始。"""
    pending = sorted([_row(f"c{i:05d}", f"2025-06-{(i%28)+1:02d}T00:00:00Z")
                      for i in range(300)], key=sw._sort_key)
    cur = _lap(sw.select_batch(pending, "", 80))
    grown = sorted(pending + [_row("newbie", "2025-06-01T00:00:00Z")], key=sw._sort_key)
    picked = sw.select_batch(grown, cur, 80).batch
    assert all(sw._sort_key(r) > cur for r in picked), "新元素插入不得让游标倒退重查"


def test_empty_pending_is_safe():
    """空 pending 不得崩,也不得让游标乱跳。"""
    rot = sw.select_batch([], "abc", 80)
    # commit() 为 None ⇒ 调用方不写游标 ⇒ 文件里的 "abc" 原样留着(旧写法是原样写回)
    assert rot.batch == [] and rot.commit() is None


# ---------- 3. ⭐批量查询必须两遍,否则丢的就是已结算样本 ----------

@pytest.fixture
def fake_gamma(monkeypatch):
    """模拟实测到的 Gamma 真实行为:condition_ids 默认**只返回未关闭**市场,
    已关闭(=已结算,我们唯一想要的)必须显式 &closed=true 才拿得到。"""
    open_m = {"conditionId": "c-open", "closed": False}
    closed_m = {"conditionId": "c-closed", "closed": True,
                "outcomePrices": '["1","0"]'}
    calls = []

    # **kw:替身的签名不该跟着被测对象逐个加参数 —— 2026-08-04 加 net=、08-06 加 deadline=
    # 都让这里红过一次。替身只该固定它**真正在验**的那部分(这里是 url)。
    def fake_get(url, **kw):
        calls.append(url)
        return [closed_m] if "closed=true" in url else [open_m]

    monkeypatch.setattr(ds, "_get", fake_get)
    return calls


def test_batch_lookup_must_do_both_passes(fake_gamma):
    """两遍取并集:少任何一遍,已结算市场就被静默丢掉(实测 100 个丢 28 个,全是已结算)。"""
    got = sw._batch_lookup_gamma(["c-open", "c-closed"])
    assert set(got) == {"c-open", "c-closed"}, \
        "必须两遍(默认 + &closed=true);只查默认 = 丢掉全部已结算样本 = 结果相关的丢样本"
    assert any("closed=true" in u for u in fake_gamma), "缺少 &closed=true 那一遍"


def test_batch_lookup_reports_missing_loudly(monkeypatch):
    """查不到的必须计数返回给调用方,不得静默吞掉(铁律 §3:任何剔除都要出声)。"""
    monkeypatch.setattr(ds, "_get", lambda url, **kw: [])
    got = sw._batch_lookup_gamma(["a", "b", "c"])
    assert got == {}, "查不到就是查不到,不得伪造"


def test_batch_size_within_probed_limit():
    """批量不得超过实测验证过的上限。

    2026-08-16 起判据从"条数 ≤ 100"改成"**URL 字节 ≤ 预算**",因为真正的硬限
    是 URL 长度不是条数:实测 100 个 = 8150 字节可过,110 个 = 8960 字节 → 422。
    写死 100 条时余量只剩 42 字节 —— 加一个查询参数就全线 422。
    新判据严格更强(条数变了它照样管用),完整一组见 test_gamma_batch_lookup.py。
    """
    cids = [f"0x{i:064x}" for i in range(1000)]
    for batch in ds.pack_condition_ids(cids):
        longest = max(ds.build_gamma_batch_url(batch, suf)
                      for suf in ds.GAMMA_CLOSED_SUFFIXES)
        assert len(longest) <= ds.GAMMA_URL_BUDGET_BYTES


# ---------- 4. 计数不静默 ----------

def test_watch_returns_full_counts(monkeypatch, tmp_path):
    """返回值必须含 pending/newly/lookup_fail/checked 四项,任一缺失都会让故障再次隐身
    ——本次 bug 潜伏 11 天,正因为 newly_resolved=0 没人把它当异常。"""
    # 测试隔离(项目约定):游标写 /tmp,绝不碰生产数据目录
    monkeypatch.setattr(sw, "CURSOR_FILE", tmp_path / "cursor.json")
    monkeypatch.setattr(sw, "load_registry", lambda: {
        "c1": _row("c1", "2025-01-01T00:00:00Z"),
    })
    monkeypatch.setattr(sw, "_batch_lookup_gamma", lambda cids, **kw: {})
    out = sw.watch_settlements(max_check=10)
    for k in ("pending_settlement", "newly_resolved", "lookup_fail", "checked"):
        assert k in out, f"计数缺 {k}"
    assert out["checked"] == 1 and out["lookup_fail"] == 1
