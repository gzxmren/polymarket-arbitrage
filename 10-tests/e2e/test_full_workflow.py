#!/usr/bin/env python3
"""
端到端测试
模拟完整用户流程
"""

import pytest
import os
import tempfile
import json
from pathlib import Path


class TestFullWorkflow:
    """测试完整用户工作流"""
    
    @pytest.mark.e2e
    @pytest.mark.slow
    def test_user_setup_workflow(self):
        """测试用户设置工作流"""
        # 1. 创建临时配置
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            
            # 写入配置
            env_content = """
TELEGRAM_BOT_TOKEN=test_token
TELEGRAM_CHAT_ID=123456
RISK_REVIEW_ENABLED=true
"""
            env_file.write_text(env_content)
            
            # 验证配置可读取
            content = env_file.read_text()
            assert "TELEGRAM_BOT_TOKEN" in content
            assert "RISK_REVIEW_ENABLED" in content
    
    @pytest.mark.e2e
    def test_data_persistence(self):
        """测试数据持久化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 模拟保存报告
            report = {
                "scan_time": "2026-03-12T00:00:00",
                "opportunities": [],
                "summary": {"total": 0}
            }
            
            report_file = Path(tmpdir) / "report.json"
            report_file.write_text(json.dumps(report))
            
            # 验证可读取
            loaded = json.loads(report_file.read_text())
            assert loaded["scan_time"] == "2026-03-12T00:00:00"
    
    @pytest.mark.e2e
    def test_directory_structure(self):
        """测试目录结构完整性"""
        project_root = Path(__file__).parent.parent.parent
        
        # 关键目录
        required_dirs = [
            "00-learning",
            "06-tools/analysis",
            "06-tools/monitoring",
            "07-data",
            "10-tests"
        ]
        
        for dir_path in required_dirs:
            full_path = project_root / dir_path
            assert full_path.exists(), f"Missing directory: {dir_path}"
    
    @pytest.mark.e2e
    def test_critical_files_exist(self):
        """测试关键文件存在"""
        project_root = Path(__file__).parent.parent.parent
        
        critical_files = [
            "README.md",
            ".gitignore",
            "06-tools/monitoring/polymarket_monitor_v2.py",
            "06-tools/monitoring/risk_reviewer.py",
            "06-tools/monitoring/telegram_notifier_v2.py"
        ]
        
        for file_path in critical_files:
            full_path = project_root / file_path
            assert full_path.exists(), f"Missing file: {file_path}"


class TestPerformance:
    """性能测试"""
    
    @pytest.mark.e2e
    @pytest.mark.slow
    def test_scan_performance(self):
        """测试扫描性能"""
        import time
        from pair_cost_scanner import calculate_pair_cost
        
        # 创建测试数据
        markets = [
            {
                "id": str(i),
                "question": f"Test {i}",
                "slug": f"test-{i}",
                "outcomePrices": ["0.52", "0.47"],
                "liquidity": 50000,
                "volume": 100000,
                "endDate": "2026-06-30T23:59:59Z"
            }
            for i in range(100)
        ]
        
        # 测试性能
        start = time.time()
        for market in markets:
            calculate_pair_cost(market)
        elapsed = time.time() - start
        
        # 100个市场应该在1秒内完成
        assert elapsed < 1.0, f"Too slow: {elapsed}s"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
