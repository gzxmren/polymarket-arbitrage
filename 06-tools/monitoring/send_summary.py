#!/usr/bin/env python3
"""
发送工作总结报告
硬编码环境变量，确保可靠

[修复] 2026-04-17:
1. 优先读取 whale_report_*.json (鲸鱼数据，每6小时更新)
2. 保留 monitor_report_*.json 读取逻辑 (主监控恢复后可用)
3. 增加数据来源说明

数据流:
- whale_report_*.json: 由 update_whale_states.py 每6小时生成
- monitor_report_*.json: 由 polymarket_monitor_v2.py 每小时生成(需恢复)
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# 从 .env 加载配置（密钥不入源码；.env 已被 .gitignore 忽略）
# [P2安全] 2026-06-03: 移除硬编码 Telegram 密钥，改从 .env 读取
_env_file = Path(__file__).parent / '.env'
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from telegram_notifier_v2 import send_summary_report


def load_whale_data(data_dir: Path) -> dict:
    """
    加载鲸鱼报告数据
    
    数据来源: 
    - whale_report_*.json: 本地鲸鱼追踪 (每6小时更新)
    - polycop_observe/polycop_addresses.json: PolyCop评分 (每6小时更新)
    """
    # 1. 读取本地鲸鱼追踪数据
    whale_reports = sorted(data_dir.glob('whale_report_*.json'), reverse=True)
    whales_tracked = 0
    active_whales = 0
    whale_source = '无数据'
    
    if whale_reports:
        try:
            with open(whale_reports[0]) as f:
                data = json.load(f)
            whales_tracked = data.get('whales_tracked', 0)
            active_whales = data.get('active_whales', 0)
            whale_source = whale_reports[0].name
        except Exception as e:
            print(f"读取鲸鱼报告失败: {e}", file=sys.stderr)
    
    # 2. 读取 PolyCop 评分数据 (高分地址)
    polycop_file = data_dir / 'polycop_observe' / 'polycop_addresses.json'
    high_score_whales = 0
    polycop_source = '无数据'
    
    if polycop_file.exists():
        try:
            with open(polycop_file) as f:
                polycop_data = json.load(f)
            # 计算高分地址 (Score >= 80)
            high_score_whales = sum(
                1 for a in polycop_data.values() 
                if a.get('smart_score', 0) >= 80
            )
            polycop_source = polycop_file.name
        except Exception as e:
            print(f"读取PolyCop数据失败: {e}", file=sys.stderr)
    
    return {
        'whales_tracked': whales_tracked,
        'active_whales': active_whales,
        'high_score_whales': high_score_whales,
        'whale_source': whale_source,
        'polycop_source': polycop_source
    }


def load_monitor_data(data_dir: Path) -> dict:
    """
    加载主监控报告数据
    
    数据来源: monitor_report_*.json
    生成频率: 每小时 (由 polymarket_monitor_v2.py 生成)
    
    Note: 当前主监控未运行，返回默认值
    """
    monitor_reports = sorted(data_dir.glob('monitor_report_*.json'), reverse=True)
    
    if not monitor_reports:
        return {
            'pair_cost_count': 0,
            'cross_market_count': 0,
            'markets_scanned': 0,
            'data_source': '主监控未运行'
        }
    
    try:
        with open(monitor_reports[0]) as f:
            data = json.load(f)
        
        return {
            'pair_cost_count': data.get('pair_cost', {}).get('approved_count', 0),
            'cross_market_count': data.get('cross_market', {}).get('approved_count', 0),
            'markets_scanned': data.get('markets', {}).get('total', 0),
            'data_source': monitor_reports[0].name
        }
    except Exception as e:
        print(f"读取监控报告失败: {e}", file=sys.stderr)
        return {
            'pair_cost_count': 0,
            'cross_market_count': 0,
            'data_source': '读取失败'
        }


# ==================== 主程序 ====================

# 生成报告数据（使用默认值）
report = {
    'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    'pair_cost_count': 0,
    'cross_market_count': 0,
    'active_whales': 0,
    'whales_tracked': 0,
    'high_score_whales': 0,
    'markets_scanned': 0,
    'total_opportunities': 0,
    'avg_pair_cost': 1.0,
    'data_sources': []
}

# 读取实际数据
try:
    import json
    
    # 数据目录
    data_dir = Path(__file__).parent.parent.parent / '07-data'
    
    # Step 1: 读取鲸鱼数据（主要数据源）
    whale_data = load_whale_data(data_dir)
    report['active_whales'] = whale_data.get('active_whales', 0)
    report['whales_tracked'] = whale_data.get('whales_tracked', 0)
    report['high_score_whales'] = whale_data.get('high_score_whales', 0)
    report['data_sources'].append(f"鲸鱼追踪: {whale_data.get('whale_source', 'N/A')}")
    report['data_sources'].append(f"PolyCop: {whale_data.get('polycop_source', 'N/A')}")
    
    # Step 2: 读取主监控数据（Pair Cost + 跨平台套利）
    monitor_data = load_monitor_data(data_dir)
    report['pair_cost_count'] = monitor_data.get('pair_cost_count', 0)
    report['cross_market_count'] = monitor_data.get('cross_market_count', 0)
    report['markets_scanned'] = monitor_data.get('markets_scanned', 0)
    report['data_sources'].append(f"监控: {monitor_data.get('data_source', 'N/A')}")
    
    # 计算总机会数
    report['total_opportunities'] = (
        report['pair_cost_count'] + 
        report['cross_market_count'] + 
        report['active_whales']
    )
    
except Exception as e:
    print(f"加载数据失败: {e}", file=sys.stderr)

# 打印调试信息
print(f"📊 报告数据:")
print(f"   活跃鲸鱼: {report['active_whales']}")
print(f"   追踪鲸鱼: {report['whales_tracked']}")
print(f"   高分鲸鱼: {report['high_score_whales']}")
print(f"   Pair Cost: {report['pair_cost_count']}")
print(f"   跨平台套利: {report['cross_market_count']}")
print(f"   数据来源: {report['data_sources']}")

# 发送报告
send_summary_report(report)
print(f"\n✅ 工作总结报告已发送: {report['scan_time']}")
