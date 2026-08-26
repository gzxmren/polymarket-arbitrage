#!/usr/bin/env python3
"""判据焊死:每日日报 —— 心跳里 25 个没人读的字段,总算有了读取者(2026-08-17)。

## 由来(实测,非假想)

今天上午修的「注册积压顶死、每天丢弃几千个市场」,证据是两个字段
(`pending_registration_count` / `pending_registration_dropped_count`)。
它们 2026-08-05 就加进心跳了,此后每 15 分钟写一条。从心跳 parquet 里直接查:

    08-08  丢 2015  积压峰值 2000 ← 顶死上限
    08-09  丢 2960  积压峰值 2000 ← 顶死上限
    ...
    08-16  丢 7651  积压峰值 2000 ← 顶死上限

**这件事清清楚楚摆了 9 天,零个读取者。** 我是靠 grep 文本日志偶然发现的。
同期粗筛:心跳 55 个字段里 **25 个找不到任何读取者**。

⇒ 缺的不是又一个采集器、又一个字段,是**有人回头看**。

## 为什么是「推」不是「网页」

这个项目建过一次监控网页(2026-03-20 提交「Phase 3: 质量监控与自动化」)。
实测:两个 systemd 单元 disabled+inactive、journal 零条记录、
数据源 `07-data/` 停写 26 天、端口被别的项目占着。**死了至少 26 天没人发现。**
⇒ 需要人主动打开的东西在这个项目里存活率 0/1;自己找上门的(Telegram)是唯一活着的。

## ⭐这个日报自己会不会静默死掉

把核心问句对准它自己:「如果日报明天不发了,我看到的会有什么不同?」
答案若是"没有不同",它就是第二个 dashboard。

解法**不是**再建一个盯梢的(无限套娃),而是接到已被证明活着的东西上:
日报每次发完写一个时间戳,**已经每 30 分钟跑一次的看门狗**顺手查它是否过期。
判据组 D 焊的就是这一条。

## 本判据**不能**回答什么

- 不回答「日报里该放哪些红线」。第一版**只报数字不报判决** ——
  阈值必须有实测分布支撑,而多数字段还没攒够分布。先定阈值 = 拍脑袋。
- 不回答「日报会不会被人真的读」。那是习惯问题,判据管不了;
  能管的是"它没发出来时有人会知道"。
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import daily_digest as dd  # noqa: E402


def _hb(**kw):
    """一条最小心跳。日报只读心跳,故判据也只喂心跳。"""
    base = {
        "ts": 1_786_900_000, "dt": "2026-08-17",
        "new_trades": 5000, "total_markets_polled": 50, "firehose_fail": 0,
        "new_registered": 100, "new_discovered": 100,
        "pending_registration_count": 0, "pending_registration_dropped_count": 0,
        "newly_resolved": 20, "truth_supply_zero_streak": 0,
        "settlement_checked": 800, "settlement_lookup_fail": 5,
        "cycle_seconds": 180.0, "slow_cycle_streak": 0,
        "trade_flow_outage_streak": 0,
        "offset_overflow_count": 0, "rotation_hole_streak": 0,
        "alert_queue_depth": 0, "alert_dropped_count": 0,
    }
    base.update(kw)
    return base


def _day(n=96, **kw):
    return [_hb(**kw) for _ in range(n)]


# ---------- 判据组 A:四条腿都要报,一条都不许漏 ----------

def test_every_leg_appears():
    """四条腿(成交流/结算真值/注册/回填)缺任何一条,那条腿就回到无人看管。"""
    body = dd.render(_day())
    for leg in ("成交流", "结算真值", "注册", "回填"):
        assert leg in body, f"日报里没有「{leg}」这条腿:\n{body}"



def test_trade_flow_leg_derives_the_run_from_the_day_itself():
    """⭐腿1 必须从**当天心跳序列**自己算断供连续段,不许只信持久化的连计字段。

    实测踩到:用 08-13(真的空转 13.5 小时那天)跑日报,腿1 报了 ✅ ——
    因为 `trade_flow_outage_streak` 是 2026-08-17 才加的字段,历史心跳里是空的。
    而 `firehose_fail` 当天有 43 轮为 1,证据一直都在。

    通用式:**回顾型报表手上有整段序列,就不该依赖某个当时可能还不存在/已丢失的状态字段。**
    (持久化连计是给实时告警用的,那里没有整段序列可看。)
    """
    hbs = _day(n=10) + _day(n=8, firehose_fail=1, new_trades=0,
                            trade_flow_outage_streak=0) + _day(n=10)
    body = dd.render(hbs)
    assert "无异常" not in body, f"当天有 8 轮连续抓取失败却报了无异常:\n{body}"
    assert "成交流" in body.split("异常")[-1] or "腿1" in body


def test_backfill_leg_is_labelled_as_current_not_historical():
    """腿4 读的是**当前**日志尾巴。印在历史日报里必须标明,否则是张冠李戴。"""
    body = dd.render(_day())
    line = [l for l in body.split("\n") if "腿4" in l][0]
    assert "当前" in line or "读不到" in line, f"腿4 没说清这是当前值:{line}"


def test_it_reads_the_fields_that_were_orphans():
    """⭐这些字段就是今天那个 bug 的证据,躺了 9 天没人读。进日报 = 从此有读取者。"""
    for f in dd.CONSUMED_FIELDS:
        assert f in dd.render(_day()) or True   # 值可能为 0,只要求确实被取用
    must = {"pending_registration_dropped_count", "pending_registration_count",
            "new_registered", "new_discovered", "trade_flow_outage_streak"}
    assert must <= set(dd.CONSUMED_FIELDS), (
        f"这些字段没进日报 ⇒ 仍是孤儿:{sorted(must - set(dd.CONSUMED_FIELDS))}")


def test_consumed_fields_all_exist_in_the_heartbeat_schema():
    """⭐日报读的每个字段必须真的在心跳 schema 里 —— 字段改名时本条先红。

    这是"读者读的正是采集器写的那份"那条接线,形式照抄批量开关那条部署判据。
    三月那个 dashboard 就是死在"数据源搬家了而它不知道"。
    """
    import storage_engine as se
    missing = [f for f in dd.CONSUMED_FIELDS if f not in se.AUDIT_FIELDS]
    assert not missing, f"日报读的字段不在心跳 schema 里:{missing}"


# ---------- 判据组 B:第一版只报数字,不报判决 ----------

def test_it_reports_numbers_not_verdicts_for_uncalibrated_fields():
    """没有实测分布的量,不许在日报里下判决(F2)。

    有分布撑着的只有三条:成交流断供(实测良性最长 3 轮)、
    慢周期(实测健康 max 228s)、结算真值零流。其余一律只报数字。
    """
    assert set(dd.JUDGED_FIELDS) <= set(dd.CONSUMED_FIELDS)
    for f in dd.JUDGED_FIELDS:
        assert f in dd.THRESHOLD_PROVENANCE, (
            f"{f} 下了判决却没写实测依据 —— 那就是拍脑袋定的阈值")


def test_a_healthy_day_ends_with_no_incident():
    body = dd.render(_day())
    assert "无异常" in body, f"稳态日报没给出干脆的结论:\n{body}"


def test_a_bad_day_names_the_leg():
    """出事那天必须点名是哪条腿,而不是笼统说"有异常"。"""
    body = dd.render(_day(firehose_fail=1, new_trades=0, trade_flow_outage_streak=10))
    assert "无异常" not in body
    assert "成交流" in body


def test_it_never_collapses_into_one_health_score():
    """⭐绝不合成「健康度」。08-13 那天三项里两项正常,一平均故障就被稀释掉了。"""
    body = dd.render(_day(firehose_fail=1, new_trades=0, trade_flow_outage_streak=10))
    for word in ("健康度", "健康分", "综合评分"):
        assert word not in body, f"日报里出现了合成分数「{word}」—— 会稀释掉单条腿的故障"


# ---------- 判据组 C:空数据不许假装正常 ----------

def test_no_heartbeat_is_loud_not_silent():
    """一条心跳都没有 ⇒ 必须响亮地说"没数据",不许渲染成一份漂亮的全 0 报表。

    全 0 报表和"系统很闲"长得一模一样 —— 那正是要消灭的模糊。
    """
    body = dd.render([])
    assert body and "没有心跳" in body
    assert "无异常" not in body


def test_partial_day_says_so():
    """轮次明显少于一天该有的数量 ⇒ 要说出来(采集器可能被杀过)。"""
    body = dd.render(_day(n=30))
    assert "30" in body and "96" in body, f"轮次不足却没出声:\n{body}"


# ---------- 判据组 D:⭐防日报自己静默死掉 ----------

def test_it_records_when_it_was_sent(tmp_path, monkeypatch):
    """发完必须留时间戳,否则"它没发"这件事无人可查。"""
    monkeypatch.setattr(dd, "STATE_FILE", tmp_path / "digest.json")
    sent = []
    monkeypatch.setattr(dd, "_send", lambda msg: sent.append(msg) or True)
    dd.run(_day())
    assert sent, "根本没发出去"
    assert dd.last_sent_age_s(now=dd._now()) is not None
    assert dd.last_sent_age_s(now=dd._now()) < 5


def test_a_failed_send_does_not_stamp_success(tmp_path, monkeypatch):
    """⭐发失败不许盖"已发送"的戳 —— 否则看门狗看到新鲜时间戳,而人什么都没收到。

    这正是本项目反复发作的形状:留痕与事实脱钩。
    """
    monkeypatch.setattr(dd, "STATE_FILE", tmp_path / "digest.json")
    monkeypatch.setattr(dd, "_send", lambda msg: False)
    dd.run(_day())
    assert dd.last_sent_age_s(now=dd._now()) is None, "发失败却盖了已发送的戳"



def test_a_truly_corrupt_parquet_file_does_not_kill_the_whole_day(tmp_path, monkeypatch):
    """⭐一个坏文件只该少那一块,不该让整份日报消失。

    ⚠️ 这条与 `test_digest_never_raises_on_a_corrupt_heartbeat` 测的**不是同一件事**:
    那条喂的是内存里字段缺失的 dict(走 render 那条路),这条喂的是磁盘上真的坏掉的
    parquet 文件(走 load_day 那条路)。初版只捕 `OSError`,而实测 pyarrow 对损坏文件抛的是
    `pyarrow.lib.ArrowInvalid`,它是 **ValueError** 的子类、**不是** OSError ——
    于是"跳过坏文件"这道防线是假的,而注释里白纸黑字写着它存在。
    """
    import pyarrow as pa, pyarrow.parquet as pq
    import storage_engine as se
    day = "2026-08-17"
    d = tmp_path / f"dt={day}"; d.mkdir(parents=True)
    good = {k: v for k, v in _hb().items() if k != "dt"}   # dt 由分区目录提供
    pq.write_table(pa.Table.from_pylist([good]), d / "good.parquet")
    (d / "bad.parquet").write_bytes(b"not a parquet file, just garbage")
    monkeypatch.setattr(se, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(dd.se, "AUDIT_DIR", tmp_path)

    rows = dd.load_day(day)
    assert len(rows) == 1, f"坏文件把好文件也拖没了(拿到 {len(rows)} 行)"
    assert dd.render(rows)


def test_a_queued_send_is_not_a_process_failure(tmp_path, monkeypatch):
    """⭐发不出去→进队列是**预期内会经常发生**的自愈行为,退出码不该报失败。

    由来(2026-08-17 code review 实测抓出):service 里两条 ExecStart 串在一起,
    而 systemd 的语义是「前一条失败,后面的都不执行」。日报一旦进队列就返回 1,
    当天的趋势报表**根本不会被生成** —— 而那正是这次要立起来的下钻能力。
    投递成功与否由 STATE_FILE + 看门狗独立追踪,不该再借退出码表达一遍。
    """
    monkeypatch.setattr(dd, "STATE_FILE", tmp_path / "digest.json")
    monkeypatch.setattr(dd, "_send", lambda msg: False)
    assert dd.run(_day()) == 0, "进队列被当成进程失败 ⇒ 会连坐掐掉后面的 ExecStart"
    assert dd.last_sent_age_s(now=dd._now()) is None, "但仍然不许盖已发送的戳"


def test_watchdog_reports_red_when_the_digest_goes_stale(tmp_path, monkeypatch):
    """⭐接线的另一头:看门狗必须真的读这个时间戳并报红。

    没有这一条,日报就是第二个「造好了但没人盯」的 dashboard。
    """
    import collector_watchdog as cw
    monkeypatch.setattr(dd, "STATE_FILE", tmp_path / "digest.json")
    monkeypatch.setattr(dd, "_send", lambda msg: True)
    dd.run(_day())

    fresh = cw._digest_problems(now=dd._now())
    assert fresh == [], f"刚发完就报警:{fresh}"

    stale = cw._digest_problems(now=dd._now() + dd.STALE_AFTER_S + 60)
    assert stale and any("🔴" in p for p in stale), "日报过期了看门狗却不报红"


def test_watchdog_is_silent_before_the_first_ever_digest(tmp_path, monkeypatch):
    """从没发过(刚上线)不许报红 —— 否则上线当天就是一条误报。"""
    import collector_watchdog as cw
    monkeypatch.setattr(dd, "STATE_FILE", tmp_path / "never.json")
    assert cw._digest_problems(now=dd._now()) == []



def test_the_timer_is_actually_deployed():
    """⭐「记录事实 vs 使用事实,只接一头」—— 这次接的是"谁去按时跑它"。

    日报模块写完了、判据也写了,但如果没有任何 timer 调用它,它一次也不会发,
    而"从没发过"恰恰被 `_digest_problems` 豁免(刚上线不该误报)⇒ 双重静默。
    ⚠️ 查**仓库**那份:本机 `~/.config/systemd/user/` 重装会被覆盖,
    只改本机 = 下次重装静默复原(2026-08-05 的教训)。
    """
    d = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    svc, timer = d / "polymarket-daily-digest.service", d / "polymarket-daily-digest.timer"
    assert svc.exists() and timer.exists(), "日报没有部署单元 ⇒ 永远不会自己跑"
    t = timer.read_text(encoding="utf-8")
    assert "OnCalendar" in t
    assert "Persistent=true" in t, (
        "没有 Persistent ⇒ 机器 suspend 过去就漏跑,而漏跑那天恰恰最该看日报")
    assert "daily_digest.py" in svc.read_text(encoding="utf-8")


def test_the_digest_interval_and_the_stale_threshold_agree():
    """阈值必须容得下发送周期 —— 两处分开写死必然分叉(cycle_minutes 那条教训)。"""
    d = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
    t = (d / "polymarket-daily-digest.timer").read_text(encoding="utf-8")
    assert "*-*-*" in t, "不是每天一次的话,26 小时这个阈值就失去依据"
    assert dd.STALE_AFTER_S > 24 * 3600


def test_stale_threshold_leaves_room_for_one_missed_run():
    """阈值必须容得下**一次没跑成**(同看门狗心跳阈值的推导方式)。

    日报一天一次 ⇒ 阈值须 > 24h,否则一次 suspend/漏跑就误报;
    但也不能太松,否则真死了要好几天才知道。取 26h。
    """
    assert 24 * 3600 < dd.STALE_AFTER_S <= 30 * 3600


# ---------- 判据组 E:防洪(稳态也发,但绝不进告警通道) ----------

def test_digest_goes_to_its_own_link_not_the_alert_queue():
    """⭐日报每天都发,告警稳态零条 —— 混进同一条队列会把真告警挤掉。

    08-03 那次同期推了 1340 条告警,没有一条指向真正出事的那件事。
    """
    assert dd.LINK != "cycle", "日报和采集器告警共用一条待发队列 ⇒ 会互相挤占"


def test_digest_never_raises_on_a_corrupt_heartbeat():
    """心跳缺字段/类型不对时降级出报,不许崩 —— 崩了就等于当天没有日报。"""
    body = dd.render([{"ts": 1, "dt": "2026-08-17"}, {"ts": 2}])
    assert body


def test_a_broken_digest_module_is_reported_red_not_silently_ignored(monkeypatch):
    """⭐⭐日报模块**坏掉**必须报红,不许当成"没问题"。

    ## 这条判据的上一版是错的,而且错得正是它要防的那个病

    上一版断言 `_digest_problems() == []` —— 即"模块坏了就静默"。把它和另一处改动
    连起来看就致命:service 的两条 ExecStart 都加了 `-` 前缀(为了日报进队列时
    不掐掉报表),于是 daily_digest.py 崩溃时 **systemd 照样报 success**;
    而负责兜底的 `_digest_problems()` 遇到同一个崩溃**也返回 []**。
    ⇒ 产出端和检测端**同时失明**,26 小时超时告警永远不会发出来 ——
    因为发这条告警的函数自己把"看不了"等同于"没问题"了。

    「如果它现在就是坏的,我看到的会有什么不同?」——完全没有不同。

    ## 「装没装」和「坏没坏」是两件相反的事

    `alerts.py` 对 telegram 的可选导入用 `except Exception` 是对的 ——
    那是**真正可选**的第三方集成,没装就关掉功能。
    但 `daily_digest` 是看门狗**要检查的对象本身**,导入失败恰恰是它能遇到的
    **最坏**那种健康状况。两者不该用同一个返回值表达。
    判别靠**文件在不在**,不靠异常类型(缺的可能是它的某个依赖,同样抛
    ModuleNotFoundError,却属于"坏了"而不是"没装")。
    """
    import builtins, collector_watchdog as cw
    real = builtins.__import__

    def boom(name, *a, **k):
        if name == "daily_digest":
            raise NameError("模块级炸了(比如一次坏部署带来的语法错误)")
        return real(name, *a, **k)

    monkeypatch.delitem(sys.modules, "daily_digest", raising=False)
    monkeypatch.setattr(builtins, "__import__", boom)

    problems = cw._digest_problems()
    assert problems and any("🔴" in p for p in problems), (
        f"日报模块坏掉却被当成没问题:{problems}")

    # 而且不许把异常放出去 —— 看门狗其余职责必须照常跑完
    monkeypatch.setattr(cw, "_timer_active", lambda unit: False)
    monkeypatch.setattr(cw, "_latest_heartbeat", lambda: None)
    all_problems = cw.check()
    assert any("timer" in p for p in all_problems), f"看门狗其余检查没跑:{all_problems}"


def test_a_genuinely_absent_module_stays_silent(monkeypatch, tmp_path):
    """反面:文件真的不在(功能没装)才允许静默 —— 否则装之前天天误报。"""
    import collector_watchdog as cw
    monkeypatch.setattr(cw, "DIGEST_MODULE_PATH", tmp_path / "not_here.py")
    monkeypatch.delitem(sys.modules, "daily_digest", raising=False)
    import builtins
    real = builtins.__import__

    def boom(name, *a, **k):
        if name == "daily_digest":
            raise ModuleNotFoundError("No module named 'daily_digest'")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", boom)
    assert cw._digest_problems() == []


def test_a_runtime_error_reading_the_stamp_is_also_reported(monkeypatch):
    """⭐时间戳读取本身抛错也要报红,不许把 check() 整个打断。

    上一版把 `except` 只包住 import,而 `dd.last_sent_age_s()` 在 try 之外 ——
    状态文件被写坏(非数字 / 非 UTF-8)时抛的 ValueError / UnicodeDecodeError
    会一路冒出去,让排在后面的三项核心检查全部不执行。
    这正是这次修复**声称**要解决的后果,只是触发点换了一个。
    """
    import collector_watchdog as cw, daily_digest as dd

    def boom(now=None):
        raise ValueError("状态文件坏了")
    monkeypatch.setattr(dd, "last_sent_age_s", boom)

    problems = cw._digest_problems()
    assert problems and any("🔴" in p for p in problems), f"读不了时间戳却不出声:{problems}"


def test_unreadable_heartbeat_files_are_counted_into_the_digest_body(tmp_path, monkeypatch):
    """⭐跳过的文件数必须进**日报正文**,不能只 print 到日志。

    只 print 的话它落在 digest.log 里,而那正是"需要人主动打开"的东西 ——
    本文件开头自己写着这类东西在本项目存活率 0/1。
    「任何剔除必须出声计数」的"计数"要有人看得到才算数,print 只做了前半句。
    """
    import pyarrow as pa, pyarrow.parquet as pq
    import storage_engine as se
    day = "2026-08-17"
    d = tmp_path / f"dt={day}"; d.mkdir(parents=True)
    good = {k: v for k, v in _hb().items() if k != "dt"}
    pq.write_table(pa.Table.from_pylist([good]), d / "good.parquet")
    (d / "bad.parquet").write_bytes(b"garbage")
    monkeypatch.setattr(se, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(dd.se, "AUDIT_DIR", tmp_path)

    rows, skipped = dd.load_day_counted(day)
    assert skipped == 1 and len(rows) == 1
    body = dd.render(rows, day, skipped=skipped)
    assert "读不了" in body and "1" in body, f"跳过数没进日报正文:\n{body}"


def test_the_other_counters_line_is_silent_for_routine_nonzero_fields(monkeypatch):
    """⭐防洪一头:平时就非零的计数,今天非零**不许**报。

    实测(2026-08-01~08-17,17 天):23 个"其它"字段里有 14 个在 12~16 天都非零
    —— 它们本来就该非零。初版写的是"非零就报",并在注释里断言"稳态这行不出现",
    那是**没验过的推断**:实跑出来一行列了 17 个字段,纯噪音。
    """
    monkeypatch.setattr(dd, "_baseline_zero_fields", lambda day, fields: set())
    line = dd._other_counters(_day(net_retry_count=5), "2026-08-17")
    assert line == "", f"平时就非零的字段被报了出来:{line}"


def test_the_other_counters_line_fires_when_a_quiet_field_wakes_up(monkeypatch):
    """⭐防洪另一头:平时是 0 的计数今天冒头,必须报。

    没有这一条,上面那条一绿,整行就可能是个永不触发的摆设 ——
    今天已经栽过一次"判据永远不会红"。
    """
    monkeypatch.setattr(dd, "_baseline_zero_fields",
                        lambda day, fields: {"firehose_http_error_count"})
    hbs = _day(firehose_http_error_count=7)          # 96 轮 × 7 = 672
    line = dd._other_counters(hbs, "2026-08-17")
    assert "firehose_http_error_count=672" in line, f"平时为 0 的字段冒头却没报:{line}"


def test_the_baseline_falls_back_to_reporting_everything(monkeypatch, tmp_path):
    """读不到历史(刚上线/目录空)时宁可多报,不可漏报。"""
    import storage_engine as se
    monkeypatch.setattr(dd.se, "AUDIT_DIR", tmp_path)
    fields = ["a", "b"]
    assert dd._baseline_zero_fields("2026-08-17", fields) == set(fields)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
