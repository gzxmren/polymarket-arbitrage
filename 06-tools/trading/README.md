# 06-tools/trading - 交易工具

## 🔧 工具列表

| 工具 | 功能 | 来源 |
|------|------|------|
| `mia-trader.py` | 自动交易执行 | mia-polymarket-trader |
| `order-manager.py` | 订单管理 | custom |
| `risk-manager.py` | 风险控制 | custom |

## ⚙️ 配置

```bash
# 设置API密钥
export POLYMARKET_API_KEY="your-key"
export POLYMARKET_PRIVATE_KEY="your-wallet-key"
```

## 🛡️ 风控规则

- 单笔最大 5% 仓位
- 止损线 20%
- 日度报告
