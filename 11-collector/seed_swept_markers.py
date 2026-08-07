#!/usr/bin/env python3
"""一次性:给**已经采全过**的已关闭市场补上 A4 完成标记(2026-08-06)。

## 为什么需要它

不变量 A4(每个市场关闭后必须有且至少有一次完整采集)落地时,
目标集从「已关闭 ∧ 一笔都没有」改成「已关闭 ∧ 没有完成标记」。
如果不先补标记,52,178 个已关闭市场会**全部**重新扫一遍 —— 其中一大半是白扫。

## 判据:只标记**证据确凿**的那些

标记条件 = `成交湖里最后一次采集时刻 > 我们首次观测到它已关闭的时刻`。

为什么用「我们首次观测到它已关闭」而不是 Gamma 给的 `end_date`:
后者是**名义**结束日期,市场可能提前关也可能拖后关,拿它当判据等于把
正确性押在一个不可靠的外部字段上。前者只依赖**我们自己做过什么**。

⭐**不对称原则(本脚本的核心)**:

> 拿不准的一律**不标记**,让它再被扫一遍。
> 误判成"没采过" = 白跑一次接口;误判成"采过了" = **永久丢一段数据**。

所以本脚本只做减法,不做推测:凡是证据不足的,一个都不标。
实测(2026-08-06):52,178 个已关闭市场里
  - 15,138 个证据确凿(关闭后采过)→ 标记
  - 12,268 个一笔都没有            → 不标,照旧扫
  - 24,772 个见过它开着、之后再没采过 → 不标,**这就是洞 0 要补的那批**

## 幂等

已有标记的不再重复写(复跑写 0 条)。

判据:10-tests/unit/test_seed_swept_markers.py
"""
from __future__ import annotations

import argparse
import datetime as dt

import storage_engine as se

# 只依赖「我们自己做过什么」:
#   t_closed  = 我们**首次**把这个市场记成 closed 的时刻(注册表是 append-only,查得到)
#   last_ing  = 成交湖里这个市场**最后一次被采集**的时刻
# last_ing > t_closed ⇒ 在"已经知道它关了"之后还采过一遍 ⇒ 那一遍是完整的。
SEED_SQL = """
with reg as (select condition_id, closed, snapshot_at
             -- union_by_name 必须带:注册表加字段后新老两版并存,不带它会静默丢列
             -- (2026-08-07 实测,判据 test_registry_schema_evolution.py)
             from read_parquet('{registry}/*.parquet', union_by_name=true)),
closed_at as (select condition_id, min(snapshot_at) t_closed
              from reg where closed = true group by 1),
latest as (select * from (
    select condition_id, closed,
           row_number() over (partition by condition_id order by snapshot_at desc) rn
    from reg) where rn = 1 and closed = true),
ing as (select condition_id, max(ingested_at) last_ing
        from read_parquet('{raw}/**/*.parquet', union_by_name=true) group by 1),
already as ({swept_src})
select l.condition_id
from latest l
join closed_at c using (condition_id)
join ing i using (condition_id)
left join already a using (condition_id)
where i.last_ing > c.t_closed and a.condition_id is null
"""

_EMPTY_SWEPT = "select null::VARCHAR as condition_id where false"
BATCH = 5000


def find_already_complete() -> list[str]:
    """返回可以安全标记为"已扫过"的 condition_id 列表(证据确凿的那些)。"""
    con = se.duckdb_conn()
    try:
        swept_src = (
            f"select distinct condition_id from read_parquet('{se.SWEPT_DIR}/*.parquet')"
            if se.has_swept_data() else _EMPTY_SWEPT)
        sql = SEED_SQL.format(registry=se.DATA_ROOT / "registry", raw=se.RAW_DIR,
                              swept_src=swept_src)
        return [r[0] for r in con.sql(sql).fetchall()]
    finally:
        con.close()


def seed(dry_run: bool = False) -> int:
    cids = find_already_complete()
    if dry_run:
        print(f"[种子标记 试跑] 证据确凿、可标记的市场:{len(cids)} 个(未写入)")
        return len(cids)
    now = int(dt.datetime.now(dt.UTC).timestamp())
    for i in range(0, len(cids), BATCH):
        se.write_swept([{"condition_id": c, "swept_at": now, "source": "seed",
                         # 种子标记不知道当初补了多少笔 —— 写 -1 而不是 0,
                         # 免得和"扫过但一笔没有"混为一谈(那是两件事)。
                         "trades_written": -1} for c in cids[i:i + BATCH]])
    print(f"[种子标记] 已写入 {len(cids)} 个(source=seed);复跑将写 0 条(幂等)")
    return len(cids)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="给已经采全过的已关闭市场补 A4 完成标记(一次性、幂等)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="确认写入")
    a = ap.parse_args()
    if a.dry_run or not a.yes:
        seed(dry_run=True)
        if not a.yes:
            print("(未加 --yes,只试跑)")
    else:
        seed(dry_run=False)
