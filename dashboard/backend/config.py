import os
from datetime import timedelta
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 数据目录
DATA_DIR = PROJECT_ROOT / "07-data"
NEWS_CACHE_DIR = DATA_DIR / "news_cache"

class Config:
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    
    # Database
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'database/polymarket.db')
    
    # CORS
    CORS_ORIGINS = ['http://localhost:3000', 'http://127.0.0.1:3000', 'http://localhost:3001', 'http://127.0.0.1:3001']
    
    # SocketIO
    SOCKETIO_CORS_ALLOWED_ORIGINS = '*'
    
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '-5052636342')
    
    # Monitoring
    MONITOR_LOG_PATH = os.getenv('MONITOR_LOG_PATH', '../07-data/logs/monitor.log')
    WHALE_STATES_PATH = os.getenv('WHALE_STATES_PATH', '../07-data/whale_states/')
