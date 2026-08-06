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
# 🔴 2026-08-06 更正:这里原本写着 offset 截断是「稳态**自愈**事件(近端已保留 +
# 下轮压频回填)」—— **前半句是假的,后半句承诺的东西不存在**。
# 实测:撞顶的 377 个市场,序列开头空白 p50 199 天 / p90 327 天(对照组 1 天 / 6 天)。
# 水位线是 max(timestamp),近端一写进去就跳到最新,下轮再也不往回翻 ⇒ **永久空洞**;
# 而"压频"回不了 —— offset 顶 10000 是接口硬约束,不是频率问题。
# 在一个错的框架里调阈值,正是 CLAUDE.md 第 4 条要防的("先问这个量本身该不该被监控")。
#
# 拆开之后两半的处置完全不同:
#   cold(首次全量就超顶)= 接口硬约束,推它只是噪音 → 不告警,只留痕
#   warm(两轮之间攒爆)  = ⭐可修,等价于轮转一圈太久 → **发生即红线被踩穿,必推**
# 总数阈值保留给"尖峰"这层旧语义(轮询系统性追不上),不删,免得回归掉旧行为。
OFFSET_OVERFLOW_ALERT_THRESHOLD = 20

# ---------- 慢周期守护(2026-08-04)----------
# 由来:当天 12 轮撞 systemd 超时被 SIGTERM 杀,吞吐可见下滑,而**所有既有告警一条没响**
# —— 心跳里根本没有"耗时"这个量,故"如果它现在就是坏的,我看到的会有什么不同?"答案是没有。
#
# 为什么监控"耗时"而不是"网络失败率":实测 60 次 _get 调用 0 次重试耗尽(单次失败率 4.8%
# 时),重试把瞬时失败全吸收了 → give-up 告警**抓不住这场事故**;而单次失败率今天现测
# 4.8%~23% 剧烈抖动、无稳态基线,此刻定阈值等于拍脑袋(计数已入心跳,攒一周再校准)。
# 耗时则不同:它变坏 = 周期被杀、吞吐下滑确实发生,且红线是**结构性**的(超过 systemd
# 上限就是真被杀),不是调出来的参数。
CYCLE_BUDGET_S = 900          # ⚠️ 必须 == .service 的 TimeoutStartSec == .timer 间隔
SLOW_CYCLE_RATIO = 0.6        # 告警线 = 540s
# 依据(journalctl 实测 148 个成功周期,剔除本次事故污染后健康子集 n=134):
#   p50=115s  p90=167s  p95=182s  p99=221s  max=228s
# 540s 对实测健康 max 有 2.4 倍余量(稳态静默),距被杀仍留 360s(是预警不是讣告)。
SLOW_CYCLE_ALERT_CYCLES = 4   # 防洪:单发不推(自愈噪声),连续 4 轮(1 小时)才推、之后按整数倍复述


def cycle_minutes() -> int:
    """一轮实际间隔(分钟)。**必须从预算派生**,不许在文案里写死。

    2026-08-04 教训:间隔从 10 分钟改成 15 分钟时,两条告警文案里的 `轮数 * 10 // 60`
    没人改 —— 于是"连续 18 轮"实际已过 4.5 小时,文案却说"约 3 小时",在最需要准确传达
    严重性的时刻低报三分之一。写死的常量必然与它描述的对象分叉。
    """
    return CYCLE_BUDGET_S // 60


def slow_cycle_threshold_s() -> float:
    """慢周期告警线。由预算派生而非独立写死 —— 改预算时告警线自动跟着走,不会漂移。"""
    return CYCLE_BUDGET_S * SLOW_CYCLE_RATIO


def is_slow_cycle(seconds: float) -> bool:
    return seconds >= slow_cycle_threshold_s()


def _slow_cycle_hint(counts: dict) -> str:
    """按**实际观测到的失败形态**给排查方向,而不是无论如何都喊"查代理隧道"。

    指错方向的告警比不告警更贵:它会让人在错的地方找半天,并在下次学会忽略这条告警。
    """
    if counts.get("rate_limit_give_up_count", 0) > 0:
        return "→ 主因像**限流**(429 打满):该压频/拉长间隔,不是查隧道"
    if counts.get("net_server_error_count", 0) > 0:
        return "→ 主因像**对端 5xx**:Polymarket 侧暂时挂了,通常自愈,先观察"
    if counts.get("net_retry_count", 0) > 0:
        return "→ 主因像**网络/代理**:查代理隧道(v2rayN/sing-box)、出口 IP、DNS"
    return "→ 网络计数干净:慢在本地(看各阶段耗时,尤其 compaction/registry 是否变大)"


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
    body = build_alert(counts)
    return _send(body) if body else False


def build_alert(counts: dict) -> str | None:
    """拼出告警正文;无触发返回 None。

    与 `maybe_alert` 分开是为了**判据能验内容而不只验"推没推"**:
    "推了一条"通不过"推的是不是那件事"这一问。
    """
    triggers = []
    ov = counts.get("offset_overflow_count", 0)
    if counts.get("firehose_fail", 0) > 0:
        triggers.append("🔴 firehose 抽风:采样 0 笔成交(Polymarket 恒有成交=抓取失败),本轮空转;"
                        "数据不丢(下轮自愈回填),但接口若持续失败须查 IP/限流")
    # 稳态截断(ov 低于阈值)不推:自愈事件,靠汇总日志 + 心跳留痕即可。
    # 只有尖峰(≥阈值)才异常——意味轮询系统性追不上,值得人工看一眼。
    if ov >= OFFSET_OVERFLOW_ALERT_THRESHOLD:
        triggers.append(f"⚠️ offset 截断尖峰 {ov} 个市场(远超稳态,轮询恐系统性追不上,须查间隔/名额)")
    # ⭐增量截断:有水位线却没追上 = 两轮之间攒了 >10,000 笔 = **轮转一圈太久**,
    # 即 test_poll_rotation.py 那条红线(一圈须短于 OFFSET_CAP / p99.9 成交率 ≈ 11.6h)
    # 被踩穿的现场证据。后果是序列中间出一个**永久**空洞(实测同类空白 p50 199 天)。
    # 阈值不需要实测分布:红线已经写死"不该发生",发生一次就是踩穿。
    # 防洪的另一头由判据焊住:cold(接口硬约束)再多也不推,稳态因此完全静默。
    ow = counts.get("offset_overflow_warm_count", 0)
    if ow > 0:
        triggers.append(
            f"🔴 增量 offset 截断 {ow} 个市场:两轮之间攒爆 10,000 笔 = **轮转一圈太久**"
            f"(红线 ≈11.6 小时)。这些市场的序列**中间**已出现永久空洞,补不回来 —— "
            f"须缩短一圈(加名额/加轮转片),不是调告警")
    # 注册链路断供(静默失败)。register_fail 个数本身不再告警 —— 单次失败会自愈,
    # 数个数是错的形状;失败仍逐轮进日志/心跳留痕,只是不再打扰人。
    rzs = counts.get("register_zero_streak", 0)
    if rzs > 0 and rzs % REGISTER_ZERO_CYCLES == 0:
        triggers.append(
            f"🔴 注册链路断供:连续 {rzs} 轮(约 {rzs * cycle_minutes() / 60:.1f} 小时)有市场可登记却一个"
            f"**新市场**都没登记成功。正常每轮 ~34 个。新市场进不来=宇宙停止增长,须查 Gamma 接口")
    sf, sc = counts.get("settlement_lookup_fail", 0), counts.get("settlement_checked", 0)
    if sf > SETTLEMENT_FAIL_MIN and sc > 0 and sf / sc > SETTLEMENT_FAIL_RATIO:
        triggers.append(f"⚠️ 结算守望查询失败 {sf}/{sc}(整条链路恐已挂,地面真值会断供)")
    # 真值断供:静默失败(一切正常但产出为 0),靠连零轮数发现。
    # 防洪:只在恰好跨过阈值的整数倍时推 → 断供期间每 3 小时复述一次,而非每轮。
    zs = counts.get("truth_supply_zero_streak", 0)
    if zs > 0 and zs % TRUTH_SUPPLY_ZERO_CYCLES == 0:
        triggers.append(
            f"🔴 结算真值断供:连续 {zs} 轮(约 {zs * cycle_minutes() / 60:.1f} 小时)有市场可查却一个都没结算。"
            f"正常每轮期望 ~20 个。须查结算守望链路(Gamma 接口/轮转游标/注册表)")
    # 慢周期:单发是自愈噪声(网络抖一下),**持续**才是真退化 → 只在连续 N 轮的整数倍推。
    scs = counts.get("slow_cycle_streak", 0)
    if scs > 0 and scs % SLOW_CYCLE_ALERT_CYCLES == 0:
        triggers.append(
            f"⚠️ 周期耗时持续偏高:连续 {scs} 轮 ≥{slow_cycle_threshold_s():.0f}s"
            f"(本轮 {counts.get('cycle_seconds', 0):.0f}s,预算 {CYCLE_BUDGET_S}s)。"
            f"实测健康区间 p99≈221s;再涨就会撞 systemd 超时被杀。"
            f"网络重试 {counts.get('net_retry_count', 0)}/{counts.get('net_attempt_count', 0)}"
            f",重试耗尽 {counts.get('net_give_up_count', 0)}"
            f",服务端 5xx {counts.get('net_server_error_count', 0)}"
            f",限流放弃 {counts.get('rate_limit_give_up_count', 0)}"
            f",分页截断 {counts.get('poll_truncated_count', 0)}"
            f"/{counts.get('firehose_truncated_count', 0)}\n"
            + _slow_cycle_hint(counts))
    if not triggers:
        return None
    body = "🔴 <b>Polymarket 采集器守护告警</b>\n" + "\n".join(triggers)
    body += (f"\n\n本轮: 市场 {counts.get('total_markets_polled', 0)} / "
             f"新成交 {counts.get('new_trades', 0)} / 新结算 {counts.get('newly_resolved', 0)}")
    return body


if __name__ == "__main__":
    # 自测:稳态截断不推(False),尖峰才推(True)
    print("稳态截断 ov=2:", maybe_alert({"offset_overflow_count": 2, "total_markets_polled": 18}))
    print("截断尖峰 ov=25:", maybe_alert({"offset_overflow_count": 25, "total_markets_polled": 18}))
