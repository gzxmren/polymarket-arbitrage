#!/usr/bin/env python3
"""
LLM 任务处理器
供 OpenClaw Agent 调用，处理语义套利和逻辑链分析的 LLM 任务

使用 OpenClaw 内置的 kimi-k2.5 模型
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class LLMTask:
    """LLM 任务数据类"""
    id: str
    type: str  # 'similarity', 'relationship', 'sentiment', etc.
    prompt: str
    status: str  # 'pending', 'processing', 'completed', 'failed'
    result: Optional[str] = None
    created_at: str = ""
    processed_at: Optional[str] = None
    error: Optional[str] = None


class LLMTaskProcessor:
    """
    LLM 任务处理器
    
    工作流程:
    1. 读取待处理的任务文件
    2. 调用 LLM (kimi-k2.5) 处理
    3. 保存结果到结果文件
    4. 标记任务为已完成
    
    注意: 此类的 process_tasks() 方法由 OpenClaw Agent 调用
    """
    
    def __init__(self, task_dir: str = "/tmp/llm_tasks", result_dir: str = "/tmp/llm_results"):
        self.task_dir = Path(task_dir)
        self.result_dir = Path(result_dir)
        self.task_dir.mkdir(exist_ok=True)
        self.result_dir.mkdir(exist_ok=True)
    
    def get_pending_tasks(self) -> List[LLMTask]:
        """获取所有待处理的 LLM 任务"""
        tasks = []
        
        for task_file in self.task_dir.glob("*.json"):
            try:
                with open(task_file, 'r') as f:
                    data = json.load(f)
                
                if data.get("status") == "pending":
                    tasks.append(LLMTask(
                        id=data["id"],
                        type=data["type"],
                        prompt=data["prompt"],
                        status=data["status"],
                        created_at=data.get("created_at", ""),
                        result=data.get("result")
                    ))
            except (json.JSONDecodeError, IOError, KeyError):
                continue
        
        return tasks
    
    def process_task(self, task: LLMTask) -> bool:
        """
        处理单个 LLM 任务
        
        此方法由 OpenClaw Agent 调用:
        1. 读取 task 中的 prompt
        2. 调用 OpenClaw 的 kimi-k2.5
        3. 保存结果
        
        Args:
            task: LLM 任务
            
        Returns:
            是否成功处理
        """
        print(f"📝 处理任务: {task.id} (类型: {task.type})")
        
        try:
            # 标记为处理中
            self._update_task_status(task.id, "processing")
            
            # 调用 LLM (由 OpenClaw Agent 实现)
            result = self._call_llm(task.prompt, task.type)
            
            if result is None:
                raise Exception("LLM 调用失败")
            
            # 保存结果
            self._save_result(task, result)
            
            # 标记为已完成
            self._update_task_status(task.id, "completed", result=result)
            
            print(f"   ✅ 任务完成: {task.id}")
            return True
        
        except Exception as e:
            error_msg = str(e)
            print(f"   ❌ 任务失败: {task.id} - {error_msg}")
            self._update_task_status(task.id, "failed", error=error_msg)
            return False
    
    def _call_llm(self, prompt: str, task_type: str) -> Optional[str]:
        """
        调用 LLM (kimi-k2.5)
        
        注意: 此方法由 OpenClaw Agent 实现
        在实际运行中，OpenClaw Agent 会:
        1. 读取 prompt
        2. 调用 kimi-k2.5 (通过 sessions_send 或直接调用)
        3. 返回 LLM 响应
        
        这里返回 None，表示需要外部实现
        """
        # 这是一个占位符，实际由 OpenClaw Agent 处理
        # OpenClaw Agent 应该:
        # 1. 读取任务文件
        # 2. 调用 kimi-k2.5
        # 3. 返回结果
        
        print(f"   🤖 调用 LLM (kimi-k2.5)...")
        print(f"   📄 Prompt 长度: {len(prompt)} 字符")
        
        # 返回 None，表示需要外部处理
        return None
    
    def _update_task_status(self, task_id: str, status: str, result: Optional[str] = None, error: Optional[str] = None):
        """更新任务状态"""
        task_file = self.task_dir / f"{task_id}.json"
        
        if not task_file.exists():
            return
        
        with open(task_file, 'r') as f:
            task = json.load(f)
        
        task["status"] = status
        
        if status == "processing":
            task["processing_at"] = datetime.now(timezone.utc).isoformat()
        elif status == "completed":
            task["completed_at"] = datetime.now(timezone.utc).isoformat()
            if result:
                task["result"] = result
        elif status == "failed":
            task["failed_at"] = datetime.now(timezone.utc).isoformat()
            if error:
                task["error"] = error
        
        with open(task_file, 'w') as f:
            json.dump(task, f, indent=2)
    
    def _save_result(self, task: LLMTask, result: str):
        """保存处理结果"""
        result_file = self.result_dir / f"{task.id}_result.json"
        
        result_data = {
            "task_id": task.id,
            "type": task.type,
            "result": result,
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        with open(result_file, 'w') as f:
            json.dump(result_data, f, indent=2)
    
    def process_all_pending(self) -> Dict[str, int]:
        """
        处理所有待处理的任务
        
        Returns:
            处理结果统计
        """
        print("\n🚀 开始处理 LLM 任务...")
        
        tasks = self.get_pending_tasks()
        
        if not tasks:
            print("   ⚪ 没有待处理的任务")
            return {"total": 0, "success": 0, "failed": 0}
        
        print(f"   发现 {len(tasks)} 个待处理任务")
        
        stats = {"total": len(tasks), "success": 0, "failed": 0}
        
        for task in tasks:
            if self.process_task(task):
                stats["success"] += 1
            else:
                stats["failed"] += 1
        
        print(f"\n📊 处理完成: 总计 {stats['total']}, 成功 {stats['success']}, 失败 {stats['failed']}")
        
        return stats
    
    def get_task_statistics(self) -> Dict:
        """获取任务统计信息"""
        stats = {
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
            "total": 0
        }
        
        for task_file in self.task_dir.glob("*.json"):
            try:
                with open(task_file, 'r') as f:
                    data = json.load(f)
                status = data.get("status", "unknown")
                if status in stats:
                    stats[status] += 1
                stats["total"] += 1
            except:
                continue
        
        return stats


# OpenClaw Agent 调用示例
"""
# 这是 OpenClaw Agent 需要执行的代码

from llm_task_processor import LLMTaskProcessor

processor = LLMTaskProcessor()

# 获取待处理任务
tasks = processor.get_pending_tasks()

for task in tasks:
    # 调用 kimi-k2.5
    # 注意: 这里需要使用 OpenClaw 的方式调用 LLM
    
    # 方式1: 如果可以直接调用
    # result = call_kimi_directly(task.prompt)
    
    # 方式2: 通过 sessions_send 发送给主 agent
    # sessions_send(session_key, f"请分析以下prompt并返回JSON: {task.prompt}")
    
    # 方式3: 保存到文件，等待主 agent 处理
    # (当前实现)
    
    pass
"""


# 测试代码
if __name__ == "__main__":
    print("🧪 测试 LLM 任务处理器...")
    
    processor = LLMTaskProcessor()
    
    # 创建测试任务
    test_task_dir = Path("/tmp/llm_tasks")
    test_task_dir.mkdir(exist_ok=True)
    
    test_task = {
        "id": "test_sim_001",
        "type": "similarity",
        "prompt": "计算两个市场的语义相似度...",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    with open(test_task_dir / "test_sim_001.json", 'w') as f:
        json.dump(test_task, f, indent=2)
    
    print(f"✅ 创建测试任务: test_sim_001")
    
    # 获取待处理任务
    tasks = processor.get_pending_tasks()
    print(f"📋 待处理任务数: {len(tasks)}")
    
    # 获取统计
    stats = processor.get_task_statistics()
    print(f"📊 任务统计: {stats}")
    
    # 注意: 实际处理需要 OpenClaw Agent 调用 LLM
    print("\n💡 下一步:")
    print("   OpenClaw Agent 调用 process_all_pending() 处理任务")
    print("   或使用 process_task(task) 逐个处理")
    
    print("\n✅ 测试完成!")