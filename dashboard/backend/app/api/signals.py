"""
信号追踪 API
"""
from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
from ..models.signal_tracking import SignalTracking
from ..services.quality_report import QualityReportService
from ..services.threshold_optimizer import ThresholdOptimizer

signals_bp = Blueprint('signals', __name__)


@signals_bp.route('/stats', methods=['GET'])
def get_signal_stats():
    """获取信号统计"""
    signal_type = request.args.get('type', None)
    days = request.args.get('days', 30, type=int)
    
    stats = SignalTracking.get_signal_stats(signal_type=signal_type, days=days)
    
    return jsonify({
        'success': True,
        'data': stats,
        'filters': {
            'type': signal_type,
            'days': days
        }
    })


@signals_bp.route('/by-type', methods=['GET'])
def get_signals_by_type():
    """按类型获取信号统计"""
    days = request.args.get('days', 30, type=int)
    
    # 获取所有类型的统计
    all_stats = SignalTracking.get_signal_stats(days=days)
    
    return jsonify({
        'success': True,
        'data': all_stats.get('by_type', []),
        'summary': {
            'total': all_stats.get('total', 0),
            'win_rate': all_stats.get('win_rate', 0),
            'total_pnl': all_stats.get('total_pnl', 0)
        }
    })


@signals_bp.route('/list', methods=['GET'])
def get_signals_list():
    """获取信号列表"""
    signal_type = request.args.get('type', None)
    result = request.args.get('result', None)  # 'win', 'loss', 'pending', 'expired'
    days = request.args.get('days', 30, type=int)
    limit = request.args.get('limit', 100, type=int)
    
    signals = SignalTracking.get_signals(
        signal_type=signal_type,
        result=result,
        days=days,
        limit=limit
    )
    
    return jsonify({
        'success': True,
        'count': len(signals),
        'data': signals
    })


@signals_bp.route('/pending', methods=['GET'])
def get_pending_signals():
    """获取待处理信号"""
    signal_type = request.args.get('type', None)
    limit = request.args.get('limit', 100, type=int)
    
    signals = SignalTracking.get_pending_signals(
        signal_type=signal_type,
        limit=limit
    )
    
    return jsonify({
        'success': True,
        'count': len(signals),
        'data': signals
    })


@signals_bp.route('/create', methods=['POST'])
def create_signal():
    """创建新信号"""
    data = request.json
    
    required_fields = ['signal_type', 'signal_id', 'prediction']
    for field in required_fields:
        if field not in data:
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
    
    signal_id = SignalTracking.create_signal(
        signal_type=data['signal_type'],
        signal_id=data['signal_id'],
        prediction=data['prediction'],
        market_id=data.get('market_id'),
        market_name=data.get('market_name'),
        predicted_direction=data.get('predicted_direction'),
        confidence=data.get('confidence'),
        trigger_price=data.get('trigger_price'),
        target_price=data.get('target_price'),
        stop_loss=data.get('stop_loss'),
        metadata=data.get('metadata')
    )
    
    return jsonify({
        'success': True,
        'signal_id': signal_id
    })


@signals_bp.route('/<signal_id>/resolve', methods=['POST'])
def resolve_signal(signal_id):
    """更新信号结果"""
    data = request.json
    
    if 'actual_result' not in data:
        return jsonify({'success': False, 'error': 'Missing actual_result'}), 400
    
    success = SignalTracking.resolve_signal(
        signal_id=signal_id,
        actual_result=data['actual_result'],
        exit_price=data.get('exit_price'),
        pnl_percent=data.get('pnl_percent'),
        actual_direction=data.get('actual_direction'),
        metadata=data.get('metadata')
    )
    
    if not success:
        return jsonify({'success': False, 'error': 'Signal not found'}), 404
    
    return jsonify({'success': True})


@signals_bp.route('/reports', methods=['GET'])
def get_quality_reports():
    """获取质量报告列表"""
    report_type = request.args.get('type', None)
    limit = request.args.get('limit', 10, type=int)
    
    reports = SignalTracking.get_quality_reports(
        report_type=report_type,
        limit=limit
    )
    
    return jsonify({
        'success': True,
        'count': len(reports),
        'data': reports
    })


@signals_bp.route('/reports/latest', methods=['GET'])
def get_latest_report():
    """获取最新质量报告"""
    report_type = request.args.get('type', 'weekly')
    
    report = SignalTracking.get_latest_quality_report(report_type=report_type)
    
    if not report:
        return jsonify({
            'success': False,
            'error': 'No report found'
        }), 404
    
    return jsonify({
        'success': True,
        'data': report
    })


@signals_bp.route('/thresholds', methods=['GET'])
def get_thresholds():
    """获取当前阈值设置"""
    thresholds = SignalTracking.get_current_thresholds()
    
    return jsonify({
        'success': True,
        'data': thresholds
    })


@signals_bp.route('/thresholds/history', methods=['GET'])
def get_threshold_history():
    """获取阈值变更历史"""
    threshold_type = request.args.get('type', None)
    limit = request.args.get('limit', 50, type=int)
    
    history = SignalTracking.get_threshold_history(
        threshold_type=threshold_type,
        limit=limit
    )
    
    return jsonify({
        'success': True,
        'count': len(history),
        'data': history
    })


@signals_bp.route('/thresholds/record', methods=['POST'])
def record_threshold_change():
    """记录阈值变更"""
    data = request.json
    
    required_fields = ['threshold_type', 'old_value', 'new_value']
    for field in required_fields:
        if field not in data:
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
    
    change_id = SignalTracking.record_threshold_change(
        threshold_type=data['threshold_type'],
        old_value=data['old_value'],
        new_value=data['new_value'],
        change_reason=data.get('change_reason'),
        auto_optimized=data.get('auto_optimized', False),
        optimization_params=data.get('optimization_params'),
        performance_before=data.get('performance_before'),
        performance_after=data.get('performance_after')
    )
    
    return jsonify({
        'success': True,
        'change_id': change_id
    })


@signals_bp.route('/dashboard', methods=['GET'])
def get_dashboard_data():
    """获取Dashboard展示的综合信号数据"""
    days = request.args.get('days', 30, type=int)
    
    # 获取总体统计
    overall_stats = SignalTracking.get_signal_stats(days=days)
    
    # 获取各类型统计
    type_stats = overall_stats.get('by_type', [])
    
    # 获取最近的质量报告
    latest_report = SignalTracking.get_latest_quality_report('weekly')
    
    # 获取待处理信号数量
    pending_signals = SignalTracking.get_pending_signals(limit=1000)
    
    # 获取阈值历史
    threshold_history = SignalTracking.get_threshold_history(limit=10)
    
    return jsonify({
        'success': True,
        'data': {
            'overall': {
                'total_signals': overall_stats.get('total', 0),
                'win_rate': overall_stats.get('win_rate', 0),
                'total_pnl': overall_stats.get('total_pnl', 0),
                'avg_pnl': overall_stats.get('avg_pnl', 0),
                'pending_count': len(pending_signals),
                'resolved_count': overall_stats.get('resolved_count', 0)
            },
            'by_type': type_stats,
            'latest_report': latest_report,
            'recent_threshold_changes': threshold_history[:5]
        }
    })


@signals_bp.route('/reports/generate', methods=['POST'])
def generate_report():
    """生成质量报告"""
    data = request.json or {}
    report_type = data.get('type', 'weekly')
    
    try:
        if report_type == 'weekly':
            report = QualityReportService.generate_weekly_report()
        elif report_type == 'monthly':
            report = QualityReportService.generate_monthly_report()
        else:
            start_date = datetime.fromisoformat(data.get('start_date'))
            end_date = datetime.fromisoformat(data.get('end_date'))
            report = QualityReportService.generate_report(
                report_type=report_type,
                start_date=start_date,
                end_date=end_date
            )
        
        return jsonify({
            'success': True,
            'data': report
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@signals_bp.route('/optimize/analyze', methods=['GET'])
def analyze_threshold():
    """分析阈值影响"""
    threshold_type = request.args.get('type')
    days = request.args.get('days', 30, type=int)
    
    if not threshold_type:
        return jsonify({
            'success': False,
            'error': 'Missing threshold_type parameter'
        }), 400
    
    result = ThresholdOptimizer.analyze_threshold_impact(
        threshold_type=threshold_type,
        days=days
    )
    
    if 'error' in result:
        return jsonify({
            'success': False,
            'error': result['error']
        }), 400
    
    return jsonify({
        'success': True,
        'data': result
    })


@signals_bp.route('/optimize/all', methods=['GET'])
def optimize_all_thresholds():
    """优化所有阈值"""
    days = request.args.get('days', 30, type=int)
    
    result = ThresholdOptimizer.optimize_all_thresholds(days=days)
    
    return jsonify({
        'success': True,
        'data': result
    })


@signals_bp.route('/optimize/apply', methods=['POST'])
def apply_optimization():
    """应用阈值优化"""
    data = request.json
    
    required_fields = ['threshold_type', 'new_value']
    for field in required_fields:
        if field not in data:
            return jsonify({
                'success': False,
                'error': f'Missing required field: {field}'
            }), 400
    
    result = ThresholdOptimizer.apply_optimization(
        threshold_type=data['threshold_type'],
        new_value=data['new_value'],
        auto_optimized=data.get('auto_optimized', False),
        reason=data.get('reason')
    )
    
    if not result.get('success'):
        return jsonify(result), 400
    
    return jsonify(result)


@signals_bp.route('/optimize/config', methods=['GET'])
def get_threshold_config():
    """获取阈值配置"""
    threshold_type = request.args.get('type')
    config = ThresholdOptimizer.get_threshold_config(threshold_type)
    
    if 'error' in config:
        return jsonify({
            'success': False,
            'error': config['error']
        }), 400
    
    return jsonify({
        'success': True,
        'data': config
    })
