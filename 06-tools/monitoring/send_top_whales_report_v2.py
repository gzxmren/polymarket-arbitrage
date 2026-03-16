#!/usr/bin/env python3
"""
发送 Top 10 鲸鱼排行榜报告 (V2 - 从数据库读取)
可以手动运行或加入定时任务
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# 添加路径
sys.path.insert(0, str(Path(__file__).parent))

from telegram_notifier_v2 import send_telegram_message

# 数据库路径
DB_PATH = Path(__file__).parent.parent.parent / "dashboard/backend/database/polymarket.db"


def get_top_whales_from_db(limit: int = 10) -> list:
    """从数据库获取 Top N 鲸鱼"""
    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        return []
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 获取Top N鲸鱼（包括关注和非关注的）
        cursor.execute('''
            SELECT 
                w.wallet, w.pseudonym, w.total_value, w.position_count,
                w.top5_ratio, w.is_watched, w.total_pnl, w.changes_count,
                w.convergence_trend
            FROM whales w
            WHERE w.total_value > 0
            ORDER BY w.total_value DESC
            LIMIT ?
        ''', (limit,))
        
        whales = []
        for row in cursor.fetchall():
            whales.append({
                'wallet': row['wallet'],
                'pseudonym': row['pseudonym'],
                'total_value': row['total_value'],
                'position_count': row['position_count'],
                'top5_ratio': row['top5_ratio'],
                'is_watched': row['is_watched'],
                'total_pnl': row['total_pnl'] or 0,
                'changes_count': row['changes_count'] or 0,
                'convergence_trend': row['convergence_trend'] or 'stable'
            })
        
        conn.close()
        return whales
        
    except Exception as e:
        print(f"❌ 数据库查询失败: {e}")
        return []


def format_top_whales_message(whales: list) -> str:
    """格式化Top鲸鱼消息"""
    if not whales:
        return "📊 *Top 鲸鱼排行榜*\n\n⚪ 暂无鲸鱼数据"
    
    lines = [
        f"🏆 *Top {len(whales)} 鲸鱼排行榜*",
        "",
        "_按持仓价值排序 | 实时数据_",
        "",
        "```",
        "=" * 50,
        "```",
        ""
    ]
    
    for i, w in enumerate(whales, 1):
        # 排名奖牌
        rank_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i:2d}.")
        
        # 盈亏状态
        pnl = w['total_pnl']
        if pnl > 1000:
            pnl_str = f"+${pnl:,.0f} 📈"
        elif pnl > 0:
            pnl_str = f"+${pnl:,.0f}"
        elif pnl < -1000:
            pnl_str = f"-${abs(pnl):,.0f} 📉"
        elif pnl < 0:
            pnl_str = f"-${abs(pnl):,.0f}"
        else:
            pnl_str = "$0"
        
        # 活跃度标记
        activity = "🔥" if w['changes_count'] > 0 else "⚪"
        
        # 重点标记
        watch_badge = "👑" if w['is_watched'] else ""
        
        # 集中度
        concentration = w['top5_ratio'] * 100
        if concentration >= 70:
            conc_emoji = "🔴"
        elif concentration >= 50:
            conc_emoji = "🟠"
        else:
            conc_emoji = "⚪"
        
        lines.append(f"""
{rank_emoji} {activity} {watch_badge} *{w['pseudonym'][:20]}*
   💰 持仓: ${w['total_value']:,.0f} | 📊 {w['position_count']}个市场
   📈 集中度: {conc_emoji} {concentration:.1f}% | P&L: {pnl_str}
   🔗 `{w['wallet'][:8]}...{w['wallet'][-6:]}`
""")
    
    lines.extend([
        "```",
        "=" * 50,
        "```",
        "",
        "💡 *解读:*",
        "• 🥇🥈🥉 = 持仓价值排名",
        "• 🔥 = 最近有交易活动",
        "• 👑 = 已加入重点关注列表",
        "• 🔴🟠⚪ = 集中度（高/中/低）",
        "",
        "📊 *建议:*",
        "关注高持仓 + 高活跃度的鲸鱼",
        "集中度高的鲸鱼信号更强",
        "",
        f"⏰ 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ])
    
    return "\n".join(lines)


def main():
    """发送 Top 10 鲸鱼排行榜"""
    print("🏆 生成 Top 10 鲸鱼排行榜（从数据库）...")
    
    # 获取 Top 10 鲸鱼
    top_whales = get_top_whales_from_db(limit=10)
    
    if not top_whales:
        print("⚪ 未获取到鲸鱼数据")
        return
    
    print(f"✅ 获取到 {len(top_whales)} 个鲸鱼数据")
    
    # 格式化消息
    message = format_top_whales_message(top_whales)
    
    # 打印到控制台
    print("\n" + "=" * 60)
    print(message)
    print("=" * 60 + "\n")
    
    # 发送到 Telegram
    if send_telegram_message(message):
        print("✅ 已发送 Top 10 鲸鱼排行榜到 Telegram")
    else:
        print("❌ 发送失败")


if __name__ == "__main__":
    main()
