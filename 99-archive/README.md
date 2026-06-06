# Polymarket 归档文件

**归档日期**: 2026-04-09

## 归档内容

### deprecated-scripts/ - 废弃脚本
| 文件 | 说明 | 废弃原因 |
|------|------|---------|
| polymarket_learning_bot.py | 学习助手 | 功能已集成到主系统 |
| polymarket_evaluation_trigger.py | 评估触发器 | 不再需要 |
| polymarket_evaluator_loop.py | 评估循环 | 被统一监控替代 |
| polymarket_workflow.py | 工作流管理 | 功能已集成 |
| polymarket_monitor.py | 旧版监控 | 被 unified 版本替代 |
| polymarket_monitor_backup.py | 备份版本 | 不再需要 |

### backups/ - 备份文件
| 文件 | 说明 |
|------|------|
| polymarket_monitor_unified.py.bak | unified 版本的备份 |

### reports/ - 历史报告
| 文件 | 说明 |
|------|------|
| polymarket_fix_report.md | 修复报告 |

## 保留的核心文件

以下文件仍在 workspace 根目录，继续使用：
- `polymarket_monitor_unified.py` ⭐ 主监控程序
- `polymarket_monitor_master.py` ⭐ 主控程序
- `polymarket-monitor-loop.sh` ⭐ 启动脚本
- `run_sync_changes.sh` ⭐ 数据同步
- `start_monitor.sh` ⭐ 服务启动

## 清理的临时文件
- `polymarket_monitor.log` (旧日志)
- `test_process_cleanup.py` (测试文件)

## 说明
这些文件归档保存，便于历史查阅，但不再使用。
