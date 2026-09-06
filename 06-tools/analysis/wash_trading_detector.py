#!/usr/bin/env python3
#
# ============================================================================
# 🛑 已归档 · 2026-09-05 —— 方向三(跟单领先者)已于 2026-09-04 判决关闭
# ============================================================================
# 判决:V6 判决版跑出 `NOT_FOLLOWABLE`(功效闸通过:零分布 p95 +1.45% < 2.0%,
#       ⇒ 这次的红是真结论,不是「尺子看不见」)。两臂全红:
#         臂A 平均 +1.248% / 中位 +0.100% / n=16,879  ← 连随机挑钱包的 +1.4516% 都打不过
#         臂B 平均 -0.112% / 中位 **-100.000%** / n=11,281
# 依据:memory `verdict-v6-not-followable-2026-09-04.md`
#       关闭设计单 `docs/DESIGN_CLOSE_DIRECTION3_2026-09-05.md`
#
# 🔴 验证窗已烧掉:预登记单规定它**只跑一次**,2026-09-04 跑了、也看了结果。
#    ⛔ 任何后续方案**不许**再拿 boundary>=2026-08-27 那段数据当「干净的一次检验」——
#       那是在已看过的数据上试第二个方案(多重比较)。要出新判决必须另立验证窗,
#       或在新预登记单里明确登记为第二次并加严红线。
#
# ⛔ 不许做的事(预登记单 §4 / 关闭设计单 §三点五 明文禁止):
#    调 p_floor(0.05/0.15/0.20)挑好看的 / 改「前 10%」比例 / 看到结果后改 Δ 或持有期。
#
# 本文件保留不删:它是「这个方向怎么被否掉的」完整记录,判据也全部保留。
# ============================================================================
# ⭐ 但本文件是**通用工具**:它的「单钱包占笔数 ≥85%」判别式与刷单市场名单
#    在新方向(BTC 短周期盘)里很可能还要复用。**归档的是方向三,不是这段逻辑。**
# ============================================================================
"""刷单市场检测器 —— 离线体检,只读数据湖,不碰采集器。

设计单:`docs/DESIGN_WASH_DETECTOR_2026-08-23.md`(V1 已证伪,本文件实现 V2)
判据:   `10-tests/unit/test_wash_detector.py`(先于本文件写成)

## 它解决什么问题

2026-08-23 实测:全湖 2,680 万笔成交里,**158 万笔(5.9%)集中在 5 个市场**
(其中 **4 个经人工核实**,见 KNOWN_WASH_MARKETS;第 5 个是本检测器多抓到的,形状相同但**未经独立核实**),
形状是「一个钱包占了该市场 85% 以上的成交笔数 + 价格贴死在 0 或 1 + 每笔金额几乎为 0」
—— 即自己跟自己对敲刷笔数。方向三按**笔数**给钱包排名,这批笔数会把排名带偏。

## ⚠️ 它**不**解决什么(边界,防止将来被误用)

- 判定单位是**市场**,不是钱包。同一个钱包在 1,055 个市场里只有 3 个在刷,
  按钱包拉黑会误杀它其余 1,051 个正常行为。
- 抓不到「价格居中的大额对敲」—— 那是另一个形状,本工具对它是瞎的。
- 不分时段:用的是整个市场生命周期的中位数。
- 不是实时告警,依赖市场已积累足够笔数。
- ⚠️ **笔数 <1,000 的市场根本不进入判定**(它们占全湖 47.29% 的笔数)。
  实测该区间内同形状且非退化的只有 23 个市场 / 3,677 笔 = 全湖 **0.014%** ——
  有界且极小,**但不是零**。若将来要找"小规模刷单",本工具答不了。

## 为什么主判据是「单钱包占笔数」而不是「规模+价格」

2026-08-22 曾提出用「价格贴两端 且 规模中位≤20 股」。**2026-08-23 实测该判别式
误伤 124 个正常大市场中的 51 个(41%),会排掉全湖 12.56% 的笔数,而真实污染只有 5.91%**
—— 即多扔掉的干净数据比脏数据还多。根因:正常大市场也大量以 4~5 股为单位、
在价格贴近 0/1 时成交(答案已明朗的市场本来就在 0.99 附近交易)。
第 3、4 条保险条件今天不改变任何结果,留着是为了挡住将来「合法做市商主导冷门盘」这一类误伤。
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
import warnings
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

# ⭐可选导入一律 `except Exception`,不许只写 ImportError:
# 模块级的 NameError/SyntaxError 抛的都不是它,只写 ImportError 会让一个附加功能坏掉时
# 把调用方其余职责全部打断(2026-08-17 踩到,照 11-collector/alerts.py 的写法抄)。
try:
    import duckdb
    # ⭐实测(2026-08-23,不是猜的):对四类坏输入 DuckDB 抛的是**四个不同的类** ——
    #   内容不是 parquet → InvalidInputException (→ ProgrammingError)
    #   文件不存在/glob 空 → IOException        (→ OperationalError)
    #   缺列              → BinderException     (→ ProgrammingError)
    # 它们的**共同基类**是 duckdb.Error。凭"应该是 IO 错误"写 except 会漏掉三种。
    LAKE_READ_ERRORS: tuple = (duckdb.Error, OSError)
    DUCKDB_AVAILABLE = True
except Exception as _e:  # noqa: BLE001
    duckdb = None
    LAKE_READ_ERRORS = (OSError,)
    DUCKDB_AVAILABLE = False
    _DUCKDB_IMPORT_ERROR = _e

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAKE_GLOB = str(PROJECT_ROOT / "11-collector" / "data" / "raw" / "dt=*" / "*.parquet")
REPORT_PATH = PROJECT_ROOT / "11-collector" / "data" / "wash_markets.json"

# ---------------- 判别式门槛 ----------------
# ⚠️ 诚实标注:下列 MEASURED_ 常量是**看过 2026-08-23 的全湖分布之后**定的,
# 不享有"预登记未被结果污染"的地位。它们的可信度来自另外两条(见设计单 §11):
#   ① 对照组用的是与判别式完全无关的轴(参与钱包数),不构成循环论证;
#   ② 门槛落在实测断层里(占比 82.2% → 91.5% 之间是空的),不是贴着边界切的。

MEASURED_MIN_TRADES = 1_000
"""笔数下界。

⚠️ **2026-08-23 review 更正**:这里原本写着「更小的市场即使 100% 是刷单,
对全湖笔数的影响 <0.01%」—— 那是**单个市场**的数,被我错当成了**总量**的界。
实测总量:笔数 <1,000 的市场有 167,656 个,合计 **12,676,929 笔 = 全湖 47.29%**,
它们被 `HAVING` 挡在门外,**连判都没判过**。这个数与"<0.01%"差了近四个数量级。

**真正的理由(结构性,已实测)**:占比这个量在低笔数处**退化** ——
一个市场只有 1 笔成交时,单钱包占比按定义就是 100%。
实测 <1,000 笔的市场里"长着刷单形状"的有 15,456 个,而其中 **15,433 个落在 1~10 笔那一档**,
全是这种退化产物,不是刷单。

**盲区的实测上界(这才是该记的数)**:剔掉退化的 1~10 笔档之后,
10~1,000 笔区间里同形状的市场共 **23 个 / 3,677 笔 = 全湖 0.014%**。
⇒ 抬高门槛漏掉的真刷单量有界且极小,但**不是零**,见模块 docstring 的边界一节。"""

MEASURED_WALLET_SHARE = 0.85
"""⭐主判据:单个钱包占该市场成交笔数的比例。
实测断层:…79.0 / 82.2 → 91.5 / 97.2 / 98.6 / 99.5 / 99.9,中间 9 个百分点是空的。
0.85 落在空档内;上下浮动 3 个百分点结果不变(判据 test_threshold_is_robust 焊住)。"""

MEASURED_EXTREME_LOW = 0.01
MEASURED_EXTREME_HIGH = 0.99
"""价格中位贴两端。1 个最小报价单位,自然定义,不是拟合值。"""

MEASURED_MAX_NOTIONAL_PER_TRADE = 10.0
"""每笔平均名义额(美元)。实测 5 个刷单市场为 $0.025 ~ $5.72;
正常大市场中位 $148.94、最小 $50.96。"""

# ---------------- 对照组(用于自我证伪,轴与判别式无关)----------------
CONTROL_MIN_TRADES = 10_000
CONTROL_MIN_WALLETS = 1_000
"""「正常大市场」的定义:笔数多**且参与者众多**。
⭐参与钱包数与判别式的三个维度(占比/价格/名义额)都不重合 —— 这是它能当对照组的唯一理由。
⚠️ 我第一版用的是「名义总额≥100万美元」,自查发现它与"每笔名义额"**构造性相关**
(笔数≥1万 + 总额≥100万 ⇒ 每笔≥$100 是被定义强制的),那个对照组已作废。"""

# ---------------- 证伪红线(设计单 §6,必须是代码会算的,不是文档里写的)----------------
MAX_CONTROL_FALSE_POSITIVES = 0
MAX_FLAGGED_TRADE_SHARE = 0.15
MIN_MARKETS_FOR_SHARE_REDLINE = 500
"""「命中笔数占比」这条红线的**生效前提**。

由来(2026-08-23,写判据时当场踩到):这条红线问的是「相对**整个数据湖**有没有抓过头」,
可它被套在一个 11 个市场的合成样本上时,刷单盘占了样本的 91.8% ⇒ 必然超线、必然假红。
判据的工况参数要来自现网:全湖扫描覆盖数千个市场,子集扫描不具备可比性。

⚠️ 前提不满足时**不是悄悄跳过** —— falsification 里会写明 not_evaluated,
命令行也会打出来。静默跳过一条红线,与"红线通过了"看起来一模一样。"""

KNOWN_WASH_MARKETS = frozenset({
    # 2026-08-23 用与判别式无关的轴(单钱包占笔数≥95%)独立挑出,并人工核对形状。
    "0xefa17dee3af09f69f9ddf245b969aa4efbe7c71cdf06ee49d694408bc33e2ed2",
    "0x5f03bb886142aaac823069e0a4c9b7e0b78dc161b32fd49247097f9907afa8cc",
    "0xc53955c1303b0ea1d676f99c52ea3b28961fb41d4f04c8cecd0b96d79c0d3bf5",
    "0x6fddebc3d35bb590ab835d9096662e3ebcbd03f4f6af549367b9da78327b53bc",
})

_NUMERIC_FIELDS = ("n_trades", "n_wallets", "top_wallet_trades",
                   "med_size", "med_price", "notional")


def _finite(v) -> bool:
    """None / NaN / inf / 非数 一律判为不可用。

    ⚠️ 不用 `if not v` —— 那会把合法的 0 也当成缺失。
    """
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def classify_market(stats: dict[str, Any]) -> tuple[bool, list[str]]:
    """判一个市场是不是刷单。返回 (是否命中, 依据/原因列表)。

    **原因列表永远非空** —— 无论命中与否都要说得出为什么。
    静默丢样本是本项目的死因(曾静默丢掉 80% 仍一声不吭)。
    """
    missing = [f for f in _NUMERIC_FIELDS if not _finite(stats.get(f))]
    if missing:
        # ⚠️ med_size 不参与判定,但仍要求它可用:它要写进报告供人工复核,
        # 一个字段缺失的行整体不可信,不许连蒙带猜地判它。
        return False, [f"坏行:字段缺失或非有限数 {missing}"]

    n_trades = float(stats["n_trades"])
    if n_trades <= 0:
        return False, ["坏行:笔数为 0"]

    share = float(stats["top_wallet_trades"]) / n_trades
    per_trade = float(stats["notional"]) / n_trades
    med_price = float(stats["med_price"])
    extreme = med_price <= MEASURED_EXTREME_LOW or med_price >= MEASURED_EXTREME_HIGH

    checks = [
        (n_trades >= MEASURED_MIN_TRADES, f"笔数 {n_trades:,.0f} >= {MEASURED_MIN_TRADES:,}"),
        (share >= MEASURED_WALLET_SHARE, f"单钱包占笔数 {share:.1%} >= {MEASURED_WALLET_SHARE:.0%}"),
        (extreme, f"价格中位 {med_price:.4f} 贴两端(<={MEASURED_EXTREME_LOW} 或 >={MEASURED_EXTREME_HIGH})"),
        (per_trade < MEASURED_MAX_NOTIONAL_PER_TRADE,
         f"每笔名义额 ${per_trade:,.4f} < ${MEASURED_MAX_NOTIONAL_PER_TRADE}"),
    ]
    hit = all(ok for ok, _ in checks)
    reasons = [("✓ " if ok else "✗ ") + text for ok, text in checks]
    return hit, reasons


def is_control_market(stats: dict[str, Any]) -> bool:
    """是否属于「正常大市场」对照组。轴与判别式无关(见 CONTROL_* 上方说明)。"""
    if not (_finite(stats.get("n_trades")) and _finite(stats.get("n_wallets"))):
        return False
    return (float(stats["n_trades"]) >= CONTROL_MIN_TRADES
            and float(stats["n_wallets"]) >= CONTROL_MIN_WALLETS)


def scan_rows(rows: Iterable[dict[str, Any]], lake_total_trades: int | None = None) -> dict[str, Any]:
    """扫一批市场统计,返回命中清单 + 对账 + 证伪判决。

    ⭐对账是强制的:scanned 必须等于 flagged + clean + bad_rows。
    「请求数 vs 返回数」对不上时**必须看得见**,不许用"大概是脏数据"糊过去。

    `lake_total_trades`:**全湖**总笔数,用作占比红线的分母。
    ⚠️ 2026-08-23 当场踩到:聚合时 `HAVING count(*) >= MEASURED_MIN_TRADES` 把小市场滤掉了,
    于是 scanned 的笔数只有全湖的 53%(1,415 万 / 2,680 万)。拿它当分母,
    同一批命中会从 5.93% 虚报成 11.24% —— **分母换了,红线就在跟一个不存在的世界比**。
    不传时退化为用扫过的笔数,并在报告里写明分母是哪一个,不许含糊。
    """
    flagged, clean, bad = [], 0, 0
    control_seen = control_fp = 0
    total_trades = flagged_trades = 0
    known_seen, known_caught = 0, 0

    for r in rows:
        cid = r.get("condition_id")
        hit, reasons = classify_market(r)
        is_bad = any(x.startswith("坏行") for x in reasons)
        if _finite(r.get("n_trades")):
            total_trades += float(r["n_trades"])

        if is_control_market(r):
            control_seen += 1
            if hit:
                control_fp += 1
        if cid in KNOWN_WASH_MARKETS:
            known_seen += 1
            known_caught += int(hit)

        if is_bad:
            bad += 1
        elif hit:
            flagged_trades += float(r["n_trades"])
            flagged.append({
                "condition_id": cid,
                "n_trades": int(r["n_trades"]),
                "n_wallets": int(r["n_wallets"]),
                "top_wallet_share": round(float(r["top_wallet_trades"]) / float(r["n_trades"]), 4),
                "med_size": round(float(r["med_size"]), 4),
                "med_price": round(float(r["med_price"]), 6),
                "notional_per_trade": round(float(r["notional"]) / float(r["n_trades"]), 4),
                "reasons": reasons,
            })
        else:
            clean += 1

    flagged.sort(key=lambda m: m["n_trades"], reverse=True)
    scanned = bad + clean + len(flagged)
    denom = float(lake_total_trades) if lake_total_trades else total_trades
    denom_kind = "全湖笔数" if lake_total_trades else "扫过的笔数(未含被 HAVING 滤掉的小市场)"
    share = (flagged_trades / denom) if denom else 0.0

    # ⭐红线在这里**被算出来并接到判决上**。
    # 2026-08-10 犯过「算了红线却没接到判决上」—— 算完不用等于没算。
    falsification = {
        "positives_all_caught": known_caught == known_seen,
        "known_positives_seen": known_seen,
        "known_positives_caught": known_caught,
        "control_markets_seen": control_seen,
        "control_false_positives": control_fp,
        "flagged_trade_share": round(share, 6),
        "share_denominator": int(denom),
        "share_denominator_kind": denom_kind,
    }
    breaches = []
    if not falsification["positives_all_caught"]:
        breaches.append(f"已知刷单市场漏掉 {known_seen - known_caught} 个(红线:0)")
    if control_fp > MAX_CONTROL_FALSE_POSITIVES:
        breaches.append(f"误伤正常大市场 {control_fp} 个(红线:{MAX_CONTROL_FALSE_POSITIVES})")
    share_redline_ran = scanned >= MIN_MARKETS_FOR_SHARE_REDLINE
    falsification["flagged_trade_share_evaluated"] = share_redline_ran
    if not share_redline_ran:
        falsification["flagged_trade_share_skip_reason"] = (
            f"只扫了 {scanned} 个市场 < {MIN_MARKETS_FOR_SHARE_REDLINE},"
            f"样本不代表全湖,本条红线未参与判决")
    elif share > MAX_FLAGGED_TRADE_SHARE:
        breaches.append(f"命中笔数占{denom_kind} {share:.2%}(红线:{MAX_FLAGGED_TRADE_SHARE:.0%})")

    return {
        "verdict": "FAIL" if breaches else "PASS",
        "breaches": breaches,
        "audit": {"scanned": scanned, "flagged": len(flagged), "clean": clean, "bad_rows": bad,
                  "scanned_trades": int(total_trades), "flagged_trades": int(flagged_trades),
                  "lake_total_trades": int(lake_total_trades) if lake_total_trades else None},
        "falsification": falsification,
        # ⭐必须记**全部**决定 verdict 的常量,不只是判别式那 5 个。
        # 否则半年后常量一改,旧报告从产物本身**无法复核**("对照组 125 个"是按什么定义数的?)。
        "thresholds": {
            "MEASURED_MIN_TRADES": MEASURED_MIN_TRADES,
            "MEASURED_WALLET_SHARE": MEASURED_WALLET_SHARE,
            "MEASURED_EXTREME_LOW": MEASURED_EXTREME_LOW,
            "MEASURED_EXTREME_HIGH": MEASURED_EXTREME_HIGH,
            "MEASURED_MAX_NOTIONAL_PER_TRADE": MEASURED_MAX_NOTIONAL_PER_TRADE,
            "CONTROL_MIN_TRADES": CONTROL_MIN_TRADES,
            "CONTROL_MIN_WALLETS": CONTROL_MIN_WALLETS,
            "MAX_CONTROL_FALSE_POSITIVES": MAX_CONTROL_FALSE_POSITIVES,
            "MAX_FLAGGED_TRADE_SHARE": MAX_FLAGGED_TRADE_SHARE,
            "MIN_MARKETS_FOR_SHARE_REDLINE": MIN_MARKETS_FOR_SHARE_REDLINE,
            "KNOWN_WASH_MARKETS": sorted(KNOWN_WASH_MARKETS),
        },
        "markets": flagged,
    }


AGG_SQL = """
SELECT condition_id,
       count(*)                        AS n_trades,
       count(DISTINCT proxy_wallet)    AS n_wallets,
       max(cnt)                        AS top_wallet_trades,
       median(size)                    AS med_size,
       median(price)                   AS med_price,
       sum(size * price)               AS notional
FROM (
  SELECT condition_id, proxy_wallet, size, price,
         count(*) OVER (PARTITION BY condition_id, proxy_wallet) AS cnt
  FROM read_parquet(?)
)
GROUP BY condition_id
HAVING count(*) >= ?
"""


def scan_lake(lake_glob: str = LAKE_GLOB, memory_limit: str = "8GB") -> dict[str, Any]:
    """扫整个数据湖。失败时**出声**并抛出,不吞异常。

    ⚠️ 只 `HAVING count(*) >= MEASURED_MIN_TRADES` 是为了让聚合结果小到能进内存;
    被过滤掉的都是笔数 <1,000 的市场,按定义永不命中 —— 这不是与结果相关的筛选。
    """
    if not DUCKDB_AVAILABLE:
        raise RuntimeError(f"需要 duckdb 才能扫数据湖,但导入失败:{_DUCKDB_IMPORT_ERROR!r}")
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC'")   # ⚠️ 不设会静默用本机时区(JST),踩过
        # ⚠️ 不许用 f-string 拼进 SQL。实测(2026-08-23 review)拼接会执行多条语句,
        # 而 DuckDB 的 SQL 能 COPY 到任意路径、ATTACH/INSTALL 扩展、read_csv 读任意文件
        # ⇒ 一次成功注入不是"报个错",是本进程权限下的任意文件读写。
        # 实测 SET 支持参数化,没有任何理由拼接。
        con.execute("SET memory_limit=?", [memory_limit])
        rows = con.execute(AGG_SQL, [lake_glob, MEASURED_MIN_TRADES]).fetchall()
        cols = [d[0] for d in con.description]
        # ⭐单独数一次全湖总笔数当分母 —— 上面的聚合被 HAVING 滤过,不能拿它当全湖。
        lake_total = con.execute("SELECT count(*) FROM read_parquet(?)", [lake_glob]).fetchone()[0]
    except LAKE_READ_ERRORS as e:
        raise RuntimeError(f"读数据湖失败({type(e).__name__}):{e}") from e
    finally:
        con.close()
    return scan_rows([dict(zip(cols, r)) for r in rows], lake_total_trades=lake_total)


def write_report(result: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    # 用 timezone.utc 而非 datetime.UTC:后者 3.11+ 才有,且代码库其余部分统一用前者
    payload["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_excluded_markets(path: str | Path = REPORT_PATH) -> set[str]:
    """给分析脚本用:读出待排除的 condition_id 集合。

    ⭐这是「使用事实」的那一头。只产出清单而没有读取者,正是本项目已犯过 4 次的形状。

    清单缺失/损坏时降级为空集,**但一定 warn** —— 静默返回空集等于悄悄关掉了排除,
    而那与"本来就没有脏数据"看起来一模一样。
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warnings.warn(f"刷单清单不存在:{path} —— 本次分析**未做任何排除**", UserWarning, stacklevel=2)
        return set()
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        warnings.warn(f"刷单清单读不出来({type(e).__name__}):{path} —— 本次分析**未做任何排除**",
                      UserWarning, stacklevel=2)
        return set()
    markets = raw.get("markets") if isinstance(raw, dict) else None
    if not isinstance(markets, list):
        warnings.warn(f"刷单清单格式不对(markets 不是列表):{path} —— 本次分析**未做任何排除**",
                      UserWarning, stacklevel=2)
        return set()
    good = {m["condition_id"] for m in markets
            if isinstance(m, dict) and isinstance(m.get("condition_id"), str)}
    # ⭐「请求数 vs 返回数」对账:进去 N 条、出来 M 条,差额必须**出声**。
    # 这函数的 docstring 承诺了"一定 warn",而原实现只对**整份文件**坏掉 warn,
    # 对**列表内单条**坏掉是静默丢弃的 —— 承诺的防线没接住(2026-08-23 review 抓到)。
    dropped = len(markets) - len(good)
    if dropped:
        warnings.warn(f"刷单清单里有 {dropped}/{len(markets)} 条格式不对已丢弃:{path}",
                      UserWarning, stacklevel=2)
    return good


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="刷单市场检测器(离线体检)")
    ap.add_argument("--test", action="store_true",
                    help="测试模式:报告写到 /tmp/,绝不碰生产数据目录")
    ap.add_argument("--lake", default=LAKE_GLOB)
    ap.add_argument("--out", default=None)
    ap.add_argument("--memory-limit", default="8GB")
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else (
        Path("/tmp/wash_markets_test.json") if args.test else REPORT_PATH)

    result = scan_lake(args.lake, args.memory_limit)
    write_report(result, out)

    a, f = result["audit"], result["falsification"]
    print(f"判决: {result['verdict']}")
    for b in result["breaches"]:
        print(f"  🔴 {b}")
    print(f"对账: 扫过 {a['scanned']:,} 个市场 = 命中 {a['flagged']} + 干净 {a['clean']:,} + 坏行 {a['bad_rows']}")
    print(f"命中笔数 {a['flagged_trades']:,} / {f['share_denominator_kind']} "
          f"{f['share_denominator']:,} = {f['flagged_trade_share']:.2%}")
    if a["lake_total_trades"]:
        print(f"  (其中扫过的笔数 {a['scanned_trades']:,},"
              f"被 HAVING<{MEASURED_MIN_TRADES:,}笔 滤掉 {a['lake_total_trades'] - a['scanned_trades']:,})")
    print(f"对照组 {f['control_markets_seen']} 个,误伤 {f['control_false_positives']} 个")
    if not f["flagged_trade_share_evaluated"]:
        print(f"  ⚠️ 占比红线未参与判决:{f['flagged_trade_share_skip_reason']}")
    print(f"报告: {out}")
    for m in result["markets"]:
        print(f"  {m['n_trades']:>9,} 笔 | 单钱包 {m['top_wallet_share']:.1%} | "
              f"价格中位 {m['med_price']:.4f} | 每笔 ${m['notional_per_trade']:.4f} | {m['condition_id']}")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
