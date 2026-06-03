#!/usr/bin/env python3
"""
测试 DataSync V2
"""

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'dashboard/backend/app/services'))

from hybrid_data_source import HybridDataSource

DB_PATH = '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend/database/polymarket.db'

print("=" * 80)
print("测试 DataSync V2 (混合数据源)")
print("=" * 80)

# 初始化混合数据源
source = HybridDataSource(DB_PATH)

# 测试钱包
test_wallets = [
    '0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1',  # Leaderboard #1
    '0x0979bad57d7a1403db89cbcd9c52bf43f2138d9b',  # 有 JSON
]

print("\n测试同步 2 个钱包:")
for wallet in test_wallets:
    print(f"\n{'='*80}")
    print(f"钱包: {wallet}")
    print(f"{'='*80}")
    
    data = source.get_whale_data(wallet)
    
    if data:
        print(f"✅ 获取成功:")
        print(f"  数据源: {data.data_source}")
        print(f"  新鲜度: {data.data_freshness}")
        print(f"  总价值: ${data.total_value:,.0f}")
        print(f"  持仓数: {data.position_count}")
        print(f"  集中度: {data.top5_ratio*100:.1f}%")
    else:
        print(f"❌ 无数据")

print("\n" + "=" * 80)
print("✅ 测试完成")
print("=" * 80)
