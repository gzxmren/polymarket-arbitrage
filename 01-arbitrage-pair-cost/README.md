# 01-arbitrage-pair-cost - Pair Cost 套利

## 🎯 策略原理

当 `YES价格 + NO价格 < $1.00` 时，存在无风险套利机会。

**例子：**
- YES = $0.62
- NO = $0.35
- Pair Cost = $0.97
- 套利空间 = $0.03 (3%)

## ✅ 执行步骤

1. 扫描所有市场，计算 Pair Cost
2. 筛选 Pair Cost < $0.98 的机会
3. 同时买入 YES + NO
4. 等待结算，稳赚差价

## ⚠️ 注意事项

- 机会稀少（市场通常有效）
- 需考虑手续费
- 资金锁定到结算日

## 📁 文件

- `scanner.py` - 扫描脚本
- `opportunities.json` - 机会记录
- `backtest/` - 历史回测
