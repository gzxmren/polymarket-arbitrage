from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO
from .models.database import Database

socketio = SocketIO(cors_allowed_origins='*')

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')
    
    # CORS
    CORS(app, origins=app.config['CORS_ORIGINS'])
    
    # SocketIO
    socketio.init_app(app)
    
    # 初始化数据库
    from .models.database import db
    
    # 注册蓝图
    from .api.whales import whales_bp
    from .api.markets import markets_bp
    from .api.arbitrage import arbitrage_bp
    from .api.alerts import alerts_bp
    from .api.summary import summary_bp
    from .api.settings import settings_bp
    from .api.semantic import semantic_bp
    from .api.signals import signals_bp
    
    app.register_blueprint(whales_bp, url_prefix='/api/whales')
    app.register_blueprint(markets_bp, url_prefix='/api/markets')
    app.register_blueprint(arbitrage_bp, url_prefix='/api/arbitrage')
    app.register_blueprint(alerts_bp, url_prefix='/api/alerts')
    app.register_blueprint(summary_bp, url_prefix='/api/summary')
    app.register_blueprint(settings_bp, url_prefix='/api/settings')
    app.register_blueprint(semantic_bp, url_prefix='/api/semantic')
    app.register_blueprint(signals_bp, url_prefix='/api/signals')
    
    return app
