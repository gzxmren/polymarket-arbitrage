#!/usr/bin/env python3
"""Test script for should_keep_whale() scoring logic (Modification 1)"""
import sys
sys.path.insert(0, '.')
from whale_tracker_v2 import should_keep_whale, INACTIVE_DAYS_THRESHOLD, KEEP_SCORE_THRESHOLD
from datetime import datetime, timezone

now = datetime.now(timezone.utc).timestamp()
day = 86400

def test(name, info, expected):
    result = should_keep_whale('0x' + 'a' * 40, info)
    status = "✅" if result == expected else "❌"
    # Show score breakdown
    score = 0
    recent_vol = info.get('total_volume', 0)
    recent_tc = info.get('trade_count', 0)
    if recent_vol >= 1000 and recent_tc >= 3:
        score += 3
    lt = info.get('large_trades', 0)
    if isinstance(lt, (int, float)) and lt >= 3:
        score += 2
    hv = info.get('historical_volume', 0)
    if isinstance(hv, (int, float)) and hv >= 5000:
        score += 3
    dc = info.get('db_changes_count', 0)
    if isinstance(dc, (int, float)) and dc >= 3:
        score += 1
    last_trade = info.get('last_trade', 0)
    if last_trade > 0:
        days_inactive = (now - last_trade) / 86400
        if days_inactive >= INACTIVE_DAYS_THRESHOLD:
            score -= 2
    print(f"  {status} {name}: expect={expected} actual={result} (score={score})")

def section(title):
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")

section("评分 >= 3 — 应保留 (return True)")

test("活跃交易(vol>=1k + trades>=3) = +3",
     {"total_volume": 1500, "trade_count": 5, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now},
     True)

test("大单>=3 (+2) + DB记录>=3 (+1) = +3",
     {"total_volume": 500, "trade_count": 2, "large_trades": 3,
      "historical_volume": 0, "db_changes_count": 4, "last_trade": now},
     True)

test("历史交易量>=5k (+3) = +3",
     {"total_volume": 0, "trade_count": 0, "large_trades": 0,
      "historical_volume": 8000, "db_changes_count": 0, "last_trade": now},
     True)

test("大单>=3 (+2) + vol略低不满足活跃 = +2 < 3 → 淘汰",
     {"total_volume": 800, "trade_count": 5, "large_trades": 3,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now},
     False)

section("评分 < 3 — 应淘汰 (return False)")

test("完全无数据",
     {"total_volume": 0, "trade_count": 0, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": 0},
     False)

test("仅大单1次(+0) + 不活跃(-2) = -2",
     {"total_volume": 100, "trade_count": 1, "large_trades": 1,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now - 40*day},
     False)

test("仅DB记录1次(+0) = 0",
     {"total_volume": 0, "trade_count": 0, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 1, "last_trade": now},
     False)

test("小量交易(vol=500,trades=2) + 不活跃(-2) + 大单2次(+0) = -2",
     {"total_volume": 500, "trade_count": 2, "large_trades": 2,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now - 35*day},
     False)

section("边界测试")

test("正好活跃(vol=1000,trades=3) = +3",
     {"total_volume": 1000, "trade_count": 3, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now},
     True)

test("正好大单3次 = +2 (<3, 淘汰)",
     {"total_volume": 0, "trade_count": 0, "large_trades": 3,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now},
     False)

test("正好历史vol 5000 = +3",
     {"total_volume": 0, "trade_count": 0, "large_trades": 0,
      "historical_volume": 5000, "db_changes_count": 0, "last_trade": now},
     True)

test("DB记录3次(+1) + 不活跃(-2) = -1 < 3",
     {"total_volume": 0, "trade_count": 0, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 3, "last_trade": now - 31*day},
     False)

test("刚好第30天(=threshold, 扣分) + 无其他 = -2 < 3",
     {"total_volume": 0, "trade_count": 0, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now - 30*day},
     False)

test("last_trade=0 (无交易记录, 不扣分, 总分0 < 3)",
     {"total_volume": 0, "trade_count": 0, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": 0},
     False)

test("活跃(+3) + 但不活跃太久(-2) = +1 < 3",
     {"total_volume": 1500, "trade_count": 5, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now - 35*day},
     False)

# 对比旧逻辑 vs 新评分制的差异
section("旧逻辑 vs 新评分制差异测试")

# 旧逻辑: large_trades>=3 → 直接保留（即使其他字段为0）
# 新评分制: large_trades>=3 → +2分, 总分仍需 >=3
test("仅大单3次(+2), 无其他 → 旧逻辑保留, 新评分淘汰",
     {"total_volume": 0, "trade_count": 0, "large_trades": 3,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": 0},
     False)  # score=2 < 3

# 旧逻辑: recent_vol>=1000 AND trade_count>=3 → 保留（即使last_trade是30天前）
# 新评分制: 满足活跃条件(+3), 但不活跃扣分(-2) = +1 < 3 → 淘汰
test("活跃(+3)但30天无交易(-2)= +1 → 新评分淘汰",
     {"total_volume": 1000, "trade_count": 3, "large_trades": 0,
      "historical_volume": 0, "db_changes_count": 0, "last_trade": now - 31*day},
     False)  # score=3-2=1

print()
print(f"INACTIVE_DAYS_THRESHOLD={INACTIVE_DAYS_THRESHOLD}  KEEP_SCORE_THRESHOLD={KEEP_SCORE_THRESHOLD}")
print("Note: 旧逻辑中 large_trades>=3 直接保留；新评分制中需累积到3分")
