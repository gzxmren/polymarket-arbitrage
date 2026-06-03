# 废弃模块说明

**目录**: `99-archive/deprecated-modules/`  
**状态**: ⚠️ 不再维护，仅供查阅

---

## 📋 模块清单

### 1. whale_tracker.py

**原路径**: `06-tools/analysis/whale_tracker.py`  
**创建时间**: 2026-03-12  
**废弃时间**: 2026-04-05

**功能**: 最早的鲸鱼跟踪脚本，从 API 获取数据并保存到 JSON 文件

**废弃原因**:
- 功能已被 `sync_changes.py` + `data_sync.py` 替代
- 数据存储方式已改为数据库
- 不再维护

**替代方案**:
- `sync_changes.py` - 实时同步交易数据
- `data_sync.py` - 定期同步持仓数据

---

### 2. whale_tracker_v2.py

**原路径**: `06-tools/analysis/whale_tracker_v2.py`  
**创建时间**: 2026-03-25  
**废弃时间**: 2026-04-05

**功能**: 增强版鲸鱼跟踪，支持更多指标

**废弃原因**:
- 功能已被新系统替代
- 与新系统重复
- 最后更新: 2026-03-26

**替代方案**:
- `sync_changes.py` - 实时同步
- `data_sync.py` - 数据同步
- `whale_classifier.py` - 智能分类

---

### 3. whale_watchlist.py

**原路径**: `06-tools/analysis/whale_watchlist.py`  
**创建时间**: 2026-03-17  
**废弃时间**: 2026-04-05

**功能**: 管理鲸鱼关注列表，保存到 JSON 文件

**废弃原因**:
- 关注功能已集成到 Dashboard
- 数据已迁移到数据库 `whales` 表
- JSON 文件数据已过时

**替代方案**:
- Dashboard 关注功能
- 数据库 `whales.is_watched` 字段

---

### 4. whale_deep_analyzer.py

**原路径**: `dashboard/backend/app/services/whale_deep_analyzer.py`  
**创建时间**: 2026-03-15  
**废弃时间**: 2026-04-05

**功能**: 深度分析鲸鱼行为模式

**废弃原因**:
- 存在 bug (详见 `docs/whale-deep-analysis-bug-report.md`)
- 功能不完整
- 被 `whale_analyzer.py` 替代

**替代方案**:
- `whale_analyzer.py` - 标准分析服务
- `whale_classifier.py` - 智能分类

---

## ⚠️ 重要提示

### 不要使用这些模块

1. **不维护** - 这些代码不再更新
2. **有bug** - 特别是 `whale_deep_analyzer.py`
3. **数据不一致** - JSON 文件数据可能已过时
4. **功能重复** - 新系统已覆盖所有功能

### 如需参考

- 查看代码逻辑可参考
- 但不要直接复制使用
- 基于新系统开发新功能

---

## 🗑️ 清理计划

### 建议删除时间

| 文件 | 建议删除时间 | 条件 |
|------|-------------|------|
| whale_tracker.py | 2026-05-05 | 确认新系统稳定运行1个月 |
| whale_tracker_v2.py | 2026-05-05 | 确认新系统稳定运行1个月 |
| whale_watchlist.py | 2026-05-05 | 确认数据已完全迁移 |
| whale_deep_analyzer.py | 2026-06-05 | 确认 bug 已修复或功能已替代 |

### 删除前检查清单

- [ ] 新系统运行稳定
- [ ] 无引用这些模块的代码
- [ ] 数据已完全迁移
- [ ] 团队确认可删除

---

## 📞 联系方式

如有疑问，联系：虾头 🦐
