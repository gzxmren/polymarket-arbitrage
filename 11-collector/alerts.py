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

# Gamma 注册查询失败:实测 1530 轮稳态 p50=6 / p90=13 / p99=22 / max=40(每轮上限 40 个)。
# 旧阈值 3 → **76% 的轮次必然触发**,且与结算失败**相加**判定 → 日志里 1340 条洪水推送。
# 定在 p99 之上:正常背景静默,而 >25/40 = 六成以上注册失败才当系统性异常。(2026-08-03)
REGISTER_FAIL_THRESHOLD = 25
# 结算守望失败改**比率**判定:批量化后每轮检查数百个(原 80),绝对值阈值失去意义。
# 下限用于挡住小样本比率抖动(2/2=100% 不该告警)。
SETTLEMENT_FAIL_RATIO = 0.5
SETTLEMENT_FAIL_MIN = 20
# offset 截断是巨盘历史回填触上限的**稳态自愈事件**(近端已保留 + 下轮压频回填)。
# 实测心跳分布(首日 82 条):中位 1 / p90 3 / 最大 8。原逻辑 ov>0 就推 = 每轮洪水告警。
# 降级:截断只进汇总日志(collector_core 每轮打印 + 心跳 parquet 留痕),**只有尖峰
# (≥阈值,远超稳态峰值 8)才当"轮询系统性追不上"的真异常推 Telegram**。(2026-07-23 用户明令)
OFFSET_OVERFLOW_ALERT_THRESHOLD = 20


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
    if counts.get("firehose_fail", 0) > 0:
        triggers.append("🔴 firehose 抽风:采样 0 笔成交(Polymarket 恒有成交=抓取失败),本轮空转;"
                        "数据不丢(下轮自愈回填),但接口若持续失败须查 IP/限流")
    # 稳态截断(ov 低于阈值)不推:自愈事件,靠汇总日志 + 心跳留痕即可。
    # 只有尖峰(≥阈值)才异常——意味轮询系统性追不上,值得人工看一眼。
    if ov >= OFFSET_OVERFLOW_ALERT_THRESHOLD:
        triggers.append(f"⚠️ offset 截断尖峰 {ov} 个市场(远超稳态,轮询恐系统性追不上,须查压频/间隔)")
    # 注册链路与结算链路**分开判定**:相加既掩盖单边真异常,又制造噪音(2026-08-03)。
    rf = counts.get("register_fail", 0)
    if rf > REGISTER_FAIL_THRESHOLD:
        triggers.append(f"⚠️ Gamma 注册查询失败 {rf} 次(远超稳态,须查接口是否变更)")
    sf, sc = counts.get("settlement_lookup_fail", 0), counts.get("settlement_checked", 0)
    if sf > SETTLEMENT_FAIL_MIN and sc > 0 and sf / sc > SETTLEMENT_FAIL_RATIO:
        triggers.append(f"⚠️ 结算守望查询失败 {sf}/{sc}(整条链路恐已挂,地面真值会断供)")
    if not triggers:
        return False
    body = "🔴 <b>Polymarket 采集器守护告警</b>\n" + "\n".join(triggers)
    body += (f"\n\n本轮: 市场 {counts.get('total_markets_polled', 0)} / "
             f"新成交 {counts.get('new_trades', 0)} / 新结算 {counts.get('newly_resolved', 0)}")
    return _send(body)


if __name__ == "__main__":
    # 自测:稳态截断不推(False),尖峰才推(True)
    print("稳态截断 ov=2:", maybe_alert({"offset_overflow_count": 2, "total_markets_polled": 18}))
    print("截断尖峰 ov=25:", maybe_alert({"offset_overflow_count": 25, "total_markets_polled": 18}))
