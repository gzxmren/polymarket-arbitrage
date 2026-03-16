#!/usr/bin/env python3
"""
定时任务调度器
定期同步数据
"""

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apscheduler.schedulers.background import BackgroundScheduler
from .data_sync import DataSyncService

class DataScheduler:
    """数据同步调度器"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.sync_service = DataSyncService()
    
    def start(self):
        """启动调度器"""
        # 每5分钟同步一次数据
        self.scheduler.add_job(
            self.sync_service.run_full_sync,
            'interval',
            minutes=5,
            id='data_sync',
            replace_existing=True
        )
        
        # 立即执行一次
        self.sync_service.run_full_sync()
        
        self.scheduler.start()
        print("✅ 数据同步调度器已启动")
    
    def stop(self):
        """停止调度器"""
        self.scheduler.shutdown()
        print("⏹️  数据同步调度器已停止")

if __name__ == '__main__':
    scheduler = DataScheduler()
    scheduler.start()
    
    try:
        # 保持运行
        while True:
            pass
    except KeyboardInterrupt:
        scheduler.stop()
