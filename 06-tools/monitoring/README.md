# 监控工具

## 📁 文件说明

| 文件 | 功能 |
|------|------|
| `telegram_notifier.py` | Telegram 通知发送器 |
| `polymarket_monitor.py` | 综合监控器（整合所有扫描） |
| `.env.example` | 环境变量配置模板 |

## 🚀 快速开始

### 1. 配置 Telegram Bot

```bash
# 复制配置模板
cp .env.example .env

# 编辑配置
nano .env
```

**获取 Telegram Bot Token:**
1. 在 Telegram 搜索 @BotFather
2. 发送 `/newbot` 创建机器人
3. 按提示设置名称，获取 token
4. 给机器人发一条消息

**获取 Chat ID:**
```bash
# 替换 YOUR_BOT_TOKEN 后执行
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates"
# 在返回的 JSON 中找到 chat->id
```

### 2. 测试通知

```bash
# 加载配置
export $(cat .env | xargs)

# 测试通知
python3 telegram_notifier.py --test
```

### 3. 运行监控

```bash
# 单次运行
python3 polymarket_monitor.py

# 后台持续运行（每5分钟）
while true; do
    python3 polymarket_monitor.py
    sleep 300
done
```

### 4. 添加到 Cron（推荐）

```bash
# 编辑 crontab
crontab -e

# 每5分钟运行一次
*/5 * * * * cd /path/to/polymarket-project/06-tools/monitoring && /usr/bin/python3 polymarket_monitor.py >> ../../07-data/monitor.log 2>&1
```

## 📊 监控内容

### Pair Cost 套利
- 扫描所有活跃市场
- 发现 YES+NO < $0.99 的机会
- 立即 Telegram 通知

### 跨平台套利
- 对比 Polymarket / Manifold / Metaculus
- 发现价差 > 5% 的机会
- 自动验证事件匹配度

### 鲸鱼追踪
- 监控最近1000笔交易
- 识别大额交易者（>$1000）
- 追踪持仓变化

## 🔔 通知格式

### 套利机会
```
🎯 Pair Cost 套利机会

📊 市场问题...
💰 利润空间: 2.5%
💵 YES: $0.52 | NO: $0.47
📉 Pair Cost: $0.99

🔗 [查看市场](...)
```

### 鲸鱼活动
```
🐋 鲸鱼活动警报

👤 鲸鱼昵称
💼 钱包: 0x1234...5678
📊 24h交易量: $50,000

⚡ 最新变动:
🟢 新建仓: 市场名称...
   YES | 1000 @ $0.62
```

## ⚙️ 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TELEGRAM_BOT_TOKEN` | Bot Token | 必填 |
| `TELEGRAM_CHAT_ID` | Chat ID | 必填 |
| `NOTIFY_IMMEDIATELY` | 即时通知 | `true` |

## 📝 日志

监控日志保存在 `../../07-data/monitor.log`

```bash
# 查看实时日志
tail -f ../../07-data/monitor.log
```
