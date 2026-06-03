# 备份文件说明

**目录**: `99-archive/backups/`  
**状态**: 💾 历史备份，可安全删除

---

## 📋 备份清单

### 1. whale_tracker_v2.py.bak

**原路径**: `06-tools/analysis/whale_tracker_v2.py.bak`  
**备份时间**: 2026-03  
**大小**: 15,069 bytes

**说明**:
- `whale_tracker_v2.py` 的备份
- 在修改前自动创建
- 新系统已稳定运行

**状态**: ✅ 可安全删除

---

### 2. whale_news_connector.py.bak

**原路径**: `06-tools/analysis/whale_news_connector.py.bak`  
**备份时间**: 2026-03  
**大小**: 26,151 bytes

**说明**:
- `whale_news_connector.py` 的备份
- 在修改前自动创建
- 新系统已稳定运行

**状态**: ✅ 可安全删除

---

## 🗑️ 清理建议

### 何时删除

这些备份文件可以在以下情况下删除：

1. **新系统稳定运行 1 个月后** (建议 2026-05-05)
2. **确认无回滚需求**
3. **团队确认可删除**

### 删除命令

```bash
# 删除备份文件
rm /home/xmren/.openclaw/workspace/polymarket-project/99-archive/backups/*.bak

# 或者保留但压缩
# tar -czf backups-2026-04-05.tar.gz *.bak
```

---

## 💡 备份策略建议

### 未来备份规范

1. **备份命名**: `filename.YYYYMMDD.bak`
2. **备份位置**: 统一放在 `99-archive/backups/`
3. **保留期限**: 最多保留 3 个月
4. **定期清理**: 每月清理过期备份

### 自动化备份脚本

```bash
#!/bin/bash
# backup-script.sh

BACKUP_DIR="99-archive/backups"
DATE=$(date +%Y%m%d)

# 创建备份
cp file.py "$BACKUP_DIR/file.py.$DATE.bak"

# 清理3个月前的备份
find "$BACKUP_DIR" -name "*.bak" -mtime +90 -delete
```

---

## 📞 联系方式

如有疑问，联系：虾头 🦐
