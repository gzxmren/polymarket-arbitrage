#!/usr/bin/env python3
"""alerts.py — 守护层告警(复用项目现有 telegram_notifier_v2,不再造轮子)。

兑现《数据契约 v1.2》§7:offset_overflow>0 或 register_fail>3 时推一条**摘要**(含市场/计数),
不搞洪水告警。Telegram 不可用时 graceful degradation(打印 stderr,不崩)。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 复用项目毛细血管:06-tools/monitoring/telegram_notifier_v2
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "06-tools" / "monitoring"))
try:
    import telegram_notifier_v2 as _tg  # noqa: E402
    TELEGRAM_ENABLED = True
except Exception:  # 缺依赖/配置 → 关掉该功能而非崩(项目 graceful-degradation 惯例)
    TELEGRAM_ENABLED = False

REGISTER_FAIL_THRESHOLD = 3


def _send(msg: str) -> bool:
    if not TELEGRAM_ENABLED:
        print(f"[Telegram 未启用] {msg}", file=sys.stderr)
        return False
    try:
        return bool(_tg.send_telegram_message(msg))
    except Exception as e:  # 发送失败不影响采集主流程
        print(f"[Telegram 发送失败] {e}", file=sys.stderr)
        return False


def maybe_alert(counts: dict) -> bool:
    """按阈值决定是否告警。返回是否真的推送了。"""
    triggers = []
    ov = counts.get("offset_overflow_count", 0)
    rf = counts.get("register_fail", 0) + counts.get("settlement_lookup_fail", 0)
    if ov > 0:
        triggers.append(f"⚠️ offset 截断 {ov} 个市场(历史回填触 1 万上限,近端已保留,更早不可得)")
    if rf > REGISTER_FAIL_THRESHOLD:
        triggers.append(f"⚠️ Gamma 查询失败 {rf} 次(注册/结算,已计数,须查接口是否变更)")
    if not triggers:
        return False
    body = "🔴 <b>Polymarket 采集器守护告警</b>\n" + "\n".join(triggers)
    body += (f"\n\n本轮: 市场 {counts.get('total_markets_polled', 0)} / "
             f"新成交 {counts.get('new_trades', 0)} / 新结算 {counts.get('newly_resolved', 0)}")
    return _send(body)


if __name__ == "__main__":
    # 自测:模拟触发
    print("触发测试:", maybe_alert({"offset_overflow_count": 2, "total_markets_polled": 18}))
