# 鲸鱼深度分析（LLM）设计文档

## 📋 概述
混合方案：实时分析（程序代码）+ 深度分析（LLM），用户手动触发深度分析。

---

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    鲸鱼分析系统                          │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐       │
│  │   实时分析          │  │   深度分析          │       │
│  │   (程序代码)        │  │   (LLM)             │       │
│  ├─────────────────────┤  ├─────────────────────┤       │
│  │ • 每15分钟自动刷新   │  │ • 用户手动触发      │       │
│  │ • 信号强度评分      │  │ • 调用LLM API       │       │
│  │ • 多维度评分        │  │ • 深度策略分析      │       │
│  │ • 快速、免费        │  │ • 自然语言解读      │       │
│  │ • 显示在顶部        │  │ • 显示在独立卡片    │       │
│  └─────────────────────┘  └─────────────────────┘       │
│                                                          │
│  ┌─────────────────────────────────────────────────┐     │
│  │              缓存层                              │     │
│  │  • 深度分析结果缓存24小时                        │     │
│  │  • 数据变化>20%才刷新                            │     │
│  │  • 按钱包地址+数据哈希索引                        │     │
│  └─────────────────────────────────────────────────┘     │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 💾 数据模型

### 数据库表

```sql
-- 深度分析缓存表
CREATE TABLE whale_deep_analysis (
    wallet TEXT PRIMARY KEY,
    content TEXT NOT NULL,           -- LLM返回的深度分析内容
    model TEXT,                       -- 使用的模型(gpt-4/claude-3等)
    generated_at TIMESTAMP,           -- 生成时间
    data_hash TEXT,                   -- 数据哈希，用于判断是否需要刷新
    cost REAL,                        -- 调用成本(USD)
    expires_at TIMESTAMP              -- 缓存过期时间
);

-- 调用频率限制表(可选)
CREATE TABLE analysis_rate_limit (
    user_id TEXT,
    wallet TEXT,
    called_at TIMESTAMP,
    PRIMARY KEY (user_id, wallet, called_at)
);
```

### TypeScript 接口

```typescript
interface WhaleAnalysis {
  // 实时分析（程序代码生成）
  realtime: {
    updated_at: string;
    signal_strength: {...};
    dimensions: {...};
    composite_score: number;
    copy_score: number;
    interpretation: string;
  };
  
  // 深度分析（LLM生成）
  deep?: {
    generated_at: string;
    model: string;
    content: string;
    cost?: number;
    expires_at?: string;
  };
}
```

---

## 🔌 API 设计

### 获取实时分析
```
GET /api/whales/:wallet/analysis
```
**响应**: 程序代码生成的实时分析

### 触发深度分析
```
POST /api/whales/:wallet/deep-analysis
```
**请求体**:
```json
{
  "force_refresh": false  // 是否强制刷新缓存
}
```

**响应**:
```json
{
  "wallet": "0x...",
  "content": "深度分析内容...",
  "model": "gpt-4",
  "generated_at": "2026-03-16T04:00:00Z",
  "cost": 0.05,
  "from_cache": false
}
```

### 获取深度分析（不触发新调用）
```
GET /api/whales/:wallet/deep-analysis
```
**响应**: 返回缓存的深度分析，如果没有则返回空

---

## 🧠 LLM Prompt 设计

```python
WHALE_ANALYSIS_PROMPT = """
你是一位专业的预测市场分析师。请分析以下鲸鱼交易者的行为和策略：

【基本信息】
- 钱包: {wallet_short}
- 持仓价值: ${total_value:,}
- 市场数: {position_count}
- Top5占比: {top5_ratio:.1%}
- 24h变动: {changes_count}次
- 总盈亏: ${total_pnl:+,.0f}

【持仓明细】
{positions_summary}

【近期变动】
{changes_summary}

请提供以下分析：

## 1. 策略判断
这位鲸鱼的交易策略是什么？看空还是看多？集中在哪些主题/事件？

## 2. 信号强度评估
- 可靠度: 高/中/低
- 理由: 为什么给出这个评级？

## 3. 操作建议
作为跟随者，应该如何操作？具体建议是什么？

## 4. 风险提示
有哪些潜在风险需要注意？最坏情况是什么？

## 5. 对比分析
与同类鲸鱼相比，这位有什么独特之处？

要求：
- 分析要具体，引用实际数据
- 给出明确的操作建议
- 指出关键风险点
- 语言简洁专业，适合快速阅读
- 总字数控制在500字以内
"""
```

---

## 💰 成本控制

### 模型选择
| 场景 | 推荐模型 | 成本/次 | 特点 |
|-----|---------|---------|------|
| 默认 | GPT-3.5-turbo | ~$0.002 | 快速、便宜、够用 |
| 深度 | GPT-4 | ~$0.05 | 更智能、更深入 |
| 备选 | Claude 3 Haiku | ~$0.001 | 快速、便宜 |

### 限制策略
```python
RATE_LIMITS = {
    "per_user_per_hour": 5,      # 每用户每小时最多5次
    "per_wallet_per_day": 3,     # 每钱包每天最多3次
    "max_cost_per_day": 1.0,     # 每天最大成本$1
}
```

---

## 🔄 缓存策略

### 缓存键
```python
cache_key = f"{wallet}:{data_hash}"
# data_hash = hash(持仓价值 + 市场数 + Top5占比 + 24h变动次数)
```

### 刷新条件
1. 缓存超过24小时
2. 数据变化超过20%
3. 用户强制刷新

### 存储方式
- 主存储：SQLite 数据库
- 备选：Redis（如果有）

---

## 🎨 前端界面

### 深度分析卡片

```tsx
<Card 
  title={
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span>🧠 AI 深度分析</span>
      {deepAnalysis ? (
        <Tag color="green">✅ 已生成</Tag>
      ) : (
        <Button 
          type="primary" 
          onClick={generateDeepAnalysis}
          loading={generating}
        >
          生成深度分析
        </Button>
      )}
    </div>
  }
>
  {deepAnalysis ? (
    <>
      <div style={{ whiteSpace: 'pre-wrap' }}>
        {deepAnalysis.content}
      </div>
      <Divider />
      <div style={{ fontSize: '12px', color: '#999' }}>
        <ClockOutlined /> {new Date(deepAnalysis.generated_at).toLocaleString()}
        {deepAnalysis.cost && ` · 💰 $${deepAnalysis.cost.toFixed(3)}`}
        {deepAnalysis.from_cache && ` · 📦 来自缓存`}
        <Button 
          size="small" 
          style={{ marginLeft: 16 }}
          onClick={() => generateDeepAnalysis(true)}
        >
          强制刷新
        </Button>
      </div>
    </>
  ) : (
    <Empty description={
      <div>
        <p>暂无深度分析</p>
        <p style={{ fontSize: '12px', color: '#999' }}>
          点击上方按钮生成，大约需要2-5秒
        </p>
      </div>
    } />
  )}
</Card>
```

---

## 📁 文件结构

```
dashboard/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── whales.py              # 添加 deep-analysis 路由
│   │   ├── services/
│   │   │   ├── whale_analyzer.py      # 实时分析（已有）
│   │   │   └── whale_deep_analyzer.py # 深度分析（新增）
│   │   └── models/
│   │       └── database.py            # 添加 deep_analysis 表
│   └── config.py                      # LLM API 配置
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   └── WhaleDetail.tsx        # 添加深度分析卡片
│       └── services/
│           └── api.ts                 # 添加 deep-analysis API
│
└── docs/
    └── whale-deep-analysis-design.md  # 本文档
```

---

## 🚀 实现步骤

1. **数据库** - 添加 `whale_deep_analysis` 表
2. **后端服务** - 创建 `whale_deep_analyzer.py`
3. **后端 API** - 添加 `POST /:wallet/deep-analysis` 路由
4. **前端 API** - 添加 `generateDeepAnalysis` 方法
5. **前端页面** - 在 WhaleDetail 添加深度分析卡片
6. **配置** - 添加 LLM API Key 配置
7. **测试** - 验证完整流程

---

## ⚠️ 注意事项

1. **API Key 安全** - 不要提交到代码库，使用环境变量
2. **成本控制** - 设置每日上限，防止意外高额费用
3. **错误处理** - LLM 调用失败时优雅降级
4. **隐私保护** - 不发送敏感信息到 LLM

---

*设计时间: 2026-03-16 04:02 CST*
*设计者: 虾头 🦐*
