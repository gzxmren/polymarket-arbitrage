#!/usr/bin/env python3
"""
PolyCop Signal 频道检查脚本

功能：
1. 获取 PolyCop Signal 最新消息
2. 提取 whale 地址和交易信号
3. 记录到观察列表文件
4. 输出简要报告

[规范] 不开发自动集成，仅观察记录
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

# Telegram 预览页面 URL
POLYCOP_SIGNAL_URL = "https://t.me/s/PolyCop_Signal"

# 观察列表文件
OBSERVE_DIR = Path(__file__).parent.parent.parent / "07-data" / "polycop_observe"
OBSERVE_FILE = OBSERVE_DIR / "polycop_addresses.json"
OBSERVE_DIR.mkdir(parents=True, exist_ok=True)

# HTML 解析正则
ADDRESS_PATTERN = r'polymarket\.com/profile/(0x[a-f0-9]{40})'
SMART_SCORE_PATTERN = r'Smart Score: <b>(\d+)</b>'
PNL_PATTERN = r'Backtest PnL: <b>[+]&#036;([\d.]+)</b>'
WIN_RATE_PATTERN = r'Win Rate: <b>([\d.]+)%</b>'


def fetch_polycop_messages() -> str:
    """获取 PolyCop Signal 频道消息"""
    try:
        req = Request(POLYCOP_SIGNAL_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except URLError as e:
        print(f"❌ 获取频道失败: {e}", file=sys.stderr)
        return ""


def extract_whale_data(html: str) -> list:
    """从 HTML 提取 whale 数据，同地址去重取最新"""
    seen = {}  # address -> whale data
    
    # 分割消息块
    message_blocks = html.split("tgme_widget_message_wrap")
    
    for block in message_blocks:
        # 提取地址
        address_match = re.search(ADDRESS_PATTERN, block)
        if not address_match:
            continue
        
        address = address_match.group(1)
        
        # 提取 Smart Score
        smart_score = 0
        score_match = re.search(SMART_SCORE_PATTERN, block)
        if score_match:
            smart_score = int(score_match.group(1))
        
        # 提取 PnL
        pnl = 0.0
        pnl_match = re.search(PNL_PATTERN, block)
        if pnl_match:
            pnl = float(pnl_match.group(1))
        
        # 提取胜率
        win_rate = 0.0
        rate_match = re.search(WIN_RATE_PATTERN, block)
        if rate_match:
            win_rate = float(rate_match.group(1))
        
        # 提取时间戳（如果有）
        time_match = re.search(r'<time datetime="([^"]+)"', block)
        timestamp = time_match.group(1) if time_match else datetime.now(timezone.utc).isoformat()
        
        # 同地址去重：只保留最新时间戳的数据
        if address not in seen or timestamp > seen[address]["timestamp"]:
            seen[address] = {
                "address": address,
                "smart_score": smart_score,
                "backtest_pnl": pnl,
                "win_rate": win_rate,
                "timestamp": timestamp,
                "source": "polycop_signal"
            }
    
    return list(seen.values())


def load_existing_addresses() -> dict:
    """加载已观察的地址"""
    if OBSERVE_FILE.exists():
        try:
            with open(OBSERVE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_addresses(data: dict):
    """保存地址观察列表"""
    try:
        with open(OBSERVE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        print(f"❌ 保存失败: {e}", file=sys.stderr)


def update_observe_list(new_whales: list) -> tuple:
    """更新观察列表，返回新增数量"""
    existing = load_existing_addresses()
    
    added = 0
    updated = 0
    
    for whale in new_whales:
        address = whale["address"]
        
        if address not in existing:
            # 新地址
            existing[address] = whale
            added += 1
        else:
            # 已存在，更新最新数据
            existing[address].update(whale)
            updated += 1
    
    save_addresses(existing)
    
    return added, updated, len(existing)


def cleanup_low_quality(existing: dict, min_score: int = 60, min_pnl: float = 500.0) -> int:
    """清理低质量观察地址，返回移除数量"""
    to_remove = []
    for addr, info in existing.items():
        score = info.get("smart_score", 0)
        pnl = info.get("backtest_pnl", 0)
        if score < min_score or (score < 80 and pnl < min_pnl):
            to_remove.append(addr)
    for addr in to_remove:
        del existing[addr]
    return len(to_remove)


def main():
    """主函数"""
    print("🔍 检查 PolyCop Signal 频道...")
    
    # 1. 获取消息
    html = fetch_polycop_messages()
    if not html:
        print("❌ 无数据，退出")
        sys.exit(1)
    
    # 2. 提取 whale 数据（已去重）
    whales = extract_whale_data(html)
    
    if not whales:
        print("⚠️ 未发现 whale 信号")
        sys.exit(0)
    
    # 3. 更新观察列表
    added, updated, total = update_observe_list(whales)
    
    # 4. 清理低质量地址
    existing = load_existing_addresses()
    removed = cleanup_low_quality(existing)
    if removed > 0:
        save_addresses(existing)
        total = len(existing)
    
    # 5. 输出报告
    print(f"\n📊 PolyCop Signal 观察")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"• 本次发现: {len(whales)} 个唯一地址")
    print(f"• 新增地址: {added} 个")
    print(f"• 更新地址: {updated} 个")
    if removed > 0:
        print(f"• 清理低质量: {removed} 个")
    print(f"• 观察总数: {total} 个")
    
    # 显示高质量地址（Smart Score ≥ 80）
    high_quality = [w for w in whales if w["smart_score"] >= 80]
    if high_quality:
        print(f"\n⭐ 高质量地址 (Score ≥ 80):")
        for w in high_quality[:5]:
            print(f"  • {w['address'][:12]}... Score={w['smart_score']} PnL=${w['backtest_pnl']:,.0f} WinRate={w['win_rate']}%")
    
    print(f"\n✅ 数据已保存: {OBSERVE_FILE}")
    print(f"⏰ 时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")


if __name__ == "__main__":
    main()