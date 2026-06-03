#!/usr/bin/env python3
"""
鲸鱼追踪器 V2
从最近交易识别大额交易者，追踪其活动

[修复] 2025-03-25: 
1. 禁用 profit-loss API 调用（返回404），改为从 positions 计算盈亏
2. 添加已发现鲸鱼列表持久化，避免丢失历史鲸鱼
"""

import json
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# 鲸鱼标准（修正门槛，过滤噪音）
WHALE_TRADE_THRESHOLD = 500       # 大单阈值 $500
WHALE_TOTAL_VOLUME = 1000         # 最低总交易量 $1,000
WHALE_MIN_TRADE_COUNT = 3         # 最少交易次数 3（过滤噪音）
TRACKING_WALLETS_LIMIT = 50       # 从 20 增加到 50（追踪更多）

# [新增 2026-04-17] 报告过滤配置（减少噪音）
WHALE_MIN_TOTAL_VALUE = 10000     # 只报告持仓价值 >$10,000 的鲸鱼
CHANGE_MIN_VALUE = 500             # 只报告变动金额 >$500 的变化
MARKET_CHANGE_WINDOW_HOURS = 4    # 同一市场 4 小时内多次变动只报一次

# 鲸鱼淘汰评分配置
INACTIVE_DAYS_THRESHOLD = 30      # 不活跃天数阈值
KEEP_SCORE_THRESHOLD = 3           # 保留分数阈值

# 状态目录
STATE_DIR = Path(__file__).parent.parent.parent / "07-data" / "whale_states"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# 已发现鲸鱼列表文件
WHALE_LIST_FILE = STATE_DIR / "discovered_whales.json"


def should_keep_whale(wallet: str, info: dict) -> bool:
    """判断是否保留鲸鱼（加权评分制）
    
    评分规则：
    - 活跃交易（volume>=1k AND trade_count>=3）→ +3
    - 大单次数（large_trades>=3）→ +2
    - DB历史交易量（historical_volume>=5k）→ +3
    - DB记录数（db_changes_count>=3）→ +1  
    - 30天无交易 → -2
    - 总分 < 3 → 淘汰
    """
    now = datetime.now(timezone.utc).timestamp()
    score = 0
    
    recent_vol = info.get('total_volume', 0)
    recent_tc = info.get('trade_count', 0)
    large_trades = info.get('large_trades', 0)
    hist_vol = info.get('historical_volume', 0)
    db_changes = info.get('db_changes_count', 0)
    last_trade = info.get('last_trade', 0)
    
    # 1. 活跃交易
    if recent_vol >= 1000 and recent_tc >= 3:
        score += 3
    
    # 2. 大单次数
    if isinstance(large_trades, (int, float)) and large_trades >= 3:
        score += 2
    
    # 3. DB历史交易量
    if isinstance(hist_vol, (int, float)) and hist_vol >= 5000:
        score += 3
    
    # 4. DB记录数
    if isinstance(db_changes, (int, float)) and db_changes >= 3:
        score += 1
    
    # 5. 不活跃惩罚（仅对历史不显著的鲸鱼，历史 volume >= $50k 的视为休眠真鲸鱼，不扣分）
    if last_trade > 0 and hist_vol < 50000:
        days_inactive = (now - last_trade) / 86400
        if days_inactive >= INACTIVE_DAYS_THRESHOLD:
            score -= 2
    
    return score >= KEEP_SCORE_THRESHOLD


def load_discovered_whales() -> dict:
    """
    加载已发现的鲸鱼列表
    
    [修复] 2025-03-25: 持久化鲸鱼列表，避免丢失历史鲸鱼
    """
    if WHALE_LIST_FILE.exists():
        try:
            with open(WHALE_LIST_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"   加载鲸鱼列表失败: {e}", file=sys.stderr)
    return {}


def save_discovered_whales(whales: dict):
    """
    保存已发现的鲸鱼列表
    
    [修复] 2025-03-25: 持久化鲸鱼列表，避免丢失历史鲸鱼
    """
    try:
        with open(WHALE_LIST_FILE, "w") as f:
            json.dump(whales, f, indent=2)
    except IOError as e:
        print(f"   保存鲸鱼列表失败: {e}", file=sys.stderr)


def ensure_db_record(wallet: str, pseudonym: str):
    """确保鲸鱼在 whales 表中有记录（INSERT OR IGNORE）
    
    当 API 发现新鲸鱼时调用，向 DB 写入一条基础记录，
    使鲸鱼拥有持久身份，不依赖 API 瞬时数据。
    """
    import sqlite3
    DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            INSERT OR IGNORE INTO whales (wallet, pseudonym, added_at, last_updated)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ''', (wallet.lower(), (pseudonym or wallet[:10] + '...')[:50]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"   \u26a0\ufe0f DB 回写失败 (wallet={wallet[:10]}...): {e}", file=sys.stderr)


def fetch_api(base_url: str, endpoint: str, max_retries: int = 2) -> dict | list | None:
    """获取API数据（带重试和超时处理）"""
    url = f"{base_url}{endpoint}"
    last_error = None

    for attempt in range(1 + max_retries):
        try:
            req = Request(url, headers={"User-Agent": "WhaleTracker/2.0"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt  # exponential backoff: 2s, 4s
                print(f"  ⚠️ fetch_api 重试 ({attempt+1}/{max_retries}): {url[:60]}... - {e}", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"  ❌ fetch_api 失败 (重试{max_retries}次): {url[:60]}... - {e}", file=sys.stderr)

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
        if (stats["total_volume"] >= WHALE_TOTAL_VOLUME and stats["trade_count"] >= WHALE_MIN_TRADE_COUNT) or stats["large_trades"] >= 3:
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
    """
    获取钱包盈亏数据
    
    [修复] 2025-03-25: profit-loss API 已废弃返回404，
    改为从 positions 数据计算盈亏，或返回空字典
    """
    # profit-loss 端点已废弃，返回404
    # 暂时禁用此API调用，避免错误日志刷屏
    # 盈亏数据可以从 positions 的 cashPnl 字段聚合计算
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
    """检测持仓变化（带过滤，减少噪音）"""
    changes = []
    current_dict = {}
    
    for pos in current_positions:
        market = pos.get("market", pos.get("title", "Unknown"))
        current_dict[market] = pos
    
    # 新持仓
    for market, pos in current_dict.items():
        # 提取 end_date（市场结算日期）
        end_date = pos.get("endDate", "")
        size = float(pos.get("size", 0))
        current_price = float(pos.get("curPrice", pos.get("currentPrice", 0)))
        value = size * current_price  # 计算持仓价值
        
        # 检查市场是否在历史持仓中（需要精确匹配）
        if market in previous_positions:
            try:
                prev_pos = previous_positions[market]
                # 仓位变化
                old_size = float(prev_pos.get("size", 0))
                new_size = size
                change_value = abs(new_size - old_size) * current_price  # 变动价值
                
                # [过滤] 只报告有价值的变化
                if abs(new_size - old_size) > 0.01 and change_value >= CHANGE_MIN_VALUE:
                    changes.append({
                        "type": "increased" if new_size > old_size else "decreased",
                        "market": market,
                        "outcome": pos.get("outcome", "?"),
                        "old_size": old_size,
                        "new_size": new_size,
                        "change": new_size - old_size,
                        "change_value": change_value,
                        "avg_price": float(pos.get("avgPrice", 0)),
                        "current_price": current_price,
                        "end_date": end_date,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
            except (KeyError, TypeError) as e:
                # 如果访问失败，视为新增
                if value >= CHANGE_MIN_VALUE:
                    changes.append({
                        "type": "new",
                        "market": market,
                        "outcome": pos.get("outcome", "?"),
                        "size": size,
                        "value": value,
                        "avg_price": float(pos.get("avgPrice", 0)),
                        "current_price": current_price,
                        "end_date": end_date
                    })
        elif value >= CHANGE_MIN_VALUE:
            # [过滤] 只报告有价值的新增仓位
            changes.append({
                "type": "new",
                "market": market,
                "outcome": pos.get("outcome", "?"),
                "size": size,
                "value": value,
                "avg_price": float(pos.get("avgPrice", 0)),
                "current_price": current_price,
                "end_date": end_date
            })
    
    # 清仓（只报告有价值仓位的清仓）
    for market, pos in previous_positions.items():
        if market not in current_dict:
            prev_size = float(pos.get("size", 0))
            prev_price = float(pos.get("curPrice", pos.get("currentPrice", 0)))
            prev_value = prev_size * prev_price
            
            # [过滤] 只报告有价值仓位的清仓
            if prev_value >= CHANGE_MIN_VALUE:
                changes.append({
                    "type": "closed",
                    "market": market,
                    "outcome": pos.get("outcome", "?"),
                    "previous_size": prev_size,
                    "previous_value": prev_value,
                    "end_date": pos.get("endDate", "")
                })
    
    return changes


def analyze_whale(wallet: str, whale_info: dict) -> dict:
    """分析单个鲸鱼（带错误隔离）"""
    try:
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

        # [新增 2026-04-17] 过滤低价值鲸鱼（持仓价值 < $10,000）
        is_low_value = total_value < WHALE_MIN_TOTAL_VALUE

        # 检测异常数据：持仓数多但总价值极低（可能是已清仓或数据异常）
        is_suspicious = False
        if len(positions) > 50 and total_value < 1000:
            is_suspicious = True
            print(f"   ⚠️  警告: {whale_info.get('pseudonym', wallet[:10])} 数据异常 "
                  f"({len(positions)}个持仓但总价值仅${total_value:.2f})，可能是已清仓")

        # 判断是否有活动（低价值鲸鱼不视为活跃）
        has_activity = len(changes) > 0 and not is_suspicious and not is_low_value

        # 保存新状态
        current_dict = {p.get("market", p.get("title", "Unknown")): p for p in positions}
        save_wallet_state(wallet, {
            "positions": current_dict,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "total_value": total_value,
            "total_pnl": total_pnl,
            "is_suspicious": is_suspicious
        })

        return {
            "wallet": wallet,
            "info": whale_info,
            "positions": positions,
            "position_count": len(positions),
            "total_value": total_value,
            "total_pnl": total_pnl,
            "changes": changes,
            "has_activity": has_activity,
            "is_suspicious": is_suspicious,
            "is_low_value": is_low_value  # 新增：低价值标记
        }
    except Exception as e:
        print(f"   ❌ 分析 {whale_info.get('pseudonym', wallet[:10])} 失败: {e}", file=sys.stderr)
        return {
            "wallet": wallet,
            "info": whale_info,
            "positions": [],
            "position_count": 0,
            "total_value": 0,
            "total_pnl": 0,
            "changes": [],
            "has_activity": False,
            "is_suspicious": False,
            "is_low_value": True,
            "error": str(e)
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
        # [改进] 按分类显示变动
        changes = analysis["changes"]
        new_changes = [c for c in changes if c["type"] == "new"]
        increased_changes = [c for c in changes if c["type"] == "increased"]
        decreased_changes = [c for c in changes if c["type"] == "decreased"]
        closed_changes = [c for c in changes if c["type"] == "closed"]
        
        lines.append(f"\n   ⚡ 变动汇总: +{len(new_changes)} 新增 / 📈{len(increased_changes)} 加仓 / 📉{len(decreased_changes)} 减仓 / -{len(closed_changes)} 清仓")
        
        # 新增仓位
        for change in new_changes[:3]:
            lines.append(f"      🆕 新建仓: {change['market'][:35]}...")
            lines.append(f"         {change['outcome']} | ${change.get('value', change['size']*change['current_price']):,.0f}")
        
        # 加仓
        for change in increased_changes[:3]:
            lines.append(f"      📈 加仓: {change['market'][:35]}...")
            lines.append(f"         {change['change']:+.2f} | ${change.get('change_value', 0):,.0f}")
        
        # 减仓
        for change in decreased_changes[:3]:
            lines.append(f"      📉 减仓: {change['market'][:35]}...")
            lines.append(f"         {change['change']:+.2f} | ${change.get('change_value', 0):,.0f}")
        
        # 清仓
        for change in closed_changes[:3]:
            lines.append(f"      ❌ 清仓: {change['market'][:35]}...")
            lines.append(f"         价值: ${change.get('previous_value', 0):,.0f}")
    
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


def get_top_whales(limit: int = 10) -> list:
    """
    获取 Top N 鲸鱼列表（按持仓价值排序）
    
    Returns:
        list: 鲸鱼分析数据列表，按持仓价值降序排列
    """
    print(f"\n📡 获取 Top {limit} 鲸鱼数据...")
    
    # 获取最近交易识别活跃鲸鱼
    trades = fetch_recent_trades(limit=1000)
    if not trades:
        print("❌ 无法获取交易数据")
        return []
    
    whales = identify_active_whales(trades)
    print(f"   发现 {len(whales)} 个活跃大户")
    
    # 分析每个鲸鱼
    analyses = []
    for wallet, info in whales.items():
        analysis = analyze_whale(wallet, info)
        # 排除异常数据
        if not analysis["is_suspicious"]:
            analyses.append(analysis)
    
    # 按持仓价值排序
    analyses.sort(key=lambda x: x["total_value"], reverse=True)
    
    return analyses[:limit]


def format_top_whales(analyses: list) -> str:
    """格式化 Top 鲸鱼列表为字符串"""
    lines = [
        f"\n{'='*70}",
        f"🏆 Top {len(analyses)} 鲸鱼排行榜 (按持仓价值)",
        f"{'='*70}",
        ""
    ]
    
    for i, analysis in enumerate(analyses, 1):
        w = analysis["info"]
        
        # 排名奖牌
        rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i:2d}.")
        
        # 盈亏状态
        pnl = analysis["total_pnl"]
        pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"
        
        # 活跃度
        activity = "🔥" if analysis["has_activity"] else "⚪"
        
        lines.append(
            f"{rank_emoji} {activity} {w['pseudonym'][:20]:<20} | "
            f"💰 ${analysis['total_value']:>12,.0f} | "
            f"📊 {analysis['position_count']:>3}个市场 | "
            f"{pnl_emoji} ${pnl:>+10,.0f}"
        )
    
    lines.append(f"\n{'='*70}")
    return "\n".join(lines)


def main():
    print("🐋 鲸鱼追踪器 V2")
    print(f"   大单阈值: ${WHALE_TRADE_THRESHOLD:,.0f}")
    print(f"   追踪数量: 前{TRACKING_WALLETS_LIMIT}个活跃大户")
    print("-" * 70)
    
    # 加载历史鲸鱼列表
    print("\n📂 加载历史鲸鱼列表...")
    historical_whales = load_discovered_whales()
    print(f"   历史鲸鱼: {len(historical_whales)} 个")
    
    # 获取最近交易
    print("\n📡 获取最近交易...")
    trades = fetch_recent_trades(limit=1000)
    print(f"   获取 {len(trades)} 笔交易")
    
    if not trades:
        print("\n❌ 无法获取交易数据")
        return
    
    # 识别鲸鱼
    print("\n🔍 识别活跃鲸鱼...")
    new_whales = identify_active_whales(trades)
    print(f"   本次发现 {len(new_whales)} 个活跃大户")
    
    # DB回写：为新发现的鲸鱼建立永久的身份记录
    for wallet, info in new_whales.items():
        if wallet not in historical_whales:
            ensure_db_record(wallet, info.get('pseudonym', ''))
            print(f"   \U0001f4dd DB回写新鲸鱼: {wallet[:10]}... ({info.get('pseudonym', '')[:20]})")

    # 合并历史鲸鱼和新发现的鲸鱼
    whales = historical_whales.copy()
    for wallet, info in new_whales.items():
        if wallet in whales:
            # 更新现有鲸鱼信息
            whales[wallet].update(info)
        else:
            whales[wallet] = info
    
    # P0-1: DB同步（在淘汰之前，为淘汰提供历史数据参考）
    db_sync_ok = False
    conn = None
    try:
        import sqlite3
        DB_PATH_SYNC = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'
        conn = sqlite3.connect(DB_PATH_SYNC)
        conn.row_factory = sqlite3.Row

        json_wallets = list(whales.keys())
        if json_wallets:
            # 分批查询，避免 SQL IN 超过 SQLite 变量上限
            BATCH_SIZE = 200
            all_db_rows = []
            for i in range(0, len(json_wallets), BATCH_SIZE):
                batch = json_wallets[i:i+BATCH_SIZE]
                placeholders = ','.join(['?'] * len(batch))
                rows = conn.execute(f'''
                    SELECT wallet, pseudonym, total_volume, changes_count, last_updated
                    FROM whales
                    WHERE LOWER(wallet) IN ({placeholders})
                ''', batch).fetchall()
                all_db_rows.extend(rows)

            for row in all_db_rows:
                wallet = row['wallet'].lower()
                if wallet in whales:
                    if row['pseudonym'] and row['pseudonym'] != 'unknown':
                        whales[wallet]['pseudonym'] = row['pseudonym']
                        if 'name' not in whales[wallet] or not whales[wallet]['name']:
                            whales[wallet]['name'] = row['pseudonym']
                    whales[wallet]['historical_volume'] = row['total_volume']
                    whales[wallet]['db_changes_count'] = row['changes_count']
            db_sync_ok = True
    except Exception as e:
        print(f"   ⚠️ DB 同步失败（不影响主流程）: {e}")
    finally:
        if conn:
            conn.close()

    # 淘汰机制：重新评估所有鲸鱼，清除噪音
    # 安全网：DB同步失败时跳过淘汰，避免缺少 historical_volume 导致误淘汰
    if db_sync_ok:
        before_eviction = len(whales)
        whales = {k: v for k, v in whales.items() if should_keep_whale(k, v)}
        evicted = before_eviction - len(whales)
        if evicted > 0:
            print(f"   🗑️  淘汰 {evicted} 个不符合条件的鲸鱼（剩余 {len(whales)} 个）")
        else:
            print(f"   ✅ 无需淘汰，当前 {len(whales)} 个鲸鱼均符合条件")
    else:
        print(f"   ⚠️  DB同步失败，跳过淘汰（避免误淘汰）")

    # 保存合并+淘汰后的鲸鱼列表
    save_discovered_whales(whales)
    print(f"   合并后共 {len(whales)} 个鲸鱼（历史+新发现-淘汰）")
    
    if not whales:
        print("\n❌ 未发现符合条件的鲸鱼")
        return
    
    # [优化] 按交易量排序，只分析最近活跃的 40 个鲸鱼，避免超时
    MAX_ANALYZE_WHALES = 40
    sorted_whales = sorted(whales.items(), key=lambda x: x[1].get('total_volume', 0), reverse=True)
    whales_to_analyze = dict(sorted_whales[:MAX_ANALYZE_WHALES])
    print(f"   [优化] 仅分析前 {MAX_ANALYZE_WHALES} 个活跃鲸鱼（共 {len(whales)} 个）")
    
    # 分析每个鲸鱼
    print(f"\n📊 分析鲸鱼持仓...")
    active_whales = []
    all_analyses = {}  # 缓存所有分析结果，避免重复 API 调用
    
    for i, (wallet, info) in enumerate(whales_to_analyze.items(), 1):
        print(f"   [{i}/{len(whales)}] 分析 {info['pseudonym']}...", end=" ", flush=True)
        try:
            analysis = analyze_whale(wallet, info)
            all_analyses[wallet] = analysis

            if analysis["has_activity"]:
                active_whales.append(analysis)
                print(f"有变动!")
            else:
                print(f"无变动")
        except Exception as e:
            print(f"❌ 失败: {e}")
            print(f"     钱包 {wallet[:10]}... 跳过（不影响其他钱包）", flush=True)

    # [新增] 轻量刷新：更新未覆盖的已发现鲸鱼的陈旧 JSON
    STALE_DAYS = 7
    MAX_STALE_REFRESH = 20
    now_dt = datetime.now(timezone.utc)

    # 找出未被主分析覆盖的鲸鱼
    unanalyzed = {k: v for k, v in whales.items() if k not in whales_to_analyze}

    stale_wallets = []
    for wallet in unanalyzed:
        state_file = STATE_DIR / f"{wallet}.json"
        if state_file.exists():
            mtime = datetime.fromtimestamp(state_file.stat().st_mtime, tz=timezone.utc)
            if (now_dt - mtime).days > STALE_DAYS:
                stale_wallets.append(wallet)
        # 没有 JSON 文件的也刷新
        elif wallet in whales:
            stale_wallets.append(wallet)

    if stale_wallets:
        stale_wallets = stale_wallets[:MAX_STALE_REFRESH]
        print(f"\n🔄 轻量刷新 {len(stale_wallets)} 个陈旧鲸鱼 JSON...")
        refreshed = 0
        for i, wallet in enumerate(stale_wallets, 1):
            try:
                positions = fetch_wallet_positions(wallet)
                current_dict = {p.get("market", p.get("title", "Unknown")): p for p in positions}
                total_value = sum(
                    float(p.get("size", 0)) * float(p.get("curPrice", p.get("currentPrice", 0)))
                    for p in positions
                )
                save_wallet_state(wallet, {
                    "positions": current_dict,
                    "last_check": datetime.now(timezone.utc).isoformat(),
                    "total_value": total_value,
                    "total_pnl": 0,
                    "is_suspicious": False
                })
                refreshed += 1
                print(f"   [{i}/{len(stale_wallets)}] {wallet[:10]}... 刷新完成", flush=True)
            except Exception as e:
                print(f"   [{i}/{len(stale_wallets)}] {wallet[:10]}... 失败: {e}", flush=True)
        print(f"   ✅ 刷新完成: {refreshed}/{len(stale_wallets)}")

    # 显示结果
    print(f"\n{'='*70}")
    print("📈 鲸鱼分析报告")
    print(f"{'='*70}")
    
    # 显示有变动的鲸鱼
    if active_whales:
        print(f"\n⚡ 有活动鲸鱼 ({len(active_whales)} 个):")
        for analysis in active_whales:
            print(format_whale(analysis))
    
    # 显示所有追踪的鲸鱼摘要（使用缓存结果，不重复调用 API）
    print(f"\n{'='*70}")
    print("📋 追踪列表摘要:")
    print(f"{'='*70}")
    for wallet, info in whales.items():
        analysis = all_analyses.get(wallet)
        if not analysis:
            continue
        status = "🟢 有变动" if analysis["has_activity"] else "⚪ 无变动"
        # 兼容历史数据格式：优先使用 total_volume，不存在则用 0
        total_volume = info.get('total_volume', 0)
        print(f"   {status} {info['pseudonym'][:20]:<20} | 24h量: ${total_volume:>10,.0f} | 持仓: {analysis['position_count']} 个")
    
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
