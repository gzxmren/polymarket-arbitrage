"""
质量报告服务 - 生成信号质量报告
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from ..models.signal_tracking import SignalTracking


class QualityReportService:
    """质量报告生成器"""
    
    @staticmethod
    def generate_weekly_report() -> Dict:
        """生成每周质量报告"""
        # 计算上周的时间范围
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        return QualityReportService.generate_report(
            report_type='weekly',
            start_date=start_date,
            end_date=end_date
        )
    
    @staticmethod
    def generate_monthly_report() -> Dict:
        """生成每月质量报告"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        return QualityReportService.generate_report(
            report_type='monthly',
            start_date=start_date,
            end_date=end_date
        )
    
    @staticmethod
    def generate_report(report_type: str, start_date: datetime, end_date: datetime) -> Dict:
        """生成质量报告"""
        # 获取总体统计
        days = (end_date - start_date).days
        overall_stats = SignalTracking.get_signal_stats(days=days)
        
        # 按类型分析
        type_analysis = {}
        for type_stat in overall_stats.get('by_type', []):
            signal_type = type_stat.get('signal_type')
            type_analysis[signal_type] = {
                'total': type_stat.get('count', 0),
                'wins': type_stat.get('wins', 0),
                'losses': type_stat.get('losses', 0),
                'win_rate': type_stat.get('win_rate', 0),
                'avg_pnl': type_stat.get('avg_pnl', 0) or 0
            }
        
        # 找出最佳和最差策略
        best_strategy = None
        worst_strategy = None
        best_rate = -1
        worst_rate = 101
        
        for signal_type, stats in type_analysis.items():
            if stats['total'] >= 5:  # 至少5个信号才考虑
                win_rate = stats['win_rate']
                if win_rate > best_rate:
                    best_rate = win_rate
                    best_strategy = signal_type
                if win_rate < worst_rate:
                    worst_rate = win_rate
                    worst_strategy = signal_type
        
        # 计算置信度分布
        confidence_distribution = QualityReportService._analyze_confidence_distribution(days)
        
        # 计算时间趋势
        time_trend = QualityReportService._analyze_time_trend(days)
        
        # 生成详细报告数据
        report_data = {
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat(),
                'days': days
            },
            'summary': {
                'total_signals': overall_stats.get('total', 0),
                'win_count': overall_stats.get('wins', 0),
                'loss_count': overall_stats.get('losses', 0),
                'pending_count': overall_stats.get('pending', 0),
                'expired_count': overall_stats.get('expired', 0),
                'win_rate': overall_stats.get('win_rate', 0),
                'avg_pnl': overall_stats.get('avg_pnl', 0) or 0,
                'total_pnl': overall_stats.get('total_pnl', 0) or 0
            },
            'by_type': type_analysis,
            'confidence_distribution': confidence_distribution,
            'time_trend': time_trend,
            'recommendations': QualityReportService._generate_recommendations(
                type_analysis, overall_stats
            )
        }
        
        # 保存报告到数据库
        report_id = SignalTracking.save_quality_report(
            report_type=report_type,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            total_signals=report_data['summary']['total_signals'],
            win_count=report_data['summary']['win_count'],
            loss_count=report_data['summary']['loss_count'],
            pending_count=report_data['summary']['pending_count'],
            win_rate=report_data['summary']['win_rate'],
            avg_pnl=report_data['summary']['avg_pnl'],
            total_pnl=report_data['summary']['total_pnl'],
            best_strategy=best_strategy,
            worst_strategy=worst_strategy,
            report_data=report_data
        )
        
        return {
            'report_id': report_id,
            'report_type': report_type,
            **report_data
        }
    
    @staticmethod
    def _analyze_confidence_distribution(days: int) -> Dict:
        """分析置信度分布"""
        # 获取所有已解决的信号
        signals = SignalTracking.get_signals(
            result='win',
            days=days,
            limit=1000
        ) + SignalTracking.get_signals(
            result='loss',
            days=days,
            limit=1000
        )
        
        # 按置信度区间分组
        distribution = {
            'high_confidence': {'total': 0, 'wins': 0, 'win_rate': 0},  # > 0.7
            'medium_confidence': {'total': 0, 'wins': 0, 'win_rate': 0},  # 0.4 - 0.7
            'low_confidence': {'total': 0, 'wins': 0, 'win_rate': 0}  # < 0.4
        }
        
        for signal in signals:
            confidence = signal.get('confidence') or 0
            is_win = signal.get('actual_result') == 'win'
            
            if confidence > 0.7:
                bucket = 'high_confidence'
            elif confidence >= 0.4:
                bucket = 'medium_confidence'
            else:
                bucket = 'low_confidence'
            
            distribution[bucket]['total'] += 1
            if is_win:
                distribution[bucket]['wins'] += 1
        
        # 计算胜率
        for bucket in distribution:
            total = distribution[bucket]['total']
            if total > 0:
                distribution[bucket]['win_rate'] = distribution[bucket]['wins'] / total * 100
        
        return distribution
    
    @staticmethod
    def _analyze_time_trend(days: int) -> List[Dict]:
        """分析时间趋势（按天）"""
        # 获取最近days天的信号
        signals = SignalTracking.get_signals(days=days, limit=5000)
        
        # 按天分组
        daily_stats = {}
        for signal in signals:
            created_at = signal.get('created_at', '')
            if created_at:
                date_key = created_at[:10]  # YYYY-MM-DD
                if date_key not in daily_stats:
                    daily_stats[date_key] = {'total': 0, 'wins': 0, 'losses': 0}
                
                daily_stats[date_key]['total'] += 1
                result = signal.get('actual_result')
                if result == 'win':
                    daily_stats[date_key]['wins'] += 1
                elif result == 'loss':
                    daily_stats[date_key]['losses'] += 1
        
        # 转换为列表并排序
        trend = []
        for date, stats in sorted(daily_stats.items()):
            resolved = stats['wins'] + stats['losses']
            trend.append({
                'date': date,
                'total': stats['total'],
                'wins': stats['wins'],
                'losses': stats['losses'],
                'win_rate': stats['wins'] / resolved * 100 if resolved > 0 else 0
            })
        
        return trend
    
    @staticmethod
    def _generate_recommendations(type_analysis: Dict, overall_stats: Dict) -> List[str]:
        """生成策略建议"""
        recommendations = []
        
        overall_win_rate = overall_stats.get('win_rate', 0)
        
        # 基于整体胜率建议
        if overall_win_rate < 40:
            recommendations.append(f"整体胜率较低 ({overall_win_rate:.1f}%)，建议审查信号生成逻辑和阈值设置")
        elif overall_win_rate > 60:
            recommendations.append(f"整体胜率良好 ({overall_win_rate:.1f}%)，可考虑适当增加仓位")
        
        # 基于各策略表现建议
        for signal_type, stats in type_analysis.items():
            if stats['total'] >= 5:
                if stats['win_rate'] < 35:
                    recommendations.append(f"{signal_type} 策略胜率过低 ({stats['win_rate']:.1f}%)，建议暂停或优化")
                elif stats['win_rate'] > 65:
                    recommendations.append(f"{signal_type} 策略表现优异 ({stats['win_rate']:.1f}%)，可增加权重")
                
                if stats['avg_pnl'] < -5:
                    recommendations.append(f"{signal_type} 策略平均亏损较大 ({stats['avg_pnl']:.1f}%)，建议调整止损设置")
        
        # 基于信号数量建议
        total_signals = overall_stats.get('total', 0)
        if total_signals < 10:
            recommendations.append("信号数量较少，建议检查监控频率或放宽阈值")
        elif total_signals > 100:
            recommendations.append("信号数量较多，建议提高阈值以减少噪音")
        
        return recommendations
    
    @staticmethod
    def get_report_summary(report_id: int) -> Optional[Dict]:
        """获取报告摘要"""
        reports = SignalTracking.get_quality_reports(limit=100)
        for report in reports:
            if report['id'] == report_id:
                return {
                    'id': report['id'],
                    'type': report['report_type'],
                    'period': f"{report['start_date'][:10]} to {report['end_date'][:10]}",
                    'win_rate': report['win_rate'],
                    'total_pnl': report['total_pnl'],
                    'best_strategy': report['best_strategy'],
                    'worst_strategy': report['worst_strategy']
                }
        return None
