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
    审查鲸鱼信号
    """
    concerns = []
    risk_points = 0
    max_points = 10
    
    w = analysis.get('info', {})
    
    # 1. 交易量检查
    total_volume = w.get('total_volume', 0)
    if total_volume < 10000:
        concerns.append(f"24h交易量较低 (${total_volume:,.0f})，信号可能不强")
        risk_points += 1
    
    # 2. 变动数量检查
    changes = len(analysis.get('changes', []))
    if changes == 0:
        concerns.append("无持仓变动，信号已过期")
        risk_points += 3
    elif changes > 10:
        concerns.append(f"变动过多 ({changes}个)，可能是噪音交易")
        risk_points += 2
    
    # 3. 持仓价值检查
    total_value = analysis.get('total_value', 0)
    if total_value < 50000:
        concerns.append(f"持仓价值较低 (${total_value:,.0f})，可能不是大鲸鱼")
        risk_points += 1
    
    # 4. 胜率检查（如果有数据）
    win_rate = w.get('win_rate', 0)
    if win_rate > 0 and win_rate < 0.55:
        concerns.append(f"历史胜率较低 ({win_rate:.1%})，跟随需谨慎")
        risk_points += 2
    
    # 计算风险分数
    risk_score = risk_points / max_points
    
    if risk_score < 0.3:
        risk_level = "low"
    elif risk_score < 0.6:
        risk_level = "medium"
    else:
        risk_level = "high"
    
    if risk_score < 0.3:
        recommendation = "✅ 信号较强，可以关注"
    elif risk_score < 0.6:
        recommendation = "⚠️ 信号一般，建议观望"
    else:
        recommendation = "❌ 信号较弱，不建议跟随"
    
    return {
        "approved": risk_score < RISK_REVIEW_THRESHOLD,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "concerns": concerns,
        "recommendation": recommendation
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
