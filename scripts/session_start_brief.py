#!/usr/bin/env python3
"""
Claude Code 会话启动简报

每次在 polymarket-project 目录打开 Claude Code，此脚本自动运行。
读取最新的健康检查结果，如有待处理问题，输出到对话上下文，
让 Claude 在会话开始时就看到并主动跟进，不需要用户手工提醒。
"""

import json
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ISSUES_FILE  = PROJECT_ROOT / "07-data" / ".pending_issues.json"
HEALTH_LOG   = Path("/tmp/auto_health_check.log")


def main():
    # 没有文件 = 健康检查从未运行过
    if not ISSUES_FILE.exists():
        # 静默退出，不打扰正常会话
        return

    try:
        data = json.loads(ISSUES_FILE.read_text())
    except Exception:
        return

    issues = data.get("issues", [])
    checked_at = data.get("checked_at", "未知")
    worst = data.get("worst", "ok")

    if not issues:
        # 全部正常，静默退出
        return

    icon = {"warn": "🟡", "critical": "🔴"}.get(worst, "⚪")
    lines = [
        f"{icon} [Polymarket 健康巡检] 发现 {len(issues)} 个待处理问题（上次检查: {checked_at}）",
        "",
    ]
    for item in issues:
        status_icon = "🔴" if item["status"] == "critical" else "🟡"
        lines.append(f"{status_icon} {item['name']}: {item['detail']}")
        if item.get("fix_hint"):
            lines.append(f"   修复提示: {item['fix_hint']}")

    lines += [
        "",
        "→ 请告知 Claude：「跟进健康检查问题」或「auto_health_check」",
        "→ 重新检查：python3 scripts/auto_health_check.py --dry-run",
    ]

    print("\n".join(lines))


if __name__ == "__main__":
    main()
