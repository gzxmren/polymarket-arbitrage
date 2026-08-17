#!/usr/bin/env python3
"""判据焊死:声明过的计数字段必须真的有人在算(2026-08-06)。

## 由来

`dedup_collapse_count` 在 `COUNTER_KEYS` 和 `AUDIT_FIELDS` 里都声明了,
每轮心跳老实写一个 0 —— 而**全项目没有任何一行代码给它加过 1**。
它从 2026-07-22 起就是个死字段,直到今天靠人手工 grep 才发现。

**死字段和"这轮真的是 0"长得一模一样。** 这正是本项目的核心病症:
一个恒为 0 的量,既可能是"系统很健康",也可能是"这个量根本没在被测量",
而看的人分辨不出来。

## 已有的两条判据为什么没抓住

它们焊的是「**声明**过的都落到心跳」和「run_cycle 算出来的都落到心跳」,
方向都是"别丢";**没有一条问过"心跳里的字段,真的有人在算吗"**。
少焊了一个方向,于是死字段可以安然存在。

## 本判据怎么判

在 `11-collector/` 全部源码里找"这个字段名被写入过"的证据:
`counters["x"] += 1` / `bump(counters, "x")` / `counts["x"] = ...` / `_set(...)` 等。
只要字段名在**赋值或累加的位置**出现过就算数;只在声明元组里出现的,不算。

⚠️ 这是**文本级**的判断,精度有限。

⭐ 一个当场的教训:本文件第一版这里写着"假阴性的方向是漏报,不会误伤好字段"——
**那句话写下去几分钟就被自己证伪了**:`offset_overflow_cold_count` 是用
`counters[f"offset_overflow_{mode}_count"] += 1` 动态拼出来的 key,
于是被误判成死字段。**我又一次用陈述的语气写了一个没验过的推断。**
现已支持 f-string 拼接的 key(把 `{...}` 当通配),并把这段留在这里当记录。
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import collector_core as cc  # noqa: E402
import storage_engine as se  # noqa: E402

# 这些字段不是"被累加的计数器",而是整轮末尾由 run_cycle 直接组装的量
# (耗时、连零计数、结算段的三个数)。它们有各自的判据盯着,不走本判据。
ASSEMBLED_BY_RUN_CYCLE = {
    "cycle_seconds", "discovery_seconds", "poll_seconds",
    "settlement_seconds", "compaction_seconds",
    "register_zero_streak", "truth_supply_zero_streak", "slow_cycle_streak",
    # 成交流断供连计与恢复时长(2026-08-17):与上面三个连计同一形状 ——
    # 由 run_cycle 在主干上算好、只出现在 `merged` 字典字面量里,故不匹配"赋值/累加"模式。
    # 读取者:alerts(分「单轮抖动」与「持续断供」,并推恢复总结)。
    "trade_flow_outage_streak", "trade_flow_outage_recovered",
    "newly_resolved", "settlement_lookup_fail", "settlement_checked",
    "settlement_timegate_skipped_count",
    # 结算守望里叫 rotation_holes,run_cycle 组装时加前缀区分于轮询那一个
    # (两处个数必须分开留痕:处置方向不同 —— 轮询查时间闸/网络,结算查 Gamma 批量查询)
    "settlement_rotation_holes",
    "new_trades", "new_registered", "new_discovered", "register_fail",
    "firehose_fail",
}


def _source_of_collector() -> str:
    """11-collector 下全部源码拼一起(判据文件本身不算)。"""
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(COLLECTOR_DIR.glob("*.py")))


# 形如 counters[f"offset_overflow_{mode}_count"] 的动态 key:
# 把 `{...}` 当通配符,看字段名能不能被它生成出来。
_FSTRING_KEY_RE = re.compile(r'\[\s*f"([^"]+)"\s*\]')


def _matches_a_dynamic_key(field: str, src: str) -> bool:
    for tmpl in _FSTRING_KEY_RE.findall(src):
        pat = "^" + re.sub(r"\\\{[^}]*\\\}", r"\\w+", re.escape(tmpl)) + "$"
        if re.match(pat, field):
            return True
    return False


def _is_written_somewhere(field: str, src: str) -> bool:
    """这个字段名是否在**赋值/累加的位置**出现过(而不是只出现在声明元组里)。"""
    patterns = [
        rf'\[\s*"{re.escape(field)}"\s*\]\s*(\+?=)',      # counters["x"] += / =
        rf'bump\s*\(\s*\w+\s*,\s*"{re.escape(field)}"',    # bump(counters, "x")
        rf'_bump\w*\s*\(\s*\w+\s*,\s*"{re.escape(field)}"',
        rf'_set\s*\(\s*\w+\s*,\s*"{re.escape(field)}"',
        rf'\.get\(\s*"{re.escape(field)}"\s*,\s*0\s*\)\s*\+',  # 读改写
    ]
    return any(re.search(p, src) for p in patterns) or _matches_a_dynamic_key(field, src)


def test_every_declared_counter_is_actually_incremented():
    """`COUNTER_KEYS` 里的每个字段,都必须在某处真的被写过。

    声明了却没人算 = 心跳里多一个永远为 0 的字段,而它和"健康的 0"无法区分。
    """
    src = _source_of_collector()
    dead = [k for k in cc.COUNTER_KEYS if not _is_written_somewhere(k, src)]
    assert not dead, (
        f"这些计数器声明了却从没被写过(死字段,心跳里恒为 0 且与健康的 0 无法区分):{dead}\n"
        f"要么把它接上(真的去算),要么把声明删掉 —— 不许留着装样子。")


def test_every_heartbeat_field_is_actually_computed():
    """心跳白名单 `AUDIT_FIELDS` 里的每个字段,要么被累加过,要么由整轮末尾组装。

    两条既有判据焊的都是"别丢"(算了要落盘);这一条焊的是**反方向**:
    落盘的必须真有人算。少焊这个方向,死字段就能安然存在。
    """
    src = _source_of_collector()
    dead = [k for k in se.AUDIT_FIELDS
            if k not in ASSEMBLED_BY_RUN_CYCLE and not _is_written_somewhere(k, src)]
    assert not dead, (
        f"这些心跳字段没有任何地方在算它:{dead}\n"
        f"心跳里一个恒为 0 的字段,分不出是'系统健康'还是'压根没在测'。")


def test_the_exemption_list_does_not_rot():
    """豁免名单本身也会烂:字段改名/删掉之后,豁免项会变成永远没人管的死条目。

    ⭐ 这条是防"守护自己坏掉"—— 本项目已经栽过一次
    (`config.DBTables` 全项目 0 引用,却一直当权威常量留着)。
    """
    stale = [k for k in ASSEMBLED_BY_RUN_CYCLE if k not in se.AUDIT_FIELDS]
    assert not stale, f"豁免名单里有心跳中已不存在的字段(名单烂了):{stale}"
