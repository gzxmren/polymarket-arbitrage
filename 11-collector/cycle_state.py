#!/usr/bin/env python3
"""cycle_state.py — 跨周期小状态(每轮是独立进程,要累积的东西必须落盘)。

放在这里而不是各自复制一份:本项目已有 data_sync / data_sync_v2 并存、权威不明的前科,
两套实现必然分叉,而分叉的那套一定是没被测试盖住的那套。

目前承载:
- 结算守望的轮转游标(settlement_watcher.CURSOR_FILE)
- 「连零」计数 —— 静默失败守护的通用底座:真值断供 / 注册链路断供都用它。
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def read_state(path: Path, key: str, default):
    """读小状态文件。任何损坏/缺失都降级为默认值 —— 状态丢失只影响节奏,不丢数据。"""
    try:
        return json.loads(path.read_text())[key]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return default


def write_state(path: Path, key: str, value, what: str = "状态") -> None:
    """原子落位(临时文件 + os.replace),避免被下一轮读到半截文件。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps({key: value}))
        os.replace(tmp, path)
    except OSError as e:
        print(f"[{what}保存失败] {e}(仅影响下轮节奏,不丢数据)", flush=True)


# ---------- 静默失败守护的通用底座 ----------

def next_zero_streak(prev: int, newly: int, attempted: int) -> int:
    """「连续多少轮有活干却零产出」的推进规则(纯函数)。

    补的是 2026-08-03 暴露的监控盲点:显式失败(报错/崩溃)有人管,
    **静默失败(一切正常但产出为 0)没人管** —— 那次潜伏了 11 天。

    - 有活干却零产出 → 进位(故障形态)
    - 有任何产出     → 归零(链路通)
    - **没活可干**   → 保持不变:那是正常不是故障,既不该误报也不该掩盖
    """
    if attempted <= 0:
        return prev
    return 0 if newly > 0 else prev + 1


def read_streak(path: Path) -> int:
    v = read_state(path, "zero_streak", 0)
    return v if isinstance(v, int) and v >= 0 else 0


def write_streak(path: Path, n: int) -> None:
    write_state(path, "zero_streak", n, "连零计数")
