#!/usr/bin/env python3
"""
项目统一配置模块
集中管理所有路径和常量配置
"""

from pathlib import Path

# 项目根目录（06-tools/analysis/ 的父目录的父目录的父目录）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 数据目录
DATA_DIR = PROJECT_ROOT / "07-data"
WHALE_STATES_DIR = DATA_DIR / "whale_states"
NEWS_CACHE_DIR = DATA_DIR / "news_cache"
LOGS_DIR = DATA_DIR / "logs"

# 配置文件
WATCHLIST_FILE = DATA_DIR / "whale_watchlist.json"

# Dashboard 数据库
DASHBOARD_DB_DIR = PROJECT_ROOT / "dashboard" / "database"
DASHBOARD_DB_FILE = DASHBOARD_DB_DIR / "polymarket.db"

# 确保目录存在
def ensure_directories():
    """确保所有数据目录存在"""
    DATA_DIR.mkdir(exist_ok=True)
    WHALE_STATES_DIR.mkdir(exist_ok=True)
    NEWS_CACHE_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    DASHBOARD_DB_DIR.mkdir(exist_ok=True)

# 阈值配置
class Thresholds:
    """监控阈值"""
    PAIR_COST = 0.995  # Pair Cost 套利阈值
    MIN_LIQUIDITY = 100  # 最低流动性
    WHALE_TRADE = 10000  # 鲸鱼交易阈值
    WATCH_VALUE = 100000  # 关注鲸鱼持仓价值阈值
    WATCH_CHANGES = 5  # 关注鲸鱼24h变动次数阈值

# API 配置
class APIConfig:
    """API 配置"""
    GAMMA_API = "https://gamma-api.polymarket.com"
    DATA_API = "https://data-api.polymarket.com"
    
# 数据库表名
class DBTables:
    """数据库表名"""
    WHALES = "whales"
    POSITIONS = "positions"
    CHANGES = "changes"
    ALERTS = "alerts"
    SIGNALS = "semantic_signals"

if __name__ == "__main__":
    print("项目配置信息:")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"  数据目录: {DATA_DIR}")
    print(f"  鲸鱼状态目录: {WHALE_STATES_DIR}")
    print(f"  新闻缓存目录: {NEWS_CACHE_DIR}")
    print(f"  Dashboard DB: {DASHBOARD_DB_FILE}")
    print(f"  关注列表文件: {WATCHLIST_FILE}")
    
    ensure_directories()
    print("\n✅ 所有目录已确保存在")