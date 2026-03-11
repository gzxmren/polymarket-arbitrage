#!/usr/bin/env python3
"""
鲸鱼追踪器
识别高绩效钱包，监控其持仓变化
"""

import json
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime
from pathlib import Path

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

# 鲸鱼筛选标准
MIN_PROFIT = 50000      # 最低盈利 $50k
MIN_WIN_RATE = 0.60     # 最低胜率 60%
MIN_TRADES = 100        # 最少交易次数
WHALE_POSITION_THRESHOLD = 5000  # 大单阈值 $5k

# 状态保存目录
STATE_DIR = Path(__file__).parent.parent.parent / "07-data" / "whale_states"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_api(base_url: str, endpoint: str) -> dict | list | None:
    """获取API数据"""
    url = f"{base_url}{endpoint}"
    try:
        req = Request(url, headers={"User-Agent": "PolymarketScanner/1.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError) as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def identify_whales(window: str = "all", limit: int = 100) -> list:
    """从排行榜识别鲸鱼"""
    data = fetch_api(GAMMA_API, f"/leaderboard?window={window}&limit={limit}")
    
    if not data:
        return []
    
    whales = []
    for user in data:
        profit = float(user.get("profit", 0) or 0)
        win_rate = float(user.get("winRate", 0) or user.get("win_rate", 0) or 0)
        trade_count = int(user.get("tradeCount", 0) or user.get("trade_count", 0) or 0)
        
        if profit >= MIN_PROFIT and win_rate >= MIN_WIN_RATE and trade_count >= MIN_TRADES:
            whales.append({
                "wallet": user.get("address", user.get("user", "")),
                "profit": profit,
                "win_rate": win_rate,
                "trade_count": trade_count,
                "rank": user.get("rank", 0),
                "volume": float(user.get("volume", 0) or 0)
            })
    
    return whales


def fetch_wallet_positions(wallet: str) -> list:
    """获取钱包持仓"""
    data = fetch_api(DATA_API, f"/positions?user={wallet}")
    return data if data else []


def fetch_wallet_trades(wallet: str, limit: int = 50) -> list:
    """获取钱包最近交易"""
    data = fetch_api(DATA_API, f"/trades?user={wallet}&limit={limit}")
    return data if data else []


def load_wallet_state(wallet: str) -> dict:
    """加载钱包历史状态"""
    state_file = STATE_DIR / f"{wallet}.json"
    if state_file.exists():
        try:
            with open(state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"positions": [], "last_check": None}


def save_wallet_state(wallet: str, state: dict):
    """保存钱包状态"""
    state_file = STATE_DIR / f"{wallet}.json"
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def detect_position_changes(current: list, previous: list) -> list:
    """检测持仓变化"""
    changes = []
    
    # 转换为字典便于比较
    prev_dict = {p.get("market", p.get("title", "")): p for p in previous}
    curr_dict = {p.get("market", p.get("title", "")): p for p in current}
    
    # 新增持仓
    for market, pos in curr_dict.items():
        if market not in prev_dict:
            changes.append({
                "type": "new_position",
                "market": market,
                "outcome": pos.get("outcome", "?"),
                "size": float(pos.get("size", 0)),
                "avg_price": float(pos.get("avgPrice", 0))
            })
        else:
            # 仓位变化
            prev_size = float(prev_dict[market].get("size", 0))
            curr_size = float(pos.get("size", 0))
            if abs(curr_size - prev_size) > 0.01:
                changes.append({
                    "type": "size_change",
                    "market": market,
                    "outcome": pos.get("outcome", "?"),
                    "old_size": prev_size,
                    "new_size": curr_size,
                    "change": curr_size - prev_size
                })
    
    # 清仓
    for market, pos in prev_dict.items():
        if market not in curr_dict:
            changes.append({
                "type": "closed",
                "market": market,
                "outcome": pos.get("outcome", "?"),
                "previous_size": float(pos.get("size", 0))
            })
    
    return changes


def analyze_whale(whale: dict) -> dict:
    """分析单个鲸鱼"""
    wallet = whale["wallet"]
    
    # 获取当前数据
    positions = fetch_wallet_positions(wallet)
    trades = fetch_wallet_trades(wallet, 20)
    
    # 加载历史状态
    previous_state = load_wallet_state(wallet)
    previous_positions = previous_state.get("positions", [])
    
    # 检测变化
    changes = detect_position_changes(positions, previous_positions)
    
    # 计算持仓价值
    total_value = sum(
        float(p.get("size", 0)) * float(p.get("curPrice", p.get("currentPrice", 0)))
        for p in positions
    )
    
    # 保存新状态
    save_wallet_state(wallet, {
        "positions": positions,
        "last_check": datetime.now().isoformat(),
        "total_value": total_value
    })
    
    return {
        "whale": whale,
        "positions": positions,
        "position_count": len(positions),
        "total_value": total_value,
        "recent_trades": len(trades),
        "changes": changes,
        "has_activity": len(changes) > 0 or len(trades) > 0
    }


def format_whale_analysis(analysis: dict) -> str:
    """格式化鲸鱼分析结果"""
    w = analysis["whale"]
    lines = [
        f"\n{'='*60}",
        f"🐋 鲸鱼: {w['wallet'][:10]}...{w['wallet'][-6:]}",
        f"   排行榜: #{w['rank']} | 胜率: {w['win_rate']:.1%} | 盈利: ${w['profit']:,.0f}",
        f"   当前持仓: {analysis['position_count']} 个 | 总价值: ${analysis['total_value']:,.2f}",
    ]
    
    if analysis["changes"]:
        lines.append(f"\n   📊 最新变动:")
        for change in analysis["changes"]:
            if change["type"] == "new_position":
                lines.append(f"      + 新建仓: {change['market'][:40]}...")
                lines.append(f"        方向: {change['outcome']} | 数量: {change['size']:.2f}")
            elif change["type"] == "size_change":
                direction = "加仓" if change["change"] > 0 else "减仓"
                lines.append(f"      ± {direction}: {change['market'][:40]}...")
                lines.append(f"        变化: {change['change']:+.2f}")
            elif change["type"] == "closed":
                lines.append(f"      - 清仓: {change['market'][:40]}...")
    
    if analysis["positions"]:
        lines.append(f"\n   💼 当前持仓:")
        for pos in analysis["positions"][:3]:  # 只显示前3个
            market = pos.get("market", pos.get("title", "Unknown"))[:35]
            outcome = pos.get("outcome", "?")
            size = float(pos.get("size", 0))
            pnl = float(pos.get("pnl", 0))
            lines.append(f"      • {market}... | {outcome} | {size:.2f} | P&L: ${pnl:+.2f}")
    
    return "\n".join(lines)


def main():
    print("🐋 鲸鱼追踪器")
    print(f"   筛选标准: 盈利>${MIN_PROFIT:,.0f}, 胜率>{MIN_WIN_RATE:.0%}, 交易>{MIN_TRADES}")
    print("-" * 60)
    
    # 识别鲸鱼
    print("\n🔍 从排行榜识别鲸鱼...")
    whales = identify_whales(limit=200)
    print(f"   发现 {len(whales)} 个符合条件的鲸鱼")
    
    if not whales:
        print("\n❌ 未发现符合条件的鲸鱼")
        return
    
    # 分析每个鲸鱼
    print(f"\n📊 分析鲸鱼持仓...")
    active_whales = []
    
    for i, whale in enumerate(whales[:10], 1):  # 只分析前10个
        print(f"   [{i}/{min(10, len(whales))}] 分析 {whale['wallet'][:10]}...")
        analysis = analyze_whale(whale)
        
        if analysis["has_activity"]:
            active_whales.append(analysis)
            print(format_whale_analysis(analysis))
    
    # 汇总
    print(f"\n{'='*60}")
    print("📈 汇总:")
    print(f"   追踪鲸鱼数: {len(whales)}")
    print(f"   有活动鲸鱼: {len(active_whales)}")
    
    if active_whales:
        print(f"\n   ⚡ 活跃信号:")
        for aw in active_whales:
            w = aw["whale"]
            print(f"      • {w['wallet'][:10]}... - {len(aw['changes'])} 个变动")
    
    # 保存报告
    report_file = STATE_DIR.parent / f"whale_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump({
            "scan_time": datetime.now().isoformat(),
            "whales_tracked": len(whales),
            "active_whales": len(active_whales),
            "whales": whales,
            "active_analyses": active_whales
        }, f, indent=2)
    print(f"\n报告已保存: {report_file}")


if __name__ == "__main__":
    main()
