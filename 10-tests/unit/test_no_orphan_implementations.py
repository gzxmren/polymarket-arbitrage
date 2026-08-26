"""结构守卫:**定义了却走不到的函数** —— 要么是死代码,要么是「又写了第二份实现」。

## 由来(2026-08-23,用户批评「真人程序员无法想象会犯这样的错误」)

`08-backtests/wallet_skill_v3.py` 里我在文件上半部分写了 `trade_edge()`/`window_of()`
并配了 5 条判据;到下半部分写 SQL 时**又把同一段逻辑重写了一遍**,而 `run()` 用的是后一份。
⇒ 那 5 条判据测的是**没人调用的替身**;把 SQL 里的 `<>` 手滑改成 `=`(项目反复强调
最容易改错的那个符号),21 条判据**全部保持绿色**。

⭐ 真人写到 SQL 那一步会有"我上面不是刚写过吗"的摩擦感;我没有 ——
我逐段生成时局部自洽,但不会自动回头对照两百行前写过什么。
⇒ 这条判据就是那个摩擦感的**机械替代品**:每个模块收尾时自动查一遍。

同形状此前记录:2026-08-06「替身替掉被测对象本身=判据在验证自己的替身」。
"""
import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 被守的模块 → (入口函数, 允许"入口走不到"的公开工具 → 理由)
#
# ⭐白名单有两种合法理由,**其余一律不许放行**:
#   1. "消费者入口":供别的脚本 import 使用(设计单里写明了的)
#   2. "规格实现":Python 版是**规格**,生产用等价的 SQL —— 仅当存在一条
#      **被指名的等价性判据**时才成立,理由里必须写 `等价判据=<测试函数名>`,
#      本文件会去核实那条判据真的存在(见 test_spec_whitelist_entries_name_a_real_test)。
GUARDED = {
    PROJECT_ROOT / "08-backtests" / "wallet_skill_v3.py": (
        {"run", "main"},
        {"arm_config": "两臂口径的文档字典,供报告与判据读取",
         "window_of": "规格实现;生产走 WINDOW_SQL。"
                      "等价判据=test_HIGH1_sql_and_python_agree_on_win_edge_and_window",
         "trade_edge": "规格实现;生产走 WIN_SQL+EDGE_SQL。"
                       "等价判据=test_HIGH1_sql_and_python_agree_on_win_edge_and_window"},
    ),
    PROJECT_ROOT / "08-backtests" / "follow_the_leader.py": (
        {"run", "main"},
        {"arm_config": "两臂口径的文档字典,供报告与判据读取",
         "find_fill": "规格实现;生产走 FILL_SQL。"
                      "等价判据=test_sql_and_python_agree_on_fill_selection",
         "scan_signals": "规格实现(纯 Python 版的漏斗与收益口径);生产走 run() 里的 SQL。"
                         "等价判据=test_sql_and_python_agree_on_fill_selection",
         "dedupe_signals": "规格实现;生产的去重由 FILL_SQL 的 GROUP BY 完成。"
                           "等价判据=test_sql_and_python_agree_on_fill_selection"},
    ),
    PROJECT_ROOT / "08-backtests" / "wallet_skill_v4_matched.py": (
        {"run", "main"},
        {"arm_config": "两臂口径的文档字典,供报告与判据读取",
         "cell_scores": "规格实现;生产走 CELL_SCORE_SQL。"
                        "等价判据=test_sql_and_python_agree_on_cell_scores"},
    ),
    PROJECT_ROOT / "06-tools" / "analysis" / "wash_trading_detector.py": (
        {"main", "scan_lake"},
        {"load_excluded_markets": "给下游分析脚本用的消费者入口(设计单 §7)",
         "write_report": "由 main 调用;同时供判据直接使用"},
    ),
    PROJECT_ROOT / "11-collector" / "backfill_market_times.py": (
        {"main"},
        {"hard_firewall": "给下游分析脚本选分界线用的公开工具(设计单 §4)",
         "conservative_firewall": "同上",
         "load_done": "供续跑与外部核查",
         "evaluate_firewall": "由 verify_against_lake 调用,同时供判据直接使用"},
    ),
}


def _reachable(path: Path, entries: set[str]) -> tuple[set[str], set[str]]:
    """返回 (模块里定义的顶层函数, 从入口可达的函数)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    calls: dict[str, set[str]] = {}
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = set()
        for sub in ast.walk(n):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
            # 被当作值传递也算用到(如 monkeypatch/回调)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                names.add(sub.id)
        calls[n.name] = names
    seen: set[str] = set()
    stack = [e for e in entries if e in defined]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(c for c in calls.get(cur, ()) if c in defined and c not in seen)
    return defined, seen


@pytest.mark.parametrize("path", list(GUARDED), ids=lambda p: p.name)
def test_every_function_is_reachable_from_the_production_entry_point(path):
    """⭐定义了却从入口走不到的函数 = 死代码 或 第二份实现。

    白名单必须**逐个写明理由** —— 空着理由的白名单等于把这条判据关掉。
    """
    entries, allow = GUARDED[path]
    defined, seen = _reachable(path, entries)
    orphans = defined - seen - set(allow) - {n for n in defined if n.startswith("_")}
    assert not orphans, (
        f"{path.name} 里这些函数定义了却从 {sorted(entries)} 走不到:{sorted(orphans)}\n"
        f"⇒ 要么是死代码,要么你又写了第二份实现(V3 就是这么栽的)。"
        f"确属公开工具的,加进白名单**并写明理由**。")
    for name, reason in allow.items():
        assert reason.strip(), f"{path.name} 的白名单条目 {name} 没写理由"


@pytest.mark.parametrize("path", list(GUARDED), ids=lambda p: p.name)
def test_spec_whitelist_entries_name_a_real_test(path):
    """⭐「规格实现」这条豁免理由,必须指名一条**真实存在**的等价性判据。

    否则白名单就成了万能借口 —— 谁都可以写句"这是规格"把守卫关掉,
    而生产用的那份实现依旧无人验证(V3 就是这个下场)。
    """
    _, allow = GUARDED[path]
    tests_dir = PROJECT_ROOT / "10-tests"
    all_test_src = "\n".join(f.read_text(encoding="utf-8")
                              for f in tests_dir.rglob("test_*.py"))
    for name, reason in allow.items():
        if "等价判据=" not in reason:
            continue
        tname = reason.split("等价判据=")[1].split()[0].strip().rstrip("。,;")
        assert f"def {tname}(" in all_test_src, (
            f"{path.name} 的白名单条目 {name} 指名了等价判据 {tname},"
            f"但判据库里**根本没有这个测试** —— 豁免不成立")


def test_this_guard_would_have_caught_the_v3_bug(tmp_path):
    """⭐先在**坏数据**上验:构造 V3 修复前的形状,这条判据必须红。

    不做这一步,「没报错」与「守卫本身坏了」看起来完全一样(2026-08-17 教训)。
    """
    bad = tmp_path / "like_v3_before_fix.py"
    bad.write_text(
        "def window_of(ts, closed):\n"
        "    return 'D'\n"
        "def trade_edge(side, oi, ro, price):\n"
        "    return 0.0\n"
        "def run():\n"
        "    # 这里在 SQL 里又写了一份,压根没调用上面两个\n"
        "    return \"CASE WHEN outcome_index <> resolved_outcome THEN 1 ELSE 0 END\"\n",
        encoding="utf-8")
    defined, seen = _reachable(bad, {"run"})
    orphans = defined - seen
    assert orphans == {"window_of", "trade_edge"}, \
        "这条守卫抓不到 V3 那个形状 —— 它本身是坏的"


def test_guard_does_not_false_alarm_on_a_normal_module(tmp_path):
    """反向验:正常调用链不许误报,否则会逼人往白名单里乱塞。"""
    ok = tmp_path / "normal.py"
    ok.write_text(
        "def helper(x):\n    return x + 1\n"
        "def mid(x):\n    return helper(x) * 2\n"
        "def run():\n    return mid(3)\n", encoding="utf-8")
    defined, seen = _reachable(ok, {"run"})
    assert defined - seen == set()
