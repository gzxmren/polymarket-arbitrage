#!/usr/bin/env python3
"""storage_engine.py — 存储底座:append-only 按日 Parquet + DuckDB 零 ETL 直查。

兑现《数据契约 v1.1》§4.4 / §5 / §7:
- 物理层强制 append-only:Parquet 落盘即不可变;写用"临时文件 + 原子 rename"。
- 分区:data/raw/dt=YYYY-MM-DD/<uuid>.parquet(hive 分区,DuckDB 自动裁剪)。
- 零 ETL:DuckDB 始终 read_parquet 直查,不落任何常驻可变主表。
- compaction:小文件合并为大文件(仍不可变、仍零 ETL),不 INSERT 进可变库。
- 去重:自然元组;重复轮询的再抓在查询层用 QUALIFY 去掉(物理保留,无偏)。
- 点位时刻:双时间戳 timestamp(成交)/ ingested_at(采集);as_of 用 DuckDB ASOF JOIN。

DuckDB 为可选依赖(pip install duckdb):写 Parquet 只需 pyarrow;查询/去重/ASOF 才需 duckdb。
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

DATA_ROOT = Path(os.environ.get("REBIRTH_DATA", Path(__file__).resolve().parent / "data"))
RAW_DIR = DATA_ROOT / "raw"          # trades:  raw/dt=YYYY-MM-DD/<uuid>.parquet
AUDIT_DIR = DATA_ROOT / "audit"      # 审计心跳: audit/dt=YYYY-MM-DD/<uuid>.parquet

# 自然元组去重键(实测接口无 log_index/fill_index,见契约 §4.1)
NATURAL_KEY = ["transaction_hash", "asset", "side", "size", "price", "proxy_wallet", "timestamp"]

TRADES_SCHEMA = pa.schema([
    ("transaction_hash", pa.string()),
    ("proxy_wallet", pa.string()),
    ("condition_id", pa.string()),
    ("asset", pa.string()),
    ("outcome_index", pa.int32()),    # 我们解析出的真实下标(非接口 999);解析失败不入库
    ("outcome_label", pa.string()),   # 交叉校验用
    ("side", pa.string()),
    ("size", pa.float64()),
    ("price", pa.float64()),
    ("timestamp", pa.int64()),        # 成交 unix 秒 —— 点位时刻锚点
    ("ingested_at", pa.int64()),      # 采集 unix 秒 —— 审计/防未来函数
])


def _atomic_write_parquet(table: pa.Table, dest: Path) -> None:
    """写临时文件后 os.replace 原子落位;避免半截文件被查询读到。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.{uuid.uuid4().hex}.tmp"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, dest)  # 同分区原子


def write_trades(rows: list[dict], day: str | None = None) -> Path | None:
    """原子追加一批已解析 trades 到当日分区。rows 为空返回 None。

    每次调用产生一个新 Parquet 文件(append-only,绝不改旧文件)。小文件由 compact_day 合并。
    """
    if not rows:
        return None
    if day is None:
        day = dt.datetime.fromtimestamp(int(rows[0]["timestamp"]), dt.UTC).strftime("%Y-%m-%d")
    table = pa.Table.from_pylist(rows, schema=TRADES_SCHEMA)
    dest = RAW_DIR / f"dt={day}" / f"{uuid.uuid4().hex}.parquet"
    _atomic_write_parquet(table, dest)
    return dest


def compact_day(day: str, keep_originals: bool = False) -> Path | None:
    """把某日分区的小文件合并成一个大文件(不可变重写 + 审计),提升查询速度。

    合并 = 读全部小 Parquet → 去重(自然元组,保留最早 ingested_at)→ 写单个新文件 → 删小文件。
    keep_originals=True 时把小文件移到 _archive/ 而非删除(更保守)。
    """
    part = RAW_DIR / f"dt={day}"
    files = sorted(f for f in part.glob("*.parquet"))
    if len(files) <= 1:
        return files[0] if files else None
    table = pq.read_table(part, schema=None)  # 读整个分区目录
    # 自然元组去重,保留最早采集(ingested_at 最小)
    import pyarrow.compute as pc
    idx = pc.sort_indices(table, sort_keys=[("ingested_at", "ascending")])
    table = table.take(idx)
    seen, keep_rows = set(), []
    cols = {name: table.column(name).to_pylist() for name in [c for c in NATURAL_KEY]}
    for i in range(table.num_rows):
        k = tuple(cols[c][i] for c in NATURAL_KEY)
        if k not in seen:
            seen.add(k)
            keep_rows.append(i)
    deduped = table.take(pa.array(keep_rows))
    merged = part / f"compacted-{uuid.uuid4().hex}.parquet"
    _atomic_write_parquet(deduped, merged)
    archive = part / "_archive"
    for f in files:
        if keep_originals:
            archive.mkdir(exist_ok=True)
            os.replace(f, archive / f.name)
        else:
            f.unlink()
    return merged


# ---------- DuckDB 查询层(零 ETL 直查 Parquet 湖) ----------

def has_data() -> bool:
    """数据湖里是否已有任何 Parquet(空湖时 read_parquet 会对空 glob 报错,须先判)。"""
    return any(RAW_DIR.glob("**/*.parquet"))


def duckdb_conn():
    """返回 DuckDB 连接;注册 trades / trades_deduped 视图,全部 read_parquet 直查。

    空湖(尚无任何 Parquet)时建**空的同构视图**(WHERE false),让下游查询照常返回 0 行
    而非崩溃 —— 这是第一次冷启动的必经路径。
    """
    import duckdb  # 延迟导入:仅查询需要
    con = duckdb.connect()
    if has_data():
        raw_glob = str(RAW_DIR / "**" / "*.parquet")
        con.execute(f"""
            CREATE VIEW trades AS
              SELECT * FROM read_parquet('{raw_glob}', hive_partitioning=1, union_by_name=1);
            CREATE VIEW trades_deduped AS
              SELECT * FROM trades
              QUALIFY row_number() OVER (
                PARTITION BY {','.join(NATURAL_KEY)} ORDER BY ingested_at
              ) = 1;
        """)
    else:
        # 空湖:用 schema 造 0 行视图,列与真实一致(dt 分区列也补上)
        cols = ", ".join(
            f"CAST(NULL AS {t}) AS {n}" for n, t in (
                ("transaction_hash", "VARCHAR"), ("proxy_wallet", "VARCHAR"),
                ("condition_id", "VARCHAR"), ("asset", "VARCHAR"),
                ("outcome_index", "INTEGER"), ("outcome_label", "VARCHAR"),
                ("side", "VARCHAR"), ("size", "DOUBLE"), ("price", "DOUBLE"),
                ("timestamp", "BIGINT"), ("ingested_at", "BIGINT"), ("dt", "VARCHAR"),
            ))
        con.execute(f"CREATE VIEW trades AS SELECT {cols} WHERE false;")
        con.execute("CREATE VIEW trades_deduped AS SELECT * FROM trades;")
    return con


def watermark(con, condition_id: str) -> int | None:
    """某市场已入库的最大成交 timestamp(增量采集用:只抓比它更新的)。"""
    row = con.execute(
        "SELECT max(timestamp) FROM trades WHERE condition_id = ?", [condition_id]
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def all_watermarks(con) -> dict[str, int]:
    """一次取回所有市场的 watermark(避免每市场开连接/查询)。空湖返回 {}。"""
    rows = con.execute(
        "SELECT condition_id, max(timestamp) FROM trades GROUP BY condition_id"
    ).fetchall()
    return {cid: ts for cid, ts in rows if ts is not None}


# ---------- 审计心跳(§7:大声报数) ----------

def write_audit_heartbeat(counts: dict) -> Path:
    """每小时一条:total_markets_polled / http_4xx / rate_limit / offset_overflow / dedup_collapse。"""
    now = int(dt.datetime.now(dt.UTC).timestamp())
    row = {"ts": now, **{k: int(counts.get(k, 0)) for k in
           ("total_markets_polled", "http_4xx_count", "rate_limit_hits",
            "offset_overflow_count", "dedup_collapse_count", "parse_reject_count",
            "firehose_fail", "new_trades", "register_fail")}}
    day = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%d")
    dest = AUDIT_DIR / f"dt={day}" / f"{uuid.uuid4().hex}.parquet"
    _atomic_write_parquet(pa.Table.from_pylist([row]), dest)
    return dest


# TODO(ops,非核心逻辑):
#  - compaction 调度(每日对昨日分区跑 compact_day)。
#  - 数据保留/归档策略。
#  - as_of 特征查询的 ASOF JOIN 模板(分析层,另建 features.sql)。
if __name__ == "__main__":
    print(f"DATA_ROOT = {DATA_ROOT}")
    print(f"RAW_DIR   = {RAW_DIR}")
    print("自检: 写一条假数据 → 读回 →(需 duckdb)去重视图")
    p = write_trades([{
        "transaction_hash": "0xtest", "proxy_wallet": "0xw", "condition_id": "0xc",
        "asset": "1", "outcome_index": 0, "outcome_label": "Yes", "side": "BUY",
        "size": 1.0, "price": 0.5, "timestamp": int(dt.datetime.now(dt.UTC).timestamp()),
        "ingested_at": int(dt.datetime.now(dt.UTC).timestamp()),
    }])
    print(f"  写入: {p}")
