#!/usr/bin/env python3
"""
Monitor Lite State 安全清理脚本
功能：清理 sent_whale_keys 中的过期条目（保留最近 N 天）
特点：带备份 + 干跑模式 + 验证

用法：
  python3 monitor_state_cleanup.py --dry-run          # 干跑（默认，只显示将要删除的数量）
  python3 monitor_state_cleanup.py --days 7           # 清理 7 天前的 key
  python3 monitor_state_cleanup.py --execute          # 真正执行（必须显式指定）
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# 配置
STATE_FILE = Path("/home/xmren/.openclaw/workspace/polymarket-project/07-data/monitor_lite_state.json")
BACKUP_DIR = Path("/home/xmren/.openclaw/workspace/polymarket-project/07-data/backup")
RETENTION_DAYS = 7


def load_state():
    if not STATE_FILE.exists():
        print("❌ STATE_FILE 不存在")
        sys.exit(1)
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def backup_state():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"monitor_lite_state_{timestamp}.json"
    shutil.copy2(STATE_FILE, backup_path)
    return backup_path


def prune_old_whale_keys(state, retention_days):
    """清理 sent_whale_keys 中超过 retention_days 的条目"""
    raw_whale = state.get("sent_whale_keys", {})
    if not raw_whale:
        return 0, 0, 0

    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - (retention_days * 86400)

    before_count = len(raw_whale)

    # 找出要保留的
    kept = {k: v for k, v in raw_whale.items() if v > cutoff}
    after_count = len(kept)
    removed_count = before_count - after_count

    state["sent_whale_keys"] = kept
    return before_count, after_count, removed_count


def verify_state(state):
    """验证清理后的状态"""
    whale_keys = state.get("sent_whale_keys", {})
    if not whale_keys:
        return "sent_whale_keys 为空"

    timestamps = list(whale_keys.values())
    oldest = min(timestamps)
    newest = max(timestamps)

    oldest_dt = datetime.fromtimestamp(oldest, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    newest_dt = datetime.fromtimestamp(newest, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    return f"剩余 {len(whale_keys)} 条 | 最旧: {oldest_dt} | 最新: {newest_dt}"


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=RETENTION_DAYS, help="保留最近 N 天")
    parser.add_argument("--execute", action="store_true", help="真正执行清理（默认 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="强制干跑模式")
    args = parser.parse_args()

    dry_run = not args.execute or args.dry_run

    print("=== Monitor Lite State 清理脚本 ===")
    print(f"STATE_FILE: {STATE_FILE}")
    print(f"保留天数: {args.days}")
    print(f"模式: {'DRY-RUN（仅预览）' if dry_run else 'EXECUTE（真实执行）'}")
    print()

    # 加载
    state = load_state()
    before_count = len(state.get("sent_whale_keys", {}))

    # 备份（无论是否 dry-run 都先备份）
    backup_path = backup_state()
    print(f"✅ 已备份到: {backup_path}")

    # 执行清理
    before, after, removed = prune_old_whale_keys(state, args.days)

    print(f"\n清理前 sent_whale_keys 数量: {before}")
    print(f"清理后数量: {after}")
    print(f"将删除/已删除: {removed} 条")

    if dry_run:
        print("\n⚠️  当前为 DRY-RUN 模式，未实际修改文件。")
        print("如需真正执行，请添加 --execute 参数。")
        # 恢复 state（因为我们修改了内存中的 state）
        state = load_state()
    else:
        save_state(state)
        print(f"\n✅ 已写入清理后的状态到 {STATE_FILE}")

    # 验证
    final_state = load_state()
    verify_result = verify_state(final_state)
    print(f"\n验证结果: {verify_result}")

    print("\n=== 完成 ===")


if __name__ == "__main__":
    main()