#!/usr/bin/env python3
"""
Polymarket 轻量监控 v1.0
单文件、30秒硬约束、事件驱动通知

功能：
1. Pair Cost 套利扫描 — YES+NO < $0.95 的真机会
2. 聪明钱持仓追踪 — PolyCop 高分地址的大额持仓变化
3. 事件驱动通知 — 有机会才推送，没机会就沉默

用法：
  python3 polymarket_monitor_lite.py          # 正常运行
  python3 polymarket_monitor_lite.py --test   # 测试模式（不发通知）
  python3 polymarket_monitor_lite.py --dry    # 干跑（只打印不发送）
  python3 polymarket_monitor_lite.py --force  # 强制发送（即使没机会也发汇总）

Created: 2026-05-02
"""

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ============================================================
# 进程锁 — 防止 cron lane 拥堵导致重复执行
# ============================================================

LOCK_FILE = Path(__file__).parent / ".monitor_lite.lock"

def acquire_lock():
    """获取进程锁，已存在则退出"""
    if LOCK_FILE.exists():
        try:
            with open(LOCK_FILE) as f:
                data = json.load(f)
            age = time.time() - data.get("ts", 0)
            # 超过 SCRIPT_TIMEOUT 的锁视为过期（进程可能被强制杀死）
            if age < SCRIPT_TIMEOUT:
                print(f"⏭️ 进程锁存在 (pid={data.get('pid')}, {age:.0f}s 前创建), 跳过")
                sys.exit(0)
            else:
                print(f"⚠️ 锁过期 ({age:.0f}s > {SCRIPT_TIMEOUT}s), 覆盖")
        except Exception:
            pass
    LOCK_FILE.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}))

def release_lock():
    """释放进程锁"""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass

# ============================================================
# 配置
# ============================================================

# 超时硬约束
SCRIPT_TIMEOUT = 40  # 比 cron 的 60s 短 20s，给脚本多些余量

# API
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

# Pair Cost 阈值
PAIR_COST_THRESHOLD = 0.98   # YES+NO < $0.98 才算机会（2%+ 利润）
MIN_LIQUIDITY = 5000         # 最低流动性 $5000（小池子滑点大，不值得）
SCAN_LIMIT = 100             # 全量扫描上限（从200降到100，减少API调用）
QUICK_SCAN_LIMIT = 50        # 快速扫描数量，发现机会后再全量扫描

# 聪明钱追踪
SMART_SCORE_MIN = 80         # PolyCop 评分 >= 80 才跟踪
TOP_WHALES_LIMIT = 15        # 跟踪 top 15 聪明钱（按 PnL 排序，扩大覆盖）
POSITION_MIN_VALUE = 1000    # 持仓当前价值 >= $1000 才关注
POSITION_CHANGE_MIN = 500    # 持仓变化 >= $500 才报告

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "<REDACTED_TELEGRAM_TOKEN>")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5052636342")

# 数据路径
BASE_DIR = Path(__file__).parent.parent.parent  # polymarket-project/
DATA_DIR = BASE_DIR / "07-data"
POLYCOP_FILE = DATA_DIR / "polycop_observe" / "polycop_addresses.json"
STATE_FILE = DATA_DIR / "monitor_lite_state.json"

# ============================================================
# 超时控制
# ============================================================

class ScriptTimeout(Exception):
    pass

def timeout_handler(signum, frame):
    raise ScriptTimeout(f"脚本超时 ({SCRIPT_TIMEOUT}s)")

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm(SCRIPT_TIMEOUT)

# ============================================================
# 工具函数
# ============================================================

def fetch_json(url: str, timeout: int = 8) -> dict | list | None:
    """HTTP GET，返回 JSON 或 None"""
    try:
        req = Request(url, headers={"User-Agent": "PolymonLite/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ⚠️ fetch failed: {url[:80]}... → {e}", file=sys.stderr)
        return None


def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """发送 Telegram 消息，HTML 失败自动降级纯文本"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️ Telegram 未配置", file=sys.stderr)
        return False
    
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (已截断)"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    for mode in [parse_mode, ""]:
        try:
            payload = json.dumps({
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": mode,
                "disable_web_page_preview": True
            }).encode()
            req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
                if result.get("ok"):
                    return True
                desc = result.get("description", "")
                if mode and "parse" in desc.lower():
                    continue
                print(f"  ⚠️ Telegram API: {desc}", file=sys.stderr)
                return False
        except Exception as e:
            if mode:
                continue
            print(f"  ⚠️ Telegram send failed: {e}", file=sys.stderr)
            return False
    return False


def load_state() -> dict:
    """加载上次运行状态"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_positions": {}, "last_run": None, "sent_pc_ids": {}, "sent_whale_keys": {}}


def save_state(state: dict):
    """保存运行状态"""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        print(f"  ⚠️ save state failed: {e}", file=sys.stderr)


def is_trading_hours() -> bool:
    """判断当前是否为美东交易时段 (ET 8:00-22:00)"""
    utc_now = datetime.now(timezone.utc)
    # EDT (5-10月): UTC-4, EST (11-4月): UTC-5
    month = utc_now.month
    utc_offset = -4 if 5 <= month <= 10 else -5  # 简化 DST 判断
    et_hour = (utc_now.hour + utc_offset) % 24
    return 8 <= et_hour < 22

# ============================================================
# 1. Pair Cost 套利扫描
# ============================================================

def scan_pair_cost() -> list:
    """两阶段扫描：先快速扫描，发现机会后再全量扫描"""
    opportunities = []
    total_scanned = 0

    # ---- Phase 1: 快速扫描 (QUICK_SCAN_LIMIT) ----
    print(f"🔍 Phase 1: 快速扫描 ({QUICK_SCAN_LIMIT} 个市场)...")
    markets = fetch_json(
        f"{GAMMA_API}/markets?active=true&closed=false&limit={QUICK_SCAN_LIMIT}&offset=0"
    )
    if not markets:
        print("  快速扫描: API 无返回")
        return []

    for m in markets:
        total_scanned += 1
        prices = m.get("outcomePrices", [])
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except Exception:
                continue
        if len(prices) < 2:
            continue
        try:
            yes_price = float(prices[0])
            no_price = float(prices[1])
        except (ValueError, TypeError):
            continue
        pair_cost = yes_price + no_price
        liquidity = float(m.get("liquidity", 0) or 0)
        volume = float(m.get("volume", 0) or 0)
        if pair_cost < PAIR_COST_THRESHOLD and liquidity >= MIN_LIQUIDITY:
            profit_pct = (1.0 - pair_cost) * 100
            opportunities.append({
                "id": m.get("id", ""),
                "question": m.get("question", "Unknown"),
                "slug": m.get("slug", ""),
                "yes": yes_price,
                "no": no_price,
                "pair_cost": pair_cost,
                "profit_pct": profit_pct,
                "liquidity": liquidity,
                "volume": volume,
            })

    print(f"  快速扫描: {total_scanned} 市场, 发现 {len(opportunities)} 个机会 (阈值 < ${PAIR_COST_THRESHOLD})")

    # ---- Phase 2: 扩展扫描 (仅快速扫描有优质机会时) ----
    # 只有最优利润 ≥ 3% 才扩展扫，否则扫出来也一般
    best_profit = opportunities[0]["profit_pct"] if opportunities else 0
    if opportunities and best_profit >= 3.0 and QUICK_SCAN_LIMIT < SCAN_LIMIT:
        print(f"🔍 Phase 2: 扩展扫描 (最优利润 {best_profit:.1f}% ≥ 3%，扩展到 {SCAN_LIMIT} 个市场)...")
        offset = QUICK_SCAN_LIMIT
        batch_size = 50
        while total_scanned < SCAN_LIMIT:
            more_markets = fetch_json(
                f"{GAMMA_API}/markets?active=true&closed=false&limit={batch_size}&offset={offset}"
            )
            if not more_markets:
                break
            for m in more_markets:
                total_scanned += 1
                prices = m.get("outcomePrices", [])
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except Exception:
                        continue
                if len(prices) < 2:
                    continue
                try:
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                except (ValueError, TypeError):
                    continue
                pair_cost = yes_price + no_price
                liquidity = float(m.get("liquidity", 0) or 0)
                volume = float(m.get("volume", 0) or 0)
                if pair_cost < PAIR_COST_THRESHOLD and liquidity >= MIN_LIQUIDITY:
                    profit_pct = (1.0 - pair_cost) * 100
                    opportunities.append({
                        "id": m.get("id", ""),
                        "question": m.get("question", "Unknown"),
                        "slug": m.get("slug", ""),
                        "yes": yes_price,
                        "no": no_price,
                        "pair_cost": pair_cost,
                        "profit_pct": profit_pct,
                        "liquidity": liquidity,
                        "volume": volume,
                    })
            if len(more_markets) < batch_size:
                break
            offset += batch_size
        print(f"  扩展扫描: 共 {total_scanned} 市场, 发现 {len(opportunities)} 个机会")
    else:
        print(f"  快速扫描无优质机会 (最佳利润{best_profit:.1f}%), 跳过扩展扫描 ✅")

    # 按利润率排序
    opportunities.sort(key=lambda x: x["pair_cost"])
    return opportunities

# ============================================================
# 2. 聪明钱持仓追踪
# ============================================================

def load_smart_wallets() -> list:
    """加载 PolyCop 高分地址，按 PnL 排序取 top N"""
    if not POLYCOP_FILE.exists():
        print("  ⚠️ PolyCop 数据文件不存在", file=sys.stderr)
        return []
    
    try:
        with open(POLYCOP_FILE) as f:
            data = json.load(f)
    except Exception as e:
        print(f"  ⚠️ 加载 PolyCop 数据失败: {e}", file=sys.stderr)
        return []
    
    wallets = [
        {
            "address": v["address"],
            "score": v.get("smart_score", 0),
            "pnl": v.get("backtest_pnl", 0),
            "win_rate": v.get("win_rate", 0),
        }
        for v in data.values()
        if v.get("smart_score", 0) >= SMART_SCORE_MIN
    ]
    # 按 PnL 降序 — 真赚到钱的才是真聪明钱
    wallets.sort(key=lambda x: x["pnl"], reverse=True)
    
    result = wallets[:TOP_WHALES_LIMIT]
    print(f"  PolyCop 高分地址: {len(wallets)} 个 (≥{SMART_SCORE_MIN}分), 跟踪 top {len(result)} (按PnL)")
    return result


def scan_smart_positions(wallets: list, prev_positions: dict) -> tuple[list, dict]:
    """
    扫描聪明钱持仓，对比上次发现变化。
    
    返回: (signals, current_positions_snapshot)
    """
    print("🐋 扫描聪明钱持仓...")
    
    if not wallets:
        return [], {}
    
    signals = []
    current_snapshot = {}
    
    for w in wallets:
        addr = w["address"]
        short_addr = addr[:8] + "..." + addr[-4:]
        
        # 拉取持仓
        positions = fetch_json(f"{DATA_API}/positions?user={addr}&limit=50")
        if positions is None:
            continue
        
        # 过滤有意义的持仓
        big_positions = []
        for p in positions:
            cur_val = float(p.get("currentValue", 0) or 0)
            if cur_val >= POSITION_MIN_VALUE:
                key = p.get("conditionId", p.get("asset", ""))
                big_positions.append({
                    "key": key,
                    "title": p.get("title", "Unknown"),
                    "outcome": p.get("outcome", "?"),
                    "size": float(p.get("size", 0) or 0),
                    "avg_price": float(p.get("avgPrice", 0) or 0),
                    "cur_value": cur_val,
                    "pnl": float(p.get("cashPnl", 0) or 0),
                    "pnl_pct": float(p.get("percentPnl", 0) or 0),
                    "cur_price": float(p.get("curPrice", 0) or 0),
                    "slug": p.get("eventSlug", p.get("slug", "")),
                })
        
        # 存当前快照
        current_snapshot[addr] = {pos["key"]: pos for pos in big_positions}
        
        # 对比上次 — 找新增/真正买卖的持仓变化
        # 关键：对比 size（持仓数量）而不是 cur_value（会随价格波动）
        prev = prev_positions.get(addr, {})
        
        for pos in big_positions:
            key = pos["key"]
            if key in prev:
                # 已有持仓 — 检查数量变化（真正的买入/卖出）
                prev_size = prev[key].get("size", 0)
                size_delta = pos["size"] - prev_size
                size_change_pct = abs(size_delta) / prev_size * 100 if prev_size > 0 else 100
                
                # 只报告数量变化 >= 5% 且价值变化 >= $500 的（过滤噪音）
                value_delta = size_delta * pos.get("cur_price", pos.get("avg_price", 0))
                if size_change_pct >= 5 and abs(value_delta) >= POSITION_CHANGE_MIN:
                    action = "buy_more" if size_delta > 0 else "sell"
                    signals.append({
                        "type": action,
                        "wallet": short_addr,
                        "score": w["score"],
                        "wallet_pnl": w["pnl"],
                        "delta": value_delta,
                        "size_delta": size_delta,
                        "size_change_pct": size_change_pct,
                        **pos,
                    })
            else:
                # 新持仓（真正的新建仓）
                if pos["cur_value"] >= POSITION_MIN_VALUE:
                    signals.append({
                        "type": "new",
                        "wallet": short_addr,
                        "score": w["score"],
                        "wallet_pnl": w["pnl"],
                        "delta": pos["cur_value"],
                        "size_delta": pos["size"],
                        "size_change_pct": 100,
                        **pos,
                    })
        
        # 检查消失的持仓（真正清仓）
        for key, prev_pos in prev.items():
            if key not in current_snapshot[addr] and prev_pos.get("cur_value", 0) >= POSITION_MIN_VALUE:
                signals.append({
                    "type": "exit",
                    "wallet": short_addr,
                    "score": w["score"],
                    "wallet_pnl": w["pnl"],
                    "delta": -prev_pos["cur_value"],
                    "size_delta": -prev_pos.get("size", 0),
                    "size_change_pct": 100,
                    **prev_pos,
                })
    
    # 按变化金额排序
    signals.sort(key=lambda x: abs(x.get("delta", 0)), reverse=True)
    
    print(f"  扫描 {len(wallets)} 位聪明钱, 发现 {len(signals)} 条持仓变化 (≥${POSITION_CHANGE_MIN})")
    return signals[:15], current_snapshot  # 最多报 15 条

# ============================================================
# 2.5 聪明钱热点聚合
# ============================================================

def aggregate_smart_positions(current_snapshot: dict) -> list:
    """
    聚合所有跟踪鲸鱼的持仓，按市场维度展示热点。
    用实际 outcome 标签（如 Yes/No 或队名），不硬编码 YES/NO。
    返回按总押注金额排序的 top 10 市场。
    """
    markets = {}
    
    for addr, positions in current_snapshot.items():
        for key, pos in positions.items():
            outcome = pos.get("outcome", "?")  # 实际标签，如 "Yes" / "No" / "Liquid"
            title = pos.get("title", "Unknown")
            
            if key not in markets:
                markets[key] = {
                    "title": title,
                    "slug": pos.get("slug", ""),
                    "outcomes": {},  # outcome_label → dict(whales=set, total=0, avg_prices=[])
                }
            
            m = markets[key]
            if outcome not in m["outcomes"]:
                m["outcomes"][outcome] = {"whales": set(), "total": 0.0, "avg_prices": []}
            
            m["outcomes"][outcome]["whales"].add(addr)
            m["outcomes"][outcome]["total"] += pos.get("cur_value", 0)
            m["outcomes"][outcome]["avg_prices"].append(pos.get("avg_price", 0))
    
    # 格式化输出
    result = []
    for key, m in markets.items():
        sides = []
        total = 0.0
        for label, data in m["outcomes"].items():
            avg = sum(data["avg_prices"]) / len(data["avg_prices"]) if data["avg_prices"] else 0
            total += data["total"]
            sides.append({
                "label": label,
                "whales": len(data["whales"]),
                "value": data["total"],
                "avg_price": avg,
            })
        sides.sort(key=lambda x: x["value"], reverse=True)
        
        result.append({
            "title": m["title"],
            "slug": m["slug"],
            "total": total,
            "sides": sides,
        })
    
    result.sort(key=lambda x: x["total"], reverse=True)
    if result:
        top_range = f"${result[0]['total']:,.0f}~${result[min(4, len(result)-1)]['total']:,.0f}"
        print(f"  聚合热点: 共 {len(result)} 个市场, top 5 总押注 {top_range}")
    else:
        print("  聚合热点: 无数据")
    return result[:10]


# ============================================================
# 3. 通知格式化
# ============================================================

def format_pair_cost_alert(opps: list) -> str:
    """格式化 Pair Cost 机会"""
    lines = ["🎯 <b>Pair Cost 套利机会</b>\n"]
    
    for i, o in enumerate(opps[:5], 1):
        lines.append(
            f"{i}. <b>{o['question'][:60]}</b>\n"
            f"   YES: ${o['yes']:.4f} + NO: ${o['no']:.4f} = <b>${o['pair_cost']:.4f}</b>\n"
            f"   💰 利润: {o['profit_pct']:.1f}% | 流动性: ${o['liquidity']:,.0f}\n"
            f"   🔗 polymarket.com/event/{o['slug']}\n"
        )
    
    if len(opps) > 5:
        lines.append(f"\n... 还有 {len(opps)-5} 个机会")
    
    return "\n".join(lines)


def format_position_signals(signals: list) -> str:
    """格式化聪明钱持仓变化"""
    lines = ["🐋 <b>聪明钱操作</b>\n"]
    
    for i, s in enumerate(signals[:8], 1):
        type_map = {
            "new": "🆕 新建仓",
            "buy_more": "🟢 加仓",
            "sell": "🔴 减仓",
            "exit": "🚪 清仓",
        }
        type_label = type_map.get(s["type"], s["type"])
        
        delta_str = f"+${s['delta']:,.0f}" if s["delta"] > 0 else f"-${abs(s['delta']):,.0f}"
        pnl_emoji = "🟢" if s.get("pnl", 0) > 0 else "🔴"
        size_pct = s.get('size_change_pct', 0)
        
        lines.append(
            f"{i}. {type_label} | {s['wallet']} (Score:{s['score']})\n"
            f"   {s.get('title', '?')[:50]}\n"
            f"   方向: {s.get('outcome', '?')} | 交易额: <b>{delta_str}</b> (仓位变动{size_pct:.0f}%)\n"
            f"   {pnl_emoji} 持仓PnL: ${s.get('pnl', 0):,.0f} ({s.get('pnl_pct', 0):.0f}%)\n"
        )
    
    if len(signals) > 8:
        lines.append(f"\n... 还有 {len(signals)-8} 条操作")
    
    return "\n".join(lines)


def format_combined_alert(opps: list, signals: list, aggregated: list = None) -> str:
    """合并通知"""
    parts = ["📊 <b>Polymarket 监控 — 发现机会</b>\n"]
    parts.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    if opps:
        parts.append(format_pair_cost_alert(opps))
    
    if signals:
        if opps:
            parts.append("\n" + "─" * 30 + "\n")
        parts.append(format_position_signals(signals))
    
    # 聪明钱热点聚合（仅当有信号时追加）
    if signals and aggregated:
        parts.append("\n" + "─" * 30 + "\n")
        parts.append(format_smart_hotspots(aggregated))
    
    return "\n".join(parts)


def format_smart_hotspots(aggregated: list) -> str:
    """格式化聪明钱热点聚合"""
    lines = ["🔥 <b>聪明钱热点</b>\n"]
    
    for i, m in enumerate(aggregated[:5], 1):
        side_strs = []
        for s in m["sides"]:
            side_strs.append(f"{s['label']}: {s['whales']}鲸 ${s['value']:,.0f}")
        sides = " | ".join(side_strs)
        lines.append(
            f"{i}. <b>{m['title'][:50]}</b>\n"
            f"   {sides}\n"
            f"   💰 总押注: ${m['total']:,.0f}\n"
        )
    
    if len(aggregated) > 5:
        lines.append(f"... 还有 {len(aggregated)-5} 个市场")
    
    return "\n".join(lines)

# ============================================================
# 主程序
# ============================================================

def main():
    start_time = time.time()
    
    # 解析参数
    args = set(sys.argv[1:])
    test_mode = "--test" in args
    dry_run = "--dry" in args
    force_send = "--force" in args
    
    print(f"{'='*50}")
    print(f"Polymarket Monitor Lite v1.1")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"模式: {'TEST' if test_mode else 'DRY' if dry_run else 'LIVE'}")
    
    # 交易时段感知：非交易时段只在整点运行
    trading = is_trading_hours()
    now_min = datetime.now().minute
    print(f"交易时段: {'✅ 是' if trading else '⚪ 否'} (美东 8:00-22:00)")
    
    if not trading and not test_mode and not force_send and now_min != 0:
        print(f"⏭️ 非交易时段，跳过本次 (minute={now_min}, 仅整点运行)")
        print(f"{'='*50}")
        return
    
    print(f"{'='*50}\n")
    
    # 加载上次状态
    state = load_state()
    # 兼容旧版 list 格式
    raw_pc = state.get("sent_pc_ids", {})
    raw_whale = state.get("sent_whale_keys", {})
    prev_sent_pc = set(raw_pc) if isinstance(raw_pc, dict) else set(raw_pc)
    prev_sent_whale = set(raw_whale) if isinstance(raw_whale, dict) else set(raw_whale)
    prev_positions = state.get("last_positions", {})
    
    # ---- Step 1: Pair Cost 扫描 ----
    try:
        pair_cost_opps = scan_pair_cost()
    except ScriptTimeout:
        raise
    except Exception as e:
        print(f"❌ Pair Cost 扫描异常: {e}", file=sys.stderr)
        pair_cost_opps = []
    
    # ---- Step 2: 聪明钱持仓扫描 ----
    try:
        smart_wallets = load_smart_wallets()
        position_signals, current_positions = scan_smart_positions(smart_wallets, prev_positions)
    except ScriptTimeout:
        raise
    except Exception as e:
        print(f"❌ 聪明钱扫描异常: {e}", file=sys.stderr)
        position_signals = []
        current_positions = prev_positions  # 保留上次数据
    
    # ---- Step 3: 去重 ----
    new_pc_opps = [o for o in pair_cost_opps if o["id"] not in prev_sent_pc]
    # 聪明钱信号去重：用 wallet+key 组合作为唯一标识
    for s in position_signals:
        # 去重 key：只用 wallet+key，去掉 type，防止同一持仓反复报（类型从new→exit→buy_more会变）
        s["_dedup_key"] = f"{s['wallet']}|{s['key']}"
    new_position_signals = [s for s in position_signals if s["_dedup_key"] not in prev_sent_whale]
    # 如果全部重复，则不推送
    if not new_position_signals:
        position_signals = []
    
    has_news = len(new_pc_opps) > 0 or len(new_position_signals) > 0
    
    # ---- Step 4: 输出结果 ----
    elapsed = time.time() - start_time
    print(f"\n{'='*50}")
    print(f"📊 扫描结果:")
    print(f"   Pair Cost 机会: {len(pair_cost_opps)} 个 (新: {len(new_pc_opps)})")
    print(f"   聪明钱持仓变化: {len(position_signals)} 条")
    print(f"   耗时: {elapsed:.1f}s")
    print(f"   有新消息: {'✅' if has_news else '⚪ 无'}")
    
    if test_mode:
        print("\n🧪 测试模式，不发送通知")
        if pair_cost_opps:
            print("\n--- Pair Cost 机会 ---")
            for o in pair_cost_opps[:3]:
                print(f"  {o['question'][:50]}: ${o['pair_cost']:.4f} (利润 {o['profit_pct']:.1f}%)")
        if position_signals:
            print("\n--- 聪明钱持仓变化 ---")
            for s in position_signals[:5]:
                delta = f"+${s['delta']:,.0f}" if s['delta'] > 0 else f"-${abs(s['delta']):,.0f}"
                print(f"  [{s['type']}] {s['wallet']} Score:{s['score']} | {delta} | {s.get('title','')[:40]}")
    
    elif has_news or force_send:
        # 聪明钱热点聚合：有新信号才聚合
        if len(new_position_signals) > 0 and current_positions:
            aggregated = aggregate_smart_positions(current_positions)
        else:
            aggregated = []
        
        if has_news:
            msg = format_combined_alert(new_pc_opps or pair_cost_opps, new_position_signals, aggregated)
        else:
            msg = (
                f"📊 <b>Polymarket 监控</b>\n\n"
                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"⚪ 市场平稳，无新机会\n"
                f"扫描市场: ~{SCAN_LIMIT} | 聪明钱: {len(smart_wallets)} 位"
            )
        
        if dry_run:
            print(f"\n📨 DRY RUN — 消息内容:\n{msg}")
        else:
            ok = send_telegram(msg)
            print(f"\n📨 通知{'✅ 已发送' if ok else '❌ 发送失败'}")
    else:
        print("\n⚪ 无新消息，不发送通知（事件驱动）")
    
    # ---- Step 5: 保存状态 ----
    # 去重：只保存真正新信号的 dedup_key
    now_ts = time.time()
    raw_pc = state.get("sent_pc_ids", {})
    raw_whale = state.get("sent_whale_keys", {})
    # 兼容旧版 list 格式
    merged_pc = raw_pc if isinstance(raw_pc, dict) else {k: now_ts for k in raw_pc}
    merged_whale = raw_whale if isinstance(raw_whale, dict) else {k: now_ts for k in raw_whale}
    # 只追加真正新的信号
    for o in new_pc_opps:
        merged_pc[o["id"]] = now_ts
    for s in new_position_signals:
        merged_whale[s["_dedup_key"]] = now_ts

    new_state = {
        "last_run": datetime.now().isoformat(),
        "last_elapsed_s": round(elapsed, 1),
        "pair_cost_count": len(pair_cost_opps),
        "position_signals_count": len(position_signals),
        "sent_pc_ids": merged_pc,
        "sent_whale_keys": merged_whale,
        "last_positions": current_positions,
        "notified": has_news or force_send,
    }
    save_state(new_state)
    
    print(f"\n✅ 完成 ({elapsed:.1f}s)")
    print(f"{'='*50}")


if __name__ == "__main__":
    acquire_lock()
    try:
        main()
    except ScriptTimeout as e:
        print(f"\n⏰ {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 未预期错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        release_lock()
