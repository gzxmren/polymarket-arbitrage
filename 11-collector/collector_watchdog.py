#!/usr/bin/env python3
"""collector_watchdog.py — 新采集器看门狗(补 auto_health_check 退役的空缺)。

采集器自带的告警(alerts.py)只在"周期跑起来但内部异常"时喊;它喊不出"timer 整个停了/
根本没在跑"。本看门狗独立于采集器运行(自己的 systemd timer,每 30 分钟),专查外部存活:
  1. collector timer 还 active 吗?
  2. 最近有没有跑过周期?(审计心跳新鲜度 —— 每轮必写,故>25 分钟无心跳=停摆)
  3. firehose 是否持续抽风?(最近心跳 firehose_fail>0)

任一红 → Telegram(复用 alerts._send)。带 4h 冷却防刷屏。
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import subprocess
import time

import pyarrow.parquet as pq

import alerts
import storage_engine as se

HEARTBEAT_STALE_MIN = 25          # 采集器每 10 分钟一轮,>25 分钟无心跳 = 停摆
COOLDOWN_S = 4 * 3600             # 同一问题 4h 内不重复告警
STATE_FILE = se.DATA_ROOT / ".watchdog_state.json"
TIMER_UNIT = "polymarket-rebirth-collector.timer"


def _timer_active() -> bool:
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", TIMER_UNIT],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() == "active"
    except (subprocess.SubprocessError, OSError):
        return False  # 查不了当异常(宁可误报,不放过停摆)


def _latest_heartbeat() -> dict | None:
    files = glob.glob(str(se.AUDIT_DIR / "**" / "*.parquet"), recursive=True)
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    try:
        rows = pq.read_table(newest).to_pylist()
    except OSError:
        return None
    return rows[-1] if rows else None


def check() -> list[str]:
    problems = []
    if not _timer_active():
        problems.append(f"🔴 采集器 timer 不在 active（{TIMER_UNIT} 已停摆）")
    hb = _latest_heartbeat()
    if hb is None:
        problems.append("🔴 无任何审计心跳（采集器从未成功跑完一轮）")
    else:
        age_min = (time.time() - hb["ts"]) / 60
        if age_min > HEARTBEAT_STALE_MIN:
            problems.append(f"🔴 采集器 {age_min:.0f} 分钟无心跳（应每 10 分钟一轮=停摆）")
        if hb.get("firehose_fail", 0) > 0:
            problems.append("🟡 最近一轮 firehose 抽风（采样 0 笔），若持续须查接口/IP")
    return problems


def _cooldown_ok(signature: str) -> bool:
    """同一问题集 4h 内只告警一次;问题变化则立即告警。"""
    try:
        st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (json.JSONDecodeError, OSError):
        st = {}
    if st.get("sig") == signature and (time.time() - st.get("ts", 0)) < COOLDOWN_S:
        return False
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"sig": signature, "ts": time.time()}))
    return True


def main() -> int:
    problems = check()
    if not problems:
        print(f"[{dt.datetime.now():%H:%M}] ✓ 采集器健康")
        return 0
    sig = "|".join(sorted(problems))
    line = "🐕 <b>采集器看门狗告警</b>\n" + "\n".join(problems)
    print(line)
    if _cooldown_ok(sig):
        alerts._send(line)
    else:
        print("（冷却期内,未重复推送）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
