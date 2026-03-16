#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend')

# 加载 .env 文件
from pathlib import Path
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value
    print("✅ 已加载 .env 配置")

from app import create_app, socketio
from app.services.scheduler import DataScheduler

app = create_app()

if __name__ == '__main__':
    print("🚀 启动 Polymarket Dashboard 后端服务...")
    
    # 检查 LLM 配置
    if os.getenv('DEEPSEEK_API_KEY'):
        print("🧠 DeepSeek API 已配置")
    elif os.getenv('OPENAI_API_KEY'):
        print("🧠 OpenAI API 已配置")
    else:
        print("⚠️  未配置 LLM API，将使用模拟模式")
    
    # 启动数据同步调度器
    try:
        scheduler = DataScheduler()
        scheduler.start()
    except Exception as e:
        print(f"⚠️  调度器启动失败: {e}")
    
    print("📡 服务地址: http://localhost:5000")
    print("📊 API 文档: http://localhost:5000/api/")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
