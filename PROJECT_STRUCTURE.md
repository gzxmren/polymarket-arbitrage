# Polymarket 项目结构规范

## 目录结构

```
polymarket-project/
├── 00-learning/          # 学习资料和文档
├── 01-arbitrage-pair-cost/    # Pair Cost 套利策略
├── 02-arbitrage-cross-market/ # 跨市场套利策略
├── 03-momentum-trading/       # 动量交易策略
├── 04-whale-following/        # 鲸鱼跟随策略
├── 05-sentiment-contrarian/   # 情绪逆向策略
├── 06-tools/             # 工具模块
│   ├── analysis/         # 分析工具
│   │   ├── config.py     # 统一配置（路径、阈值等）
│   │   ├── semantic_arbitrage.py
│   │   ├── logic_chain_analyzer.py
│   │   ├── whale_watchlist.py
│   │   └── ...
│   ├── monitoring/       # 监控工具
│   │   ├── polymarket_monitor_v2.py
│   │   └── ...
│   └── trading/          # 交易工具
├── 07-data/              # 数据目录（统一数据存储）
│   ├── logs/             # 日志文件
│   ├── news_cache/       # 新闻缓存
│   ├── whale_states/     # 鲸鱼状态
│   └── *.json            # 数据文件
├── 08-backtests/         # 回测框架
├── 09-docs/              # 文档
├── 10-tests/             # 测试
├── dashboard/            # Web 仪表盘
│   ├── backend/          # Flask 后端
│   ├── frontend/         # React 前端
│   └── database/         # SQLite 数据库
├── database/             # 主数据库（与 dashboard/database 同步）
├── docs/                 # 设计文档
├── memory/               # 工作日志
└── tests/                # 测试
```

## 路径规范

### 统一配置模块

所有路径统一使用 `06-tools/analysis/config.py`：

```python
from config import (
    PROJECT_ROOT,    # 项目根目录
    DATA_DIR,        # 07-data/
    WHALE_STATES_DIR,# 07-data/whale_states/
    NEWS_CACHE_DIR,  # 07-data/news_cache/
    WATCHLIST_FILE,  # 07-data/whale_watchlist.json
    DASHBOARD_DB_FILE,# dashboard/database/polymarket.db
)
```

### 禁止的做法

❌ **不要使用绝对路径**：
```python
# 错误
DATA_DIR = "/home/xmren/.openclaw/workspace/polymarket-project/07-data"
```

❌ **不要重复定义路径**：
```python
# 错误 - 每个文件都重新定义
DATA_DIR = Path(__file__).parent.parent.parent / "07-data"
```

### 推荐的做法

✅ **使用统一配置模块**：
```python
from config import DATA_DIR, WHALE_STATES_DIR
```

✅ **使用相对路径计算（仅在必要时）**：
```python
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent.parent
```

## 版本规范

### Python 代码
- 使用 Python 3.12+
- 使用类型注解
- 使用 f-string 格式化

### 前端代码
- React 18+
- TypeScript
- Ant Design 组件库

## 配置文件

### 环境变量
- `.env` 文件存储敏感配置（API Key 等）
- 不提交到版本控制

### 阈值配置
- 所有阈值统一在 `config.py` 的 `Thresholds` 类中定义
- 不要在代码中硬编码阈值

## 数据库

### SQLite
- 主数据库：`dashboard/database/polymarket.db`
- 备份：`database/polymarket.db`

### 表命名
- 小写下划线命名
- 复数形式（whales, positions, changes）

## 日志规范

### 日志目录
- `07-data/logs/`

### 日志格式
```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

## 测试规范

### 测试目录
- `10-tests/unit/` - 单元测试
- `10-tests/integration/` - 集成测试
- `10-tests/e2e/` - 端到端测试

### 测试命名
- 测试文件：`test_*.py`
- 测试函数：`test_*`

## 文档规范

### 文档目录
- `docs/` - 设计文档
- `09-docs/` - 用户文档
- `memory/` - 工作日志

### 文档格式
- Markdown
- 包含日期、作者、版本信息

---

*最后更新: 2026-03-17*
*版本: v2.1*