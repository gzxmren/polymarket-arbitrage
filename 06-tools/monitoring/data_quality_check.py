#!/usr/bin/env python3
"""Polymarket 数据质量监控脚本 - 每日检查关键字段质量"""

import sqlite3
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

DB = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))
WHALE_DIR = str(Path(__file__).resolve().parents[2] / "07-data" / "whale_states")
REPORT_DIR = str(Path(__file__).resolve().parents[2] / "07-data" / "quality_reports")

os.makedirs(REPORT_DIR, exist_ok=True)

# ========== 伪名生成（从 update_whale_names.py 复制） ==========
ADJECTIVES = [
    "Swift", "Silent", "Mighty", "Clever", "Bold", "Wise", "Fierce", "Calm",
    "Rapid", "Stealthy", "Valiant", "Cunning", "Gentle", "Fierce", "Sly",
    "Brave", "Quick", "Sharp", "Smooth", "Wild", "Tame", "Grand", "Noble",
    "Lucky", "Keen", "Bright", "Dark", "Light", "Heavy", "Cool", "Warm",
    "Fresh", "Ancient", "Modern", "Future", "Cosmic", "Solar", "Lunar",
    "Crystal", "Golden", "Silver", "Bronze", "Iron", "Steel", "Diamond",
    "Ruby", "Emerald", "Sapphire", "Amber", "Obsidian", "Marble", "Ivory"
]

NOUNS = [
    "Whale", "Shark", "Dolphin", "Orca", "Narwhal", "Beluga", "Manatee",
    "Kraken", "Leviathan", "Dragon", "Phoenix", "Griffin", "Unicorn",
    "Tiger", "Lion", "Eagle", "Falcon", "Hawk", "Raven", "Wolf", "Bear",
    "Fox", "Otter", "Seal", "Walrus", "Penguin", "Turtle", "Stingray",
    "Jellyfish", "Octopus", "Squid", "Cuttlefish", "Nautilus", "Clam",
    "Oyster", "Pearl", "Coral", "Reef", "Wave", "Tide", "Current",
    "Tsunami", "Abyss", "Depth", "Mariana", "Atlantis", "Neptune", "Poseidon"
]

COLORS = [
    "Azure", "Crimson", "Emerald", "Golden", "Indigo", "Jade", "Lavender",
    "Maroon", "Navy", "Olive", "Purple", "Ruby", "Scarlet", "Teal",
    "Violet", "Amber", "Coral", "Ebony", "Ivory", "Jet", "Khaki",
    "Lilac", "Magenta", "Onyx", "Pearl", "Quartz", "Rose", "Slate",
    "Topaz", "Umber", "Vanilla", "Wine", "Xanthic", "Yellow", "Zaffre"
]


def generate_pseudonym(wallet: str, existing_names: set) -> str:
    """
    基于钱包地址生成唯一的伪名
    
    策略:
    1. 使用地址的一部分作为随机种子，确保同一地址总是生成相同名称
    2. 检查是否已存在，如果存在则添加数字后缀
    """
    # 使用地址前10位作为种子
    seed = int(wallet[2:10], 16)
    rng = random.Random(seed)
    
    # 尝试生成唯一名称
    max_attempts = 100
    for _ in range(max_attempts):
        # 随机组合策略
        pattern = rng.choice([
            lambda: f"{rng.choice(ADJECTIVES)}-{rng.choice(NOUNS)}",
            lambda: f"{rng.choice(COLORS)}-{rng.choice(NOUNS)}",
            lambda: f"{rng.choice(ADJECTIVES)}-{rng.choice(COLORS)}-{rng.choice(NOUNS)}",
            lambda: f"{rng.choice(NOUNS)}-{rng.choice(ADJECTIVES)}"
        ])
        
        name = pattern()
        
        # 确保唯一性
        if name not in existing_names:
            existing_names.add(name)
            return name
    
    # 如果无法生成唯一名称，添加数字后缀
    base_name = f"{rng.choice(ADJECTIVES)}-{rng.choice(NOUNS)}"
    counter = 1
    while f"{base_name}-{counter}" in existing_names:
        counter += 1
    
    final_name = f"{base_name}-{counter}"
    existing_names.add(final_name)
    return final_name


def check_db():
    db = sqlite3.connect(DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    cursor = db.cursor()
    
    issues = []
    stats = {}
    
    # === CHANGES 表 ===
    cursor.execute("SELECT COUNT(*) FROM changes")
    total = cursor.fetchone()[0]
    stats['changes_total'] = total
    
    if total > 0:
        # 空值检查
        cursor.execute("SELECT COUNT(*) FROM changes WHERE market IS NULL OR market = ''")
        bad_market = cursor.fetchone()[0]
        if bad_market > 0:
            issues.append(f"🔴 changes: {bad_market} 条 market 为空")
        
        cursor.execute("SELECT COUNT(*) FROM changes WHERE change_amount IS NULL OR change_amount = 0")
        bad_amount = cursor.fetchone()[0]
        if bad_amount > total * 0.05:  # >5%
            issues.append(f"🟡 changes: {bad_amount} 条 change_amount 为0 ({bad_amount/total*100:.1f}%)")
        
        # 时效性 - 最新数据不应超过2小时
        cursor.execute("SELECT MAX(timestamp) FROM changes")
        latest = cursor.fetchone()[0]
        stats['changes_latest'] = latest
        
        # 可读标题覆盖率
        cursor.execute("SELECT COUNT(*) FROM changes WHERE market_title != '' AND market_title IS NOT NULL")
        has_title = cursor.fetchone()[0]
        title_rate = has_title / total * 100
        stats['changes_title_rate'] = f"{title_rate:.1f}%"
        if title_rate < 50:
            issues.append(f"🟡 changes: 可读标题覆盖率仅 {title_rate:.1f}%")
    
    # === WHALES 表 ===
    cursor.execute("SELECT COUNT(*) FROM whales")
    whale_total = cursor.fetchone()[0]
    stats['whales_total'] = whale_total
    
    cursor.execute("SELECT COUNT(*) FROM whales WHERE has_activity = 1")
    active = cursor.fetchone()[0]
    stats['whales_active'] = active
    
    cursor.execute("SELECT COUNT(*) FROM whales WHERE total_volume = 0 AND total_value = 0 AND has_activity = 1")
    bad_active = cursor.fetchone()[0]
    if bad_active > 0:
        issues.append(f"🔴 whales: {bad_active} 条标记为活跃但 volume/value 都为0")
    
    cursor.execute("SELECT COUNT(*) FROM whales WHERE pseudonym = 'unknown' AND total_volume > 10000")
    unknown_big = cursor.fetchone()[0]
    if unknown_big > 0:
        issues.append(f"🟡 whales: {unknown_big} 条交易量>$10k 的鲸鱼 pseudonym 仍为 unknown")
        
        # --- 自动修复：为>10k 但 pseudonym='unknown' 的鲸鱼生成伪名 ---
        cursor.execute("SELECT wallet FROM whales WHERE pseudonym = 'unknown' AND total_volume > 10000")
        fix_wallets = [row[0] for row in cursor.fetchall()]
        
        if fix_wallets:
            # 获取已有的非地址伪名（用于去重检查）
            cursor.execute("SELECT pseudonym FROM whales WHERE pseudonym NOT LIKE '0x%'")
            existing_names = set(row[0] for row in cursor.fetchall() if row[0] and row[0] != 'unknown')
            
            fixed_count = 0
            for wall in fix_wallets:
                new_pseudo = generate_pseudonym(wall, existing_names)
                cursor.execute(
                    "UPDATE whales SET pseudonym = ?, last_updated = ? WHERE wallet = ?",
                    (new_pseudo, datetime.now().isoformat(), wall)
                )
                fixed_count += 1
            
            db.commit()
            print(f"  自动修复: 为 {fixed_count} 条鲸鱼生成伪名")
            # 更新报告中的计数
            if unknown_big - fixed_count > 0:
                issues[-1] = f"🟡 whales: {unknown_big - fixed_count} 条交易量>$10k 的鲸鱼 pseudonym 仍为 unknown（已修复 {fixed_count} 条）"
            else:
                issues.pop()  # 全部修复，移除该issue
    
    # === POSITIONS 表 ===
    cursor.execute("SELECT COUNT(*) FROM positions")
    pos_total = cursor.fetchone()[0]
    stats['positions_total'] = pos_total
    
    cursor.execute("SELECT COUNT(*) FROM positions WHERE is_expired = 1")
    expired = cursor.fetchone()[0]
    stats['positions_expired'] = expired

    # 按过期天数分层
    cursor.execute('''SELECT 
        CASE 
            WHEN end_date < date('now', '-30 days') THEN 'expired_30dplus'
            WHEN end_date < date('now', '-7 days') THEN 'expired_7d_30d'
            ELSE 'expired_recent'
        END as bucket,
        COUNT(*) as cnt
    FROM positions WHERE is_expired = 1
    GROUP BY bucket''')
    bucket_map = {}
    for row in cursor.fetchall():
        bucket_map[row[0]] = row[1]

    expired_30dplus = bucket_map.get('expired_30dplus', 0)
    expired_7d_30d = bucket_map.get('expired_7d_30d', 0)
    expired_recent = bucket_map.get('expired_recent', 0)

    stats['positions_expired_30dplus'] = expired_30dplus
    stats['positions_expired_7d_30d'] = expired_7d_30d
    stats['positions_expired_recent'] = expired_recent

    expired_rate = expired / pos_total * 100 if pos_total > 0 else 0
    if expired_30dplus > 0:
        issues.append(f"🔴 positions: {expired_30dplus} 条过期30天+仍未清理（应清理未清理！）")
    elif expired_rate > 50:
        issues.append(f"🟡 positions: {expired_rate:.1f}% 已过期 ({expired:,}/{pos_total:,}) — {expired_7d_30d} 条等待清理, {expired_recent} 条近期过期")
    
    # === ALERTS 表 ===
    cursor.execute("SELECT COUNT(*) FROM alerts WHERE is_read = 0")
    unread = cursor.fetchone()[0]
    stats['alerts_unread'] = unread
    if unread > 1000:
        issues.append(f"🟡 alerts: {unread} 条未读，可能告警疲劳")
    
    # === 套利数据 ===
    cursor.execute("SELECT COUNT(*) FROM cross_market_arbitrage WHERE audit_status = 'approved' AND (polymarket_price = 0 OR manifold_price = 0)")
    bad_arb = cursor.fetchone()[0]
    if bad_arb > 0:
        issues.append(f"🔴 套利: {bad_arb} 条 approved 但价格为0的误匹配")
    
    # === 鲸鱼 JSON 时效 ===
    # 只统计活跃鲸鱼（has_activity=1）的JSON过期率，不活跃鲸鱼单独计数
    cursor.execute("SELECT wallet FROM whales WHERE has_activity = 1")
    active_wallets = {row[0].lower() for row in cursor.fetchall()}

    stale_json = 0
    active_json = 0
    inactive_json = 0
    now = datetime.now()
    if os.path.exists(WHALE_DIR):
        for f in os.listdir(WHALE_DIR):
            if f.endswith('.json'):
                wallet_from_file = f.replace('.json', '').lower()
                # 跳过非钱包地址文件（如 discovered_whales.json）
                if not wallet_from_file.startswith('0x'):
                    continue
                fp = os.path.join(WHALE_DIR, f)
                mtime = datetime.fromtimestamp(os.path.getmtime(fp))
                if wallet_from_file in active_wallets:
                    active_json += 1
                    if (now - mtime).days > 7:
                        stale_json += 1
                else:
                    inactive_json += 1

    active_stale_rate = stale_json / active_json * 100 if active_json > 0 else 0
    stats['whale_json_active'] = active_json
    stats['whale_json_active_stale'] = stale_json
    stats['whale_json_active_stale_rate'] = f"{active_stale_rate:.1f}%"
    stats['whale_json_inactive'] = inactive_json

    stats['whale_json_orphan'] = 0

    if active_stale_rate > 50:
        issues.append(f"🔴 活跃鲸鱼JSON: {stale_json}/{active_json} 超过7天未更新 ({active_stale_rate:.1f}%)")
    elif active_stale_rate > 30:
        issues.append(f"🟡 活跃鲸鱼JSON: {stale_json}/{active_json} 超过7天未更新 ({active_stale_rate:.1f}%)")
    if inactive_json > active_json and active_json > 0:
        issues.append(f"🟡 不活跃鲸鱼JSON ({inactive_json}) 多于活跃JSON ({active_json})")
    elif inactive_json > 0:
        issues.append(f"ℹ️ 不活跃鲸鱼JSON: {inactive_json} 个（不计入过期率）")

    # 检查孤儿 JSON 文件
    discovered_wallets = set()
    try:
        discovered_path = os.path.join(os.path.dirname(WHALE_DIR), 'whale_states', 'discovered_whales.json')
        if os.path.exists(discovered_path):
            with open(discovered_path) as f:
                discovered = json.load(f)
            discovered_wallets = {k.lower() for k in discovered.keys()}
    except Exception:
        pass

    orphan_json = 0
    whale_dir_files = os.listdir(WHALE_DIR) if os.path.exists(WHALE_DIR) else []
    for f in whale_dir_files:
        if f.endswith('.json') and f.startswith('0x'):
            wallet = f.replace('.json', '').lower()
            if wallet not in discovered_wallets:
                orphan_json += 1

    if orphan_json > 0:
        issues.append(f"🟡 孤儿JSON文件: {orphan_json} 个不在 discovered_whales 中，需确认")
        stats['whale_json_orphan'] = orphan_json
    
    # === 汇总 ===
    score = 100
    for issue in issues:
        if issue.startswith('🔴'):
            score -= 15
        elif issue.startswith('🟡'):
            score -= 5
        # ℹ️ info lines don't deduct score
    score = max(0, score)
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'score': score,
        'stats': stats,
        'issues': issues,
    }
    
    # 保存报告
    date_str = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'quality_{date_str}.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    # 输出
    print(f"📊 数据质量评分: {score}/100")
    print(f"📈 统计:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    
    if issues:
        print(f"\n⚠️ 发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ 无质量问题")
    
    db.close()
    return report

if __name__ == '__main__':
    check_db()
