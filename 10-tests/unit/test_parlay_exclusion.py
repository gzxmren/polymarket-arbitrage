#!/usr/bin/env python3
"""判据焊死:串关(parlay)组合盘必须**显式剔除 + 出声计数**(2026-08-04)。

## 由来(实测,非假想)

追查「发现层耗时 57s → 100s」时挖出来的。注册积压里稳定有 15~16% 的市场
`slug` 为空,`_lookup_gamma(slug)` 对它们**永远**返回 None:

- 每轮进 `new` 集合 → 占掉 `max_new` 预算的名额 → 失败 → 下轮原样重来
- 实测抽 40 个注册名额,**10 个**(25%)喂给了这类永远注册不上的东西

它们是什么(实测,n=448 活跃市场):**串关组合盘**。
title 形如 `"Dota 2: A vs B AND C vs D AND E vs F"`,而 condition_id 是另一套体制 ——
长 **64 字符**(正常市场 66 = `0x` + 64 hex)且**右侧补零**:
`0x0379047733ac11e756e1408b9c4380ca020000000000000000000000000000`

Gamma 用 `?condition_ids=` 也查不到(实测 0/8)—— 因为它压根不是独立市场,
是多腿组合产品。**它本来就不该进注册表**(实测注册表零污染:57764 个市场里
非标准长度 0 个、title 含 " AND " 0 个)。

## 为什么这是 bug 而不是"反正也没注册进去,无所谓"

因为它现在走的是**静默失败**:每轮悄悄失败、悄悄重来、悄悄吃掉 25% 注册吞吐,
而心跳里唯一的痕迹是 `register_fail` 里混着的几个数字 —— 分不出"接口抽风"和
"这东西根本不可能注册"。违反 CLAUDE.md 第 3 条:**任何降级/剔除/回退,必须出声计数**。

## 判据选型:为什么用 condition_id 形状,不用 title 也不用 slug

实测 n=448,三个判据**完全等价**(`无 slug` ≡ `len(cid)!=66` ≡ `title 含 " AND "`)。
既然等价,选**结构性**的那个:

- ❌ `title 含 " AND "` —— 文案判据。正常市场标题完全可能出现 " AND "
  (如 `"Will X AND Y both happen?"`),误伤是迟早的事
- ❌ `slug 为空` —— 那是**症状**(查不到的原因),不是**身份**。上游哪天补上
  slug 字段,判据就悄悄失效,而我们不会知道
- ✅ `condition_id 不匹配 ^0x[0-9a-f]{64}$` —— 这是 ID 体制本身的差别,
  与文案、与上游字段补全都无关

## 本测试**不能**回答什么

不回答「串关该不该采集」。这里只断言:**如果决定不采,就必须显式剔除并计数**。
若将来要采串关,得先解决它们在 Gamma 上不存在(拿不到 token_id/结算)的问题 ——
那是另一件事,不在本判据范围内。
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11-collector"))

import discovery_service as ds  # noqa: E402

# --- 实测样本(2026-08-04 从生产 firehose 抓的真串关 cid)---
REAL_PARLAY_CIDS = [
    "0x0379047733ac11e756e1408b9c4380ca020000000000000000000000000000",
    "0x037c8917bfcf24862e04525e0ed98c64f40000000000000000000000000000",
    "0x032605d2dafa6205e5b22fe48e662d80ac0000000000000000000000000000",
    "0x037c3cac18e71465e1a20c2cb3d555d3ac0000000000000000000000000000",
]
# 正常市场 cid = 0x + 64 hex,共 66 字符
REAL_NORMAL_CIDS = [
    "0x" + "f7" * 32,
    "0x" + "bc" * 32,
    "0x" + "00" * 32,
]


def _trade(cid, slug="some-market", title="Some Market"):
    return {"conditionId": cid, "slug": slug, "title": title}


# ---------- 判据 1:识别本身 ----------

def test_real_parlay_cids_are_recognised():
    """实测抓到的真串关 cid,一个都不许漏。"""
    for cid in REAL_PARLAY_CIDS:
        assert ds.is_parlay_cid(cid), f"漏判串关: {cid}"


def test_real_normal_cids_are_not_recognised():
    """正常市场一个都不许误伤 —— 误伤的代价是**整个市场的成交流永久收不到**。"""
    for cid in REAL_NORMAL_CIDS:
        assert not ds.is_parlay_cid(cid), f"误判正常市场为串关: {cid}"


def test_criterion_is_id_shape_not_title():
    """判据必须是 ID 形状,**不是**文案 —— 走 extract_active 端到端判。

    焊死的是"将来有人图省事改成 title 匹配"。正常市场的标题完全可能出现 " AND "
    (如 `"Will X AND Y both happen?"`),那种市场是**真市场**,误剔的代价是
    整条成交流永久收不到。

    ⚠️ 必须过 extract_active:`is_parlay_cid` 只收 cid,在它上面断言 title 无关
    是**同义反复**(签名里就没有 title,不可能受影响)—— 那样的断言在判据改成
    title 匹配时照样绿,等于没测。真正会被 title 影响的是 extract_active。
    """
    cid = REAL_NORMAL_CIDS[0]
    counts = {}
    active = ds.extract_active([_trade(cid, title="Will X AND Y both happen?")], counts)
    assert cid in active, "标题含 \" AND \" 的正常市场被误剔了 —— 判据滑向了 title 匹配"
    assert counts.get("excluded_parlay_count") == 0


def test_criterion_is_id_shape_not_slug():
    """判据必须是 ID 形状,**不是** slug 缺失 —— 走 extract_active 端到端判。

    slug 是**症状**(查不到的原因)不是**身份**。上游哪天把 slug 补上,串关仍然是串关
    (Gamma 依然查不到),判据必须照样拦住它。

    ⚠️ 关键是这里的 slug **非空**:本文件其它串关样本一律 `slug=None`,
    若判据悄悄退化成"slug 为空",那些用例全都照样绿 —— 只有这条会红。
    """
    cid = REAL_PARLAY_CIDS[0]
    counts = {}
    active = ds.extract_active([_trade(cid, slug="upstream-filled-this-in")], counts)
    assert cid not in active, "上游补上 slug 后串关就被放行了 —— 判据拿症状当身份"
    assert counts.get("excluded_parlay_count") == 1


@pytest.mark.parametrize("bad", ["", None, "0x", "not-a-cid", "0x" + "ab" * 31])
def test_malformed_cids_are_excluded_too(bad):
    """畸形 cid 一律按"注册不了"处理 —— 放行只会让它们在注册层静默失败。"""
    assert ds.is_parlay_cid(bad)


def test_case_insensitive_hex():
    """大写 hex 的正常 cid 不许被误伤(上游大小写不由我们决定)。"""
    assert not ds.is_parlay_cid("0x" + "AB" * 32)


# ---------- 判据 2:剔除 + 计数 ----------

def test_extract_active_drops_parlays():
    trades = [_trade(REAL_NORMAL_CIDS[0]), _trade(REAL_PARLAY_CIDS[0], slug=None)]
    active = ds.extract_active(trades)
    assert REAL_NORMAL_CIDS[0] in active
    assert REAL_PARLAY_CIDS[0] not in active, "串关仍然进了活跃集合 → 仍会吃注册预算"


def test_parlay_exclusion_is_counted():
    """⭐核心:剔除必须**出声**。计数为 0 而实际剔了 = 又一次静默失败。"""
    counts = {}
    trades = [_trade(c, slug=None) for c in REAL_PARLAY_CIDS] + \
             [_trade(REAL_NORMAL_CIDS[0])]
    ds.extract_active(trades, counts)
    assert counts.get("excluded_parlay_count") == len(REAL_PARLAY_CIDS)


def test_parlay_count_is_by_market_not_by_trade():
    """按**市场**计数,不按成交笔数 —— 否则一个高频串关能把计数刷爆、淹掉真信号。"""
    counts = {}
    cid = REAL_PARLAY_CIDS[0]
    ds.extract_active([_trade(cid, slug=None) for _ in range(50)], counts)
    assert counts.get("excluded_parlay_count") == 1


def test_hft_exclusion_is_also_counted():
    """顺带焊死:hft_crypto 的剔除**原本也是静默的**(旧代码 `continue` 无计数)。

    同一条规矩对所有剔除一视同仁,否则下次又会漏。
    """
    counts = {}
    hft = {"conditionId": REAL_NORMAL_CIDS[1], "slug": "btc-updown-5m-1200PM",
           "title": "BTC up or down 5min 12:00 PM"}
    assert ds.classify_market(hft)[0] == "hft_crypto", "样本本身没被分类成 hft,判据失效"
    ds.extract_active([hft], counts)
    assert counts.get("excluded_hft_count") == 1


def test_counts_is_optional_and_never_crashes():
    """计数是可选的(生产以外的调用方不传),但不许因此崩 —— 也不许因此静默。"""
    assert ds.extract_active([_trade(REAL_NORMAL_CIDS[0])]) != {}


# ---------- 判据 3:计数必须活到心跳(否则等于没计) ----------

def test_new_counters_include_exclusion_counters():
    import collector_core as cc
    counts = cc.new_counters()
    for k in ("excluded_parlay_count", "excluded_hft_count"):
        assert k in counts, f"{k} 没进 COUNTER_KEYS → 运行时靠 .get 兜底,漏计无人知"


def test_exclusion_counters_reach_the_heartbeat():
    """进程一退就蒸发的计数 = 没有计数。一周后校准阈值时只能 grep 日志文本。"""
    import storage_engine as se
    for k in ("excluded_parlay_count", "excluded_hft_count"):
        assert k in se.AUDIT_FIELDS, f"{k} 没进 AUDIT_FIELDS → 不落 parquet"


# ---------- 判据 4:真实数据回归(固定数据,只变代码) ----------

# ⚠️ 必须在**仓库内**,且**不许 skipif**。
# 这两条是满足 CLAUDE.md 铁律 4(原版代码 vs 新版代码跑同一份数据)的唯一产物 ——
# 一旦样本丢失就该**变红**,而不是静默跳过。原先它指向 /tmp 下某个会话的 scratchpad:
# 那份文件在这台机器上碰巧还在,所以本地一直绿,但换机器/重启/新 clone 上会静默 skip,
# 「铁律 4 已满足」这个结论就在无人知晓的情况下作废了 —— 典型的假信心。
#
# 样本裁剪(2026-08-05):原始 5000 笔 4.0MB → 388 笔 83KB。裁剪方式是
# 「每个 conditionId 保留首见 + 只留代码真正读的 conditionId/slug/title 三个字段」,
# 这与被测代码的语义**恒等**(extract_active 按 cid 取首见;classify_market 只读
# slug+title),并已实测逐项比对:extract_active 输出/两个计数/cid 集合/slug 全部相同。
# 不是按内容筛行 —— 那会变成与结果相关的抽样。
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "firehose_sample_2026-08-04.json"


def test_regression_on_real_firehose_only_parlays_differ():
    """⭐回归证明:在**同一份真实数据**上,新版相对旧版**只**少了串关,别的一个不差。

    CLAUDE.md 铁律 4:改共享引擎必须用「原版代码 vs 新版代码跑同一份数据」逐项证明一致。
    这里直接在测试里重建旧版语义(不剔串关),再逐项比对差集。
    """
    trades = json.loads(FIXTURE.read_text())

    # 旧版语义:只剔 hft,不认识串关
    old = {}
    for t in trades:
        cid = t.get("conditionId")
        if not cid or cid in old:
            continue
        if ds.classify_market(t)[0] == "hft_crypto":
            continue
        old[cid] = t

    counts = {}
    new = ds.extract_active(trades, counts)

    dropped = set(old) - set(new)
    assert set(new) - set(old) == set(), "新版凭空多出了市场 —— 不是纯剔除"
    assert dropped, "固化样本里没有串关,这条回归证明不了任何事(换样本)"
    assert all(ds.is_parlay_cid(c) for c in dropped), "剔掉了非串关的东西"
    assert counts["excluded_parlay_count"] == len(dropped), "计数与实际剔除数对不上"

    # 剔除比例必须是"少数派" —— 若某天判据写错把大半市场剔了,这条会红
    assert len(dropped) / len(old) < 0.10, f"剔除比例异常高 {len(dropped)}/{len(old)}"


def test_regression_dropped_markets_are_all_unregistrable():
    """被剔的每一个,都必须**确实**是 slug 缺失(= 旧代码下必然注册失败)。

    这条是"我没有顺手剔掉有用东西"的可核查证明。
    """
    trades = json.loads(FIXTURE.read_text())
    by_cid = {}
    for t in trades:
        by_cid.setdefault(t.get("conditionId"), t)
    dropped = [c for c in by_cid if c and ds.is_parlay_cid(c)]
    assert dropped, "固化样本里没有串关(换样本)"
    for cid in dropped:
        assert not by_cid[cid].get("slug"), f"{cid} 有 slug,旧代码下本可注册,不该被剔"
