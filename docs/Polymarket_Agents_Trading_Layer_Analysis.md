# Polymarket/agents 交易执行层深度拆解

**分析时间**: 2026-04-16 12:22 CST
**目标**: 评估集成到我们系统的可行性

---

## 一、交易执行层核心代码拆解

### 1. 初始化流程（3步）

```
Step 1: Web3 连接 Polygon 链
  └─ RPC: https://polygon-rpc.com
  └─ Chain ID: 137 (Polygon Mainnet)

Step 2: CLOB Client 初始化
  └─ 私钥 → 派生 API 凭证（api_key, api_secret, api_passphrase）
  └─ ClobClient(clob_url, key=private_key, chain_id=137)

Step 3: 合约授权（6次链上交易，只需执行一次）
  └─ USDC approve → CTF Exchange
  └─ CTF setApprovalForAll → CTF Exchange
  └─ USDC approve → Neg Risk CTF Exchange
  └─ CTF setApprovalForAll → Neg Risk CTF Exchange
  └─ USDC approve → Neg Risk Adapter
  └─ CTF setApprovalForAll → Neg Risk Adapter
```

### 2. 下单方式（2种）

#### 限价单（Limit Order）
```python
execute_order(price, size, side, token_id)
  └─ client.create_and_post_order(OrderArgs(
       price=0.65,      # 价格
       size=10,          # USDC 金额
       side=BUY,         # BUY 或 SELL
       token_id=token_id # 市场 token
     ))
```

#### 市价单（Market Order）
```python
execute_market_order(market, amount)
  └─ client.create_market_order(MarketOrderArgs(
       token_id=token_id,
       amount=amount      # USDC 金额
     ))
  └─ client.post_order(signed_order, orderType=OrderType.FOK)
     # FOK = Fill or Kill（全部成交或取消）
```

### 3. 关键合约地址

| 合约 | 地址 | 用途 |
|------|------|------|
| USDC | 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174 | 支付代币 |
| CTF | 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 | 条件代币框架 |
| Exchange | 0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e | 交易所 |
| Neg Risk Exchange | 0xC5d563A36AE78145C45a50134d48A1215220f80a | 负风险交易所 |
| Neg Risk Adapter | 0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296 | 负风险适配器 |

### 4. 核心依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| py-clob-client | 0.17.5 | Polymarket 官方 CLOB SDK |
| py-order-utils | 0.3.2 | 订单签名工具 |
| web3 | 6.11.0 | 以太坊/Polygon 交互 |
| eth-account | 0.13.1 | 钱包管理 |

---

## 二、与我们系统的集成评估

### 我们已有的能力 ✅

| 能力 | 状态 | 说明 |
|------|------|------|
| 市场数据获取 | ✅ 完整 | Gamma API + Data API |
| 鲸鱼追踪 | ✅ 完整 | whale_states + PolyCop（114个地址）|
| 信号源 | ✅ 完整 | PolyCop Score + 鲸鱼活跃度 |
| 持仓分析 | ✅ 完整 | CLOB API positions/trades |

### 我们缺少的能力 ❌

| 能力 | 状态 | 说明 |
|------|------|------|
| 钱包连接 | ❌ | 需要 Polygon 钱包 + 私钥 |
| CLOB 下单 | ❌ | 需要 py-clob-client |
| 合约授权 | ❌ | 首次使用需要6次链上交易 |
| 风控系统 | ❌ | 止损、仓位管理、资金分配 |

---

## 三、集成方案设计

### 架构图

```
┌─────────────────────────────────────────────┐
│              我们的系统（已有）                │
│                                             │
│  PolyCop Signal ──→ 信号评估 ──→ 交易决策    │
│  whale_states  ──→ 鲸鱼跟单 ──↗              │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │    交易执行层（需要集成）             │    │
│  │                                     │    │
│  │  钱包管理 → 订单构建 → 下单执行      │    │
│  │       ↑                    ↓        │    │
│  │   风控检查              成交确认      │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

### 集成步骤（4阶段）

#### 阶段1：基础设施（1-2天）
- 安装 py-clob-client + web3
- 创建钱包管理模块（私钥加密存储）
- 实现 CLOB Client 初始化
- 执行合约授权

#### 阶段2：只读验证（1天）
- 查询 USDC 余额
- 查询 orderbook
- 获取市场价格
- 模拟下单（不执行）

#### 阶段3：小额测试（2-3天）
- $1 限价单测试
- $1 市价单测试
- 订单状态查询
- 成交确认

#### 阶段4：策略集成（3-5天）
- 鲸鱼跟单策略 → 下单
- 风控模块（止损、仓位限制）
- 通知系统（Telegram 报告）

---

## 四、风险评估

### 安全风险

| 风险 | 级别 | 说明 | 缓解方案 |
|------|------|------|---------|
| **私钥泄露** | 🔴 高 | 私钥控制全部资金 | 加密存储 + 环境变量 |
| **无限授权** | 🟡 中 | approve(MAX_INT) 授权无限额度 | 限制授权额度 |
| **无风控** | 🔴 高 | 原代码无止损/仓位限制 | 必须自建风控 |
| **递归重试** | 🟡 中 | 失败时无限递归重试 | 添加重试次数限制 |

### 原代码问题（必须修复）

| 问题 | 严重性 | 说明 |
|------|--------|------|
| bare except | 🔴 | `except: pass` 吞掉所有错误 |
| 递归重试 | 🔴 | `self.one_best_trade()` 无限递归 |
| 私钥明文 | 🔴 | `.env` 文件存储私钥 |
| 无日志 | 🟡 | 交易没有日志记录 |
| GPT-3.5 | 🟡 | 模型过时，需要替换 |
| 无测试 | 🟡 | 交易逻辑没有单元测试 |

---

## 五、我们应该复用什么 vs 重写什么

### 直接复用 ✅

| 模块 | 原因 |
|------|------|
| **合约地址** | 官方地址，不需要改 |
| **合约 ABI** | ERC20/ERC1155 标准 ABI |
| **py-clob-client 用法** | 官方 SDK，用法正确 |
| **授权流程** | 6步授权逻辑完整 |

### 需要重写 ❌

| 模块 | 原因 |
|------|------|
| **策略引擎** | 原策略太简单，我们用鲸鱼跟单 |
| **LLM 决策** | 原用 GPT-3.5，我们有更好的模型 |
| **风控系统** | 原代码完全没有 |
| **日志/通知** | 需要完整的交易日志和 Telegram 通知 |
| **钱包管理** | 需要加密存储，不能明文 |

---

## 六、最小可行产品（MVP）

### 鲸鱼跟单 MVP

```python
class WhaleFollower:
    def __init__(self):
        self.polymarket = PolymarketTrader()  # 交易执行
        self.risk_manager = RiskManager()      # 风控
        
    def follow_whale_trade(self, whale_addr, market_id):
        # 1. 获取鲸鱼最新交易
        whale_trade = get_whale_latest_trade(whale_addr)
        
        # 2. 风控检查
        if not self.risk_manager.check(whale_trade):
            return "风控拒绝"
        
        # 3. 计算跟单金额（鲸鱼金额的 1-5%）
        amount = whale_trade.amount * 0.02
        
        # 4. 执行交易
        result = self.polymarket.execute_order(
            price=whale_trade.price,
            size=amount,
            side=whale_trade.side,
            token_id=whale_trade.token_id
        )
        
        # 5. 通知
        send_telegram(f"跟单成功: {result}")
```

### 风控规则

| 规则 | 限制 |
|------|------|
| 单笔最大金额 | $50 |
| 日交易总额 | $200 |
| 最大持仓数 | 5 个市场 |
| 止损线 | -20% |
| 最低流动性 | $10,000 |

---

## 七、结论与建议

### 可行性评估：✅ 可行

**理由**：
1. 交易执行层代码完整，py-clob-client 是官方 SDK
2. 我们的信号源（PolyCop + 鲸鱼）远强于原仓库
3. 技术难度不高，主要是集成和风控

### 建议路线

| 步骤 | 时间 | 优先级 |
|------|------|--------|
| 1. 准备钱包 + 充值 USDC | 1天 | P0 |
| 2. 安装依赖 + 初始化 | 1天 | P0 |
| 3. 只读测试（余额/价格） | 1天 | P0 |
| 4. $1 小额交易测试 | 2天 | P1 |
| 5. 鲸鱼跟单策略集成 | 3天 | P1 |
| 6. 风控系统 | 2天 | P0 |
| 7. 通知系统 | 1天 | P2 |

**总计**：约 10 天可完成 MVP

### 前置条件

| 条件 | 状态 | 说明 |
|------|------|------|
| Polygon 钱包 | ❌ 需要 | MetaMask 或新建钱包 |
| USDC 余额 | ❌ 需要 | 建议初始 $100-500 |
| OpenAI API Key | ❌ 需要（如用LLM决策）| 或用我们自己的模型 |
| 科学上网 | ⚠️ 可能 | Polymarket API 可能需要 |

---

*分析完成时间: 2026-04-16 12:30 CST*
*分析人: 虾头*