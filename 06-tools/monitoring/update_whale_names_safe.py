#!/usr/bin/env python3
"""
安全更新鲸鱼名称（pseudonym）

特性:
1. 自动备份数据库
2. 事务批量处理（每100条提交）
3. 详细日志记录
4. 出错自动回滚
5. 支持试运行模式
"""

import sqlite3
import urllib.request
import json
import ssl
import random
import shutil
import sys
from pathlib import Path
from datetime import datetime
import os

# 配置
DB_PATH = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[2] / "dashboard" / "backend" / "database" / "polymarket.db"))
BACKUP_DIR = Path('/tmp/polymarket_backups')
LOG_FILE = Path('/tmp/update_whale_names.log')
BATCH_SIZE = 100

# 确保备份目录存在
BACKUP_DIR.mkdir(exist_ok=True)

# 日志函数
def log(msg, level='INFO'):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{timestamp}] [{level}] {msg}"
    print(log_msg)
    with open(LOG_FILE, 'a') as f:
        f.write(log_msg + '\n')

# SSL 上下文
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# 命名词库
ADJECTIVES = [
    "Swift", "Silent", "Mighty", "Clever", "Bold", "Wise", "Fierce", "Calm",
    "Rapid", "Stealthy", "Valiant", "Cunning", "Gentle", "Sly", "Brave", "Quick",
    "Sharp", "Smooth", "Wild", "Tame", "Grand", "Noble", "Lucky", "Keen",
    "Bright", "Dark", "Light", "Heavy", "Cool", "Warm", "Fresh", "Ancient",
    "Modern", "Future", "Cosmic", "Solar", "Lunar", "Crystal", "Golden", "Silver",
    "Bronze", "Iron", "Steel", "Diamond", "Ruby", "Emerald", "Sapphire", "Amber",
    "Obsidian", "Marble", "Ivory", "Shadow", "Thunder", "Storm"
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


def backup_database():
    """备份数据库"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = BACKUP_DIR / f'polymarket_backup_{timestamp}.db'
    
    log(f"创建数据库备份: {backup_path}")
    shutil.copy2(DB_PATH, backup_path)
    
    # 验证备份
    if backup_path.exists() and backup_path.stat().st_size > 0:
        log(f"✓ 备份成功 ({backup_path.stat().st_size:,} bytes)")
        return backup_path
    else:
        raise RuntimeError("备份失败")


def generate_pseudonym(wallet: str, existing_names: set) -> str:
    """生成唯一的伪名"""
    seed = int(wallet[2:10], 16)
    rng = random.Random(seed)
    
    max_attempts = 100
    for _ in range(max_attempts):
        pattern = rng.choice([
            lambda: f"{rng.choice(ADJECTIVES)}-{rng.choice(NOUNS)}",
            lambda: f"{rng.choice(COLORS)}-{rng.choice(NOUNS)}",
            lambda: f"{rng.choice(ADJECTIVES)}-{rng.choice(COLORS)}-{rng.choice(NOUNS)}",
            lambda: f"{rng.choice(NOUNS)}-{rng.choice(ADJECTIVES)}"
        ])
        
        name = pattern()
        
        if name not in existing_names:
            existing_names.add(name)
            return name
    
    # 添加数字后缀
    base_name = f"{rng.choice(ADJECTIVES)}-{rng.choice(NOUNS)}"
    counter = 1
    while f"{base_name}-{counter}" in existing_names:
        counter += 1
    
    final_name = f"{base_name}-{counter}"
    existing_names.add(final_name)
    return final_name


def fetch_pseudonym_from_api(wallet: str) -> str:
    """尝试从 API 获取 pseudonym"""
    try:
        url = "https://data-api.polymarket.com/trades?limit=100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            trades = json.loads(resp.read().decode())
            
            for trade in trades:
                if trade.get('proxyWallet', '').lower() == wallet.lower():
                    # 优先使用 pseudonym，其次使用 name
                    pseudonym = trade.get('pseudonym')
                    if pseudonym and not pseudonym.startswith('0x'):
                        return pseudonym
                    name = trade.get('name')
                    if name and not name.startswith('0x'):
                        return name
    except Exception as e:
        log(f"API 查询失败: {e}", 'WARNING')
    
    return None


def update_whale_names(dry_run: bool = False):
    """安全更新鲸鱼名称"""
    
    # 1. 备份数据库
    if not dry_run:
        backup_path = backup_database()
    
    # 2. 连接数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 3. 统计需要更新的数量
        cursor.execute('''
            SELECT COUNT(*) 
            FROM whales 
            WHERE pseudonym IS NULL 
               OR pseudonym = '' 
               OR pseudonym LIKE '0x%'
        ''')
        total_to_update = cursor.fetchone()[0]
        
        if total_to_update == 0:
            log("✓ 所有鲸鱼已有有效名称")
            return 0, 0
        
        log(f"需要更新 {total_to_update} 个鲸鱼名称")
        log(f"模式: {'试运行' if dry_run else '实际更新'}")
        
        # 4. 获取现有名称
        cursor.execute('SELECT pseudonym FROM whales WHERE pseudonym IS NOT NULL')
        existing_names = set(row[0] for row in cursor.fetchall() if row[0] and not row[0].startswith('0x'))
        log(f"现有有效名称: {len(existing_names)} 个")
        
        # 5. 分批处理
        total_updated = 0
        total_failed = 0
        batch_num = 0
        
        while True:
            batch_num += 1
            
            # 获取一批需要更新的鲸鱼
            cursor.execute('''
                SELECT wallet, pseudonym 
                FROM whales 
                WHERE pseudonym IS NULL 
                   OR pseudonym = '' 
                   OR pseudonym LIKE '0x%'
                LIMIT ?
            ''', (BATCH_SIZE,))
            
            batch = cursor.fetchall()
            if not batch:
                break
            
            log(f"\n批次 {batch_num}: 处理 {len(batch)} 个")
            
            batch_updated = 0
            batch_failed = 0
            
            for wallet, current_name in batch:
                try:
                    # 尝试从 API 获取
                    new_name = fetch_pseudonym_from_api(wallet)
                    source = "API"
                    
                    # 如果 API 没有，生成伪名
                    if not new_name:
                        new_name = generate_pseudonym(wallet, existing_names)
                        source = "Generated"
                    
                    if dry_run:
                        log(f"  [DRY RUN] {wallet[:20]}... -> {new_name} ({source})")
                    else:
                        cursor.execute('''
                            UPDATE whales 
                            SET pseudonym = ?, last_updated = ?
                            WHERE wallet = ?
                        ''', (new_name, datetime.now().isoformat(), wallet))
                        log(f"  ✓ {wallet[:20]}... -> {new_name} ({source})")
                    
                    batch_updated += 1
                    
                except Exception as e:
                    log(f"  ✗ {wallet[:20]}... 失败: {e}", 'ERROR')
                    batch_failed += 1
            
            # 提交批次
            if not dry_run:
                conn.commit()
                log(f"  批次 {batch_num} 已提交: {batch_updated} 成功, {batch_failed} 失败")
            
            total_updated += batch_updated
            total_failed += batch_failed
            
            # 如果少于 BATCH_SIZE，说明处理完了
            if len(batch) < BATCH_SIZE:
                break
        
        log(f"\n{'='*50}")
        log(f"✓ 全部完成!")
        log(f"  总计更新: {total_updated}")
        log(f"  失败: {total_failed}")
        
        if not dry_run:
            log(f"  备份文件: {backup_path}")
        
        return total_updated, total_failed
        
    except Exception as e:
        log(f"\n✗ 发生错误: {e}", 'ERROR')
        if not dry_run:
            conn.rollback()
            log("  已回滚所有更改", 'WARNING')
        raise
    finally:
        conn.close()


def verify_update():
    """验证更新结果"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    log("\n验证更新结果:")
    
    cursor.execute('''
        SELECT 
            CASE 
                WHEN pseudonym IS NULL THEN 'NULL'
                WHEN pseudonym = '' THEN 'Empty'
                WHEN pseudonym LIKE '0x%' THEN 'Address'
                ELSE 'Valid Name'
            END as name_type,
            COUNT(*) as count
        FROM whales
        GROUP BY name_type
    ''')
    
    for row in cursor.fetchall():
        log(f"  {row[0]}: {row[1]} 个")
    
    # 显示几个示例
    cursor.execute('''
        SELECT wallet, pseudonym, total_value 
        FROM whales 
        WHERE pseudonym NOT LIKE '0x%' AND pseudonym IS NOT NULL
        LIMIT 5
    ''')
    
    log("\n新名称示例:")
    for row in cursor.fetchall():
        log(f"  {row[0][:20]}... | {row[1]} | ${row[2]:,.0f}")
    
    conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='安全更新鲸鱼名称')
    parser.add_argument('--dry-run', action='store_true', help='试运行，不实际更新')
    parser.add_argument('--verify', action='store_true', help='仅验证当前状态')
    
    args = parser.parse_args()
    
    if args.verify:
        verify_update()
    else:
        try:
            updated, failed = update_whale_names(dry_run=args.dry_run)
            if not args.dry_run and updated > 0:
                verify_update()
        except Exception as e:
            log(f"脚本执行失败: {e}", 'ERROR')
            sys.exit(1)
