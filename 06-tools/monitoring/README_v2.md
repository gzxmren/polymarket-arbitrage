# Polymarket 监控器 V2

## ✨ 新特性

- ✅ **Telegram 主动推送** - 发现机会立即通知
- ✅ **定时监控** - 每5分钟自动扫描
- ✅ **风险评估** - 执行前自动审查
- ✅ **风险评估开关** - 可配置开启/关闭
- ✅ **语音转文字** - 支持语音指令（开发中）

## 📁 文件说明

| 文件 | 功能 |
|------|------|
| `polymarket_monitor_v2.py` | 综合监控器（推荐） |
| `telegram_notifier_v2.py` | Telegram 通知器 |
| `risk_reviewer.py` | 风险评估器 |
| `voice_transcriber.py` | 语音转文字 |
| `setup_cron.sh` | 定时任务设置脚本 |
| `.env` | 配置文件 |

## 🚀 快速开始

### 1. 配置 Telegram

```bash
cd polymarket-project/06-tools/monitoring

# 复制配置模板
cp .env.example .env

# 编辑配置
nano .env
```

填写：
- `TELEGRAM_BOT_TOKEN` - 从 @BotFather 获取
- `TELEGRAM_CHAT_ID` - 从 getUpdates API 获取

### 2. 配置风险评估（可选）

编辑 `.env`：

```bash
# 风险评估开关
RISK_REVIEW_ENABLED=true      # true=开启, false=关闭
RISK_REVIEW_THRESHOLD=0.5     # 风险阈值（0-1）
```

### 3. 测试配置

```bash
# 测试 Telegram 通知
python3 telegram_notifier_v2.py --test

# 运行一次监控
python3 polymarket_monitor_v2.py
```

### 4. 设置定时任务

```bash
# 自动设置
chmod +x setup_cron.sh
./setup_cron.sh

# 或手动设置
crontab -e
# 添加: */5 * * * * cd /path/to/monitoring && python3 polymarket_monitor_v2.py
```

## 🔔 通知内容

### Pair Cost 套利通知
```
🎯 套利机会 detected

┌─────────────────────────────┐
│  💰 利润空间: 1.50%          │
│  📊 Pair Cost: $0.9850       │
├─────────────────────────────┤
│  YES: $0.520 | NO: $0.465   │
└─────────────────────────────┘

💡 操作建议:
   同时买入 YES + NO
   建议金额: $5,000
   预期利润: $75

⚠️ 风险提示
```

### 风险评估通知
```
🟢 风险评估: Pair Cost 套利

┌─────────────────────────────┐
│  风险等级: LOW              │
│  风险分数: 20.0%            │
│  审核结果: ✅ 通过          │
└─────────────────────────────┘

💡 建议: ✅ 风险较低，可以考虑执行
```

## ⚙️ 风险评估配置

### 评估维度

| 维度 | 说明 |
|------|------|
| 利润空间 | 过低利润可能无法覆盖手续费 |
| 流动性 | 流动性不足难以成交 |
| 结算时间 | 过长锁定资金，过短来不及操作 |
| 匹配度 | 跨平台套利需确认是同一事件 |
| 历史胜率 | 鲸鱼信号需考虑历史表现 |

### 开关控制

**完全关闭风险评估：**
```bash
# 编辑 .env
RISK_REVIEW_ENABLED=false
```

**调整严格程度：**
```bash
# 更严格（默认0.5）
RISK_REVIEW_THRESHOLD=0.3

# 更宽松
RISK_REVIEW_THRESHOLD=0.7
```

## 📝 日志查看

```bash
# 实时监控日志
tail -f ../../07-data/logs/monitor.log

# 查看历史报告
ls -la ../../07-data/monitor_report_*.json

# 查看风险评估历史
grep "风险评估" ../../07-data/logs/monitor.log
```

## 🎤 语音指令（Beta）

```bash
# 转录音频文件
python3 voice_transcriber.py /path/to/audio.ogg

# 支持格式: ogg, mp3, wav, m4a
```

## 🔧 故障排查

### 没有收到通知
1. 检查 `.env` 配置是否正确
2. 运行 `telegram_notifier_v2.py --test` 测试
3. 检查日志 `logs/monitor.log`

### 风险评估过于严格/宽松
- 调整 `RISK_REVIEW_THRESHOLD` 参数
- 或临时关闭 `RISK_REVIEW_ENABLED=false`

### 定时任务不运行
```bash
# 检查 crontab
crontab -l

# 检查日志权限
ls -la ../../07-data/logs/

# 手动运行测试
cd /path/to/monitoring && python3 polymarket_monitor_v2.py
```

## 📊 监控频率

| 任务 | 频率 | 说明 |
|------|------|------|
| 市场扫描 | 每5分钟 | 发现套利机会 |
| 鲸鱼追踪 | 每5分钟 | 检测大户活动 |
| 心跳报告 | 每小时 | 确认系统运行中 |
| 日志清理 | 每天 | 删除7天前日志 |

## 💡 使用建议

1. **初期**: 开启风险评估，观察一段时间
2. **熟悉后**: 可根据经验调整阈值
3. **稳定后**: 如策略成熟，可关闭自动审核
4. **重要操作**: 始终人工复核后再执行
