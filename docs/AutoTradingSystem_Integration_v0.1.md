# 🦐 虾伴自动交易系统 - 集成设计方案 v0.1

**版本**: 0.1 草案
**日期**: 2026-04-16
**目标**: 整合三大数据源，构建完整的自动交易系统

---

## 一、系统架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          🦐 虾伴自动交易系统                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    📡 信号层（Signal Layer）                     │   │
│  │                                                                  │   │
│  │  ┌──────────────────┐   ┌──────────────────┐                   │   │
│  │  │  PolyCop Signal  │   │  whale_states    │                   │   │
│  │  │  (114地址,Score)  │   │  (本地鲸鱼追踪)   │                   │   │
│  │  └────────┬─────────┘   └────────┬─────────┘                   │   │
│  │           │                      │                             │   │
│  │           └──────────┬───────────┘                             │   │
│  │                      ↓                                          │   │
│  │            ┌─────────────────┐                                  │   │
│  │            │  Signal Router  │ → 策略选择                        │   │
│  │            │  信号路由器      │   (鲸鱼跟单 vs 独立信号)          │   │
│  │            └────────┬────────┘                                  │   │
│  └─────────────────────┼───────────────────────────────────────────┘   │
│                        ↓                                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    🧠 决策层（Decision Layer）                   │   │
│  │                                                                  │   │
│  │  ┌──────────────────┐   ┌──────────────────┐                   │   │
│  │  │  Edge Calculator  │   │  Risk Assessor   │                   │   │
│  │  │  模型 vs 市场     │   │  风险评估         │                   │   │
│  │  └────────┬─────────┘   └────────┬─────────┘                   │   │
│  │           │                      │                             │   │
│  │           └──────────┬───────────┘                             │   │
│  │                      ↓                                          │   │
│  │            ┌─────────────────┐                                  │   │
│  │            │  Trade Decider   │ → 是否下单 + 金额                  │   │
│  │            │  交易决策器      │                                    │   │
│  │            └────────┬────────┘                                  │   │
│  └─────────────────────┼───────────────────────────────────────────┘   │
│                        ↓                                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    ⚡ 执行层（Execution Layer）                  │   │
│  │                                                                  │   │
│  │  ┌──────────────────┐   ┌──────────────────┐                   │   │
│  │  │  Order Builder   │   │  Order Executor  │                   │   │
│  │  │  订单构建         │   │  CLOB API下单     │                   │   │
│  │  └────────┬─────────┘   └────────┬─────────┘                   │   │
│  │           │                      │                             │   │
│  │           └──────────┬───────────┘                             │   │
│  │                      ↓                                          │   │
│  │            ┌─────────────────┐                                  │   │
│  │            │  Position Mgmt  │ → 持仓管理 + 止盈止损              │   │
│  │            │  持仓管理器     │                                    │   │
│  │            └────────┬────────┘                                  │   │
│  └─────────────────────┼───────────────────────────────────────────┘   │
│                        ↓                                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    📊 通知层（Notification Layer）               │   │
│  │                                                                  │   │
│  │  ┌──────────────────┐   ┌──────────────────┐                   │   │
│  │  │  Trade Logger    │   │  Telegram Alert │                   │   │
│  │  │  交易日志         │   │  实时推送        │                   │   │
│  │  └──────────────────┘   └──────────────────┘                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 二、各层详细设计

### 2.1 信号层（Signal Layer）

#### 2.1.1 信号来源

| 信号源 | 数据 | 用途 |
|--------|------|------|
| **PolyCop Signal** | 114个地址, Score评分 | 高分地址发现, 信号评分 |
| **whale_states** | 鲸鱼持仓, 交易历史 | 鲸鱼跟单策略 |
| **本地CLOB API** | orderbook, 价格 | 市场流动性检查 |

#### 2.1.2 信号路由器（Signal Router）

```python
class SignalRouter:
    """信号路由器 - 选择使用哪个策略"""
    
    STRATEGY_WHALE_FOLLOW = "whale_follow"
    STRATEGY_POLYCOP_SIGNAL = "polycop"
    STRATEGY_HYBRID = "hybrid"
    
    def route(self, signal_data: dict) -> str:
        """
        根据信号数据选择策略
        
        规则:
        - 鲸鱼 Score ≥ 90 + 有新交易 → 鲸鱼跟单
        - PolyCop Score ≥ 90 + Edge > 阈值 → 独立信号
        - 两者同时满足 → 混合策略
        """
        whale_score = signal_data.get("whale_score", 0)
        polycop_score = signal_data.get("polycop_score", 0)
        has_whale_trade = signal_data.get("has_new_trade", False)
        
        if whale_score >= 90 and has_whale_trade:
            return self.STRATEGY_WHALE_FOLLOW
        elif polycop_score >= 90:
            return self.STRATEGY_POLYCOP_SIGNAL
        else:
            return None  # 不交易
```

#### 2.1.3 鲸鱼跟单信号

```python
class WhaleFollowSignal:
    """鲸鱼跟单信号"""
    
    def generate(self, whale_address: str, recent_trades: list) -> dict:
        """
        生成鲸鱼跟单信号
        
        返回:
        {
            "signal_type": "whale_follow",
            "whale_address": "0x...",
            "whale_score": 95,
            "whale_winrate": 72.5,
            "trade": {
                "side": "BUY",
                "token_id": "...",
                "price": 0.65,
                "amount": 100,
                "market": "BTC > $95k"
            },
            "confidence": 0.85,  # 基于 Score
            "timestamp": "..."
        }
        """
        pass
```

#### 2.1.4 PolyCop 独立信号

```python
class PolyCopSignal:
    """PolyCop 信号（不带鲸鱼）"""
    
    def generate(self, address_data: dict) -> dict:
        """
        生成 PolyCop 信号
        
        返回:
        {
            "signal_type": "polycop",
            "address": "0x...",
            "score": 95,
            "winrate": 72.5,
            "pnl": 50000,
            "volume": 100000,
            "confidence": 0.80,
            "timestamp": "..."
        }
        """
        pass
```

---

### 2.2 决策层（Decision Layer）

#### 2.2.1 Edge 计算器（来自 BTC15mAssistant）

```python
class EdgeCalculator:
    """Edge 计算器 - 模型概率 vs 市场概率"""
    
    def compute(self, model_prob: float, market_price: float) -> dict:
        """
        计算 Edge
        
        参数:
        - model_prob: 模型预测的概率 (0-1)
        - market_price: 市场赔率 (0-1)
        
        返回:
        {
            "model_prob": 0.70,
            "market_prob": 0.55,
            "edge": 0.15,  # model - market
            "edge_pct": 27.3  # edge/market * 100
        }
        """
        market_prob = market_price
        edge = model_prob - market_prob
        
        return {
            "model_prob": model_prob,
            "market_prob": market_prob,
            "edge": edge,
            "edge_pct": (edge / market_prob * 100) if market_prob > 0 else 0
        }
```

#### 2.2.2 时间衰减（来自 BTC15mAssistant）

```python
class TimeDecay:
    """时间衰减 - 越接近结算越保守"""
    
    def __init__(self, window_minutes: int = 15):
        self.window_minutes = window_minutes
    
    def apply(self, raw_prob: float, remaining_minutes: float) -> dict:
        """
        应用时间衰减
        
        逻辑:
        - 15分钟时: 不衰减
        - 5分钟时: 衰减50%
        - 0分钟时: 完全向50%收敛
        """
        time_ratio = max(0, min(remaining_minutes / self.window_minutes, 1))
        
        # 越接近结算，概率越向50%收敛
        adjusted_prob = 0.5 + (raw_prob - 0.5) * time_ratio
        
        return {
            "raw_prob": raw_prob,
            "remaining_minutes": remaining_minutes,
            "time_ratio": time_ratio,
            "adjusted_prob": adjusted_prob
        }
```

#### 2.2.3 市场状态检测（来自 BTC15mAssistant）

```python
class RegimeDetector:
    """市场状态检测 - TREND/RANGE/CHOP"""
    
    def detect(self, price: float, vwap: float, vwap_slope: float, 
               volume_recent: float, volume_avg: float) -> str:
        """
        检测市场状态
        
        返回:
        - TREND_UP: 价格 > VWAP 且 VWAP 上升
        - TREND_DOWN: 价格 < VWAP 且 VWAP 下降
        - RANGE: 区间震荡
        - CHOP: 低成交量震荡
        """
        above_vwap = price > vwap
        
        # 低成交量检测
        if volume_recent < 0.6 * volume_avg and abs(price - vwap) / vwap < 0.001:
            return "CHOP"
        
        # 趋势判断
        if above_vwap and vwap_slope > 0:
            return "TREND_UP"
        elif not above_vwap and vwap_slope < 0:
            return "TREND_DOWN"
        else:
            return "RANGE"
```

#### 2.2.4 交易决策器（Trade Decider）

```python
class TradeDecider:
    """交易决策器 - 综合所有因素决定是否交易"""
    
    # 阶段阈值（来自 BTC15mAssistant）
    PHASE_EARLY = "EARLY"   # > 10分钟
    PHASE_MID = "MID"       # 5-10分钟
    PHASE_LATE = "LATE"     # < 5分钟
    
    # 阶段对应的阈值
    THRESHOLDS = {
        PHASE_EARLY: {"edge": 0.05, "prob": 0.55},
        PHASE_MID: {"edge": 0.10, "prob": 0.60},
        PHASE_LATE: {"edge": 0.20, "prob": 0.65}
    }
    
    def decide(self, 
               signal: dict,
               market_data: dict,
               position_data: dict) -> dict:
        """
        综合决策
        
        参数:
        - signal: 信号数据（来自信号层）
        - market_data: 市场数据（价格、流动性、剩余时间）
        - position_data: 持仓数据（当前持仓、盈亏）
        
        返回:
        {
            "action": "ENTER" | "NO_TRADE" | "INCREASE" | "CLOSE",
            "side": "UP" | "DOWN",
            "amount": 10.0,  # USDC 金额
            "reason": "...",
            "edge": 0.15,
            "risk_score": 0.3
        }
        """
        # 1. 确定阶段
        remaining = market_data.get("remaining_minutes", 15)
        if remaining > 10:
            phase = self.PHASE_EARLY
        elif remaining > 5:
            phase = self.PHASE_MID
        else:
            phase = self.PHASE_LATE
        
        thresholds = self.THRESHOLDS[phase]
        
        # 2. 风控检查
        risk_check = self._check_risk(position_data, market_data)
        if not risk_check["passed"]:
            return {
                "action": "NO_TRADE",
                "reason": f"risk_failed: {risk_check['reason']}"
            }
        
        # 3. 计算 Edge（如果有市场赔率）
        if "market_price" in market_data and "model_prob" in signal:
            edge_calc = EdgeCalculator()
            edge_result = edge_calc.compute(
                signal["model_prob"], 
                market_data["market_price"]
            )
            
            # 4. 检查阈值
            if edge_result["edge"] < thresholds["edge"]:
                return {
                    "action": "NO_TRADE",
                    "reason": f"edge_below_threshold: {edge_result['edge']:.2%} < {thresholds['edge']:.2%}"
                }
            
            if signal.get("confidence", 0) < thresholds["prob"]:
                return {
                    "action": "NO_TRADE",
                    "reason": f"prob_below_threshold: {signal['confidence']:.2%} < {thresholds['prob']:.2%}"
                }
        
        # 5. 计算交易金额
        amount = self._calculate_amount(signal, position_data, market_data)
        
        return {
            "action": "ENTER",
            "side": signal.get("side", "UP"),
            "amount": amount,
            "reason": f"OK_{phase}",
            "edge": edge_result.get("edge") if 'edge_result' in locals() else None,
            "risk_score": risk_check.get("score", 0.5)
        }
    
    def _check_risk(self, position_data, market_data) -> dict:
        """风控检查"""
        # 日交易限额
        daily_volume = position_data.get("daily_volume", 0)
        if daily_volume >= 200:  # $200/天
            return {"passed": False, "reason": "daily_limit"}
        
        # 最大持仓数
        open_positions = position_data.get("open_positions", 0)
        if open_positions >= 5:
            return {"passed": False, "reason": "max_positions"}
        
        # 止损检查
        unrealized_pnl = position_data.get("unrealized_pnl_pct", 0)
        if unrealized_pct < -0.20:  # -20% 止损
            return {"passed": False, "reason": "stop_loss"}
        
        return {"passed": True, "score": 0.3}
    
    def _calculate_amount(self, signal, position_data, market_data) -> float:
        """计算交易金额"""
        base_amount = 10.0  # 基础金额
        
        # 根据置信度调整
        confidence = signal.get("confidence", 0.5)
        amount = base_amount * confidence
        
        # 根据鲸鱼 Score 调整
        score = signal.get("whale_score") or signal.get("score", 50)
        if score >= 95:
            amount *= 2
        elif score >= 90:
            amount *= 1.5
        
        # 根据 Edge 调整
        if "edge" in signal and signal["edge"] > 0.15:
            amount *= 1.5
        
        # 硬上限
        return min(amount, 50.0)
```

---

### 2.3 执行层（Execution Layer）

#### 2.3.1 钱包管理

```python
class WalletManager:
    """钱包管理器"""
    
    def __init__(self, private_key: str):
        self.private_key = private_key
        self.web3 = self._init_web3()
        self.clob_client = self._init_clob_client()
    
    def _init_web3(self):
        """初始化 Web3"""
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        return w3
    
    def _init_clob_client(self):
        """初始化 CLOB Client"""
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON
        
        client = ClobClient(
            "https://clob.polymarket.com",
            key=self.private_key,
            chain_id=POLYGON
        )
        return client
    
    def get_balance(self) -> float:
        """获取 USDC 余额"""
        return self.clob_client.get_balance()
    
    def is_approved(self) -> bool:
        """检查是否已授权"""
        return self.clob_client.has_position()
```

#### 2.3.2 合约授权

```python
class ContractApprover:
    """合约授权 - 只需执行一次"""
    
    # 关键合约地址
    CONTRACTS = {
        "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        "EXCHANGE": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        "NEG_RISK_EXCHANGE": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
        "NEG_RISK_ADAPTER": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
    }
    
    def approve_all(self) -> list:
        """执行全部授权（6次链上交易）"""
        # 1. USDC → Exchange
        # 2. CTF → Exchange
        # 3. USDC → Neg Risk Exchange
        # 4. CTF → Neg Risk Exchange
        # 5. USDC → Neg Risk Adapter
        # 6. CTF → Neg Risk Adapter
        pass
```

#### 2.3.3 订单执行器

```python
class OrderExecutor:
    """订单执行器"""
    
    def __init__(self, wallet_manager: WalletManager):
        self.wallet = wallet_manager
    
    def execute_market_order(self, token_id: str, amount: float, side: str) -> dict:
        """
        市价单执行
        
        参数:
        - token_id: 市场 token ID
        - amount: USDC 金额
        - side: "BUY" 或 "SELL"
        """
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        
        order = self.wallet.clob_client.create_market_order(
            MarketOrderArgs(
                token_id=token_id,
                amount=str(amount)
            )
        )
        
        result = self.wallet.clob_client.post_order(
            order, 
            orderType=OrderType.FOK  # Fill or Kill
        )
        
        return {
            "success": True,
            "order_id": result.get("orderID"),
            "filled_amount": result.get("size"),
            "price": result.get("price")
        }
    
    def execute_limit_order(self, token_id: str, amount: float, 
                            price: float, side: str) -> dict:
        """
        限价单执行
        
        参数:
        - token_id: 市场 token ID
        - amount: USDC 金额
        - price: 价格 (0-1)
        - side: "BUY" 或 "SELL"
        """
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.constants import BUY, SELL
        
        order = self.wallet.clob_client.create_and_post_order(
            OrderArgs(
                price=str(price),
                size=str(amount),
                side=BUY if side == "BUY" else SELL,
                token_id=token_id
            )
        )
        
        return {
            "success": True,
            "order_id": order.get("orderID")
        }
```

#### 2.3.4 持仓管理器

```python
class PositionManager:
    """持仓管理器 - 止盈止损"""
    
    def __init__(self):
        self.positions = {}  # token_id -> position
        self.daily_volume = 0
        self.trade_log = []
    
    def update_position(self, token_id: str, filled: float, price: float, side: str):
        """更新持仓"""
        if token_id not in self.positions:
            self.positions[token_id] = {
                "size": 0,
                "avg_price": 0,
                "side": side
            }
        
        pos = self.positions[token_id]
        if side == "BUY":
            # 加仓
            total_cost = pos["size"] * pos["avg_price"] + filled * price
            pos["size"] += filled
            pos["avg_price"] = total_cost / pos["size"] if pos["size"] > 0 else 0
        else:
            # 减仓
            pos["size"] -= filled
    
    def check_stop_loss(self, current_price: float) -> list:
        """检查是否触发止损"""
        to_close = []
        
        for token_id, pos in self.positions.items():
            if pos["size"] <= 0:
                continue
            
            pnl_pct = (current_price - pos["avg_price"]) / pos["avg_price"]
            if pos["side"] == "SELL":
                pnl_pct = -pnl_pct
            
            if pnl_pct <= -0.20:  # -20% 止损
                to_close.append({
                    "token_id": token_id,
                    "size": pos["size"],
                    "reason": "stop_loss",
                    "pnl_pct": pnl_pct
                })
        
        return to_close
    
    def check_take_profit(self, current_price: float) -> list:
        """检查是否触发止盈"""
        to_close = []
        
        for token_id, pos in self.positions.items():
            if pos["size"] <= 0:
                continue
            
            pnl_pct = (current_price - pos["avg_price"]) / pos["avg_price"]
            if pos["side"] == "SELL":
                pnl_pct = -pnl_pct
            
            if pnl_pct >= 0.30:  # +30% 止盈
                to_close.append({
                    "token_id": token_id,
                    "size": pos["size"],
                    "reason": "take_profit",
                    "pnl_pct": pnl_pct
                })
        
        return to_close
    
    def log_trade(self, trade: dict):
        """记录交易"""
        self.trade_log.append({
            **trade,
            "timestamp": datetime.now().isoformat()
        })
        
        # 更新日交易量
        self.daily_volume += trade.get("amount", 0)
    
    def reset_daily(self):
        """重置日交易量（每天0点）"""
        self.daily_volume = 0
```

---

### 2.4 通知层（Notification Layer）

#### 2.4.1 交易日志

```python
class TradeLogger:
    """交易日志"""
    
    def __init__(self, log_dir: str = "./logs"):
        self.log_dir = log_dir
        self.trades_file = f"{log_dir}/trades.csv"
    
    def log(self, trade: dict):
        """记录交易到CSV"""
        import csv
        from datetime import datetime
        
        row = {
            "timestamp": datetime.now().isoformat(),
            "signal_type": trade.get("signal_type"),
            "whale_address": trade.get("whale_address", ""),
            "score": trade.get("score", ""),
            "side": trade.get("side"),
            "amount": trade.get("amount"),
            "price": trade.get("price"),
            "result": trade.get("result", ""),
            "pnl": trade.get("pnl", ""),
            "edge": trade.get("edge", ""),
            "reason": trade.get("reason", "")
        }
        
        # 写入CSV
        with open(self.trades_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(row)
```

#### 2.4.2 Telegram 通知

```python
class TelegramNotifier:
    """Telegram 通知"""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
    
    def notify_trade(self, trade: dict):
        """发送交易通知"""
        message = self._format_trade_message(trade)
        self._send(message)
    
    def notify_error(self, error: dict):
        """发送错误通知"""
        message = f"⚠️ 交易错误\n\n{error}"
        self._send(message)
    
    def notify_summary(self, summary: dict):
        """发送日报摘要"""
        message = self._format_summary_message(summary)
        self._send(message)
    
    def _format_trade_message(self, trade: dict) -> str:
        """格式化交易消息"""
        emoji = "🟢" if trade.get("action") == "ENTER" else "🔴"
        
        return f"""
{emoji} 交易信号

🐋 鲸鱼: `{trade.get('whale_address', 'N/A')[:10]}...`
📊 Score: {trade.get('score', 'N/A')}
💰 金额: ${trade.get('amount', 0):.2f}
📈 方向: {trade.get('side', 'N/A')}
🎯 Edge: {trade.get('edge', 'N/A')}
⏰ 原因: {trade.get('reason', 'N/A')}
"""
    
    def _send(self, message: str):
        """发送消息"""
        import requests
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        requests.post(url, data=data)
```

---

## 三、数据流

```
1. 信号获取
   ┌─────────────────────────────────────────┐
   │ PolyCop Signal (每6小时)                │
   │ whale_states (每6小时)                  │
   │ CLOB API 实时价格 (按需)                 │
   └──────────────────┬──────────────────────┘
                     ↓
2. 信号处理
   ┌─────────────────────────────────────────┐
   │ Signal Router → 策略选择                │
   │ WhaleFollowSignal / PolyCopSignal      │
   └──────────────────┬──────────────────────┘
                     ↓
3. 市场数据获取
   ┌─────────────────────────────────────────┐
   │ Gamma API → 市场信息                    │
   │ CLOB API → 当前价格 + 流动性             │
   │ 计算剩余时间                            │
   └──────────────────┬──────────────────────┘
                     ↓
4. 决策计算
   ┌─────────────────────────────────────────┐
   │ Edge Calculator → 模型 vs 市场          │
   │ Time Decay → 时间衰减                   │
   │ Regime Detector → 市场状态             │
   │ Trade Decider → 是否下单                │
   └──────────────────┬──────────────────────┘
                     ↓
5. 风控检查
   ┌─────────────────────────────────────────┐
   │ 日交易限额 $200                         │
   │ 最大持仓 5 个                            │
   │ 止损 -20%                               │
   │ 止盈 +30%                               │
   └──────────────────┬──────────────────────┘
                     ↓
6. 执行交易
   ┌─────────────────────────────────────────┐
   │ Order Executor → CLOB API              │
   │ Position Manager → 更新持仓            │
   └──────────────────┬──────────────────────┘
                     ↓
7. 通知
   ┌─────────────────────────────────────────┐
   │ Trade Logger → CSV                     │
   │ Telegram Notifier → 实时推送            │
   └─────────────────────────────────────────┘
```

---

## 四、风控规则

| 规则 | 限制 | 备注 |
|------|------|------|
| **单笔最大** | $50 | 避免单笔过大 |
| **日交易总额** | $200 | 风险控制 |
| **最大持仓数** | 5个市场 | 分散风险 |
| **止损线** | -20% | 触发则平仓 |
| **止盈线** | +30% | 触发则部分平仓 |
| **最小流动性** | $10,000 | 避免滑点过大 |
| **最小 Score** | 90 | 只跟高分鲸鱼 |

---

## 五、部署步骤

### Phase 1: 基础设施（1天）

- [ ] 创建钱包，充值 USDC
- [ ] 安装依赖：`pip install py-clob-client web3 eth-account`
- [ ] 配置私钥（环境变量）
- [ ] 执行合约授权（6次链上交易）

### Phase 2: 核心模块（2天）

- [ ] 实现 `WalletManager`
- [ ] 实现 `ContractApprover`
- [ ] 实现 `OrderExecutor`
- [ ] 实现 `PositionManager`

### Phase 3: 决策逻辑（2天）

- [ ] 实现 `SignalRouter`
- [ ] 实现 `EdgeCalculator`（来自 BTC15mAssistant）
- [ ] 实现 `TimeDecay`（来自 BTC15mAssistant）
- [ ] 实现 `TradeDecider`

### Phase 4: 集成测试（3天）

- [ ] 单元测试各模块
- [ ] 模拟交易测试（不真下单）
- [ ] $1 小额真单测试
- [ ] $10 正常交易测试

### Phase 5: 风控与通知（2天）

- [ ] 实现 `TradeLogger`
- [ ] 实现 `TelegramNotifier`
- [ ] 配置风控规则
- [ ] 配置日/周报

---

## 六、待优化方向

| 方向 | 说明 | 优先级 |
|------|------|--------|
| **回测系统** | 基于历史数据验证策略 | P1 |
| **多策略并行** | 同时运行鲸鱼跟单+独立信号 | P2 |
| **动态阈值** | 根据市场波动调整 Edge 阈值 | P2 |
| **滑点优化** | 智能选择市价/限价 | P3 |
| **Gas 优化** | 批量授权、批量平仓 | P3 |

---

## 七、代码结构

```
polymarket-project/
├── 08-trading/                    # 新增交易模块
│   ├── __init__.py
│   ├── config.py                  # 交易配置
│   ├── wallet.py                  # 钱包管理
│   ├── approver.py                # 合约授权
│   ├── executor.py                # 订单执行
│   ├── position.py                # 持仓管理
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── router.py              # 信号路由
│   │   ├── whale_follow.py        # 鲸鱼跟单信号
│   │   └── polycop.py             # PolyCop 信号
│   ├── decision/
│   │   ├── __init__.py
│   │   ├── edge.py                # Edge 计算
│   │   ├── time_decay.py          # 时间衰减
│   │   ├── regime.py              # 市场状态
│   │   └── decider.py             # 交易决策
│   ├── risk/
│   │   ├── __init__.py
│   │   └── checker.py             # 风控检查
│   ├── notification/
│   │   ├── __init__.py
│   │   ├── logger.py              # 交易日志
│   │   └── telegram.py            # Telegram 通知
│   ├── main.py                    # 主入口
│   └── run.sh                     # 启动脚本
├── 06-tools/
│   └── monitoring/
│       ├── check_polycop_signal.py  # (已有)
│       └── update_whale_states.py   # (已有)
└── 07-data/
    ├── polycop_observe/            # (已有)
    └── whale_states/               # (已有)
```

---

## 八、依赖

```txt
py-clob-client==0.17.5
py-order-utils==0.3.2
web3==6.11.0
eth-account==0.13.1
python-dotenv==1.0.1
requests==2.32.3
pandas==2.0.0
```

---

*版本: 0.1 草案*
*最后更新: 2026-04-16*
*作者: 虾头 🦐*
