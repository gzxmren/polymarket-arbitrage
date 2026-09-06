#!/usr/bin/env python3
"""registration_backlog.py — 注册积压:被预算跳过的市场必须留下名单(2026-08-05)。

## 修的是什么

`register_new_markets` 的预算闸(个数 + 时间)超了就停 —— 那是**降级**,是对的。
错的是跳过的 cid 没有任何地方持久化:候选集 `new` 完全来自本轮 firehose 窗口
(`active - registry`),而 `run_once` 只要采到东西就推进 watermark,
所以下一轮的窗口里**根本没有**上一轮被跳过的那批 —— 它们只有**再成交一次**才回得来。

⇒ **丢得与结果相关**:`items` 按 firehose 新鲜度排序,低频盘天然排后面先被砍;
低频盘又最不可能在下个窗口再成交。两头指向同一批。
(丢样本本身不致命 —— 随机缺失是噪声;**丢得与结果相关才致命**。)

## 为什么不照抄 settlement_watcher 的游标

那边的 `pending` 是**持久的全量列表**(注册表里所有未结算市场),存个排序键就够了。
这边的候选集是**易逝的**:watermark 一推进,stub(slug/title)就再也拿不到。
所以必须持久化**积压本身**,不是游标。

## 排序键 (attempts, first_seen, cid)

- `attempts` 在前 → 反复失败的**自动沉底**。否则队头一旦堆满"永远注册不上"的死号,
  预算全喂给它们,新市场永远轮不到 —— 那是用一个更糟的饿死替换掉原来的饿死
- `first_seen` 次之 → 同等尝试次数下**先来先服务**(不饿死的正面保证)
- `cid` 兜底 → 全序,结果可复现(不依赖 dict 遍历顺序)

⭐**被预算跳过 ≠ 尝试失败**:跳过的 `attempts` 不许 +1(压根没发出去查询),
否则"系统忙了几轮"会被当成"这东西有问题"而沉底 —— 要修的病换个形式复发。

判据:10-tests/unit/test_registration_backlog.py
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import storage_engine as se

BACKLOG_FILE = se.DATA_ROOT / "state" / "registration_backlog.json"

# ⚠️ 两个上限都是**占位值**,尚无实测分布支撑 —— 并入 ⏰2026-08-11 阈值校准。
# 现在只保证「存在上限 + 超了出声」,不声称数值选得对。
MAX_ATTEMPTS = 20      # ≈5 小时(15 分钟/轮)。超了判定为"这东西注册不上",丢弃并出声
MAX_BACKLOG = 2000     # 磁盘/内存安全阀。现网实测每轮涌入 63~94、预算 100,正常不该触发

_FIELDS = ("slug", "title", "first_seen", "attempts")


def _bump(counts: dict | None, key: str, n: int) -> None:
    """累加 —— 只给**真计数器**用(丢弃数:一轮里丢几次就该累计几次)。"""
    if counts is not None:
        counts[key] = counts.get(key, 0) + n


def _set(counts: dict | None, key: str, n: int) -> None:
    """直接赋值 —— 给**仪表读数**用(积压条数、最老年龄:它们是"当前是多少",不是"发生了几次")。

    不能用累加:仪表用累加,一旦哪天 settle 在一轮里被调用两次,数字就凭空翻倍,
    而它看起来完全正常(仍是个合理的正整数)—— 又一个"看着对但是错的"计数。
    """
    if counts is not None:
        counts[key] = n


def _sort_key(cid: str, e: dict) -> tuple:
    """见模块 docstring。attempts 在最前是为了让死号沉底,不是为了"公平"。"""
    return (e["attempts"], e["first_seen"], cid)


def load(path: Path = BACKLOG_FILE) -> dict[str, dict]:
    """读积压。任何损坏/缺失降级为空 —— 与 cycle_state 同一约定(丢状态只影响节奏)。

    ⚠️ 逐条校验:外部写坏的**单条**记录不许污染整份积压,也不许在下游炸成 KeyError。
    """
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for cid, e in raw.items():
        if not isinstance(e, dict) or not all(k in e for k in _FIELDS):
            continue
        if not isinstance(e["first_seen"], int) or not isinstance(e["attempts"], int):
            continue
        out[cid] = {k: e[k] for k in _FIELDS}
    return out


def save(path: Path, backlog: dict[str, dict]) -> None:
    """原子落位(临时文件 + os.replace),避免下一轮读到半截文件。

    每轮是独立进程:不落盘 = 等于没有积压 = bug 原样存在。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(backlog))
        os.replace(tmp, path)
    except OSError as e:
        print(f"[注册积压保存失败] {e}(下轮退化为旧行为:跳过的市场须再成交才回得来)",
              flush=True)


def merge(backlog: dict[str, dict], fresh: dict[str, dict], now: int) -> dict[str, dict]:
    """把本轮 firehose 新发现的候选并入积压。

    已在积压里的:**保留原 first_seen 与 attempts**。
    - 不刷新 `first_seen` —— 否则高频盘每轮重新出现、每轮刷新时间戳,
      会把冷门盘不断挤到队尾,「先来先服务」被悄悄架空
    - 不清零 `attempts` —— 否则死号永远沉不下去,反复占队头
    """
    out = dict(backlog)
    for cid, stub in fresh.items():
        if cid in out:
            out[cid] = {**out[cid], "slug": stub.get("slug"), "title": stub.get("title")}
        else:
            out[cid] = {"slug": stub.get("slug"), "title": stub.get("title"),
                        "first_seen": now, "attempts": 0}
    return out


def order(backlog: dict[str, dict]) -> dict[str, dict]:
    """按 (attempts, first_seen, cid) 升序,产出给 register_new_markets 消费的 stub 字典。

    返回 dict 而非 list:`register_new_markets` 收的就是 `dict[cid, stub]`,
    而 Python 3.7+ 的 dict 保插入顺序 —— 顺序即优先级,不必改它的签名。
    """
    items = sorted(backlog.items(), key=lambda kv: _sort_key(*kv))
    return {cid: {"slug": e["slug"], "title": e["title"]} for cid, e in items}


def settle(backlog: dict[str, dict], attempted, registered, now: int,
           counts: dict | None = None,
           max_attempts: int = MAX_ATTEMPTS, max_size: int = MAX_BACKLOG) -> dict[str, dict]:
    """按本轮结果更新积压,并出声计数。

    - 注册成功 → 移出
    - **真尝试过**但没成功 → `attempts += 1`(沉底一格)
    - 没轮到(预算跳过) → 原样留下,`attempts` **不变**
    - 超 `max_attempts` → 丢弃 + 出声(判定为"这东西注册不上",否则死号堵住队头)
    - 超 `max_size` → 丢**沉底那端**(attempts 最多/最新的),保住等最久的那批,
      并出声。⚠️ 若按 first_seen 丢最老的,丢掉的正是最该救的一批 —— 那是与结果相关的丢法
    """
    attempted, registered = set(attempted), set(registered)
    out = {}
    dropped = 0
    for cid, e in backlog.items():
        if cid in registered:
            continue
        n = e["attempts"] + 1 if cid in attempted else e["attempts"]
        if n >= max_attempts:
            dropped += 1
            continue
        out[cid] = {**e, "attempts": n}

    if len(out) > max_size:
        keep = sorted(out.items(), key=lambda kv: _sort_key(*kv))[:max_size]
        dropped += len(out) - len(keep)
        out = dict(keep)

    _set(counts, "pending_registration_count", len(out))
    _bump(counts, "pending_registration_dropped_count", dropped)
    if dropped:
        # 计数落 parquet 已满足"出声"的字面要求,但**日志才是人每天真会看的地方**,
        # 而丢弃是罕见且不可逆的(等于宣布"这个市场我们不采了")。查 parquet 才知道
        # = 实际上没人会知道。稳态 dropped==0 故这行天然静默,不构成噪音。
        print(f"[注册积压] 丢弃 {dropped} 个(超 {max_attempts} 次尝试或超容量 {max_size})",
              flush=True)
    # 「积压在涨」和「积压卡住了」是两种病,只看条数分不出来 —— 故最老年龄单独出声。
    # 空积压必须是 0 而不是 None/上一轮残值,否则告警会盯着一个假数字看。
    oldest = min((e["first_seen"] for e in out.values()), default=now)
    _set(counts, "pending_registration_oldest_age_s", max(0, now - oldest))
    return out
