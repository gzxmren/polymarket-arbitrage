# 11-collector — Polymarket 重生 · 无偏成交流采集器

本目录是项目重生的数据采集层,**严格兑现** [`docs/DATA_CONTRACT_REBIRTH_2026-07-22.md`](../docs/DATA_CONTRACT_REBIRTH_2026-07-22.md)(数据契约 v1.1,已冻结)。

## 判据先行(不得偏离契约)
- 头号原则:采集时不施加任何"未来策略可能想改的过滤";谁算大户/采哪些/多大算大,推迟到分析时。
- token→阵营:**按 asset 在 clobTokenIds 的位置定下标**,不信接口的 `outcomeIndex`(实测 93% 是垃圾 999)。
- 存储:**append-only 按日 Parquet + DuckDB 零 ETL 直查**;compaction 合并小文件,不落可变主表。
- 点位时刻:双时间戳(成交 `timestamp` / 采集 `ingested_at`),只增不改;as_of 用 DuckDB ASOF JOIN 机械强制。

## 模块
| 文件 | 职责 | 契约条款 |
|---|---|---|
| `probe_engine.py` | 开工仪式探针:接口/限流/翻页上限/token映射/宇宙量级(可复现) | §3/§9/§10 |
| `discovery_service.py` | **Firehose 发现驱动**(v1.2):采样全局成交发现活跃市场 + HFT 剔除 + 按需 Gamma 注册 + token/结算解析 | §3/§4.2/§7 |
| `storage_engine.py` | append-only 按日 Parquet 原子写 + compaction + DuckDB 直查/去重视图/审计心跳 | §4.4/§5/§7 |
| `collector_core.py` | 按市场轮询主循环:分页/重试/解析(拒绝+计数)/增量去重/offset溢出兜底 | §3/§4.1/§7 |

## 运行
```bash
pip install duckdb            # pyarrow 已装
python3 11-collector/probe_engine.py                 # 开工仪式(全读公开接口)
python3 11-collector/discovery_service.py --once     # 发现+注册一轮
python3 11-collector/collector_core.py --once        # 轮询一轮(骨架)
```

## 状态
- 判据 **v1.2** 已审定(Firehose 发现驱动)。核心路径已**真实端到端验证**(2026-07-22 试跑):
  发现 3000 笔 → 416 活跃市场 → 注册 29 → 轮询落库 **11777 笔真实成交**;
  **parse_reject=0**(asset 定位铁律零失败)、offset 溢出保留近端+计数(诚实截断)、
  append-only Parquet + DuckDB 直查 + 去重视图全通。
- 常驻守护(systemd timer 包装)、告警通道、compaction 调度、自适应升频为 TODO(见各文件末)。
- 数据落 `11-collector/data/`(git 忽略)。
