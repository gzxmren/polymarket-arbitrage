#!/usr/bin/env python3
"""判据焊死:注册表加字段时,新老两版文件必须都读得出来(2026-08-07)。

## 由来(实测,非假想)

注册表是 append-only 的一摞 Parquet 文件,每次采集追加新文件、从不改旧文件。
今天要给它加四个字段(`closed_time` 等),于是**老文件 14 列、新文件 18 列**并存。

动手前实测(拿真实注册表文件的副本做的):

| 读法 | 结果 |
|---|---|
| `pq.read_table(目录)` —— `load_registry` 现在用的 | 🔴 **只读出 14 列,新列全丢,不报错** |
| 同上但把新文件排到最前 | 🔴 照样丢(与文件顺序无关) |
| `pyarrow.dataset(目录)` 默认 | 🔴 照样丢 |
| DuckDB `read_parquet('*.parquet')` —— 回填/seed 用的 | 🔴 照样丢 |
| ✅ `pq.read_table(目录, schema=完整schema)` | 🟢 老文件缺的列补成空值,新文件的值保留 |
| ✅ DuckDB `read_parquet(..., union_by_name=true)` | 🟢 同上 |

⇒ 若不先修这一步就加字段:采集器照常跑、日志照常打、看门狗照常绿,
而那四个新字段**永远是空的**。等几天后发现全空,还得先怀疑是不是接口没返回。
**"一切正常,只有某个东西恒为空,而没人规定过它不该为空"** —— 本项目反复发作的那个病。

## 这一条的通用式(用户 2026-08-07 一句话点破)

> **核心就是前后两个版本的数据 schema 造成的错误。**

这个项目此前**没有"数据格式版本"这个概念**,今天是第一次撞上。
本判据焊的不是那四个字段,是**"格式变了之后新老都得读得出来"这件事本身**。

## 本判据**不能**回答什么

1. 不保证新字段的**值**是对的(那是回填脚本的判据)。只保证它不会在读取环节消失。
2. 不覆盖成交湖(`raw/`)—— 那边一直用 `union_by_name=true`,本来就是对的。
"""
import re
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = PROJECT_ROOT / "11-collector"
sys.path.insert(0, str(COLLECTOR_DIR))

import discovery_service as ds  # noqa: E402

# 模拟"上一个版本":从当前 schema 里去掉两列。
# ⚠️ 故意不写死列名清单 —— 写死的话,以后加字段还得记得来改这里,而"记得"正是靠不住的。
_OLD_DROP = ("market_class", "hft_suspect")


def _row(cid: str, schema: pa.Schema) -> dict:
    """按给定 schema 造一行,每列填一个类型正确、且**非空**的值。"""
    out = {}
    for f in schema:
        if f.name == "condition_id":
            out[f.name] = cid
        elif pa.types.is_string(f.type):
            out[f.name] = f"v-{f.name}"
        elif pa.types.is_boolean(f.type):
            out[f.name] = True
        elif pa.types.is_floating(f.type):
            out[f.name] = 1.0
        else:
            out[f.name] = 1
    return out


def _write(path: Path, rows: list[dict], schema: pa.Schema) -> None:
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def _mixed_registry(tmp_path: Path) -> Path:
    """造一个新老两版并存的注册表目录。老文件在前(真实情况就是这样)。"""
    old_schema = pa.schema([f for f in ds.MARKETS_SCHEMA if f.name not in _OLD_DROP])
    _write(tmp_path / "aaa-old.parquet", [_row("0xold", old_schema)], old_schema)
    _write(tmp_path / "zzz-new.parquet", [_row("0xnew", ds.MARKETS_SCHEMA)],
           ds.MARKETS_SCHEMA)
    return tmp_path


# ============ 组 A:新版文件里的每一列都必须读得出来 ============

def test_every_current_field_survives_a_mixed_registry(tmp_path, monkeypatch):
    """⭐核心判据:老版文件在场时,新版行的**每一个字段**都必须原样读到。

    这条会在以下任一情况变红:
    - 有人把 `load_registry` 里的 `schema=` 参数去掉(退回静默丢列)
    - 有人给 `parse_market` 加了字段却忘了同步 `MARKETS_SCHEMA`
    """
    monkeypatch.setattr(ds, "REGISTRY_DIR", _mixed_registry(tmp_path))
    reg = ds.load_registry()
    new = reg["0xnew"]
    missing = [f.name for f in ds.MARKETS_SCHEMA if new.get(f.name) is None]
    assert not missing, f"新版行的这些字段在读取时丢了:{missing}"


def test_the_old_rows_are_not_lost(tmp_path, monkeypatch):
    """老文件的行不许因为格式对不上就消失 —— 那是把历史整段丢掉。"""
    monkeypatch.setattr(ds, "REGISTRY_DIR", _mixed_registry(tmp_path))
    reg = ds.load_registry()
    assert set(reg) == {"0xold", "0xnew"}


def test_missing_columns_in_old_files_become_null_not_an_error(tmp_path, monkeypatch):
    """老文件缺的那几列读出来是空值(而不是报错、也不是拿别的列的值顶上)。"""
    monkeypatch.setattr(ds, "REGISTRY_DIR", _mixed_registry(tmp_path))
    old = ds.load_registry()["0xold"]
    assert all(old[name] is None for name in _OLD_DROP)
    assert old["condition_id"] == "0xold"      # 其余列不受影响


def test_an_all_old_registry_still_works(tmp_path, monkeypatch):
    """全是老文件时也不许崩 —— 加字段上线的那一刻,库里全是老文件。"""
    old_schema = pa.schema([f for f in ds.MARKETS_SCHEMA if f.name not in _OLD_DROP])
    _write(tmp_path / "a.parquet", [_row("0xold", old_schema)], old_schema)
    monkeypatch.setattr(ds, "REGISTRY_DIR", tmp_path)
    assert set(ds.load_registry()) == {"0xold"}


def test_an_empty_registry_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "REGISTRY_DIR", tmp_path)
    assert ds.load_registry() == {}


# ============ 组 B:结构判据 —— 另外两个读取点也不许漏 ============

def _read_parquet_calls(src: str) -> list[str]:
    """揪出源码里每一处 `read_parquet(...)` 的完整参数串(括号配对,认得嵌套)。

    ⚠️ 这个函数是 2026-08-07 评审抓出漏洞后重写的。原来的写法是一条正则:
        r"read_parquet\\(\\s*'?\\{?registry\\}?[^)]*\\)"
    它只认 `.format(registry=...)` 那一种拼串方式。实测:
        read_parquet(f'{REGISTRY_DIR}/*.parquet')        → **匹配不到**
        read_parquet(str(REGISTRY_DIR) + '/*.parquet')   → **匹配不到**
    而 `REGISTRY_DIR` 恰恰是这个模块导出的真实常量名 —— 也就是说,
    **最自然的那种写法正好是它抓不到的**,而判据的说明里却写着"挡住以后新增第四处"。
    判据在验证自己的替身,今天第三次。
    """
    out, i = [], 0
    while (i := src.find("read_parquet(", i)) != -1:
        j, depth = i + len("read_parquet("), 1
        while j < len(src) and depth:
            depth += (src[j] == "(") - (src[j] == ")")
            j += 1
        out.append(src[i:j])
        i = j
    return out


def test_every_duckdb_reader_of_the_registry_unions_by_name():
    """⭐同一个隐患有三处(load_registry、回填目标集、seed 脚本)。

    实测 DuckDB 不带 `union_by_name=true` 时**同样静默丢列**。
    修一处不够 —— 这条判据把另外两处也焊上,并挡住以后新增第四处。

    认"读的是不是注册表"用的是宽判据:参数串里出现 `registry`(不分大小写)就算,
    于是 `{registry}`、`REGISTRY_DIR`、`registry_dir` 三种命名风格都盖得住。
    ⚠️ 仍是**代理判据**:有人把路径拆成变量、参数串里一个 registry 字样都不出现的话,
    它还是抓不到。这一层写清楚,不许再吹成"以后不会重演"。
    """
    offenders = []
    for p in sorted(COLLECTOR_DIR.glob("*.py")):
        for call in _read_parquet_calls(p.read_text(encoding="utf-8")):
            if "registry" in call.lower() and "union_by_name" not in call:
                offenders.append(f"{p.name}: {call[:80]}")
    assert not offenders, ("这些地方读注册表没带 union_by_name=true,"
                           f"格式变更时会静默丢列:{offenders}")


def test_the_blind_spot_of_the_previous_regex_is_now_covered():
    """把评审实测到的两种"抓不到"的写法直接喂进来,证明现在抓得到。

    没有这一条的话,上面那条判据改没改对全靠我说 —— 而它上一版正是"看起来对"。
    """
    bad = ("x = read_parquet(f'{REGISTRY_DIR}/*.parquet')\n"
           "y = read_parquet(str(REGISTRY_DIR) + '/*.parquet')\n")
    calls = _read_parquet_calls(bad)
    assert len(calls) == 2
    assert all("registry" in c.lower() and "union_by_name" not in c for c in calls)


def test_every_writer_of_the_registry_declares_the_schema():
    """⭐读侧焊住了,写侧也得焊(评审指出:7 条判据只守了一半)。

    今天两个写者(discovery_service / settlement_watcher)共享**同一个**
    MARKETS_SCHEMA 对象,零风险。焊的是未来:CLAUDE.md 自己记着
    "backfill_*.py / cleanup_*.py 一次性脚本散落、不在 import graph 里" ——
    这种脚本直接 `pa.Table.from_pylist(rows)`(不带 schema、类型靠推断)写进注册表,
    就会在**写侧**重新引入同一个病,而读侧那几条判据一条都抓不住。
    """
    import ast
    offenders = []
    for p in sorted(COLLECTOR_DIR.glob("*.py")):
        src = p.read_text(encoding="utf-8")
        if "REGISTRY_DIR /" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(src, node) or ""
            if "REGISTRY_DIR /" in body and "MARKETS_SCHEMA" not in body:
                offenders.append(f"{p.name}::{node.name}")
    assert not offenders, (
        f"这些函数往注册表写文件却没声明 MARKETS_SCHEMA:{offenders}")


def test_the_reader_declares_the_schema_explicitly():
    """`load_registry` 必须把完整 schema 显式交给读取器。

    ⚠️ 这是**代理判据**:真正的保证是组 A 那条行为判据。
    留它是因为它指得更准 —— 组 A 变红时,这条能直接说出是哪一行被改了。
    """
    import inspect
    src = inspect.getsource(ds.load_registry)
    assert "schema=MARKETS_SCHEMA" in src
