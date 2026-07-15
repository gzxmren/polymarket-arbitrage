#!/usr/bin/env python3
"""
第1步:回填历史 → 在加长的价格历史上独立复核市场校准 edge。

数据库 daily_price_snapshots 只到 2026-05-09;07-data/whale_report_*.json(372 份,回溯到 03-12)
的 positions[] 带 slug/curPrice/outcome/endDate/scan_time,可重建**鲸鱼持有过的市场**的历史 YES 价。
把它并进价格序列,历史前延约 2 个月,给校准 edge 一次**真正更独立**的 OOS 复核。

正确性安全阀:对**同时出现在 DB 和归档**的 (市场,日期),两处 YES 价必须吻合(MAD 小)——
否则说明 outcome→YES 归一化有误,不可信,中止。

建模让步(诚实):
 - 覆盖=鲸鱼持有盘(偏流动/可交易子集,非全市场);
 - 归档无成交量,回填盘给"可容纳 $200"的代理容量(鲸鱼持有→可吸 $200);
 - 同日多次报告取均值。

用法:PYTHONPATH=08-backtests python3 08-backtests/run_calibration_extended.py [--json]
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.data import connect, load_price_series, PriceSeries, _to_date
from run_h7b_price_sensitive import calibrate_curve, price_sensitive_cost, CAP
from run_calibration_oos import market_entries, make_trades, cw, cw_drop_top, BANDS, MIN_TEST_N

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_GLOB = str(PROJECT_ROOT / "07-data" / "whale_report_*.json")
PROXY_CAP = 50_000.0  # 代理容量:0.02*50k=$1000 ≥ $200,回填盘可吸满 $200
CUTOFFS_EXT = ["2026-04-15", "2026-05-01", "2026-05-15", "2026-06-01", "2026-06-15", "2026-07-01"]


def extract_archive():
    """从所有 whale_report 抽 (slug -> {date: [yes_prices], end_date})。"""
    obs = defaultdict(lambda: {"px": defaultdict(list), "end": None})
    files = sorted(glob.glob(ARCHIVE_GLOB))
    for fp in files:
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        sd = _to_date(d.get("scan_time", ""))
        if sd is None:
            continue
        for a in d.get("active_analyses", []):
            for p in a.get("positions", []):
                slug = p.get("slug")
                cur = p.get("curPrice")
                out = p.get("outcome")
                if not slug or cur is None or out is None:
                    continue
                try:
                    cur = float(cur)
                except (TypeError, ValueError):
                    continue
                if not (0.0 <= cur <= 1.0):
                    continue
                # 归一化到 YES 价:outcome=='Yes' 直接用;'No' 取 1-cur
                yes = cur if str(out).lower().startswith("y") else (1.0 - cur)
                obs[slug]["px"][sd].append(yes)
                ed = _to_date(p.get("endDate", ""))
                if ed and obs[slug]["end"] is None:
                    obs[slug]["end"] = ed
    # 同日多报告取均值
    out = {}
    for slug, rec in obs.items():
        daily = {dd: float(np.mean(v)) for dd, v in rec["px"].items()}
        out[slug] = {"daily": daily, "end": rec["end"]}
    return out, len(files)


def validate(db_series, arch):
    """重叠 (市场,日期) 的 YES 价一致性校验。返回 (n_overlap, MAD)。"""
    diffs = []
    for slug, rec in arch.items():
        ps = db_series.get(slug)
        if ps is None:
            continue
        db_by_date = dict(zip(ps.dates, ps.yes_prices))
        for dd, yes in rec["daily"].items():
            if dd in db_by_date:
                diffs.append(abs(yes - db_by_date[dd]))
    if not diffs:
        return 0, None
    return len(diffs), float(np.mean(diffs))


def merge(db_series, arch):
    """把归档观测并入价格序列(前延历史 / 新增市场)。返回新 series dict。"""
    series = {k: v for k, v in db_series.items()}
    added_dates = new_markets = 0
    for slug, rec in arch.items():
        daily, end = rec["daily"], rec["end"]
        ps = series.get(slug)
        if ps is None:
            ps = PriceSeries(market=slug, end_date=end)
            series[slug] = ps
            new_markets += 1
            existing = set()
        else:
            existing = set(ps.dates)
            if ps.end_date is None and end:
                ps.end_date = end
        # 合并日期(去重),重排
        merged = dict(zip(ps.dates, zip(ps.yes_prices, ps.cap_usd)))
        for dd, yes in daily.items():
            if dd not in merged:
                merged[dd] = (yes, PROXY_CAP)  # 回填盘用代理容量
                if dd not in existing:
                    added_dates += 1
        ordered = sorted(merged)
        ps.dates = ordered
        ps.yes_prices = [merged[x][0] for x in ordered]
        ps.cap_usd = [merged[x][1] for x in ordered]
    return series, added_dates, new_markets


def run_bands(entries, cost, cutoffs):
    """在给定入场宇宙上跑各 band × cutoff 的 test 净资金加权/抗单点。"""
    res = {}
    for b in BANDS:
        tr = make_trades(entries, b, cost)
        row = {}
        for c in cutoffs:
            cc = date.fromisoformat(c)
            te = [t for t in tr if t.entry_date >= cc]
            row[c] = {"n": len(te), "cw": cw(te, "net_ret")[0],
                      "top": cw_drop_top(te, "net_ret")}
        res[b] = {"n_total": len(tr), "cutoffs": row}
    return res


def _p(v):
    return "  —  " if v is None else f"{v*100:+6.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = connect(args.db)
    db_series = load_price_series(conn)
    conn.close()

    curve, meta = calibrate_curve("median")
    cost = price_sensitive_cost(curve)

    print("=" * 100)
    print("🗄️  回填历史 → 加长历史上复核市场校准 edge (第1步)")
    print("=" * 100)
    arch, n_files = extract_archive()
    print(f"归档: {n_files} 份 whale_report | 抽到 {len(arch)} 个市场的历史价")

    n_ov, mad = validate(db_series, arch)
    print(f"正确性校验: 重叠(市场,日)点 {n_ov} | 平均绝对价差 MAD = "
          f"{mad:.4f}" if mad is not None else "无重叠")
    if mad is None or mad > 0.05:
        print("🔴 归一化校验未过(MAD>0.05 或无重叠)——归档 YES 价与库不一致,中止,不出结论。")
        return
    print(f"✅ 校验通过(MAD={mad:.4f} < 0.05),归档 YES 价与库一致,可信。")

    ext_series, added, newm = merge(db_series, arch)
    # 历史跨度对比
    db_dates = [d for ps in db_series.values() for d in ps.dates]
    ext_dates = [d for ps in ext_series.values() for d in ps.dates]
    print(f"\n历史跨度: DB {min(db_dates)}~{max(db_dates)} → 扩展后 {min(ext_dates)}~{max(ext_dates)}")
    print(f"  新增市场 {newm} | 新增(市场,日)价格点 {added}")

    ent_db = market_entries(db_series, cost)
    ent_ext = market_entries(ext_series, cost)
    print(f"  入场宇宙: DB {len(ent_db)} → 扩展 {len(ent_ext)} (+{len(ent_ext)-len(ent_db)})")

    res = run_bands(ent_ext, cost, CUTOFFS_EXT)
    print(f"\n{'─'*100}\n扩展历史上各 band 的 test 净资金加权/抗单点(价格敏感成本):")
    hdr = f"  {'band':>6}" + "".join(f"{c[5:]:>16}" for c in CUTOFFS_EXT)
    print(hdr)
    for b in BANDS:
        cells = []
        for c in CUTOFFS_EXT:
            m = res[b]["cutoffs"][c]
            if m["n"] < MIN_TEST_N or m["cw"] is None:
                cells.append(f"{'薄n=' + str(m['n']):>16}")
            else:
                cells.append(f"{_p(m['cw']) + '/' + _p(m['top']):>16}")
        print(f"  {b:>6.2f}" + "".join(cells))

    print(f"\n{'='*100}")
    print("读法: 早 cutoff(04-15/05-01)现在是**真·样本外**(那段数据 DB 里原本没有);")
    print("      若 band 0.60 在这些更早、更独立的窗口仍净正 → edge 从'2月嵌套'升级为'跨4月多窗'一致,可信度实质提升。")
    print("      仍非定论: 覆盖偏鲸鱼持有盘、回填用代理容量、探针成本 9 天。")
    print("=" * 100)

    if args.json:
        RESULTS_DIR = Path(__file__).resolve().parent / "results"
        RESULTS_DIR.mkdir(exist_ok=True)
        p = RESULTS_DIR / f"calibration_extended_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps({
            "generated_at": datetime.now().isoformat(),
            "n_files": n_files, "overlap_points": n_ov, "mad": mad,
            "new_markets": newm, "added_points": added,
            "entries_db": len(ent_db), "entries_ext": len(ent_ext),
            "hist_span": [str(min(ext_dates)), str(max(ext_dates))],
            "bands": {str(k): v for k, v in res.items()},
        }, ensure_ascii=False, indent=2, default=str))
        print(f"\n结果已保存: {p}")


if __name__ == "__main__":
    main()
