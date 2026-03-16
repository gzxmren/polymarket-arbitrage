from flask import Blueprint, jsonify

markets_bp = Blueprint('markets', __name__)

@markets_bp.route('/', methods=['GET'])
def get_markets():
    """获取市场列表"""
    # TODO: 实现市场数据获取
    return jsonify({
        'count': 0,
        'markets': []
    })
