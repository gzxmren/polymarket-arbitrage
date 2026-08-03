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

# 注册链路断供守护:连续 N 轮"有市场可登记却一个都没成功"。
# 为什么不再数失败个数:单次注册失败**会自愈**(市场还在交易,下轮会被重新登记),稳态每轮
# 失败 ~6 个纯属正常损耗 —— 数个数是**错的形状**(旧阈值 3 → 76% 轮次必推,共 1340 条;
# 定高了又抓不住真事故)。真事故是接口挂掉 → 全部失败 → 新市场再也进不来、宇宙悄悄停止增长。
# 依据(实测 1541 轮):new_registered 中位 34/轮,为 0 占 3.8%,**最长自然连零 10 轮**。
# 取 18 轮(3 小时)≈ 2 倍余量;且只在"确实有市场可登记"时进位,实际更难自然达成。
REGISTER_ZERO_CYCLES = 18
# 结算守望失败改**比率**判定:批量化后每轮检查数百个(原 80),绝对值阈值失去意义。
# 下限用于挡住小样本比率抖动(2/2=100% 不该告警)。
SETTLEMENT_FAIL_RATIO = 0.5
SETTLEMENT_FAIL_MIN = 20
# 真值断供守护:连续 N 轮"有市场可查却一个都没结算"就报警。
# 补的是 2026-08-03 暴露的监控盲点 —— 结算真值静默死了 11 天,同期推了 1340 条告警,
# 却没有一条是关于它的:监控只覆盖"接口报错",对"一切正常但产出为 0"完全是瞎的。
# 依据(实测):近 14 天每天自然结算 ~2904 个市场(中位)→ 每轮(10 分钟)期望 ~20 个,
# 故连续 18 轮(3 小时)一个都没有是强异常。⚠️ 修复后仅 6 轮观测且全是清存量轮次,
# 尚无稳态分布 → 此值为保守初值,zero_streak 已逐轮入日志,攒够一周应回来校准。
TRUTH_SUPPLY_ZERO_CYCLES = 18
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
    # 注册链路断供(静默失败)。register_fail 个数本身不再告警 —— 单次失败会自愈,
    # 数个数是错的形状;失败仍逐轮进日志/心跳留痕,只是不再打扰人。
    rzs = counts.get("register_zero_streak", 0)
    if rzs > 0 and rzs % REGISTER_ZERO_CYCLES == 0:
        triggers.append(
            f"🔴 注册链路断供:连续 {rzs} 轮(约 {rzs * 10 // 60} 小时)有市场可登记却一个"
            f"**新市场**都没登记成功。正常每轮 ~34 个。新市场进不来=宇宙停止增长,须查 Gamma 接口")
    sf, sc = counts.get("settlement_lookup_fail", 0), counts.get("settlement_checked", 0)
    if sf > SETTLEMENT_FAIL_MIN and sc > 0 and sf / sc > SETTLEMENT_FAIL_RATIO:
        triggers.append(f"⚠️ 结算守望查询失败 {sf}/{sc}(整条链路恐已挂,地面真值会断供)")
    # 真值断供:静默失败(一切正常但产出为 0),靠连零轮数发现。
    # 防洪:只在恰好跨过阈值的整数倍时推 → 断供期间每 3 小时复述一次,而非每轮。
    zs = counts.get("truth_supply_zero_streak", 0)
    if zs > 0 and zs % TRUTH_SUPPLY_ZERO_CYCLES == 0:
        triggers.append(
            f"🔴 结算真值断供:连续 {zs} 轮(约 {zs * 10 // 60} 小时)有市场可查却一个都没结算。"
            f"正常每轮期望 ~20 个。须查结算守望链路(Gamma 接口/轮转游标/注册表)")
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
