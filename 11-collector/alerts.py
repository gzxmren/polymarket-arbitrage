#!/usr/bin/env python3
"""alerts.py — 守护层告警(复用项目现有 telegram_notifier_v2,不再造轮子)。

兑现《数据契约 v1.2》§7:offset_overflow>0 或 register_fail>3 时推一条**摘要**(含市场/计数),
不搞洪水告警。Telegram 不可用时 graceful degradation(打印 stderr,不崩)。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cycle_state
import storage_engine as se

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

# ---------- 游标空洞守护(2026-08-07,随 rotation.py 一同立)----------
# 空洞 = 轮转片里"越过一个没做成的继续往后做"。游标用**保守规则**停在空洞前面
# (不跨过去,免得那一个每圈都被跳过 —— 丢得与结果相关正是本项目的死因),
# 而保守规则的反面风险是**卡死**:某个市场每轮都做不成,后面全饿死。
#
# 阈值不需要实测分布,因为实测分布就是**恒 0**:今天三处的循环都是"做一个报一个,
# 中断即中断",结构上产生不出空洞(判据 test_rotation.py::test_steady_state_has_zero_holes)。
# 故这是一条红线告警("不该发生"),3 轮只是防洪 —— 挡掉新代码上线时的一次性抖动,
# 不是灵敏度调参。稳态静默由"恒 0"保证,两头都焊死。
ROTATION_HOLE_CYCLES = 3
# 回填清扫断供:连续 N 轮"有活干却一个标记都没落成"。
# 依据(实测 backfill.log,A4 上线后 61 轮):有活干的 53 轮里 swept_marked
# 中位 213 / p90 272 / max 281;有活干却为 0 的仅 2 轮,**最长自然连零 2 轮**
# (2026-08-07 早上那次约 15 分钟断网)。取 8 轮(2 小时)≈ 4 倍余量。
# ⚠️ 样本仅约 15 小时,属**保守初值**;streak 已逐轮入日志,攒够一周应回来校准。
BACKFILL_ZERO_CYCLES = 8

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


def _send(msg: str, max_retries: int = 1) -> bool:
    """推一条。发送失败一律吞掉 —— 告警发不出去不该反过来搞垮被监控的东西。

    ⭐默认只试 1 次(2026-08-07 从 2 改为 1):**待发队列已经取代了重试**。
    单次 urlopen 超时 30s,故最坏阻塞 ~30s;试两次就是 ~61s,而回填那条链路
    减去 300s 时间闸后只剩 90s 余量,且这条推送恰恰只在出事时才发 ——
    "告警耗时"与"被告警的故障"是相关的,不能按平时的余量估。
    发不出去不丢:进队列,下一轮(15 分钟后)整批补发,那才是正确的重试尺度。

    ⚠️ 唯一调用方是 `dispatch`,且**不传第二个参数** ——
    既有判据里的假发送是单参数的 `lambda m: ...`,显式传参会把它们全打翻。
    """
    if not TELEGRAM_ENABLED:
        print(f"[Telegram 未启用] {msg}", file=sys.stderr)
        return False
    try:
        return bool(_tg.send_telegram_message(msg, max_retries=max_retries))
    except Exception as e:  # 发送失败不影响采集主流程
        print(f"[Telegram 发送失败] {e}", file=sys.stderr)
        return False


# ---------- 待发队列(2026-08-07)----------
# 由来:08-07 08:00 断网 15 分钟,采集器**正确检测到** firehose 采样 0 笔、
# **正确生成了**告警,然后发不出去 —— 而 `_send` 只往 stderr 打一行就吞掉,
# 于是日志上看起来"今天 0 条告警",与真正的风平浪静一模一样。
# 实测历史累计 25 条告警生成了但从没送到(采集器 23 + 看门狗 2),全仓库无人读这个失败。
#
# ⚠️ **这个机制解决不了的事,必须说清楚**:"告警通道坏了"没法用告警去通知。
# 要真解决得有第二条独立通道(短信/邮件/本地弹窗),不在本次范围。
# 本次做到的是两件较弱但真实的事:①通道恢复时补发,并写明曾中断多久、积压几条
# ⇒ 人**事后**知道自己被蒙了多久;②队列深度进心跳 ⇒ 看门狗读得到 ——
# 这条在"网络没坏、但 Telegram 令牌失效/接口变更/被限流"这类故障下是真管用的。
#
# ⭐关键约束:**每轮最多一次网络调用**。逐条补发 20 条 × 单条最坏 30s = 10 分钟,
# 足以撑爆 900s 硬杀线。故队列**拼成一条**发出去 —— 即队列**取代**了重试,
# 不是叠加在重试之上(所以下面 `_send` 一律 max_retries=1)。
PENDING_MAX = 20
_QUEUE_DIR = se.DATA_ROOT / "state"


def _queue_path(link: str) -> Path:
    """一条链路一个文件。采集器(:00/:15/:30/:45)与看门狗(:00/:30)会**同时**跑,
    共用一个文件会在读-改-写之间丢条目 —— 而丢的正是告警本身。"""
    return _QUEUE_DIR / f"pending_alerts_{link}.json"


def _load_queue(link: str) -> tuple[list[dict], int]:
    v = cycle_state.read_state(_queue_path(link), "pending", None)
    if isinstance(v, dict) and isinstance(v.get("items"), list):
        try:
            return v["items"], int(v.get("dropped") or 0)
        except (TypeError, ValueError):
            pass
    return [], 0            # 损坏/缺失一律降级为空:只影响节奏,不该让采集器崩


def queue_stats(link: str = "cycle") -> dict:
    items, dropped = _load_queue(link)
    oldest = int(time.time() - items[0]["ts"]) if items else 0
    return {"queue_depth": len(items), "dropped": dropped, "oldest_age_s": oldest}


_SEP = "\n\n———\n\n"
# telegram_notifier_v2 的硬上限是 4000 字符,**且它是从尾部截断的**。
# 而队列把最新的排在最后 ⇒ 若交给它截,被截掉的恰好是队列费劲保下来的"最新",
# 与"溢出丢最老、留最新"这条设计意图正相反(2026-08-07 评审实测:
# 真实告警一条约 770 字节,20 条拼起来约 15,000 字节,必被截)。
# 故长度由**我们自己**控制:从最新往回收,收不下的出声报数,不交给下游默默砍。
COMPOSE_MAX_CHARS = 3600      # 给页眉和 Telegram 侧留余量


def _compose(items: list[dict], dropped: int) -> tuple[str, int]:
    """把队列拼成一条,返回 (正文, 因长度没带上的条数)。

    ⚠️ 调用方保证 items 非空。**本函数永远加页眉** ——
    "本轮触发本轮就发"的逐字原样那条路径在 `dispatch` 里直接短路,不进这里。
    理由见 dispatch:只有一条但**被推迟过**的,也必须写明延迟了多久
    (那是最高频的场景,以前它与"从没出过故障"逐字相同,承诺等于没兑现)。
    """
    kept: list[dict] = []
    total = 0
    for it in reversed(items):            # 从最新往回收
        need = len(it["body"]) + len(_SEP)
        if kept and total + need > COMPOSE_MAX_CHARS:
            break
        kept.append(it)
        total += need
    kept.reverse()                        # 展示时仍按时间顺序,读起来才顺
    omitted = len(items) - len(kept)
    mins = (time.time() - items[0]["ts"]) / 60
    head = (f"📮 <b>补发 {len(kept)} 条告警</b>(推送通道中断约 {mins:.0f} 分钟)"
            if not omitted else
            f"📮 <b>补发告警:积压 {len(items)} 条,本条只带得下最新 {len(kept)} 条</b>"
            f"(推送通道中断约 {mins:.0f} 分钟)")
    if dropped:
        head += f"\n⚠️ 另有 {dropped} 条更早的因队列上限({PENDING_MAX})被丢弃"
    if omitted:
        # ⭐为什么不把装不下的留到下轮再发:那样 `delivered=True` 却 `queue_depth>0`,
        # 而看门狗把"队列非空"解读成**通道坏了** ⇒ 会推一条假告警。
        # 宁可诚实地丢并说清楚,也不要造一个会误导排查方向的信号
        # (指错方向的告警比不告警更贵 —— 见 _slow_cycle_hint 上方的说明)。
        head += f"\n⚠️ 更早的 {omitted} 条因单条消息长度上限**未展示,内容已丢弃**"
    return head + "\n\n" + _SEP.join(i["body"] for i in kept), omitted


def dispatch_log_line(r: dict) -> str | None:
    """把 dispatch 的返回值翻成一行人话;无事可说返回 None。

    ⭐三条链路共用一份措辞 —— 回填那条以前连失败分支都没有,
    而"照抄结构而不抽象"已经犯过 3 次,这里不许再抄第四份。
    """
    if r["sent"]:
        if r["backlog_sent"]:
            line = (f"✅ 已推送告警 {r['sent']} 条,其中**补发 {r['backlog_sent']} 条**"
                    f"(推送通道曾中断约 {r['oldest_age_s'] / 60:.0f} 分钟)")
            if r["dropped"]:
                line += f";另有 {r['dropped']} 条因队列上限被丢弃"
            if r["omitted"]:
                line += f";另有 {r['omitted']} 条因长度上限未展示"
            return line
        return "已推送告警"
    if r["queue_depth"]:
        return (f"⚠️ 告警推送失败,{r['queue_depth']} 条待发"
                f"(最早 {r['oldest_age_s'] / 60:.0f} 分钟前),下轮补发")
    return None


def dispatch(body: str | None, link: str = "cycle") -> dict:
    """把本轮告警(可为 None)并入待发队列,并尝试**一次**把整个队列送出去。

    返回 {delivered, queue_depth, dropped, oldest_age_s}。
    `body` 为 None 且队列为空 ⇒ 一次网络调用都不发(防洪的另一头:稳态完全静默)。
    ⭐但 `body` 为 None 而队列**非空**时仍要试 —— 否则一旦风平浪静积压就永远排不出去,
    而"风平浪静"恰恰是故障结束后的常态。
    """
    items, dropped = _load_queue(link)
    backlog = len(items)                  # 本次调用**之前**就积压着的条数
    if body:
        items.append({"ts": time.time(), "body": body})
    quiet = {"delivered": True, "queue_depth": 0, "dropped": 0, "oldest_age_s": 0,
             "sent": 0, "backlog_sent": 0, "omitted": 0}
    if not items:
        return quiet
    if len(items) > PENDING_MAX:
        # 丢最老的:告警是状态描述,新的更能反映现状。但**必须出声** ——
        # 静默丢样本是本项目的真凶,补发时会把丢弃数一并告诉人。
        dropped += len(items) - PENDING_MAX
        backlog = max(0, backlog - (len(items) - PENDING_MAX))
        items = items[-PENDING_MAX:]
    age = int(time.time() - items[0]["ts"])
    # ⭐"本轮触发、本轮就发"这一条走逐字原样:稳态下的告警正文必须与从前一个字不差,
    # 否则会悄悄改掉所有既有告警的样子(而那些告警的内容判据验的正是正文)。
    # 注意条件里有 `not backlog` —— 只有一条但**被推迟过**的不走这里,
    # 它必须带上"延迟了多久",那是最高频的场景。
    if body and not backlog and not dropped:
        msg, omitted = items[0]["body"], 0
    else:
        msg, omitted = _compose(items, dropped)
    if _send(msg):
        try:
            _queue_path(link).unlink(missing_ok=True)   # 送达即清空,稳态不留垃圾文件
        except OSError as e:
            # ⚠️ 删不掉就把内容清空(内容为准,不靠文件在不在)。两条都失败才会重复补发,
            # 而且是**每轮**重复,不是"一次" —— 原注释低估了这个故障的持续性。
            print(f"[待发队列清空失败] {e},改写空队列兜底", file=sys.stderr)
            cycle_state.write_state(_queue_path(link), "pending",
                                    {"items": [], "dropped": 0}, "待发告警队列")
        return {"delivered": True, "queue_depth": 0, "dropped": dropped,
                "oldest_age_s": age, "sent": len(items) - omitted,
                "backlog_sent": min(backlog, len(items) - omitted), "omitted": omitted}
    cycle_state.write_state(_queue_path(link), "pending",
                            {"items": items, "dropped": dropped}, "待发告警队列")
    # ⚠️ 落盘可能失败(write_state 内部吞掉 OSError 只打一行)。照样返回 len(items)
    # 等于**声称已存而实际没存** —— 又一次"记录了没记上"。故回读确认,以磁盘为准。
    real = _load_queue(link)[0]
    if len(real) != len(items):
        print(f"[待发队列落盘失败] 本应存 {len(items)} 条,实存 {len(real)} 条 ——"
              f" 这些告警已永久丢失", file=sys.stderr)
    return {"delivered": False, "queue_depth": len(real), "dropped": dropped,
            "oldest_age_s": age, "sent": 0, "backlog_sent": 0, "omitted": 0}


def maybe_alert(counts: dict) -> bool:
    """按阈值决定是否告警。返回**本轮是否真的推出去一条**。

    注意语义:队列里补发成功不算"本轮推了" —— 本轮没触发就该返回 False,
    这是既有判据(test_alerts_*.py 等约 15 条)验的东西,不许因为加队列而改掉。
    """
    return bool(maybe_alert_with_counts(counts)["sent_fresh"])


def maybe_alert_with_counts(counts: dict) -> dict:
    """同 `maybe_alert`,但把 dispatch 的全部计数带出来给调用方写进心跳。

    ⭐调用方**必须用这个返回值**,不许在 dispatch 之后重读磁盘:
    送达成功时队列文件已被删,重读只会拿到 0 —— 而"成功了"恰恰是
    `dropped`/`backlog_sent` 最不该沉默的时刻(2026-08-07 评审实测抓出)。
    """
    body = build_alert(counts)
    r = dispatch(body, link="cycle")
    # 本轮触发且送出去了 = 老语义的 True。补发成功不算(既有约 15 条判据验的是这个)。
    r["sent_fresh"] = bool(body) and r["delivered"]
    return r


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
    # 游标空洞:稳态恒 0(见 ROTATION_HOLE_CYCLES 上方说明),持续非 0 = 结构变了。
    rhs = counts.get("rotation_hole_streak", 0)
    if rhs > 0 and rhs % ROTATION_HOLE_CYCLES == 0:
        triggers.append(
            f"🔴 轮转游标出现空洞并持续 {rhs} 轮(轮询 {counts.get('poll_rotation_holes', 0)} / "
            f"结算 {counts.get('settlement_rotation_holes', 0)} 个)。"
            f"含义:有市场被跳过而轮转不知道,游标已停在它前面 —— 要么有人加了新的跳过路径,"
            f"要么某个市场每轮都做不成把整条队伍卡住了。须人工看一眼是哪个市场")
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


def maybe_alert_backfill(counts: dict) -> bool:
    """回填清扫链路的告警。返回是否真的推送了。

    ⚠️ 单次最坏阻塞 ~30s(`dispatch` 内一律 max_retries=1):回填的 systemd 硬杀线
    是 420s,时间闸已占 300s,余量只有 90s —— 而这条推送**恰恰只在出事时才发**,
    出的事(网络/接口退化)又正是让 Telegram 也卡住的那类。
    即"告警耗时"与"被告警的故障"相关,不是独立事件,故不能按平时的余量估。
    发不出去也不丢:进本链路自己的待发队列,下一轮(15 分钟后)补发。
    """
    body = build_backfill_alert(counts)
    delivered = dispatch(body, link="backfill")["delivered"]
    return bool(body) and delivered


def build_backfill_alert(counts: dict) -> str | None:
    """回填清扫(独立进程、独立节奏)的告警正文;无触发返回 None。

    ⭐为什么单独一个函数而不是塞进 `build_alert`:回填是**独立链路**,
    CLAUDE.md 要求独立链路分开判定、计数不许相加 —— 主周期健康并不代表回填健康,
    合在一起判会让其中一条的故障被另一条的正常稀释掉。
    复用本模块的 `_send` 与"连续 N 轮整数倍才复述"的防洪写法,不另抄一份发送逻辑。

    由来:2026-08-07 实测发现 `zero_streak` 算了、写了、打印了,却**全仓库无人读** ——
    回填彻底扫不动时不会有任何通知。本函数就是那个缺失的消费者。
    """
    triggers = []
    zs = counts.get("zero_streak", 0)
    if zs > 0 and zs % BACKFILL_ZERO_CYCLES == 0:
        triggers.append(
            f"🔴 回填清扫断供:连续 {zs} 轮(约 {zs * 15 / 60:.1f} 小时)有市场可扫却一个"
            f"完成标记都没落成。实测正常每轮 ~213 个。含义:不变量 A4(市场关闭后必有一次"
            f"完整采集)正在失守,而已关闭的市场**再也不会**出现在活体链路里。须查网络/接口")
    rhs = counts.get("rotation_hole_streak", 0)
    if rhs > 0 and rhs % ROTATION_HOLE_CYCLES == 0:
        triggers.append(
            f"🔴 回填游标出现空洞并持续 {rhs} 轮(本轮 {counts.get('rotation_holes', 0)} 个)。"
            f"含义:有市场被跳过而轮转不知道,游标已停在它前面 —— 若是同一个市场每轮都失败,"
            f"整条队伍会卡死在它那里。须人工看一眼是哪个市场")
    if not triggers:
        return None
    return ("🔴 <b>Polymarket 回填清扫守护告警</b>\n" + "\n".join(triggers)
            + f"\n\n本轮: 目标 {counts.get('targets', 0)} / 尝试 {counts.get('attempted', 0)}"
              f" / 落标记 {counts.get('swept_marked', 0)}"
              f" / 未扫完 {counts.get('sweep_incomplete', 0)}")


if __name__ == "__main__":
    # 自测:稳态截断不推(False),尖峰才推(True)
    print("稳态截断 ov=2:", maybe_alert({"offset_overflow_count": 2, "total_markets_polled": 18}))
    print("截断尖峰 ov=25:", maybe_alert({"offset_overflow_count": 25, "total_markets_polled": 18}))
