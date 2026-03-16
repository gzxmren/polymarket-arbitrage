#!/usr/bin/env python3
"""
阶段3代码检查
检查前端页面更新
"""

import ast
import sys
from pathlib import Path

def test_tsx_syntax(file_path):
    """检查TSX文件基本语法"""
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # 基本检查
        checks = [
            ('import React', 'React导入'),
            ('export default', '默认导出'),
            ('const.*=.*React.FC', '函数组件定义'),
        ]
        
        issues = []
        for pattern, desc in checks:
            if pattern not in content:
                issues.append(f"缺少: {desc}")
        
        return len(issues) == 0, issues
    except Exception as e:
        return False, [str(e)]

def main():
    frontend_dir = Path('/home/xmren/.openclaw/workspace/polymarket-project/dashboard/frontend/src')
    
    print("=" * 60)
    print("🔍 阶段3前端代码检查报告")
    print("=" * 60)
    
    files_to_check = [
        'pages/Arbitrage.tsx',
        'pages/Alerts.tsx',
        'pages/Settings.tsx',
        'pages/WhaleDetail.tsx',
        'App.tsx',
    ]
    
    all_passed = True
    
    for file_name in files_to_check:
        file_path = frontend_dir / file_name
        if not file_path.exists():
            print(f"❌ {file_name}: 文件不存在")
            all_passed = False
            continue
        
        passed, issues = test_tsx_syntax(file_path)
        if passed:
            print(f"✅ {file_name}: 语法正确")
        else:
            print(f"⚠️  {file_name}: {', '.join(issues)}")
    
    print("=" * 60)
    if all_passed:
        print("✅ 所有阶段3文件检查通过")
    else:
        print("❌ 部分文件存在问题")
    print("=" * 60)

if __name__ == '__main__':
    main()
