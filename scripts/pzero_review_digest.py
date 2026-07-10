#!/usr/bin/env python3
"""
pzero H6 复盘汇总 —— 会话启动时自动整理两份速览：
  1) 探针滑点分层：按"价格离边距离"把 slippage_probe 分成 均衡/近边/贴边 三档，
     看实测滑点分布，验证"给 H6 加价格下限只做均衡盘"值不值得做。
  2) cutoff 连贯曲线：把每个 distinct cutoff 的最新 pzero_oos_*.json 拉出来，
     对关键候选(H0/H1/H6/H7)列 test_cw(资金加权) 与 test_cw_drop_top(抗单点),
     看跨 cutoff 是否持续为正且抗单点——单个 cutoff 好看没意义。

设计：只读、永远 exit 0（挂在 SessionStart hook 里，绝不能打断会话）。
自动退役：过了 SUNSET 日期后静默不输出（OOS 窗口跑完就不用天天看了）。
独立运行 `python3 scripts/pzero_review_digest.py --full` 看完整版。
"""

import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE_DB = PROJECT_ROOT / "07-data" / "slippage_probe.db"
RESULTS_DIR = PROJECT_ROOT / "08-backtests" / "results"

# OOS 窗口预计 ~07-20 跑完第 8 个 cutoff；给一周缓冲后自动退役这份会话简报。
SUNSET = date(2026, 7, 27)

# 关注的候选（+1d horizon 下）——H0/H1 是能下注主力，H6 微仓，H7 价格下限试验
KEY_HYPS = ["H0 基线(全量)", "H1 避超流动(<200k)", "H6 H5+微仓$200", "H7 H6+价格下限≥0.20"]


def _fmt_pct(x):
    return f"{x*100:+.2f}%" if isinstance(x, (int, float)) else "  n/a"


def slippage_strata():
    """按价格离边距离分层返回文本行；DB 缺失/异常则返回提示行。"""
    if not PROBE_DB.exists():
        return ["  探针 DB 不存在，跳过"]
    try:
        con = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT gamma_price, slippage_bps, spread_bps, fully_filled FROM slippage_probe"
        ).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001 - hook 必须不崩
        return [f"  探针读取失败: {e}"]

    if not rows:
        return ["  探针无数据"]

    # 离边距离 d = min(price, 1-price)：0.5 为最均衡，0 为最贴边
    buckets = {"均衡 d≥0.35": [], "近边 0.20–0.35": [], "贴边 d<0.20": []}
    for price, slip, spread, filled in rows:
        if price is None or slip is None:
            continue
        d = min(price, 1 - price)
        if d >= 0.35:
            key = "均衡 d≥0.35"
        elif d >= 0.20:
            key = "近边 0.20–0.35"
        else:
            key = "贴边 d<0.20"
        buckets[key].append((slip, spread, filled))

    out = [f"  {'档位':<14}{'n':>5}{'滑点中位':>9}{'滑点P90':>9}{'点差中位':>9}{'全成率':>7}"]
    for key, vals in buckets.items():
        if not vals:
            out.append(f"  {key:<14}{0:>5}{'—':>9}{'—':>9}{'—':>9}{'—':>7}")
            continue
        slips = sorted(v[0] for v in vals)
        spreads = [v[1] for v in vals if v[1] is not None]
        n = len(slips)
        p90 = slips[min(n - 1, int(round(0.9 * (n - 1))))]
        fill_rate = sum(1 for v in vals if v[2]) / n
        out.append(
            f"  {key:<14}{n:>5}{median(slips):>8.0f}b{p90:>8.0f}b"
            f"{(median(spreads) if spreads else 0):>8.0f}b{fill_rate*100:>6.0f}%"
        )
    return out


def _latest_per_cutoff():
    """每个 distinct cutoff 取最新一份 json，返回 [(cutoff, data), ...] 按 cutoff 升序。"""
    best = {}  # cutoff -> (generated_at, data)
    for f in RESULTS_DIR.glob("pzero_oos_*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        cut = d.get("cutoff")
        gen = d.get("generated_at", "")
        if not cut:
            continue
        if cut not in best or gen > best[cut][0]:
            best[cut] = (gen, d)
    return [(c, best[c][1]) for c in sorted(best)]


def cutoff_coherence():
    series = _latest_per_cutoff()
    if not series:
        return ["  无 pzero_oos 结果"]
    out = []
    header = f"  {'cutoff':<11}" + "".join(f"{h.split()[0]:>16}" for h in KEY_HYPS)
    out.append(header)
    out.append(f"  {'':11}" + "".join(f"{'cw/剔顶':>16}" for _ in KEY_HYPS))
    for cut, d in series:
        hy = d.get("horizons", {}).get("+1d", {})
        cells = []
        for name in KEY_HYPS:
            m = hy.get(name)
            if not m:
                cells.append(f"{'—':>16}")
                continue
            cw = m.get("test_cw")
            drop = m.get("test_cw_drop_top")
            robust = "稳" if (isinstance(drop, (int, float)) and drop > 0) else "塌"
            cells.append(f"{_fmt_pct(cw)}/{robust:>1}".rjust(16))
        out.append(f"  {cut:<11}" + "".join(cells))
    out.append("  （cw=test资金加权/每元；剔顶=剔除最大单点后仍>0 记'稳'否则'塌'——抗单点判据）")
    return out


def main():
    full = "--full" in sys.argv
    if not full and date.today() > SUNSET:
        return  # 窗口已过，静默退役

    lines = ["", "📊 [pzero H6 复盘速览]"]
    lines.append("① 探针滑点分层（越贴边滑点越黑洞→验证价格下限的价值）:")
    lines += slippage_strata()
    lines.append("② cutoff 连贯曲线（要的是跨 cutoff 持续为正且'稳'，非单点表头）:")
    lines += cutoff_coherence()
    if not full:
        lines.append(f"  （本速览 {SUNSET} 后自动退役；完整版: python3 scripts/pzero_review_digest.py --full）")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
