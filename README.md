# Polymarket 交易项目

> 系统化预测市场交易策略研究与执行

---

## 📁 目录结构

```
polymarket-project/
├── 00-learning/              # 学习资料与笔记
├── 01-arbitrage-pair-cost/   # Pair Cost 套利策略
├── 02-arbitrage-cross-market/# 跨平台套利策略
├── 03-momentum-trading/      # 动量交易策略
├── 04-whale-following/       # 鲸鱼跟随策略
├── 05-sentiment-contrarian/  # 情绪逆向策略
├── 06-tools/                 # 工具脚本
│   ├── analysis/             # 分析工具
│   ├── trading/              # 交易执行
│   └── monitoring/           # 监控脚本
├── 07-data/                  # 数据存储
├── 08-backtests/             # 回测结果
└── 09-docs/                  # 文档与API参考
```

---

## 🎯 当前状态

- [x] 项目结构初始化
- [x] 学习资料整理（8篇核心文档）
- [x] Pair Cost 套利扫描器
- [x] 跨平台套利扫描器
- [x] 鲸鱼追踪器 V2
- [x] Telegram 通知系统
- [x] 综合监控器
- [ ] 策略回测框架
- [ ] 实盘交易执行

## 🚀 快速开始

### 运行监控

```bash
cd 06-tools/monitoring

# 配置 Telegram 通知
cp .env.example .env
# 编辑 .env 填入你的 Bot Token 和 Chat ID

# 运行综合监控
python3 polymarket_monitor.py
```

### 单独运行工具

```bash
cd 06-tools/analysis

# Pair Cost 扫描
python3 pair_cost_scanner.py

# 跨平台套利扫描
python3 cross_market_scanner.py

# 鲸鱼追踪
python3 whale_tracker_v2.py
```

---

## 📚 已安装技能

- `polymarket-analysis` - 市场分析、鲸鱼追踪
- `prediction-market-aggregator` - 跨平台套利扫描
- `mia-polymarket-trader` - 自动化交易
