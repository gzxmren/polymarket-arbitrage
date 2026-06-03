# Polymarket 下单执行参考

*收藏时间: 2026-04-29*
*来源: https://x.com/runes_leo/status/2047911428959687068*
*用途: 未来开发交易执行模块时参考*

---

## 核心知识：订单类型与执行机制

### 1. 所有订单底层都是 Limit Order

Polymarket 没有真正的 "market order"，区别在于 **订单类型**（GTC/GTD/FOK/FAK）和 **`postOnly` 参数** 的组合。

### 2. GTC + postOnly 组合矩阵

| 组合 | 立刻可成交时 | 挂上去后被打 | 角色 |
|------|------------|------------|------|
| GTC + postOnly=**true** | ❌ **直接 reject** | ✅ maker | 强制做 maker |
| GTC + postOnly=**false** | ✅ taker | ✅ maker | 默认行为 |

### 3. "Market Order" 的本质

所谓 "market order" = 用可立即成交价送出去的 limit order，或直接用 FOK/FAK。

### 4. 订单类型说明

| 类型 | 全称 | 含义 |
|------|------|------|
| **GTC** | Good Till Cancelled | 挂到取消为止 |
| **GTD** | Good Till Date | 挂到指定日期 |
| **FOK** | Fill Or Kill | 全部成交或全部取消 |
| **FAK** | Fill And Kill | 部分成交，剩余取消 |

---

## 实盘关键经验

### ⚠️ Taker 不建议用 FOK + 滑点

**原因**: taker 有延迟，FOK 加了滑点也挂不上去，接单基本都是不利方向移动。

> 这是实盘和模拟差距最大的一点。—— @runes_leo

### 做 Maker 的注意事项

- 使用 `postOnly=true` 确保只做 maker
- 如果价格已经可以立即成交，订单会被 **reject**（不会变成 taker）
- 需要处理 reject 的情况，重新调整价格挂单

### 实盘 vs 模拟的差距

1. **延迟**: 实盘网络延迟导致成交价格偏移
2. **滑点**: 实盘滑点远大于模拟
3. **不利选择**: 被成交时往往是价格朝不利方向移动

---

## 开发交易模块时的 Checklist

- [ ] 安装 `py-clob-client` SDK（Polymarket 官方 Python SDK）
- [ ] 实现钱包签名（需要私钥管理）
- [ ] 根据策略选择订单类型：
  - 做市策略 → GTC + postOnly=true
  - 吃单策略 → GTC + postOnly=false（不建议 FOK）
  - 套利策略 → 需要评估延迟影响
- [ ] 处理 reject/失败的重试逻辑
- [ ] 实盘前用小额测试验证滑点
- [ ] 记录实盘成交价 vs 预期价的偏差数据

---

---

## CLOB V2 升级记录 (2026-04-28)

*来源: PolyCop Mega Update 公告*

### 升级内容
- **抵押代币**: USDC.e → pUSD（ERC-20 on Polygon，1:1 backed by USDC）
- **API 变更**: 订单簿单次请求返回 bids+asks（不再需要分 side 两次请求）
- **新增字段**: `min_order_size`, `tick_size`, `neg_risk`, `last_trade_price`
- **升级时所有限价单被清除**

### 代码已更新 (2026-04-30)
- `06-tools/analysis/clob_api.py` → V2 单次请求 + 新字段
- `dashboard/backend/app/services/clob_service.py` → V2 单次请求 + 新字段
- 向后兼容: 所有下游模块无需修改

### PolyCop 跟单经验 (2026-04-29)
- **Limit → Market 自动切换**: Limit 单过期未成交时自动转为 Market 单
- **跟单上限过滤**: 设置 "Ignore Trades Over: $—" 避免跟踪超大额交易
- **失败重试**: 支持 0-3 次自动重试

---

## 相关资源

- [Polymarket CLOB API 文档](https://docs.polymarket.com/)
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)
- 项目现有模块: `clob_api.py`, `clob_service.py`（已升级 V2，只读订单簿，可复用）
