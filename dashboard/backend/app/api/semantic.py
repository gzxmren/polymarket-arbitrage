#!/usr/bin/env python3
"""
语义套利 API
提供语义套利和逻辑链分析的 REST API
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "06-tools/analysis"))

from semantic_arbitrage import SemanticArbitrageEngine, Market as SemanticMarket
from logic_chain_analyzer import LogicChainAnalyzer

semantic_bp = Blueprint('semantic', __name__)

# 全局实例
semantic_engine = SemanticArbitrageEngine()
logic_analyzer = LogicChainAnalyzer()


@semantic_bp.route('/scan', methods=['POST'])
def scan_semantic_arbitrage():
    """
    执行语义套利扫描
    
    POST /api/semantic/scan
    
    Returns:
        扫描结果
    """
    try:
        # 获取请求数据
        data = request.get_json() or {}
        markets_data = data.get('markets', [])
        
        if not markets_data:
            return jsonify({
                'success': False,
                'error': 'No markets provided'
            }), 400
        
        # 转换为 Market 对象
        markets = [
            SemanticMarket(
                id=m['id'],
                title=m['title'],
                description=m.get('description', m['title']),
                yes_price=m['yes_price'],
                no_price=m['no_price'],
                liquidity=m.get('liquidity', 0)
            )
            for m in markets_data
        ]
        
        # 执行语义套利扫描
        semantic_signals = semantic_engine.scan_with_predefined(markets)
        
        # 执行逻辑链分析
        logic_violations = logic_analyzer.scan_all_violations(markets_data)
        
        # 构建结果
        result = {
            'success': True,
            'scan_time': datetime.now(timezone.utc).isoformat(),
            'markets_scanned': len(markets),
            'semantic_signals': [
                {
                    'type': s.type,
                    'subtype': s.subtype,
                    'market_a': s.market_a,
                    'market_b': s.market_b,
                    'price_a': s.price_a,
                    'price_b': s.price_b,
                    'violation': s.violation,
                    'expected_profit': s.expected_profit,
                    'confidence': s.confidence,
                    'reasoning': s.reasoning,
                    'suggested_action': s.suggested_action,
                    'created_at': s.created_at.isoformat()
                }
                for s in semantic_signals
            ],
            'logic_violations': [
                {
                    'violation_type': v.violation_type,
                    'nodes': v.nodes,
                    'expected': v.expected,
                    'actual': v.actual,
                    'severity': v.severity,
                    'profit_potential': v.profit_potential,
                    'description': v.description
                }
                for v in logic_violations
            ],
            'total_opportunities': len(semantic_signals) + len(logic_violations)
        }
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@semantic_bp.route('/relationships', methods=['GET'])
def get_semantic_relationships():
    """
    获取预定义的语义关系
    
    GET /api/semantic/relationships
    
    Returns:
        语义关系列表
    """
    try:
        relationships = []
        
        for conclusion, premises in semantic_engine.IMPLICATIONS.items():
            relationships.append({
                'conclusion': conclusion,
                'premises': premises,
                'type': 'implies'
            })
        
        return jsonify({
            'success': True,
            'count': len(relationships),
            'relationships': relationships
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@semantic_bp.route('/statistics', methods=['GET'])
def get_semantic_statistics():
    """
    获取语义套利统计信息
    
    GET /api/semantic/statistics
    
    Returns:
        统计信息
    """
    try:
        # 从数据库获取统计
        from app.models.database import db
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 检查表是否存在
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='semantic_signals'
        """)
        
        if not cursor.fetchone():
            return jsonify({
                'success': True,
                'total_signals': 0,
                'pending_signals': 0,
                'completed_signals': 0
            })
        
        # 获取统计
        cursor.execute("SELECT COUNT(*) FROM semantic_signals")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM semantic_signals WHERE status='pending'")
        pending = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM semantic_signals WHERE status='completed'")
        completed = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'total_signals': total,
            'pending_signals': pending,
            'completed_signals': completed
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@semantic_bp.route('/test', methods=['GET'])
def test_semantic():
    """
    测试语义套利功能
    
    GET /api/semantic/test
    
    Returns:
        测试结果
    """
    try:
        # 使用测试数据
        test_markets = [
            {
                'id': '0x100',
                'title': 'Will Chiefs win Super Bowl 2024?',
                'yes_price': 0.55,
                'no_price': 0.45
            },
            {
                'id': '0x101',
                'title': 'Will AFC win Super Bowl 2024?',
                'yes_price': 0.45,
                'no_price': 0.55
            },
            {
                'id': '0x102',
                'title': 'Will Trump win 2024?',
                'yes_price': 0.55,
                'no_price': 0.45
            },
            {
                'id': '0x103',
                'title': 'Will Republican win 2024?',
                'yes_price': 0.48,
                'no_price': 0.52
            }
        ]
        
        # 转换为 Market 对象
        markets = [
            SemanticMarket(
                id=m['id'],
                title=m['title'],
                description=m['title'],
                yes_price=m['yes_price'],
                no_price=m['no_price'],
                liquidity=100000
            )
            for m in test_markets
        ]
        
        # 执行扫描
        semantic_signals = semantic_engine.scan_with_predefined(markets)
        logic_violations = logic_analyzer.scan_all_violations(test_markets)
        
        return jsonify({
            'success': True,
            'test': True,
            'markets_count': len(test_markets),
            'semantic_signals_count': len(semantic_signals),
            'logic_violations_count': len(logic_violations),
            'message': 'Semantic arbitrage test completed successfully'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500