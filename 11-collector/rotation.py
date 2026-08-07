#!/usr/bin/env python3
"""rotation.py — 游标轮转,全项目唯一一份(2026-08-07)。

## 为什么要有这个模块

同一个游标 bug 被写了三遍、分三次发现:结算守望(08-06 上午修)、
回填清扫(08-06 下午,**在当天新写的代码里又犯一遍**)、轮询(至今未修)。
三处的骨架完全一样:排序 → 二分定位游标 → 切一片 → 走到头回卷 → 记游标。

修三个实例没有用 —— 第四条链路还会写第四遍。这里修的是**那个类**。

## 核心规则:不报"做了什么",就推不动游标

    rot = Rotation(items, cursor, n)     # 取一批
    for it in rot.batch:
        if 时间到了: break                # 砍断
        做事(it)
        rot.done(it)                     # 做完一个报一个
    new_cursor = rot.commit()            # 只走到真报过的那个;没人报 → None

老写法 `cursor = 这一片的最后一个` 在这个接口下**没有对应的表达式** ——
模块只提供 `commit()` 一条出路,而它只读 `done()` 报上来的东西。

⚠️ 说清楚边界:`batch` 是公开的,谁想不开仍可以自己算 `key(rot.batch[-1])`
把老 bug 手工重造一遍。这里保证的是弱一些但真实的东西:**自然的写法就是对的写法**。
不许把它吹成"物理上不可能"。

## 空洞:保守规则(用户 2026-08-07 定)

报了 0-9 和 11-19、唯独第 10 个没报 → 游标停在 **9**,不跨过去。
理由:"没做成"往往不是随机的(那个市场每次都超时),跨过去 = 它每圈都被跳过
= 永远采不到,且**丢得与结果相关** —— 本项目 2026-07-15 的死因就是这个。
代价是 11-19 下轮白做一次,可接受。

保守规则的反面风险是**游标卡死**(那一个每轮都做不成,后面全饿死),
故 `holes` 必须被调用方读出来接告警 —— 卡死的症状就是同一个空洞每轮都在。

判据:`10-tests/unit/test_rotation.py`;重构前的行为基线:`test_rotation_baseline.py`。
"""
from __future__ import annotations

import bisect
from typing import Any, Callable, Iterable


def _default_key(item: Any) -> str:
    return item["condition_id"]


class Rotation:
    """按游标取下一片 + 回卷;游标只走过 `done()` 报过的那些。

    `items` 不需要预先排序 —— 内部按 `key` 排。游标存的是**排序键**而不是下标:
    池子每轮都在变(补完的消失、新关闭的加入),存下标会乱跳。
    这条是 2026-08-03 事故验证过的,不许改回去。
    """

    def __init__(self, items: Iterable[Any], cursor: str | None, n: int,
                 key: Callable[[Any], str] = _default_key) -> None:
        self._key = key
        ordered = sorted(items, key=key)
        keys = [key(it) for it in ordered]
        n = max(0, min(n, len(ordered)))
        i = bisect.bisect_right(keys, cursor or "")
        batch = ordered[i:i + n]
        if len(batch) < n:                 # 回卷:不然队尾永远轮不到(不变量 A2)
            batch += ordered[:n - len(batch)]
        self.batch: list[Any] = batch
        self._keys = [key(it) for it in batch]
        # ⭐键必须唯一,否则**游标会永久卡死**(2026-08-07 复核查出,当时三个调用方都碰不到):
        # 下标表把同键的多个项塌成最后一个,于是靠前那几个位置**再也没有对象能标记它** ——
        # 连续前缀永远停在它前面,holes 每轮都非 0,而告警会指挥人去找一个"每轮都失败的
        # 市场",那个市场根本不存在。排查方向被带偏,比不报警更糟。
        #
        # 故这里**响亮地炸**而不是悄悄去重:三个现有调用方的目标集都是 dict 或 SQL
        # row_number() 去过重的,喂进重复键属于新调用方的编程错误,越早越吵越好。
        # 崩掉的消费者是现成的:采集器崩 ⇒ 不写心跳 ⇒ 看门狗告警。
        if len(set(self._keys)) != len(self._keys):
            raise ValueError(
                f"轮转的排序键必须唯一,本片 {len(self._keys)} 项里只有 "
                f"{len(set(self._keys))} 个不同的键 —— 请先给目标集去重(重复键会让游标永久卡死)")
        self._pos = {k: p for p, k in enumerate(self._keys)}
        self._reported: set[int] = set()

    def done(self, item: Any) -> None:
        """报告"这个我真处理过了"。不属于本片的一律忽略。

        ⭐"处理过"= **轮到了并且真的动手了**,不是"成功了"。三处现在都这么接:
        结算查 Gamma 没查到的照报、回填网络截断没落成标记的照报、轮询零成交的照报。

        为什么不是"成功才报":那会把**持续失败的那一个**变成路障 —— 游标永远停在它
        前面,后面全饿死。而"成功了没有"另有各自的机制在管(结算靠 pending 集合、
        回填靠 swept 标记、轮询靠水位线),失败的下一圈自然还会回来。
        轮转只负责一件事:**保证每个都能在有界时间内轮到**(不变量 A2)。

        忽略不属于本片的是给轮询层用的:它把**新鲜片**的市场也一起报进来
        (它不知道谁属于轮转片),而新鲜片跟游标毫无关系,混进去会污染游标。
        """
        p = self._pos.get(self._key(item))
        if p is not None:
            self._reported.add(p)

    def _last_contiguous(self) -> int:
        """报过的**连续前缀**的最后一个下标;一个都没报(或第一个就没报)→ -1。"""
        last = -1
        while last + 1 < len(self._keys) and (last + 1) in self._reported:
            last += 1
        return last

    def commit(self) -> str | None:
        """新游标 = 报过的连续前缀的最后一个;一个都没报 → None(原地不动)。

        返回 None 时调用方**不许写游标**:那意味着这一批整个留给下轮。
        照旧往前写等于"什么都没干却宣布这批过去了"。
        """
        last = self._last_contiguous()
        return self._keys[last] if last >= 0 else None

    @property
    def holes(self) -> int:
        """越过空洞报上来的个数 —— 稳态恒 0,调用方须读它接告警。

        ⚠️ 做成 property 而不是 `commit()` 的副作用:副作用版有个静默陷阱 ——
        谁先读 holes 再 commit(),会拿到 0 而毫不知情。
        这类"记录了,但读的时机不对所以永远是 0"正是本项目反复发作的形状,
        能从结构上消掉就别靠调用方记得顺序。
        """
        last = self._last_contiguous()
        return sum(1 for p in self._reported if p > last)
