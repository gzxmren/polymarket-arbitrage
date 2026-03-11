# 02-arbitrage-cross-market - 跨平台套利

## 🎯 策略原理

同一事件在不同平台定价不同，买低卖高。

**例子：**
| 平台 | BTC>$70k概率 |
|------|-------------|
| Polymarket | 62% |
| Manifold | 71% |
| Metaculus | 58% |

**操作：** 买入 Polymarket YES + Manifold NO

## ✅ 执行步骤

1. 聚合多平台数据
2. 匹配相同/相似事件
3. 计算价差 > 5% 的机会
4. 双边下注，锁定利润

## ⚠️ 注意事项

- 事件定义可能不完全相同
- 结算时间可能不同
- 平台手续费差异

## 📁 文件

- `aggregator.py` - 数据聚合
- `matcher.py` - 事件匹配
- `arbitrage-bot.py` - 套利执行
