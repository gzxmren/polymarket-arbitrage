#!/usr/bin/env python3
"""
更新鲸鱼名称（pseudonym）
从 Polymarket API 获取真实用户名，或生成可读性好的伪名
"""

import sqlite3
import urllib.request
import json
import ssl
import random
from pathlib import Path
from datetime import datetime
import os

# 数据库路径
DB_PATH = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))

# Polymarket API
DATA_API = "https://data-api.polymarket.com"

# 禁用 SSL 验证（如果需要）
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# 命名词库
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


def fetch_user_info_from_api(wallet: str) -> dict:
    """
    尝试从 Polymarket API 获取用户信息
    
    注意：Polymarket 的 API 通常需要认证才能获取用户信息
    这里尝试从最近的交易记录中提取
    """
    try:
        # 获取该钱包的最近交易
        url = f"{DATA_API}/trades?limit=100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            trades = json.loads(resp.read().decode())
            
            # 查找该钱包的交易记录
            for trade in trades:
                if trade.get('proxyWallet', '').lower() == wallet.lower():
                    return {
                        'pseudonym': trade.get('pseudonym'),
                        'name': trade.get('name'),
                        'bio': trade.get('bio'),
                        'profile_image': trade.get('profileImage')
                    }
    except Exception as e:
        print(f"  API 查询失败: {e}")
    
    return {}


def update_whale_names(batch_size: int = 100, dry_run: bool = False):
    """
    更新鲸鱼名称
    
    Args:
        batch_size: 每批处理的数量
        dry_run: 如果为 True，只打印不实际更新
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 获取所有使用地址作为名称的鲸鱼
    cursor.execute('''
        SELECT wallet, pseudonym 
        FROM whales 
        WHERE pseudonym IS NULL 
           OR pseudonym = '' 
           OR pseudonym LIKE '0x%'
        LIMIT ?
    ''', (batch_size,))
    
    whales_to_update = cursor.fetchall()
    
    if not whales_to_update:
        print("✅ 所有鲸鱼已有有效名称")
        conn.close()
        return
    
    print(f"📝 找到 {len(whales_to_update)} 个需要命名的鲸鱼")
    
    # 获取现有名称（确保唯一性）
    cursor.execute('SELECT pseudonym FROM whales WHERE pseudonym IS NOT NULL')
    existing_names = set(row[0] for row in cursor.fetchall() if not row[0].startswith('0x'))
    
    updated = 0
    failed = 0
    
    for wallet, current_name in whales_to_update:
        try:
            # 首先尝试从 API 获取真实名称
            user_info = fetch_user_info_from_api(wallet)
            
            if user_info.get('pseudonym'):
                new_name = user_info['pseudonym']
                source = "API"
            elif user_info.get('name'):
                new_name = user_info['name']
                source = "API(name)"
            else:
                # 生成伪名
                new_name = generate_pseudonym(wallet, existing_names)
                source = "Generated"
            
            if dry_run:
                print(f"  [DRY RUN] {wallet[:20]}... -> {new_name} ({source})")
            else:
                cursor.execute('''
                    UPDATE whales 
                    SET pseudonym = ?, last_updated = ?
                    WHERE wallet = ?
                ''', (new_name, datetime.now().isoformat(), wallet))
                print(f"  ✅ {wallet[:20]}... -> {new_name} ({source})")
            
            updated += 1
            
        except Exception as e:
            print(f"  ❌ {wallet[:20]}... 更新失败: {e}")
            failed += 1
    
    if not dry_run:
        conn.commit()
    
    conn.close()
    
    print(f"\n📊 更新完成: {updated} 成功, {failed} 失败")
    
    return updated, failed


def batch_update_all(dry_run: bool = False):
    """批量更新所有需要命名的鲸鱼"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 统计需要更新的数量
    cursor.execute('''
        SELECT COUNT(*) 
        FROM whales 
        WHERE pseudonym IS NULL 
           OR pseudonym = '' 
           OR pseudonym LIKE '0x%'
    ''')
    
    total_to_update = cursor.fetchone()[0]
    conn.close()
    
    if total_to_update == 0:
        print("✅ 所有鲸鱼已有有效名称")
        return
    
    print(f"🎯 总共需要更新 {total_to_update} 个鲸鱼名称")
    print(f"   模式: {'试运行 (dry-run)' if dry_run else '实际更新'}\n")
    
    total_updated = 0
    total_failed = 0
    batch_num = 0
    
    while True:
        batch_num += 1
        print(f"\n📦 批次 {batch_num}:")
        updated, failed = update_whale_names(batch_size=100, dry_run=dry_run)
        
        if updated == 0 and failed == 0:
            break
        
        total_updated += updated
        total_failed += failed
        
        if updated < 100:  # 少于 batch_size 说明处理完了
            break
    
    print(f"\n{'='*50}")
    print(f"✅ 全部完成!")
    print(f"   总计更新: {total_updated}")
    print(f"   失败: {total_failed}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='更新鲸鱼名称')
    parser.add_argument('--dry-run', action='store_true', help='试运行，不实际更新')
    parser.add_argument('--batch', type=int, default=100, help='每批处理数量')
    
    args = parser.parse_args()
    
    if args.dry_run:
        print("🔍 试运行模式 (dry-run)\n")
    
    batch_update_all(dry_run=args.dry_run)