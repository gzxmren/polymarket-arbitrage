#!/usr/bin/env python3
"""判据焊死:游标轮转只有一份实现,且「不报做了什么就推不动游标」(2026-08-07)。

## 由来(实测,非假想)

同一个游标 bug 被写了三遍、分三次发现、分三次修:

| 何时 | 何处 | 症状 |
|---|---|---|
| 08-06 上午 | 结算守望 | 时间闸砍断后游标照走到"规划的最后一个" |
| 08-06 下午 | 回填清扫 | **同一个 bug 在我当天新写的代码里又犯一遍** |
| 至今未修 | 轮询 | 同上 |

打个比方:银行叫号一次叫 400 人进来,下班只办完 240 个,剩下的被请出去,
**而叫号机照样跳到 400 号** —— 他们的号作废,明天重新排队尾。
实测危害:回填清扫一圈只覆盖目标集的约六成,清存量的时间因此多出六成。

修三个实例没有用 —— 第四条链路(第 3 步要拆的四条腿)还会写第四遍。
**这次修的是那个类**:抽一份共用的 `rotation.py`,让"游标只走过真做过的"
成为结构上的默认写法。

## ⭐验收标准(用户 2026-08-06 定,逐字沿用)

> **用这个新接口,还写得出原来那个 bug 吗?**

判据组 A 就是这一问的可执行版本。

## 本测试**不能**回答什么

1. **不保证老 bug 在物理上无法写出。** `Rotation.batch` 是公开的,
   谁想不开仍可以自己算 `key(rot.batch[-1])` 把老 bug 手工重造一遍。
   这里保证的是弱一些但真实的东西:**自然的写法就是对的写法**,
   而重造老 bug 需要绕开模块**故意不提供**的东西。这个差别要说清楚,不许吹成"不可能"。
2. **不回答三处的批大小/时间闸/过滤条件对不对。** 那些留在各自模块,不属本次。
3. **不回答"一圈要多久"。** 那取决于名额与周期,判据在 `test_poll_rotation.py`。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import alerts  # noqa: E402
import cycle_state  # noqa: E402
import rotation  # noqa: E402


def _items(n: int, prefix: str = "m") -> list[dict]:
    return [{"condition_id": f"{prefix}{i:03d}"} for i in range(n)]


def _cids(rows) -> list[str]:
    return [r["condition_id"] for r in rows]


# ============ 组 A:老 bug 写不出来(核心) ============

def test_cursor_stops_at_the_last_reported_not_the_last_planned():
    """⭐这一条就是那个 bug 本身。规划 40 个、只做完 12 个 → 游标必须停在第 12 个。"""
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    assert len(rot.batch) == 40
    for m in rot.batch[:12]:
        rot.done(m)
    assert rot.commit() == "m011"        # 不是 "m039"


def test_next_round_resumes_at_the_first_one_never_touched():
    """被砍掉的尾巴下一轮**立刻**接着做,而不是等一整圈。"""
    r1 = rotation.Rotation(_items(100), cursor="", n=40)
    for m in r1.batch[:12]:
        r1.done(m)
    r2 = rotation.Rotation(_items(100), cursor=r1.commit(), n=40)
    assert _cids(r2.batch)[0] == "m012"


def test_nothing_reported_leaves_the_cursor_untouched():
    """一个都没做成(比如开工前就让路)→ 整批留给下轮,不许宣布"这批过去了"。"""
    rot = rotation.Rotation(_items(100), cursor="m004", n=40)
    assert rot.commit() is None


def test_reporting_the_whole_batch_advances_to_its_end():
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    for m in rot.batch:
        rot.done(m)
    assert rot.commit() == "m039"


def test_the_module_does_not_hand_out_the_planned_last_key():
    """老 bug 的写法在新接口下**没有对应的表达式**。

    模块只提供 `commit()` 一条出路,而它只读 `done()` 报上来的东西。
    这条判据焊住的是"别哪天为了方便又加个 `planned_cursor` 属性回来"。
    """
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    public = {a for a in dir(rot) if not a.startswith("_")}
    assert public == {"batch", "done", "commit", "holes"}


# ============ 组 B:空洞用保守规则(用户 2026-08-07 定) ============

def test_a_hole_stops_the_cursor_in_front_of_it():
    """做了 0-9 和 11-19,**第 10 个没做** → 游标停在第 9 个。

    保守规则的理由:"没做成"往往不是随机的(比如那个市场每次都超时)。
    跨过去 = 它每一圈都被跳过 = **永远采不到**,且丢得与结果相关 —— 本项目的死因。
    代价是 11-19 下轮白做一次,可接受。
    """
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    for i, m in enumerate(rot.batch[:20]):
        if i != 10:
            rot.done(m)
    assert rot.commit() == "m009"


def test_the_hole_is_counted_out_loud():
    """空洞必须出声 —— 保守规则的反面风险是**游标卡死**(那一个每轮都做不成)。

    卡死的症状就是同一个空洞每轮都在,故这个计数同时是"卡死"的探测器。
    """
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    for i, m in enumerate(rot.batch[:20]):
        if i != 10:
            rot.done(m)
    rot.commit()
    assert rot.holes == 9                 # 越过空洞报上来的那 9 个


def test_a_hole_at_the_head_means_no_advance_at_all():
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    for m in rot.batch[1:5]:
        rot.done(m)
    assert rot.commit() is None
    assert rot.holes == 4


def test_holes_reads_the_same_before_and_after_commit():
    """读 holes 不许依赖调用顺序。

    最初 holes 是 `commit()` 的副作用 —— 谁先读 holes 再 commit(),会拿到 0 而毫不知情。
    "记录了,但读的时机不对所以永远是 0"正是本项目反复发作的形状,故从结构上消掉。
    """
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    for i, m in enumerate(rot.batch[:20]):
        if i != 10:
            rot.done(m)
    before = rot.holes                    # 故意在 commit 之前读
    assert before == 9 == rot.holes and rot.commit() == "m009"


def test_steady_state_has_zero_holes():
    """今天三处的循环都是"做一个报一个,中断即中断" ⇒ 空洞恒为 0。

    这是告警防洪的另一头:稳态完全静默。它不是 0 了,说明有人加了新的跳过路径。
    """
    rot = rotation.Rotation(_items(100), cursor="", n=40)
    for m in rot.batch[:25]:
        rot.done(m)
    rot.commit()
    assert rot.holes == 0


# ============ 组 C:轮转的老性质一条都不许丢 ============

def test_wraps_around_at_the_tail():
    """走到末尾要回卷,否则后半段永远轮不到(不变量 A2)。"""
    rot = rotation.Rotation(_items(10), cursor="m008", n=4)
    assert _cids(rot.batch) == ["m009", "m000", "m001", "m002"]


def test_wrapping_never_takes_the_same_item_twice():
    rot = rotation.Rotation(_items(10), cursor="m004", n=99)
    assert len(rot.batch) == 10 == len(set(_cids(rot.batch)))


def test_cursor_is_a_sort_key_so_a_growing_pool_does_not_rewind():
    """游标存**排序键**不存下标:池子每轮都在变,存下标会乱跳(08-03 事故验证过)。"""
    rot = rotation.Rotation(_items(10), cursor="m004", n=3)
    for m in rot.batch:
        rot.done(m)
    grown = _items(10) + [{"condition_id": "m00" + s} for s in ("a", "b")]
    nxt = rotation.Rotation(grown, cursor=rot.commit(), n=3)
    assert _cids(nxt.batch) == ["m008", "m009", "m00a"]


def test_one_lap_reaches_everyone(  ):
    """不变量 A2:第 N+1 个的等待必须有界 —— 一圈之内人人被取到,一个不落。"""
    pool, seen, cursor = _items(50), set(), ""
    for _ in range(10):                   # 50 个 / 每轮 5 个 = 10 轮整一圈
        rot = rotation.Rotation(pool, cursor=cursor, n=5)
        for m in rot.batch:
            rot.done(m)
        seen |= set(_cids(rot.batch))
        cursor = rot.commit()
    assert seen == set(_cids(pool))


def test_a_custom_sort_key_is_honoured():
    """结算守望的排序键是 end_date 复合键,不是 condition_id —— 必须能换。

    且缺 end_date 的排**最后**(到期未知 = 最低优先级)。让它排最前正是
    2026-08-03「真值断供 11 天」的直接根因:队头挤满永远查不出结果的市场。
    """
    rows = [{"cid": "a", "end": "2026-03-01"}, {"cid": "b", "end": None},
            {"cid": "c", "end": "2026-01-01"}]
    key = lambda r: f"{'1' if not r['end'] else '0'}|{r['end'] or ''}|{r['cid']}"
    rot = rotation.Rotation(rows, cursor="", n=3, key=key)
    assert [r["cid"] for r in rot.batch] == ["c", "a", "b"]


def test_items_outside_the_batch_are_ignored_when_reported():
    """轮询层会把**新鲜片**的市场也报进来(它不知道谁属于轮转片)。

    不属于本片的一律忽略 —— 否则新鲜片会污染游标,而它跟游标毫无关系。
    """
    rot = rotation.Rotation(_items(10), cursor="", n=3)
    rot.done({"condition_id": "m099"})    # 根本不在池子里
    assert rot.commit() is None


def test_duplicate_keys_blow_up_loudly_instead_of_wedging_the_cursor():
    """⭐重复键会让游标**永久卡死**,故必须当场炸,不许悄悄去重。

    机理:下标表把同键的多个项塌成最后一个 ⇒ 靠前那几个位置再也没有对象能标记它
    ⇒ 连续前缀永远停在它前面 ⇒ holes 每轮非 0,而告警会指挥人去找一个"每轮都失败的
    市场" —— 那个市场根本不存在。**把排查方向带偏,比不报警更糟**。

    今天三个调用方的目标集都已去重(registry 是 dict、回填 SQL 用 row_number),
    碰不到这条。它焊的是**第四个调用方** —— 而模块头就写着还会有第四条链路。
    """
    dup = [{"condition_id": "m000"}, {"condition_id": "m001"}, {"condition_id": "m000"}]
    with pytest.raises(ValueError, match="唯一"):
        rotation.Rotation(dup, cursor="", n=3)


def test_an_empty_pool_is_not_an_error():
    """池子空 = 活全干完了,是**成功**不是故障:不抛异常、不推进游标。"""
    rot = rotation.Rotation([], cursor="m004", n=5)
    assert rot.batch == [] and rot.commit() is None


# ============ 组 D:结构判据 —— 不许再手写第四份 ============

def test_nobody_else_hand_rolls_a_rotation():
    """⭐"修了类"的可执行证明:全 11-collector 只有 rotation.py 能 `import bisect`。

    谁想再手写一份轮转,这条判据当场变红。没有它,"以后不会再犯"只是一句承诺,
    而本项目已经证明:同一个形状我会在同一天里犯第二次。

    ⚠️ 这是**代理指标**,不是证明:硬要用线性扫描手写一份轮转,它抓不到。
    它挡的是"照着旧代码再抄一遍"这条实际发生过的路径(三次全是二分+切片+回卷)。
    """
    offenders = sorted(
        p.name for p in COLLECTOR_DIR.glob("*.py")
        if p.name != "rotation.py" and "import bisect" in p.read_text())
    assert offenders == []


# ============ 组 E:空洞计数有人读(防洪两头都要焊死) ============

def test_main_cycle_alerts_only_when_holes_persist():
    """单发不推(可能是新代码上线的一次性抖动),连续 N 轮才推、之后按整数倍复述。"""
    assert alerts.ROTATION_HOLE_CYCLES == 3
    for streak in range(1, alerts.ROTATION_HOLE_CYCLES):
        assert alerts.build_alert({"rotation_hole_streak": streak}) is None
    body = alerts.build_alert({"rotation_hole_streak": alerts.ROTATION_HOLE_CYCLES})
    assert body is not None and "游标" in body


def test_main_cycle_is_silent_in_steady_state():
    """防洪的另一头:空洞恒 0 ⇒ 永不打扰。"""
    assert alerts.build_alert({"rotation_hole_streak": 0}) is None


def test_hole_streak_advances_and_resets():
    """复用现成的 next_hit_streak,不另抄一份连计规则(照抄结构已犯过 3 次)。"""
    assert cycle_state.next_hit_streak(2, hit=True) == 3
    assert cycle_state.next_hit_streak(9, hit=False) == 0


# ---- 回填清扫是**独立链路**,分开判定,不许把计数与主周期相加 ----

def test_backfill_link_has_its_own_alert_path():
    """由来:实测 backfill_closed_streak.json 全仓库**除它自己外无人读** ——
    回填清扫彻底扫不动时不会有任何通知(已知形状第 5 次:记录事实与使用事实只接一头)。
    """
    assert callable(alerts.maybe_alert_backfill)


def test_backfill_zero_streak_threshold_has_measured_backing():
    """阈值必须有实测分布支撑(CLAUDE.md)。

    实测(A4 上线后 61 轮 backfill.log):有活干的 53 轮,swept_marked
    中位 213 / p90 272 / max 281;**最长自然连零 2 轮**(今早那次断网)。
    取 8 轮(2 小时)≈ 4 倍余量。
    ⚠️ 样本仅约 15 小时,属**保守初值**,streak 已逐轮入日志,攒够一周应回来校准。
    """
    MEASURED_LONGEST_NATURAL_ZERO_STREAK = 2
    assert alerts.BACKFILL_ZERO_CYCLES >= 2 * MEASURED_LONGEST_NATURAL_ZERO_STREAK


def test_backfill_alerts_on_supply_outage_but_not_on_a_natural_blip():
    below = alerts.BACKFILL_ZERO_CYCLES - 1
    assert alerts.build_backfill_alert({"zero_streak": below}) is None
    body = alerts.build_backfill_alert({"zero_streak": alerts.BACKFILL_ZERO_CYCLES})
    assert body is not None and "回填" in body


def test_backfill_alerts_on_persistent_holes_too():
    assert alerts.build_backfill_alert({"rotation_hole_streak": 1}) is None
    body = alerts.build_backfill_alert(
        {"rotation_hole_streak": alerts.ROTATION_HOLE_CYCLES})
    assert body is not None and "游标" in body


def test_backfill_is_silent_when_healthy():
    """稳态:有活干、扫得动、无空洞 ⇒ 一条都不推。"""
    assert alerts.build_backfill_alert(
        {"zero_streak": 0, "rotation_hole_streak": 0, "swept_marked": 213}) is None


def test_backfill_no_work_left_is_success_not_failure():
    """目标集补完 ⇒ 没活可干 ⇒ next_zero_streak 保持不变,不进位、不误报。

    误报会让真信号无处可显 —— 降噪是可靠性工作,不是体验优化。
    """
    assert cycle_state.next_zero_streak(5, newly=0, attempted=0) == 5
