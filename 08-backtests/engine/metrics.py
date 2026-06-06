#!/usr/bin/env python3
"""
回测引擎 — 指标
把 TradeResult 列表聚合成诚实的统计:扣成本后期望、胜率、分层、鲸鱼 alpha、容量。
纯标准库实现(不依赖 numpy/pandas)。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev

from .portfolio import TradeResult


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson 相关系数。<3 点或任一方差为 0 返回 None。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sx, sy = pstdev(xs), pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    return cov / (sx * sy)


def _agg(rets: list[float]) -> dict:
    if not rets:
        return {"n": 0, "mean": None, "median": None, "win_rate": None,
                "std": None, "sharpe_like": None, "min": None, "max": None}
    rets_sorted = sorted(rets)
    m = mean(rets)
    sd = pstdev(rets) if len(rets) > 1 else 0.0
    wins = sum(1 for r in rets if r > 0)
    return {
        "n": len(rets),
        "mean": m,
        "median": rets_sorted[len(rets_sorted) // 2],
        "win_rate": wins / len(rets),
        "std": sd,
        # 类夏普:每信号均值/波动(非年化,样本短仅参考)
        "sharpe_like": (m / sd) if sd > 0 else None,
        "min": rets_sorted[0],
        "max": rets_sorted[-1],
    }


def _cap_stats(results: list[TradeResult]) -> dict:
    """
    资金加权口径(诚实之锚):按"实际能部署的本金(deployable)"加权,
    回答"每投入一美元真实赚多少",而非"每笔交易平均赚多少"(等权)。
    edge 若集中在铺不开钱的薄市场,资金加权会把它打回原形。
    """
    w = sum(r.deployable for r in results)
    if w <= 0:
        return {"deployable": 0.0, "mean_net": None, "mean_gross": None, "win_rate": None}
    return {
        "deployable": w,
        # 每元投入的净/毛回报
        "mean_net": sum(r.net_ret * r.deployable for r in results) / w,
        "mean_gross": sum(r.gross_ret * r.deployable for r in results) / w,
        # 赚钱交易占用的资金比例(按资金算的胜率)
        "win_rate": sum(r.deployable for r in results if r.net_ret > 0) / w,
    }


def _markable_stratum(days: int) -> str:
    if days < 3:
        return "1-2d"
    if days < 7:
        return "3-6d"
    if days < 15:
        return "7-14d"
    return "15d+"


@dataclass
class WhaleAlpha:
    wallet: str
    n: int
    mean_net: float                 # 等权净均值(每笔一票)
    mean_net_cw: float | None       # 资金加权净均值(按 deployable;排序依据,可放大口径)
    win_rate: float
    total_deployable: float


def whale_alpha(results: list[TradeResult], min_trades: int = 5) -> list[WhaleAlpha]:
    """
    逐鲸鱼真实跟随收益,过滤出现次数过少的(剔噪声/运气)。
    **按资金加权净均值降序**(可放大口径,与 verdict 一致);等权同时保留作对照。
    无容量数据(总 deployable=0)的鲸鱼排最后。
    """
    by_w: dict[str, list[TradeResult]] = {}
    for r in results:
        by_w.setdefault(r.wallet, []).append(r)

    out: list[WhaleAlpha] = []
    for w, rs in by_w.items():
        if len(rs) < min_trades:
            continue
        nets = [r.net_ret for r in rs]
        dep = sum(r.deployable for r in rs)
        cw = (sum(r.net_ret * r.deployable for r in rs) / dep) if dep > 0 else None
        out.append(WhaleAlpha(
            wallet=w, n=len(rs), mean_net=mean(nets), mean_net_cw=cw,
            win_rate=sum(1 for x in nets if x > 0) / len(nets),
            total_deployable=dep,
        ))
    out.sort(key=lambda x: x.mean_net_cw if x.mean_net_cw is not None else float("-inf"),
             reverse=True)
    return out


def horizon_summary(results: list[TradeResult]) -> dict:
    """单个持有期的总体 + 分层统计。"""
    gross = _agg([r.gross_ret for r in results])
    net = _agg([r.net_ret for r in results])

    # 按"可标记天数"分层(回应快照覆盖薄的约束)
    strata: dict[str, list[float]] = {}
    for r in results:
        strata.setdefault(_markable_stratum(r.markable_days), []).append(r.net_ret)
    strata_summary = {k: _agg(v) for k, v in sorted(strata.items())}

    resolved = [r for r in results if r.resolved]

    return {
        "gross": gross,
        "net": net,
        "capital_weighted": _cap_stats(results),   # 资金加权(诚实之锚)
        "resolved_only_net": _agg([r.net_ret for r in resolved]),
        "by_markable_days": strata_summary,
        "total_deployable": sum(r.deployable for r in results),
    }


def verdict(net_summary: dict, cap_weighted_mean: float | None = None,
            min_n: int = 30) -> str:
    """
    一句话判决(诚实口径)。以**资金加权净均值**为准(可放大的真实回报),
    等权净均值作对照。三态:
      🟢 可放大  —— 资金加权 > 0(每投入一美元真的赚)。
      🟡 仅微仓  —— 等权 > 0 但资金加权 ≤ 0:edge 集中在铺不开钱的薄市场,放大即亏。
      🔴 不赚钱  —— 等权 ≤ 0:连每笔平均都亏。
    """
    n = net_summary.get("n") or 0
    ew = net_summary.get("mean")
    if n < min_n or ew is None:
        return f"⚠️ 样本不足(n={n}),无法判决——需更多数据。"
    wr = net_summary.get("win_rate") or 0
    cw = cap_weighted_mean
    if ew <= 0:
        return (f"🔴 等权净均值为负({ew*100:.2f}%/信号, n={n})。"
                f"连每笔平均都亏——看精选/精选鲸鱼能否翻正,否则砍。")
    if cw is None:
        return (f"🟢 等权净均值为正(+{ew*100:.2f}%/信号, 胜率{wr*100:.0f}%, n={n})。"
                f"⚠️ 无容量数据未能算资金加权;样本仅 1 月需复核。")
    if cw > 0:
        return (f"🟢 资金加权为正(+{cw*100:.2f}%/每元投入;等权 +{ew*100:.2f}%/信号, n={n})。"
                f"容量内可放大;样本仅 1 月需复核。")
    return (f"🟡 仅微仓可行:等权 +{ew*100:.2f}%/信号 但资金加权 {cw*100:.2f}%/每元投入 ≤0"
            f"(n={n})——edge 集中在铺不开钱的薄市场,放大即亏。")
