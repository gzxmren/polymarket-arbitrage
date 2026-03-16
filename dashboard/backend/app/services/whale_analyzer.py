#!/usr/bin/env python3
"""
鲸鱼分析服务
提供实时分析意见
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / '06-tools/analysis'))

from app.models.database import db


class WhaleAnalyzer:
    """鲸鱼分析服务"""
    
    def __init__(self):
        self.db = db
    
    def analyze_whale(self, wallet: str) -> Dict[str, Any]:
        """
        分析单个鲸鱼
        每次调用都实时计算最新结果
        """
        # 获取最新数据
        whale = self._get_whale_data(wallet)
        if not whale:
            return {"error": "Whale not found"}
        
        positions = self._get_positions(wallet)
        changes = self._get_recent_changes(wallet, hours=24)
        
        # 计算各项指标
        analysis = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "signal_strength": self._calculate_signal_strength(whale, changes),
            "strategy_assessment": self._assess_strategy(whale),
            "pnl_status": self._calculate_pnl_status(whale),
            "recommendation": self._generate_recommendation(whale, changes),
            "interpretation": self._generate_interpretation(whale, changes, positions),
            "risk_warning": self._check_risks(whale, positions),
            "dimensions": {
                "fund_strength": self._score_fund_strength(whale),
                "activity": self._score_activity(changes),
                "concentration": self._score_concentration(whale),
                "profitability": self._score_profitability(whale)
            }
        }
        
        # 计算综合评分
        analysis["composite_score"] = self._calculate_composite_score(analysis["dimensions"])
        analysis["copy_score"] = self._calculate_copy_score(whale, changes, analysis["dimensions"])
        
        return analysis
    
    def _get_whale_data(self, wallet: str) -> Dict:
        """获取鲸鱼基本信息"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        # 确保 wallet 是字符串
        wallet_str = str(wallet).lower()
        cursor.execute('SELECT * FROM whales WHERE LOWER(wallet) = ?', (wallet_str,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def _get_positions(self, wallet: str) -> List[Dict]:
        """获取持仓明细"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM positions 
            WHERE wallet = ? 
            ORDER BY value DESC
        ''', (wallet,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def _get_recent_changes(self, wallet: str, hours: int = 24) -> List[Dict]:
        """获取最近变动"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        cursor.execute('''
            SELECT * FROM changes 
            WHERE wallet = ? AND timestamp > ?
            ORDER BY timestamp DESC
        ''', (wallet, since))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def _calculate_signal_strength(self, whale: Dict, changes: List[Dict]) -> Dict:
        """计算信号强度"""
        changes_count = len(changes)
        
        if changes_count >= 5:
            return {"level": "extreme", "score": 95, "emoji": "🔴", "desc": "极强", "changes_count": changes_count}
        elif changes_count >= 3:
            return {"level": "high", "score": 80, "emoji": "🟠", "desc": "强", "changes_count": changes_count}
        elif changes_count >= 1:
            return {"level": "medium", "score": 60, "emoji": "🟡", "desc": "中等", "changes_count": changes_count}
        else:
            return {"level": "low", "score": 40, "emoji": "⚪", "desc": "弱", "changes_count": 0}
    
    def _assess_strategy(self, whale: Dict) -> Dict:
        """评估策略"""
        top5_ratio = whale.get('top5_ratio', 0)
        
        if top5_ratio >= 0.7:
            return {
                "concentration_level": "高度集中",
                "top5_ratio": top5_ratio,
                "desc": "策略明确，值得跟踪",
                "emoji": "🎯"
            }
        elif top5_ratio >= 0.5:
            return {
                "concentration_level": "开始集中",
                "top5_ratio": top5_ratio,
                "desc": "策略逐渐明确",
                "emoji": "📊"
            }
        elif top5_ratio >= 0.3:
            return {
                "concentration_level": "适度分散",
                "top5_ratio": top5_ratio,
                "desc": "正在形成重点",
                "emoji": "📈"
            }
        else:
            return {
                "concentration_level": "高度分散",
                "top5_ratio": top5_ratio,
                "desc": "策略不明确，观望",
                "emoji": "⚪"
            }
    
    def _calculate_pnl_status(self, whale: Dict) -> Dict:
        """计算盈亏状态"""
        pnl = whale.get('total_pnl', 0)
        value = whale.get('total_value', 1)
        pnl_percent = (pnl / value * 100) if value > 0 else 0
        
        return {
            "value": pnl,
            "percent": round(pnl_percent, 2),
            "emoji": "📈" if pnl > 0 else "📉" if pnl < 0 else "➖",
            "is_positive": pnl > 0,
            "is_negative": pnl < 0
        }
    
    def _generate_recommendation(self, whale: Dict, changes: List[Dict]) -> Dict:
        """生成操作建议"""
        top5_ratio = whale.get('top5_ratio', 0)
        changes_count = len(changes)
        pnl = whale.get('total_pnl', 0)
        
        # 高度集中 + 活跃 + 盈利
        if top5_ratio >= 0.6 and changes_count >= 3 and pnl > 0:
            return {
                "action": "重点关注",
                "priority": "high",
                "emoji": "⭐",
                "desc": "策略明确且盈利，建议重点关注"
            }
        # 高度集中 + 活跃
        elif top5_ratio >= 0.6 and changes_count >= 3:
            return {
                "action": "关注",
                "priority": "medium",
                "emoji": "👀",
                "desc": "策略明确，观察后续表现"
            }
        # 开始集中
        elif top5_ratio >= 0.5:
            return {
                "action": "观察",
                "priority": "low",
                "emoji": "🔍",
                "desc": "策略正在形成，等待收敛信号"
            }
        # 分散
        else:
            return {
                "action": "观望",
                "priority": "low",
                "emoji": "⏸️",
                "desc": "策略不明确，暂不关注"
            }
    
    def _generate_interpretation(self, whale: Dict, changes: List[Dict], positions: List[Dict]) -> str:
        """生成解读"""
        parts = []
        
        # 活跃度解读
        changes_count = len(changes)
        if changes_count >= 5:
            parts.append(f"该鲸鱼近期非常活跃，24小时内变动{changes_count}次")
        elif changes_count >= 3:
            parts.append(f"该鲸鱼近期较为活跃，24小时内变动{changes_count}次")
        elif changes_count >= 1:
            parts.append(f"该鲸鱼近期有少量变动，24小时内变动{changes_count}次")
        else:
            parts.append("该鲸鱼近期暂无变动")
        
        # 集中度解读
        top5_ratio = whale.get('top5_ratio', 0)
        if top5_ratio >= 0.7:
            parts.append("持仓高度集中，策略非常明确")
        elif top5_ratio >= 0.5:
            parts.append("持仓开始集中，策略逐渐明确")
        else:
            parts.append("持仓较为分散，策略尚不明确")
        
        # 盈亏解读
        pnl = whale.get('total_pnl', 0)
        if pnl > 10000:
            parts.append(f"，总盈亏为+${pnl:,.0f}，处于盈利状态")
        elif pnl > 0:
            parts.append(f"，总盈亏为+${pnl:,.0f}，小幅盈利")
        elif pnl < -10000:
            parts.append(f"，总盈亏为${pnl:,.0f}，亏损较大")
        elif pnl < 0:
            parts.append(f"，总盈亏为${pnl:,.0f}，小幅亏损")
        else:
            parts.append("，盈亏平衡")
        
        # 建议
        if top5_ratio >= 0.6 and changes_count >= 3:
            parts.append("。建议重点关注其后续动向，可作为市场情绪参考")
        elif top5_ratio >= 0.5:
            parts.append("。建议观察其是否进一步收敛")
        else:
            parts.append("。建议等待策略明确后再关注")
        
        return "，".join(parts)
    
    def _check_risks(self, whale: Dict, positions: List[Dict]) -> str:
        """检查风险"""
        risks = []
        
        # 集中度风险
        top5_ratio = whale.get('top5_ratio', 0)
        if top5_ratio >= 0.9:
            risks.append("持仓过度集中(90%+)，注意单一事件风险")
        elif top5_ratio >= 0.8:
            risks.append("持仓高度集中(80%+)，注意风险分散")
        
        # 盈亏风险
        pnl = whale.get('total_pnl', 0)
        value = whale.get('total_value', 1)
        if value > 0 and pnl / value < -0.2:
            risks.append("亏损超过20%，注意止损风险")
        
        # 持仓数量风险
        if len(positions) > 100:
            risks.append("持仓市场数过多(100+)，可能过度分散")
        
        return "；".join(risks) if risks else "暂无显著风险"
    
    def _score_fund_strength(self, whale: Dict) -> Dict:
        """评分：资金实力"""
        value = whale.get('total_value', 0)
        if value >= 500000:
            return {"score": 100, "desc": f"持仓${value/10000:.0f}万", "level": "极高"}
        elif value >= 200000:
            return {"score": 90, "desc": f"持仓${value/10000:.0f}万", "level": "很高"}
        elif value >= 100000:
            return {"score": 80, "desc": f"持仓${value/10000:.0f}万", "level": "高"}
        elif value >= 50000:
            return {"score": 70, "desc": f"持仓${value/1000:.0f}k", "level": "中高"}
        elif value >= 10000:
            return {"score": 60, "desc": f"持仓${value/1000:.0f}k", "level": "中等"}
        else:
            return {"score": 40, "desc": f"持仓${value:.0f}", "level": "较低"}
    
    def _score_activity(self, changes: List[Dict]) -> Dict:
        """评分：活跃度"""
        count = len(changes)
        if count >= 10:
            return {"score": 100, "desc": f"24h变动{count}次", "level": "极高"}
        elif count >= 5:
            return {"score": 90, "desc": f"24h变动{count}次", "level": "很高"}
        elif count >= 3:
            return {"score": 80, "desc": f"24h变动{count}次", "level": "高"}
        elif count >= 1:
            return {"score": 60, "desc": f"24h变动{count}次", "level": "中等"}
        else:
            return {"score": 30, "desc": "24h无变动", "level": "低"}
    
    def _score_concentration(self, whale: Dict) -> Dict:
        """评分：策略集中度"""
        ratio = whale.get('top5_ratio', 0)
        if ratio >= 0.7:
            return {"score": 95, "desc": f"Top5占{ratio:.0%}", "level": "高度集中"}
        elif ratio >= 0.5:
            return {"score": 85, "desc": f"Top5占{ratio:.0%}", "level": "开始集中"}
        elif ratio >= 0.3:
            return {"score": 70, "desc": f"Top5占{ratio:.0%}", "level": "适度分散"}
        else:
            return {"score": 50, "desc": f"Top5占{ratio:.0%}", "level": "高度分散"}
    
    def _score_profitability(self, whale: Dict) -> Dict:
        """评分：盈利能力"""
        pnl = whale.get('total_pnl', 0)
        value = whale.get('total_value', 1)
        if value <= 0:
            return {"score": 50, "desc": "盈亏未知", "level": "未知"}
        
        ratio = pnl / value
        if ratio >= 0.1:
            return {"score": 95, "desc": f"盈利{ratio:.1%}", "level": "优秀"}
        elif ratio >= 0.05:
            return {"score": 85, "desc": f"盈利{ratio:.1%}", "level": "良好"}
        elif ratio >= 0:
            return {"score": 75, "desc": f"盈利{ratio:.1%}", "level": "盈利"}
        elif ratio >= -0.05:
            return {"score": 60, "desc": f"亏损{abs(ratio):.1%}", "level": "小幅亏损"}
        elif ratio >= -0.1:
            return {"score": 45, "desc": f"亏损{abs(ratio):.1%}", "level": "亏损"}
        else:
            return {"score": 30, "desc": f"亏损{abs(ratio):.1%}", "level": "大幅亏损"}
    
    def _calculate_composite_score(self, dimensions: Dict) -> int:
        """计算综合评分"""
        weights = {
            "fund_strength": 0.40,
            "activity": 0.25,
            "concentration": 0.20,
            "profitability": 0.15
        }
        
        score = 0
        for key, weight in weights.items():
            score += dimensions[key]["score"] * weight
        
        return round(score)
    
    def _calculate_copy_score(self, whale: Dict, changes: List[Dict], dimensions: Dict) -> int:
        """计算智能跟单评分"""
        score = 0
        
        # 策略明确度 (30%)
        top5 = whale.get('top5_ratio', 0)
        if top5 >= 0.4 and top5 <= 0.8:
            score += 30
        elif top5 > 0.8:
            score += 20
        else:
            score += top5 * 50
        
        # 盈利状态 (25%)
        if dimensions["profitability"]["score"] >= 75:
            score += 25
        else:
            score += dimensions["profitability"]["score"] * 0.25
        
        # 活跃度适中 (25%)
        changes_count = len(changes)
        if changes_count >= 1 and changes_count <= 5:
            score += 25
        elif changes_count > 5:
            score += 15
        else:
            score += changes_count * 5
        
        # 资金适中 (20%)
        value = whale.get('total_value', 0)
        if value >= 50000 and value <= 500000:
            score += 20
        elif value > 500000:
            score += 15
        else:
            score += (value / 50000) * 20
        
        return round(score)


# 全局分析器实例
analyzer = WhaleAnalyzer()
