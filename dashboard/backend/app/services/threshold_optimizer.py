"""
阈值自动优化服务
"""
import json
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from ..models.signal_tracking import SignalTracking


class ThresholdOptimizer:
    """阈值自动优化器"""
    
    # 可优化的阈值类型
    THRESHOLD_TYPES = {
        'whale_min_value': {
            'name': '鲸鱼最小持仓价值',
            'default': 100000,
            'min': 10000,
            'max': 1000000,
            'step': 10000
        },
        'whale_min_pnl': {
            'name': '鲸鱼最小盈亏',
            'default': 10000,
            'min': 1000,
            'max': 100000,
            'step': 5000
        },
        'arbitrage_min_profit': {
            'name': '套利最小利润',
            'default': 0.02,
            'min': 0.005,
            'max': 0.1,
            'step': 0.005
        },
        'arbitrage_min_liquidity': {
            'name': '套利最小流动性',
            'default': 50000,
            'min': 10000,
            'max': 500000,
            'step': 10000
        },
        'semantic_confidence': {
            'name': '语义套利置信度',
            'default': 0.7,
            'min': 0.3,
            'max': 0.95,
            'step': 0.05
        },
        'news_impact_threshold': {
            'name': '新闻影响阈值',
            'default': 0.5,
            'min': 0.1,
            'max': 0.9,
            'step': 0.05
        }
    }
    
    @staticmethod
    def analyze_threshold_impact(threshold_type: str, test_values: List[float] = None,
                                  days: int = 30) -> Dict:
        """
        分析不同阈值对信号质量的影响
        
        Args:
            threshold_type: 阈值类型
            test_values: 要测试的阈值列表，None则使用默认值范围
            days: 分析历史数据天数
        
        Returns:
            各阈值的表现分析
        """
        config = ThresholdOptimizer.THRESHOLD_TYPES.get(threshold_type)
        if not config:
            return {'error': f'Unknown threshold type: {threshold_type}'}
        
        # 如果没有提供测试值，生成测试范围
        if test_values is None:
            test_values = []
            current = config['min']
            while current <= config['max']:
                test_values.append(current)
                current += config['step']
        
        # 获取历史信号数据
        signals = SignalTracking.get_signals(days=days, limit=5000)
        
        # 根据阈值类型过滤相关信号
        relevant_signals = ThresholdOptimizer._filter_relevant_signals(
            signals, threshold_type
        )
        
        results = []
        for test_value in test_values:
            # 模拟该阈值下的信号筛选
            filtered_signals = ThresholdOptimizer._apply_threshold(
                relevant_signals, threshold_type, test_value
            )
            
            # 计算表现指标
            performance = ThresholdOptimizer._calculate_performance(filtered_signals)
            
            results.append({
                'threshold': test_value,
                'signal_count': performance['total'],
                'win_rate': performance['win_rate'],
                'avg_pnl': performance['avg_pnl'],
                'total_pnl': performance['total_pnl'],
                'sharpe': performance['sharpe_ratio'],
                'score': ThresholdOptimizer._calculate_score(performance)
            })
        
        # 找出最优阈值
        best_result = max(results, key=lambda x: x['score']) if results else None
        
        return {
            'threshold_type': threshold_type,
            'threshold_name': config['name'],
            'analysis_period_days': days,
            'test_values': test_values,
            'results': results,
            'optimal_threshold': best_result['threshold'] if best_result else None,
            'optimal_performance': best_result if best_result else None,
            'current_threshold': config['default']
        }
    
    @staticmethod
    def optimize_all_thresholds(days: int = 30) -> Dict:
        """
        优化所有阈值
        
        Returns:
            各阈值的最优值建议
        """
        optimization_results = {}
        recommendations = []
        
        for threshold_type in ThresholdOptimizer.THRESHOLD_TYPES:
            result = ThresholdOptimizer.analyze_threshold_impact(
                threshold_type, days=days
            )
            
            if 'error' not in result:
                optimization_results[threshold_type] = result
                
                optimal = result.get('optimal_threshold')
                current = result.get('current_threshold')
                
                if optimal and current and abs(optimal - current) > 0.001:
                    recommendations.append({
                        'threshold_type': threshold_type,
                        'threshold_name': result['threshold_name'],
                        'current_value': current,
                        'recommended_value': optimal,
                        'expected_improvement': ThresholdOptimizer._estimate_improvement(
                            result['results'], current, optimal
                        )
                    })
        
        return {
            'optimization_date': datetime.now().isoformat(),
            'analysis_period_days': days,
            'results': optimization_results,
            'recommendations': recommendations,
            'recommendation_count': len(recommendations)
        }
    
    @staticmethod
    def apply_optimization(threshold_type: str, new_value: float,
                          auto_optimized: bool = False,
                          reason: str = None) -> Dict:
        """
        应用阈值优化
        
        Args:
            threshold_type: 阈值类型
            new_value: 新阈值
            auto_optimized: 是否自动优化
            reason: 变更原因
        
        Returns:
            应用结果
        """
        config = ThresholdOptimizer.THRESHOLD_TYPES.get(threshold_type)
        if not config:
            return {'success': False, 'error': f'Unknown threshold type: {threshold_type}'}
        
        # 验证新值在有效范围内
        if new_value < config['min'] or new_value > config['max']:
            return {
                'success': False,
                'error': f'Value {new_value} out of range [{config["min"]}, {config["max"]}]'
            }
        
        # 获取当前阈值
        current_thresholds = SignalTracking.get_current_thresholds()
        old_value = current_thresholds.get(threshold_type, config['default'])
        
        # 记录性能（优化前）
        performance_before = ThresholdOptimizer._get_current_performance(threshold_type)
        
        # 记录阈值变更
        change_id = SignalTracking.record_threshold_change(
            threshold_type=threshold_type,
            old_value=old_value,
            new_value=new_value,
            change_reason=reason or f'{"Auto" if auto_optimized else "Manual"} optimization',
            auto_optimized=auto_optimized,
            optimization_params={
                'method': 'historical_analysis',
                'analysis_days': 30
            },
            performance_before=performance_before
        )
        
        return {
            'success': True,
            'change_id': change_id,
            'threshold_type': threshold_type,
            'old_value': old_value,
            'new_value': new_value,
            'auto_optimized': auto_optimized
        }
    
    @staticmethod
    def get_optimization_history(threshold_type: str = None, limit: int = 20) -> List[Dict]:
        """获取优化历史"""
        history = SignalTracking.get_threshold_history(
            threshold_type=threshold_type,
            limit=limit
        )
        
        # 添加额外分析
        for item in history:
            if item.get('parsed_performance_before') and item.get('parsed_performance_after'):
                before = item['parsed_performance_before']
                after = item['parsed_performance_after']
                item['improvement'] = {
                    'win_rate_change': after.get('win_rate', 0) - before.get('win_rate', 0),
                    'pnl_change': after.get('total_pnl', 0) - before.get('total_pnl', 0)
                }
        
        return history
    
    @staticmethod
    def _filter_relevant_signals(signals: List[Dict], threshold_type: str) -> List[Dict]:
        """根据阈值类型过滤相关信号"""
        type_mapping = {
            'whale_min_value': 'whale',
            'whale_min_pnl': 'whale',
            'arbitrage_min_profit': 'arbitrage',
            'arbitrage_min_liquidity': 'arbitrage',
            'semantic_confidence': 'semantic',
            'news_impact_threshold': 'news'
        }
        
        relevant_type = type_mapping.get(threshold_type)
        if not relevant_type:
            return signals
        
        return [s for s in signals if s.get('signal_type') == relevant_type]
    
    @staticmethod
    def _apply_threshold(signals: List[Dict], threshold_type: str, value: float) -> List[Dict]:
        """模拟应用阈值筛选信号"""
        # 这里简化处理，实际应根据信号元数据中的具体值进行筛选
        # 例如：如果信号包含持仓价值信息，筛选出大于阈值的信号
        
        filtered = []
        for signal in signals:
            metadata = signal.get('parsed_metadata', {})
            
            # 根据阈值类型检查信号是否满足条件
            if threshold_type == 'whale_min_value':
                position_value = metadata.get('position_value', 0)
                if position_value >= value:
                    filtered.append(signal)
            elif threshold_type == 'arbitrage_min_profit':
                profit_potential = metadata.get('profit_potential', 0)
                if profit_potential >= value:
                    filtered.append(signal)
            elif threshold_type == 'semantic_confidence':
                confidence = signal.get('confidence', 0)
                if confidence >= value:
                    filtered.append(signal)
            else:
                # 默认保留信号
                filtered.append(signal)
        
        return filtered
    
    @staticmethod
    def _calculate_performance(signals: List[Dict]) -> Dict:
        """计算信号集合的表现指标"""
        if not signals:
            return {
                'total': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0,
                'avg_pnl': 0,
                'total_pnl': 0,
                'sharpe_ratio': 0
            }
        
        wins = sum(1 for s in signals if s.get('actual_result') == 'win')
        losses = sum(1 for s in signals if s.get('actual_result') == 'loss')
        resolved = wins + losses
        
        pnls = [s.get('pnl_percent', 0) or 0 for s in signals 
                if s.get('actual_result') in ('win', 'loss')]
        
        total_pnl = sum(pnls) if pnls else 0
        avg_pnl = total_pnl / len(pnls) if pnls else 0
        
        # 计算夏普比率（简化版）
        if len(pnls) > 1:
            std_pnl = np.std(pnls) if np.std(pnls) > 0 else 1
            sharpe = avg_pnl / std_pnl
        else:
            sharpe = 0
        
        return {
            'total': len(signals),
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / resolved * 100) if resolved > 0 else 0,
            'avg_pnl': avg_pnl,
            'total_pnl': total_pnl,
            'sharpe_ratio': sharpe
        }
    
    @staticmethod
    def _calculate_score(performance: Dict) -> float:
        """计算综合评分"""
        # 综合考虑胜率、盈亏比、信号数量
        win_rate_score = performance['win_rate'] * 0.4
        pnl_score = min(performance['avg_pnl'] * 10, 50) * 0.4  # 限制最大影响
        
        # 信号数量惩罚（太少或太多都不好）
        signal_count = performance['total']
        if signal_count < 5:
            count_score = signal_count * 2  # 鼓励更多信号
        elif signal_count > 100:
            count_score = max(0, 20 - (signal_count - 100) * 0.1)  # 太多信号扣分
        else:
            count_score = 10
        
        return win_rate_score + pnl_score + count_score
    
    @staticmethod
    def _estimate_improvement(results: List[Dict], current: float, optimal: float) -> Dict:
        """估计优化后的改进"""
        current_result = next((r for r in results if abs(r['threshold'] - current) < 0.001), None)
        optimal_result = next((r for r in results if abs(r['threshold'] - optimal) < 0.001), None)
        
        if not current_result or not optimal_result:
            return {'error': 'Cannot estimate improvement'}
        
        return {
            'win_rate_improvement': optimal_result['win_rate'] - current_result['win_rate'],
            'pnl_improvement': optimal_result['total_pnl'] - current_result['total_pnl'],
            'signal_count_change': optimal_result['signal_count'] - current_result['signal_count']
        }
    
    @staticmethod
    def _get_current_performance(threshold_type: str) -> Dict:
        """获取当前性能"""
        # 获取相关类型的信号统计
        type_mapping = {
            'whale_min_value': 'whale',
            'whale_min_pnl': 'whale',
            'arbitrage_min_profit': 'arbitrage',
            'arbitrage_min_liquidity': 'arbitrage',
            'semantic_confidence': 'semantic',
            'news_impact_threshold': 'news'
        }
        
        signal_type = type_mapping.get(threshold_type)
        if signal_type:
            stats = SignalTracking.get_signal_stats(signal_type=signal_type, days=30)
        else:
            stats = SignalTracking.get_signal_stats(days=30)
        
        return {
            'win_rate': stats.get('win_rate', 0),
            'total_pnl': stats.get('total_pnl', 0),
            'avg_pnl': stats.get('avg_pnl', 0),
            'total_signals': stats.get('total', 0)
        }
    
    @staticmethod
    def get_threshold_config(threshold_type: str = None) -> Dict:
        """获取阈值配置"""
        if threshold_type:
            config = ThresholdOptimizer.THRESHOLD_TYPES.get(threshold_type)
            if config:
                current = SignalTracking.get_current_thresholds()
                return {
                    'type': threshold_type,
                    **config,
                    'current_value': current.get(threshold_type, config['default'])
                }
            return {'error': 'Unknown threshold type'}
        
        # 返回所有配置
        current = SignalTracking.get_current_thresholds()
        configs = {}
        for t_type, t_config in ThresholdOptimizer.THRESHOLD_TYPES.items():
            configs[t_type] = {
                **t_config,
                'current_value': current.get(t_type, t_config['default'])
            }
        return configs
