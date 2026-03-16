#!/usr/bin/env python3
"""
API 测试脚本
测试后端API是否正常工作
"""

import sys
import json
sys.path.insert(0, '/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend')

from app import create_app

def test_api():
    """测试API端点"""
    app = create_app()
    client = app.test_client()
    
    print("=" * 60)
    print("🔍 API 测试")
    print("=" * 60)
    
    tests = [
        ('GET', '/api/summary/', '汇总API'),
        ('GET', '/api/whales/', '鲸鱼列表API'),
        ('GET', '/api/markets/', '市场API'),
        ('GET', '/api/arbitrage/pair-cost', 'Pair Cost API'),
        ('GET', '/api/arbitrage/cross-market', '跨平台套利API'),
        ('GET', '/api/alerts/', '警报API'),
    ]
    
    passed = 0
    for method, endpoint, name in tests:
        try:
            if method == 'GET':
                response = client.get(endpoint)
            else:
                response = client.post(endpoint)
            
            if response.status_code == 200:
                print(f"✅ {name}: {endpoint}")
                passed += 1
            else:
                print(f"⚠️  {name}: {endpoint} (状态码: {response.status_code})")
        except Exception as e:
            print(f"❌ {name}: {endpoint} (错误: {e})")
    
    print("=" * 60)
    print(f"📊 测试结果: {passed}/{len(tests)} 通过")
    print("=" * 60)
    
    return passed == len(tests)

if __name__ == '__main__':
    success = test_api()
    sys.exit(0 if success else 1)
