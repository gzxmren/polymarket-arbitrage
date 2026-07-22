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
import json
import sys
from pathlib import Path

import alerts
import collector_core
import settlement_watcher
import storage_engine as se

STATE_FILE = se.DATA_ROOT / ".cycle_state.json"
# 每轮工作量必须能在 10 分钟间隔内跑完(否则被 8 分钟超时杀、永远跑不到写心跳=空转打转)。
# 实测冷启动 register 112 + poll 270(含大量 8000 笔全回填)单轮 >10 分钟。故双封顶:
DEFAULT_MAX_NEW = 40      # 每轮最多注册 N 个新市场(~1.3s/个 Gamma)
DEFAULT_POLL_LIMIT = 50   # 每轮最多轮询 N 个市场(冷启动全回填 ~11s/个)
# 冷启动:全宇宙(~600)摊到 ~12 轮(~2 小时)跑满;之后 watermark 令轮询转增量、极快。


def _daily_compaction_if_due() -> str | None:
    """每日对昨日分区跑一次 compaction(幂等:同一天只跑一次)。"""
    today = dt.date.today().isoformat()
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (json.JSONDecodeError, OSError):
        state = {}
    if state.get("compacted_date") == today:
        return None
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    merged = se.compact_day(yesterday)
    state["compacted_date"] = today
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))
    return str(merged) if merged else None


def main(sample: int = 5000, max_new: int | None = DEFAULT_MAX_NEW,
         poll_limit: int | None = DEFAULT_POLL_LIMIT) -> int:
    t0 = dt.datetime.now(dt.UTC)
    print(f"=== 采集周期 {t0:%Y-%m-%d %H:%M:%S}Z ===", flush=True)

    counts = collector_core.run_once(limit=poll_limit, sample=sample, max_new=max_new)
    settle = settlement_watcher.watch_settlements()
    print(f"结算守望: {settle}", flush=True)

    merged = {
        **counts,
        "newly_resolved": settle["newly_resolved"],
        "settlement_lookup_fail": settle["lookup_fail"],
    }
    if alerts.maybe_alert(merged):
        print("已推送告警", flush=True)

    comp = _daily_compaction_if_due()
    if comp:
        print(f"compaction: {comp}", flush=True)

    dur = (dt.datetime.now(dt.UTC) - t0).total_seconds()
    print(f"=== 周期结束 {dur:.0f}s ===", flush=True)
    return 0


if __name__ == "__main__":
    # 默认每轮注册上限 DEFAULT_MAX_NEW(防冷启动撑爆超时);可传参覆盖(0=不注册新市场)。
    max_new = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MAX_NEW
    sys.exit(main(max_new=max_new))
