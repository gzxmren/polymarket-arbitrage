#!/usr/bin/env python3
"""
全阶段代码检查
"""

import ast
from pathlib import Path

def check_python_syntax(file_path):
    """检查Python语法"""
    try:
        with open(file_path, 'r') as f:
            ast.parse(f.read())
        return True, None
    except SyntaxError as e:
        return False, str(e)

def check_tsx_exists(file_path):
    """检查TSX文件存在"""
    if not file_path.exists():
        return False, "文件不存在"
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    if 'import React' not in content:
        return False, "缺少React导入"
    
    if 'export default' not in content:
        return False, "缺少默认导出"
    
    return True, None

def main():
    print("=" * 70)
    print("🔍 Polymarket Dashboard 全阶段代码检查")
    print("=" * 70)
    
    # 后端文件
    backend_files = [
        'backend/config.py',
        'backend/run.py',
        'backend/app/__init__.py',
        'backend/app/models/database.py',
        'backend/app/api/whales.py',
        'backend/app/api/summary.py',
        'backend/app/api/markets.py',
        'backend/app/api/arbitrage.py',
        'backend/app/api/alerts.py',
        'backend/app/services/data_sync.py',
        'backend/app/services/scheduler.py',
        'backend/app/services/notification.py',
    ]
    
    # 前端文件
    frontend_files = [
        'frontend/src/index.tsx',
        'frontend/src/App.tsx',
        'frontend/src/components/Layout/Sidebar.tsx',
        'frontend/src/components/Layout/Header.tsx',
        'frontend/src/components/Charts/ConcentrationChart.tsx',
        'frontend/src/pages/Dashboard.tsx',
        'frontend/src/pages/Whales.tsx',
        'frontend/src/pages/WhaleDetail.tsx',
        'frontend/src/pages/Arbitrage.tsx',
        'frontend/src/pages/Alerts.tsx',
        'frontend/src/pages/Settings.tsx',
        'frontend/src/services/api.ts',
        'frontend/src/services/socket.ts',
    ]
    
    base_dir = Path('/home/xmren/.openclaw/workspace/polymarket-project/dashboard')
    
    print("\n📦 后端代码检查:")
    backend_ok = 0
    for file in backend_files:
        path = base_dir / file
        passed, error = check_python_syntax(path)
        if passed:
            print(f"  ✅ {file}")
            backend_ok += 1
        else:
            print(f"  ❌ {file}: {error}")
    
    print(f"\n  后端: {backend_ok}/{len(backend_files)} 通过")
    
    print("\n🎨 前端代码检查:")
    frontend_ok = 0
    for file in frontend_files:
        path = base_dir / file
        passed, error = check_tsx_exists(path)
        if passed:
            print(f"  ✅ {file}")
            frontend_ok += 1
        else:
            print(f"  ❌ {file}: {error}")
    
    print(f"\n  前端: {frontend_ok}/{len(frontend_files)} 通过")
    
    # Docker配置
    print("\n🐳 Docker配置检查:")
    docker_files = ['docker-compose.yml', 'backend/Dockerfile', 'frontend/Dockerfile']
    docker_ok = 0
    for file in docker_files:
        path = base_dir / file
        if path.exists():
            print(f"  ✅ {file}")
            docker_ok += 1
        else:
            print(f"  ❌ {file}: 不存在")
    
    print(f"\n  Docker: {docker_ok}/{len(docker_files)} 通过")
    
    # 汇总
    total = len(backend_files) + len(frontend_files) + len(docker_files)
    passed = backend_ok + frontend_ok + docker_ok
    
    print("\n" + "=" * 70)
    print(f"📊 汇总: {passed}/{total} 文件通过")
    if passed == total:
        print("✅ 所有代码检查通过！")
    else:
        print(f"⚠️  {total - passed} 个文件存在问题")
    print("=" * 70)

if __name__ == '__main__':
    main()
