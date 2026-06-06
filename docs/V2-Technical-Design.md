# V2 技术设计方案

## 文档信息
- **版本**: V2.1
- **日期**: 2026-03-17
- **状态**: 定稿

---

## 一、技术架构

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        用户层                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Telegram │  │Dashboard │  │  API     │  │  报告    │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                      应用服务层                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ 语义套利引擎 │  │ 策略信号引擎 │  │ 质量监控引擎 │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ 逻辑链分析器 │  │ 鲸鱼跟踪器   │  │ 新闻分析器   │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                        数据层                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │  SQLite  │  │  API数据 │  │  LLM缓存 │  │  配置    │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| 前端 | React + TypeScript | Dashboard界面 |
| 后端 | Flask + Python | API服务 |
| 数据库 | SQLite | 本地数据存储 |
| LLM | bailian/kimi-k2.5 | 语义分析（OpenClaw内置）|
| 消息 | Telegram Bot API | 通知推送 |
| 部署 | systemd + nginx | 服务管理 |

### 1.3 LLM 调用架构（OpenClaw）

由于使用 OpenClaw 内置的 kimi-k2.5 模型，LLM 调用采用**任务队列 + 外部处理**模式：

```
┌─────────────────────────────────────────┐
│           Python 程序                   │
│  ┌─────────────────────────────────┐   │
│  │  SemanticArbitrageEngine        │   │
│  │  - 生成 prompt                  │   │
│  │  - 保存到任务文件               │   │
│  │  - 等待结果                     │   │
│  └─────────────────────────────────┘   │
│              │                          │
│              ▼                          │
│  ┌─────────────────────────────────┐   │
│  │  Task Queue (JSON files)        │   │
│  │  /tmp/llm_tasks/*.json          │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
                   │
                   │ OpenClaw Agent
                   ▼
┌─────────────────────────────────────────┐
│        OpenClaw Agent (主会话)          │
│  ┌─────────────────────────────────┐   │
│  │  读取任务文件                   │   │
│  │  调用 kimi-k2.5                 │   │
│  │  保存结果                       │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

**工作流程**:
1. Python 程序生成 LLM prompt，保存到任务文件
2. Python 程序进入等待状态（或定期轮询）
3. OpenClaw Agent 读取任务文件，调用 kimi-k2.5
4. OpenClaw Agent 将结果保存到结果文件
5. Python 程序读取结果，继续处理

**优势**:
- 利用 OpenClaw 内置的 kimi-k2.5，无需额外 API Key
- 成本可控（使用现有 OpenClaw 资源）
- 可并行处理多个 LLM 任务

**实现方式**:
- 方式1: 子 Agent 模式（推荐）
- 方式2: 主 Agent 定期轮询
- 方式3: 使用 OpenClaw 的 cron 任务

---

## 二、模块详细设计

### 2.1 语义套利引擎

#### 2.1.1 类设计

```python
@dataclass
class Market:
    """市场数据类"""
    id: str
    title: str
    description: str
    yes_price: float
    no_price: float
    liquidity: float

@dataclass
class SemanticRelationship:
    """语义关系数据类"""
    market_a: str
    market_b: str
    similarity: float
    relationship_type: str  # 'implies', 'mutex', 'related'
    confidence: float
    detected_at: datetime

class SemanticArbitrageEngine:
    """语义套利引擎"""
    
    def __init__(self):
        self.db = Database()
        self.cache = Cache(ttl=3600)  # 1小时缓存
    
    def calculate_similarity(self, market_a: Market, market_b: Market) -> float:
        """
        计算两个市场的语义相似度
        
        注意: 此方法需要外部调用LLM，本方法只负责构建prompt和解析结果
        
        Args:
            market_a: 市场A
            market_b: 市场B
            
        Returns:
            相似度评分 (0-1)
        """
        cache_key = f"sim:{market_a.id}:{market_b.id}"
        if cached := self.cache.get(cache_key):
            return cached
        
        prompt = f"""分析以下两个预测市场的语义相似度：

市场A: {market_a.title}
描述: {market_a.description}

市场B: {market_b.title}
描述: {market_b.description}

请给出相似度评分（0-1）和理由。
以JSON格式返回：{{"similarity": 0.85, "reason": "..."}}"""
        
        # LLM调用由外部处理，这里返回prompt供外部调用
        return {"type": "similarity", "prompt": prompt, "cache_key": cache_key}
    
    def detect_relationship(self, market_a: Market, market_b: Market) -> Dict:
        """
        检测两个市场的逻辑关系
        
        Returns:
            包含prompt的字典，供外部LLM调用
        """
        prompt = f"""分析以下两个预测市场的逻辑关系：

市场A: {market_a.title}
市场B: {market_b.title}

可能的关系类型：
- implies: A发生则B必然发生 (A⊆B)
- mutex: A和B互斥 (A∩B=∅)
- related: 相关但无明确逻辑关系
- independent: 独立

以JSON格式返回：
{{"relationship": "implies/mutex/related/independent", "confidence": 0.9, "reason": "..."}}"""
        
        return {"type": "relationship", "prompt": prompt, "market_a": market_a.id, "market_b": market_b.id}
    
    def process_llm_response(self, task_type: str, response: str, **kwargs) -> Any:
        """
        处理LLM返回的结果
        
        Args:
            task_type: 任务类型 (similarity/relationship)
            response: LLM返回的JSON字符串
            **kwargs: 额外参数
        """
        try:
            result = json.loads(response)
            
            if task_type == "similarity":
                similarity = float(result.get("similarity", 0))
                cache_key = kwargs.get("cache_key")
                if cache_key:
                    self.cache.set(cache_key, similarity)
                return similarity
            
            elif task_type == "relationship":
                return SemanticRelationship(
                    market_a=kwargs.get("market_a"),
                    market_b=kwargs.get("market_b"),
                    similarity=kwargs.get("similarity", 0),
                    relationship_type=result.get("relationship", "independent"),
                    confidence=result.get("confidence", 0),
                    detected_at=datetime.now(timezone.utc)
                )
        
        except json.JSONDecodeError:
            logger.error(f"LLM response parse error: {response}")
            return None
            detected_at=datetime.now(timezone.utc)
        )
    
    def check_pricing_contradiction(self, rel: SemanticRelationship, 
                                    market_a: Market, market_b: Market) -> Optional[Dict]:
        """
        检查定价是否违反逻辑关系
        
        Returns:
            矛盾详情或None
        """
        price_a = market_a.yes_price
        price_b = market_b.yes_price
        tolerance = 0.05  # 5%容错
        
        if rel.relationship_type == "implies":
            # A implies B: Price(A) <= Price(B) + tolerance
            if price_a > price_b + tolerance:
                return {
                    "type": "implies_violation",
                    "market_a": market_a.id,
                    "market_b": market_b.id,
                    "price_a": price_a,
                    "price_b": price_b,
                    "violation": price_a - price_b,
                    "suggested_action": f"SELL {market_a.title}, BUY {market_b.title}",
                    "expected_profit": price_a - price_b - tolerance
                }
        
        elif rel.relationship_type == "mutex":
            # A mutex B: Price(A) + Price(B) <= 1.0 + tolerance
            if price_a + price_b > 1.0 + tolerance:
                return {
                    "type": "mutex_violation",
                    "market_a": market_a.id,
                    "market_b": market_b.id,
                    "combined_price": price_a + price_b,
                    "violation": price_a + price_b - 1.0,
                    "suggested_action": "SELL both YES or arbitrage",
                    "expected_profit": price_a + price_b - 1.0 - tolerance
                }
        
        return None
    
    def scan_arbitrage_opportunities(self) -> List[Signal]:
        """
        扫描所有市场的套利机会
        
        Returns:
            套利信号列表
        """
        signals = []
        markets = self.get_active_markets()
        
        # 获取已知的语义关系
        known_relationships = self.db.get_semantic_relationships()
        
        for rel in known_relationships:
            market_a = next(m for m in markets if m.id == rel.market_a)
            market_b = next(m for m in markets if m.id == rel.market_b)
            
            contradiction = self.check_pricing_contradiction(rel, market_a, market_b)
            if contradiction:
                signals.append(self._create_signal(contradiction, rel))
        
        return signals
```

#### 2.1.2 LLM 任务处理器（OpenClaw 集成）

```python
# llm_task_processor.py
# 由 OpenClaw Agent 调用，处理 LLM 任务

import json
import os
from pathlib import Path
from datetime import datetime

class LLMTaskProcessor:
    """LLM 任务处理器 - 供 OpenClaw Agent 调用"""
    
    TASK_DIR = Path("/tmp/llm_tasks")
    RESULT_DIR = Path("/tmp/llm_results")
    
    def __init__(self):
        self.TASK_DIR.mkdir(exist_ok=True)
        self.RESULT_DIR.mkdir(exist_ok=True)
    
    def get_pending_tasks(self) -> List[Dict]:
        """获取待处理的 LLM 任务"""
        tasks = []
        for task_file in self.TASK_DIR.glob("*.json"):
            with open(task_file, 'r') as f:
                task = json.load(f)
                if not task.get("processed", False):
                    tasks.append({
                        "file": task_file,
                        "data": task
                    })
        return tasks
    
    def process_task(self, task: Dict) -> str:
        """
        处理单个 LLM 任务
        
        此方法由 OpenClaw Agent 调用:
        1. 读取 task 中的 prompt
        2. 调用 kimi-k2.5 (通过 OpenClaw)
        3. 返回 LLM 响应
        """
        prompt = task["prompt"]
        
        # OpenClaw Agent 在这里调用 kimi-k2.5
        # 返回结果给调用方
        return prompt  # 实际由 OpenClaw Agent 处理
    
    def save_result(self, task_file: Path, result: str):
        """保存 LLM 处理结果"""
        result_file = self.RESULT_DIR / f"{task_file.stem}_result.json"
        
        with open(result_file, 'w') as f:
            json.dump({
                "task_id": task_file.stem,
                "result": result,
                "processed_at": datetime.now().isoformat()
            }, f)
        
        # 标记任务已处理
        with open(task_file, 'r') as f:
            task = json.load(f)
        task["processed"] = True
        with open(task_file, 'w') as f:
            json.dump(task, f)


# OpenClaw Agent 调用示例
"""
# 由 OpenClaw Agent 定期执行的任务

from llm_task_processor import LLMTaskProcessor

processor = LLMTaskProcessor()
tasks = processor.get_pending_tasks()

for task in tasks:
    # 调用 OpenClaw 的 kimi-k2.5
    result = call_openclaw_llm(task["data"]["prompt"])
    processor.save_result(task["file"], result)
"""
```

#### 2.1.3 数据库表设计

```sql
-- 语义关系表
CREATE TABLE semantic_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_a TEXT NOT NULL,
    market_b TEXT NOT NULL,
    similarity REAL NOT NULL,
    relationship_type TEXT NOT NULL, -- 'implies', 'mutex', 'related', 'independent'
    confidence REAL NOT NULL,
    detected_by TEXT DEFAULT 'llm', -- 'llm' or 'manual'
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market_a, market_b)
);

-- 语义套利信号表
CREATE TABLE semantic_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    subtype TEXT,
    market_a TEXT,
    market_b TEXT,
    price_a REAL,
    price_b REAL,
    violation REAL,
    expected_profit REAL,
    confidence REAL,
    reasoning TEXT,
    suggested_action TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

### 2.2 Dashboard Web 集成

#### 2.2.1 架构设计

```
┌─────────────────────────────────────────┐
│              前端 (React)                │
│  ┌─────────────────────────────────┐   │
│  │  SemanticArbitrage.tsx          │   │
│  │  - 扫描按钮                     │   │
│  │  - 统计卡片                     │   │
│  │  - 信号列表                     │   │
│  │  - 违反列表                     │   │
│  └─────────────────────────────────┘   │
│              │                          │
│              ▼ Axios                    │
│  ┌─────────────────────────────────┐   │
│  │  api.ts (semanticAPI)           │   │
│  │  - scan(markets)                │   │
│  │  - getRelationships()           │   │
│  │  - getStatistics()              │   │
│  │  - test()                       │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
                   │ HTTP
                   ▼
┌─────────────────────────────────────────┐
│              后端 (Flask)                │
│  ┌─────────────────────────────────┐   │
│  │  semantic.py (Blueprint)        │   │
│  │  - POST /api/semantic/scan      │   │
│  │  - GET /api/semantic/relationships │   │
│  │  - GET /api/semantic/statistics │   │
│  │  - GET /api/semantic/test       │   │
│  └─────────────────────────────────┘   │
│              │                          │
│              ▼                          │
│  ┌─────────────────────────────────┐   │
│  │  SemanticArbitrageEngine        │   │
│  │  LogicChainAnalyzer             │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

#### 2.2.2 API 端点

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| `/api/semantic/scan` | POST | `{"markets": [...]}` | 扫描结果 | 执行语义套利扫描 |
| `/api/semantic/relationships` | GET | - | 关系列表 | 获取预定义关系 |
| `/api/semantic/statistics` | GET | - | 统计信息 | 获取统计 |
| `/api/semantic/test` | GET | - | 测试结果 | 测试连接 |

#### 2.2.3 前端组件

**SemanticArbitrage.tsx**
```typescript
// 主要功能
- 扫描按钮: 触发 API 调用
- 统计卡片: 展示 markets_scanned / semantic_signals / logic_violations / total_opportunities
- 信号列表: 展示语义套利信号
- 违反列表: 展示逻辑链违反

// 状态管理
const [loading, setLoading] = useState(false);
const [result, setResult] = useState<ScanResult | null>(null);
const [error, setError] = useState<string | null>(null);

// API 调用
const runScan = async () => {
  const response = await semanticAPI.scan(testMarkets);
  setResult(response.data);
};
```

#### 2.2.4 路由配置

**App.tsx**
```typescript
import SemanticArbitrage from './pages/SemanticArbitrage';

<Route path="/semantic-arbitrage" element={<SemanticArbitrage />} />
```

**Sidebar.tsx**
```typescript
{ key: '/semantic-arbitrage', icon: <ExperimentOutlined />, label: '语义套利' }
```

#### 2.2.5 文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| semantic.py | `dashboard/backend/app/api/semantic.py` | Flask API Blueprint |
| SemanticArbitrage.tsx | `dashboard/frontend/src/pages/SemanticArbitrage.tsx` | React 页面组件 |
| api.ts | `dashboard/frontend/src/services/api.ts` | API 服务封装 |
| App.tsx | `dashboard/frontend/src/App.tsx` | 路由配置 |
| Sidebar.tsx | `dashboard/frontend/src/components/Layout/Sidebar.tsx` | 导航菜单 |
