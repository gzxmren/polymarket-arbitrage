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
    mean_net: float
    win_rate: float
    total_deployable: float


def whale_alpha(results: list[TradeResult], min_trades: int = 5) -> list[WhaleAlpha]:
    """逐鲸鱼真实跟随收益,过滤出现次数过少的(剔噪声/运气)。按均值降序。"""
    by_w: dict[str, list[TradeResult]] = {}
    for r in results:
        by_w.setdefault(r.wallet, []).append(r)

    out: list[WhaleAlpha] = []
    for w, rs in by_w.items():
        if len(rs) < min_trades:
            continue
        nets = [r.net_ret for r in rs]
        out.append(WhaleAlpha(
            wallet=w, n=len(rs), mean_net=mean(nets),
            win_rate=sum(1 for x in nets if x > 0) / len(nets),
            total_deployable=sum(r.deployable for r in rs),
        ))
    out.sort(key=lambda x: x.mean_net, reverse=True)
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
        "resolved_only_net": _agg([r.net_ret for r in resolved]),
        "by_markable_days": strata_summary,
        "total_deployable": sum(r.deployable for r in results),
    }


def verdict(net_summary: dict, min_n: int = 30) -> str:
    """一句话判决(诚实口径)。"""
    n = net_summary.get("n") or 0
    m = net_summary.get("mean")
    if n < min_n or m is None:
        return f"⚠️ 样本不足(n={n}),无法判决——需更多数据。"
    if m > 0:
        wr = net_summary.get("win_rate") or 0
        return (f"🟢 扣成本后均值为正(+{m*100:.2f}%/信号, 胜率{wr*100:.0f}%, n={n})。"
                f"进一步看分层/鲸鱼 alpha 是否稳健;样本仅 1 月,需复核。")
    return (f"🔴 扣成本后均值为负({m*100:.2f}%/信号, n={n})。"
            f"无差别跟全部鲸鱼不赚钱——看精选鲸鱼能否翻正,否则砍。")
