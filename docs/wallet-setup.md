# Polymarket 数字钱包接入准备文档

> 本文档记录 Polymarket API 接入和数字钱包连接的技术方案
> 创建时间: 2026-03-15
> 状态: 准备阶段

---

## 📋 目录

1. [API 接入方案](#api-接入方案)
2. [钱包连接方案](#钱包连接方案)
3. [安全建议](#安全建议)
4. [开通步骤](#开通步骤)
5. [技术调研任务](#技术调研任务)
6. [待确认事项](#待确认事项)

---

## API 接入方案

### 1. API 端点

| 类型 | 端点 | 说明 |
|-----|------|------|
| **数据API** | `https://data-api.polymarket.com` | 市场数据、交易数据 |
| **Gamma API** | `https://gamma-api.polymarket.com` | 市场列表、事件信息 |
| **CLOB API** | `https://clob.polymarket.com` | 订单簿数据 |
| **交易API** | 需通过智能合约 | 下单、撤单 |

### 2. 关键 API 接口

```python
# 获取市场列表
GET /markets?active=true&closed=false

# 获取市场详情
GET /markets/{slug}

# 获取订单簿
GET /book?token_id={token_id}&side={buy|sell}

# 获取用户持仓
GET /positions?user={wallet_address}

# 获取交易历史
GET /trades?limit=100
```

### 3. 交易执行方式

**重要**: Polymarket 没有传统 REST 交易 API，交易通过智能合约完成

```
交易流程：
1. 用户签名交易数据（私钥）
2. 提交到 Polygon 区块链
3. 智能合约执行撮合
4. 链上确认后成交
```

**需要使用的库**:
- `web3.py` - 与区块链交互
- `eth-account` - 签名交易
- 智能合约 ABI

---

## 钱包连接方案

### 方案1：MetaMask/WalletConnect（推荐）

**优点**:
- 私钥不离开用户设备
- 安全，风险可控
- 用户手动确认每笔交易

**实现**:
```python
from web3 import Web3

# 连接钱包
w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))

# 用户通过 MetaMask 授权
# 程序获取授权后的地址
wallet_address = "0x..."  # 用户授权后提供
```

### 方案2：私钥托管（风险较高）

**实现**:
```python
import os
from eth_account import Account

# 从环境变量读取私钥（不要硬编码）
private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
account = Account.from_key(private_key)

# 签名交易
signed_tx = account.sign_transaction(transaction_dict)
```

**风险**:
- 私钥泄露 = 资金被盗
- 需要严格的安全措施

---

## 安全建议

### 1. 私钥管理

```bash
# 使用环境变量，不要写入代码
export POLYMARKET_PRIVATE_KEY="0x..."

# 或使用加密文件
# 程序启动时解密，内存中使用
```

### 2. 权限控制

```python
# 建议权限
PERMISSIONS = {
    "read_only": True,           # 默认只读
    "trading": False,            # 交易需额外授权
    "max_daily_volume": 1000,    # 日交易限额
}
```

### 3. 交易确认

```python
# 每笔交易前确认
def confirm_trade(market, side, amount, price):
    message = f"""
    确认交易:
    市场: {market}
    方向: {side}
    金额: ${amount}
    价格: ${price}
    
    确认? (yes/no)
    """
    # 发送 Telegram 确认
    # 等待用户回复
```

---

## 开通步骤

### 步骤1：准备钱包

1. **安装 MetaMask**
   - 浏览器插件或手机App
   - 创建新钱包或导入现有钱包

2. **添加 Polygon 网络**
   ```
   网络名称: Polygon
   RPC URL: https://polygon-rpc.com
   链ID: 137
   货币符号: MATIC
   ```

3. **充值 USDC**
   - 从交易所提 USDC 到 Polygon 网络
   - 需要少量 MATIC 作为 gas 费

### 步骤2：连接 Polymarket

1. 访问 `https://polymarket.com`
2. 点击 "Connect Wallet"
3. 选择 MetaMask
4. 授权连接
5. 签名验证

### 步骤3：获取 API 凭证

**目前 Polymarket 没有正式的交易 API**，但可以通过以下方式：

1. **数据读取**：直接调用公共 API（无需认证）
2. **交易执行**：通过智能合约 + 钱包签名

---

## 技术调研任务

### 待完成任务

- [ ] **Polymarket 智能合约分析**
  - 找到交易合约地址
  - 分析合约 ABI
  - 了解交易数据结构

- [ ] **Python 交易库开发**
  - 封装合约调用
  - 实现签名功能
  - 错误处理

- [ ] **测试网验证**
  - 使用 Mumbai 测试网
  - 小额测试交易
  - 验证流程正确性

- [ ] **安全审计**
  - 私钥管理方案
  - 交易确认机制
  - 异常处理

---

## 待确认事项

### 需要你确认的问题

1. **钱包选择**:
   - [ ] MetaMask（推荐）
   - [ ] 其他钱包

2. **私钥管理**:
   - [ ] 手动确认每笔交易（安全）
   - [ ] 程序自动签名（需严格风控）

3. **初始资金**:
   - 计划投入多少 USDC 测试？
   - 是否需要先观察，暂不投入？

4. **法律合规**:
   - 你所在地区是否允许使用 Polymarket？
   - 是否需要咨询法律意见？

---

## 实施建议

### 阶段1：准备阶段（当前）
- ✅ 完成技术调研
- ⏳ 确认钱包方案
- ⏳ 法律合规确认

### 阶段2：测试阶段（未来）
- 使用 Mumbai 测试网
- 小额测试交易
- 验证流程正确性

### 阶段3：生产阶段（未来）
- 正式接入主网
- 设置风险控制
- 开始自动交易

---

## 相关文档

- [系统帮助文档](../README.md)
- [操作日志](../../memory/2026-03-15.md)

---

*文档版本: v1.0*
*最后更新: 2026-03-15*
*维护者: 虾头 🦐*
