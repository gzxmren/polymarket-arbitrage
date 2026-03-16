#!/usr/bin/env python3
"""
阶段2后端代码检查
"""

import ast
import sys
from pathlib import Path

def test_syntax(file_path):
    """测试Python文件语法"""
    try:
        with open(file_path, 'r') as f:
            code = f.read()
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        return False, str(e)

def main():
    backend_dir = Path('/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend')
    
    print("=" * 60)
    print("🔍 阶段2后端代码检查报告")
    print("=" * 60)
    
    files_to_check = [
        'app/services/data_sync.py',
        'app/services/scheduler.py',
        'app/services/notification.py',
    ]
    
    all_passed = True
    
    for file_name in files_to_check:
        file_path = backend_dir / file_name
        if not file_path.exists():
            print(f"❌ {file_name}: 文件不存在")
            all_passed = False
            continue
        
        passed, error = test_syntax(file_path)
        if passed:
            print(f"✅ {file_name}: 语法正确")
        else:
            print(f"❌ {file_name}: 语法错误 - {error}")
            all_passed = False
    
    # 检查 Docker 配置
    docker_files = [
        '../docker-compose.yml',
        'Dockerfile',
    ]
    
    print("\n📦 Docker 配置检查:")
    for file_name in docker_files:
        file_path = Path('/home/xmren/.openclaw/workspace/polymarket-project/dashboard') / file_name
        if file_path.exists():
            print(f"✅ {file_name}: 文件存在")
        else:
            print(f"❌ {file_name}: 文件不存在")
            all_passed = False
    
    print("=" * 60)
    if all_passed:
        print("✅ 所有阶段2文件检查通过")
    else:
        print("❌ 部分文件存在问题")
    print("=" * 60)

if __name__ == '__main__':
    main()
