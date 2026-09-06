"""判据质量守卫:防两个**同一天里重复出现**的形状。

## 形状一:文本检查冒充行为检查(2026-08-23 一天内 3 次)

写成 `src = inspect.getsource(mod); assert "某句话" in src` ——
只要那句注释还在就永远绿,被检验对象怎么坏都抓不住。实例:
- `test_edge_is_already_net_of_the_entry_spread` 只匹配 docstring 里一句话
- 时间防火墙那条起初查「源码里有没有 getaddrinfo 这个词」,匹配到了**解释为什么不这么做的注释**
- V3 的 H1 判据查「源码里有没有 verify_against_lake」,变异把调用改成 `if False:` 照样绿

⭐**有分辨力的区分**:
- `assert "x" not in src` = **结构守卫**(禁某种写法),合法且有用
- `assert "x" in src` = **拿文本当行为的替身**,除非它守的是"必须存在的结构",否则一律可疑
⇒ 后者必须在判据 docstring 里写明 `结构检查:` 并说清它守的是什么结构。

## 形状二:f-string 拼 SQL 的写操作(2026-08-23 一天内 2 次)

`con.execute(f"SET ...{x}")` / `DELETE ... {x}` —— 实测能执行多条语句,
而 DuckDB 的 SQL 能 COPY 到任意路径、读任意文件。写操作一律要参数化。
"""
import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = PROJECT_ROOT / "10-tests"
GUARDED_SRC_DIRS = [PROJECT_ROOT / "06-tools" / "analysis",
                    PROJECT_ROOT / "11-collector",
                    PROJECT_ROOT / "08-backtests"]

# 白名单:确实在守"某个结构必须存在"的判据,必须在 docstring 里写 `结构检查:`
_JUSTIFY_MARK = "结构检查:"

# ⭐棘轮基线(2026-08-23 冻结):守卫上线时**继承下来**的欠账。
# 规则:**只许缩,不许涨** —— 新写的代码一条都不许进这份名单。
# 这不是"把守卫关掉":基线是逐条列名的、可见的、且下面有一条判据钉住它不会变长。
# ⚠️ 我自己当天写的 5 条已就地清掉,不在基线里 —— 继承的债可以缓,自己刚欠的不行。
_TEXT_BASELINE = {
    "test_alert_queue.py": {"test_run_cycle_uses_the_return_value_not_a_reread"},
    "test_backfill_closed.py": {"test_uses_the_shared_append_only_write_path"},
    "test_get_baseline.py": {"test_difference_4_both_sides_now_accept_a_deadline"},
    "test_history_truncation.py": {"test_no_code_still_promises_a_backfill_that_cannot_exist",
                                   "test_the_false_self_healing_premise_is_corrected_in_writing"},
    "test_poll_rotation.py": {"test_run_once_actually_uses_the_selector"},
    "test_registration_budget.py": {"test_known_unbounded_segments_are_pinned"},
    "test_registry_schema_evolution.py": {"test_the_reader_declares_the_schema_explicitly"},
    "test_settlement_time_gate.py": {"test_run_cycle_actually_passes_the_gate",
                                     "test_skipped_count_is_persisted_to_the_heartbeat"},
    "test_wallet_skill_persistence.py": {"test_expectation_check_is_wired_into_the_main_pipeline",
                                         "test_null_distribution_comparison_is_computed_not_hardcoded"},
}
_SQL_BASELINE = {"wallet_skill_persistence.py:284", "wallet_skill_persistence.py:288"}
_BASELINE_FROZEN_AT = "2026-08-23"


def _test_funcs(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
            yield n


def _uses_getsource(fn: ast.FunctionDef) -> bool:
    return any(isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute)
               and s.func.attr == "getsource" for s in ast.walk(fn))


def _positive_str_in_src_assert(fn: ast.FunctionDef) -> bool:
    """有没有 `assert "字面量" in 变量` 这种**正向**文本断言。"""
    for s in ast.walk(fn):
        if not isinstance(s, ast.Assert):
            continue
        for cmp_ in ast.walk(s.test):
            if (isinstance(cmp_, ast.Compare) and cmp_.ops
                    and isinstance(cmp_.ops[0], ast.In)
                    and isinstance(cmp_.left, ast.Constant)
                    and isinstance(cmp_.left.value, str)):
                return True
    return False


@pytest.mark.parametrize("path", sorted(TESTS_DIR.rglob("test_*.py")), ids=lambda p: p.name)
def test_no_text_match_masquerading_as_behavior_check(path):
    """⭐正向的「源码里有没有这句话」断言,必须在 docstring 里写明它守的是什么结构。

    否则它就是行为检查的替身 —— 注释还在就永远绿(2026-08-23 一天内犯 3 次)。
    """
    bad = []
    for fn in _test_funcs(path):
        if not (_uses_getsource(fn) and _positive_str_in_src_assert(fn)):
            continue
        doc = ast.get_docstring(fn) or ""
        if _JUSTIFY_MARK not in doc and fn.name not in _TEXT_BASELINE.get(path.name, set()):
            bad.append(fn.name)
    assert not bad, (
        f"{path.name} 里这些判据用「源码里有没有某句话」当断言,却没说明守的是什么结构:{bad}\n"
        f"⇒ 它们是行为检查的替身,注释还在就永远绿。要么改成行为断言,"
        f"要么在 docstring 里写 `{_JUSTIFY_MARK}...` 说清守的是哪条结构不变量。")


_WRITE_SQL = re.compile(r"\b(SET|DELETE|INSERT|UPDATE|COPY|ATTACH|INSTALL|LOAD)\b", re.I)

# ⚠️ **已知盲区,如实写明,不假装堵上了**(2026-08-26 review 指出 + 实测确认):
#   `CREATE ... AS SELECT ... WHERE {x}` 这类语句不在上面的正则里,会绕过扫描。
#   为什么不补进去:实测 **DuckDB 的 `CREATE VIEW` 不支持参数绑定**
#     CREATE VIEW v AS SELECT * FROM read_parquet(?)  → BinderException
#     CREATE TABLE t AS SELECT ? AS x                 → 支持
#   ⇒ `CREATE VIEW ... read_parquet('{glob}')` 是**结构性必须拼接**,不是偷懒。
#   全项目现有 20+ 处 CREATE 插值,逐一核对过:插的都是内部算出的 glob / 浮点数 / 列名,
#   **没有一处接受外部输入**,故当前实际注入面为零。
#   ⇒ 处置:不扩大正则(那会逼出一份 20 条的白名单,反而稀释棘轮),
#     改为下面这条判据 —— 盯住"CREATE 插值里有没有混进外部输入"这个真正要紧的点。
_CLI_TAINT = re.compile(r"\b(args|argv|sys\.argv|input|os\.environ|getenv)\b")


@pytest.mark.parametrize("d", GUARDED_SRC_DIRS, ids=lambda p: p.name)
def test_no_fstring_interpolation_in_write_sql(d):
    """⭐写类 SQL 不许用 f-string 拼(2026-08-23 一天内犯 2 次)。

    实测:拼接能执行多条语句,而 DuckDB 的 SQL 能 COPY 到任意路径、读任意文件 ⇒
    一次注入等于本进程权限下的任意文件读写。写操作一律参数化。
    """
    offenders = []
    for py in sorted(d.glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            lit = "".join(v.value for v in node.values
                          if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if not _WRITE_SQL.search(lit):
                continue
            # ⭐只盯**变量**插值。全大写的模块级常量是**代码**不是数据,
            #   拿它拼 SQL 不构成注入面(2026-08-23 首版守卫太粗,把 {EDGE_SQL} 也报了)。
            risky = [v for v in node.values if isinstance(v, ast.FormattedValue)
                     and not (isinstance(v.value, ast.Name) and v.value.id.isupper())]
            if risky and f"{py.name}:{node.lineno}" not in _SQL_BASELINE:
                offenders.append(f"{py.name}:{node.lineno}")
    assert not offenders, (
        f"这些地方用 f-string 拼了写类 SQL:{offenders}\n⇒ 改成参数化 `con.execute(sql, [args])`。")


@pytest.mark.parametrize("d", GUARDED_SRC_DIRS, ids=lambda p: p.name)
def test_create_statements_never_interpolate_external_input(d):
    """⭐CREATE 类语句可以拼接(DuckDB 的 CREATE VIEW 无法绑参数,实测),
    但**绝不许把外部输入拼进去** —— 那才是真正的注入面。

    盯的是:f-string 里插的值是否来自 argparse / argv / 环境变量 / input()。
    """
    offenders = []
    for py in sorted(d.glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            lit = "".join(v.value for v in node.values
                          if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if not re.search(r"\bCREATE\b", lit, re.I):
                continue
            for v in node.values:
                if not isinstance(v, ast.FormattedValue):
                    continue
                if _CLI_TAINT.search(ast.unparse(v.value)):
                    offenders.append(f"{py.name}:{node.lineno} ← {ast.unparse(v.value)}")
    assert not offenders, (
        f"这些 CREATE 语句把**外部输入**拼进了 SQL:{offenders}\n"
        f"⇒ CREATE VIEW 确实不能绑参数,但外部输入必须先校验/白名单,不能直接拼。")


def test_create_taint_guard_catches_a_real_case():
    """结构检查:验证上面那条在已知坏样本上会红 —— 守的是「外部输入不得入 SQL」。"""
    bad = 'con.execute(f"CREATE VIEW v AS SELECT * FROM read_parquet(\'{args.path}\')")'
    tree = ast.parse(bad)
    hit = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue) and _CLI_TAINT.search(ast.unparse(v.value)):
                    hit.append(ast.unparse(v.value))
    assert hit == ["args.path"], "外部输入拼进 CREATE 竟然没被抓到"


# ---------- 两条守卫的自检(先在坏数据上验会不会红) ----------

def test_text_guard_catches_a_fake_assertion(tmp_path):
    """结构检查:验证上面那条守卫在**已知坏样本**上真的会红。"""
    f = tmp_path / "test_fake.py"
    f.write_text('import inspect\n'
                 'def test_x():\n'
                 '    """没有说明的文本断言。"""\n'
                 '    src = inspect.getsource(object)\n'
                 '    assert "某句注释" in src\n', encoding="utf-8")
    fn = next(_test_funcs(f))
    assert _uses_getsource(fn) and _positive_str_in_src_assert(fn)


def test_text_guard_allows_negative_structural_assertions(tmp_path):
    """结构检查:`not in` 是禁某种写法的结构守卫,不该被误伤。"""
    f = tmp_path / "test_ok.py"
    f.write_text('import inspect\n'
                 'def test_y():\n'
                 '    src = inspect.getsource(object)\n'
                 '    assert "banned" not in src\n', encoding="utf-8")
    fn = next(_test_funcs(f))
    assert not _positive_str_in_src_assert(fn), "负向断言被误判成了正向文本检查"


def test_sql_guard_catches_fstring_write(tmp_path):
    """结构检查:验证 SQL 守卫在已知坏样本上会红。"""
    f = tmp_path / "bad.py"
    def _risky(code):
        tree = ast.parse(code)
        out = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.JoinedStr):
                continue
            lit = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
            if not _WRITE_SQL.search(lit):
                continue
            if [v for v in n.values if isinstance(v, ast.FormattedValue)
                    and not (isinstance(v.value, ast.Name) and v.value.id.isupper())]:
                out.append(n.lineno)
        return out
    # 坏样本:插的是 CLI 传进来的变量 ⇒ 必须抓到
    assert _risky('x="1GB"\ncon.execute(f"SET memory_limit=\'{x}\'")\n')
    # 好样本:插的是全大写模块常量 ⇒ 不许误报
    assert not _risky('EDGE_SQL="a-b"\ncon.execute(f"UPDATE t SET c = {EDGE_SQL}")\n')


# ---------- 棘轮:基线只许缩不许涨 ----------

def test_debt_baseline_only_shrinks():
    """⭐基线里已经不存在的条目必须被删掉 —— 否则它会变成"永久豁免"的藏身处。

    结构检查:逐条核实基线里点名的判据函数**仍然存在且仍然违规**;
    守的是「棘轮只许往一个方向走」这条结构不变量。
    不做这一步,基线就会从"待还的债"悄悄变成"关掉守卫的开关"。
    """
    stale = []
    for fname, names in _TEXT_BASELINE.items():
        path = next(iter(TESTS_DIR.rglob(fname)), None)
        if path is None:
            stale.append(f"{fname}(文件没了)")
            continue
        offenders = {fn.name for fn in _test_funcs(path)
                     if _uses_getsource(fn) and _positive_str_in_src_assert(fn)
                     and _JUSTIFY_MARK not in (ast.get_docstring(fn) or "")}
        for n in names - offenders:
            stale.append(f"{fname}::{n}")
    assert not stale, (
        f"基线里这些条目已经不违规了(或文件没了),请从 _TEXT_BASELINE 删掉:{stale}\n"
        f"⇒ 留着它们等于给未来的同名判据开后门。")


def test_baseline_is_dated_and_bounded():
    """结构检查:基线必须带冻结日期,且规模不许超过冻结当时 —— 守的是「债务可见且有界」。"""
    assert _BASELINE_FROZEN_AT == "2026-08-23"
    assert sum(len(v) for v in _TEXT_BASELINE.values()) <= 12, "文本欠账变多了"
    assert len(_SQL_BASELINE) <= 2, "SQL 欠账变多了"
