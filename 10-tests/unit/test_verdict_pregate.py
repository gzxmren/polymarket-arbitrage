"""前置门槛口径修正的验收判据(**先于实现写成**)。

设计单:`docs/DESIGN_VERDICT_PREGATE_2026-09-02.md`(需求 / 设计 / 两轮 review)

由来:2026-09-02 V6 第四次真跑,前置门槛(宽口径)放行、后置门槛(`min(两臂池)`)拒跑
⇒ 白跑 293 秒,**且两臂收益率打到屏幕并完整落进 `results.json`**
(实测 `arms.A.r_v_pct=1.2293` / `null_p95_pct=1.5094` / `boot_q025_pct=-3.8668`)。

⭐本文件钉四件事:
1. **前置门槛必须用与后置逐字相同的口径**(`min(两臂池)`),不是更松的那个;
2. **数据不够时的输出里不许有任何收益率** —— 屏幕和文件都不许;
3. 抽函数**没有改变计算结果**(等价性回归,替代 R5 指出的做不到的全量 diff);
4. 抽出的函数只吃 `con` 里已建好的表,**不碰磁盘**(否则判据喂不进人工数据)。

⚠️ 坏输入用例见文件末尾 `# ---------- 坏输入 ----------`(CLAUDE.md 异常清单第 4 条:
写完一组判据先数有几条是喂真坏输入的,**零 = 没覆盖**)。
"""
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "08-backtests"))
sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "analysis"))

import follow_the_leader as fl  # noqa: E402

MIN_BETS = fl.MIN_BETS_PER_WINDOW          # 20,不在这里重抄一个数
CUT = 0.5                                   # 人工数据里 m1/m4=0.1 进臂B,m2=0.9 不进


# ---------- 人工数据集:期望值是**手算**出来的,不引用被测代码 ----------

def _build_fixture(con):
    """建一份小 `tagged` / `mb`,让四个钱包分别落在四种命运上。

    | 钱包 | 落在哪 | 臂A | 臂B | 为什么 |
    |---|---|---|---|---|
    | wA | m1(leg_imb .1)+ m2(.9) | ✅ | ✅ | 两窗都 ≥20 笔,且同格有 ≥5 笔别人 |
    | wB | 只在 m2(.9) | ✅ | ❌ | 臂B 把 m2 滤掉了 ⇒ 它在臂B 一笔不剩 |
    | wC | 只在 m3(**不在 mb 表里**) | ❌ | ❌ | `JOIN mb` 直接丢掉整个市场 |
    | wD | 只在 m4(.1),同格别人只有 4 笔 | ❌ | ❌ | `MIN_OTHER_TRADES=5` 把这一格滤掉 |

    ⇒ 手算:臂A 池 = [wA, wB],臂B 池 = [wA]。
      而**旧的宽口径**(直接在 tagged 上数 D∩V 各 ≥20)会数出 4 个 —— 差别就在这里。
    """
    rows = []

    def put(w, cid, tag, n, size=10.0, price=0.5):
        for i in range(n):
            rows.append((w, cid, 0, size, price, tag))

    for tag in ("D", "V"):
        put("wA", "m1", tag, MIN_BETS)
        put("wA", "m2", tag, MIN_BETS)
        put("wB", "m2", tag, MIN_BETS)
        put("wC", "m3", tag, MIN_BETS)
        put("wD", "m4", tag, MIN_BETS)
        # 「其他人」:每格 6 笔,足够 >=MIN_OTHER_TRADES(5)
        # ⚠️ zz1 自己不许进池:臂A 下它只有 m1+m2 = 12 笔 < 20
        for cid in ("m1", "m2", "m3"):
            put("zz1", cid, tag, 6, size=7.0, price=0.4)
        # ⚠️ m4 故意只给 4 笔别人(<5)⇒ wD 那一格被 CELL_SCORE_SQL 滤掉
        put("zz2", "m4", tag, 4, size=7.0, price=0.4)

    con.execute("CREATE OR REPLACE TABLE tagged (w VARCHAR, cid VARCHAR, leg INTEGER, "
                "size DOUBLE, price DOUBLE, win_tag VARCHAR)")
    con.executemany("INSERT INTO tagged VALUES (?,?,?,?,?,?)", rows)
    # ⚠️ m3 **故意不在 mb 里** —— 复现生产里 JOIN 造成的损失
    con.execute("CREATE OR REPLACE TABLE mb (cid VARCHAR, leg_imb DOUBLE)")
    con.executemany("INSERT INTO mb VALUES (?,?)",
                    [("m1", 0.1), ("m2", 0.9), ("m4", 0.1)])


@pytest.fixture()
def con():
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    _build_fixture(c)
    yield c
    c.close()


# ---------- V1a:真口径的池 == 手算的池 ----------

def test_V1a_arm_pool_matches_the_hand_computed_answer(con):
    """⭐期望值是**手算写死**的,不是拿被测代码算出来再断言它等于自己(那是恒真式)。"""
    pool_a, _ = fl._arm_pool(con, "A", CUT)
    pool_b, _ = fl._arm_pool(con, "B", CUT)
    assert pool_a == ["wA", "wB"], f"臂A 池不对:{pool_a}"
    assert pool_b == ["wA"], f"臂B 池不对:{pool_b}"


def test_V1a_pool_excludes_markets_missing_from_mb(con):
    """wC 只在 m3,而 m3 不在 `mb` 表里 ⇒ `JOIN mb` 必须把它整个丢掉。"""
    pool_a, _ = fl._arm_pool(con, "A", CUT)
    assert "wC" not in pool_a, "不在 mb 里的市场没有被 JOIN 丢掉"


def test_V1a_pool_excludes_cells_without_enough_other_traders(con):
    """wD 那一格只有 4 笔别人(<MIN_OTHER_TRADES=5)⇒ 该格被滤掉 ⇒ 它笔数不够进池。"""
    pool_a, _ = fl._arm_pool(con, "A", CUT)
    assert "wD" not in pool_a, "同格其他人不足 5 笔的格没有被滤掉"


# ---------- V1b:把「旧口径系统性更松」钉成判据 ----------

def _wide_count(con):
    """旧的宽口径:直接在 tagged 上数 D∩V 各 >=20 —— 这是**出问题的那一版**的算法。"""
    return con.execute(f"""SELECT count(*) FROM (
        SELECT w FROM tagged WHERE win_tag='D' GROUP BY 1 HAVING count(*)>={MIN_BETS}
        INTERSECT
        SELECT w FROM tagged WHERE win_tag='V' GROUP BY 1 HAVING count(*)>={MIN_BETS})""").fetchone()[0]


def test_V1b_wide_prescreen_is_structurally_looser_than_the_real_gate(con):
    """⭐这次事故的机制本身:宽口径**必然**比真口径松,不是偶然撞上。

    手算:宽口径数到 4 个(wA/wB/wC/wD),真口径 min(两臂) = 1(只有 wA)。
    """
    wide = _wide_count(con)
    pool_a, _ = fl._arm_pool(con, "A", CUT)
    pool_b, _ = fl._arm_pool(con, "B", CUT)
    real = min(len(pool_a), len(pool_b))
    assert wide == 4, f"宽口径应数到 4 个,实际 {wide}"
    assert real == 1, f"真口径应是 1 个,实际 {real}"
    assert wide > real, "宽口径没有比真口径松 —— 那这次事故就无从发生,数据造错了"


def test_S6_arm_B_pool_is_a_subset_of_arm_A(con):
    """臂B 的市场范围是臂A 的子集 ⇒ 池必然是子集。日后新增臂或改定义时这条会先红。"""
    pool_a, _ = fl._arm_pool(con, "A", CUT)
    pool_b, _ = fl._arm_pool(con, "B", CUT)
    assert set(pool_b) <= set(pool_a), "臂B 池不再是臂A 池的子集 —— min() 的前提变了"


# ---------- V3′:等价性回归(替代做不到的全量 diff) ----------

def test_V3_extracted_function_equals_the_old_inline_sql(con):
    """⭐抽函数不许改变计算结果。

    对照基准 = 抽走之前那段内联 SQL 的**逐字拷贝**(取自 `follow_the_leader.py` 抽函数前的
    `:454-463`)。它只活在判据里、永远不被生产调用 ⇒ 这是对照基准,不是"抄第二份实现"。

    R5 指出「原版 vs 新版跑同一份数据」在这条线上做不到(数据源写死在 run() 内部、
    数据湖每 15 分钟长大)⇒ 用等价性判据替代,固定数据、只变实现。
    """
    import wallet_skill_v4_matched as v4

    def _old_inline(arm):
        where = "TRUE" if arm == "A" else f"m.leg_imb <= {CUT}"
        con.execute(f"""CREATE OR REPLACE TABLE a AS SELECT t.* FROM tagged t
            JOIN mb m USING (cid) WHERE {where} AND t.win_tag IS NOT NULL""")
        for tag in ("D", "V"):
            con.execute(f"CREATE OR REPLACE TABLE src_{tag} AS SELECT * FROM a WHERE win_tag='{tag}'")
        sc = {t: {r[0]: (r[1], r[2], r[4]) for r in con.execute(
            v4._wallet_scores_sql(f"src_{t}", v4.MIN_OTHER_TRADES)).fetchall()} for t in ("D", "V")}
        return sorted(w for w in sc["D"] if w in sc["V"]
                      and sc["D"][w][2] >= MIN_BETS and sc["V"][w][2] >= MIN_BETS)

    for arm in ("A", "B"):
        want = _old_inline(arm)
        got, _ = fl._arm_pool(con, arm, CUT)
        assert got == want, f"臂{arm}:抽函数后结果变了 {got} != {want}"


def test_V3_the_equivalence_test_would_catch_a_changed_condition(con):
    """⚠️ 上一条若是恒真式就白搭 —— 这里证明它**能**抓到差异:
    把门槛从 20 改成 1,对照基准立刻给出不同的池。"""
    import wallet_skill_v4_matched as v4

    def _old_inline_with(min_bets):
        con.execute("""CREATE OR REPLACE TABLE a AS SELECT t.* FROM tagged t
            JOIN mb m USING (cid) WHERE TRUE AND t.win_tag IS NOT NULL""")
        for tag in ("D", "V"):
            con.execute(f"CREATE OR REPLACE TABLE src_{tag} AS SELECT * FROM a WHERE win_tag='{tag}'")
        sc = {t: {r[0]: (r[1], r[2], r[4]) for r in con.execute(
            v4._wallet_scores_sql(f"src_{t}", v4.MIN_OTHER_TRADES)).fetchall()} for t in ("D", "V")}
        return sorted(w for w in sc["D"] if w in sc["V"]
                      and sc["D"][w][2] >= min_bets and sc["V"][w][2] >= min_bets)

    assert _old_inline_with(MIN_BETS) != _old_inline_with(1), \
        "对照基准对条件变化不敏感 —— 那 V3 那条判据是恒真式"


# ---------- V2a:数据不够时的输出里,一个收益率都不许有 ----------

LEAK_KEYS = ("mean_return_pct", "median_return_pct", "r_v_pct", "null_p95_pct",
             "null_p50_pct", "boot_q025_pct", "boot_q975_pct")


def _leaky_arms():
    """造一份**含完整收益率**的 arms —— 就是今天那次真跑落盘的形状。"""
    return {"A": {"r_v_pct": 1.2293, "null_p95_pct": 1.5094, "boot_q025_pct": -3.8668,
                  "boot_q975_pct": 5.0, "null_p50_pct": 0.1, "n_pool": 7768, "n_top": 776,
                  "n_markets": 1234, "follow_funnel": {"signals": 16898, "filled": 14821},
                  "summary": {"mean_return_pct": 1.2293, "median_return_pct": 0.1001,
                              "n_signals": 12730, "n_dropped_below_floor": 2091, "p_floor": 0.1}},
            "B": {"r_v_pct": -0.639, "null_p95_pct": 0.760, "boot_q025_pct": -6.632,
                  "n_pool": 6613, "n_top": 661,
                  "summary": {"mean_return_pct": -0.639, "median_return_pct": -100.0,
                              "n_signals": 8204}}}


def _walk(obj, path=""):
    """摊平所有层级的 (键路径, 值) —— 收益率藏在 `arms.A.summary.mean_return_pct` 这种第三层。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else k
            yield here, v
            yield from _walk(v, here)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def _keys(obj):
    return {p.rsplit(".", 1)[-1] for p, _ in _walk(obj)}


def _leaked_numbers(obj):
    """⭐泄露的是**数字**,不是键名。

    早退分支按设计要留 `power_gate.null_p95_pct = None` 这个占位键
    (原代码注释:"早退分支也要给出 power_gate 键,否则下游读它直接 KeyError"),
    值为 None 的占位不含任何信息。所以这里只揪**带着实数值**的收益率字段。
    ⚠️ 本判据第一版把"键名出现"就算泄露 —— 写实现时发现它会把那个必需的占位键判红,
    是判据写错了,当场改的(不是为了让实现通过而放松:数值一个都不许有,这条没动)。
    """
    return {p: v for p, v in _walk(obj)
            if p.rsplit(".", 1)[-1] in LEAK_KEYS and v is not None}


def test_V2a_blocked_result_strips_every_return_number():
    """⭐核心需求判据:数据不够时,输出里**一个收益率键都不许剩**。

    ⚠️ 递归查所有层级 —— 收益率藏在 `arms.A.summary.mean_return_pct` 这种第三层。
    """
    out = fl._blocked_result({"arms": _leaky_arms(), "funnel": {"raw_buy": 19640844}},
                             {"allowed": False, "missing": ["合格钱包 6,613 < 8,000"]})
    leaked = _leaked_numbers(out)
    assert not leaked, f"数据不够却泄露了收益率:{sorted(leaked)}"


def test_V2a_blocked_result_keeps_the_sample_sizes():
    """⭐但样本量必须留着 —— 否则没人知道差多少,拒绝信息等于没有。"""
    out = fl._blocked_result({"arms": _leaky_arms(), "funnel": {"raw_buy": 19640844}},
                             {"allowed": False, "missing": ["合格钱包 6,613 < 8,000"]})
    keys = _keys(out)
    assert "n_pool" in keys and "n_signals" in keys, "样本量被一起抹掉了,拒绝信息没用了"
    assert out["verdict"] == "NOT_YET_ENOUGH_DATA"
    assert "6,613" in out["reading"], "拒绝理由里没写实际值"
    assert "power_gate" in out, "早退分支没给 power_gate 键,下游会 KeyError"


def test_V2a_uses_an_allowlist_not_a_denylist():
    """⭐白名单 vs 黑名单:将来新增一个收益率字段时,黑名单会**静默泄露**。

    喂一个判据里没列出的新字段名,它必须也被挡掉。
    """
    arms = _leaky_arms()
    arms["A"]["sharpe_ratio_pct"] = 9.99          # 未来某天新增的字段
    arms["A"]["summary"]["trimmed_mean_pct"] = 8.88
    out = fl._blocked_result({"arms": arms}, {"allowed": False, "missing": ["x"]})
    keys = _keys(out)
    assert "sharpe_ratio_pct" not in keys and "trimmed_mean_pct" not in keys, \
        "新增字段漏过去了 —— 说明用的是黑名单,将来一定会静默泄露"


# ---------- V2b:结构 —— 生产路径真的调用了它 ----------

def test_V2b_run_uses_blocked_result_on_both_gates():
    """两处早退都必须走同一个构造函数,不许各写一份。

    结构检查:守的不变量是「`run()` 里每一条早退路径都经过 `_blocked_result`,
    且前置门槛用的是 `_arm_pool` 的真口径」。这次事故的成因正是**两处各写一份口径**,
    所以"有没有接上同一个函数"本身就是要守的结构。
    ⚠️ 它的弱点写在明处:「函数被调用了」≠「它的返回值控制了流程」
    (见 [[lesson-tests-that-dont-run-production-path]],机械守卫只拦下 2/6 次)。
    这一层由 `test_V2c_*`(端到端、标 slow)补,不是靠这条。
    """
    import inspect
    src = inspect.getsource(fl.run)
    assert src.count("_blocked_result(") >= 2, \
        f"run() 里只有 {src.count('_blocked_result(')} 处调用 —— 两处早退没有都接上"
    assert "_arm_pool(" in src, "run() 没有调用 _arm_pool —— 前置门槛还在用旧的宽口径"


def test_V2b_arm_pool_takes_no_disk_path():
    """⭐抽出的函数只吃 con 里已建好的表(S4)。一旦它自己去读 parquet,人工数据就喂不进来。

    结构检查:守的不变量是「`_arm_pool` 只吃 `con` 里已建好的表,自己不碰磁盘」——
    它一旦自己去 `read_parquet`,人工数据集就喂不进去,V1a/V1b/V3′ 四条判据会全部失去意义
    (设计单 §四 S4)。所以这是判据体系能否成立的**前置结构**,不只是风格偏好。

    ⚠️ 本判据第一版是**文本检查**(`"PROJECT_ROOT" not in src`),当场被自己的 docstring
    误伤 —— 那句话恰恰是"不该读 PROJECT_ROOT"。这是本项目「文本检查冒充行为检查」的
    第 4 次(见 [[lesson-no-friction-for-duplication]])。改成查 **AST 里真实的代码**:
    注释不进 AST,docstring 显式剔除 ⇒ 查的是"代码有没有用它",不是"文件里有没有这几个字"。
    下面 `..._would_catch_a_real_violation` 用一个真的碰磁盘的实现验证它会红。
    """
    import ast
    import inspect
    import textwrap
    sig = set(inspect.signature(fl._arm_pool).parameters)
    assert sig == {"con", "arm", "cut"}, f"_arm_pool 的签名变了:{sorted(sig)}"
    fn = ast.parse(textwrap.dedent(inspect.getsource(fl._arm_pool))).body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    nodes = [n for stmt in body for n in ast.walk(stmt)]
    used = ({n.id for n in nodes if isinstance(n, ast.Name)}
            | {n.attr for n in nodes if isinstance(n, ast.Attribute)})
    banned = {"read_parquet", "PROJECT_ROOT", "open", "Path", "duckdb", "connect"}
    assert not (used & banned), f"_arm_pool 用到了磁盘相关的名字:{sorted(used & banned)}"
    sql = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("read_parquet" in s for s in sql), "拼出来的 SQL 里出现了 read_parquet"


def test_V2b_the_disk_guard_would_catch_a_real_violation():
    """⚠️ 上一条若永远绿就白搭 —— 用一个真的碰磁盘的函数验证它会红。"""
    import ast
    import textwrap
    bad_src = textwrap.dedent('''
        def _arm_pool(con, arm, cut):
            """假的。"""
            con.execute(f"CREATE TABLE a AS SELECT * FROM read_parquet('{PROJECT_ROOT}/x')")
            return [], {}
    ''')
    fn = ast.parse(bad_src).body[0]
    body = fn.body[1:]
    nodes = [n for stmt in body for n in ast.walk(stmt)]
    used = ({n.id for n in nodes if isinstance(n, ast.Name)}
            | {n.attr for n in nodes if isinstance(n, ast.Attribute)})
    sql = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert (used & {"PROJECT_ROOT"}) or any("read_parquet" in s for s in sql), \
        "守卫对真正碰磁盘的实现也没反应 —— 那它是恒真式"


# ---------- 坏输入 ----------
# CLAUDE.md 异常清单第 4 条:写完一组判据先数这里有几条 —— 下面 5 条,不是 0 条。

def test_bad_input_unknown_arm_must_raise_not_silently_mean_arm_B(con):
    """⭐类型/取值错:`where = "TRUE" if arm=="A" else ...` ⇒ **任何非 A 都会被当成臂B**。

    传 "C" 必须炸,不许静默返回一个"看起来正常"的臂B 池 —— 那是本项目最典型的静默失败形状。
    """
    with pytest.raises((ValueError, KeyError, AssertionError)):
        fl._arm_pool(con, "C", CUT)


@pytest.mark.parametrize("bad_cut", ["0.5", "0.5 OR TRUE", True, None])
def test_bad_input_cut_of_wrong_type_is_rejected(con, bad_cut):
    """⭐`cut` 被 f-string 直接拼进 SQL,类型不对就是一句"能跑但意思全变"的 SQL。

    ⚠️ 本判据第一版只喂注入串 `"0.5 OR TRUE"` —— **变异实测(2026-09-02)证明它是空的**:
    把 `_arm_pool` 里的显式类型校验整段删掉,这条判据**照样绿**,
    因为 `float("0.5 OR TRUE")` 顺手抛了 ValueError。
    ⇒ 它通过的原因不是"校验在",而是"别处碰巧也会炸" —— 这就是
    [[lesson-tests-that-dont-run-production-path]] 说的那种绿得没有意义的判据。
    现在补上 `"0.5"` / `True` / `None` 这几个 **`float()` 会接受或语义已错**的输入:
    `float("0.5")=0.5`、`float(True)=1.0` 都不会抛,删掉校验它们就静默通过 ⇒ 变异才抓得到。
    """
    with pytest.raises((TypeError, ValueError)):
        fl._arm_pool(con, "B", bad_cut)


def test_bad_input_arms_is_none_degrades_safely_toward_no_leak():
    """⭐嵌套值类型错:arms 是 None(而不是 dict)。

    断言**安全退化的方向** —— 不是"不崩就行":宁可输出空,也不许把原样结构塞回去。
    """
    out = fl._blocked_result({"arms": None}, {"allowed": False, "missing": ["x"]})
    assert out["verdict"] == "NOT_YET_ENOUGH_DATA"
    assert not _leaked_numbers(out)


def test_bad_input_summary_is_a_string_not_a_dict():
    """⭐嵌套值类型错(第二类):`summary` 本该是 dict,喂个字符串进去。

    ⚠️ 若实现里写 `v["summary"].items()`,这里会抛 AttributeError ⇒ 必须先判类型。
    """
    arms = {"A": {"n_pool": 10, "summary": "mean_return_pct=1.23"}}
    out = fl._blocked_result({"arms": arms}, {"allowed": False, "missing": ["x"]})
    assert "mean_return_pct" not in str(out), "字符串里的收益率被原样带出去了"


def test_bad_input_previous_schema_shape_is_still_stripped():
    """⭐上一版 schema 的**合法旧形状**:V5b 时代的 arms 没有 `summary`,
    收益率直接挂在臂上(`mean_pp` / `edge_pp` 这种老单位名)。

    白名单实现天然挡得住;黑名单实现会漏 —— 这条就是用来分辨这两者的。
    """
    old = {"A": {"n_pool": 10, "mean_pp": 4.16, "edge_pp": 1.2, "null_p95_pp": 4.16}}
    out = fl._blocked_result({"arms": old}, {"allowed": False, "missing": ["x"]})
    keys = _keys(out)
    assert not (keys & {"mean_pp", "edge_pp", "null_p95_pp"}), \
        f"上一版 schema 的收益率字段漏过去了:{sorted(keys & {'mean_pp', 'edge_pp', 'null_p95_pp'})}"


# ---------- V2c:端到端,真走进后置分支(现有判据从没走到过那里) ----------

@pytest.mark.skipif(not os.environ.get("RUN_SLOW_E2E"),
                    reason="要真跑两臂 + 400 次置换(分钟级)。第 6 步手工跑:"
                           "RUN_SLOW_E2E=1 python3 -m pytest 10-tests/unit/test_verdict_pregate.py -k V2c")
def test_V2c_post_gate_early_exit_leaks_nothing_end_to_end(tmp_path, monkeypatch, capsys):
    """⭐真走进**后置门槛**的早退分支,断言屏幕与文件都没有收益率。

    为什么非要它不可(设计单 §四 S3):现有两条 `test_CRITICAL2_*` 的 monkeypatch 是
    **无条件**返回 `allowed=False` ⇒ `verdict_run_allowed` 的第一次调用(前置)就拦下了
    ⇒ **后置那个分支零判据覆盖**,而 2026-09-02 的事故恰好就发生在那里。

    ⚠️ 替身按「第几次调用」作答是本项目踩过的坑(静默失败清单第 11 条:改动让调用次数一变
    就错位,红绿都不可信)。这里改用**语义**区分:前置两道传 `n_fills=None`,后置传实数。
    """
    real = fl.verdict_run_allowed
    seen = {"pre": 0, "post": 0}

    def spy(**kw):
        if kw.get("n_fills") is None:          # 前置(粗筛 + 真口径)一律放行
            seen["pre"] += 1
            return {"allowed": True, "missing": [], "fills_checked": False,
                    "checked": {k: v for k, v in kw.items()}}
        seen["post"] += 1                      # 后置:造一个"跟单数不够"
        return {"allowed": False, "fills_checked": True,
                "missing": ["p>=门槛的成交跟单 8,204 < 10,000"],
                "checked": {"p>=门槛的成交跟单": (8204, 10000)}}

    monkeypatch.setattr(fl, "verdict_run_allowed", spy)
    out = fl.run(out_dir=tmp_path, p_floor=fl.P_FLOOR_FOR_SUBSET,
                 boundary=fl.VERDICT_BOUNDARY, descriptive=False)

    assert seen["post"] > 0, "没走到后置门槛 —— 本判据又没测到点上(S3 说的就是这个)"
    assert out["verdict"] == "NOT_YET_ENOUGH_DATA"

    # ① 返回值里没有实数收益率
    # ⚠️ 报错只列**键路径**,不列数值 —— 2026-09-02 变异验证时这条消息把两臂收益率
    #    原样打了出来,于是判据本身成了泄露渠道。判据的失败信息同样受本单的约束。
    assert not _leaked_numbers(out), f"返回值泄露:{sorted(_leaked_numbers(out))}"
    # ② 落盘的文件里也没有 —— 屏幕会滚走,文件不会,09-02 就是栽在文件上
    disk = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert not _leaked_numbers(disk), f"results.json 泄露:{sorted(_leaked_numbers(disk))}"
    # ③ 屏幕上没有
    printed = capsys.readouterr().out
    for word in ("平均收益率", "中位", "零分布p95", "自举2.5%"):
        assert word not in printed, f"stdout 里出现了「{word}」"
    # ④ 但样本量要留着,否则拒绝信息等于没有
    assert "8,204" in out["reading"], "拒绝理由里没写实际值"
    assert real is not fl.verdict_run_allowed, "替身没装上,这条判据是空过的"


def test_bad_input_leaky_name_with_container_value_is_stripped():
    """⭐第五类坏输入:**键名像收益率、值却是容器**(dict / list)。

    由来:2026-09-02 代码 review(subagent)抓到 —— `_strip_leaks` 第一版的条件是
    「键名匹配 **且值是标量**」才剥,于是 `{"mean_edge_summary": {"raw_value": ...}}`
    被整个放过去,而且 `_stripped_unexpected` 是空的:**漏了还不留痕**,
    两头都断了(记录事实 / 使用事实,本项目犯过 4 次的那个形状)。

    实测复现(改之前):`'mean_edge_summary' in out` 为 True,里面的数值原样躺着。
    ⇒ 这正是 `_blocked_result` 自己文档里说要兜住的场景("将来有人往顶层塞收益率"),
      而兜底那层恰好对它失明。
    """
    out = fl._blocked_result(
        {"arms": {}, "funnel": {"raw_buy": 19640844},
         "mean_edge_summary": {"raw_value": 1.2345, "n": 500},   # 容器值
         "median_by_bucket": [1.1, 2.2, 3.3]},                    # 列表值
        {"allowed": False, "missing": ["合格钱包 6,630 < 8,000"]})
    keys = _keys(out)
    assert "mean_edge_summary" not in keys, "键名像收益率的 dict 被整个放过去了"
    assert "median_by_bucket" not in keys, "键名像收益率的 list 被整个放过去了"
    assert not _leaked_numbers(out), f"泄露:{sorted(_leaked_numbers(out))}"
    # ⭐剥了必须留痕 —— 静默删与静默泄露是同一个病的两面
    assert set(out.get("_stripped_unexpected", [])) >= {"mean_edge_summary", "median_by_bucket"}, \
        f"剥掉了却没留痕:{out.get('_stripped_unexpected')}"
    # 无辜的容器不许误伤
    assert out.get("funnel", {}).get("raw_buy") == 19640844, "把不相干的 funnel 也剥了"


# ---------- 子集不变量的守卫本身要有判据(变异实测:内联版一条判据都够不着) ----------

def test_pool_subset_guard_fires_when_the_invariant_breaks():
    """⭐守卫不许是"写了没人验"的东西。

    2026-09-02 变异实测:这条守卫内联在 `run()` 里时,把它改成 `if False:`,
    **22 条判据一条都没红** —— 典型的「记录事实 vs 使用事实只接了一头」。
    抽成函数之后才够得着。
    """
    fl.check_pool_subset(["a", "b", "c"], ["a", "b"])     # 正常:子集,不该抛
    fl.check_pool_subset([], [])                          # 边界:两个都空
    with pytest.raises(RuntimeError, match="min"):
        fl.check_pool_subset(["a"], ["a", "b"])           # 违反:臂B 比臂A 大


def test_pool_subset_guard_survives_python_O_flag():
    """⭐`python -O` 会把裸 `assert` 整条删掉 —— 守卫必须是真的 `raise`,不是 assert。

    结构检查:守的不变量是「这道焊缝不依赖 assert 语句」。
    直接跑一个 `-O` 子进程来验,不靠读源码有没有 "assert" 这几个字。
    """
    import subprocess
    code = (
        "import sys; sys.path.insert(0, '08-backtests'); sys.path.insert(0, '06-tools/analysis');"
        "import follow_the_leader as fl;"
        "\ntry:\n    fl.check_pool_subset(['a'], ['a','b'])\n    print('NO_RAISE')\n"
        "except RuntimeError:\n    print('RAISED')\n")
    out = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True,
                         text=True, cwd=str(PROJECT_ROOT))
    assert out.stdout.strip() == "RAISED", \
        f"-O 下守卫没触发(用 assert 写的会被优化掉):stdout={out.stdout!r} stderr={out.stderr[-300:]!r}"
