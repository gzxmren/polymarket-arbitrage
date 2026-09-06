#!/usr/bin/env python3
"""回归基线:三处游标轮转「挑出了哪些」的行为对照(2026-08-07,重构前写下)。

## 这个文件是什么

它**不判断对错**,只钉住「现在是什么样」。用途只有一个:
把三份游标轮转合并成一份(`rotation.py`)之后,证明**挑选结果逐项没变**。

CLAUDE.md 铁律 4 要求「改共享引擎必须默认关闭 + 回归证明」。
本次与用户商定(2026-08-07):**省掉开关那一半,保留证明那一半**。
理由是留开关等于新旧两套游标实现同时埋在树里,而"同一件事两份实现悄悄分叉、
判据还不变红"正是 2026-08-06 刚花一天修掉的洞 2(两份 `_get`)——
为防一个坑挖回另一个坑,不划算。作为交换,这份基线必须先绿、改完还绿。

## ⭐唯一允许改变的行为(有意的,就是这次要修的 bug)

**游标推进得更近**:被时间闸/让路砍断时,游标只走到**真做过**的那一个,
而不是"这一片规划的最后一个"。所以本文件**只钉「挑了哪些」,不钉「游标记到哪」**——
钉后者就等于用判据把 bug 焊死,那是拿判据迁就代码。

游标推进规则的正确性判据在 `test_rotation.py`,不在这里。

## 📌 重构当天对本文件动过什么(留痕,便于事后复核我有没有放水)

三个函数的**返回类型**由本次重构有意改变(改成返回 `Rotation` 对象),
故本文件的**调用写法**跟着改了。但**期望值一个字符都没动** —— git diff 可逐行核对:
改的全是 `picked, _ = f(...)` → `picked = f(...).batch` 这类调用行。

唯二**删掉**的是两条断言:它们断言的是"规划的最后一个"当游标,
而那正是本次要修的 bug 本身。留着它们等于用判据把 bug 焊死。
删除位置在下方各自标了 `🗑`。

## 为什么用 `m000` 这种假 id 而不是真实的 0x… 哈希

期望值要**人手写得出来、看得懂**。用 64 位十六进制哈希的话,期望列表只能靠
"跑一遍把输出粘回来"生成 —— 那不叫对照,那叫把当前行为抄了一份,
连它是不是错的都看不出来。这里每一条期望都是照着"排序 → 游标之后切一片 →
不够就回卷"这句话手推的。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import backfill_closed_markets as bf  # noqa: E402
import collector_core as cc  # noqa: E402
import settlement_watcher as sw  # noqa: E402


def _cids(picked) -> list[str]:
    return [m["condition_id"] for m in picked]


# ============ 一、结算守望:排序键是 end_date 复合键,不是 condition_id ============

def _settlement_pending() -> list[dict]:
    """10 个待结算市场。m008/m009 **没有 end_date**。

    ⭐缺 end_date 的必须排**最后**(到期时间未知 = 最低优先级)。
    旧代码 `end_date or ""` 让它们排最前,是 2026-08-03 那次「真值断供 11 天」的直接根因
    —— 队头挤满了永远查不出结果的市场。这条性质在这里被钉住,重构不许把它弄丢。
    """
    rows = [{"condition_id": f"m{i:03d}",
             "end_date": None if i >= 8 else f"2026-01-{i + 1:02d}"}
            for i in range(10)]
    rows.sort(key=sw._sort_key)
    return rows


def test_settlement_sorted_order_puts_missing_end_date_last():
    assert _cids(_settlement_pending()) == [
        "m000", "m001", "m002", "m003", "m004", "m005", "m006", "m007",
        "m008", "m009"]


def test_settlement_first_round_takes_the_head():
    picked = sw.select_batch(_settlement_pending(), "", 4).batch
    assert _cids(picked) == ["m000", "m001", "m002", "m003"]


def test_settlement_resumes_after_the_cursor():
    pending = _settlement_pending()
    cursor = sw._sort_key(pending[3])          # 上一轮停在 m003
    picked = sw.select_batch(pending, cursor, 4).batch
    assert _cids(picked) == ["m004", "m005", "m006", "m007"]


def test_settlement_wraps_around_at_the_tail():
    pending = _settlement_pending()
    cursor = sw._sort_key(pending[7])          # 停在 m007,后面只剩 2 个
    picked = sw.select_batch(pending, cursor, 4).batch
    assert _cids(picked) == ["m008", "m009", "m000", "m001"]


def test_settlement_batch_never_exceeds_the_pool():
    picked = sw.select_batch(_settlement_pending(), "", 99).batch
    assert len(picked) == 10                   # 不许靠回卷把同一个取两次


# ============ 二、轮询:新鲜片 + 轮转片,两片合起来才是本轮名额 ============

# 30 个可轮询市场,**新鲜度顺序与 condition_id 顺序无关**(真实数据就是这样:
# "最近成交"和"ID 大小"毫不相干)。这一点很要命 —— 若测试里两者一致,
# "轮转片有没有按 cid 排序"在判据里完全看不出差别(2026-08-06 实测:去掉排序 17 条全绿)。
_FRESHEST_FIRST = ["m17", "m03"] + [f"m{i:02d}" for i in range(30)
                                    if f"m{i:02d}" not in ("m17", "m03")]


def _poll_markets() -> list[dict]:
    return [{"condition_id": c, "token_id_0": "1", "token_id_1": "2"}
            for c in _FRESHEST_FIRST]


def test_poll_rotate_share_is_still_24():
    """名额分配是基线的一部分:它变了,下面两条期望就都不成立了。"""
    assert cc.POLL_ROTATE_SHARE == 24


def test_poll_first_round_is_fresh_slice_then_rotation_from_the_top():
    """limit=26 ⇒ 新鲜片 2 个(最近成交的)+ 轮转片 24 个(剩下 28 个里按 cid 排序取前 24)。

    剩下的 28 个 = m00..m29 去掉 m17、m03 → 排序后取 24 个,即 m00..m25 去掉 m03、m17。
    """
    rest_sorted = [f"m{i:02d}" for i in range(30) if f"m{i:02d}" not in ("m17", "m03")]
    picked, _rot, skipped = cc.select_poll_targets(_poll_markets(), {}, 26, "")
    assert _cids(picked) == ["m17", "m03"] + rest_sorted[:24]
    assert skipped == 4                        # 30 个可轮询 - 26 个名额
    # 🗑 删掉的断言(原:`assert cursor == rest_sorted[23]`):它断言"游标 = 规划的
    #    最后一个",而这正是本次要修的 bug。现在游标只由 rot.commit() 给,
    #    而 commit() 要看真做过几个 —— 选批阶段根本还不知道。


def test_poll_rotation_wraps_while_fresh_slice_stays_put():
    """轮转片走到末尾要回卷;新鲜片不受游标影响,永远是最近成交的那几个。"""
    rest_sorted = [f"m{i:02d}" for i in range(30) if f"m{i:02d}" not in ("m17", "m03")]
    picked = cc.select_poll_targets(_poll_markets(), {}, 26, rest_sorted[26])[0]
    assert _cids(picked)[:2] == ["m17", "m03"]
    assert _cids(picked)[2:] == rest_sorted[27:] + rest_sorted[:23]


def test_poll_no_duplicates_between_the_two_slices():
    picked = cc.select_poll_targets(_poll_markets(), {}, 26, "")[0]
    assert len(set(_cids(picked))) == len(picked)


def test_poll_no_limit_means_everything_and_no_rotation():
    picked, _rot, skipped = cc.select_poll_targets(_poll_markets(), {}, None, "seed")
    assert _cids(picked) == _FRESHEST_FIRST     # 原样返回,连排序都不做
    assert skipped == 0
    # 🗑 删掉的断言(原:`assert cursor == "seed"`,即原样把游标写回去)。
    #    新行为:返回一个空轮转,commit() 恒为 None ⇒ 调用方**不写**游标文件,
    #    文件里的旧值原样留着。对外效果相同,少一次无谓写盘。
    assert _rot.commit() is None


# ============ 三、回填清扫:整批都给轮转,排序键是 condition_id ============

def _targets() -> list[dict]:
    # 故意乱序传入 —— select_batch 内部自己排序,调用方不负责
    order = [7, 2, 9, 0, 4, 1, 8, 3, 6, 5]
    return [{"condition_id": f"m{i:03d}", "token_id_0": "1", "token_id_1": "2"}
            for i in order]


def test_backfill_sorts_internally_and_takes_the_head():
    picked = bf.select_batch(_targets(), "", 3).batch
    assert _cids(picked) == ["m000", "m001", "m002"]


def test_backfill_resumes_after_the_cursor():
    picked = bf.select_batch(_targets(), "m004", 3).batch
    assert _cids(picked) == ["m005", "m006", "m007"]


def test_backfill_wraps_around_at_the_tail():
    picked = bf.select_batch(_targets(), "m008", 4).batch
    assert _cids(picked) == ["m009", "m000", "m001", "m002"]


def test_backfill_empty_target_set_is_not_an_error():
    """目标集空 = 全补完了,是**成功**不是故障。不许抛异常、不许推进游标。"""
    rot = bf.select_batch([], "m004", 3)
    assert rot.batch == [] and rot.commit() is None
