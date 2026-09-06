#!/usr/bin/env python3
"""判据焊死:Gamma 批量查 —— 按 URL 字节打包、两遍必查、「没问到」与「答案是没有」分得开(2026-08-16)。

## 由来(实测,非假想)

注册层逐个查 slug,实测 **950~2350 ms/个**,180 秒时间闸下只做得了 76~100 个;
而**同一份代码库里**的结算守望早就在用 `condition_ids=` 批量查,实测 800 个/22 秒。
一个模块学会了批量,另一个从来没拿到这个教训 —— 项目「重复形状清单」第 2 条
(照抄结构而不抽象)的反面:**该抄的没抄**。

后果不是"慢一点":2026-08-13 起涌入量涨到 ~350/轮而预算 100/轮,
积压顶死 `MAX_BACKLOG=2000` 并每轮丢弃 82~169 个;在成交的市场里
因为没登记而采不到的比例从 8.5% 涨到 **35.7%**。见
`test_registration_backlog.py::test_capacity_cap_must_not_starve_anyone_at_the_measured_arrival_rate`。

## 实测的三个硬事实(本判据把它们焊住)

1. **URL 有 8192 字节硬限**。实测:100 个 cid = 8150 字节 → 200;
   110 个 = 8960 字节 → **HTTP 422**;120/130/150 同样 422。
   ⚠️ 结算守望现行写死 `100 个/批`,即**距离 422 只剩 42 字节** ——
   谁再加一个查询参数,整条链路全线 422。故打包按**字节**算,不按个数。
2. **必须查两遍**。实测请求 100 个:默认只回 52(未关闭),
   `&closed=true` 回 48(已关闭),并集才凑齐 100。
   只查一遍 = 静默丢掉全部已结算样本 = **与结果相关地丢样本**(2026-08-03 那场事故)。
3. **批量与逐个查产出的注册表行逐字段一致**(实测 8/8,见回归判据组)。

## 本判据**不能**回答什么

- 不回答"批量之后一轮该注册多少个"。那是 `test_registration_backlog.py` 里
  用实测到达率焊的另一条,自变量不同。
- 不回答 Gamma 会不会哪天把上限从 8192 改掉。判据只保证**我们这边永不越界**,
  以及越界时是响亮的 422 而不是静默截断。
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import discovery_service as ds  # noqa: E402


# 实测边界(2026-08-16,对生产 Gamma 实打实发的请求):
#   100 个 → URL 8150 字节 → 200 OK
#   110 个 → URL 8960 字节 → 422
MEASURED_OK_BATCH = 100
MEASURED_OK_URL_BYTES = 8150
MEASURED_REJECTED_BATCH = 110
MEASURED_REJECTED_URL_BYTES = 8960
SERVER_URL_CEILING = 8192          # 8150 过 / 8960 拒 ⇒ 硬限落在这两者之间的 2^13


def _cids(n, start=0):
    return [f"0x{i:064x}" for i in range(start, start + n)]


def _market(cid, closed=False):
    """一个能通过 parse_market 的最小 Gamma 市场对象。"""
    return {"conditionId": cid, "slug": f"s-{cid[-4:]}", "question": "Q?",
            "clobTokenIds": '["1","2"]', "outcomes": '["Yes","No"]',
            "closed": closed,
            "outcomePrices": '["1.0","0.0"]' if closed else None}


# ---------- 判据组 A:按字节打包,永不越界 ----------

def test_packed_urls_never_exceed_the_budget():
    """⭐核心:任何一批拼出来的 URL(含**最长**的那个后缀)都不许超预算。

    按最长后缀算,不按当前这一遍算 —— 否则第一遍刚好卡线、第二遍加上
    `&closed=true` 就越界,而越界的表现是整批 422(= 整批查不到)。
    """
    for n in (1, 7, 99, 100, 101, 500, 2000):
        for batch in ds.pack_condition_ids(_cids(n)):
            longest = max(ds.build_gamma_batch_url(batch, suf)
                          for suf in ds.GAMMA_CLOSED_SUFFIXES)
            assert len(longest) <= ds.GAMMA_URL_BUDGET_BYTES, (
                f"n={n} 时某一批 URL 长 {len(longest)} 字节 > "
                f"预算 {ds.GAMMA_URL_BUDGET_BYTES}")


def test_budget_stays_below_the_measured_server_ceiling():
    """预算必须留在实测硬限之下,并且**留出余量** —— 42 字节不叫余量。"""
    assert ds.GAMMA_URL_BUDGET_BYTES < SERVER_URL_CEILING
    margin = SERVER_URL_CEILING - ds.GAMMA_URL_BUDGET_BYTES
    assert margin >= 512, (
        f"距服务端硬限只剩 {margin} 字节。现行结算守望写死 100 个/批时"
        f"余量是 {SERVER_URL_CEILING - MEASURED_OK_URL_BYTES} 字节,"
        f"加一个查询参数就全线 422 —— 修的就是这个")


def test_the_measured_rejection_boundary_is_welded_down():
    """把实测到的那条线焊住:110 个一定超预算(否则我们会再走回 422)。"""
    url = ds.build_gamma_batch_url(_cids(MEASURED_REJECTED_BATCH), "&closed=true")
    assert len(url) > ds.GAMMA_URL_BUDGET_BYTES
    assert len(url) >= MEASURED_REJECTED_URL_BYTES, (
        "实测 110 个 = 8960 字节;若本断言变红说明 URL 构造方式变了,"
        "上面那些实测数字全部作废,须重新实测")


def test_packing_loses_nobody_and_duplicates_nobody():
    """请求数对账(静默失败清单第 1 条):并集必须**恰好**等于输入,无重无漏。"""
    cids = _cids(1234)
    packed = list(ds.pack_condition_ids(cids))
    flat = [c for b in packed for c in b]
    assert flat == cids, "打包改变了顺序或丢了元素"
    assert len(set(flat)) == len(flat), "打包产生了重复"
    assert all(b for b in packed), "产生了空批(会白发一次请求)"


def test_empty_input_issues_no_request():
    assert list(ds.pack_condition_ids([])) == []


def test_limit_param_is_load_bearing_not_decorative():
    """🔴 `limit=` 必须 ≥ 单批最大条数,否则接口静默分页截断。

    实测(2026-08-16):同样请求 85 个 cid,**不给 limit 只回 20 个**,给 limit=500 回全。
    即"数量看着差不多、其实丢了四分之三",而且丢的那批一声不响 ——
    正是静默失败清单第 1 条的原形。2026-08-16 的 code review 曾建议把这个常量
    当"虚高摆设"清理掉,实测证明方向是反的,故焊成判据。
    """
    biggest = max(len(b) for b in ds.pack_condition_ids(_cids(2000)))
    assert ds.GAMMA_BATCH_LIMIT >= biggest, (
        f"limit={ds.GAMMA_BATCH_LIMIT} 小于单批最大条数 {biggest} ⇒ 接口会静默截断")
    assert "limit=" in ds.build_gamma_batch_url(_cids(3)), "URL 里没带 limit ⇒ 默认只回 20 个"


def test_a_single_cid_can_never_exceed_the_budget():
    """打包时"第一个元素无条件收下"这一步,靠的是**单个 cid 一定装得下**。

    把这个前提焊下来,而不是加一段永远跑不到的防御代码:
    合法 condition_id 定长 66 字符(`is_parlay_cid` 在更早的 `extract_active` 就按
    这个正则把畸形的剔掉了),单元素 URL 只有约 150 字节,离预算 7000 差着两个数量级。
    若哪天 cid 变长或过滤放宽,本条先红 —— 那时再决定怎么处置,而不是现在猜。
    """
    one = ds.build_gamma_batch_url(_cids(1), max(ds.GAMMA_CLOSED_SUFFIXES, key=len))
    assert len(one) < ds.GAMMA_URL_BUDGET_BYTES / 10, (
        f"单个 cid 的 URL 已经 {len(one)} 字节,离预算 {ds.GAMMA_URL_BUDGET_BYTES} 不再遥远 —— "
        f"「第一个元素无条件收下」这个前提要重新审")
    assert ds.is_parlay_cid("0x" + "ab" * 40), "过滤放宽了:超长 cid 现在能走到打包层"


# ---------- 判据组 B:两遍必查(2026-08-03 那场事故的红线) ----------

def _fake_gamma(open_markets=(), closed_markets=(),
                fail_default=False, fail_closed=False, urls=None):
    """假 Gamma:**按 URL 里问了谁**作答,像真接口那样。

    ⚠️ 不按"第几次调用"作答 —— 打包批数一变(正是本次改动会变的东西),
    按调用序预置的假接口就会错位,而错位的表现是判据红/绿都不可信。
    判据自己也会静默坏掉,这一条本轮已经真的踩到一次。
    """
    om = {m["conditionId"]: m for m in open_markets}
    cm = {m["conditionId"]: m for m in closed_markets}

    def _g(url, **kw):
        if urls is not None:
            urls.append(url)
        want_closed = "closed=true" in url
        if (fail_closed and want_closed) or (fail_default and not want_closed):
            return {"__http__": 500}
        src = cm if want_closed else om
        asked = re.findall(r"condition_ids=(0x[0-9a-f]+)", url)
        return [src[c] for c in asked if c in src]
    return _g


def test_both_passes_are_issued(monkeypatch):
    """默认那遍 + `&closed=true` 那遍,每一批都不许少查任何一遍。"""
    urls = []
    cids = _cids(300)          # 故意跨多批,顺带焊住"每批都两遍"
    monkeypatch.setattr(ds, "_get", _fake_gamma(urls=urls))
    ds.batch_lookup_gamma(cids, net={})
    n_batches = len(list(ds.pack_condition_ids(cids)))
    assert len(urls) == 2 * n_batches, f"{n_batches} 批却只发了 {len(urls)} 次请求"
    assert sum("closed=true" in u for u in urls) == n_batches, "有批次少查了已关闭那一遍"


def test_single_pass_would_lose_exactly_the_settled_markets(monkeypatch):
    """⭐反证:只查默认那一遍,丢的**全是已结算的** —— 与结果相关的丢样本。

    比例照抄实测(2026-08-16 请求 100 → 默认回 52 未关闭 / `&closed=true` 回 48 已关闭;
    2026-08-03 那次是请求 100 回 72,缺的 28 个抽样全部已结算)。
    """
    open_cids, closed_cids = _cids(52), _cids(48, start=52)
    all_cids = open_cids + closed_cids
    opens = [_market(c) for c in open_cids]
    closeds = [_market(c, closed=True) for c in closed_cids]

    monkeypatch.setattr(ds, "_get", _fake_gamma(opens, closeds))
    found, inconclusive = ds.batch_lookup_gamma(all_cids, net={})
    assert inconclusive == set()
    assert set(found) == set(all_cids), "两遍都查了才应该凑齐 100"

    # 反事实:把已关闭那遍关掉 = 旧世界。丢的必须**恰好**是已结算那批,一个不多一个不少。
    monkeypatch.setattr(ds, "_get", _fake_gamma(opens, closeds, fail_closed=True))
    found_one_pass, _ = ds.batch_lookup_gamma(all_cids, net={})
    lost = set(all_cids) - set(found_one_pass)
    assert lost == set(closed_cids), (
        f"只查一遍丢了 {len(lost)} 个,而已结算的有 {len(closed_cids)} 个 —— "
        f"丢的与结果相关,正是 2026-08-03 那场事故")


# ---------- 判据组 C:C4 —— 「没问到答案」与「答案是没有」必须分得开 ----------

def test_http_failure_makes_the_whole_batch_inconclusive(monkeypatch):
    """整批 HTTP 失败 → 全部算**没问到**,不许算"查不到"。

    算错的后果不是少一条数据:积压会给它们 `attempts+1` 逐格沉底,
    攒够 `MAX_ATTEMPTS` 就**永久丢弃一个真实存在的市场**。
    """
    cids = _cids(5)
    monkeypatch.setattr(ds, "_get", _fake_gamma(fail_default=True, fail_closed=True))
    found, inconclusive = ds.batch_lookup_gamma(cids, net={})
    assert found == {}
    assert inconclusive == set(cids)


def test_absent_from_both_successful_passes_is_confirmed_missing(monkeypatch):
    """两遍都查成了、两遍都没有它 → 才算**确认查不到**。"""
    cids = _cids(3)
    monkeypatch.setattr(ds, "_get", _fake_gamma([_market(cids[0])], []))
    found, inconclusive = ds.batch_lookup_gamma(cids, net={})
    assert set(found) == {cids[0]}
    assert inconclusive == set(), "两遍都成功时不该有'没问到'"


def test_one_failed_pass_poisons_only_the_unfound(monkeypatch):
    """⭐最容易写错的一格:第一遍成功、第二遍失败。

    第一遍就找到的 → 答案已经拿到,不受影响。
    第一遍没找到的 → **可能是已关闭的、而查已关闭那遍恰好挂了** ⇒ 只能算没问到。
    写成"确认查不到"就是把网络故障翻译成"这市场不存在" —— 与结果相关(专丢已结算的)。
    """
    cids = _cids(4)
    monkeypatch.setattr(ds, "_get", _fake_gamma(
        [_market(cids[0]), _market(cids[1])], [], fail_closed=True))
    found, inconclusive = ds.batch_lookup_gamma(cids, net={})
    assert set(found) == {cids[0], cids[1]}
    assert inconclusive == {cids[2], cids[3]}


def test_every_requested_cid_lands_in_exactly_one_bucket():
    """请求数 vs 返回数对账(静默失败清单第 1 条):三个桶必须**恰好**分完输入。

    对账放在这里而不是让 `batch_lookup_gamma` 自己记 —— 调用方各有一套
    已经进心跳、且真有人读的计数器;在底层再记一份只会多出没人读的孤儿字段。
    本条焊的是"料齐了":任何一个 cid 都归得了类,没有第四种"无声消失"。
    """
    cids = _cids(10)
    opens = [_market(c) for c in cids[:6]]
    for label, kw, exp_found, exp_inc in [
            ("两遍都成功", {}, 6, 0),
            ("已关闭那遍挂了", {"fail_closed": True}, 6, 4),
            ("两遍都挂了", {"fail_default": True, "fail_closed": True}, 0, 10)]:
        saved = ds._get
        try:
            ds._get = _fake_gamma(opens, [], **kw)
            found, inconclusive = ds.batch_lookup_gamma(cids, net={})
        finally:
            ds._get = saved
        missing = [c for c in cids if c not in found and c not in inconclusive]
        assert len(found) == exp_found and len(inconclusive) == exp_inc, label
        assert len(found) + len(inconclusive) + len(missing) == len(cids), (
            f"{label}:有 cid 一个桶都没落进去 = 无声消失")


def test_registration_accounts_for_every_market_it_attempted(monkeypatch, tmp_path):
    """⭐调用方层面的对账:注册层「试过的」必须 = 登记成功 + 确认查不到 + 没问到。

    差一个就是有市场被静默吞掉 —— 而积压那头会照常把它当"轮到过了"处理。
    """
    monkeypatch.setattr(ds, "BATCH_REGISTER", True)
    monkeypatch.setattr(ds, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(ds, "_atomic_write_parquet", lambda tbl, dest: None)
    monkeypatch.setattr(ds.time, "sleep", lambda *_: None)

    cids = _cids(9)
    stubs = {c: {"slug": f"s-{i}"} for i, c in enumerate(cids)}
    # 前 4 个默认那遍查得到;已关闭那遍挂了 ⇒ 其余 5 个只能算"没问到"
    monkeypatch.setattr(ds, "_get", _fake_gamma(
        [_market(c) for c in cids[:4]], [], fail_closed=True))

    net, outcome = {}, {}
    n_reg, n_fail = ds.register_new_markets(stubs, net=net, outcome=outcome)
    inconc = net.get("register_inconclusive_count", 0)
    attempted = len(cids) - net.get("register_budget_skipped_count", 0)
    assert n_reg + n_fail + inconc == attempted, (
        f"试了 {attempted} 个,但只交代了 登记{n_reg} + 查不到{n_fail} + 没问到{inconc}")
    # 「没问到」的绝不许进 attempted 名单 —— 进了积压就会给它 attempts+1 逐格沉底
    assert len(outcome["attempted"]) == n_reg + n_fail


# ---------- 判据组 D:回归 —— 批量与逐个查必须产出同一行 ----------

def test_batch_and_per_slug_produce_identical_rows(monkeypatch):
    """⭐同一份 Gamma 数据,两条路径解析出的注册表行必须**逐字段**相同。

    这是"改共享引擎必须默认关闭 + 回归证明"里的回归证明那一半:
    固定数据、只变代码路径。生产数据上的同一对照已实测 8/8 一致(2026-08-16)。
    """
    cids = _cids(6)
    markets = {c: _market(c, closed=(i % 2 == 0)) for i, c in enumerate(cids)}

    per_slug = {c: ds.parse_market(markets[c]) for c in cids}

    monkeypatch.setattr(ds, "_get", _fake_gamma(
        [markets[c] for c in cids if not markets[c]["closed"]],
        [markets[c] for c in cids if markets[c]["closed"]]))
    found, _ = ds.batch_lookup_gamma(cids, net={})
    batched = {c: ds.parse_market(found[c]) for c in cids}

    assert batched == per_slug
    for c in cids:
        assert batched[c]["condition_id"] == c


# ---------- 判据组 D2:⭐跨路对照 —— 同一份数据,只变代码路径 ----------
#
# CLAUDE.md 铁律 4:「改共享引擎必须默认关闭 + 回归证明;用**原版代码 vs 新版代码
# 跑同一份数据**证明逐项一致」。逐个查那条路的十几条判据分散在
# test_registration_budget.py / test_discovery_rate_limit_and_deadline.py /
# test_registration_backlog.py,它们已显式钉在 BATCH_REGISTER=False 上。
# 本组负责另一半:**同样的场景两条路各跑一遍,断言结果相同**。
# 这比"每条路各自满足一遍手写断言"更强 —— 手写断言可能两条都写松了。

def _fake_get_both_dialects(markets, fail_when=()):
    """假 Gamma,**两种问法都懂**:`?slug=X`(逐个查)与 `?condition_ids=…`(批量查)。

    ⚠️ 这是对照实验成立的前提。假接口只懂一种问法的话,两臂之间变的就不止
    "代码路径"一个变量了 —— 那正是项目铁律 1「一次只动一个自变量」禁止的事。
    """
    by_slug = {m["slug"]: m for m in markets}
    by_cid = {m["conditionId"]: m for m in markets}

    def _g(url, **kw):
        for token in fail_when:
            if token in url:
                return {"__http__": token}
        want_closed = "closed=true" in url
        if "condition_ids=" in url:
            asked = re.findall(r"condition_ids=(0x[0-9a-f]+)", url)
            return [by_cid[c] for c in asked
                    if c in by_cid and bool(by_cid[c].get("closed")) == want_closed]
        hit = re.search(r"[?&]slug=([^&]+)", url)
        mk = by_slug.get(hit.group(1)) if hit else None
        return [mk] if mk and bool(mk.get("closed")) == want_closed else []
    return _g


def _register_under(monkeypatch, tmp_path, *, batch, markets, stubs,
                    fail_when=(), **kwargs):
    """在指定路径下跑一次 `register_new_markets`,返回可比较的结果摘要。"""
    written = []
    monkeypatch.setattr(ds, "BATCH_REGISTER", batch)
    monkeypatch.setattr(ds, "REGISTRY_DIR", tmp_path / "registry")
    monkeypatch.setattr(ds, "_atomic_write_parquet",
                        lambda tbl, dest: written.append(tbl.to_pylist()))
    monkeypatch.setattr(ds.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ds, "_get", _fake_get_both_dialects(markets, fail_when))
    net, outcome = {}, {}
    n_reg, n_fail = ds.register_new_markets(stubs, net=net, outcome=outcome, **kwargs)
    rows = written[0] if written else []
    for r in rows:
        r.pop("snapshot_at", None)      # 两臂跑在不同时刻,时间戳本就该不同
    return {"registered": n_reg, "fail": n_fail,
            "inconclusive": net.get("register_inconclusive_count", 0),
            "skipped": net.get("register_budget_skipped_count", 0),
            "attempted": list(outcome.get("attempted", [])),
            "ok": list(outcome.get("registered", [])),
            "rows": sorted(rows, key=lambda r: r["condition_id"])}


@pytest.mark.parametrize("scenario,fail_when,kwargs", [
    ("全部查得到",              (),                {}),
    ("确认查不到(两遍都空)",   (),                {}),
    ("整条链路挂了",            ("slug=", "condition_ids="), {}),
    ("已关闭那遍挂了",          ("closed=true",),  {}),
    ("max_new=0 必须真的是零",  (),                {"max_new": 0}),
    ("max_new 砍一半",          (),                {"max_new": 4}),
    ("max_new=None 不设上限",   (),                {"max_new": None}),
])
def test_both_paths_agree_item_by_item(monkeypatch, tmp_path, scenario, fail_when, kwargs):
    """⭐回归证明:两条路在同一份数据上必须**逐项相同**。"""
    cids = _cids(8)
    markets = [_market(c, closed=(i % 3 == 0)) for i, c in enumerate(cids)]
    if scenario.startswith("确认查不到"):
        markets = markets[:5]           # 后 3 个接口里压根没有
    stubs = {c: {"slug": f"s-{c[-4:]}"} for c in cids}

    a = _register_under(monkeypatch, tmp_path, batch=False, markets=markets,
                        stubs=stubs, fail_when=fail_when, **kwargs)
    b = _register_under(monkeypatch, tmp_path, batch=True, markets=markets,
                        stubs=stubs, fail_when=fail_when, **kwargs)
    assert a == b, f"{scenario}:两条路结果不一致\n逐个查={a}\n批量={b}"


def test_batch_path_no_longer_needs_a_slug(monkeypatch, tmp_path):
    """⭐一处**有意**的行为差异,写下来免得日后被当成 bug 追。

    逐个查是按 `?slug=` 问的 ⇒ 上游没给 slug 的市场永远查不到,
    每轮占一个名额、每轮失败,最后攒够 `MAX_ATTEMPTS` 被永久丢弃
    (2026-08-04 实测这类占积压的 15~16%,吃掉 25% 吞吐)。
    批量是按 `condition_ids=` 问的,**不依赖 slug** ⇒ 这类市场能正常登记。

    这是净改进,但它意味着两条路在这一格上**不等价** —— 故单独立判据,
    不混进上面那组"逐项相同"里(混进去只能靠放松断言,那是自欺)。
    """
    cid = _cids(1)[0]
    markets = [_market(cid)]
    stubs = {cid: {"slug": None}}
    a = _register_under(monkeypatch, tmp_path, batch=False, markets=markets, stubs=stubs)
    b = _register_under(monkeypatch, tmp_path, batch=True, markets=markets, stubs=stubs)
    assert a["registered"] == 0 and a["fail"] == 1, "逐个查:没 slug 就查不到"
    assert b["registered"] == 1 and b["fail"] == 0, "批量:按 cid 问,不需要 slug"


# ---------- 判据组 E:结构 —— 不许再手写第二份打包 ----------

def test_nobody_else_hand_rolls_the_batch_url():
    """焊死"照抄结构而不抽象":`condition_ids=` 的拼接只许有一处。

    2026-08-16 之前有两处各写一份(注册层压根没写、结算守望自己拼),
    结果只有一处知道 8192 这条线。判据钉在**结构**上,而不是靠人记得。
    """
    # 只抓**真的在拼 URL**的写法(f-string / 字符串里带 `condition_ids=` 后面接插值或 %),
    # 不抓文档里提到这个参数名 —— 判据抓错东西会让人去改注释而不是改代码。
    build = re.compile(r"""["']condition_ids=(\{|%s)""")
    root = Path(__file__).resolve().parents[2] / "11-collector"
    offenders = [py.name for py in root.glob("*.py")
                 if py.name not in ("discovery_service.py", "probe_engine.py")
                 and build.search(py.read_text(encoding="utf-8"))]
    assert offenders == [], (
        f"{offenders} 自己拼了 condition_ids= —— 必须走 "
        f"discovery_service.pack_condition_ids / batch_lookup_gamma")


def test_the_structural_guard_would_actually_catch_a_second_copy(tmp_path):
    """⭐判据自检:上面那条如果失效了,我看到的会有什么不同?

    ——把一份"手写打包"喂给它,必须抓到。不做这一步的话,上面那条正则
    只要写错一个字符就永远绿着,而它守的恰恰是"没人再抄一遍"。
    """
    build = re.compile(r"""["']condition_ids=(\{|%s)""")
    assert build.search('q = "&".join(f"condition_ids={c}" for c in cids)')
    assert build.search("""url = "condition_ids=%s" % cid""")
    assert not build.search('# Gamma `condition_ids=` 默认只返回未关闭市场')


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
