#!/usr/bin/env python3
"""collector_watchdog.py — 新采集器看门狗(补 auto_health_check 退役的空缺)。

采集器自带的告警(alerts.py)只在"周期跑起来但内部异常"时喊;它喊不出"timer 整个停了/
根本没在跑"。本看门狗独立于采集器运行(自己的 systemd timer,每 30 分钟),专查外部存活:
  1. collector timer 还 active 吗?
  2. 最近有没有跑过周期?(审计心跳新鲜度 —— 每轮必写,故>25 分钟无心跳=停摆)
  3. firehose 是否持续抽风?(最近心跳 firehose_fail>0)

任一红 → Telegram(复用 alerts.dispatch(走待发队列,发不出去不丢))。带 4h 冷却防刷屏。
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pyarrow.parquet as pq

import alerts
import storage_engine as se

# 采集器每 15 分钟一轮(2026-08-04 从 10 分钟放宽,见 .timer)。
# 阈值必须容得下**一轮被杀**:一轮被 SIGTERM → 下一条心跳要等到 ~30 分钟后。
# 取 40 分钟 = 容 1 轮失手、抓 2 轮连续失手。旧值 25 分钟配 15 分钟间隔会对单轮失手误报。
# ⚠️ 本守护只查"完全停摆";"跑得动但越来越慢"由 alerts 的慢周期守护负责。
#    心跳已于 2026-08-04 移到整轮末尾(原先在结算守望之前 → 结算阶段被杀仍留新鲜心跳,
#    本守护对那一段是瞎的)。现在"心跳新鲜"= 整轮真的跑完了,新鲜度才真有分辨力。
HEARTBEAT_STALE_MIN = 40
COOLDOWN_S = 4 * 3600             # 同一问题 4h 内不重复告警
STATE_FILE = se.DATA_ROOT / ".watchdog_state.json"
TIMER_UNIT = "polymarket-rebirth-collector.timer"
# ⭐时间防火墙侧表(2026-08-26 加)。它每小时把新结算市场的 closedTime 取回来,
#   **当时错过就永久取不回**(市场一关闭就掉出轮询队列)——而所有"预测准不准"的
#   分析都以它为时间防火墙。原先本看门狗只盯 TIMER_UNIT 一条,侧表整个不在视野里。
MARKET_TIMES_TIMER_UNIT = "polymarket-market-times.timer"
# 侧表 OnCalendar=*:50 ⇒ 一轮 60 分钟。150 分钟 = 2.5 轮:容 1 轮失手、抓 2 轮连续失手。
# 与上面 HEARTBEAT_STALE_MIN(40 分钟配 15 分钟周期 = 2.67 轮)是同一把尺子,不另立标准。
MARKET_TIMES_STALE_MIN = 150
MARKET_TIMES_SUMMARY = (Path(__file__).resolve().parent
                        / "data" / "market_times" / "last_run_summary.json")
# 判别「日报功能没装」与「日报模块坏了」靠文件在不在 —— 见 _digest_problems。
DIGEST_MODULE_PATH = Path(__file__).resolve().parent / "daily_digest.py"


def _timer_active(unit: str) -> bool:
    """⛔ 参数**不给默认值**:两条 timer 都要查,给了默认值就会有人忘了传,
    而那种漏检的症状与"健康"一模一样。"""
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", unit],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() == "active"
    except (subprocess.SubprocessError, OSError):
        return False  # 查不了当异常(宁可误报,不放过停摆)


def _latest_heartbeat() -> dict | None:
    files = glob.glob(str(se.AUDIT_DIR / "**" / "*.parquet"), recursive=True)
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    try:
        rows = pq.read_table(newest).to_pylist()
    except OSError:
        return None
    return rows[-1] if rows else None


def _heartbeat_problems(hb: dict) -> list[str]:
    """只看心跳**内容**的问题(不含新鲜度 —— 那个要拿当前时间比,单独在 check 里判)。

    拆成纯函数是为了判据能直接喂一条心跳进来验,不必伪造文件系统和时钟。
    """
    problems = []
    if hb.get("firehose_fail", 0) > 0:
        # ⭐2026-08-20:此处原先只说一句「可能要查接口/IP」的软话 ——
        #   **一句永远不会自己变严重的话**(原文逐字保留在
        #   10-tests/unit/test_collector_watchdog.py 的 docstring 与判据断言里)。
        # 08-19 那次连续 14 小时(110 轮里 63 轮空转)它推了 118 条一模一样的 🟡,
        # 而「持续」从来没有被数出来过。可笑的是计数一直就在手上这条心跳里:
        # `trade_flow_outage_streak` 由 run_cycle 每轮持久化、在 AUDIT_FIELDS 白名单内、
        # alerts.py 也早就在用它分档 —— 只有本函数没读。
        # 形状:「记录事实 vs 使用事实,只接一头」。
        # ⛔ 红线一律 import alerts 的那一份,不在这里复制(照抄结构而不抽象,已犯 3 次)。
        #
        # 老心跳(本次改动之前写的)没有该字段 ⇒ 取 0;而 firehose_fail>0 至少意味着
        # 本轮失手,故下限取 1,不说「已连续 0 轮」这种自相矛盾的话。
        try:
            tfs = max(1, int(hb.get("trade_flow_outage_streak", 0) or 0))
        except (ValueError, TypeError) as e:
            # 异常家族按【造真坏输入实测】决定,不凭想当然(2026-08-20):
            #   'corrupt' -> ValueError | [1] / {} -> TypeError | None / 3.7 -> 不抛。
            # ⛔ 绝不许让它冒出去:本函数是 check() 的【最后一步】,异常会把前面已经
            #    攒好的 timer/心跳新鲜度等问题**整体丢掉** ⇒ 看门狗整轮零告警。
            #    CLAUDE.md 铁律「一个附加功能坏掉不许打断调用方其余职责」;
            #    2026-08-17 daily_digest 已栽过同一形状。
            problems.append(
                f"🔴 看门狗读不了心跳的 trade_flow_outage_streak"
                f"({type(e).__name__}: {e})—— 本轮 firehose 分档已跳过,"
                f"须查心跳写入端;其余检查不受影响")
            return problems + _queue_problems(hb)
        hours = tfs * alerts.cycle_minutes() / 60
        if tfs >= alerts.TRADE_FLOW_OUTAGE_CYCLES:
            problems.append(
                f"🔴 成交流持续断供:已连续 {tfs} 轮(约 {hours:.1f} 小时)"
                f"firehose 一笔都没抓到。⚠️ 这已不是会自愈的抖动 —— 轮转一圈超过约 "
                f"{alerts.LAP_RED_LINE_HOURS} 小时后,最活跃的市场会出**永久**空洞"
                f"(翻页够不回去)。须立刻查网络/代理/IP,不要等它自己好")
        else:
            problems.append(
                f"🟡 firehose 抽风:本轮采样 0 笔,已连续 {tfs} 轮(约 {hours:.1f} 小时)。"
                f"多数是单轮抖动、下轮自愈;连续 {alerts.TRADE_FLOW_OUTAGE_CYCLES} 轮起升级为 🔴")
    # ⭐告警送达盲区(2026-08-07 立):采集器自己发不出去的时候是喊不出来的,
    # 只能由**别人**替它喊。本条在"网络没坏、但 Telegram 令牌失效/接口变更/被限流"
    # 这类故障下真管用;若是整机断网,看门狗自己也喊不出去 —— 那一层解决不了,
    # 需要第二条独立通道,已在 alerts.py 里写明不在范围内。
    return problems + _queue_problems(hb)


def _queue_problems(hb: dict) -> list[str]:
    """告警送达盲区。单独拆出来,是为了 firehose 分档降级时也带得上它 ——
    否则「一个附加功能坏掉」又会顺手把这条职责一起吞掉。"""
    qd = hb.get("alert_queue_depth", 0)
    if qd > 0:
        return [f"🟡 采集器有 {qd} 条告警**发不出去**(积压待发)。"
                f"含义:它可能正在出事而喊不出来 —— 须查 Telegram 令牌/网络"]
    return []


def _digest_problems(now: float | None = None) -> list[str]:
    """⭐日报的"死人开关":日报自己不会喊"我死了",只能由别人替它喊。

    为什么挂在看门狗身上而不是新起一个盯梢的:再建一个就要问"那谁盯它",无限套娃。
    看门狗已经每 30 分钟跑一次、有 systemd timer 兜底、今天验证过健康 ——
    加一个字段一个判断,不加新组件。

    ⚠️ 从没发过(刚上线)不报 —— 否则上线当天就是一条误报。
    """
    # ⭐「装没装」与「坏没坏」是两件**相反**的事,不许用同一个返回值表达。
    #
    # alerts.py 对 telegram 的可选导入用 `except Exception` 是对的 —— 那是**真正可选**
    # 的第三方集成,没装就关掉功能。但 daily_digest 是看门狗**要检查的对象本身**,
    # 导入失败恰恰是它能遇到的**最坏**那种健康状况。
    #
    # 🔴 初版(2026-08-17 上午)把两者混为一谈,后果致命:service 的两条 ExecStart
    # 都带 `-` 前缀 ⇒ daily_digest 崩溃时 systemd 照样报 success;而这里遇到同一个崩溃
    # 也返回 [] ⇒ **产出端和检测端同时失明**,26 小时超时告警永远发不出来。
    # 更糟的是当时那条判据**断言的就是这个错行为**。(第二轮 code review 抓出。)
    #
    # 判别靠**文件在不在**,不靠异常类型:缺的可能是它的某个依赖(同样抛
    # ModuleNotFoundError),那属于"坏了"而不是"没装"。
    try:
        import daily_digest as dd
    except Exception as e:
        if not DIGEST_MODULE_PATH.exists():
            return []        # 文件真的不在 = 功能没装,静默(否则装之前天天误报)
        return [f"🔴 日报模块存在却**导入失败**({type(e).__name__}: {e})。"
                f"含义:看门狗对日报的健康检查已经瞎了,而 service 因为 `-` 前缀"
                f"仍会报 success —— 产出端和检测端同时失明。须查 daily_digest.py"]
    # ⚠️ 这一句也必须在保护里:状态文件被写坏(非数字 / 非 UTF-8)时抛的
    # ValueError / UnicodeDecodeError 不在 cycle_state.read_state 的捕获范围内,
    # 会一路冒出去把 check() 剩下三项打断 —— 与上面那条是同一个后果、不同触发点。
    try:
        age = dd.last_sent_age_s(now=now)
    except Exception as e:
        return [f"🔴 读不了日报的发送时间戳({type(e).__name__}: {e})—— "
                f"「日报多久没发」这个问题现在没人答得了。须查 {dd.STATE_FILE}"]
    if age is None or age <= dd.STALE_AFTER_S:
        return []
    return [f"🔴 每日日报已 {age / 3600:.1f} 小时没发出来(阈值 "
            f"{dd.STALE_AFTER_S / 3600:.0f} 小时)。含义:**看板本身死了** —— "
            f"心跳里那些没人读的字段又回到没人读的状态。须查 "
            f"polymarket-daily-digest.timer 与 Telegram 通道"]


def _market_times_problems(now: float | None = None) -> list[str]:
    """⭐时间防火墙侧表的"死人开关"。侧表自己不会喊"我死了",只能由别人替它喊。

    ## 为什么不能只查 timer 是不是 active

    对着「如果它现在就是坏的,我看到的会有什么不同?」这一问,有两种**实际存在**的
    故障下答案是"没有不同":
      1. `.backfill.lock` 残留 —— 进程被 SIGKILL(超时被 systemd 砍)不走 `finally`,
         锁留下 ⇒ 此后每一轮都立刻 return 2、什么都不做,而 timer 仍是 active。
      2. 网络坏掉(2026-08-04 / 08-22 真发生过隧道被拖垮)⇒ 每轮照跑,零产出。
    ⇒ 三条一起判:timer 存活 / 真的跑成过 / 有活真干成了。

    ## ⚠️「没活可干」不是异常

    侧表是**回填**任务,追平之后大多数轮次本来就没活干 ⇒ 产出为 0 在这里是**健康**。
    拿"产出为 0"当判据的那一秒起,它就变成天天误报的噪音源(静默失败清单第 6 条)。
    有分辨力的量是 `pending > 0 而 written == 0`:有活,一个都没干成。

    ## 异常家族按【造真坏输入实测】决定(2026-08-26 实跑,判据里逐条焊死)

    缺文件→FileNotFoundError / 是目录→IsADirectoryError,同属 OSError;
    非 UTF-8→UnicodeDecodeError、JSON 垃圾→JSONDecodeError,同属 ValueError;
    fromisoformat("nope")→ValueError 而 fromisoformat(None)→**TypeError**。
    ⛔ 全程不许把异常放出去:本函数是附加职责,抛出去会把 check() 里
       采集器那几项核心检查**整体丢掉**(2026-08-17 daily_digest 栽过同一形状)。
    """
    problems: list[str] = []
    if not _timer_active(MARKET_TIMES_TIMER_UNIT):
        problems.append(
            f"🔴 时间防火墙侧表 timer 不在 active（{MARKET_TIMES_TIMER_UNIT} 已停摆）。"
            f"含义:新结算市场的 closedTime **当时错过就永久取不回** —— "
            f"所有按时间切窗的分析都会从今天起出现无法回填的缺口")
    try:
        summary = json.loads(MARKET_TIMES_SUMMARY.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ValueError(f"顶层不是对象而是 {type(summary).__name__}")
    except FileNotFoundError:
        return problems + [
            f"🔴 侧表从未成功跑完一轮(没有 {MARKET_TIMES_SUMMARY.name})。"
            f"须查 {MARKET_TIMES_TIMER_UNIT} 与 market_times.log"]
    except (OSError, ValueError) as e:
        return problems + [
            f"🔴 侧表的运行记录读不了({type(e).__name__}: {e})—— "
            f"「侧表还活着吗」这个问题现在没人答得了。须查 {MARKET_TIMES_SUMMARY}"]

    try:
        ran_at = dt.datetime.fromisoformat(summary.get("run_at_utc"))
    except (TypeError, ValueError) as e:
        return problems + [
            f"🔴 侧表运行记录里的时间戳读不了({type(e).__name__}: {e})—— "
            f"新鲜度判不了 = 本守护对停摆已经瞎了。须查 {MARKET_TIMES_SUMMARY}"]
    # ⚠️ 无时区的时间戳按 UTC 解释,不许落回本机时区:本机是 JST,
    #    误差 9 小时足以把"已经停了 8 小时"读成"刚跑过"(项目已因 JST/UTC 错位
    #    虚构过一个不存在的异常)。
    if ran_at.tzinfo is None:
        ran_at = ran_at.replace(tzinfo=dt.timezone.utc)
    age_min = ((time.time() if now is None else now) - ran_at.timestamp()) / 60
    if age_min > MARKET_TIMES_STALE_MIN:
        problems.append(
            f"🔴 侧表已 {age_min:.0f} 分钟没跑成一轮(应每 60 分钟一轮,"
            f"阈值 {MARKET_TIMES_STALE_MIN} 分钟 = 容 1 轮失手)。"
            f"常见原因:`.backfill.lock` 残留(被硬杀后没清)或上游持续失败")

    if "pending" not in summary:
        problems.append(
            "🟡 侧表运行记录里没有 pending 字段 —— 写记录的那一版还没升级,"
            "「有活没干成」这一条现在判不了(不是健康,是**没在判**)")
        return problems
    try:
        pending, written = int(summary["pending"]), int(summary.get("written", 0))
    except (TypeError, ValueError) as e:
        problems.append(
            f"🟡 侧表运行记录里的 pending/written 不是数字({type(e).__name__}: {e})"
            f"——「有活没干成」这一条判不了")
        return problems
    # ⭐`invalid_cids`(格式不合法的 condition_id)是 review 抓出来的:它每轮都被写进
    #   记录,却**没有任何人读** —— 又一次「记录事实 vs 使用事实,只接一头」。
    #   要命之处:这些 cid 被**永久**排除在 todo 之外 ⇒ pending 不会因它们变大
    #   ⇒ 上面两条都判不到 ⇒ 一批市场从此拿不到 closed_time,而看板全绿。
    #   门槛取 >0 不是拍的:现网实测长期恒为 0(last_run_summary / market_times.log),
    #   任何非零都是偏离稳态。
    try:
        invalid = int(summary.get("invalid_cids", 0) or 0)
    except (TypeError, ValueError):
        invalid = 0          # 读不出来不另判:上面已有专门的坏记录分支在管
    if invalid > 0:
        problems.append(
            f"🔴 侧表有 {invalid:,} 个 condition_id 格式不合法,已被**永久**排除 —— "
            f"这些市场再也拿不到 closed_time,而 pending 不会反映它们。须查注册表写入端")
    if pending > 0 and written == 0:
        problems.append(
            f"🔴 侧表上一轮有 {pending:,} 个市场待取,却**一个都没落盘**。"
            f"这是「有活没干成」,不是「没活可干」—— 须查网络/代理/Gamma 接口")
    return problems


def check() -> list[str]:
    problems = []
    problems += _digest_problems()
    if not _timer_active(TIMER_UNIT):
        problems.append(f"🔴 采集器 timer 不在 active（{TIMER_UNIT} 已停摆）")
    hb = _latest_heartbeat()
    if hb is None:
        problems.append("🔴 无任何审计心跳（采集器从未成功跑完一轮）")
    else:
        age_min = (time.time() - hb["ts"]) / 60
        if age_min > HEARTBEAT_STALE_MIN:
            problems.append(f"🔴 采集器 {age_min:.0f} 分钟无心跳"
                            f"（应每 {alerts.cycle_minutes()} 分钟一轮=停摆）")
        problems += _heartbeat_problems(hb)
    problems += _market_times_problems()
    return problems


def _cooldown_signature(problems: list[str]) -> str:
    """冷却签名:把正文里【所有会变的数字】抹掉,只留问题种类与档位(🟡/🔴)。

    🔴 由来(2026-08-19 实测):签名原先是问题正文原样拼接,而其中一行是
    「采集器有 **N** 条告警发不出去」—— N 实测取过 1/2/3/7/8/9/20。
    数字一变签名就变 ⇒ 4h 冷却**完全失效** ⇒ 几乎每轮都推。
    实测那次:看门狗触发 136 次、推出去 63 次(冷却有效的话应是每 4h 一条)。

    ⚠️ 与上面的连续计数**必须成对落地**:只把轮数写进消息而不修签名,
    轮数每轮都在变 ⇒ 洪水只会更大。
    档位靠 🟡/🔴 区分,不靠数字,所以抹数字不会把真升级一起压住。
    """
    # ⚠️ 2026-08-26 复发一次:原式 `\d+` 抹不掉**千分位逗号与小数点** ——
    #    「12,246 个」→「#,#」而「907 个」→「#」,签名照样每轮不同 ⇒ 洪水照旧。
    #    是新加的侧表守护的防洪判据当场抓到的。故整数、带千分位、小数一律并成一个 #。
    #    (必须以数字开头,免得把正文里孤立的逗号也吃掉。)
    return "|".join(sorted(re.sub(r"\d[\d,]*(?:\.\d+)?", "#", p) for p in problems))


def _cooldown_ok(signature: str) -> bool:
    """同一问题集 4h 内只告警一次;问题变化则立即告警。"""
    try:
        st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (json.JSONDecodeError, OSError):
        st = {}
    if st.get("sig") == signature and (time.time() - st.get("ts", 0)) < COOLDOWN_S:
        return False
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"sig": signature, "ts": time.time()}))
    return True


def main() -> int:
    problems = check()
    if not problems:
        print(f"[{dt.datetime.now():%H:%M}] ✓ 采集器健康")
        return 0
    sig = _cooldown_signature(problems)
    line = "🐕 <b>采集器看门狗告警</b>\n" + "\n".join(problems)
    print(line)
    if _cooldown_ok(sig):
        # 走待发队列:看门狗自己也栽过 —— 2026-08-07 早上它抓到了 firehose 抽风、
        # 想推却因同一场断网推不出去,只在日志里留了两行没人读的 Error。
        alerts.dispatch(line, link="watchdog")
    else:
        print("（冷却期内,未重复推送）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
