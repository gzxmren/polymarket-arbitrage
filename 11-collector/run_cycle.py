#!/usr/bin/env python3
"""run_cycle.py — 守护层入口:一个采集周期。由 systemd --user timer 每 10 分钟调用。

兑现《数据契约 v1.2》守护层:
= 采集(run_once:Firehose 发现 + 逐市场增量轮询)
+ 结算守望(watch_settlements:已关闭未结算 → Gamma 抓官方结算)
+ 告警(maybe_alert:offset_overflow / Gamma 失败超阈值 → Telegram 摘要)
+ 每日 compaction(合并昨日小 Parquet,幂等 once/day)。

**幂等**:一切由 watermark 驱动;timer 多叫醒几次无副作用(重复轮询只增量、去重在查询层)。
故 systemd Persistent=true 的"休眠醒来补跑"天然安全。
"""
from __future__ import annotations

import datetime as dt
import sys
import time

import alerts
import collector_core
import cycle_state
import settlement_watcher
import storage_engine as se

# 每轮工作量必须能在 15 分钟间隔内跑完(否则被超时杀、永远跑不到写心跳=空转打转)。
# 上限见 alerts.CYCLE_BUDGET_S(2026-08-04 从 10 分钟/480s 放宽到 15 分钟/900s)。
# 实测冷启动 register 112 + poll 270(含大量 8000 笔全回填)单轮 >10 分钟。故双封顶:
DEFAULT_MAX_NEW = 40      # 每轮最多注册 N 个新市场(~1.3s/个 Gamma)
DEFAULT_POLL_LIMIT = 50   # 每轮最多轮询 N 个市场(冷启动全回填 ~11s/个)
# 冷启动:全宇宙(~600)摊到 ~12 轮(~2 小时)跑满;之后 watermark 令轮询转增量、极快。


COMPACT_MIN_FILES = 50   # 分区文件数超此值即合并(含被回填污染的旧分区)


def _compaction_sweep() -> list[str]:
    """扫全部分区,合并文件数超阈值的(不只"昨天")。

    自限:合并后分区落到 1 个大文件,须再累积 >阈值 才会被下轮重新合并 → 天然幂等、不空转。
    修掉旧"每天只合并昨天一次"的盲区(回填写进旧日期分区 → 小文件永久累积)。
    """
    return se.compact_due_partitions(min_files=COMPACT_MIN_FILES)


SLOW_STREAK_FILE = se.DATA_ROOT / "state" / "slow_cycle_streak.json"


def main(sample: int = 5000, max_new: int | None = DEFAULT_MAX_NEW,
         poll_limit: int | None = DEFAULT_POLL_LIMIT) -> int:
    t0 = time.monotonic()   # 单调钟:测耗时不能用 wall clock(NTP 校时会把它拨得忽前忽后)
    print(f"=== 采集周期 {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S}Z ===", flush=True)

    # 各阶段分开计时。2026-08-04 事故里周期从 130s 涨到 480s,而日志里**没有任何**
    # 能指出"慢在哪一段"的信息 —— 当时只能靠手工逐段实测才定位到网络。那是可观测性缺口,
    # 不是"注意一点"能避免的,故焊进日志。
    t = time.monotonic()
    counts = collector_core.run_once(limit=poll_limit, sample=sample, max_new=max_new)
    t_collect = time.monotonic() - t

    t = time.monotonic()
    # 结算守望的网络失败也计进同一份 net_*(同一条代理隧道,见 discovery_service.new_net_stats)
    settle = settlement_watcher.watch_settlements(net=counts)
    t_settle = time.monotonic() - t
    print(f"结算守望: {settle}", flush=True)

    t = time.monotonic()
    comp = _compaction_sweep()
    t_compact = time.monotonic() - t
    if comp:
        print(f"compaction: 合并 {len(comp)} 个分区 {comp}", flush=True)

    dur = time.monotonic() - t0
    slow_streak = cycle_state.next_hit_streak(
        cycle_state.read_state(SLOW_STREAK_FILE, "streak", 0), hit=alerts.is_slow_cycle(dur))
    cycle_state.write_state(SLOW_STREAK_FILE, "streak", slow_streak, "慢周期连计数")

    merged = {
        **counts,
        "newly_resolved": settle["newly_resolved"],
        "settlement_lookup_fail": settle["lookup_fail"],
        "settlement_checked": settle["checked"],  # 失败按比率判定,须带上分母
        "truth_supply_zero_streak": settle["zero_streak"],  # 真值断供守护(静默失败)
        "cycle_seconds": dur,
        # run_once 内部再拆成发现/轮询两段(它自己填 discovery_seconds),这里减出纯轮询耗时
        "poll_seconds": t_collect - counts.get("discovery_seconds", 0.0),
        "settlement_seconds": t_settle,
        "compaction_seconds": t_compact,
        "slow_cycle_streak": slow_streak,
    }
    # 心跳写在这里(而非 run_once 内)= "整轮真跑完了"的凭证,详见 collector_core 里的说明。
    se.write_audit_heartbeat(merged)

    if alerts.maybe_alert(merged):
        print("已推送告警", flush=True)

    print(f"=== 周期结束 {dur:.0f}s "
          f"(发现 {merged['discovery_seconds']:.0f}s / 轮询 {merged['poll_seconds']:.0f}s / "
          f"结算 {t_settle:.0f}s / compaction {t_compact:.0f}s"
          f" | 慢周期连计 {slow_streak}) ===", flush=True)
    return 0


if __name__ == "__main__":
    # 默认每轮注册上限 DEFAULT_MAX_NEW(防冷启动撑爆超时);可传参覆盖(0=不注册新市场)。
    max_new = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MAX_NEW
    sys.exit(main(max_new=max_new))
