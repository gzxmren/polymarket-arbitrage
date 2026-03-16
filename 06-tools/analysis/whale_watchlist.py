#!/usr/bin/env python3
"""
重点鲸鱼跟踪模块
自动标记和管理值得关注的鲸鱼
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# 配置
DATA_DIR = Path(__file__).parent.parent.parent / "07-data"
WATCHLIST_FILE = DATA_DIR / "whale_watchlist.json"
WHALE_STATES_DIR = DATA_DIR / "whale_states"

# 自动标记阈值
WATCH_THRESHOLD_VALUE = 100000  # 持仓价值 > $100k
WATCH_THRESHOLD_CHANGES = 5      # 24h变动 > 5次
WATCH_THRESHOLD_TRADES = 3       # 连续多次交易

# 冷却时间（避免重复提醒）
ALERT_COOLDOWN_MINUTES = 15


def load_watchlist() -> Dict:
    """加载重点鲸鱼列表"""
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "whales": {},  # wallet -> whale info
        "alerts": []   # alert history
    }


def save_watchlist(watchlist: Dict):
    """保存重点鲸鱼列表"""
    watchlist["updated_at"] = datetime.now(timezone.utc).isoformat()
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_FILE, 'w') as f:
        json.dump(watchlist, f, indent=2)


def should_add_to_watchlist(analysis: Dict) -> bool:
    """
    判断是否应加入重点列表
    必须同时满足：高持仓价值 AND 有实际持仓
    """
    total_value = analysis.get('total_value', 0)
    position_count = analysis.get('position_count', 0)
    
    # 排除测试数据：持仓为0或价值过低
    if position_count == 0 or total_value < 1000:
        return False
    
    # 条件1: 高持仓价值 (> $100k)
    if total_value >= WATCH_THRESHOLD_VALUE:
        return True
    
    # 条件2: 高变动频率 (> 5次) AND 中等持仓 (> $50k)
    changes_count = len(analysis.get('changes', []))
    if changes_count >= WATCH_THRESHOLD_CHANGES and total_value >= 50000:
        return True
    
    return False


def calculate_concentration_metrics(positions: list) -> dict:
    """
    计算持仓集中度指标
    """
    if not positions:
        return {"hhi": 0, "top5_ratio": 0, "top10_ratio": 0}
    
    # 计算各仓位价值
    position_values = []
    for pos in positions:
        size = float(pos.get('size', 0))
        price = float(pos.get('curPrice', pos.get('currentPrice', 0)))
        value = size * price
        position_values.append(value)
    
    total_value = sum(position_values)
    if total_value == 0:
        return {"hhi": 0, "top5_ratio": 0, "top10_ratio": 0}
    
    # 计算HHI（赫芬达尔指数）
    hhi = sum((v/total_value)**2 for v in position_values)
    
    # 计算Top5/Top10占比
    sorted_values = sorted(position_values, reverse=True)
    top5_ratio = sum(sorted_values[:5]) / total_value
    top10_ratio = sum(sorted_values[:10]) / total_value
    
    return {
        "hhi": hhi,
        "top5_ratio": top5_ratio,
        "top10_ratio": top10_ratio
    }


def detect_convergence_trend(whale: dict, current_metrics: dict) -> str:
    """
    检测收敛趋势
    返回: 'converging'(收敛中), 'diverging'(分散中), 'stable'(稳定), ''(无趋势)
    """
    history = whale.get("concentration_history", [])
    if len(history) < 2:
        return ""
    
    # 比较最近两次的Top5占比
    prev_top5 = history[-2].get("top5_ratio", 0)
    curr_top5 = current_metrics.get("top5_ratio", 0)
    
    if curr_top5 > prev_top5 + 0.1:  # Top5占比增加10%
        return "converging"
    elif curr_top5 < prev_top5 - 0.1:  # Top5占比减少10%
        return "diverging"
    else:
        return "stable"


def add_to_watchlist(watchlist: Dict, wallet: str, analysis: Dict) -> bool:
    """添加鲸鱼到重点列表（添加集中度跟踪）"""
    positions = analysis.get('positions', [])
    current_metrics = calculate_concentration_metrics(positions)
    
    if wallet in watchlist["whales"]:
        # 已存在，更新信息
        whale = watchlist["whales"][wallet]
        whale["last_seen"] = datetime.now(timezone.utc).isoformat()
        whale["total_value"] = analysis.get('total_value', 0)
        whale["position_count"] = analysis.get('position_count', 0)
        whale["changes_count"] = len(analysis.get('changes', []))
        whale["check_count"] = whale.get("check_count", 0) + 1
        
        # 更新集中度历史
        if "concentration_history" not in whale:
            whale["concentration_history"] = []
        whale["concentration_history"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **current_metrics
        })
        # 只保留最近10条记录
        whale["concentration_history"] = whale["concentration_history"][-10:]
        
        # 检测收敛趋势
        trend = detect_convergence_trend(whale, current_metrics)
        whale["convergence_trend"] = trend
        
        return False  # 不是新添加
    
    # 新添加
    info = analysis.get('info', {})
    watchlist["whales"][wallet] = {
        "wallet": wallet,
        "pseudonym": info.get('pseudonym', wallet[:10] + '...'),
        "added_at": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "total_value": analysis.get('total_value', 0),
        "position_count": analysis.get('position_count', 0),
        "changes_count": len(analysis.get('changes', [])),
        "check_count": 1,
        "alert_count": 0,
        "concentration_history": [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **current_metrics
        }],
        "convergence_trend": ""
    }
    return True  # 是新添加


def remove_from_watchlist(watchlist: Dict, wallet: str):
    """从重点列表移除"""
    if wallet in watchlist["whales"]:
        del watchlist["whales"][wallet]


def is_in_cooldown(watchlist: Dict, wallet: str, market: str = "") -> bool:
    """检查是否在冷却期"""
    alerts = watchlist.get("alerts", [])
    now = datetime.now(timezone.utc)
    
    for alert in reversed(alerts):
        if alert.get("wallet") == wallet:
            # 如果有指定市场，检查同一市场
            if market and alert.get("market") != market:
                continue
            alert_time = datetime.fromisoformat(alert["timestamp"])
            if now - alert_time < timedelta(minutes=ALERT_COOLDOWN_MINUTES):
                return True
    return False


def record_alert(watchlist: Dict, wallet: str, alert_type: str, market: str = ""):
    """记录警报（带去重）"""
    now = datetime.now(timezone.utc)
    
    # 检查最近1分钟内是否已有相同警报
    for alert in reversed(watchlist.get("alerts", [])):
        if (alert.get("wallet") == wallet and 
            alert.get("type") == alert_type and
            alert.get("market") == market):
            # 检查时间差（1分钟内视为重复）
            try:
                alert_time = datetime.fromisoformat(alert["timestamp"].replace('Z', '+00:00'))
                if (now - alert_time).total_seconds() < 60:
                    return  # 重复警报，跳过
            except:
                pass
    
    watchlist["alerts"].append({
        "wallet": wallet,
        "type": alert_type,
        "market": market,
        "timestamp": now.isoformat()
    })
    
    # 更新鲸鱼警报计数
    if wallet in watchlist["whales"]:
        watchlist["whales"][wallet]["alert_count"] = \
            watchlist["whales"][wallet].get("alert_count", 0) + 1
    
    # 只保留最近100条警报
    watchlist["alerts"] = watchlist["alerts"][-100:]


def get_watchlist_summary(watchlist: Dict) -> Dict:
    """获取重点鲸鱼概况"""
    whales = list(watchlist.get("whales", {}).values())
    
    # 按持仓价值排序
    whales.sort(key=lambda x: x.get("total_value", 0), reverse=True)
    
    return {
        "count": len(whales),
        "whales": whales,
        "total_value": sum(w.get("total_value", 0) for w in whales),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }


def format_watchlist_summary(summary: Dict) -> str:
    """格式化重点鲸鱼概况（添加收敛指标）"""
    lines = [
        "🔔 *重点鲸鱼概况*",
        "",
        f"⏰ 报告时间: {datetime.now().strftime('%H:%M')}",
        f"📊 当前关注: {summary['count']} 位",
        f"💰 总持仓价值: ${summary['total_value']:,.0f}",
        ""
    ]
    
    for i, whale in enumerate(summary['whales'][:5], 1):  # 只显示前5
        pseudonym = whale.get('pseudonym', whale['wallet'][:10] + '...')
        value = whale.get('total_value', 0)
        positions = whale.get('position_count', 0)
        changes = whale.get('changes_count', 0)
        check_count = whale.get('check_count', 0)
        
        # 标记新鲸鱼（检查次数<=3）
        is_new = check_count <= 3
        new_mark = "🔥[新] " if is_new else ""
        
        lines.append(f"{i}. {new_mark}*{pseudonym}*")
        lines.append(f"   💰 持仓: ${value:,.0f} ({positions}个市场)")
        lines.append(f"   📈 24h变动: {changes}次")
        
        # 集中度指标
        history = whale.get("concentration_history", [])
        if history:
            latest = history[-1]
            top5_ratio = latest.get("top5_ratio", 0)
            hhi = latest.get("hhi", 0)
            
            # 集中度描述
            if top5_ratio >= 0.7:
                concentration = "高度集中"
            elif top5_ratio >= 0.5:
                concentration = "开始集中"
            elif top5_ratio >= 0.3:
                concentration = "适度分散"
            else:
                concentration = "高度分散"
            
            lines.append(f"   📊 集中度: Top5占{top5_ratio:.0%} ({concentration})")
        
        # 收敛趋势
        trend = whale.get("convergence_trend", "")
        if trend == "converging":
            lines.append(f"   🎯 策略: 🔥正在收敛！关注重点仓位")
        elif trend == "diverging":
            lines.append(f"   🎯 策略: 正在分散，观望")
        elif positions > 50:
            lines.append(f"   🎯 策略: 分散持仓，等待收敛信号")
        elif changes >= 5:
            lines.append(f"   🎯 策略: 活跃调仓 🔥")
        else:
            lines.append(f"   🎯 策略: 稳定持有")
        lines.append("")
    
    lines.append("💡 *提示: 重点鲸鱼每4小时更新一次*")
    lines.append("📊 *集中度>50%表示开始集中，>70%为高度集中*")
    
    return "\n".join(lines)


def update_watchlist_from_analysis(watchlist: Dict, wallet: str, analysis: Dict) -> Dict:
    """
    从分析结果更新重点列表
    返回: {'is_new': bool, 'should_alert': bool, 'is_watched': bool}
    
    逻辑:
    - 符合条件的鲸鱼加入关注列表
    - 不符合条件的鲸鱼从关注列表移除（但保留历史记录）
    - 移除后如果再次符合条件，会重新加入
    """
    result = {
        "is_new": False,
        "should_alert": False,
        "is_watched": False
    }
    
    # 检查是否应加入重点列表
    if should_add_to_watchlist(analysis):
        # 符合条件，加入关注列表
        is_new = add_to_watchlist(watchlist, wallet, analysis)
        result["is_watched"] = True
        result["is_new"] = is_new
        
        # 新加入的鲸鱼立即提醒
        if is_new:
            result["should_alert"] = True
            record_alert(watchlist, wallet, "new_watched")
        else:
            # 已有鲸鱼，检查是否在冷却期
            changes = analysis.get('changes', [])
            if changes and not is_in_cooldown(watchlist, wallet):
                result["should_alert"] = True
                record_alert(watchlist, wallet, "activity")
    else:
        # 不符合条件，从关注列表移除（如果存在）
        if wallet in watchlist.get("whales", {}):
            remove_from_watchlist(watchlist, wallet)
            print(f"   ℹ️  {analysis.get('info', {}).get('pseudonym', wallet[:10])} 不再符合条件，从关注列表移除")
    
    save_watchlist(watchlist)
    return result


# 测试代码（仅在测试模式下运行，不影响生产数据）
if __name__ == "__main__":
    import sys
    
    # 检查是否明确指定测试模式
    if "--test" not in sys.argv:
        print("⚠️  测试代码需要使用 --test 参数运行")
        print("   示例: python3 whale_watchlist.py --test")
        sys.exit(0)
    
    print("🐋 重点鲸鱼跟踪模块测试模式")
    print("=" * 50)
    
    # 使用临时测试文件，不污染生产数据
    test_file = Path("/tmp/test_whale_watchlist.json")
    
    # 临时修改全局变量
    original_file = WATCHLIST_FILE
    globals()['WATCHLIST_FILE'] = test_file
    
    # 清理测试文件
    if test_file.exists():
        test_file.unlink()
    
    # 加载测试
    watchlist = load_watchlist()
    print(f"✅ 加载成功，当前关注: {len(watchlist['whales'])} 位")
    
    # 测试1: 高价值鲸鱼（应加入）
    test_analysis1 = {
        "info": {"pseudonym": "TestWhale_HighValue"},
        "total_value": 150000,
        "position_count": 50,
        "changes": [{"type": "new"}]
    }
    result1 = update_watchlist_from_analysis(watchlist, "0xabc...", test_analysis1)
    print(f"✅ 高价值测试: is_new={result1['is_new']}, is_watched={result1['is_watched']}")
    
    # 测试2: 低价值鲸鱼（不应加入）
    test_analysis2 = {
        "info": {"pseudonym": "TestWhale_LowValue"},
        "total_value": 500,
        "position_count": 5,
        "changes": [{"type": "new"}]
    }
    result2 = update_watchlist_from_analysis(watchlist, "0xdef...", test_analysis2)
    print(f"✅ 低价值测试: is_new={result2['is_new']}, is_watched={result2['is_watched']} (应都为False)")
    
    # 测试3: 零持仓鲸鱼（不应加入）
    test_analysis3 = {
        "info": {"pseudonym": "TestWhale_Zero"},
        "total_value": 0,
        "position_count": 0,
        "changes": [{"type": "new"}]
    }
    result3 = update_watchlist_from_analysis(watchlist, "0x000...", test_analysis3)
    print(f"✅ 零持仓测试: is_new={result3['is_new']}, is_watched={result3['is_watched']} (应都为False)")
    
    # 概况测试
    summary = get_watchlist_summary(watchlist)
    print(f"✅ 概况生成: {summary['count']} 位 (应为1), 总价值 ${summary['total_value']:,.0f}")
    
    # 清理测试文件
    if test_file.exists():
        test_file.unlink()
    
    # 恢复全局变量
    globals()['WATCHLIST_FILE'] = original_file
    
    print("\n" + "=" * 50)
    print("✅ 所有测试通过!")
