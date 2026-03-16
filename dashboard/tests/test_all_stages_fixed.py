#!/usr/bin/env python3
"""
全阶段代码检查（修复版）
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

def check_tsx_component(file_path):
    """检查TSX组件文件"""
    if not file_path.exists():
        return False, "文件不存在"
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # 组件文件检查
    if 'import React' not in content and "from 'react'" not in content:
        return False, "缺少React导入"
    
    if 'export default' not in content:
        return False, "缺少默认导出"
    
    return True, None

def check_ts_service(file_path):
    """检查TS服务文件"""
    if not file_path.exists():
        return False, "文件不存在"
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # 服务文件检查
    if 'export' not in content:
        return False, "缺少导出"
    
    return True, None

def check_tsx_entry(file_path):
    """检查TSX入口文件"""
    if not file_path.exists():
        return False, "文件不存在"
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # 入口文件检查
    if 'ReactDOM' not in content:
        return False, "缺少ReactDOM"
    
    return True, None

def main():
    print("=" * 70)
    print("🔍 Polymarket Dashboard 全阶段代码检查（修复版）")
    print("=" * 70)
    
    base_dir = Path('/home/xmren/.openclaw/workspace/polymarket-project/dashboard')
    
    # 后端文件
    print("\n📦 后端代码检查:")
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
    
    backend_ok = sum(1 for f in backend_files if check_python_syntax(base_dir / f)[0])
    for f in backend_files:
        passed, _ = check_python_syntax(base_dir / f)
        print(f"  {'✅' if passed else '❌'} {f}")
    print(f"\n  后端: {backend_ok}/{len(backend_files)} 通过")
    
    # 前端组件
    print("\n🎨 前端组件检查:")
    component_files = [
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
    ]
    
    component_ok = sum(1 for f in component_files if check_tsx_component(base_dir / f)[0])
    for f in component_files:
        passed, _ = check_tsx_component(base_dir / f)
        print(f"  {'✅' if passed else '❌'} {f}")
    print(f"\n  组件: {component_ok}/{len(component_files)} 通过")
    
    # 前端入口
    print("\n🚪 前端入口检查:")
    entry_files = ['frontend/src/index.tsx']
    entry_ok = sum(1 for f in entry_files if check_tsx_entry(base_dir / f)[0])
    for f in entry_files:
        passed, _ = check_tsx_entry(base_dir / f)
        print(f"  {'✅' if passed else '❌'} {f}")
    print(f"\n  入口: {entry_ok}/{len(entry_files)} 通过")
    
    # 前端服务
    print("\n🔧 前端服务检查:")
    service_files = ['frontend/src/services/api.ts', 'frontend/src/services/socket.ts']
    service_ok = sum(1 for f in service_files if check_ts_service(base_dir / f)[0])
    for f in service_files:
        passed, _ = check_ts_service(base_dir / f)
        print(f"  {'✅' if passed else '❌'} {f}")
    print(f"\n  服务: {service_ok}/{len(service_files)} 通过")
    
    # Docker配置
    print("\n🐳 Docker配置检查:")
    docker_files = ['docker-compose.yml', 'backend/Dockerfile', 'frontend/Dockerfile']
    docker_ok = sum(1 for f in docker_files if (base_dir / f).exists())
    for f in docker_files:
        print(f"  {'✅' if (base_dir / f).exists() else '❌'} {f}")
    print(f"\n  Docker: {docker_ok}/{len(docker_files)} 通过")
    
    # 汇总
    total = len(backend_files) + len(component_files) + len(entry_files) + len(service_files) + len(docker_files)
    passed = backend_ok + component_ok + entry_ok + service_ok + docker_ok
    
    print("\n" + "=" * 70)
    print(f"📊 汇总: {passed}/{total} 文件通过")
    if passed == total:
        print("✅ 所有代码检查通过！")
    else:
        print(f"⚠️  {total - passed} 个文件存在问题")
    print("=" * 70)

if __name__ == '__main__':
    main()
