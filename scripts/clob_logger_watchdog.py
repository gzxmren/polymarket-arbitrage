#!/usr/bin/env python3
"""
CLOB 摄像头看门狗 — 确保前向数据采集不悄悄停摆

前向套利测试要跑≥2周。最大风险是 clob_pair_logger 静默挂掉(cron被删/脚本崩/
API变更/锁卡死)，两周后才发现没数据。本看门狗定时检查"摄像头"是否在持续录像，
发现异常立刻 Telegram 告警(管理员私聊，ID 从 openclaw.json 读)。

检查项(读 07-data/clob_pair_log.db):
  1. 新鲜度: 最近一轮距今多久? >70min = 漏了≥2个采集周期 → 🔴严重(停摆)
  2. 采集量: 近6h每轮平均市场数 <25 → 🟡警告(采集退化,可能API问题)
  3. 锁卡死: .clob_pair_logger.lock 存在且 >35min → 🟡警告
  4. 完全无数据 → 🔴严重

防刷屏: 同一问题 6h 内不重复告警(状态文件记录)。状态由问题→正常时发"恢复"。

用法:
  python3 scripts/clob_logger_watchdog.py            # 检查，异常才告警
  python3 scripts/clob_logger_watchdog.py --daily     # 发每日健康汇总(无论好坏)
  python3 scripts/clob_logger_watchdog.py --dry-run    # 只打印不发 Telegram
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "07-data" / "clob_pair_log.db"
LOCK_FILE = PROJECT_ROOT / "06-tools" / "monitoring" / ".clob_pair_logger.lock"
STATE_FILE = PROJECT_ROOT / "07-data" / ".clob_watchdog_state.json"

sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "monitoring"))
import telegram_notifier_v2 as tg  # noqa: E402
from telegram_notifier_v2 import send_telegram_message  # noqa: E402

# 告警发到管理员私聊（运维惯例）。ID 从 openclaw.json 读，不硬编码。
# 覆盖模块全局 TELEGRAM_CHAT_ID（send_telegram_message 调用时读此全局）。
_admin = os.environ.get("TELEGRAM_CHAT_ID") or tg.get_admin_chat_id()
if _admin:
    tg.TELEGRAM_CHAT_ID = _admin

# 阈值
STALE_MIN = 70          # 距上轮 >此分钟 = 停摆
LOW_MARKETS = 25        # 近6h每轮均 <此 = 采集退化
STALE_LOCK_MIN = 35     # 锁年龄 >此分钟 = 可能卡死
ALERT_COOLDOWN_S = 6 * 3600  # 同问题 6h 不重复


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"last_status": "ok", "last_alert_ts": 0}


def save_state(status: str, alerted: bool):
    st = load_state()
    st["last_status"] = status
    if alerted:
        st["last_alert_ts"] = time.time()
    STATE_FILE.write_text(json.dumps(st))


def check_health() -> dict:
    """返回 {status: ok|warn|critical, problems: [...], metrics: {...}}"""
    now = datetime.now(timezone.utc)
    problems = []
    metrics = {}

    if not DB_PATH.exists():
        return {"status": "critical", "problems": ["数据库不存在，摄像头从未成功写入"], "metrics": {}}

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()

    total_runs, last_ts = cur.execute(
        "SELECT COUNT(*), MAX(ts) FROM clob_pair_runs").fetchone()
    total_snap = cur.execute("SELECT COUNT(*) FROM clob_pair_log").fetchone()[0]
    metrics["total_runs"] = total_runs
    metrics["total_snapshots"] = total_snap

    if not total_runs or not last_ts:
        conn.close()
        return {"status": "critical", "problems": ["无任何采集轮次记录"], "metrics": metrics}

    # 1. 新鲜度
    last_dt = datetime.fromisoformat(last_ts)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    mins_since = (now - last_dt).total_seconds() / 60
    metrics["minutes_since_last"] = round(mins_since, 1)
    metrics["last_run_local"] = last_dt.astimezone().strftime("%m-%d %H:%M")
    if mins_since > STALE_MIN:
        problems.append(f"🔴 停摆：最近一轮在 {mins_since:.0f} 分钟前（>{STALE_MIN}min，漏了≥2个周期）")

    # 2. 近6h采集量
    cutoff = (now.timestamp() - 6 * 3600)
    rows = cur.execute("SELECT ts, n_markets FROM clob_pair_runs ORDER BY ts DESC LIMIT 12").fetchall()
    recent = []
    for ts, nm in rows:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.timestamp() >= cutoff:
            recent.append(nm)
    if recent:
        avg_m = sum(recent) / len(recent)
        metrics["avg_markets_6h"] = round(avg_m, 1)
        metrics["runs_6h"] = len(recent)
        if avg_m < LOW_MARKETS:
            problems.append(f"🟡 采集退化：近6h每轮均 {avg_m:.0f} 市场（<{LOW_MARKETS}，疑API问题）")

    # 3. 锁卡死
    if LOCK_FILE.exists():
        lock_age = (time.time() - LOCK_FILE.stat().st_mtime) / 60
        if lock_age > STALE_LOCK_MIN:
            problems.append(f"🟡 进程锁卡死：锁已存在 {lock_age:.0f} 分钟（cron 可能未在触发）")

    conn.close()

    if any("🔴" in p for p in problems):
        status = "critical"
    elif problems:
        status = "warn"
    else:
        status = "ok"
    return {"status": status, "problems": problems, "metrics": metrics}


def fmt_summary(h: dict) -> str:
    m = h["metrics"]
    label = {"ok": "🟢 正常", "warn": "🟡 警告", "critical": "🔴 严重"}[h["status"]]
    lines = ["📡 摄像头健康汇总（CLOB pair-cost 前向采集）"]
    lines.append(f"状态: {label}")
    if "last_run_local" in m:
        lines.append(f"最近采集: {m['last_run_local']}（{m.get('minutes_since_last','?')}分钟前）")
    if "avg_markets_6h" in m:
        lines.append(f"近6h: {m.get('runs_6h','?')}轮，每轮均{m['avg_markets_6h']}市场")
    lines.append(f"累计: {m.get('total_runs',0)}轮 / {m.get('total_snapshots',0)}个市场快照")
    if h["problems"]:
        lines.append("\n问题:")
        lines.extend("  " + p for p in h["problems"])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="发每日健康汇总(无论好坏)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发Telegram")
    args = ap.parse_args()

    h = check_health()
    summary = fmt_summary(h)
    print(summary)

    state = load_state()
    now = time.time()

    should_alert = False
    msg = None

    if args.daily:
        # 每日汇总：无条件发
        should_alert = True
        msg = "🗓️ 每日 " + summary
    elif h["status"] in ("warn", "critical"):
        # 问题告警：状态恶化 或 距上次告警超冷却
        cooldown_ok = (now - state.get("last_alert_ts", 0)) > ALERT_COOLDOWN_S
        status_worsened = state.get("last_status") == "ok"
        if status_worsened or cooldown_ok:
            should_alert = True
            msg = "⚠️ 摄像头异常告警\n\n" + summary + "\n\n请检查 /tmp/clob_pair_logger.log 与系统 crontab"
    elif h["status"] == "ok" and state.get("last_status") in ("warn", "critical"):
        # 恢复通知
        should_alert = True
        msg = "✅ 摄像头已恢复正常\n\n" + summary

    if should_alert and msg:
        if args.dry_run:
            print("\n[dry-run] 将发送以下 Telegram：\n" + msg)
        else:
            ok = send_telegram_message(msg)
            print(f"\nTelegram 已发送: {ok}")
            save_state(h["status"], alerted=True)
    else:
        save_state(h["status"], alerted=False)
        print(f"\n（{h['status']}，无需告警）")

    # 退出码：严重=2，警告=1，正常=0（便于 cron 链判断）
    sys.exit({"ok": 0, "warn": 1, "critical": 2}[h["status"]])


if __name__ == "__main__":
    main()
