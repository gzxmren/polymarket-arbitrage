# DataSync V2 部署文档

**版本**: V2.0  
**日期**: 2026-04-05  
**作者**: 虾头 🦐

---

## 📋 部署概述

### 主要改进

1. **修复 concentration_history 累积问题**
   - 自动清理 7 天前的历史数据
   - 防止表无限增长

2. **实现混合数据源**
   - 优先使用 Trades 重建 (实时)
   - 其次使用 JSON 文件 (历史)
   - 最后使用 Leaderboard (参考)

3. **新增持仓重建功能**
   - 从 changes 表重建持仓明细
   - 计算集中度 (top5_ratio)
   - 估算当前价值

---

## 📁 文件清单

### 新增文件

| 文件 | 路径 | 功能 |
|------|------|------|
| position_rebuilder.py | `06-tools/analysis/` | 持仓重建核心 |
| hybrid_data_source.py | `06-tools/analysis/` | 混合数据源管理 |
| data_sync_v2.py | `dashboard/backend/app/services/` | V2 同步服务 |
| test_data_sync_v2.py | `06-tools/analysis/` | 测试脚本 |

### 修改文件

| 文件 | 路径 | 修改内容 |
|------|------|---------|
| data_sync.py | `dashboard/backend/app/services/` | 添加清理逻辑 |

---

## 🚀 部署步骤

### 步骤 1: 备份现有数据

```bash
# 备份数据库
cp ~/polymarket-project/dashboard/backend/database/polymarket.db \
   ~/polymarket-project/dashboard/backend/database/polymarket.db.backup.$(date +%Y%m%d)

# 备份 JSON 文件
tar -czf ~/polymarket-project/99-archive/whale_states-backup-$(date +%Y%m%d).tar.gz \
    ~/polymarket-project/07-data/whale_states/
```

### 步骤 2: 验证新代码

```bash
cd ~/polymarket-project/06-tools/analysis

# 测试混合数据源
python3 test_data_sync_v2.py

# 预期输出:
# ✅ 活跃钱包: Trades 重建成功
# ✅ 非活跃钱包: JSON fallback 成功
```

### 步骤 3: 切换到 V2 版本

```bash
cd ~/polymarket-project/dashboard/backend/app/services

# 备份原版本
mv data_sync.py data_sync_v1.py

# 使用 V2 版本
ln -s data_sync_v2.py data_sync.py

# 或者修改 scheduler.py 引用 V2
```

### 步骤 4: 重启 Dashboard 服务

```bash
# 停止现有服务
pkill -f "python3 run.py"

# 启动服务
cd ~/polymarket-project/dashboard/backend
python3 run.py
```

### 步骤 5: 验证部署

```bash
# 检查日志
tail -f ~/polymarket-project/dashboard/backend/app.log

# 预期输出:
# ✅ 混合数据源已初始化
# ✅ 同步完成: X 成功, Y 失败
```

---

## 📊 数据对比验证

### 验证清单

| 检查项 | 方法 | 预期结果 |
|--------|------|---------|
| 活跃钱包 | 对比 Trades vs JSON | 数据接近或一致 |
| 非活跃钱包 | 检查 JSON fallback | 数据完整 |
| concentration_history | 检查表大小 | 不再无限增长 |
| 集中度计算 | 对比重建 vs JSON | 误差 < 10% |

### 验证命令

```bash
# 1. 检查 concentration_history 大小
sqlite3 ~/polymarket-project/dashboard/backend/database/polymarket.db \
    "SELECT COUNT(*) FROM concentration_history WHERE date(timestamp) = date('now');"

# 预期: 每天新增约 200-500 条，总量 < 50,000 条

# 2. 对比重建数据 vs JSON 数据
python3 ~/polymarket-project/06-tools/analysis/position_rebuilder.py

# 预期: 活跃钱包匹配度 > 80%

# 3. 检查 Dashboard 显示
# 访问 http://localhost:3001/whales
# 验证持仓明细显示正常
```

---

## ⚠️ 回滚方案

### 如果出现问题

```bash
# 1. 停止服务
pkill -f "python3 run.py"

# 2. 恢复 V1 版本
cd ~/polymarket-project/dashboard/backend/app/services
rm data_sync.py
mv data_sync_v1.py data_sync.py

# 3. 恢复数据库（如果需要）
cp ~/polymarket-project/dashboard/backend/database/polymarket.db.backup.20260405 \
   ~/polymarket-project/dashboard/backend/database/polymarket.db

# 4. 重启服务
python3 run.py
```

---

## 📈 监控指标

### 关键指标

| 指标 | 正常范围 | 告警阈值 |
|------|---------|---------|
| 同步成功率 | > 90% | < 80% |
| Trades 重建比例 | > 50% | < 30% |
| concentration_history 日增 | < 1,000 | > 5,000 |
| 数据新鲜度 (fresh) | > 60% | < 40% |

### 监控命令

```bash
# 每日检查脚本
#!/bin/bash

echo "=== DataSync V2 每日检查 ==="

# 检查同步成功率
sqlite3 polymarket.db "SELECT 
    data_source, 
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM whales), 2) as pct
FROM whales 
GROUP BY data_source;"

# 检查 concentration_history
sqlite3 polymarket.db "SELECT 
    date(timestamp) as day,
    COUNT(*) as count
FROM concentration_history
GROUP BY day
ORDER BY day DESC
LIMIT 7;"
```

---

## 🔧 故障排除

### 问题 1: 混合数据源加载失败

**症状**: `⚠️ 混合数据源加载失败`

**解决**:
```bash
# 检查文件是否存在
ls ~/polymarket-project/06-tools/analysis/hybrid_data_source.py

# 检查 Python 路径
python3 -c "import sys; print(sys.path)"

# 手动测试导入
cd ~/polymarket-project/06-tools/analysis
python3 -c "from hybrid_data_source import HybridDataSource; print('OK')"
```

### 问题 2: Trades 重建失败

**症状**: `❌ 无交易记录，无法重建`

**原因**: 
- 钱包在 changes 表中无记录
- 查询时间范围太短

**解决**:
- 自动 fallback 到 JSON 数据
- 或扩展查询时间范围到 90 天或 1 年

### 问题 3: concentration_history 继续增长

**症状**: 表大小超过 100 万条

**解决**:
```sql
-- 手动清理旧数据
DELETE FROM concentration_history 
WHERE timestamp < datetime('now', '-7 days');

-- 检查清理结果
SELECT COUNT(*) FROM concentration_history;
```

---

## 📞 联系方式

如有问题，联系：虾头 🦐

---

## 📝 更新日志

### V2.0 (2026-04-05)
- ✅ 实现混合数据源
- ✅ 修复 concentration_history 累积问题
- ✅ 实现 Trades 持仓重建
- ✅ 添加数据新鲜度标记

---

*部署完成时间: 2026-04-05 21:40 GMT+8*
