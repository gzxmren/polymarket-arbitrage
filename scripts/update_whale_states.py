#!/usr/bin/env python3
"""
Whale States 定期更新脚本

功能：
1. 运行 whale_tracker_v2.py 发现活跃 whale
2. 更新 whale_states 目录中的数据
3. 输出简要报告

执行频率：每 6 小时
"""

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import os

# 路径配置
PROJECT_DIR = Path(__file__).resolve().parents[1]
WHALE_TRACKER = PROJECT_DIR / "06-tools" / "analysis" / "whale_tracker_v2.py"
DATA_DIR = PROJECT_DIR / "07-data"

def main():
    """主函数"""
    print("🔄 开始更新 whale_states...", flush=True)
    print(f"⏰ 时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", flush=True)
    
    # 1. 运行 whale_tracker_v2.py
    try:
        result = subprocess.run(
            ["python3", str(WHALE_TRACKER)],
            cwd=str(PROJECT_DIR / "06-tools" / "analysis"),
            capture_output=True,
            text=True,
            timeout=150  # 符合 AGENTS.md 规范（低频 job ≤150s）
        )
        
        if result.returncode == 0:
            print(result.stdout)
        else:
            print(f"❌ whale_tracker_v2.py 执行失败:")
            print(result.stderr)
            sys.exit(1)
            
    except subprocess.TimeoutExpired:
        print("⚠️ whale_tracker_v2.py 超时（150秒）")
        # 超时时仍统计现有结果（部分成功）
        whale_states_dir = DATA_DIR / "whale_states"
        if whale_states_dir.exists():
            files = list(whale_states_dir.glob("*.json"))
            print(f"\n📊 whale_states 统计（部分）:")
            print(f"  • 总文件数: {len(files)}")
        # 不以错误退出，允许下次继续
        sys.exit(0)
    
    # 2. 统计更新结果
    whale_states_dir = DATA_DIR / "whale_states"
    if whale_states_dir.exists():
        files = list(whale_states_dir.glob("*.json"))
        print(f"\n📊 whale_states 统计:")
        print(f"  • 总文件数: {len(files)}")
        
        # 统计最近更新的文件
        recent_files = [f for f in files if datetime.fromtimestamp(f.stat().st_mtime).date() == datetime.now().date()]
        print(f"  • 今日更新: {len(recent_files)} 个")
    
    # 3. 从 DB 补充高价值鲸鱼的 JSON 占位
    print(f"\n📦 从 DB 补充高价值鲸鱼 JSON 占位...", flush=True)
    
    DB_PATH = (os.environ.get("POLYMARKET_DB") or str(Path(__file__).resolve().parents[1] / "dashboard" / "backend" / "database" / "polymarket.db"))
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute('PRAGMA busy_timeout=10000')
        c = conn.cursor()
        
        # 获取 has_activity=1 且 total_volume > 10000 的前100个鲸鱼
        c.execute('''
            SELECT wallet, pseudonym, total_volume 
            FROM whales 
            WHERE has_activity = 1 AND total_volume > 10000 
            ORDER BY total_volume DESC 
            LIMIT 100
        ''')
        high_value_whales = c.fetchall()
        conn.close()
        
        # 检查哪些已经有 JSON 文件
        existing_json = set()
        for f in whale_states_dir.glob("0x*.json"):
            existing_json.add(f.stem.lower())
        
        missing_json = [(w, p, v) for w, p, v in high_value_whales if w.lower() not in existing_json]
        print(f"  DB高价值鲸鱼(vol>$10k): {len(high_value_whales)}, 已有JSON: {len(high_value_whales) - len(missing_json)}, 缺失: {len(missing_json)}")
        
        # 为缺失 JSON 的鲸鱼创建初始占位（仅前20个，避免创建过多文件）
        created_count = 0
        for wallet, pseudonym, volume in missing_json[:20]:
            state_file = whale_states_dir / f"{wallet}.json"
            if not state_file.exists():
                with open(state_file, 'w') as f:
                    json.dump({"positions": {}, "last_check": None, "total_value": 0, "is_suspicious": False}, f)
                created_count += 1
                print(f"  ✅ 创建占位: {pseudonym[:20]:<20} | vol=${volume:,.0f}", flush=True)
        
        if created_count > 0:
            print(f"  创建 {created_count} 个占位 JSON，等待下轮 whale_tracker_v2 填充数据", flush=True)
        else:
            print(f"  无需创建新占位", flush=True)
            
    except Exception as e:
        print(f"  ⚠️ DB 补充失败: {e}", flush=True)
    
    print(f"\n✅ whale_states 更新完成")

if __name__ == "__main__":
    main()