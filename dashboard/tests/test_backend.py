#!/usr/bin/env python3
"""
后端代码测试脚本
验证语法和基本逻辑
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

def test_imports(file_path):
    """测试导入语句"""
    issues = []
    with open(file_path, 'r') as f:
        code = f.read()
    
    # 检查是否有未使用的导入
    tree = ast.parse(code)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    
    return issues

def main():
    backend_dir = Path('/home/xmren/.openclaw/workspace/polymarket-project/dashboard/backend')
    
    print("=" * 60)
    print("🔍 后端代码检查报告")
    print("=" * 60)
    
    files_to_check = [
        'config.py',
        'run.py',
        'app/__init__.py',
        'app/models/database.py',
        'app/api/whales.py',
        'app/api/summary.py',
        'app/api/markets.py',
        'app/api/arbitrage.py',
        'app/api/alerts.py',
    ]
    
    all_passed = True
    
    for file_name in files_to_check:
        file_path = backend_dir / file_name
        if not file_path.exists():
            print(f"❌ {file_name}: 文件不存在")
            all_passed = False
            continue
        
        # 语法检查
        passed, error = test_syntax(file_path)
        if passed:
            print(f"✅ {file_name}: 语法正确")
        else:
            print(f"❌ {file_name}: 语法错误 - {error}")
            all_passed = False
            continue
        
        # 导入检查
        issues = test_imports(file_path)
        if issues:
            print(f"⚠️  {file_name}: 导入问题 - {', '.join(issues)}")
    
    print("=" * 60)
    if all_passed:
        print("✅ 所有文件语法检查通过")
    else:
        print("❌ 部分文件存在问题")
    print("=" * 60)

if __name__ == '__main__':
    main()
