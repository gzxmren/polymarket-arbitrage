#!/usr/bin/env python3
"""run_cycle.py — 守护层入口:一个采集周期。由 systemd --user timer 每 **15** 分钟调用。

⚠️ 间隔以 `deploy/systemd/polymarket-rebirth-collector.timer` 的 `OnCalendar` 为准
(仓库那份是权威,本机副本会被重装覆盖)。2026-08-04 从 10 分钟放宽到 15 分钟,
本文件的时间闸与 `alerts.CYCLE_BUDGET_S=900` 都是按 15 分钟算的 —— 改间隔必须三处同改。

兑现《数据契约 v1.2》守护层:
= 采集(run_once:Firehose 发现 + 逐市场增量轮询)
+ 结算守望(watch_settlements:已关闭未结算 → Gamma 抓官方结算)
+ 告警(maybe_alert:offset_overflow / Gamma 失败超阈值 → Telegram 摘要)
+ 每日 compaction(合并昨日小 Parquet,幂等 once/day)。

**幂等**:一切由 watermark 驱动;timer 多叫醒几次无副作用(重复轮询只增量、去重在查询层)。
故 systemd Persistent=true 的"休眠醒来补跑"天然安全。
"""
from __future__ import annotations

import datetime as dt
import sys
import time

import alerts
import collector_core
import cycle_state
import settlement_watcher
import storage_engine as se

# 每轮工作量必须能在 15 分钟间隔内跑完(否则被超时杀、永远跑不到写心跳=空转打转)。
# 上限见 alerts.CYCLE_BUDGET_S(2026-08-04 从 10 分钟/480s 放宽到 15 分钟/900s)。
# 实测冷启动 register 112 + poll 270(含大量 8000 笔全回填)单轮 >10 分钟。故双封顶:
# ---------- 发现层配额(2026-08-04 晚重定,全部有实测支撑)----------
# 实测每轮涌入 ~90 个新市场,而旧上限是 40 → new_registered 逐轮恒在 33~38
# (「恒定不变的计数」= 被上限削平,不是自然产出)。注册滞后实测 p50=94 分钟 / p90=17.8 小时。
DEFAULT_MAX_NEW = 100     # 上限必须够得着实测到达率(~90/轮),否则积压永远清不掉
# ⚠️ 接口硬顶(实测):offset > 10000 一律 HTTP 400
#    {"error":"max historical trades offset of 10000 exceeded"}
# → 最多拿到 11000 笔 ≈ **8.4 分钟**(按实测峰值 1316 笔/分钟 = 5000 笔 / 3.8 分钟)。
DEFAULT_SAMPLE = 11000    # 上限;实际翻多少由 watermark 决定(接上上一轮就停)

# ⭐已知的结构性缺口 —— 写下来是为了它不能悄悄变大。
# 采集间隔(15 分钟)> 采样够得着的时长(~8.4 分钟)→ **每轮必然漏掉 ~6.6 分钟**,
# 这段时间里只在别处成交过的市场,本轮看不见。翻页解决不了(接口不给)。
# 缓解:市场一旦注册,poll_market 会从 offset=0 补全历史(实测注册前累积成交
# p50=8 / p90=42 / max=8000,无一超 OFFSET_CAP=10000)→ **晚发现 ≠ 丢数据**;
# 真损失只有"一生从未被任何采样窗撞上"的市场。
# 根治要么降间隔到 <8 分钟(当前周期耗时装不下),要么把发现层拆成独立的轻量 timer。
# 由 test_firehose_coverage.py::test_known_structural_gap_is_written_down_and_reconciled 对账。
KNOWN_FIREHOSE_GAP_MIN = 6.6
DEFAULT_POLL_LIMIT = 50   # 每轮最多轮询 N 个市场(冷启动全回填 ~11s/个)
# 冷启动:全宇宙(~600)摊到 ~12 轮(~2 小时)跑满;之后 watermark 令轮询转增量、极快。

# ⭐两道时间闸 —— 计数闸挡不住的那一半。
# 发现层耗时 ≈ 常数 + N × (一次 Gamma 往返),而**往返时长不由我们决定**:
# 实测同一天从 ~1.2s 漂到 1.90s(代理隧道退化)。纯计数闸在延迟翻倍时让周期跟着翻倍
# → 撞 systemd 超时被杀(08-04 当天真的发生了 12 次,而被杀的是已干完大半活的周期)。
# 有时间闸就变成**降级**:本轮少采/少注册几个并出声计数,下轮继续。
# ⚠️ **每一段都要有闸,否则"周期有界"是假的**:第一版只给发现层和注册层加了闸,
# 判据里就敢写"最坏周期 = 各闸之和 + 实测其余" —— 而轮询层无界,代理一退化就穿底。
# 上限依据(2026-08-04 晚新配置真机实测两轮 499s / 453s):
#   固定开销 = 结算 61s(受 checked=800 配额约束)+ compaction 2s + 注册表读取 3s = 66s
#   90 + 180 + 170 + 66 = 506s < 慢周期告警线 540s(见 alerts.slow_cycle_threshold_s)
# 该算式由 test_registration_budget.py::test_worst_case_cycle_fits_under_the_alert_line 焊死。
#
# ⚠️ 三道闸目前**每轮都在咬**(实测 没轮到 注册45/轮询18,firehose 只翻到 8 页)——
# 即系统已在容量边缘,瓶颈是代理往返 ~2.4s/次。这不是配错了,是硬约束;
# 余量只剩 34s,慢周期告警线(540s)需在 08-11 用一周心跳分布重新推导。
FIREHOSE_TIME_BUDGET_S = 90    # 必须够翻满 11 页到接口硬顶,否则第 3 条的覆盖率白改
REGISTER_TIME_BUDGET_S = 180   # 实测 ~2.35s/个 → ~76 个/轮
POLL_TIME_BUDGET_S = 170       # 实测 ~5.6s/个(含新注册市场的冷启动全回填)
# ⭐第四道闸(2026-08-06 补)。此前结算段是唯一无闸的一段 —— 它靠 `checked=800` 这个
# **个数**配额约束,而个数挡不住延迟退化:同样 800 个,往返 1.1s→10s 就从 18s 变成 160s。
# 实测 160 轮心跳:p50 25s / p90 57s / p99 389s / max 441s(p50 与 max 差 17.6 倍),
# 441s 那轮把整轮推到 438s,离告警线只剩 ~100s,而那 100s 不由我们决定。
# 取值被两条红线夹出来:> 实测 p90(57s,否则稳态天天被砍)且 < 95s(否则四闸之和越过
# 540s 告警线)。推导与判据见 10-tests/unit/test_settlement_time_gate.py。
SETTLEMENT_TIME_BUDGET_S = 80


COMPACT_MIN_FILES = 50   # 分区文件数超此值即合并(含被回填污染的旧分区)


def _compaction_sweep(counts: dict | None = None) -> list[str]:
    """扫全部分区,合并文件数超阈值的(不只"昨天")。

    自限:合并后分区落到 1 个大文件,须再累积 >阈值 才会被下轮重新合并 → 天然幂等、不空转。
    修掉旧"每天只合并昨天一次"的盲区(回填写进旧日期分区 → 小文件永久累积)。
    """
    return se.compact_due_partitions(min_files=COMPACT_MIN_FILES, counts=counts)


SLOW_STREAK_FILE = se.DATA_ROOT / "state" / "slow_cycle_streak.json"
# 游标空洞连计(轮询与结算两处共用一条:两者同进程同节奏,任一处出空洞都算"这轮命中"）。
# ⚠️ 是**布尔或**不是相加 —— 相加会稀释阈值,而 CLAUDE.md 禁止把独立链路的计数相加。
# 各自的个数仍分别进心跳(poll_rotation_holes / settlement_rotation_holes),不合并。
HOLE_STREAK_FILE = se.DATA_ROOT / "state" / "rotation_hole_streak.json"


def main(sample: int = DEFAULT_SAMPLE, max_new: int | None = DEFAULT_MAX_NEW,
         poll_limit: int | None = DEFAULT_POLL_LIMIT) -> int:
    t0 = time.monotonic()   # 单调钟:测耗时不能用 wall clock(NTP 校时会把它拨得忽前忽后)
    print(f"=== 采集周期 {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S}Z ===", flush=True)

    # 各阶段分开计时。2026-08-04 事故里周期从 130s 涨到 480s,而日志里**没有任何**
    # 能指出"慢在哪一段"的信息 —— 当时只能靠手工逐段实测才定位到网络。那是可观测性缺口,
    # 不是"注意一点"能避免的,故焊进日志。
    t = time.monotonic()
    counts = collector_core.run_once(
        limit=poll_limit, sample=sample, max_new=max_new,
        sample_time_budget_s=FIREHOSE_TIME_BUDGET_S,
        register_time_budget_s=REGISTER_TIME_BUDGET_S,
        poll_time_budget_s=POLL_TIME_BUDGET_S)
    t_collect = time.monotonic() - t

    t = time.monotonic()
    # 结算守望的网络失败也计进同一份 net_*(同一条代理隧道,见 discovery_service.new_net_stats)
    settle = settlement_watcher.watch_settlements(
        net=counts, time_budget_s=SETTLEMENT_TIME_BUDGET_S)
    t_settle = time.monotonic() - t
    print(f"结算守望: {settle}", flush=True)

    t = time.monotonic()
    comp = _compaction_sweep(counts)
    t_compact = time.monotonic() - t
    if comp:
        print(f"compaction: 合并 {len(comp)} 个分区 {comp}", flush=True)

    dur = time.monotonic() - t0
    slow_streak = cycle_state.next_hit_streak(
        cycle_state.read_state(SLOW_STREAK_FILE, "streak", 0), hit=alerts.is_slow_cycle(dur))
    cycle_state.write_state(SLOW_STREAK_FILE, "streak", slow_streak, "慢周期连计数")

    hit_hole = counts.get("poll_rotation_holes", 0) > 0 or settle["rotation_holes"] > 0
    hole_streak = cycle_state.next_hit_streak(
        cycle_state.read_state(HOLE_STREAK_FILE, "streak", 0), hit=hit_hole)
    cycle_state.write_state(HOLE_STREAK_FILE, "streak", hole_streak, "游标空洞连计")

    merged = {
        **counts,
        "newly_resolved": settle["newly_resolved"],
        "settlement_lookup_fail": settle["lookup_fail"],
        "settlement_checked": settle["checked"],  # 失败按比率判定,须带上分母
        # 被时间闸砍掉几个。稳态应恒 0;持续非零 = 结算吞吐在悄悄掉,而 checked 本身看不出来
        "settlement_timegate_skipped_count": settle["timegate_skipped"],
        "truth_supply_zero_streak": settle["zero_streak"],  # 真值断供守护(静默失败)
        # 游标空洞:两处的个数分别留痕(处置方向不同),连计数只有一条(见 HOLE_STREAK_FILE)
        "settlement_rotation_holes": settle["rotation_holes"],
        "rotation_hole_streak": hole_streak,
        "cycle_seconds": dur,
        # run_once 内部再拆成发现/轮询两段(它自己填 discovery_seconds),这里减出纯轮询耗时
        "poll_seconds": t_collect - counts.get("discovery_seconds", 0.0),
        "settlement_seconds": t_settle,
        "compaction_seconds": t_compact,
        "slow_cycle_streak": slow_streak,
    }
    # ⭐告警放在心跳**之前**发(2026-08-07 调整):告警送达与否是本轮的产出之一,
    # 发完才知道队列深度。心跳仍是整轮最后一件事,故"心跳新鲜 = 整轮真跑完了"不变。
    ar = alerts.maybe_alert_with_counts(merged)
    # ⚠️ 用返回值,**不许**在这里重新 queue_stats():送达成功时队列文件已被删,
    # 重读恒为 0 —— 而"成功了"恰恰是 dropped 最不该沉默的时刻(2026-08-07 评审抓出)。
    merged["alert_queue_depth"] = ar["queue_depth"]      # 消费者:看门狗读心跳
    merged["alert_dropped_count"] = ar["dropped"]
    # 告警最坏阻塞约 30s。它以前算在 cycle_seconds 之外 ⇒ 心跳系统性少算,
    # 且恰好在网络最差、最该被准确记录的那些轮次。故耗时在告警之后定稿。
    # ⚠️ `slow_cycle_streak` 仍用告警**之前**的耗时算:判定结果要拿去生成告警正文,
    # 用告警之后的数就成了先有鸡还是先有蛋。两者差最多 ~30s,不影响 540s 那条线。
    merged["cycle_seconds"] = time.monotonic() - t0
    # 心跳写在这里(而非 run_once 内)= "整轮真跑完了"的凭证,详见 collector_core 里的说明。
    se.write_audit_heartbeat(merged)

    # 出声:否则"发不出去"或"补发成功"都只剩 stderr 里一行,而那行没人读(本次要修的病)
    line = alerts.dispatch_log_line(ar)
    if line:
        print(line, flush=True)

    print(f"=== 周期结束 {dur:.0f}s "
          f"(发现 {merged['discovery_seconds']:.0f}s / 轮询 {merged['poll_seconds']:.0f}s / "
          f"结算 {t_settle:.0f}s / compaction {t_compact:.0f}s"
          f" | 慢周期连计 {slow_streak}) ===", flush=True)
    return 0


if __name__ == "__main__":
    # 默认每轮注册上限 DEFAULT_MAX_NEW(防冷启动撑爆超时);可传参覆盖(0=不注册新市场)。
    max_new = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MAX_NEW
    sys.exit(main(max_new=max_new))
