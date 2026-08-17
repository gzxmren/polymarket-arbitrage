#!/usr/bin/env python3
"""daily_digest.py — 每日日报:给心跳里那 25 个没人读的字段找个读者(2026-08-17)。

## 由来(实测,非假想)

2026-08-17 修的「注册积压顶死、每天丢弃几千个市场」,证据是心跳里两个字段
(`pending_registration_count` / `pending_registration_dropped_count`)。它们
2026-08-05 就加进心跳了,每 15 分钟写一条 —— 从 08-08 起「顶死 2000 + 天天丢弃」
清清楚楚摆在那里 **9 天**,零个读取者;最后是靠 grep 文本日志偶然发现的。
同期粗筛:心跳 55 个字段里 **25 个找不到任何读取者**。

⇒ 缺的不是又一个采集器、又一个字段,是**有人回头看**。

## 为什么是「推」不是「网页」

本项目建过一次监控网页(2026-03-20 提交「Phase 3: 质量监控与自动化」)。实测:
两个 systemd 单元 disabled+inactive、journal 零条记录、数据源 `07-data/` 停写 26 天、
端口还被别的项目占着 —— **死了至少 26 天,没有任何人/告警/判据发现过**。
需要人主动打开的东西在这个项目里存活率 0/1;自己找上门的(Telegram)是唯一活着的。

## 三条自我约束

1. **只做读者,不新建管线。** 数据源就是采集器每轮自己写的心跳 parquet。
   三月那个 dashboard 正是死于"数据源搬家了而它不知道",故
   `test_daily_digest.py::test_consumed_fields_all_exist_in_the_heartbeat_schema`
   把"读的正是写的那份"焊死 —— 字段改名时判据先红。
2. **第一版只报数字,不报判决。** 只有三个量有实测分布撑着(见 THRESHOLD_PROVENANCE),
   其余一律只摆数字。没有分布就定阈值 = 拍脑袋,那是本项目反复犯的错。
3. **绝不合成「健康度」。** 08-13 空转 14 小时那天,「活着吗」和「数据对吗」都是绿的,
   只有「干活了吗」塌了 —— 三项一平均,故障就被稀释掉。

## ⭐它自己会不会静默死掉

把核心问句对准它自己:「如果日报明天不发了,我看到的会有什么不同?」
答案若是"没有不同",它就是第二个 dashboard。

解法不是再建一个盯梢的(无限套娃),而是接到**已被证明活着**的东西上:
发完写 `STATE_FILE` 时间戳,已经每 30 分钟跑一次的 `collector_watchdog`
读它、过期就报红。⚠️ **发失败绝不盖戳** —— 否则看门狗看到新鲜时间戳而人什么都没收到,
那是"留痕与事实脱钩",本项目反复发作的形状。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import re
import sys
import time
from pathlib import Path

import alerts
import cycle_state
import storage_engine as se

# 日报走**自己**的待发队列,不与采集器告警共用。
# 依据:告警稳态零条、日报天天有;混在一条队列里,日报会把真告警挤出上限
# (08-03 那次推了 1340 条告警,没有一条指向真正出事的那件事)。
LINK = "digest"

STATE_FILE = se.DATA_ROOT / "state" / "daily_digest.json"
# 阈值要容得下**一次没跑成**(同看门狗心跳阈值的推导方式):一天一次 ⇒ 必须 >24h,
# 否则一次 suspend/漏跑就误报;又不能太松,否则真死了要好几天才知道。取 26h。
STALE_AFTER_S = 26 * 3600

# 一天该有的轮数(15 分钟一轮)。少于这个要出声 —— 采集器可能被杀过。
EXPECTED_CYCLES = 96

BACKFILL_LOG = se.DATA_ROOT / "backfill.log"

# 日报读用的心跳字段。⭐这份清单就是"读取者"本身:
# 进了这里的字段从此有人看;没进来又没有其它读者的,应当被质疑是否该留在心跳里。
CONSUMED_FIELDS = (
    # 腿1 成交流
    "new_trades", "total_markets_polled", "firehose_fail", "trade_flow_outage_streak",
    "offset_overflow_count",
    # 腿2 结算真值
    "newly_resolved", "settlement_checked", "settlement_lookup_fail",
    "truth_supply_zero_streak",
    # 腿3 注册(⭐这两个就是躺了 9 天没人读的那对)
    "new_discovered", "new_registered",
    "pending_registration_count", "pending_registration_dropped_count",
    "register_inconclusive_count",
    # 整轮健康
    "cycle_seconds", "slow_cycle_streak", "rotation_hole_streak",
    "alert_queue_depth", "alert_dropped_count",
)

# 只有这几个量有**实测分布**撑着,才允许在日报里下判决;其余一律只报数字。
JUDGED_FIELDS = ("trade_flow_outage_streak", "slow_cycle_streak",
                 "truth_supply_zero_streak", "pending_registration_dropped_count")
THRESHOLD_PROVENANCE = {
    "trade_flow_outage_streak":
        "实测 2026-08-05~08-17 firehose_fail>0 连续段:良性最长 3 轮,事故 56 轮 ⇒ 红线 6",
    "slow_cycle_streak":
        "实测 134 个健康周期 p50=115s/max=228s ⇒ 告警线 540s,连续 4 轮才推",
    "truth_supply_zero_streak":
        "实测每轮期望结算 ~20 个 ⇒ 连续 18 轮(约 4.5 小时)为 0 是强异常",
    "pending_registration_dropped_count":
        "丢弃稳态应恒 0(2026-08-17 改批量注册后实测连续多轮 0);非 0 即配额不够",
}


def _now() -> int:
    return int(time.time())


def _num(hb: dict, key: str, default=0):
    """取一个数;缺字段/类型不对一律降级为默认值 —— 日报崩了就等于当天没有日报。"""
    v = hb.get(key, default)
    return v if isinstance(v, (int, float)) and v == v else default


# ---------- 数据源:心跳 ----------

def load_day(day: str | None = None) -> list[dict]:
    """读某一天(UTC)的全部心跳。默认昨天。

    只读采集器自己写的审计分区 —— 不碰 SQLite、不碰 07-data(那两条都已废弃)。
    """
    day = day or (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)).isoformat()
    files = sorted(glob.glob(str(se.AUDIT_DIR / f"dt={day}" / "*.parquet")))
    if not files:
        return []
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows: list[dict] = []
    skipped = 0
    for f in files:
        try:
            rows += pq.read_table(f).to_pylist()
        except (OSError, pa.lib.ArrowException):
            # ⚠️ pyarrow 的异常分属**三个**家族:ArrowInvalid→ValueError、
            # ArrowTypeError→TypeError、ArrowIOError→OSError。只捕其中一两个都会漏
            # (初版只捕 OSError,改成 (OSError, ValueError) 仍漏 ArrowTypeError)。
            # 共同基类是 ArrowException,用它才真的覆盖。
            # (2026-08-17 code review 抓出 + 实测逐个复现 MRO。)
            skipped += 1        # ⭐跳过必须出声:静默少一块和"那天本来就少"无法区分
            continue
    if skipped:
        print(f"⚠️ 有 {skipped} 个心跳文件读不了,已跳过(当日数据不完整)", flush=True)
    rows.sort(key=lambda r: r.get("ts") or 0)
    return rows


def _backfill_remaining() -> int | None:
    """回填腿的剩余目标数。

    ⚠️ 这条腿是**独立 systemd 服务**,只写文本日志、没有结构化留痕,
    所以这里读的是日志尾巴 —— 比另外三条腿弱。读不到时返回 None,
    调用方必须显示「读不到」而**不是** 0(0 和"已清完"长得一模一样)。
    """
    try:
        tail = BACKFILL_LOG.read_text(encoding="utf-8", errors="replace")[-8000:]
    except OSError:
        return None
    hits = re.findall(r"'targets':\s*(\d+)", tail)
    return int(hits[-1]) if hits else None


# ---------- 渲染 ----------

def _longest_outage_run(hbs: list[dict]) -> int:
    """从**当天心跳序列**自己算 firehose 抓取失败的最长连续段。

    ⭐不读持久化的 `trade_flow_outage_streak`。实测踩到:用 08-13(真的空转
    13.5 小时那天)跑日报,腿1 报了 ✅ —— 因为那个连计字段是 2026-08-17 才加的,
    历史心跳里是空的;而 `firehose_fail` 当天 43 轮为 1,证据一直都在。

    通用式:**回顾型报表手上有整段序列,就不该依赖某个当时可能还不存在、
    或中途丢过状态文件的字段。** 持久化连计是给实时告警用的 —— 那里没有整段序列可看。
    """
    best = cur = 0
    for h in hbs:
        cur = cur + 1 if _num(h, "firehose_fail") > 0 else 0
        best = max(best, cur)
    return best


def _leg_trade_flow(hbs: list[dict]) -> tuple[str, bool]:
    trades = sum(_num(h, "new_trades") for h in hbs)
    polled = sum(_num(h, "total_markets_polled") for h in hbs)
    worst = _longest_outage_run(hbs)
    ovf = sum(_num(h, "offset_overflow_count") for h in hbs)
    bad = worst >= alerts.TRADE_FLOW_OUTAGE_CYCLES
    mark = "🔴" if bad else "✅"
    extra = ""
    if worst:
        extra = (f" / **断供最长 {worst} 轮"
                 f"(约 {worst * alerts.cycle_minutes() / 60:.1f} 小时)**")
    if ovf:
        extra += f" / offset 截断 {ovf}"
    return f"腿1 成交流   {mark} {trades:,} 笔 / 轮询 {polled:,} 个市场{extra}", bad


def _leg_settlement(hbs: list[dict]) -> tuple[str, bool]:
    resolved = sum(_num(h, "newly_resolved") for h in hbs)
    checked = sum(_num(h, "settlement_checked") for h in hbs)
    fail = sum(_num(h, "settlement_lookup_fail") for h in hbs)
    worst = max((_num(h, "truth_supply_zero_streak") for h in hbs), default=0)
    bad = worst >= alerts.TRUTH_SUPPLY_ZERO_CYCLES
    mark = "🔴" if bad else "✅"
    rate = f" / 查询失败 {fail / checked * 100:.0f}%" if checked else ""
    return (f"腿2 结算真值 {mark} 新结算 {resolved:,} 个 / 查了 {checked:,}{rate}"
            f" / 零流最长 {worst} 轮"), bad


def _leg_registration(hbs: list[dict]) -> tuple[str, bool]:
    """⭐这条腿读的两个字段,就是躺了 9 天没人看的那对。"""
    disc = sum(_num(h, "new_discovered") for h in hbs)
    reg = sum(_num(h, "new_registered") for h in hbs)
    dropped = sum(_num(h, "pending_registration_dropped_count") for h in hbs)
    pend = max((_num(h, "pending_registration_count") for h in hbs), default=0)
    bad = dropped > 0
    mark = "🔴" if bad else "✅"
    tail = f" / **丢弃 {dropped:,}**(积压峰值 {pend:,})" if bad else " / 丢弃 0"
    return f"腿3 注册     {mark} 涌入 {disc:,} → 登记 {reg:,}{tail}", bad


def _leg_backfill() -> tuple[str, bool]:
    left = _backfill_remaining()
    if left is None:
        return "腿4 回填     ⚠️ 读不到(它只写文本日志,没有结构化留痕)", False
    # ⚠️ 标「当前」不是当日:这条腿没有按日留痕,读的是日志尾巴。
    # 印在历史日报里不标明就是张冠李戴 —— 那正是"用与事实脱钩的留痕"那个形状。
    return f"腿4 回填     ✅ 当前剩 {left:,} 个(该腿无按日留痕,读的是日志尾巴)", False


def render(hbs: list[dict], day: str | None = None) -> str:
    """把一天的心跳渲染成日报正文。空输入必须响亮,不许渲染成漂亮的全 0 报表。"""
    day = day or (hbs[0].get("dt") if hbs else "?")
    head = f"📊 采集器日报 {day}"
    if not hbs:
        return (f"{head}\n🔴 **这一天没有心跳** —— 采集器可能整天没跑成。\n"
                f"注意:这不是「很闲」,是没有数据。须查 timer 与 collector.log")

    lines, bads = [], []
    for text, bad in (_leg_trade_flow(hbs), _leg_settlement(hbs),
                      _leg_registration(hbs), _leg_backfill()):
        lines.append(text)
        if bad:
            bads.append(text.split()[0] + text.split()[1])

    n = len(hbs)
    if n < EXPECTED_CYCLES:
        lines.append(f"⚠️ 本日只有 {n} 轮心跳(该有 {EXPECTED_CYCLES} 轮)—— 采集器被杀过或漏跑")

    slow = max((_num(h, "slow_cycle_streak") for h in hbs), default=0)
    holes = max((_num(h, "rotation_hole_streak") for h in hbs), default=0)
    qmax = max((_num(h, "alert_queue_depth") for h in hbs), default=0)
    qdrop = sum(_num(h, "alert_dropped_count") for h in hbs)
    secs = [_num(h, "cycle_seconds") for h in hbs if _num(h, "cycle_seconds")]
    secs.sort()
    p50 = secs[len(secs) // 2] if secs else 0
    worst = secs[-1] if secs else 0
    lines.append(f"周期 中位 {p50:.0f}s / 最慢 {worst:.0f}s"
                 + (f" / 慢周期连计 {slow}" if slow else "")
                 + (f" / 游标空洞连计 {holes}" if holes else ""))
    if qmax or qdrop:
        lines.append(f"⚠️ 告警通道:队列峰值 {qmax} 条,丢弃 {qdrop} 条 —— 有告警没送出去")

    verdict = "昨日无异常" if not bads else "🔴 昨日有异常:" + "、".join(bads)
    return f"{head}\n" + "\n".join(lines) + f"\n{verdict}"


# ---------- 发送 + 自保时间戳 ----------

def _send(msg: str) -> bool:
    """走 alerts 的待发队列(断网时排队、恢复后补发),但用**自己**那条链路。"""
    return bool(alerts.dispatch(msg, link=LINK).get("delivered"))


def last_sent_age_s(now: int | None = None) -> int | None:
    """距上次**成功发出**多久。从没发过返回 None(刚上线不该误报)。"""
    ts = cycle_state.read_state(STATE_FILE, "last_sent_ts", 0)
    if not ts:
        return None
    return max(0, (now if now is not None else _now()) - int(ts))


def run(hbs: list[dict] | None = None, day: str | None = None) -> int:
    hbs = load_day(day) if hbs is None else hbs
    body = render(hbs, day)
    ok = _send(body)
    print(body, flush=True)
    if ok:
        # ⭐只有真发出去了才盖戳。发失败还盖 = 看门狗看到新鲜时间戳而人什么都没收到。
        cycle_state.write_state(STATE_FILE, "last_sent_ts", _now(), "日报发送时间")
    else:
        print("⚠️ 日报没发出去(已进待发队列,下次补发);**未**更新时间戳", flush=True)
    # ⭐返回 0 —— 「进队列」是预期内会经常发生的**自愈**行为,不是进程失败。
    # 由来(2026-08-17 code review 实测抓出):service 里两条 ExecStart 串在一起,
    # 而 systemd 的语义是「前一条失败(且没有 `-` 前缀),后面的都不执行」
    # ⇒ 日报一进队列,当天的趋势报表就根本不会生成,而那正是本次要立起来的下钻能力。
    # 投递成功与否由 STATE_FILE + 看门狗独立追踪,不必再借退出码表达一遍。
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="采集器每日日报")
    ap.add_argument("--day", help="YYYY-MM-DD(UTC),默认昨天")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送")
    a = ap.parse_args()
    if a.dry_run:
        print(render(load_day(a.day), a.day))
        sys.exit(0)
    sys.exit(run(day=a.day))
