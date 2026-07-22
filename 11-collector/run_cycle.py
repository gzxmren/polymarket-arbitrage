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


def main(sample: int = 5000, max_new: int | None = None) -> int:
    t0 = dt.datetime.now(dt.UTC)
    print(f"=== 采集周期 {t0:%Y-%m-%d %H:%M:%S}Z ===", flush=True)

    counts = collector_core.run_once(sample=sample, max_new=max_new)
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
    # 首轮冷启动可能注册大量新市场,--max-new 限一下;稳态后无参即可。
    max_new = int(sys.argv[1]) if len(sys.argv) > 1 else None
    sys.exit(main(max_new=max_new))
