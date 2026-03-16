from flask import Blueprint, jsonify

arbitrage_bp = Blueprint('arbitrage', __name__)

@arbitrage_bp.route('/pair-cost', methods=['GET'])
def get_pair_cost():
    """获取Pair Cost套利机会"""
    return jsonify({
        'opportunities': []
    })

@arbitrage_bp.route('/cross-market', methods=['GET'])
def get_cross_market():
    """获取跨平台套利机会"""
    return jsonify({
        'opportunities': []
    })
