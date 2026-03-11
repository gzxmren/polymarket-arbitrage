# 06-tools/analysis - 分析工具

## 🔧 工具列表

| 工具 | 功能 | 来源 |
|------|------|------|
| `market-analyzer.py` | 市场综合分析 | polymarket-analysis |
| `user-profile.py` | 用户持仓分析 | polymarket-analysis |
| `cross-market-scanner.py` | 跨平台扫描 | prediction-market-aggregator |
| `arbitrage-detector.py` | 套利检测 | prediction-market-aggregator |

## 📊 使用方法

```bash
# 分析单个市场
python3 market-analyzer.py <market_url>

# 分析用户
python3 user-profile.py <wallet_address>

# 扫描套利
python3 cross-market-scanner.py
```
