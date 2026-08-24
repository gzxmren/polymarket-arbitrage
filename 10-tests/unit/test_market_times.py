"""时间防火墙回填的验收判据(先于实现写成)。

设计单:docs/DESIGN_TIME_FIREWALL_2026-08-23.md

⭐本文件重点钉三件事(都是本项目栽过跟头的形状):
1. **不许用 `end_date` 当结算时刻** —— 实测它其实是**开赛时刻**(中位差 0 分钟),
   把它当分界线正是"15.7% 成交落在结算之后"这个假象的根源。
2. **不许用价格反推结算时刻** —— CLAUDE.md 明令禁止(与结果相关的标签)。
3. **对账**:请求几个、拿回几个,必须逐批记录且能被判据读到。
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "11-collector"))

import backfill_market_times as bmt  # noqa: E402


# ---------- 实测样本(2026-08-23,抽样 195 个已结算市场,详见设计单 §3)----------
MEASURED_COVERAGE = {"closedTime": 1.000, "gameStartTime": 0.908, "endDate": 1.000}
MEASURED_POST_CLOSED_TRADE_FRACTION = 2 / 22490   # 实测:22,490 笔里只有 2 笔落在结算后
MEASURED_END_TO_GAMESTART_MINUTES = 0          # ⇒ end_date 就是开赛时刻


def _api_row(cid, closed_time="2026-08-23 04:02:09.886203+00",
             game_start="2026-08-23 01:30:00+00", end_date="2026-08-23T01:30:00Z", **kw):
    d = {"conditionId": cid, "closedTime": closed_time, "gameStartTime": game_start,
         "endDate": end_date, "umaResolutionStatus": "resolved", "closed": True}
    d.update(kw)
    return d


# ---------- 1. 解析 ----------

def test_parses_both_firewalls():
    r = bmt.parse_market(_api_row("0xaa"))
    assert r["condition_id"] == "0xaa"
    assert r["closed_time"] is not None
    assert r["game_start"] is not None
    assert r["uma_status"] == "resolved"


def test_handles_missing_game_start():
    """非体育盘没有 gameStartTime(实测 9.2%) —— 缺它不许崩,也不许伪造。"""
    r = bmt.parse_market(_api_row("0xbb", game_start=None))
    assert r["game_start"] is None
    assert r["closed_time"] is not None


@pytest.mark.parametrize("bad", ["", "not-a-time", None, "0000-00-00"])
def test_unparseable_timestamps_become_none_not_garbage(bad):
    """坏时间戳必须变成 None 并被计数,不许悄悄变成 1970 或今天。"""
    r = bmt.parse_market(_api_row("0xcc", closed_time=bad))
    assert r["closed_time"] is None


def test_closed_time_is_the_hard_firewall_not_end_date():
    """⭐实测:endDate 与 gameStartTime 中位差 0 分钟 ⇒ endDate 是**开赛**时刻。

    这条判据钉死:硬防火墙取的是 closedTime,任何改回 end_date 的改动必须变红。
    """
    r = bmt.parse_market(_api_row("0xdd",
                                  closed_time="2026-08-23 04:02:09+00",
                                  game_start="2026-08-23 01:30:00+00",
                                  end_date="2026-08-23T01:30:00Z"))
    assert bmt.hard_firewall(r) == r["closed_time"]
    assert bmt.hard_firewall(r) != r["end_date"], "硬防火墙取成了 end_date(=开赛时刻)"
    assert bmt.conservative_firewall(r) == r["game_start"]


def test_no_price_derived_firewall_anywhere():
    """⭐CLAUDE.md 明令:不许用「价格是否收敛到 0/1」判断是否已结算。

    这条判据在源码层面钉死 —— 它是我 2026-08-23 亲手提出又撤回的错误做法。
    """
    import inspect
    src = inspect.getsource(bmt)
    for banned in ("outcomePrices", "bestAsk", "bestBid", "lastTradePrice"):
        assert banned not in src, f"源码里出现了价格字段 {banned} —— 防火墙不许由价格推导"


# ---------- 2. 对账(请求数 vs 返回数) ----------

def test_reconciliation_is_recorded_per_batch():
    """请求 5 个只回 3 个时,缺的 2 个必须被记下来且**点名**,不许只记个数。"""
    st = bmt.ReconcileLedger()
    st.record(requested=["a", "b", "c", "d", "e"], returned=["a", "b", "c"])
    assert st.requested == 5 and st.returned == 3
    assert st.missing == {"d", "e"}, "缺了哪几个必须点名 —— 只记个数答不了'丢的是哪一类'"


def test_reconciliation_flags_extra_returns():
    """回来的比请求的多 = 接口串行了,同样是异常,不许当成好事默默收下。"""
    st = bmt.ReconcileLedger()
    st.record(requested=["a"], returned=["a", "zzz"])
    assert st.unexpected == {"zzz"}


# ---------- 3. 断点续跑 ----------

def test_resume_skips_already_fetched(tmp_path):
    """已完成的不许重复请求(16 万市场 × 两遍请求,重复=几小时白跑)。"""
    out = tmp_path / "t.parquet"
    bmt.write_rows([bmt.parse_market(_api_row("0x01")), bmt.parse_market(_api_row("0x02"))], out)
    done = bmt.load_done(tmp_path)
    assert done == {"0x01", "0x02"}
    todo = bmt.select_todo(["0x01", "0x02", "0x03"], done)
    assert todo == ["0x03"]


def test_load_done_on_empty_dir_returns_empty_set(tmp_path):
    assert bmt.load_done(tmp_path) == set()


# ---------- 4. 批量 URL(复用采集器已验证的两遍查法) ----------

def test_batch_url_queries_closed_twice():
    """⚠️ Gamma 默认只回未关闭的市场(项目已知坑,踩过两次) ⇒ 必须查两遍取并集。"""
    urls = bmt.batch_urls(["0x01", "0x02"])
    assert len(urls) == 2
    assert any("closed=true" in u for u in urls)
    assert any("closed=true" not in u for u in urls)
    for u in urls:
        assert "limit=" in u, "不给 limit 会被静默截断到 20 条(实测)"


def test_batch_size_respects_url_budget():
    """URL 硬限 8192 字节,110 个 cid 就 422(实测)。"""
    big = [f"0x{i:064x}" for i in range(200)]
    for chunk in bmt.chunk_cids(big):
        for u in bmt.batch_urls(chunk):
            assert len(u.encode()) <= 8192


# ---------- 5. 红线接到判决上 ----------

def test_firewall_effectiveness_redline_is_computed():
    """§5 红线 2:落在 closedTime 之后的成交占比 < 0.1%。必须是**算出来**的。"""
    v = bmt.evaluate_firewall(trades_total=22490, trades_after_closed=2)
    assert v["fraction_after"] == pytest.approx(2 / 22490)
    assert v["passes"] is True


def test_firewall_redline_goes_red_on_bad_data():
    """⭐先在坏数据上验:泄漏 5% 时必须判红,否则这条红线是摆设。"""
    v = bmt.evaluate_firewall(trades_total=1000, trades_after_closed=50)
    assert v["passes"] is False


def test_firewall_redline_handles_zero_trades():
    v = bmt.evaluate_firewall(trades_total=0, trades_after_closed=0)
    assert v["fraction_after"] == 0.0 and v["passes"] is True


# ---------- 6. 断点续跑的**写入端**(变异测试之外,实跑时发现的缺口) ----------

def test_long_run_flushes_incrementally_not_only_at_the_end(tmp_path, monkeypatch):
    """⭐原版只在全部跑完时写一次盘 ⇒ 2 小时的任务被**硬杀**(OOM/断电)就全丢,
    load_done 无从可续 —— 「记录事实 vs 使用事实,只接了一头」的第 5 次。

    ⚠️ 本判据第一版是错的:它在 KeyboardInterrupt 之后查磁盘,而 `finally: flush()`
    会兜住软性中断 ⇒ **去掉中途落盘它照样全绿**(2026-08-23 变异实测)。
    硬杀时 `finally` 根本不会执行,所以必须在**循环进行中**查磁盘,不能在结束后查。
    """
    cids = [f"0x{i:064x}" for i in range(400)]
    seen_on_disk = []

    def fake_fetch(batch, ledger, counters=None, deadline=None):
        # 每批开始前记录"此刻磁盘上已有多少" —— 这是唯一能区分
        # 「中途落盘」与「只在 finally 落盘」的观察点
        seen_on_disk.append(len(bmt.load_done(tmp_path)))
        ledger.record(batch, batch)
        return [bmt.parse_market(_api_row(c)) for c in batch], []

    monkeypatch.setattr(bmt, "fetch_batch", fake_fetch)
    monkeypatch.setattr(bmt, "settled_cids", lambda limit=None: cids)
    bmt.main(["--out", str(tmp_path), "--sleep", "0", "--flush-every", "1"])

    assert len(seen_on_disk) >= 3, "批数太少,这条判据没被真正执行到"
    assert max(seen_on_disk) > 0, (
        "跑到最后一批时磁盘上仍然一条都没有 —— 说明只在结束时落盘,"
        "进程被硬杀时几小时的请求会全部白费")
    assert seen_on_disk[-1] >= seen_on_disk[len(seen_on_disk) // 2], "落盘量没有随进度增长"


def test_resume_after_flush_skips_completed_work(tmp_path, monkeypatch):
    """落盘之后再跑,已完成的必须被跳过(否则续跑=重复几千次请求)。"""
    cids = [f"0x{i:064x}" for i in range(200)]
    monkeypatch.setattr(bmt, "fetch_batch",
                        lambda b, l, counters=None, deadline=None:
                        (l.record(b, b), [bmt.parse_market(_api_row(c)) for c in b], [])[1:])
    monkeypatch.setattr(bmt, "settled_cids", lambda limit=None: cids)
    bmt.main(["--out", str(tmp_path), "--sleep", "0", "--flush-every", "1"])
    done = bmt.load_done(tmp_path)
    assert len(done) == len(cids)
    assert bmt.select_todo(cids, done) == []


# =====================================================================
# 7. 2026-08-23 python-reviewer 判 Block 的 10 条,逐条钉住
# =====================================================================

def test_H1_firewall_redline_is_actually_executed_by_main(tmp_path, monkeypatch):
    """⭐H1:`evaluate_firewall` 曾经**只被单元测试用假数字调过**,生产代码一次没调,
    而它的 docstring 却写着「接到判决上(不是文档里写写)」—— 那句话本身就是文档里写写。

    ⚠️ 本判据第一版是**文本检查冒充行为检查**:只断言源码里有 `verify_against_lake`
    这个词。变异实测把调用处改成 `if False:` —— 词还在,判据照过(2026-08-23)。
    现在改成真调 main、看那个函数有没有被执行到。
    """
    called = {"n": 0}

    def fake_verify(out_dir, sample_markets=3000):
        called["n"] += 1
        return dict(bmt.evaluate_firewall(10000, 1), sampled_markets=10000)

    monkeypatch.setattr(bmt, "verify_against_lake", fake_verify)
    monkeypatch.setattr(bmt, "settled_cids", lambda limit=None: [f"0x{i:064x}" for i in range(20)])
    monkeypatch.setattr(bmt, "fetch_batch",
                        lambda b, l, counters=None, deadline=None:
                        (l.record(b, b), [bmt.parse_market(_api_row(c)) for c in b], [])[1:])
    bmt.main(["--out", str(tmp_path), "--sleep", "0"])
    assert called["n"] == 1, "main 跑完了却没执行红线实跑 —— 红线又变成摆设了"
    summary = json.loads((tmp_path / "last_run_summary.json").read_text(encoding="utf-8"))
    assert "firewall_check" in summary, "红线结果没被写进汇总,产物里看不到判决"
    assert summary["firewall_check"]["passes"] is True


def test_H2_a_bad_batch_does_not_kill_the_whole_run(tmp_path, monkeypatch):
    """⭐H2:落盘抛异常时,**整次运行不许陪葬**,更不许打翻 `finally` 的保底落盘。

    变异实测(2026-08-23):把 flush 里的 `except Exception` 换窄,32 条判据一条不红 ——
    说明当时根本没有判据覆盖"落盘会抛异常"这条路径。
    """
    boom = {"n": 0}
    real_write = bmt.write_rows

    def flaky_write(rows, path):
        boom["n"] += 1
        if boom["n"] == 1:
            raise TypeError("模拟上游字段类型漂移导致的 ArrowTypeError")
        return real_write(rows, path)

    monkeypatch.setattr(bmt, "write_rows", flaky_write)
    monkeypatch.setattr(bmt, "verify_against_lake", lambda *a, **k: bmt.evaluate_firewall(10, 0))
    monkeypatch.setattr(bmt, "settled_cids", lambda limit=None: [f"0x{i:064x}" for i in range(200)])
    monkeypatch.setattr(bmt, "fetch_batch",
                        lambda b, l, counters=None, deadline=None:
                        (l.record(b, b), [bmt.parse_market(_api_row(c)) for c in b], [])[1:])
    rc = bmt.main(["--out", str(tmp_path), "--sleep", "0", "--flush-every", "1"])
    assert rc == 0, "一批坏数据把整次运行打死了"
    assert boom["n"] >= 2, "第一批抛异常后没有继续跑下去"
    assert len(bmt.load_done(tmp_path)) > 0, "后续批次也没能落盘 —— 兜底被打翻了"


def test_H2_nonstring_fields_are_sanitized_before_parquet():
    """⭐H2:reviewer 用真实坏输入复现过 —— `uma_status` 原样透传,
    上游把它变成 dict 时 pyarrow 抛 ArrowTypeError,整批陪葬,且连 finally 兜底一起打翻。
    """
    row = bmt.parse_market(_api_row("0x" + "a" * 64, umaResolutionStatus={"weird": 1}))
    assert bmt._as_str(row["uma_status"]) is not None
    assert isinstance(bmt._as_str(row["uma_status"]), str)


def test_H2_write_rows_survives_malformed_field(tmp_path):
    """造真的坏输入跑一遍(CLAUDE.md 异常清单第 4 条),不是内存里的假对象。"""
    rows = [bmt.parse_market(_api_row("0x" + "b" * 64, umaResolutionStatus=["a", "b"])),
            bmt.parse_market(_api_row("0x" + "c" * 64, umaResolutionStatus=42))]
    p = tmp_path / "x.parquet"
    bmt.write_rows(rows, p)                    # 不许抛
    assert bmt.load_done(tmp_path) == {"0x" + "b" * 64, "0x" + "c" * 64}


def test_H3_records_with_unparseable_closed_time_are_reported_not_hidden(tmp_path):
    """⭐H3:「拿到记录但 closed_time 解析失败」不许被 done 两个字盖过去。

    后果:上游换个时间写法 → 解析失败 → cid 有值 → 被标 done → **续跑永久跳过它**,
    而对账层完全干净看不出异常。
    """
    good = bmt.parse_market(_api_row("0x" + "1" * 64))
    bad = bmt.parse_market(_api_row("0x" + "2" * 64, closed_time="某种没见过的写法"))
    bmt.write_rows([good, bad], tmp_path / "s.parquet")
    seen, null_closed = bmt.scan_shards(tmp_path)
    assert seen == {"0x" + "1" * 64, "0x" + "2" * 64}
    assert null_closed == {"0x" + "2" * 64}, "解析失败的没被单独点出来"


def test_H3_later_shard_can_repair_an_earlier_null(tmp_path):
    """同一个 cid 后来补上了可解析的时刻,就不该再算缺。"""
    cid = "0x" + "3" * 64
    bmt.write_rows([bmt.parse_market(_api_row(cid, closed_time="坏的"))], tmp_path / "a.parquet")
    bmt.write_rows([bmt.parse_market(_api_row(cid))], tmp_path / "b.parquet")
    assert bmt.scan_shards(tmp_path)[1] == set()


def test_H4_chunk_never_exceeds_the_url_limit_parameter():
    """⭐H4:切批必须同时受**字节预算**和 **BATCH_LIMIT** 两个上限约束。

    只挡字节数时,谁把预算调大就会切出超过 `limit=` 的批 ⇒ 上游**静默截断**,
    多出来的只显示成"缺失",看起来跟普通丢包一样,不会有信号指向"limit 配错了"。
    """
    big = [f"0x{i:064x}" for i in range(3000)]
    for chunk in bmt.chunk_cids(big, budget=10**9):     # 故意把字节预算放到无限大
        assert len(chunk) <= bmt.BATCH_LIMIT, "字节预算放开后切批突破了 limit"


def test_M1_measured_constants_are_actually_asserted():
    """⭐M1:判据文件里的 MEASURED_* 常量原先是**纯装饰**,一处 assert 都没用到 ——
    看起来像"被实测数据锚定",实际上删掉它们行为毫无变化。现在接上。
    """
    v = bmt.evaluate_firewall(trades_total=22490, trades_after_closed=2)
    assert v["fraction_after"] == pytest.approx(MEASURED_POST_CLOSED_TRADE_FRACTION)
    assert MEASURED_COVERAGE["closedTime"] == 1.0
    assert MEASURED_END_TO_GAMESTART_MINUTES == 0


def test_M2_parse_market_reads_only_whitelisted_fields():
    """⭐M2:原判据是 4 个词的**黑名单**,任何没列到的价格字段都能溜过去。

    改成白名单:parse_market 只许读这 5 个字段,新增任何字段都会让判据变红,
    与设计单 §2「不许用与结果相关的变量」这条**原则性**红线覆盖面对等。
    """
    import inspect
    import re
    src = inspect.getsource(bmt.parse_market)
    read = set(re.findall(r'm\.get\("(\w+)"', src))
    allowed = {"conditionId", "closedTime", "gameStartTime", "endDate", "umaResolutionStatus"}
    assert read <= allowed, f"parse_market 读了白名单外的字段: {read - allowed}"


def test_M3_missing_gaps_carry_a_reason():
    """⭐M3:缺口不能只答"谁丢了",还要答"为什么丢" ——
    设计单 §5 红线 1 要求归因,光点名答不了"是不是又踩到与结果相关的过滤器"。
    """
    lg = bmt.ReconcileLedger()
    lg.record(requested=["a", "b"], returned=["a"], failures=["retry_exhausted"])
    assert lg.missing == {"b"}
    assert "retry_exhausted" in str(lg.missing_reasons)
    lg2 = bmt.ReconcileLedger()
    lg2.record(requested=["a", "b"], returned=["a"], failures=[])
    assert lg2.missing_reasons, "网络没失败但东西没回来,也必须给出一个说法"


def test_M4_importing_this_module_does_not_patch_process_dns():
    """⭐M4:`discovery_service` 在**导入时**就全局改 socket.getaddrinfo(强制 IPv4)。
    只为拿一个 UA 常量就把整个进程(以及每次 pytest 会话)的 DNS 行为改掉是隐形副作用。

    结构检查:模块级不许出现 socket.getaddrinfo 赋值,且 main() 里必须触发延迟导入 ——
    守的是「进程级 DNS 副作用只能发生在运行时」这条结构不变量,不是在验行为。
    """
    import ast
    import inspect
    # ⚠️ 第一版用「源码里有没有 getaddrinfo 这个词」来判,结果匹配到了**解释为什么不这么做
    #    的注释**,判据当场变红 —— 又一次"我自己的查法坏了"。改用语法树精确判断。
    tree = ast.parse(inspect.getsource(bmt))
    for node in tree.body:                      # 只看模块级语句(import 时会执行的)
        # ⚠️ 第二版也错了:`ast.walk` 会**钻进函数体**,于是它找到了 main() 里那次赋值 ——
        #    而那恰恰是我们**要求**它待的地方。必须先排除函数/类体。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr == "getaddrinfo" and \
               isinstance(sub.ctx, ast.Store):
                raise AssertionError("模块导入时就改了 DNS 解析 —— 会污染整个进程/每次 pytest 会话")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
            assert "discovery_service" not in names, "又把带 DNS 副作用的模块 import 进来了"
    # IPv4 强制现在由**延迟导入**的 discovery_service 负责(见 bmt._ds 的说明),
    # 所以断言"main 里会触发那次延迟导入",而不是断言 main 自己改 DNS。
    assert "_ds()" in inspect.getsource(bmt.main), "运行时没有触发延迟导入,IPv4 强制会丢"
    import socket
    before = socket.getaddrinfo
    import importlib
    importlib.reload(bmt)                     # 重新导入本模块
    assert socket.getaddrinfo is before, "重新导入本模块改变了进程级 DNS 行为"


def test_M5_parquet_write_is_atomic(tmp_path):
    """⭐M5:直接写最终文件名时,写一半被硬杀会留下损坏分片。必须临时文件 + replace。

    结构检查:源码里必须出现 os.replace —— 守的是「写入必须原子」这条结构不变量;
    下面另有真写一次的行为断言配合。
    """
    import inspect
    src = inspect.getsource(bmt.write_rows)
    assert "os.replace" in src, "不是原子写"
    p = tmp_path / "y.parquet"
    bmt.write_rows([bmt.parse_market(_api_row("0x" + "d" * 64))], p)
    assert p.exists()
    assert not list(tmp_path.glob("*.tmp")), "临时文件没清掉"


def test_M6_invalid_condition_ids_are_rejected_and_counted():
    """⭐M6:脏 cid 直接拼进 URL 会**无声破坏整条查询字符串**,而报错不会指向它。
    选择校验+计数,而不是转义 —— 转义只会把数据质量问题藏起来。
    """
    good, bad = bmt.split_valid_cids(
        ["0x" + "e" * 64, "0xshort", "0x" + "f" * 64 + "&closed=true", "", None, 123])
    assert good == ["0x" + "e" * 64]
    assert len(bad) == 5


def test_M6_second_instance_refuses_to_run(tmp_path, monkeypatch):
    """⭐M6:两个进程同时跑会各自读到旧的 done 快照、重复请求几千次。"""
    monkeypatch.setattr(bmt, "settled_cids", lambda limit=None: [])
    (tmp_path / ".backfill.lock").write_text("99999")
    assert bmt.main(["--out", str(tmp_path), "--skip-verify"]) == 2


def test_M7_batch_url_is_delegated_not_hand_rolled():
    """⭐本文件第一版自己拼了 `condition_ids=`,被项目已有的结构守卫
    (`test_gamma_batch_lookup.py::test_nobody_else_hand_rolls_the_batch_url`)当场抓住 ——
    那是「照抄结构而不抽象」的第 4 次。

    结构检查:本模块不许自己拼 condition_ids=,且必须委托给唯一的那一处 ——
    守的是「URL 拼接全项目只此一份」这条结构不变量。
    """
    import inspect
    import re
    src = inspect.getsource(bmt)
    assert not re.search(r"""["']condition_ids=(\{|%s)""", src), "又自己拼 URL 了"
    assert "build_gamma_batch_url" in inspect.getsource(bmt.batch_urls)
    assert "pack_condition_ids" in inspect.getsource(bmt.chunk_cids)
