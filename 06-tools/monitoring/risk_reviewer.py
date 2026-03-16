#!/usr/bin/env python3
"""
风险评估审查器
在执行套利操作前进行风险评估
"""

import os
from datetime import datetime

# 配置
RISK_REVIEW_ENABLED = os.getenv("RISK_REVIEW_ENABLED", "true").lower() == "true"
RISK_REVIEW_THRESHOLD = float(os.getenv("RISK_REVIEW_THRESHOLD", "0.5"))


def review_pair_cost_opportunity(opp: dict) -> dict:
    """
    审查 Pair Cost 套利机会
    
    Returns:
        {
            "approved": bool,
            "risk_score": float (0-1),
            "risk_level": "low/medium/high",
            "concerns": [str],
            "recommendation": str
        }
    """
    concerns = []
    risk_points = 0
    max_points = 10
    
    # 1. 利润空间检查
    profit_pct = opp.get('profit_pct', 0)
    if profit_pct < 1.0:
        concerns.append(f"利润空间过低 ({profit_pct:.2f}%)，可能无法覆盖手续费")
        risk_points += 3
    elif profit_pct < 2.0:
        concerns.append(f"利润空间较小 ({profit_pct:.2f}%)，需谨慎")
        risk_points += 1
    
    # 2. 流动性检查
    liquidity = opp.get('liquidity', 0)
    if liquidity < 5000:
        concerns.append(f"流动性不足 (${liquidity:,.0f})，可能难以成交")
        risk_points += 2
    elif liquidity < 10000:
        concerns.append(f"流动性一般 (${liquidity:,.0f})")
        risk_points += 1
    
    # 3. 结算时间检查
    end_date = opp.get('end_date', '')
    if end_date:
        try:
            from datetime import datetime
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            now = datetime.now(end.tzinfo)
            days_to_resolution = (end - now).days
            
            if days_to_resolution > 90:
                concerns.append(f"结算时间较远 ({days_to_resolution}天)，资金锁定时间长")
                risk_points += 2
            elif days_to_resolution < 1:
                concerns.append("即将结算，可能来不及操作")
                risk_points += 3
        except:
            pass
    
    # 4. 交易量检查
    volume = opp.get('volume', 0)
    if volume < 10000:
        concerns.append(f"交易量较低 (${volume:,.0f})，市场可能不活跃")
        risk_points += 1
    
    # 5. Pair Cost 检查
    pair_cost = opp.get('pair_cost', 1.0)
    if pair_cost > 0.995:
        concerns.append(f"Pair Cost 接近 $1.00 ({pair_cost:.4f})，利润空间极薄")
        risk_points += 2
    
    # 计算风险分数 (0-1, 越低越好)
    risk_score = risk_points / max_points
    
    # 确定风险等级
    if risk_score < 0.3:
        risk_level = "low"
    elif risk_score < 0.6:
        risk_level = "medium"
    else:
        risk_level = "high"
    
    # 生成建议
    if risk_score < 0.3:
        recommendation = "✅ 风险较低，可以考虑执行"
    elif risk_score < 0.6:
        recommendation = "⚠️ 中等风险，建议小仓位测试"
    else:
        recommendation = "❌ 风险较高，建议放弃或进一步分析"
    
    return {
        "approved": risk_score < RISK_REVIEW_THRESHOLD,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "concerns": concerns,
        "recommendation": recommendation
    }


def review_cross_market_opportunity(opp: dict) -> dict:
    """
    审查跨平台套利机会
    """
    concerns = []
    risk_points = 0
    max_points = 10
    
    # 1. 匹配度检查
    similarity = opp.get('similarity', 0)
    if similarity < 0.5:
        concerns.append(f"事件匹配度极低 ({similarity:.1%})，可能不是同一事件！")
        risk_points += 5
    elif similarity < 0.7:
        concerns.append(f"事件匹配度中等 ({similarity:.1%})，需人工确认")
        risk_points += 2
    
    # 2. 价差检查
    gap = opp.get('gap', 0)
    if gap < 0.05:
        concerns.append(f"价差较小 ({gap:.1%})，扣除费用后可能无利润")
        risk_points += 2
    elif gap > 0.20:
        concerns.append(f"价差过大 ({gap:.1%})，可能存在隐藏风险")
        risk_points += 2
    
    # 3. 平台流动性检查
    poly_liquidity = opp.get('polymarket', {}).get('liquidity', 0)
    if poly_liquidity < 10000:
        concerns.append(f"Polymarket 流动性不足 (${poly_liquidity:,.0f})")
        risk_points += 2
    
    # 4. 结算时间一致性检查
    # 跨平台套利要求结算时间一致
    
    # 计算风险分数
    risk_score = risk_points / max_points
    
    if risk_score < 0.3:
        risk_level = "low"
    elif risk_score < 0.6:
        risk_level = "medium"
    else:
        risk_level = "high"
    
    if risk_score < 0.3:
        recommendation = "✅ 风险较低，可以考虑执行"
    elif risk_score < 0.6:
        recommendation = "⚠️ 中等风险，建议人工确认后再执行"
    else:
        recommendation = "❌ 风险较高，建议放弃"
    
    return {
        "approved": risk_score < RISK_REVIEW_THRESHOLD,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "concerns": concerns,
        "recommendation": recommendation
    }


def review_whale_signal(analysis: dict) -> dict:
    """
    审查鲸鱼信号 - 修复版
    优化阈值和判断逻辑，适应Polymarket实际情况
    """
    concerns = []
    risk_points = 0
    max_points = 10
    
    w = analysis.get('info', {})
    
    # 数据一致性修复：明确区分不同数据来源
    # whale_info 中的 total_volume = 24h交易量（来自交易记录）
    # analysis 中的 total_value = 当前持仓价值（实时计算）
    total_volume = w.get('total_volume', 0)  # 24h交易量
    total_value = analysis.get('total_value', 0)  # 持仓价值（修正：从analysis获取）
    position_count = analysis.get('position_count', 0)  # 持仓市场数
    
    # 检查异常数据：持仓多但价值极低
    is_suspicious = analysis.get('is_suspicious', False)
    if is_suspicious or (position_count > 50 and total_value < 1000):
        return {
            "approved": False,
            "risk_score": 1.0,  # 最高风险
            "risk_level": "high",
            "concerns": [f"数据异常 ({position_count}个持仓但总价值仅${total_value:,.0f})，可能是已清仓或API错误"],
            "recommendation": "❌ 数据异常，建议忽略",
            "metrics": {
                "total_volume": total_volume,
                "total_value": total_value,
                "position_count": position_count,
                "changes": len(analysis.get('changes', [])),
                "change_ratio": 0,
                "is_suspicious": True
            }
        }
    
    # 1. 交易量检查 - 降低阈值，Polymarket活跃交易者标准
    if total_volume < 3000:  # 从10000降低到3000
        concerns.append(f"24h交易量较低 (${total_volume:,.0f})，信号可能不强")
        risk_points += 2
    elif total_volume < 8000:  # 新增中等区间
        concerns.append(f"24h交易量一般 (${total_volume:,.0f})")
        risk_points += 0.5
    
    # 2. 变动数量检查 - 优化判断逻辑，区分首次追踪和已有历史
    changes = analysis.get('changes', [])
    changes_count = len(changes)
    
    # 判断是否为首次追踪：所有变动都是"new"类型且比例接近100%
    is_first_time = False
    if changes_count > 0 and position_count > 0:
        new_positions = sum(1 for c in changes if c.get('type') == 'new')
        # 如果新建仓占绝大多数(>80%)且接近总持仓数，认为是首次追踪
        if new_positions / changes_count > 0.8 and changes_count / position_count > 0.8:
            is_first_time = True
    
    if changes_count == 0:
        concerns.append("无持仓变动，信号已过期")
        risk_points += 3
    elif is_first_time:
        # 首次追踪：显示为"新发现"而非"变动"
        concerns.append(f"新发现鲸鱼，首次追踪 ({changes_count}个持仓)")
        risk_points += 0.5  # 低风险，只是提示
    elif position_count > 0:  # 有持仓时计算变动比例
        change_ratio = changes_count / position_count
        if change_ratio > 0.5:  # 变动超过持仓数的50%才认为是高频
            concerns.append(f"变动比例较高 ({changes_count}/{position_count}个仓位，{change_ratio:.0%})")
            risk_points += 1.5
        elif changes_count > 15:  # 绝对数量阈值放宽
            concerns.append(f"变动数量较多 ({changes_count}个)，可能是活跃调仓")
            risk_points += 1
    elif changes_count > 15:  # 无position_count时的fallback
        concerns.append(f"变动数量较多 ({changes_count}个)")
        risk_points += 1
    
    # 3. 持仓价值检查 - 大幅降低阈值
    if total_value < 10000:  # 从50000降低到10000
        concerns.append(f"持仓价值较低 (${total_value:,.0f})，可能不是大鲸鱼")
        risk_points += 2
    elif total_value < 50000:  # 新增中等区间
        concerns.append(f"持仓价值中等 (${total_value:,.0f})")
        risk_points += 0.5
    
    # 4. 持仓分散度检查 - 新增：持仓过于分散可能是风险
    if position_count > 50:  # 持仓超过50个市场
        concerns.append(f"持仓过于分散 ({position_count}个市场)，可能缺乏重点")
        risk_points += 1
    
    # 5. 胜率检查（如果有数据）
    win_rate = w.get('win_rate', 0)
    if win_rate > 0 and win_rate < 0.55:
        concerns.append(f"历史胜率较低 ({win_rate:.1%})，跟随需谨慎")
        risk_points += 2
    elif win_rate >= 0.6:  # 高胜率加分
        risk_points -= 1
    
    # 计算风险分数（确保在0-1范围内）
    risk_score = max(0, min(1, risk_points / max_points))
    
    # 风险等级判断
    if risk_score < 0.25:
        risk_level = "low"
    elif risk_score < 0.5:
        risk_level = "medium"
    else:
        risk_level = "high"
    
    # 生成建议
    if risk_score < 0.25:
        recommendation = "✅ 信号较强，值得关注"
    elif risk_score < 0.5:
        recommendation = "⚠️ 信号一般，建议观望"
    else:
        recommendation = "❌ 信号较弱，不建议跟随"
    
    return {
        "approved": risk_score < RISK_REVIEW_THRESHOLD,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "concerns": concerns,
        "recommendation": recommendation,
        "metrics": {  # 返回实际使用的指标，便于调试
            "total_volume": total_volume,
            "total_value": total_value,
            "position_count": position_count,
            "changes": changes_count,
            "change_ratio": changes_count / position_count if position_count > 0 else 0
        }
    }


def format_risk_review(review: dict, opp_type: str = "套利") -> str:
    """格式化风险评估报告"""
    emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(review['risk_level'], "⚪")
    
    message = f"""
{emoji} *风险评估: {opp_type}*

┌─────────────────────────────┐
│  风险等级: {review['risk_level'].upper()}              │
│  风险分数: {review['risk_score']:.1%}              │
│  审核结果: {'✅ 通过' if review['approved'] else '❌ 未通过'}              │
└─────────────────────────────┘
"""
    
    if review['concerns']:
        message += "\n⚠️ *关注点:*\n"
        for i, concern in enumerate(review['concerns'], 1):
            message += f"{i}. {concern}\n"
    
    message += f"\n💡 *建议:*\n{review['recommendation']}\n"
    
    if not review['approved']:
        message += """
⚠️ *此机会未通过风险评估*
   建议放弃或进一步分析
"""
    
    return message


# 便捷函数
def should_review() -> bool:
    """检查是否启用风险评估"""
    return RISK_REVIEW_ENABLED


def get_review_threshold() -> float:
    """获取风险评估阈值"""
    return RISK_REVIEW_THRESHOLD
