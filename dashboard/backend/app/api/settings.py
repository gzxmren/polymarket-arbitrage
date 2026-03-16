from flask import Blueprint, request, jsonify
import json
from pathlib import Path

settings_bp = Blueprint('settings', __name__)

SETTINGS_FILE = Path(__file__).parent.parent.parent / 'config' / 'settings.json'

def load_settings():
    """加载设置"""
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {
        'telegram_bot_token': '',
        'telegram_chat_id': '-5052636342',
        'enable_whale_alerts': True,
        'enable_arbitrage_alerts': True,
        'enable_summary_report': True,
        'whale_threshold_value': 100000,
        'whale_threshold_changes': 5,
        'pair_cost_threshold': 0.995,
        'min_gap_threshold': 0.05,
        'max_position_per_trade': 100,
        'stop_loss_pct': 20,
    }

def save_settings_to_file(settings):
    """保存设置到文件"""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

@settings_bp.route('/', methods=['GET'])
def get_settings():
    """获取设置"""
    settings = load_settings()
    return jsonify(settings)

@settings_bp.route('/', methods=['POST'])
def update_settings():
    """更新设置"""
    try:
        new_settings = request.get_json()
        save_settings_to_file(new_settings)
        return jsonify({'success': True, 'message': '设置已保存'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
