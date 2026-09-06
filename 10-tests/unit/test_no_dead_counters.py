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


# ---------- 反方向之二:算了但**没人读**(2026-08-17 立) ----------
#
# 由来:注册积压顶死那个 bug,证据(`pending_registration_dropped_count` /
# `pending_registration_count`)在心跳里躺了 **9 天**、每 15 分钟写一条,零个读取者;
# 最后是靠 grep 文本日志偶然发现的。同期粗筛:55 个字段里 25 个找不到任何读取者。
#
# 上面两条判据焊的是"声明了要有人算"和"落盘的要有人算";这条焊的是**再往前一步**:
# 算了、落盘了,**得有人真的看**。这三条合起来才是「记录事实 vs 使用事实」那条
# 项目铁律(标着"犯过 4 次",2026-08-17 是第 5 次)的可执行版本。
#
# ⚠️ "有人读"的定义**只算会做决定或会给人看的地方**:
#   alerts.py(决定推不推告警)/ collector_watchdog.py(同)/
#   daily_digest.py(推给人看)/ render_report.py(给人看)。
# 别的模块把某个键写进自己的 counts 不算读 —— 那是产出端,不是消费端。

CONSUMER_FILES = ("alerts.py", "collector_watchdog.py",
                  "daily_digest.py", "render_report.py")

# 明确豁免:必须写清**为什么**这个字段不需要读取者。空理由不许过。
# 这份名单是**棘轮**:新字段要么有读取者,要么在这里留下一句话,没有第三条路。
# 2026-08-17 当前为空 —— 日报的 `OTHER_FIELDS` 兜底读取者把 `AUDIT_FIELDS` 全覆盖了,
# 一个都不需要豁免。这是**目标状态**,不是巧合:新加字段默认就落进兜底那一行,
# 除非有人显式把它排除掉,那时才需要在这里留一句话。
# (`ts` / `dt` 不在 AUDIT_FIELDS 里 —— 前者由写入路径单独写,后者由分区目录合成,
#  故它们压根不进这条判据的射程,不该出现在豁免名单里。)
READER_EXEMPT: dict[str, str] = {}


def _reader_files(field: str) -> list[str]:
    q = "[\"']"
    pat = re.compile(rf"(get\(\s*{q}{re.escape(field)}{q}|\[{q}{re.escape(field)}{q}\])")
    out = []
    for name in CONSUMER_FILES:
        f = COLLECTOR_DIR / name
        if f.exists() and pat.search(f.read_text(encoding="utf-8")):
            out.append(name)
    return out


def _declared_consumed() -> set:
    """日报/报表用常量声明的消费清单(它们是遍历取用,不是逐个字面量取)。"""
    import sys as _s
    _s.path.insert(0, str(COLLECTOR_DIR))
    import daily_digest as dd
    import render_report as rr
    # STAGE_SECONDS 的读取者是 `_stage_seconds`(分段耗时那一行),不走兜底 ——
    # 它们永远非零,塞进"非零才显示"会让那行天天出现。
    return (set(dd.CONSUMED_FIELDS) | set(rr.CONSUMED_FIELDS)
            | set(dd.other_fields()) | set(dd.STAGE_SECONDS))


def test_every_heartbeat_field_has_a_reader():
    """⭐心跳里的每个字段,要么有人读,要么在豁免名单里写明为什么不用读。

    「如果这个字段现在恒为 0 是个故障,我会看到什么不同?」——
    没有读取者时答案是"完全没有不同"。那 9 天就是这么过去的。
    """
    consumed = _declared_consumed()
    orphans = [f for f in se.AUDIT_FIELDS
               if f not in consumed and f not in READER_EXEMPT and not _reader_files(f)]
    assert not orphans, (
        f"这些心跳字段算了、落盘了,但**没有任何地方读它**:{orphans}\n"
        f"给它一个读取者(进日报/页面/告警),或在 READER_EXEMPT 里写明为什么不用。\n"
        f"「记录事实 vs 使用事实,只接一头」——本项目已犯 5 次。")


def test_reader_exemptions_carry_a_real_reason():
    """豁免必须有理由,且理由不许是空话 —— 否则名单会变成静默的垃圾场。"""
    for field, why in READER_EXEMPT.items():
        assert field in se.AUDIT_FIELDS, f"豁免名单里的 {field} 已不在心跳里(名单烂了)"
        assert why and len(why) >= 8, f"{field} 的豁免理由太短,等于没写:{why!r}"


def test_a_new_field_is_automatically_covered_by_the_catch_all(monkeypatch):
    """⭐焊的是**机制**,不是当前状态。

    ## 这条判据的上一版是空的

    上一版扫"当前有没有孤儿"。但兜底读取者是 `AUDIT_FIELDS − 已消费` 派生出来的
    ⇒ 任何新字段自动落进兜底 ⇒ **孤儿在结构上不可能存在** ⇒ 那条判据永远不会红。
    我跑自检(往心跳里塞一个没人读的新字段)才发现它是空的 —— 一条永远绿的判据,
    正是本项目反复要消灭的假绿灯。

    所以改成焊那条**派生关系**本身:往 `AUDIT_FIELDS` 里塞一个字段,
    兜底必须自动把它收进去。谁哪天把它换成手工维护的清单,本条当场红。
    """
    import daily_digest as dd
    fake = "totally_fabricated_counter_xyz"
    assert fake not in dd.other_fields()
    monkeypatch.setattr(se, "AUDIT_FIELDS", tuple(se.AUDIT_FIELDS) + (fake,))
    monkeypatch.setattr(dd.se, "AUDIT_FIELDS", tuple(dd.se.AUDIT_FIELDS) + (fake,))
    assert fake in dd.other_fields(), (
        "新字段没有被兜底读取者自动收进去 ⇒ 有人把派生关系换成了手工清单,"
        "而手工清单必然漏 —— 那正是 9 天没人读那个 bug 的成因")


def test_the_reader_regex_actually_matches(monkeypatch):
    """⭐第二个自检:`_reader_files` 的正则本身没写坏。

    它若失效,上面那条"有读取者"的判据会靠豁免/兜底蒙混过关而不自知。
    """
    assert not _reader_files("totally_fabricated_counter_xyz")
    assert _reader_files("firehose_fail"), "已知有读取者的字段都匹配不到 ⇒ 正则坏了"

