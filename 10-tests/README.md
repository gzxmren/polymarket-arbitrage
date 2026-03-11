# 测试框架

## 📁 目录结构

```
10-tests/
├── unit/                   # 单元测试
├── integration/            # 集成测试
├── e2e/                    # 端到端测试
├── fixtures/               # 测试数据
├── mocks/                  # Mock 数据
└── conftest.py            # pytest 配置
```

## 🧪 测试类型

| 类型 | 说明 | 运行频率 |
|------|------|----------|
| **单元测试** | 单个函数/模块测试 | 每次提交 |
| **集成测试** | 模块间交互测试 | 每日 |
| **E2E测试** | 完整流程测试 | 每周 |
| **模拟测试** | 使用 Mock 数据测试 | 随时 |

## 🚀 快速开始

```bash
# 安装测试依赖
pip install pytest pytest-asyncio pytest-cov

# 运行所有测试
pytest

# 运行单元测试
pytest unit/

# 运行带覆盖率报告
pytest --cov=../06-tools --cov-report=html

# 运行特定测试
pytest unit/test_pair_cost_scanner.py -v
```

## 📊 测试覆盖目标

- 核心算法：> 90%
- 风险评估：> 85%
- 通知系统：> 80%
- 整体：> 75%
