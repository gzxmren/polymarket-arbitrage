# 08-backtests — 回测框架

> 设计文档:`docs/BACKTEST_DESIGN_2026-06-04.md` · 路线图:`docs/ROADMAP_2026-06-03.md`
> **铁律:不信 demo 好看,信扣成本后的回测数字;跑不出正期望的策略老实砍。**

## 通用回测引擎(Roadmap 阶段 B)

```
engine/
  data.py       # DB → 标准化"信号事件流" + "价格序列"(可被任意策略复用)
  costs.py      # 成本模型:手续费/滑点/gas/容量上限,参数化 + 预设档
  portfolio.py  # 信号→入场→逐日 mark→结算 的统一回路(两档入场口径)
  metrics.py    # 扣成本期望/胜率/分层/鲸鱼 alpha/容量/类夏普
strategies/
  follow_whale.py  # 策略 1:跟鲸鱼(信号 = changes 里的 BUY/SELL)
run_follow_whale.py # 入口
results/        # 回测产物 JSON(gitignore 视情况)
backtest_pair_cost.py  # [DEPRECATED] 旧假回测,勿用
```

engine 与策略解耦,后续跨市场/Pair-Cost 策略复用同一 engine。

## 运行

```bash
# 默认 base 成本档、realistic 入场
PYTHONPATH=08-backtests python3 08-backtests/run_follow_whale.py

# 成本敏感性扫描(zero/optimistic/base/conservative)+ 落 JSON
PYTHONPATH=08-backtests python3 08-backtests/run_follow_whale.py --cost-scan --json

# 过滤小单 / 纳入卖出 / 调鲸鱼 alpha 门槛
PYTHONPATH=08-backtests python3 08-backtests/run_follow_whale.py \
    --min-notional 100 --include-sell --min-whale-trades 10
```

DB 路径走 `06-tools/analysis/config.py`(支持 `POLYMARKET_DB` 覆盖),只读打开。

## 指标

扣成本后期望 · 胜率 · 中位数(防均值被肥尾误导)· 按持有期分层(+1d/+3d/+7d/resolution)·
按快照天数分层 · 逐鲸鱼 alpha · 容量(可部署本金)· 类夏普(样本短仅参考)。

## 当前判决(2026-06-04 首跑)

- **策略 1 跟鲸鱼基线:🔴 不赚钱。** resolution 扣成本净均值 −2.29%,胜率 19.5%,中位 −0.5%;
  均值靠极少数真实长尾暴击硬撑,去掉 top1% 跌到 −8.45%。无差别跟全部鲸鱼是亏的。
- 活口:少数鲸鱼个体 alpha 体面(如某 n=35 胜率43% 净+17.7%)→ **下一步验证"精选鲸鱼"能否翻正**,
  但须警惕 1 月样本的过拟合(样本内挑赢家 = 偷看答案)。
- **Pair-Cost 无法历史回测**(没存盘口),Stage C-3 做实时盘口采集后再说。

诚实声明见报告头部:样本仅 ~1 月、逐日粒度、仅二元 Yes/No、选择偏差。
