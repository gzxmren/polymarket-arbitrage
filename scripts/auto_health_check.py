#!/usr/bin/env python3
"""
Polymarket 系统自动健康巡检

每天定时运行，自动检查系统关键指标，发现问题直接发 Telegram 告警。
不需要人工触发——这就是它存在的意义。

检查项:
  1. Monitor 报告新鲜度（是否按时跑）
  2. 信号链健康（whale_following 是否真的在产生信号）
  3. 关键日志是否有崩溃/ERROR
  4. Leaderboard 同步是否正常（每周一）
  5. CLOB 摄像头（最近采集时间）
  6. Cron 是否有重复条目
  7. 数据库关键表行数是否合理
  8. Cron 健康（各 job 是否在预期频率内跑过）

防刷屏: 同一问题 4h 内不重复告警。状态恢复时发"已修复"通知。

用法:
  python3 scripts/auto_health_check.py            # 检查，有问题才告警
  python3 scripts/auto_health_check.py --daily    # 每日汇总（无论好坏都发）
  python3 scripts/auto_health_check.py --dry-run  # 只打印，不发 Telegram
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = PROJECT_ROOT / "dashboard" / "backend" / "database" / "polymarket.db"
CLOB_DB = PROJECT_ROOT / "07-data" / "clob_pair_log.db"
STATE_FILE   = PROJECT_ROOT / "07-data" / ".auto_health_state.json"
ISSUES_FILE  = PROJECT_ROOT / "07-data" / ".pending_issues.json"   # Claude Code 会话钩子读取此文件
MONITOR_REPORT_GLOB = str(PROJECT_ROOT / "07-data" / "monitor_report_*.json")
LEADERBOARD_LOG = PROJECT_ROOT / "07-data" / "logs" / "leaderboard_sync.log"

# 告警冷却：同一问题 4h 内只告警一次
ALERT_COOLDOWN_S = 4 * 3600

# 阈值
MONITOR_STALE_H = 7        # monitor_report 超过 7h 未更新 → 严重
CLOB_STALE_MIN = 70        # CLOB 超过 70min 未采集 → 严重
LEADERBOARD_STALE_DAYS = 8 # leaderboard 超过 8 天未同步 → 警告
SIGNAL_WARN_DAYS = 2       # signals 表超过 2 天零新增 → 警告

sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "monitoring"))


# ───────────────────────── 数据结构 ─────────────────────────

@dataclass
class Check:
    name: str
    status: str          # ok / warn / critical
    detail: str          # 一行说明
    fix_hint: str = ""   # 出问题时给出的修复提示


def _icon(status: str) -> str:
    return {"ok": "✅", "warn": "🟡", "critical": "🔴"}[status]


# ───────────────────────── Telegram ─────────────────────────

def _send(msg: str) -> bool:
    try:
        import telegram_notifier_v2 as tg
        return tg.send_telegram_message(msg)
    except Exception as e:
        print(f"[Telegram 发送失败] {e}", file=sys.stderr)
        return False


# ───────────────────────── 状态持久化 ───────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(worst: str, alerted: bool):
    st = _load_state()
    st["last_status"] = worst
    if alerted:
        st["last_alert_ts"] = time.time()
    STATE_FILE.write_text(json.dumps(st))


def _save_pending_issues(checks: List[Check]):
    """把未解决的问题写入 .pending_issues.json，供 Claude Code 会话钩子读取。
    全部 ok 时写空列表（让钩子知道已清干净）。"""
    bad = [c for c in checks if c.status != "ok"]
    ISSUES_FILE.write_text(json.dumps({
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "worst": _worst(checks),
        "issues": [
            {"name": c.name, "status": c.status,
             "detail": c.detail, "fix_hint": c.fix_hint}
            for c in bad
        ],
    }, ensure_ascii=False, indent=2))


# ───────────────────────── 各项检查 ─────────────────────────

def check_monitor_freshness() -> Check:
    """Monitor 报告是否按时生成（每6小时一次）"""
    reports = sorted(glob.glob(MONITOR_REPORT_GLOB))
    if not reports:
        return Check("Monitor报告", "critical", "07-data 里没有任何 monitor_report",
                     "检查 crontab：polymarket_monitor_v2 是否在跑")
    latest = reports[-1]
    age_h = (time.time() - os.path.getmtime(latest)) / 3600
    name = Path(latest).name
    if age_h > MONITOR_STALE_H:
        return Check("Monitor报告", "critical",
                     f"最新报告 {name} 距今 {age_h:.1f}h（>{MONITOR_STALE_H}h）",
                     "crontab -l | grep monitor_v2 确认任务存在；查 /tmp/monitor_v2.log 看报错")
    return Check("Monitor报告", "ok", f"{name}（{age_h:.1f}h 前）")


def check_signal_pipeline() -> Check:
    """信号链：signals 表是否在增长，以及最近报告里 whale_following 有无产出"""
    # 1. DB 里 signals 表最近 48h 新增
    try:
        conn = sqlite3.connect(MAIN_DB)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=SIGNAL_WARN_DAYS)).isoformat()
        cnt = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE created_at > ?", (cutoff,)
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        conn.close()
    except Exception as e:
        return Check("信号链", "warn", f"读取 signals 表失败: {e}")

    # 2. 最近报告里 whale_following 字段
    reports = sorted(glob.glob(MONITOR_REPORT_GLOB))[-3:]
    wf_signals = []
    for r in reports:
        try:
            data = json.loads(Path(r).read_text())
            wf_signals.append(data.get("whale_following", {}).get("signals", 0))
        except Exception:
            pass

    recent_wf = sum(wf_signals)

    if total == 0 and recent_wf == 0:
        return Check("信号链", "warn",
                     f"signals 表共 {total} 行，最近3次报告 whale_following 均为0",
                     "运行: python3 -c \"import sys; sys.path.insert(0,'06-tools/analysis'); "
                     "sys.path.insert(0,'06-tools/monitoring'); from whale_following import "
                     "WhaleFollowingStrategy; print(len(WhaleFollowingStrategy().scan()))\"")
    if cnt == 0 and total > 0:
        return Check("信号链", "warn",
                     f"signals 表共 {total} 行，但 {SIGNAL_WARN_DAYS} 天内零新增",
                     "检查 whale_following 逻辑 or leaderboard 数据是否过期")
    return Check("信号链", "ok",
                 f"signals 共 {total} 行，近 {SIGNAL_WARN_DAYS}d 新增 {cnt}，"
                 f"最近3次报告 whale_following={wf_signals}")


def check_logs() -> Check:
    """关键日志是否有崩溃 / 未处理异常（只看最近 24h 内写入的日志）"""
    log_files = {
        "leaderboard":    LEADERBOARD_LOG,
        "data_quality":   Path("/tmp/data_quality_check.log"),
        "cleanup":        Path("/tmp/polymarket_cleanup.log"),
        "price_snapshot": Path("/tmp/snapshot_daily_prices.log"),
        "backup_configs": Path("/tmp/backup_configs.log"),
        "cleanup_tmp":    Path("/tmp/cleanup_temp_files.log"),
    }
    cutoff_mtime = time.time() - 24 * 3600
    problems = []
    for name, path in log_files.items():
        if not path.exists():
            continue
        # 只检查 24h 内有修改的日志（避免历史已修复的旧错误反复告警）
        if path.stat().st_mtime < cutoff_mtime:
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()[-200:]
        except Exception:
            continue
        error_pat = re.compile(
            r"Traceback|NameError|ImportError|SyntaxError|AttributeError|Exception:|Error:")
        crash_indices = [i for i, l in enumerate(lines) if error_pat.search(l)]
        if crash_indices:
            # 若最后一条错误之后存在成功标志，说明已自行恢复，不告警
            last_err_idx = crash_indices[-1]
            recovered = any(
                re.search(r"✅|完成|SUCCESS|completed", l, re.IGNORECASE)
                for l in lines[last_err_idx + 1:]
            )
            if not recovered:
                crashes = [lines[i] for i in crash_indices]
                problems.append(f"{name}: {len(crashes)}处异常（末行: {crashes[-1].strip()[:80]}）")

    if problems:
        return Check("日志异常", "warn",
                     "；".join(problems),
                     "查看对应日志文件定位具体错误")
    return Check("日志异常", "ok", "关键日志无崩溃（24h内）")


def check_cron_staleness() -> Check:
    """检查各 cron job 是否在预期频率内跑过（通过日志 mtime 判断）"""
    H = 3600
    now = time.time()
    jobs = [
        # (display_name, log_path, max_stale_seconds)
        ("data_quality(日)",     Path("/tmp/data_quality_check.log"),                           25 * H),
        ("price_snapshot(日)",   Path("/tmp/snapshot_daily_prices.log"),                        25 * H),
        ("cleanup(日)",          Path("/tmp/polymarket_cleanup.log"),                           25 * H),
        ("backup_configs(日)",   Path("/tmp/backup_configs.log"),                               25 * H),
        ("cleanup_tmp(日)",      Path("/tmp/cleanup_temp_files.log"),                           25 * H),
        ("pzero_weekly(周)",     Path("/home/xmren/.openclaw/workspace/polymarket-project/07-data/logs/weekly_pzero.log"), 8 * 24 * H),
        ("learning_review(周)",  Path("/home/xmren/.openclaw/workspace/logs/weekly_learning_review.log"), 8 * 24 * H),
        ("leaderboard(周)",      LEADERBOARD_LOG,                                               8 * 24 * H),
    ]
    stale, missing = [], []
    for name, path, max_stale in jobs:
        if not path.exists():
            missing.append(name)
            continue
        age = now - path.stat().st_mtime
        if age > max_stale:
            stale.append(f"{name}({age / H:.0f}h未跑)")

    problems = []
    if stale:
        problems.append("超时: " + "、".join(stale))
    if missing:
        problems.append("无日志: " + "、".join(missing))
    if problems:
        return Check("Cron健康", "warn", "；".join(problems),
                     "检查 crontab -l 条目是否正常；日志路径是否正确")
    return Check("Cron健康", "ok", f"全部 {len(jobs)} 个 job 按时运行")


def check_leaderboard_sync() -> Check:
    """Leaderboard 是否最近同步过（每周一跑）"""
    try:
        conn = sqlite3.connect(MAIN_DB)
        last_ts = conn.execute(
            "SELECT MAX(last_updated) FROM leaderboard_whales"
        ).fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM leaderboard_whales").fetchone()[0]
        conn.close()
    except Exception as e:
        return Check("Leaderboard同步", "warn", f"查询失败: {e}")

    if not last_ts or count == 0:
        return Check("Leaderboard同步", "critical", "leaderboard_whales 表为空",
                     "手动运行: cd 06-tools/analysis && python3 leaderboard_whale_tracker.py")

    try:
        dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_d = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        return Check("Leaderboard同步", "warn", f"时间解析失败: {last_ts}")

    if age_d > LEADERBOARD_STALE_DAYS:
        return Check("Leaderboard同步", "warn",
                     f"上次同步 {age_d:.1f} 天前（>{LEADERBOARD_STALE_DAYS}d），共 {count} 条",
                     "检查 crontab 的周一同步任务；或手动运行 leaderboard_whale_tracker.py")
    return Check("Leaderboard同步", "ok", f"上次 {age_d:.1f} 天前，共 {count} 条")


def check_clob_camera() -> Check:
    """CLOB 摄像头：最近采集时间"""
    if not CLOB_DB.exists():
        return Check("CLOB摄像头", "critical", "clob_pair_log.db 不存在",
                     "检查 crontab 里 clob_pair_logger 是否在跑")
    try:
        conn = sqlite3.connect(f"file:{CLOB_DB}?mode=ro", uri=True)
        last_ts, total_runs = conn.execute(
            "SELECT MAX(ts), COUNT(*) FROM clob_pair_runs"
        ).fetchone()
        conn.close()
    except Exception as e:
        return Check("CLOB摄像头", "warn", f"读取失败: {e}")

    if not last_ts:
        return Check("CLOB摄像头", "critical", "无任何采集记录")

    try:
        dt = datetime.fromisoformat(last_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except Exception:
        return Check("CLOB摄像头", "warn", f"时间解析失败: {last_ts}")

    if mins > CLOB_STALE_MIN:
        return Check("CLOB摄像头", "critical",
                     f"停摆 {mins:.0f} 分钟（>{CLOB_STALE_MIN}min，漏≥2个周期）",
                     "查 /tmp/clob_pair_logger.log；检查锁文件；重启 cron")
    return Check("CLOB摄像头", "ok",
                 f"最近 {mins:.0f} 分钟前（共 {total_runs} 轮）")


def check_cron_duplicates() -> Check:
    """Cron 是否有重复条目"""
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines = [l.strip() for l in result.stdout.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        duplicates = [l for l in set(lines) if lines.count(l) > 1]
    except Exception as e:
        return Check("Cron重复", "warn", f"无法读取 crontab: {e}")

    if duplicates:
        return Check("Cron重复", "warn",
                     f"发现 {len(duplicates)} 条重复：{duplicates[0][:60]}...",
                     "crontab -e 手动清理重复条目")
    return Check("Cron重复", "ok", "无重复条目")


def check_db_sanity() -> Check:
    """数据库关键表行数是否在合理范围"""
    try:
        conn = sqlite3.connect(MAIN_DB)
        whales = conn.execute("SELECT COUNT(*) FROM whales").fetchone()[0]
        lb = conn.execute("SELECT COUNT(*) FROM leaderboard_whales").fetchone()[0]
        changes = conn.execute(
            "SELECT COUNT(*) FROM changes WHERE timestamp > datetime('now','-7 days')"
        ).fetchone()[0]
        conn.close()
    except Exception as e:
        return Check("数据库", "warn", f"查询失败: {e}")

    problems = []
    if whales < 100:
        problems.append(f"whales 表仅 {whales} 行（预期>1000）")
    if lb < 50:
        problems.append(f"leaderboard_whales 仅 {lb} 行（预期>100）")
    if changes < 100:
        problems.append(f"近7日 changes 仅 {changes} 行（预期>1000）")

    if problems:
        return Check("数据库", "warn", "；".join(problems),
                     "检查 data_sync 是否正常写入")
    return Check("数据库", "ok",
                 f"whales={whales} lb_whales={lb} 近7日changes={changes}")


# ───────────────────────── 汇总 & 报告 ─────────────────────

def run_all_checks() -> List[Check]:
    return [
        check_logs(),
        check_cron_staleness(),
        check_leaderboard_sync(),
        check_cron_duplicates(),
        check_db_sanity(),
    ]


def _worst(checks: List[Check]) -> str:
    if any(c.status == "critical" for c in checks):
        return "critical"
    if any(c.status == "warn" for c in checks):
        return "warn"
    return "ok"


def format_report(checks: List[Check], title: str = "🔍 系统健康巡检") -> str:
    worst = _worst(checks)
    status_label = {"ok": "🟢 全部正常", "warn": "🟡 有警告", "critical": "🔴 有严重问题"}[worst]
    now_str = datetime.now().strftime("%m-%d %H:%M")
    lines = [f"{title}", f"状态: {status_label}  |  {now_str}", ""]
    for c in checks:
        lines.append(f"{_icon(c.status)} {c.name}: {c.detail}")
        if c.status != "ok" and c.fix_hint:
            lines.append(f"   → {c.fix_hint}")
    return "\n".join(lines)


# ───────────────────────── 入口 ─────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Polymarket 系统健康巡检")
    ap.add_argument("--daily", action="store_true", help="每日汇总（无论好坏都发 Telegram）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发 Telegram")
    args = ap.parse_args()

    checks = run_all_checks()
    worst = _worst(checks)
    report = format_report(checks)
    _save_pending_issues(checks)   # 供 Claude Code 会话钩子读取

    print(report)

    state = _load_state()
    now = time.time()
    cooldown_ok = (now - state.get("last_alert_ts", 0)) > ALERT_COOLDOWN_S

    should_alert = False
    msg = report

    if args.daily:
        should_alert = True
        msg = "🗓️ 每日 " + report
    elif worst in ("warn", "critical"):
        status_worsened = state.get("last_status", "ok") == "ok"
        if status_worsened or cooldown_ok:
            should_alert = True
            # 附上修复提示
            bad = [c for c in checks if c.status != "ok"]
            msg = report
    elif worst == "ok" and state.get("last_status") in ("warn", "critical"):
        # 恢复通知
        should_alert = True
        msg = "✅ 系统已恢复正常\n\n" + report

    print()
    if should_alert and msg:
        if args.dry_run:
            print("[dry-run] 将发送以下 Telegram:\n" + msg)
        else:
            ok = _send(msg)
            print(f"Telegram 已发送: {ok}")
            _save_state(worst, alerted=True)
    else:
        _save_state(worst, alerted=False)
        print(f"（{worst}，无需告警）")

    sys.exit({"ok": 0, "warn": 1, "critical": 2}[worst])


if __name__ == "__main__":
    main()
