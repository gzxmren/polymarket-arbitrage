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

# 采集器每 15 分钟一轮(2026-08-04 从 10 分钟放宽,见 .timer)。
# 阈值必须容得下**一轮被杀**:一轮被 SIGTERM → 下一条心跳要等到 ~30 分钟后。
# 取 40 分钟 = 容 1 轮失手、抓 2 轮连续失手。旧值 25 分钟配 15 分钟间隔会对单轮失手误报。
# ⚠️ 本守护只查"完全停摆";"跑得动但越来越慢"由 alerts 的慢周期守护负责。
#    心跳已于 2026-08-04 移到整轮末尾(原先在结算守望之前 → 结算阶段被杀仍留新鲜心跳,
#    本守护对那一段是瞎的)。现在"心跳新鲜"= 整轮真的跑完了,新鲜度才真有分辨力。
HEARTBEAT_STALE_MIN = 40
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


def _heartbeat_problems(hb: dict) -> list[str]:
    """只看心跳**内容**的问题(不含新鲜度 —— 那个要拿当前时间比,单独在 check 里判)。

    拆成纯函数是为了判据能直接喂一条心跳进来验,不必伪造文件系统和时钟。
    """
    problems = []
    if hb.get("firehose_fail", 0) > 0:
        problems.append("🟡 最近一轮 firehose 抽风（采样 0 笔），若持续须查接口/IP")
    # ⭐告警送达盲区(2026-08-07 立):采集器自己发不出去的时候是喊不出来的,
    # 只能由**别人**替它喊。本条在"网络没坏、但 Telegram 令牌失效/接口变更/被限流"
    # 这类故障下真管用;若是整机断网,看门狗自己也喊不出去 —— 那一层解决不了,
    # 需要第二条独立通道,已在 alerts.py 里写明不在范围内。
    qd = hb.get("alert_queue_depth", 0)
    if qd > 0:
        problems.append(f"🟡 采集器有 {qd} 条告警**发不出去**(积压待发)。"
                        f"含义:它可能正在出事而喊不出来 —— 须查 Telegram 令牌/网络")
    return problems


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
            problems.append(f"🔴 采集器 {age_min:.0f} 分钟无心跳"
                            f"（应每 {alerts.cycle_minutes()} 分钟一轮=停摆）")
        problems += _heartbeat_problems(hb)
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
        # 走待发队列:看门狗自己也栽过 —— 2026-08-07 早上它抓到了 firehose 抽风、
        # 想推却因同一场断网推不出去,只在日志里留了两行没人读的 Error。
        alerts.dispatch(line, link="watchdog")
    else:
        print("（冷却期内,未重复推送）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
