# 下一步设计与计划 — 三方向(编码前文档)

> 制定:2026-06-04。承接回测主线(`docs/BACKTEST_DESIGN_2026-06-04.md`):跟鲸鱼已证伪不赚钱。
> **本文档目的:在写任何代码前,把三个候选方向的思路、计划步骤、设计细节固定下来,供 review 与编码参考。**
> 铁律不变:不信 demo 信回测数字;跑不出正期望就砍;先验证再投。

---

## §0 推荐排序与理由

| 序 | 方向 | 为什么这个序 | 能否用现有数据出结论 |
|---|---|---|---|
| 1 | **补引擎单元测试**(方向 C) | 最快、无依赖;堵住"金融计算引擎零测试"的信誉缺口,后续策略都复用同一 engine,先把地基钉死 | N/A(工程) |
| 2 | **C-2 跨市场套利**(方向 A) | 推进 Roadmap 策略判决;**但现有数据无法回测**(无历史时序 + Manifold 非真钱),实为"验前提 + 建等价匹配 + 起采集" | ❌ 只能验premise,需forward采集 |
| 3 | **引擎复活攒数据**(方向 B) | 收益最慢但解锁未来:为鲸鱼重验 + 跨市场历史 提供 forward 数据。曾决策"先修密钥暂不常驻",此处给出受管服务化方案 | N/A(运维) |

> 依赖关系:方向 2 与方向 3 互锁——C-2 真正能回测需要方向 3 持续采集出历史时序。
> 故现实路径:**先 1(测试)→ 再 2 的"验前提"快检(便宜,今天就能做)→ 视前提结论决定是否值得做 3 的采集**。

---

## §A 方向 C —— 引擎单元测试(优先,先做)

> **✅ 已完成(2026-06-04)**:`10-tests/unit/test_backtest_engine.py`,34 用例全绿,engine 覆盖率 **95%**
> (costs 97/data 94/metrics 97/portfolio 93)。顺带把 `pearson` 从 `run_whale_oos.py` 上移进 `engine/metrics.py`
> (相关性属 metrics、可复用可测);conftest 加 `08-backtests` 到 path;CI compileall + unit 已含本套件。
> 既有 21 unit 无回归(共 55 全绿)。

### 目标
给 `08-backtests/engine/` 的金融计算加单元测试,纳入既有 `10-tests/` + CI(`.github/workflows/ci.yml`)。
目标覆盖率:engine 核心函数 ≥ 80%。

### 思路
engine 是纯函数 over 数据,极易测:构造**合成**价格序列 + 信号(不连真实库),断言每步数值。
遵守项目铁律:测试用合成 fixture,**绝不**写生产 `07-data/`,可用内存 sqlite 或纯对象。

### 关键设计
- 新增 `10-tests/unit/test_backtest_engine.py`(与现有 unit 测试同目录,`conftest.py` 已把 analysis/monitoring 上 sys.path;需补 `08-backtests` 到 path)。
- Fixture:手搓 `PriceSeries`(已知日期→已知 Yes 价)+ `Signal` 列表,数值可手算验证。
- 分模块用例:

| 模块 | 用例(断言点) |
|---|---|
| `costs.py` | `entry_fill` 买入价变高/卖出变低、边界裁剪到[0,1];`gas_drag`=g/notional;`deployable`=min(want, k·cap);`impact_drag` 超容量线性;notional=0 不崩 |
| `data.py` | `outcome_price` Yes/No/多结果(None);`_to_date` 解析 ISO 带时区 + 纯日期 + 脏值;`PriceSeries.yes_on_or_after`(命中/边界/越界 None)、`terminal`、`is_resolved`(收敛+过期才 True) |
| `portfolio.py` | `simulate_one`:realistic 用次日快照入场、optimistic 用 implied_px;`+Nd` 取 entry+N 后首快照;`resolution` 用终值;不可入场/不可标记→None;收益率 = (mark−entry)/entry 手算对齐 |
| `metrics.py` | `_agg` 空/单元素/正常;`whale_alpha` min_trades 过滤 + 降序;`_pearson` 已知相关数列(+1/−1/0)、<3 点 None、零方差 None |

### 计划步骤
1. 写 fixture 工厂(合成 PriceSeries/Signal)。
2. 逐模块补用例(costs→data→portfolio→metrics,从纯到组合)。
3. `python3 -m pytest 10-tests/unit/test_backtest_engine.py -v` 全绿。
4. 跑 `pytest --cov=engine`(若装 coverage)确认 ≥80%;不足补用例。
5. 确认 CI 能发现 `08-backtests`(ci.yml 的 compileall/pytest 路径或 PYTHONPATH)。

### 验收
全部用例绿 + 覆盖率达标 + CI 通过;故意改坏一行成本公式能被测试抓到(变异冒烟)。

### 风险
engine import 用相对包(`from .costs`),测试需以包方式导入或调 sys.path;`run_*.py` 用 `sys.path.insert`,测试沿用同法即可。

---

## §B 方向 A —— C-2 跨市场套利(验前提优先,暂不回测)

### 残酷前提(必须先正视)
- **现有数据无法回测**:`cross_market_arbitrage` 是 alerts 表非时序,仅 1 行,且是**错配假阳**
  (event="Trump out before GTA VI" 配到 Manifold "GTA6 before PS6",match_rate 50%,毫不相关)。
- **现有对手盘不可真实套利**:Manifold = play-money(mana 虚拟币,无真金对手仓);
  Metaculus = 纯预测无交易(且 404 禁用)。→ **唯一真钱可套利对 = Polymarket ↔ Kalshi**(已有 `kalshi_fetcher.py`)。
- 故 C-2 不是"在历史上跑回测",而是先回答 premise:**两个真钱场里,到底存不存在同一事件、可同时持仓、能收敛获利的配对?**

### 思路(分三关,任一关死则砍)
1. **等价性关**(最可能死):同事件 + **同结算标准** + 同时点。语义匹配产生垃圾(那 1 行为证)。
   需结算规则级核对,非标题相似度。
2. **可执行关**:Kalshi 受监管(KYC/地域限制/单独资金),Polymarket 链上;两边手续费、
   最小单位、结算时点不同。价差可能到 resolution 才收敛 → **资金锁定期**吃掉年化。
3. **正期望关**:扣两边手续费 + 资金占用 + FX/gas + 等价性残差风险后,价差是否还正。

### 关键设计
- **不复用** semantic_arbitrage 的 LLM 模糊匹配做信号源(已证生成垃圾)。改为:
  - 手工种子:人工筛 5~10 个**确证同事件**的 Polymarket↔Kalshi 配对(如同一选举/同一经济数据)作金标准。
  - 在金标准上量化:历史价差是否存在、是否收敛、收敛需多久。
- 复用回测 engine 的成本框架(`costs.py`)扩一个 `CrossVenueCost`(两边费率 + 资金占用天数 × 资金成本)。
- 数据:Kalshi 历史价 + Polymarket 历史价。**我们当前无 Kalshi 历史** → 需 forward 采集(接方向 3)。

### 计划步骤(本轮只做 1–3,便宜)
1. **premise 快检**(今天可做,无需建系统):拉当前 Polymarket + Kalshi 活跃市场,人工找是否存在
   确证同事件配对;若一个都难找 → C-2 premise 存疑,直接给判决"暂不投入"。
2. 若找到配对:核对结算标准是否真等价,记录价差快照。
3. 写一页 premise 结论(存在性 + 等价性难度 + 是否值得起采集)。
4. (仅 premise 通过才做)设计 Kalshi↔Polymarket forward 采集 + 收敛回测,挂方向 3。

### 验收
产出 premise 判决文档:🟢值得起采集 / 🔴 premise 不成立暂砍。**不在垃圾匹配上叠系统。**

### 风险
Kalshi 地域/KYC 可能令"可执行关"对本账户直接不成立——若如此,C-2 实盘价值归零,应明确告知用户。

---

## §C 方向 B —— 监控引擎复活 + 受管服务化(攒数据,最后做)

### 现状(As-Built 实据)
引擎 `06-tools/monitoring/polymarket_monitor_v2.py` 曾停摆 ~6 周(末报告 2026-04-22),
无 cron/systemd 调度;曾决策"先修密钥,暂不常驻"。Dashboard 后端的 data_sync 由 APScheduler 每 5 分钟跑。
`changes` 表由 `sync_changes.py` 不规则触发(疑 OpenClaw agent/loop)。

### 目标
让引擎成为**受管服务 + 管道健康告警**(核心痛点:死了 6 周无人知),持续产出 forward 数据,
为鲸鱼重验(攒到数月样本)+ 跨市场历史采集打底。

### 思路 / 关键设计
- **受管化**:做成 `systemd --user` 定时服务(参考已有 dashboard-backend.service 的 systemd 约定,
  见记忆 [[dashboard-systemd-ops]]),或 OpenClaw cron 统一调度。**避免 nohup**(无监管、死了无声)。
- **健康告警**:看门狗——若最新 `monitor_report_*.json` / changes 时间戳超 N 分钟未更新 → Telegram 告警
  (复用 `telegram_notifier_v2.py` 统一出口)。这是"6 周无人知"的直接解药。
- **去硬编码 / 轮转**已在 P0/P2 完成;引擎复活前确认 PAIR_COST 算法缺陷(中间价 yes+no≡1)不影响
  数据采集(采集 changes/价格不依赖该算法,套利信号本就该空)。

### 计划步骤
1. 决策调度载体(systemd --user 服务 vs OpenClaw cron)——需用户拍板,涉及运维边界。
2. 写 service/timer 单元 或 cron 条目 + 看门狗脚本。
3. 起服务,验证 changes / price 快照按周期增长。
4. 看门狗故意断喂测试 → 确认告警触达。
5. 攒 1–3 月后,回到回测重验鲸鱼(样本变厚)+ 起跨市场采集。

### 验收
服务受管、重启不复活旧代码(见 [[dashboard-systemd-ops]] 坑)、看门狗能在停喂时告警、数据稳定增长。

### 风险
引擎代码虽手动验证可跑,但常驻后资源/稳定性未知;need 观察。Pair-Cost 套利信号会持续空(预期,非故障)。

---

## §D 一句话总览

1. **先补 engine 单元测试**(快、堵信誉缺口、地基钉死)。
2. **再做 C-2 premise 快检**(今天就能:真钱场只有 Polymarket↔Kalshi,先看同事件配对是否存在;不存在就诚实砍)。
3. **视结论再决定引擎复活攒数据**(解锁鲸鱼重验 + 跨市场历史,但收益慢,且涉运维边界需拍板)。

*相关:`docs/BACKTEST_DESIGN_2026-06-04.md`、`docs/ROADMAP_2026-06-03.md`、`docs/AS_BUILT_ARCHITECTURE_2026-06-03.md`*
