#!/usr/bin/env python3
"""
鲸鱼追踪器 V2
从最近交易识别大额交易者，追踪其活动
"""

import json
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# 鲸鱼标准
WHALE_TRADE_THRESHOLD = 1000      # 单笔交易 > $1000 (降低阈值)
WHALE_TOTAL_VOLUME = 10000        # 总交易量 > $10k (降低阈值)
TRACKING_WALLETS_LIMIT = 20       # 追踪前20个活跃大户

# 状态目录
STATE_DIR = Path(__file__).parent.parent.parent / "07-data" / "whale_states"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_api(base_url: str, endpoint: str) -> dict | list | None:
    """获取API数据"""
    url = f"{base_url}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "WhaleTracker/2.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        print(f"Error: {url} - {e}", file=sys.stderr)
        return None


def fetch_recent_trades(limit: int = 500) -> list:
    """获取最近交易"""
    return fetch_api(DATA_API, f"/trades?limit={limit}") or []


def identify_active_whales(trades: list) -> dict:
    """从交易记录识别活跃鲸鱼"""
    wallet_stats = defaultdict(lambda: {
        "total_volume": 0,
        "trade_count": 0,
        "large_trades": 0,
        "markets": set(),
        "last_trade": 0,
        "pseudonym": "",
        "name": ""
    })
    
    for trade in trades:
        wallet = trade.get("proxyWallet", "").lower()
        if not wallet:
            continue
        
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        volume = size * price
        timestamp = trade.get("timestamp", 0)
        
        wallet_stats[wallet]["total_volume"] += volume
        wallet_stats[wallet]["trade_count"] += 1
        wallet_stats[wallet]["markets"].add(trade.get("slug", ""))
        
        if volume >= WHALE_TRADE_THRESHOLD:
            wallet_stats[wallet]["large_trades"] += 1
        
        if timestamp > wallet_stats[wallet]["last_trade"]:
            wallet_stats[wallet]["last_trade"] = timestamp
            wallet_stats[wallet]["pseudonym"] = trade.get("pseudonym", "")
            wallet_stats[wallet]["name"] = trade.get("name", "")
    
    # 筛选鲸鱼
    whales = {}
    for wallet, stats in wallet_stats.items():
        if stats["total_volume"] >= WHALE_TOTAL_VOLUME or stats["large_trades"] >= 3:
            whales[wallet] = {
                "wallet": wallet,
                "total_volume": stats["total_volume"],
                "trade_count": stats["trade_count"],
                "large_trades": stats["large_trades"],
                "markets_traded": len(stats["markets"]),
                "last_trade": stats["last_trade"],
                "pseudonym": stats["pseudonym"] or wallet[:10] + "...",
                "name": stats["name"]
            }
    
    # 按交易量排序
    sorted_whales = dict(sorted(whales.items(), 
                                key=lambda x: x[1]["total_volume"], 
                                reverse=True)[:TRACKING_WALLETS_LIMIT])
    
    return sorted_whales


def fetch_wallet_positions(wallet: str) -> list:
    """获取钱包当前持仓"""
    return fetch_api(DATA_API, f"/positions?user={wallet}") or []


def fetch_wallet_pnl(wallet: str) -> dict:
    """获取钱包盈亏数据"""
    pnl_data = fetch_api(DATA_API, f"/profit-loss?user={wallet}")
    if pnl_data and len(pnl_data) > 0:
        return pnl_data[0]  # 通常返回数组
    return {}


def load_wallet_state(wallet: str) -> dict:
    """加载历史状态"""
    state_file = STATE_DIR / f"{wallet}.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"positions": {}, "last_check": None}


def save_wallet_state(wallet: str, state: dict):
    """保存状态"""
    state_file = STATE_DIR / f"{wallet}.json"
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def detect_changes(current_positions: list, previous_positions: dict) -> list:
    """检测持仓变化"""
    changes = []
    current_dict = {}
    
    for pos in current_positions:
        market = pos.get("market", pos.get("title", "Unknown"))
        current_dict[market] = pos
    
    # 新持仓
    for market, pos in current_dict.items():
        if market not in previous_positions:
            changes.append({
                "type": "new",
                "market": market,
                "outcome": pos.get("outcome", "?"),
                "size": float(pos.get("size", 0)),
                "avg_price": float(pos.get("avgPrice", 0)),
                "current_price": float(pos.get("curPrice", pos.get("currentPrice", 0)))
            })
        else:
            # 仓位变化
            old_size = float(previous_positions[market].get("size", 0))
            new_size = float(pos.get("size", 0))
            if abs(new_size - old_size) > 0.01:
                changes.append({
                    "type": "increased" if new_size > old_size else "decreased",
                    "market": market,
                    "outcome": pos.get("outcome", "?"),
                    "old_size": old_size,
                    "new_size": new_size,
                    "change": new_size - old_size,
                    "avg_price": float(pos.get("avgPrice", 0)),
                    "current_price": float(pos.get("curPrice", pos.get("currentPrice", 0)))
                })
    
    # 清仓
    for market, pos in previous_positions.items():
        if market not in current_dict:
            changes.append({
                "type": "closed",
                "market": market,
                "outcome": pos.get("outcome", "?"),
                "previous_size": float(pos.get("size", 0))
            })
    
    return changes


def analyze_whale(wallet: str, whale_info: dict) -> dict:
    """分析单个鲸鱼"""
    # 获取数据
    positions = fetch_wallet_positions(wallet)
    pnl = fetch_wallet_pnl(wallet)
    
    # 加载历史
    previous = load_wallet_state(wallet)
    prev_positions = previous.get("positions", {})
    
    # 检测变化
    changes = detect_changes(positions, prev_positions)
    
    # 计算统计
    total_value = sum(
        float(p.get("size", 0)) * float(p.get("curPrice", p.get("currentPrice", 0)))
        for p in positions
    )
    
    total_pnl = float(pnl.get("total", 0)) if pnl else 0
    
    # 保存新状态
    current_dict = {p.get("market", p.get("title", "Unknown")): p for p in positions}
    save_wallet_state(wallet, {
        "positions": current_dict,
        "last_check": datetime.now(timezone.utc).isoformat(),
        "total_value": total_value,
        "total_pnl": total_pnl
    })
    
    return {
        "wallet": wallet,
        "info": whale_info,
        "positions": positions,
        "position_count": len(positions),
        "total_value": total_value,
        "total_pnl": total_pnl,
        "changes": changes,
        "has_activity": len(changes) > 0
    }


def format_whale(analysis: dict) -> str:
    """格式化鲸鱼信息"""
    w = analysis["info"]
    lines = [
        f"\n{'='*70}",
        f"🐋 鲸鱼: {w['pseudonym']}",
        f"   钱包: {w['wallet'][:10]}...{w['wallet'][-6:]}",
        f"   24h交易量: ${w['total_volume']:,.0f} | 大单: {w['large_trades']} 笔",
        f"   交易次数: {w['trade_count']} | 涉及市场: {w['markets_traded']}",
        f"   当前持仓: {analysis['position_count']} 个 | 总价值: ${analysis['total_value']:,.2f}",
    ]
    
    if analysis["total_pnl"] != 0:
        pnl_emoji = "📈" if analysis["total_pnl"] > 0 else "📉"
        lines.append(f"   总盈亏: {pnl_emoji} ${analysis['total_pnl']:+.2f}")
    
    if analysis["changes"]:
        lines.append(f"\n   ⚡ 最新变动:")
        for change in analysis["changes"][:5]:  # 只显示前5个
            if change["type"] == "new":
                lines.append(f"      + 新建仓: {change['market'][:40]}...")
                lines.append(f"        {change['outcome']} | {change['size']:.2f} @ ${change['avg_price']:.3f}")
            elif change["type"] in ["increased", "decreased"]:
                emoji = "📈" if change["type"] == "increased" else "📉"
                lines.append(f"      {emoji} {change['type'].upper()}: {change['market'][:40]}...")
                lines.append(f"        {change['change']:+.2f} ({change['old_size']:.2f} → {change['new_size']:.2f})")
            elif change["type"] == "closed":
                lines.append(f"      - 清仓: {change['market'][:40]}...")
    
    if analysis["positions"]:
        lines.append(f"\n   💼 主要持仓:")
        # 按价值排序
        sorted_pos = sorted(analysis["positions"], 
                           key=lambda p: float(p.get("size", 0)) * float(p.get("curPrice", p.get("currentPrice", 0))),
                           reverse=True)[:3]
        for pos in sorted_pos:
            market = pos.get("market", pos.get("title", "Unknown"))[:35]
            outcome = pos.get("outcome", "?")
            size = float(pos.get("size", 0))
            pnl = float(pos.get("pnl", 0))
            value = size * float(pos.get("curPrice", pos.get("currentPrice", 0)))
            lines.append(f"      • {market}... | {outcome} | ${value:,.0f} | P&L: ${pnl:+.0f}")
    
    return "\n".join(lines)


def main():
    print("🐋 鲸鱼追踪器 V2")
    print(f"   大单阈值: ${WHALE_TRADE_THRESHOLD:,.0f}")
    print(f"   追踪数量: 前{TRACKING_WALLETS_LIMIT}个活跃大户")
    print("-" * 70)
    
    # 获取最近交易
    print("\n📡 获取最近交易...")
    trades = fetch_recent_trades(limit=1000)
    print(f"   获取 {len(trades)} 笔交易")
    
    if not trades:
        print("\n❌ 无法获取交易数据")
        return
    
    # 识别鲸鱼
    print("\n🔍 识别活跃鲸鱼...")
    whales = identify_active_whales(trades)
    print(f"   发现 {len(whales)} 个活跃大户")
    
    if not whales:
        print("\n❌ 未发现符合条件的鲸鱼")
        return
    
    # 分析每个鲸鱼
    print(f"\n📊 分析鲸鱼持仓...")
    active_whales = []
    
    for i, (wallet, info) in enumerate(whales.items(), 1):
        print(f"   [{i}/{len(whales)}] 分析 {info['pseudonym']}...", end=" ")
        analysis = analyze_whale(wallet, info)
        
        if analysis["has_activity"]:
            active_whales.append(analysis)
            print(f"有变动!")
        else:
            print(f"无变动")
    
    # 显示结果
    print(f"\n{'='*70}")
    print("📈 鲸鱼分析报告")
    print(f"{'='*70}")
    
    # 显示有变动的鲸鱼
    if active_whales:
        print(f"\n⚡ 有活动鲸鱼 ({len(active_whales)} 个):")
        for analysis in active_whales:
            print(format_whale(analysis))
    
    # 显示所有追踪的鲸鱼摘要
    print(f"\n{'='*70}")
    print("📋 追踪列表摘要:")
    print(f"{'='*70}")
    for wallet, info in whales.items():
        analysis = analyze_whale(wallet, info)
        status = "🟢 有变动" if analysis["has_activity"] else "⚪ 无变动"
        print(f"   {status} {info['pseudonym'][:20]:<20} | 24h量: ${info['total_volume']:>10,.0f} | 持仓: {analysis['position_count']} 个")
    
    # 保存报告
    report = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "whales_tracked": len(whales),
        "active_whales": len(active_whales),
        "threshold": WHALE_TRADE_THRESHOLD,
        "whales": list(whales.values()),
        "active_analyses": active_whales
    }
    
    report_file = STATE_DIR.parent / f"whale_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n📄 报告已保存: {report_file}")


if __name__ == "__main__":
    main()
