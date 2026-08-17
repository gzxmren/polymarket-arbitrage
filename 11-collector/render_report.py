#!/usr/bin/env python3
"""render_report.py — 采集器趋势报表:一张自包含静态单页(2026-08-17)。

## 定位:**下钻,不是首页**

第一眼是每日日报(`daily_digest.py`,推到 Telegram)。这张页面是"日报某行不对劲、
想看看它是从哪天开始变坏的"时才点开的东西。所以:不做实时刷新、不做告警 ——
那些是日报和 `alerts.py` 的活,在这里重做一遍只会变成第二份会分叉的实现。

## 为什么是静态文件而不是 Flask 服务

本项目上一个监控网页(2026-03-20 提交「Phase 3: 质量监控与自动化」)死了至少 26 天
没人发现:两个 systemd 单元 disabled+inactive、journal 零条记录、
数据源 `07-data/` 停写 26 天、3000 端口还被别的项目占着。
⇒ 起服务这条路已经被证伪过一次。落一份自包含 HTML,跟日报**同一个 systemd service**
一起生成 —— 不占端口、不需要人拉起、也不可能和日报各自漂移。

## ⭐最要紧的那条判据不是"图好看"

上一版最危险的地方不是它死了,是**它死了还能打开**:一个显示着 26 天前数字的看板,
看上去和正常的一模一样。所以本页必须显示**数据本身的最新时刻**(不只是生成时刻),
并在数据明显过期时自己喊出来。判据:`test_render_report.py` 判据组 A。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import html as _html
import sys
import time
from pathlib import Path

import daily_digest as dd
import storage_engine as se

OUT_DEFAULT = se.DATA_ROOT / "report.html"
DEFAULT_DAYS = 21
# 数据比这更旧就在页面上喊。一天一轮日报 + 采集器 15 分钟一轮 ⇒ 正常永远远小于此值。
STALE_AFTER_S = 26 * 3600

# 只读心跳。⭐与日报共用同一份字段清单再加几个趋势用的量 ——
# 不另起一份"报表专用字段表",否则两份清单必然分叉。
CONSUMED_FIELDS = tuple(dd.CONSUMED_FIELDS)


def _esc(s) -> str:
    return _html.escape(str(s))


def load_days(days: int = DEFAULT_DAYS) -> list[dict]:
    """读最近 N 个日分区的心跳。只碰采集器自己写的审计目录。"""
    import pyarrow as pa
    import pyarrow.parquet as pq
    parts = sorted(glob.glob(str(se.AUDIT_DIR / "dt=*")))[-days:]
    rows: list[dict] = []
    skipped = 0
    for part in parts:
        for f in sorted(glob.glob(f"{part}/*.parquet")):
            try:
                rows += pq.read_table(f).to_pylist()
            except (OSError, pa.lib.ArrowException):
                # ⚠️ pyarrow 的异常分属**三个**家族:ArrowInvalid→ValueError、
                # ArrowTypeError→TypeError、ArrowIOError→OSError。只捕其中一两个都会漏
                # (初版只捕 OSError;改成 (OSError, ValueError) 仍漏 ArrowTypeError)。
                # 共同基类是 ArrowException,用它才真覆盖。
                # (2026-08-17 code review 抓出 + 实测逐个复现 MRO。)
                skipped += 1     # ⭐跳过必须出声:静默少一块和"那天本来就少"无法区分
                continue
    if skipped:
        print(f"⚠️ 有 {skipped} 个心跳文件读不了,已跳过(趋势不完整)", flush=True)
    rows.sort(key=lambda r: r.get("ts") or 0)
    return rows


def daily_rows(hbs: list[dict]) -> list[dict]:
    """按天聚合。⭐腿的算法一律走 `daily_digest`,不在这里重写一份。"""
    by_day: dict[str, list[dict]] = {}
    for h in hbs:
        by_day.setdefault(str(h.get("dt") or "?"), []).append(h)
    out = []
    for day in sorted(by_day):
        g = by_day[day]
        n = lambda k: sum(dd._num(x, k) for x in g)          # noqa: E731
        secs = sorted(dd._num(x, "cycle_seconds") for x in g if dd._num(x, "cycle_seconds"))
        out.append({
            "day": day,
            "cycles": len(g),
            "trades": n("new_trades"),
            "resolved": n("newly_resolved"),
            "discovered": n("new_discovered"),
            "registered": n("new_registered"),
            "dropped": n("pending_registration_dropped_count"),
            "pending_peak": max((dd._num(x, "pending_registration_count") for x in g), default=0),
            # 断供连续段从**当天序列**自己算 —— 与日报同一个函数,不重写
            "outage_run": dd._longest_outage_run(g),
            "truth_zero": max((dd._num(x, "truth_supply_zero_streak") for x in g), default=0),
            "cycle_p50": secs[len(secs) // 2] if secs else 0,
            "cycle_max": secs[-1] if secs else 0,
            "alert_dropped": n("alert_dropped_count"),
            "last_ts": max((dd._num(x, "ts") for x in g), default=0),
        })
    return out


def _bars(rows: list[dict], key: str, *, bad=lambda r: False, fmt="{:,.0f}") -> str:
    """一组横条。用 CSS 宽度,不引任何图表库(自包含硬要求)。"""
    top = max((r[key] for r in rows), default=0) or 1
    out = []
    for r in rows:
        w = r[key] / top * 100
        cls = "bar bad" if bad(r) else "bar"
        out.append(
            f'<div class="row"><span class="d">{_esc(r["day"][5:])}</span>'
            f'<div class="track"><div class="{cls}" style="width:{w:.1f}%"></div></div>'
            f'<span class="v">{fmt.format(r[key])}</span></div>')
    return "\n".join(out)


def render(hbs: list[dict], now: float | None = None) -> str:
    now = time.time() if now is None else now
    gen = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")

    if not hbs:
        body = ('<p class="loud">🔴 <b>没有心跳</b> —— 这不是「系统很闲」,是没有数据。'
                '须查 <code>polymarket-rebirth-collector.timer</code> 与 '
                '<code>collector.log</code>。</p>')
        return _PAGE.format(gen=gen, freshness="", body=body)

    rows = daily_rows(hbs)
    last_ts = max(r["last_ts"] for r in rows)
    age = now - last_ts if last_ts else None
    last_str = (dt.datetime.fromtimestamp(last_ts, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
                if last_ts else "未知")
    stale = age is not None and age > STALE_AFTER_S
    fresh_cls = "stamp bad" if stale else "stamp"
    warn = (f' · <b>🔴 数据已过期 {age / 3600:.0f} 小时</b>' if stale else "")
    freshness = (f'<p class="{fresh_cls}">数据最新:<b>{_esc(last_str)}</b>'
                 f'(最后一天 {_esc(rows[-1]["day"])},共 {len(rows)} 天){warn}</p>')

    body = f"""
<section><h2>腿1 · 成交流</h2>
<p class="note">每日新采成交笔数。<b>红条</b>=当天出现过 ≥{dd.alerts.TRADE_FLOW_OUTAGE_CYCLES} 轮连续抓取失败
(阈值依据:实测良性最长 3 轮)。</p>
<div class="chart">{_bars(rows, "trades", bad=lambda r: r["outage_run"] >= dd.alerts.TRADE_FLOW_OUTAGE_CYCLES)}</div></section>

<section><h2>腿2 · 结算真值</h2>
<p class="note">每日新拿到官方结算的市场数。红条 = 当天出现过 ≥{dd.alerts.TRUTH_SUPPLY_ZERO_CYCLES} 轮零流。</p>
<div class="chart">{_bars(rows, "resolved", bad=lambda r: r["truth_zero"] >= dd.alerts.TRUTH_SUPPLY_ZERO_CYCLES)}</div></section>

<section><h2>腿3 · 注册积压丢弃</h2>
<p class="note">被容量闸丢掉的市场数。<b>稳态应恒 0</b> —— 非 0 即注册配额跟不上涌入。
2026-08-17 改批量注册后回到 0。</p>
<div class="chart">{_bars(rows, "dropped", bad=lambda r: r["dropped"] > 0)}</div></section>

<section><h2>整轮耗时(中位)</h2>
<p class="note">红条 = 当天最慢周期越过慢周期告警线 {dd.alerts.slow_cycle_threshold_s():.0f}s。</p>
<div class="chart">{_bars(rows, "cycle_p50", bad=lambda r: r["cycle_max"] >= dd.alerts.slow_cycle_threshold_s(), fmt="{:,.0f}s")}</div></section>

<section><h2>逐日明细</h2>
<div class="scroll"><table>
<thead><tr><th>日期</th><th>轮次</th><th>成交</th><th>新结算</th><th>涌入</th><th>登记</th>
<th>丢弃</th><th>断供最长</th><th>周期中位</th><th>告警丢弃</th></tr></thead><tbody>
{"".join(
    f'<tr class="{"bad" if (r["dropped"] or r["outage_run"] >= dd.alerts.TRADE_FLOW_OUTAGE_CYCLES) else ""}">'
    f'<td>{_esc(r["day"])}</td><td class="n">{r["cycles"]}</td><td class="n">{r["trades"]:,}</td>'
    f'<td class="n">{r["resolved"]:,}</td><td class="n">{r["discovered"]:,}</td>'
    f'<td class="n">{r["registered"]:,}</td><td class="n">{r["dropped"]:,}</td>'
    f'<td class="n">{r["outage_run"] or ""}</td><td class="n">{r["cycle_p50"]:.0f}s</td>'
    f'<td class="n">{r["alert_dropped"]:,}</td></tr>'
    for r in reversed(rows))}
</tbody></table></div></section>
"""
    return _PAGE.format(gen=gen, freshness=freshness, body=body)


_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>采集器趋势</title>
<style>
:root{{--ground:#EEF1F4;--surface:#fff;--surface2:#E4E9EE;--ink:#12171D;--ink2:#4C5661;
--ink3:#79838F;--rule:#D2D9E0;--accent:#8F5400;--crit:#9C2A21}}
@media (prefers-color-scheme:dark){{:root{{--ground:#101419;--surface:#181D24;--surface2:#212832;
--ink:#DDE3EA;--ink2:#9AA5B1;--ink3:#6F7A86;--rule:#2A323C;--accent:#E3A548;--crit:#EE7A6E}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:ui-sans-serif,system-ui,
'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.65;font-size:15px}}
.wrap{{max-width:960px;margin:0 auto;padding:36px 20px 72px}}
h1{{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}}
h2{{font-size:17px;margin:0 0 6px}}
.stamp{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:var(--ink2);
margin:0 0 4px;padding:8px 12px;background:var(--surface);border:1px solid var(--rule);border-radius:3px}}
.stamp.bad{{color:var(--crit);border-color:var(--crit)}}
.gen{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--ink3);margin:0 0 26px}}
.note{{font-size:13px;color:var(--ink2);margin:0 0 12px}}
section{{margin:0 0 34px}}
.chart{{background:var(--surface);border:1px solid var(--rule);border-radius:3px;padding:16px 18px}}
.row{{display:grid;grid-template-columns:52px 1fr 96px;gap:10px;align-items:center;margin-bottom:4px}}
.d{{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--ink3)}}
.track{{height:16px;background:var(--surface2);border-radius:2px;overflow:hidden}}
.bar{{height:100%;background:var(--ink3);border-radius:2px}}
.bar.bad{{background:var(--crit)}}
.v{{font-family:ui-monospace,Menlo,monospace;font-size:12px;text-align:right;
font-variant-numeric:tabular-nums;color:var(--ink2)}}
.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px;min-width:640px;background:var(--surface)}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--rule);text-align:left}}
th{{font-size:11px;letter-spacing:.06em;color:var(--ink3);font-weight:500;text-transform:uppercase}}
td.n{{font-family:ui-monospace,Menlo,monospace;text-align:right;font-variant-numeric:tabular-nums}}
tr.bad td{{background:color-mix(in srgb,var(--crit) 9%,transparent)}}
.loud{{font-size:17px;padding:18px;background:var(--surface);border-left:3px solid var(--crit);
border-radius:0 3px 3px 0}}
code{{font-family:ui-monospace,Menlo,monospace;font-size:.88em;background:var(--surface2);
padding:1px 5px;border-radius:3px}}
footer{{margin-top:44px;padding-top:16px;border-top:1px solid var(--rule);
font-size:12px;color:var(--ink3)}}
</style></head><body><div class="wrap">
<h1>采集器趋势</h1>
{freshness}
<p class="gen">本页生成于 {gen} · 由 polymarket-daily-digest.service 每天重新生成 ·
第一眼看的是 Telegram 日报,这页是下钻</p>
{body}
<footer>只读心跳 parquet(11-collector/data/audit)。自包含单页,不引任何外部资源。
判据:10-tests/unit/test_render_report.py</footer>
</div></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="采集器趋势报表(静态单页)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    a = ap.parse_args()
    html = render(load_days(a.days))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(a.out)          # 原子落位:半张页面比没有页面更糟
    print(f"[趋势报表] 已生成 {a.out}({len(html):,} 字节)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
