#!/usr/bin/env python3
"""方向三 · 第一关(毛利层):钱包战绩持续性。

预登记单(唯一权威):docs/PREREG_WALLET_SKILL_V2_2026-08-18.md
⛔ 本脚本的任何参数若与该单子不符,以单子为准 —— 改参数 = 新测试 = 须另立子单。

执行顺序(单子 §11 反自欺条款,不可颠倒):
    1. 启动自检(§2 封窗等待期 + §4 符号铁律分离度)   -- 不过则中止
    2. §8 坏数据自检 P1/P2                              -- 不亮红则中止
    3. §9 G5 缺失与结果不相关检验
    4. 主判决 + 敏感性网格(同一次批量运行,一次性全表输出)

用法:
    python3 08-backtests/wallet_skill_persistence.py --stage selfcheck
    python3 08-backtests/wallet_skill_persistence.py --stage badcheck
    python3 08-backtests/wallet_skill_persistence.py --stage full
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "08-backtests" / "results" / "wallet_skill_v2_2026-08-18"

# ─────────────────────────────────────────────────────────────────────────────
# 单子参数(§2 §3 §6 §7);⛔ 逐字来自预登记单,不许在别处硬编码第二份
# ─────────────────────────────────────────────────────────────────────────────
WIN_D = ("2026-07-22 00:00:00", "2026-08-01 00:00:00")   # 排名窗 D(半开区间,UTC)
WIN_V = ("2026-08-01 00:00:00", "2026-08-11 00:00:00")   # 验证窗 V(半开区间,UTC)
SEAL_WAIT_SEC = 86400            # §2 封窗等待期:验证窗末日 +24h 内不得开跑
INGEST_GATE_SEC = 86400          # §3.2 闸一:采集延迟 < 24h
SETTLE_GATE_DAYS = 7             # §3.2 闸二(主口径):成交 -> end_date 在 0~N 天内
MIN_TRADES = 20                  # §3.3 主门槛:两窗各 >= 20 笔
BOT_TRADES_PER_MKT = 20          # §3.3 疑似机器人:D 窗每市场平均成交 >= 20 笔
TOP_FRAC = 0.10                  # §6 取前 10%
REDLINE_A_PP = 0.5               # §6 红线 A:R_V >= +0.5pp
BOOTSTRAP_N = 1000               # §6 红线 B:分块 bootstrap 次数
BOOTSTRAP_Q = 0.025              # §6 红线 B:2.5% 分位数 > 0
SIGN_SEPARATION_MIN = 0.50       # §4 符号铁律主判据:分离度 > +0.50
BADCHECK_SEEDS = 20              # §8 P1/P2 各跑 20 次种子

# §0.8(2026-08-19,用户批准):红线 A 由「绝对 +0.5pp」改为【置换检验】。
# 由来(实测):随机抽一组钱包当「高手」,R_V 中位 +0.9449pp、62% 越过 +0.5pp
# ⇒ 原红线画在了瞎猜的平均水平【以下】,分不出会的和不会的。
PERM_N = 200                     # 零分布的置换次数
PERM_Q = 0.95                    # 真实前 10% 须【超过】零分布的这个分位
# ⛔ 两段盐必须不相交:定阈值的抽样与 §8 检验阈值的抽样若是同一批,
#    假阳率必然恰好等于 5%,对实现写没写错【毫无分辨力】(同义反复)。
PERM_SALT_NULL = 0               # 零分布用 [0, PERM_N)
PERM_SALT_BADCHECK = 100000      # §8 P1/P2 用 [100000, ...)
BADCHECK_MAX_GREEN = 1           # §8 闸门:亮绿次数 <= 1/20
G5_TOLERANCE_PP = 1.0            # §9 第 11 项:两窗已结算率偏离 >1pp 即中止
G1_MIN_MARKETS = 400             # §9 第 10 项 = 母单 G1:每窗已结算市场数须 >= 400

# §0.5 修订二(2026-08-19,用户批准):G5「实质差异」的数值线。
# ⚠️ 自我举报:这两个数【无实测分布支撑】—— 闸一从没被测过,没有分布可依。
#    依据是已知病例的量级:§0.1 实测「不设闸二时两窗剔除画像相反」的价差是 0.90,
#    本线取 1/18。偏保守是有意的 —— 误报的代价只是今天不许宣布绿,
#    漏报的代价是整个结论作废 ⇒ 此处假红灯远比假绿灯便宜。
G5_BASERATE_DIFF_MAX_PP = 5.0    # 两组结算基础率之差(百分点)
G5_PRICE_MEDIAN_DIFF_MAX = 0.05  # 两组入场价中位数之差

# §9 第 6、7 项的预期值。修订前只是并排 print、【没有任何判定】(2026-08-18 review 第 5 条)。
# ⚠️ 两个基线都是 2026-08-18 实测,而当时闸二的 end_date 解析是坏的(§0.5 修订三);
#    修好后必须重测并回填此处 —— 否则就是拿过期保证当红线用。
EXPECT_LEAK_PCT = 1.59           # 跨窗同钱包同市场泄漏占 V 窗的比例(%)
LEAK_TOLERANCE_PP = 0.5          # 偏离超过它即打红(须查明,不中止)
EXPECT_BOTS = 245                # D 窗疑似机器人钱包数
BOTS_TOLERANCE_FRAC = 0.10       # 相对偏离超过它即打红

# DuckDB 资源闸(2026-08-18 事故后新增)
# ⚠️ DuckDB 默认按【物理内存的 80%】规划(本机实测 24.4 GiB),它不知道机器上
#    还跑着浏览器和两个会话。08-18 涨到 18.7G 时 systemd-oomd 把整个 tmux scope
#    连同会话一起杀了,当次已算完的结果全部消失。故按【当前可用内存】取一部分。
DUCKDB_MEM_FRACTION = 0.35       # 取可用内存的比例(留足余量给系统与其它进程)
DUCKDB_MEM_CAP_GB = 8.0          # 硬上限
DUCKDB_MEM_FLOOR_GB = 2.0        # 硬下限(低于此宁可让 DuckDB 溢写也不再让步)
DUCKDB_THREADS = 8               # 本机 16 线程;留一半给系统

RAW = str(PROJECT_ROOT / "11-collector" / "data" / "raw" / "*" / "*.parquet")
REG = str(PROJECT_ROOT / "11-collector" / "data" / "registry" / "*.parquet")

# §9 第 11 项要复现的基线(2026-08-18 实测,写进单子 §3.2 表)
EXPECT_SETTLED_RATE = {"D": 99.3, "V": 99.7}


# ─────────────────────────────────────────────────────────────────────────────
# §4 符号铁律 —— 判定式在全脚本中【只允许存在这一份】
# ⛔ 不许把下面的表达式复制到任何别处(CLAUDE.md:照抄结构而不抽象,已犯 3 次)
# ─────────────────────────────────────────────────────────────────────────────
def sql_win(idx: str = "t.outcome_index", res: str = "m.resolved_outcome") -> str:
    """win(idx) = resolved_outcome (idx==0) / 1 - resolved_outcome (idx==1)。

    ⚠️ resolved_outcome 是【0 号结果的最终赔付值】,不是赢家编号。
    见单子 §4 与 memory: pitfall-resolved-outcome-is-payout。
    """
    return f"CASE WHEN {idx} = 0 THEN {res} ELSE 1.0 - {res} END"


def sql_dir(side: str = "t.side") -> str:
    """dir = +1 (BUY) / -1 (SELL)。"""
    return f"CASE WHEN {side} = 'BUY' THEN 1.0 ELSE -1.0 END"


def sql_race_group(slug: str = "m.slug") -> str:
    """比赛组键(§6 红线 B 的 bootstrap 分块单位)。

    实测(2026-08-18):registry 的 event_slug 与 slug 逐行相同、一市场一值,
    不携带事件分组信息。但 slug 本身有结构:同一场比赛的多个玩法共享
    `联赛-队1-队2-YYYY-MM-DD` 前缀(抽查 codmw-100t-bos-2026-08-05 下 5 腿
    确为同一场比赛的 game1/game2/game3/让分/大小球)。
    含日期模式的占已结算市场 73.2%;平均 2.74 腿/组(memory 记载 2.60,数据增长所致)。
    无日期模式者(政治/天气/股价)各自独立成组 —— 保守,不会低估相关。
    """
    return (r"CASE WHEN regexp_matches(%s, '-\d{4}-\d{2}-\d{2}') "
            r"THEN regexp_extract(%s, '^(.*?-\d{4}-\d{2}-\d{2})', 1) ELSE %s END"
            % (slug, slug, slug))


# ─────────────────────────────────────────────────────────────────────────────
# 出声计数(母单铁律 3:任何降级/剔除/回退必须出声计数)
def sql_end_ts(col: str = "end_date") -> str:
    """把注册表 end_date 文本解析成时刻。⛔ 全脚本只允许存在这一份。

    两种格式都要认:2026-08-19 实测 167,487 个市场里 887 个(0.53%)带毫秒
    (形如 ...T03:59:59.999Z)。只认不带毫秒那种时它们返回 NULL,会被无声归入
    「闸二剔除」,与真正超窗的混在一起(单子 §0.5 修订三)。
    """
    return f"try_strptime({col}, ['%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%fZ'])"


def sql_end_unparseable(col: str = "end_date") -> str:
    """end_date 非空却两种格式都解析不了 —— 必须单独数,不许混进闸二剔除。

    ⛔ 与「end_date 本来就为空」是两回事,不许合并成一格。
    """
    return f"({col} IS NOT NULL AND {sql_end_ts(col)} IS NULL)"


def sql_settle_gate(days: int, ts: str = "timestamp", end: str = "end_date") -> str:
    """闸二(§3.2):成交时刻 -> 该市场 end_date 在 0~days 天内。

    ⛔ 全脚本只允许存在这一份。修订前它在 build_window / check_settled_rate /
    g5_missingness 各有一份副本 —— 而 check_settled_rate 存在的唯一目的就是发现
    口径漂移,它自己的闸门是副本,自己漂了察觉不到自己漂。
    """
    return (f"date_diff('day', CAST(to_timestamp({ts}) AS DATE),"
            f" CAST({sql_end_ts(end)} AS DATE)) BETWEEN 0 AND {days}")


def sql_det_order(key: str, salt: int) -> str:
    """确定性「随机」排序键。⛔ 全脚本只允许存在这一份。

    🔴 为什么不能用 random():2026-08-19 实测复现 —— 并行 GROUP BY 建出来的表
    【物理行序每次都不同】(threads=8 时三次跑表头完全不同),而 ORDER BY random()
    是把固定的随机数序列【按行序】贴上去的 ⇒ 种子固定也会抽到不同的行。
    实证后果:同一份数据两次跑,§8 一次 P1 亮绿 0/20(PASS)、一次 4/20(FAIL)。
    用 64 位哈希做排序键则与行序、线程数都无关。
    """
    return f"hash({key} || '#{salt}')"


def sql_det_unit(key: str, salt: int) -> str:
    """确定性 [0,1) 抽样值(用于 Bernoulli 一类的比较,不用于排序)。"""
    return f"((hash({key} || '#{salt}') % 1000000000) / 1000000000.0)"


def sql_num(size: str = "size", dir_: str = "dir", win: str = "win",
            price: str = "price") -> str:
    """§5 主口径分子:资金加权毛收益的分子。⛔ 全脚本只允许存在这一份。

    (2026-08-18 review 第 3 条:此式原本在 rank_and_verdict / R3 / R4 各有副本,
     母单要求 R3/R4 与主判决同口径,而副本漂了没有任何机制报得出来。)
    """
    return f"sum({size} * {dir_} * ({win} - {price}))"


def sql_den(size: str = "size", price: str = "price") -> str:
    """§5 主口径分母。⛔ 与 sql_num 成对,只允许存在这一份。"""
    return f"sum({size} * {price})"


def sql_window_where(win: tuple[str, str], ts: str = "t.timestamp",
                     dt: str = "t.dt", use_dt: bool = False) -> str:
    """窗口过滤(§2:半开区间,UTC,按真实成交时刻)。

    use_dt=True 改用 dt 分区值,【仅供 D3 诊断】—— 实测 dt 全湖 37.54% 不等于
    真实成交日,拉平后仍错约 10%(单子 §0.2)。
    """
    a, b = win
    if use_dt:
        return f"{dt} >= DATE '{a[:10]}' AND {dt} < DATE '{b[:10]}'"
    return (f"to_timestamp({ts}) >= TIMESTAMP '{a}' "
            f"AND to_timestamp({ts}) < TIMESTAMP '{b}'")


# ─────────────────────────────────────────────────────────────────────────────
class Ledger:
    """逐级剔除台账。首尾必须对账:起始 = Σ各级剔除 + 最终纳入。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.starts: dict[str, int] = {}      # 各窗起始笔数,由 build_window 登记
        self.notes: list[dict] = []           # 账外事实(不参与对账算术,但必须落盘)

    def step(self, window: str, name: str, before: int, after: int, note: str = "") -> None:
        self.rows.append({"window": window, "step": name, "before": before,
                          "after": after, "dropped": before - after,
                          "drop_pct": round((before - after) / before * 100, 4) if before else 0.0,
                          "note": note})
        print(f"  [{window}] {name:<34} {before:>10,} -> {after:>10,} "
              f"(剔除 {before-after:>9,} = {(before-after)/before*100 if before else 0:>5.2f}%) {note}")

    def note(self, window: str, key: str, value) -> None:
        """记一条【不参与对账算术】的事实(如畸形 end_date 笔数)。

        单独存在的理由:塞进 step() 会污染首尾对账;只 print 则报表里看不见 ——
        「记录事实 vs 使用事实只接一头」已犯 5 次,这里两头都接。
        """
        self.notes.append({"window": window, "key": key, "value": value})

    def set_start(self, window: str, n: int) -> None:
        """登记该窗起点。⛔ 与 reconcile 成对存在 —— 少了它对账无从做起。"""
        self.starts[window] = n

    def reconcile(self, window: str, start: int, final: int) -> bool:
        """首尾对账:起始 - Σ各级剔除 == 最终纳入。"""
        drops = sum(r["dropped"] for r in self.rows if r["window"] == window)
        ok = (start - drops == final)
        mark = "PASS" if ok else "🔴 FAIL"
        print(f"  [{window}] 首尾对账: {start:,} - Σ剔除 {drops:,} = {start-drops:,} "
              f"vs 最终 {final:,}  [{mark}]")
        return ok

    def dump(self) -> list[dict]:
        return self.rows


def _mem_budget_gb() -> float:
    """DuckDB 内存预算 —— 依据【现网当前可用内存】,不是物理总量。

    读 /proc/meminfo。异常必须按实际会抛的来接:文件缺失/无权限 -> OSError;
    行格式变了 -> int() 抛 ValueError、split() 越界抛 IndexError。
    取不到就退到地板值(宁可慢、不可再把机器打爆)。
    """
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail_gb = int(line.split()[1]) / 1024 / 1024
                    break
            else:
                return DUCKDB_MEM_FLOOR_GB
    except (OSError, ValueError, IndexError):
        return DUCKDB_MEM_FLOOR_GB
    return max(DUCKDB_MEM_FLOOR_GB, min(DUCKDB_MEM_CAP_GB, avail_gb * DUCKDB_MEM_FRACTION))


def save(out: dict, name: str = "results.json") -> None:
    """算完一步就落盘。

    ⚠️ 2026-08-18:原来只在 main() 最后一行写一次 —— 中途被 OOM 杀掉时,
    前面算完的九个网格格子跟着一起消失。多写几次的成本远低于重跑一次。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def die(msg: str) -> None:
    """中止 —— 单子要求「不过则中止,不许降级继续」。"""
    print(f"\n🔴 中止: {msg}", file=sys.stderr)
    sys.exit(1)


def connect():
    import duckdb
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")   # ⚠️ 必须;默认本地时区(JST)会静默得出错误结论
    con.execute(f"SET memory_limit='{_mem_budget_gb():.1f}GB'")
    con.execute(f"SET threads={DUCKDB_THREADS}")
    tmp = OUT_DIR / ".duckdb_tmp"       # ⛔ 绝对路径;默认 '.tmp' 会随 cwd 漂移
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory='{tmp}'")
    con.execute(f"""
        CREATE OR REPLACE VIEW mkt AS SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY condition_id ORDER BY snapshot_at DESC) rn
          FROM read_parquet('{REG}')) WHERE rn = 1
    """)
    return con


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 1:启动自检(§2 封窗等待期 + §4 符号铁律)—— 不过则中止
# ─────────────────────────────────────────────────────────────────────────────
def selfcheck_seal_window() -> dict:
    """§2 硬约束:验证窗末日 + 24h 之内不得开跑。

    由来(单子 §0.3):V1 于 2026-08-11 03:40:58 UTC 测量,而 V 窗末日(08-10)的
    24h 采集窗当时尚未关闭 ⇒ 窗末那天被系统性削薄 2,517 笔。
    """
    import datetime as dtm
    v_end = dtm.datetime.fromisoformat(WIN_V[1]).replace(tzinfo=dtm.UTC)
    earliest = v_end.timestamp() + SEAL_WAIT_SEC
    now = time.time()
    ok = now >= earliest
    print(f"  验证窗末 {v_end.isoformat()} + {SEAL_WAIT_SEC}s "
          f"=> 最早可跑 {dtm.datetime.fromtimestamp(earliest, dtm.UTC).isoformat()}")
    print(f"  现在      {dtm.datetime.fromtimestamp(now, dtm.UTC).isoformat()}  "
          f"[{'PASS' if ok else '🔴 FAIL'}]")
    if not ok:
        die(f"封窗等待期未满,还需 {earliest - now:.0f} 秒。提前跑 = 验证窗末日被削薄。")
    return {"v_window_end": WIN_V[1], "earliest_run_utc": earliest, "run_at": now, "pass": ok}


def selfcheck_sign(con) -> dict:
    """§4 符号铁律运行时自检。

    🔴 口径写死:在【全湖】上取每个 (condition_id, outcome_index) 的最后一笔成交。
    ⛔ 不得在窗口内取 —— 窗口内的「最后一笔」不是市场真正的最后一笔,会让本自检假红
       (单子 §0.4 实例 2:窗口内算得 eq 中位 0.12 -> 假 FAIL,全湖为 0.0500)。

    主判据用【分离度】而非绝对阈值:若符号读反,两组中位数必然对调 ⇒ 差值变号 ⇒ 必 FAIL。
    V1 的两条绝对阈值仍计算并公布,但不作判决依据(它一部分测的是「价格收敛得多彻底」,
    会随市场周期构成漂移:全湖 eq=0.05,而仅 8-30 天周期盘 eq=0.2486)。
    """
    r = con.execute(f"""
        WITH last AS (
          SELECT t.outcome_index, m.resolved_outcome, t.price,
                 row_number() OVER (PARTITION BY t.condition_id, t.outcome_index
                                    ORDER BY t.timestamp DESC) rn
          FROM read_parquet('{RAW}') t JOIN mkt m USING (condition_id)
          WHERE m.resolved_outcome IS NOT NULL)
        SELECT median(CASE WHEN outcome_index = resolved_outcome THEN price END) eq,
               median(CASE WHEN outcome_index <> resolved_outcome THEN price END) ne,
               count(*) FILTER (WHERE outcome_index = resolved_outcome) n_eq,
               count(*) FILTER (WHERE outcome_index <> resolved_outcome) n_ne
        FROM last WHERE rn = 1
    """).fetchone()
    eq, ne, n_eq, n_ne = r
    sep = ne - eq
    ok = sep > SIGN_SEPARATION_MIN
    print(f"  median(idx==resolved) = {eq:.4f}  (n={n_eq:,})   [V1 旧阈值 <0.10: "
          f"{'满足' if eq < 0.10 else '不满足 — 仅公布,不作判决'}]")
    print(f"  median(idx!=resolved) = {ne:.4f}  (n={n_ne:,})   [V1 旧阈值 >0.90: "
          f"{'满足' if ne > 0.90 else '不满足 — 仅公布,不作判决'}]")
    print(f"  ⭐主判据 分离度 ne-eq  = {sep:+.4f}  (需 > +{SIGN_SEPARATION_MIN})  "
          f"[{'PASS' if ok else '🔴 FAIL'}]")
    if not ok:
        die("符号铁律自检未过 —— 疑似 resolved_outcome 语义反转,结论会静默变号。")
    return {"median_eq": eq, "median_ne": ne, "separation": sep,
            "n_eq": n_eq, "n_ne": n_ne, "pass": ok,
            "v1_abs_eq_lt_010": bool(eq < 0.10), "v1_abs_ne_gt_090": bool(ne > 0.90)}


# ─────────────────────────────────────────────────────────────────────────────
# 过滤链(§3)—— 逐级出声计数,首尾对账
# ─────────────────────────────────────────────────────────────────────────────
def build_window(con, tag: str, win: tuple[str, str], led: Ledger,
                 settle_gate_days: int | None, quiet: bool = False,
                 use_dt: bool = False, ingest_gate: bool = True) -> int:
    """把某窗的可用成交物化成表 s2_<tag>,返回最终笔数。

    settle_gate_days=None 表示不设闸二(敏感性与 G5 检验用)。
    use_dt=True 改用 dt 分区值切窗(= V1 坐标),【仅供 D3 诊断】。
    """
    base_where = sql_window_where(win, use_dt=use_dt)
    # §0.7:闸一做成开关,用于「闸一开 vs 关」这对登记过的稳健性双臂。
    # 关掉时条件恒真 —— 台账仍照常出声(剔除 0 笔),首尾对账才不会失衡。
    g1_where = (f"t.ingested_at - t.timestamp < {INGEST_GATE_SEC}" if ingest_gate else "TRUE")

    def cnt(sql: str) -> int:
        return con.execute(sql).fetchone()[0]

    # L0 窗内原始成交(§2:按 timestamp 切,⛔ 不用 dt —— dt 全湖 37.54% 错位)
    n0 = cnt(f"SELECT count(*) FROM read_parquet('{RAW}') t WHERE {base_where}")
    if not quiet:
        print(f"  [{tag}] L0 窗内原始成交(按 timestamp UTC)  {n0:,}")
        led.set_start(tag, n0)

    # L1 闸一:采集延迟 < 24h
    n1 = cnt(f"SELECT count(*) FROM read_parquet('{RAW}') t "
             f"WHERE {base_where} AND {g1_where}")
    if not quiet:
        led.step(tag, "L1 闸一·采集延迟<24h" if ingest_gate else "L1 闸一·【已关闭】(稳健性臂)",
                 n0, n1)

    con.execute(f"""
        CREATE OR REPLACE TABLE s1_{tag} AS
        SELECT t.transaction_hash, t.proxy_wallet, t.condition_id, t.outcome_index,
               t.side, t.size, t.price, t.timestamp,
               m.resolved_outcome, m.market_class, m.hft_suspect, m.end_date, m.slug
        FROM read_parquet('{RAW}') t JOIN mkt m USING (condition_id)
        WHERE {base_where} AND {g1_where}
    """)
    n1b = cnt(f"SELECT count(*) FROM s1_{tag}")
    if n1b != n1 and not quiet:
        led.step(tag, "L1b 注册表 join 丢失", n1, n1b, "⚠️ 成交的市场不在注册表")

    # L2 市场层:hft_crypto / hft_suspect
    n2 = cnt(f"SELECT count(*) FROM s1_{tag} WHERE market_class='event' AND NOT hft_suspect")
    if not quiet:
        led.step(tag, "L2 市场层·hft_crypto/suspect", n1b, n2)

    # L3 闸二:结算延迟闸
    gate = "" if settle_gate_days is None else f" AND {sql_settle_gate(settle_gate_days)}"

    # 畸形 end_date 出声计数(§0.5 修订三)。⛔ 必须在闸二【之前】数 ——
    # 闸二一旦把它们判 false,就与真正超窗的混为一谈,再也分不开了。
    n_bad_end = cnt(f"SELECT count(*) FROM s1_{tag} "
                    f"WHERE market_class='event' AND NOT hft_suspect "
                    f"AND {sql_end_unparseable('end_date')}")
    if not quiet:
        led.note(tag, "end_date 解析失败笔数", n_bad_end)
        if n_bad_end:
            print(f"  🔴 [{tag}] end_date 解析失败 {n_bad_end:,} 笔 —— 它们会被闸二当作超窗剔除,"
                  f"须查明是哪一类市场(单子 §0.5 修订三)")
        else:
            print(f"  [{tag}] end_date 解析失败 0 笔  [PASS]")
    n3 = cnt(f"SELECT count(*) FROM s1_{tag} WHERE market_class='event' AND NOT hft_suspect{gate}")
    if not quiet:
        led.step(tag, f"L3 闸二·结算延迟 0~{settle_gate_days}天" if settle_gate_days is not None
                 else "L3 闸二·未设(敏感性)", n2, n3)

    # L4 已结算(官方真值)
    con.execute(f"""
        CREATE OR REPLACE TABLE s2_{tag} AS
        SELECT *, {sql_win('outcome_index', 'resolved_outcome')} AS win,
               {sql_dir('side')} AS dir, {sql_race_group('slug')} AS race_group
        FROM s1_{tag}
        WHERE market_class='event' AND NOT hft_suspect{gate} AND resolved_outcome IS NOT NULL
    """)
    n4 = cnt(f"SELECT count(*) FROM s2_{tag}")
    if not quiet:
        led.step(tag, "L4 未结算(resolved_outcome IS NULL)", n3, n4)
    return n4


# ─────────────────────────────────────────────────────────────────────────────
# 主判决(§6)与其组件
# ─────────────────────────────────────────────────────────────────────────────
def apply_wallet_layer(con, led: Ledger, min_trades: int, drop_bots: bool,
                       quiet: bool = False) -> dict:
    """§3.3 钱包层:泄漏剔除 -> 机器人剔除 -> 门槛。建 wD / wV 两表。"""
    c = lambda q: con.execute(q).fetchone()[0]
    n_v0 = c("SELECT count(*) FROM s2_V")

    # L5 跨窗同钱包同市场泄漏(仅 V 窗;单子偏离一的替代措施,预期 ~1.59%)
    con.execute("""
        CREATE OR REPLACE TABLE v_noleak AS
        SELECT v.* FROM s2_V v WHERE NOT EXISTS (
          SELECT 1 FROM s2_D d
          WHERE d.proxy_wallet = v.proxy_wallet AND d.condition_id = v.condition_id)
    """)
    n_v1 = c("SELECT count(*) FROM v_noleak")
    if not quiet:
        led.step("V", "L5 跨窗同钱包同市场泄漏", n_v0, n_v1, f"预期约 {EXPECT_LEAK_PCT}%")

    # L6 疑似机器人(D 窗每市场平均成交 >= 20 笔),两窗同时剔
    con.execute(f"""
        CREATE OR REPLACE TABLE bots AS
        SELECT proxy_wallet, count(*) * 1.0 / count(DISTINCT condition_id) AS tpm
        FROM s2_D GROUP BY 1
        HAVING count(*) * 1.0 / count(DISTINCT condition_id) >= {BOT_TRADES_PER_MKT}
    """)
    n_bots = c("SELECT count(*) FROM bots")
    if drop_bots:
        con.execute("CREATE OR REPLACE TABLE d_nobot AS SELECT * FROM s2_D "
                    "WHERE proxy_wallet NOT IN (SELECT proxy_wallet FROM bots)")
        con.execute("CREATE OR REPLACE TABLE v_nobot AS SELECT * FROM v_noleak "
                    "WHERE proxy_wallet NOT IN (SELECT proxy_wallet FROM bots)")
    else:
        con.execute("CREATE OR REPLACE TABLE d_nobot AS SELECT * FROM s2_D")
        con.execute("CREATE OR REPLACE TABLE v_nobot AS SELECT * FROM v_noleak")
    n_d1, n_v2 = c("SELECT count(*) FROM d_nobot"), c("SELECT count(*) FROM v_nobot")
    if not quiet:
        n_d0 = c("SELECT count(*) FROM s2_D")
        led.step("D", "L6 疑似机器人钱包", n_d0, n_d1,
                 f"{n_bots:,} 个钱包(预期约 {EXPECT_BOTS})")
        led.step("V", "L6 疑似机器人钱包", n_v1, n_v2, f"同一批钱包")

    # 分母防护:size*price <= 0 的成交(price=0 会让资金加权分母失效)
    n_bad = c("SELECT count(*) FROM d_nobot WHERE size*price <= 0") + \
            c("SELECT count(*) FROM v_nobot WHERE size*price <= 0")
    if n_bad and not quiet:
        print(f"  ⚠️ 分母非正(size*price<=0)的成交 {n_bad:,} 笔 —— 出声计数,保留在分子分母中"
              f"(sum 层面无害,仅每笔等权口径 S4 需另行防护)")

    # L7 门槛:两窗各 >= min_trades 笔
    con.execute(f"""
        CREATE OR REPLACE TABLE keep_w AS
        SELECT d.proxy_wallet FROM
          (SELECT proxy_wallet, count(*) n FROM d_nobot GROUP BY 1 HAVING count(*) >= {min_trades}) d
        JOIN
          (SELECT proxy_wallet, count(*) n FROM v_nobot GROUP BY 1 HAVING count(*) >= {min_trades}) v
        ON d.proxy_wallet = v.proxy_wallet
    """)
    n_keep = c("SELECT count(*) FROM keep_w")
    con.execute("CREATE OR REPLACE TABLE wD AS SELECT * FROM d_nobot "
                "WHERE proxy_wallet IN (SELECT proxy_wallet FROM keep_w)")
    con.execute("CREATE OR REPLACE TABLE wV AS SELECT * FROM v_nobot "
                "WHERE proxy_wallet IN (SELECT proxy_wallet FROM keep_w)")
    n_d2, n_v3 = c("SELECT count(*) FROM wD"), c("SELECT count(*) FROM wV")
    if not quiet:
        led.step("D", f"L7 门槛(两窗各>={min_trades}笔)", n_d1, n_d2, f"存活 {n_keep:,} 个钱包")
        led.step("V", f"L7 门槛(两窗各>={min_trades}笔)", n_v2, n_v3, f"存活 {n_keep:,} 个钱包")

    # G1(§9 第 10 项 = 母单):两窗已结算市场数。基准取【最终分析样本】wD/wV ——
    # 比在 s2 层面数更严,而子单只可加严;且它才是判决真正依据的那份样本。
    n_mkt_d = c("SELECT count(DISTINCT condition_id) FROM wD")
    n_mkt_v = c("SELECT count(DISTINCT condition_id) FROM wV")

    out = {"wallets_kept": n_keep, "bots_found": n_bots, "leak_dropped": n_v0 - n_v1,
           "leak_pct": round((n_v0 - n_v1) / n_v0 * 100, 4) if n_v0 else 0.0,
           "trades_D": n_d2, "trades_V": n_v3, "nonpositive_denom": n_bad,
           "markets_D": n_mkt_d, "markets_V": n_mkt_v}

    # ⭐ 主口径跑完必过的两道出口闸。放在这里而不是 main(),是因为
    #    main() 里「记得调一下」正是 2026-08-18 review 抓到的那个洞:
    #    reconcile() 写了、测了、从来没被调用过(「只接一头」第 5 次)。
    #    网格格子(quiet=True)是诊断,不参与主口径记账,故不在此设闸。
    if not quiet:
        for tag, final in (("D", n_d2), ("V", n_v3)):
            if tag not in led.starts:
                die(f"[{tag}] 台账没有起点 —— build_window 没在出声模式下跑过,对账无从做起。")
            if not led.reconcile(tag, led.starts[tag], final):
                die(f"[{tag}] 首尾对账不平 —— 有一级剔除没有出声计数(静默丢样本)。")
        print(f"  G1 已结算市场数: D={n_mkt_d:,} / V={n_mkt_v:,}  "
              f"(各须 >= {G1_MIN_MARKETS})")
        for tag, n_mkt in (("D", n_mkt_d), ("V", n_mkt_v)):
            if n_mkt < G1_MIN_MARKETS:
                die(f"[{tag}] 已结算市场数 {n_mkt:,} < 母单 G1 下限 {G1_MIN_MARKETS} "
                    f"—— 统计功效不足,不许在这个样本上下判决(单子 §9 第 10 项)。")
        # §9 第 6、7 项:出声计数与预期比对。焊在这个出口而不是 main(),同上理由。
        exp = check_expectations(out, led)
        out["expectations"] = exp
        if exp["pass"]:
            print(f"  出声计数核对: 泄漏 {out['leak_pct']:.2f}% (预期 {EXPECT_LEAK_PCT}%) / "
                  f"机器人 {out['bots_found']:,} 个 (预期 {EXPECT_BOTS})  [PASS]")
        else:
            for f in exp["failures"]:
                print(f"  🔴 出声计数偏离预期,须查明:{f}")
    return out


def rank_and_verdict(con, top_frac: float, equal_weight: bool = False,
                     d_table: str = "wD", v_table: str = "wV",
                     top_override: str | None = None) -> dict:
    """§6 主判决:D 窗排名 -> 取前 top_frac -> 算 V 窗组合层面 R_V。"""
    # §5 主口径:资金加权毛收益率。等权口径(S4)仅作旁证。
    if equal_weight:
        num_d = "sum(dir * (win - price) / nullif(price, 0))"
        den_d = "count(*) FILTER (WHERE price > 0)"
    else:
        num_d = sql_num()
        den_d = sql_den()

    con.execute(f"""
        CREATE OR REPLACE TABLE rank_d AS
        SELECT proxy_wallet, {num_d} AS num, {den_d} AS den,
               CASE WHEN {den_d} > 0 THEN {num_d} / {den_d} END AS r_d
        FROM {d_table} GROUP BY 1
    """)
    n_all = con.execute("SELECT count(*) FROM rank_d WHERE r_d IS NOT NULL").fetchone()[0]
    n_top = max(1, int(round(n_all * top_frac)))
    if top_override:
        con.execute(f"CREATE OR REPLACE TABLE top_w AS SELECT * FROM {top_override}")
    else:
        con.execute(f"""
            CREATE OR REPLACE TABLE top_w AS
            SELECT proxy_wallet, r_d FROM rank_d WHERE r_d IS NOT NULL
            ORDER BY r_d DESC LIMIT {n_top}
        """)
    # 高手组在 V 窗:聚合到比赛组层面(bootstrap 的分块单位)
    con.execute(f"""
        CREATE OR REPLACE TABLE vgrp AS
        SELECT race_group, {num_d} AS num, {den_d} AS den
        FROM {v_table} WHERE proxy_wallet IN (SELECT proxy_wallet FROM top_w)
        GROUP BY 1
    """)
    r = con.execute("SELECT sum(num), sum(den), count(*) FROM vgrp").fetchone()
    num, den, n_grp = r
    r_v = (num / den) if den else None
    return {"n_wallets_ranked": n_all, "n_top": n_top,
            "r_v": r_v, "r_v_pp": (r_v * 100) if r_v is not None else None,
            "num": num, "den": den, "n_race_groups": n_grp}


def block_bootstrap(con, n_iter: int = BOOTSTRAP_N, seed: int = 12345) -> dict:
    """§6 红线 B:按比赛组分块 bootstrap,取 2.5% 分位数。"""
    import numpy as np
    arr = con.execute("SELECT num, den FROM vgrp").fetchnumpy()
    num, den = np.asarray(arr["num"], dtype=float), np.asarray(arr["den"], dtype=float)
    n = len(num)
    if n == 0:
        # ⚠️ 调用方无条件读 q025_pp;只返两个字段会在网格中段抛 KeyError,
        #    连带把这次已算完的格子全带走(2026-08-18 review,已实测复现)。
        return {"q025": None, "q025_pp": None, "n_groups": 0,
                "boot_mean_pp": None, "boot_q975_pp": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_iter, n))
    boot = num[idx].sum(axis=1) / np.where(den[idx].sum(axis=1) == 0, np.nan, den[idx].sum(axis=1))
    q = float(np.nanquantile(boot, BOOTSTRAP_Q))
    return {"q025": q, "q025_pp": q * 100, "n_groups": n,
            "boot_mean_pp": float(np.nanmean(boot) * 100),
            "boot_q975_pp": float(np.nanquantile(boot, 1 - BOOTSTRAP_Q) * 100)}


def judge(r_v_pp: float | None, q025_pp: float | None, g5_pass: bool,
          null_p95: float | None) -> tuple[bool, str]:
    """§6.1 红线 A(幅度)、B(显著),加 §0.5 修订二的 G5 否决权。三条都要过。

    ⛔ g5_pass 【必填,不给默认值】。给默认值 = 2026-08-18 那个洞的翻版
    (reconcile 写了测了从来没人调):以后新加的调用点忘了传,会静默按「G5 过了」算。
    现在漏传直接 TypeError,想写错都写不出来。
    """
    if r_v_pp is None or q025_pp is None or null_p95 is None:
        return False, "🔴 数据不足"
    a0 = r_v_pp >= REDLINE_A_PP          # 绝对下限(保留,只可加严)
    a1 = r_v_pp > null_p95               # ⭐ 主红线:须【超过】零分布分位
    b = q025_pp > 0
    return (a0 and a1 and b and g5_pass), (
        f"A·置换 {r_v_pp:+.4f}pp > 零分布{int(PERM_Q*100)}分位 {null_p95:+.4f}pp: "
        f"{'PASS' if a1 else 'FAIL'} | "
        f"A0·绝对下限 >= +{REDLINE_A_PP}pp: {'PASS' if a0 else 'FAIL'} | "
        f"B·显著 2.5%分位 {q025_pp:+.4f}pp > 0: {'PASS' if b else 'FAIL'} | "
        f"G5·缺失与结果不相关: {'PASS' if g5_pass else 'FAIL(否决 ⇒ 不可采信)'}")


def check_expectations(wl: dict, led: "Ledger | None" = None) -> dict:
    """§9 第 6、7 项:出声计数必须与预期【比对并判定】,不是并排 print 完就算。

    修订前这两处只是把期望值和实际值一起印出来,埋在几十行输出里没有 PASS/FAIL
    (2026-08-18 review 第 5 条)。单子原文是「偏离预期须查明」——
    没有判定,就没有人会知道该去查。
    """
    fails: list[str] = []
    leak = wl.get("leak_pct")
    if leak is None:
        fails.append("泄漏率缺失 —— 该算的没算出来")
    else:
        dev = round(abs(leak - EXPECT_LEAK_PCT), 9)
        if dev > LEAK_TOLERANCE_PP:
            fails.append(f"泄漏率 {leak:.2f}% vs 预期 {EXPECT_LEAK_PCT}%"
                         f"(偏离 {dev:.2f}pp > {LEAK_TOLERANCE_PP}pp)")
    bots = wl.get("bots_found")
    if bots is None:
        fails.append("疑似机器人钱包数缺失 —— 该算的没算出来")
    else:
        rel = round(abs(bots - EXPECT_BOTS) / EXPECT_BOTS, 12)
        if rel > BOTS_TOLERANCE_FRAC:
            fails.append(f"疑似机器人 {bots:,} 个 vs 预期 {EXPECT_BOTS} 个"
                         f"(相对偏离 {rel*100:.1f}% > {BOTS_TOLERANCE_FRAC*100:.0f}%)")
    # end_date 解析失败:两种格式都覆盖之后,任何一笔都是缺陷(接口格式又变了)。
    # ⚠️ 2026-08-19 review 抓到:此前它只 print 了个红字、没有任何判定 ——
    #    正是本函数存在的理由那个毛病,换个地方又犯了一遍。
    n_bad = sum(x["value"] for x in (led.notes if led else [])
                if x["key"] == "end_date 解析失败笔数")
    if n_bad:
        fails.append(f"end_date 解析失败 {n_bad:,} 笔(容差 0 笔)—— 注册表格式可能又变了")
    return {"pass": not fails, "failures": fails, "leak_pct": leak,
            "bots_found": bots, "end_date_parse_failures": n_bad}


def final_verdict_lines(out: dict) -> list[str]:
    """把结论渲染成【唯一一处】结论行。G5 的否决权就焊在这里。

    单独抽出来的理由:main 里自己拼一份 print,等于给 G5 否决留了一条绕行路。
    """
    # ⛔ 缺键即报错,不给默认值。judge() 为此把 g5_pass 设成必填,
    #    这里若用 .get(..., True) 兜底,等于把同一个洞在一步之隔处又开了一次
    #    (2026-08-19 review 抓到)。
    for k in ("main", "g5_missingness", "expectations"):
        if k not in out:
            raise ValueError(f"final_verdict_lines 缺少 '{k}' —— 不许按『过了』渲染结论")
    m, g5, exp = out["main"], out["g5_missingness"], out["expectations"]
    g5_ok = g5["pass"]
    lines = ["=" * 78, f"主判决: {m.get('detail', '(缺)')}"]
    if not g5_ok:
        lines.append("🔴 G5 不过 —— 缺失与结果相关,本次结论【不可采信】(单子 §0.5 修订二):")
        lines += [f"     · {f}" for f in g5.get("failures", [])]
    if not exp["pass"]:
        lines.append("🔴 出声计数偏离预期,【须查明】(单子 §9 第 6、7 项):")
        lines += [f"     · {f}" for f in exp.get("failures", [])]
    unaudited = [g["label"] for g in out.get("grid", [])
                 if g.get("green") and not g.get("g5_audited", True)]
    if unaudited:
        lines.append("⚠️ 下列格子亮了绿灯,但它们的样本【未经 G5 审查】,不得单独引用:")
        lines += [f"     · {x}" for x in unaudited]
    green = bool(m.get("green")) and g5_ok
    if green:
        tail = "🟢 毛利层未被证伪(仅有资格进第二关,不是 edge)"
    elif g5_ok:
        tail = "🔴 证伪"
    else:
        tail = "⛔ 判决作废 —— 缺失与结果相关,红线过不过都不算数"
    lines += [f"⇒ {tail}", "=" * 78]
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 2:§8 坏数据自检 —— P1/P2 各 20 次种子,亮绿次数须 <= 1/20
# ⛔ 不亮红则立即停工修判据,不许去看真实结果
# ─────────────────────────────────────────────────────────────────────────────
def _shuffle_win_within_price_band(con, src: str, dst: str, salt: int = 0) -> None:
    """【单子 §8 原做法】在入场价档内随机重排 win。价格档沿用 §6 R4 分层。

    🔴 2026-08-18 实测:此做法【本身会凭空注入约 +2.2pp 正收益】,不可作为 P2 主实现。
    根因:档内 shuffle 后每笔的 E[win] 变成该档平均 p̄,与自身 price 脱钩;
    而低价成交的 size 系统性更大(同样的钱买更多份)⇒ size 加权均价 < p̄ ⇒ 分母效应。
    实测(V 窗):<0.2 档 p̄=0.1006 vs size加权均价=0.0797 ⇒ 该档单独就 +26.1pp。
    ⇒ 保留此函数【仅作对照与留痕】,证明修订不是为了让闸门过。
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE {dst} AS
        WITH b AS (SELECT *, CASE WHEN price < 0.2 THEN 0
                                  WHEN price <= 0.8 THEN 1 ELSE 2 END AS _band FROM {src}),
             a AS (SELECT *, row_number() OVER (PARTITION BY _band
                     ORDER BY {sql_det_order('transaction_hash', salt)}) AS _rn FROM b),
             s AS (SELECT _band AS _b2, win AS _w,
                          row_number() OVER (PARTITION BY _band
                     ORDER BY {sql_det_order('transaction_hash', salt + 500000)}) AS _rn2 FROM b)
        SELECT a.* REPLACE (s._w AS win)
        FROM a JOIN s ON a._band = s._b2 AND a._rn = s._rn2
    """)


def _resample_win_from_price(con, src: str, dst: str, salt: int = 0) -> None:
    """P2【修订实现,2026-08-18】:win ~ Bernoulli(price)。

    为什么改(实测证据,非推断):
      · 单子 §8 原写「在入场价档内随机重排 win」。实测该做法系统性注入 +2.2pp
        (三次种子 +2.18 / +2.27 / +2.39),⇒ P2 必然亮绿,对判据好坏【没有分辨力】。
        这正是 CLAUDE.md 那一问打中的:「如果判据现在就是坏的,我看到的会有什么不同?」
        —— 答案是【没有不同】,原 P2 无论如何都绿。
      · 真实数据里市场定价基本无偏:实测 E[win|price] 与 price 偏差最大仅 +0.0168。
        任何「砍掉真实输赢」的做法都必须保住这个性质,否则砍掉的不是输赢,
        而是把定价改成了严重有偏 —— 那是在造一个不存在的世界(静默失败第 9 条)。
      · Bernoulli(price) 保住 E[win|price] = price ⇒ E[edge] = 0。
        实测三次种子 +0.01 / -0.67 / -0.16pp,围绕 0。

    保留:价格结构、下注结构(size/side)、钱包身份、市场归属。砍掉:真实输赢。
    ⚠️ 已知限制:逐笔独立抽样,不保市场级一致性(同市场同 outcome 的成交会拿到不同 win)。
       这使比赛组内相关被削弱 ⇒ bootstrap 方差偏小 ⇒ 更容易亮绿 ⇒ 本检验因此【更严格】,
       方向上不利于「让闸门通过」,故可接受。
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE {dst} AS
        SELECT * REPLACE (CASE WHEN {sql_det_unit('transaction_hash', salt)} < price
                          THEN 1.0 ELSE 0.0 END AS win)
        FROM {src}
    """)


def badcheck_gate(g1: int, g2: int, seeds: int) -> dict:
    """§8 闸门。返回整份可落盘的判决,而不只是一个 bool。

    ⚠️ 2026-08-18 修:原实现把上限写死成 BADCHECK_MAX_GREEN(=1),与实际跑了几个种子
    无关 —— `--seeds 3` 跑出来照样按 <=1 判,等于把假阳性容忍度从 1/20(5%)
    悄悄放宽到 1/3(33%),而屏幕上仍然打印 PASS。当天真的这么跑过、真的印了 PASS。
    对准核心问句「如果它现在就是坏的,我看到的会有什么不同?」——【完全没有不同】。
    这就是 CLAUDE.md 静默失败第 9 条:判据跑在一个不会发生的世界里。

    两道锁,都要焊死:
      1. 上限随种子数【等比缩放】,向下取整(只会更严),保住 5% 这个率本身;
      2. 种子数少于单子写死的 BADCHECK_SEEDS 一律【非权威】,亮绿 0 个也不算通过 ——
         少跑不是「跑得快一点的同一个检验」,是【压根没跑那个检验】。
    """
    authoritative = seeds >= BADCHECK_SEEDS
    max_green = (seeds * BADCHECK_MAX_GREEN) // BADCHECK_SEEDS
    passed = authoritative and g1 <= max_green and g2 <= max_green
    if not authoritative:
        reason = (f"非权威试跑:种子数 {seeds} < 单子写死的 {BADCHECK_SEEDS},"
                  f"不构成 §8 通过(无论亮绿几个)。要么跑满 {BADCHECK_SEEDS} 个种子,"
                  f"要么把这次当调试、别当判决。")
    elif not passed:
        reason = (f"坏数据自检未亮红:P1 亮绿 {g1}、P2 亮绿 {g2},各须 <= {max_green}/{seeds}"
                  f" —— 立即停工修判据,不许去看真实结果(单子 §8)。")
    else:
        reason = f"P1 亮绿 {g1}、P2 亮绿 {g2},各 <= {max_green}/{seeds}"
    return {"pass": passed, "max_green": max_green, "authoritative": authoritative,
            "seeds": seeds, "p1_green": g1, "p2_green": g2, "reason": reason}


def permutation_null(con, n_top: int, n_perm: int = PERM_N,
                     salt_base: int = PERM_SALT_NULL) -> dict:
    """§0.8 零分布:随机抽 n_top 个钱包当「高手组」,算其 V 窗组合毛收益率。

    这就是「没有持续性」这一假设下 R_V 该长什么样。判决改为:真实的前 10%
    须【超过】此分布的 PERM_Q 分位。这样自动扣掉大盘基准与事件聚类 ——
    而原来的绝对红线 +0.5pp 低于本分布的中位数,分不出会的和不会的。
    """
    vals: list[float] = []
    for i in range(n_perm):
        v = con.execute(f"""
            WITH pick AS (
              SELECT proxy_wallet FROM rank_d WHERE r_d IS NOT NULL
              ORDER BY {sql_det_order('proxy_wallet', salt_base + i)} LIMIT {n_top})
            SELECT {sql_num()} / nullif({sql_den()}, 0) * 100
            FROM wV WHERE proxy_wallet IN (SELECT proxy_wallet FROM pick)
        """).fetchone()[0]
        if v is not None:
            vals.append(v)
    vals.sort()

    def q(pq: float):
        if not vals:
            return None
        return vals[min(len(vals) - 1, int(round(pq * (len(vals) - 1))))]

    out = {"n_perm": len(vals), "salt_base": salt_base,
           "p50": q(0.50), "p90": q(0.90), "p95": q(PERM_Q), "p99": q(0.99),
           "mean": (sum(vals) / len(vals)) if vals else None,
           "min": vals[0] if vals else None, "max": vals[-1] if vals else None}
    print(f"  零分布({len(vals)} 次置换,盐 {salt_base}~):中位 {out['p50']:+.4f}pp | "
          f"p90 {out['p90']:+.4f}pp | p{int(PERM_Q*100)} {out['p95']:+.4f}pp | "
          f"p99 {out['p99']:+.4f}pp")
    # ⛔ 这里必须【实时算】,不许写死结论:40 个种子时估得中位 +0.9449pp(高于原红线),
    #    200 次置换实测是 +0.4375pp(低于原红线)—— 写死的那句当场就成了假话。
    #    这正是 CLAUDE.md 静默失败第 8 条:旧结论在新工况下变假话。
    above = sum(v >= REDLINE_A_PP for v in vals)
    print(f"     对照原绝对红线 +{REDLINE_A_PP}pp:零分布中位 {out['p50']:+.4f}pp,"
          f"{above}/{len(vals)} = {above/len(vals)*100:.0f}% 的随机组能越过它"
          f" ⇒ 原红线{'几乎没有' if above/len(vals) > 0.25 else '仍有'}分辨力")
    return out


def bad_data_checks(con, seeds: int = BADCHECK_SEEDS) -> dict:
    """P1 打乱身份 / P2 打乱结果。两者都【必须 🔴】。"""
    base = rank_and_verdict(con, TOP_FRAC)
    n_top = base["n_top"]
    print(f"\n  §0.8 置换零分布(定红线用;与下面 P1/P2 的抽样【盐不相交】)")
    null = permutation_null(con, n_top)
    null_p95 = null["p95"]
    out: dict = {"n_top": n_top, "perm_null": null, "P1": [], "P2": []}

    print(f"\n  P1 · 打乱身份(随机抽 {n_top:,} 个钱包当「高手组」,其余不变)")
    print(f"     必须 🔴 —— 若绿,说明判据在测分档本身的均值回归/分母效应,不是在测钱包")
    for i in range(seeds):
        salt = PERM_SALT_BADCHECK + i        # 与零分布那段盐不相交
        con.execute(f"""CREATE OR REPLACE TABLE p1_top AS
            SELECT proxy_wallet, r_d FROM rank_d WHERE r_d IS NOT NULL
            ORDER BY {sql_det_order('proxy_wallet', salt)} LIMIT {n_top}""")
        res = rank_and_verdict(con, TOP_FRAC, top_override="p1_top")
        bs = block_bootstrap(con, seed=1000 + i)
        # ⛔ G5 在坏数据自检里【不适用】,固定传 True。理由:这里检验的是「判据能不能
        #    被假数据骗绿」,若把真实 G5 结果传进来,G5 一旦不过就永远 0 绿、闸门照印
        #    PASS —— 判据跑在一个不会发生的世界里(CLAUDE.md 静默失败第 9 条)。
        green, _ = judge(res["r_v_pp"], bs["q025_pp"], True, null_p95)
        out["P1"].append({"seed": i, "r_v_pp": res["r_v_pp"], "q025_pp": bs["q025_pp"],
                          "green": green})
        print(f"     seed {i:>2}: R_V {res['r_v_pp']:+8.4f}pp | 2.5%分位 {bs['q025_pp']:+8.4f}pp"
              f" | {'🟢 绿' if green else '🔴 红'}")

    print(f"\n  P2 · 打乱结果(win ~ Bernoulli(price),两窗都打乱)")
    print(f"     必须 🔴 —— 若绿,说明判据能凭空造出正收益")
    print(f"     ⚠️ 实现已于 2026-08-18 修订(原「档内 shuffle」自身注入 +2.2pp,见函数注释)")
    for i in range(seeds):
        salt = PERM_SALT_BADCHECK + i
        _resample_win_from_price(con, "wD", "wD_p2", salt=salt)
        _resample_win_from_price(con, "wV", "wV_p2", salt=salt)
        res = rank_and_verdict(con, TOP_FRAC, d_table="wD_p2", v_table="wV_p2")
        bs = block_bootstrap(con, seed=2000 + i)
        green, _ = judge(res["r_v_pp"], bs["q025_pp"], True, null_p95)  # 同上:G5 不适用
        out["P2"].append({"seed": i, "r_v_pp": res["r_v_pp"], "q025_pp": bs["q025_pp"],
                          "green": green})
        print(f"     seed {i:>2}: R_V {res['r_v_pp']:+8.4f}pp | 2.5%分位 {bs['q025_pp']:+8.4f}pp"
              f" | {'🟢 绿' if green else '🔴 红'}")

    # 对照留痕:原单子做法(档内 shuffle)跑 3 次,证明修订不是为了让闸门过
    print(f"\n  对照 · 原单子做法(档内 shuffle win),仅留痕不参与闸门:")
    out["P2_original_method"] = []
    for i in range(3):
        salt = PERM_SALT_BADCHECK + i
        _shuffle_win_within_price_band(con, "wD", "wD_p2o", salt=salt)
        _shuffle_win_within_price_band(con, "wV", "wV_p2o", salt=salt)
        res = rank_and_verdict(con, TOP_FRAC, d_table="wD_p2o", v_table="wV_p2o")
        bs = block_bootstrap(con, seed=3000 + i)
        green, _ = judge(res["r_v_pp"], bs["q025_pp"], True, null_p95)  # 同上:G5 不适用
        out["P2_original_method"].append({"seed": i, "r_v_pp": res["r_v_pp"],
                                          "q025_pp": bs["q025_pp"], "green": green})
        print(f"     seed {i:>2}: R_V {res['r_v_pp']:+8.4f}pp | 2.5%分位 {bs['q025_pp']:+8.4f}pp"
              f" | {'🟢 绿(= 该做法自身的人工正偏)' if green else '🔴 红'}")

    g1 = sum(x["green"] for x in out["P1"])
    g2 = sum(x["green"] for x in out["P2"])
    out["P1_green"], out["P2_green"] = g1, g2
    out["gate"] = badcheck_gate(g1, g2, seeds)
    out["pass"] = out["gate"]["pass"]
    print(f"\n  闸门(§8):P1 亮绿 {g1}/{seeds}、P2 亮绿 {g2}/{seeds},"
          f"各须 <= {out['gate']['max_green']}/{seeds}"
          f"{'' if out['gate']['authoritative'] else '  ⚠️ 非权威试跑(种子数不足)'}"
          f"  [{'PASS' if out['pass'] else '🔴 FAIL'}]")
    if not out["pass"]:
        die(out["gate"]["reason"])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 3:§9 G5 —— 证明缺失与结果不相关 + 第 11 项两窗已结算率复现
# ─────────────────────────────────────────────────────────────────────────────
def g5_verdict(kept: dict, dropped: dict) -> tuple[bool, str]:
    """单子 §0.5 修订二:把「实质差异」判成 PASS/FAIL,不留给看见结果之后再定。

    线(逐字用本模块常量,不许在别处复制数字):
      · 结算基础率两组之差 > G5_BASERATE_DIFF_MAX_PP 个百分点
      · 入场价中位数两组之差 > G5_PRICE_MEDIAN_DIFF_MAX
    """
    if not dropped or not dropped.get("n"):
        return True, "该闸未剔除任何成交"
    kb, db = kept.get("base_rate"), dropped.get("base_rate")
    if (kb is None) != (db is None):
        # 一侧一个已结算市场都没有 = 最极端的「与结果相关」。
        # ⛔ 不许因为「差值算不出来」就放过 —— 那正是静默失败最爱藏的地方。
        return False, "一侧无任何已结算市场,无法比较基础率(极端的与结果相关)"
    msgs = []
    ok = True
    if kb is not None and db is not None:
        # round 到 1e-9:让「恰好等于红线」按单子写的『>』算作不越线,
        # 不受浮点表示误差影响(0.5+0.05-0.5 实际是 0.050000000000000044)。
        d = round(abs(kb - db) * 100, 9)
        if d > G5_BASERATE_DIFF_MAX_PP:
            ok = False
            msgs.append(f"基础率差 {d:.2f}pp > {G5_BASERATE_DIFF_MAX_PP}pp")
    kp, dp = kept.get("p50"), dropped.get("p50")
    if kp is not None and dp is not None:
        d = round(abs(kp - dp), 12)
        if d > G5_PRICE_MEDIAN_DIFF_MAX:
            ok = False
            msgs.append(f"入场价中位差 {d:.4f} > {G5_PRICE_MEDIAN_DIFF_MAX}")
    return ok, "; ".join(msgs) if msgs else "两组画像一致"


# 四格 + 一个未知桶。⛔ 只允许存在这一份分格式。
# 由来(2026-08-19 review 洞 B):原设计「测闸一时固定闸二通过、测闸二时固定闸一通过」,
# 自以为对称,但生产真正剔除的是两道闸的【并集】——「两道闸同时不过」那一块
# 两个检验都碰不到(按主跑推算约 36 万笔)。
G5_CELL_SQL = """CASE
    WHEN end_unknown THEN 'end_unknown'
    WHEN g1 AND g2 THEN 'kept'
    WHEN NOT g1 AND g2 THEN 'drop_g1_only'
    WHEN g1 AND NOT g2 THEN 'drop_g2_only'
    ELSE 'drop_both' END"""

G5_CELL_LABEL = {
    "kept": "保留(两道闸都过)",
    "drop_g1_only": "只闸一不过(采集延迟>=24h)",
    "drop_g2_only": "只闸二不过(结算延迟超窗)",
    "drop_both": "两道闸都不过",
    "end_unknown": "end_date 缺失/无法解析(闸二状态未知)",
}


def _g5_cells(con, base_sql: str) -> dict:
    """把基底分成四格 + 未知桶,各出一份画像(笔数/结算基础率/价格四分位)。"""
    rows = con.execute(f"""
        SELECT {G5_CELL_SQL} AS cell, count(*) n,
               avg(CASE WHEN resolved_outcome = 1.0 THEN 1.0
                        WHEN resolved_outcome = 0.0 THEN 0.0 END) base_rate,
               median(price) p50, quantile_cont(price, 0.25) p25,
               quantile_cont(price, 0.75) p75
        FROM ({base_sql}) GROUP BY 1
    """).fetchall()
    out = {r[0]: {"n": r[1], "base_rate": r[2], "p50": r[3], "p25": r[4], "p75": r[5]}
           for r in rows}
    for c in G5_CELL_LABEL:
        out.setdefault(c, {"n": 0, "base_rate": None, "p50": None, "p25": None, "p75": None})
    return out


def g5_missingness(con, settle_gate_days: int = SETTLE_GATE_DAYS) -> dict:
    """母单 G5:被剔除的成交 vs 保留的成交,比较结算基础率与入场价分布。

    §0.5 修订一(2026-08-19):此前只测了闸二 —— 源表 s1_* 是闸一【之后】才建的,
    结构上不可能看见闸一剔掉了什么。而闸一(采集延迟 < 24h)是否与结果相关,
    是整个方向三成立的前提(热门市场完全可能被采得更快)。

    §0.7 第二轮修订(2026-08-19,review 洞 B/C):改成【四格 + 未知桶】——
    生产剔除的是两道闸的并集,原来的两条边加起来漏掉「两道闸都不过」那一格;
    且 end_date 缺失/无法解析的成交此前被无声并入「闸二剔除」,而它们的
    结算基础率与有值的差 27pp(reviewer 实测),必须单列。

    settle_gate_days 可变:敏感性网格里 S5/S6 用的是 3/14 天,若仍拿 7 天的 G5
    结果去给它们背书,等于「判据跑在一个不会发生的世界里」(review 洞 A)。
    """
    res: dict = {"failures": [], "settle_gate_days": settle_gate_days}
    for tag, win in (("D", WIN_D), ("V", WIN_V)):
        base = f"""
            SELECT t.price AS price, m.resolved_outcome AS resolved_outcome,
                   (t.ingested_at - t.timestamp < {INGEST_GATE_SEC}) AS g1,
                   ({sql_settle_gate(settle_gate_days, ts='t.timestamp', end='m.end_date')}) AS g2,
                   (m.end_date IS NULL OR {sql_end_unparseable('m.end_date')}) AS end_unknown
            FROM read_parquet('{RAW}') t JOIN mkt m USING (condition_id)
            WHERE {sql_window_where(win)}
              AND m.market_class = 'event' AND NOT m.hft_suspect
        """
        cells = _g5_cells(con, base)
        kept = cells["kept"]
        res[tag] = cells
        print(f"  [{tag} 窗]  闸二取值 0~{settle_gate_days} 天")
        for cell, label in G5_CELL_LABEL.items():
            d = cells[cell]
            if cell == "kept":
                d["pass"], d["reason"] = True, "基准组"
            else:
                d["pass"], d["reason"] = g5_verdict(kept, d)
                if not d["pass"]:
                    res["failures"].append(f"{tag}/{cell}({label}): {d['reason']}")
            d["label"] = label
            br = f"{d['base_rate']:.4f}" if d["base_rate"] is not None else "n/a"
            pr = (f"{d['p25']:.3f}/{d['p50']:.3f}/{d['p75']:.3f}"
                  if d["p50"] is not None else "n/a")
            mark = "     " if cell == "kept" else ("[PASS]" if d["pass"] else "[🔴FAIL]")
            print(f"    {mark} {label:<32} n={d['n']:>10,} | 基础率 {br:>7} | "
                  f"价 p25/p50/p75 = {pr}")
            if cell != "kept" and not d["pass"]:
                print(f"            └─ {d['reason']}")

    res["pass"] = not res["failures"]
    if not res["pass"]:
        print(f"\n  🔴 G5 不过 —— 缺失与结果相关。按单子 §0.5 修订二,主判决不得为绿。")
        for f in res["failures"]:
            print(f"     · {f}")
    return res


def check_settled_rate(con) -> dict:
    """§9 第 11 项:两窗已结算率必须复现 §3.2 表(D≈99.3% / V≈99.7%)。

    ⭐ 偏离 >1pp 即中止 —— 这是本 V2 存在的理由,不许静默通过。
    """
    out = {}
    ok = True
    for tag in ("D", "V"):
        gate = f" AND {sql_settle_gate(SETTLE_GATE_DAYS)}"
        r = con.execute(f"""
            SELECT count(*), sum(CASE WHEN resolved_outcome IS NOT NULL THEN 1 ELSE 0 END)
            FROM s1_{tag} WHERE market_class='event' AND NOT hft_suspect{gate}
        """).fetchone()
        rate = r[1] / r[0] * 100 if r[0] else 0.0
        exp = EXPECT_SETTLED_RATE[tag]
        dev = abs(rate - exp)
        good = dev <= G5_TOLERANCE_PP
        ok &= good
        out[tag] = {"n": r[0], "settled": r[1], "rate_pct": rate, "expect_pct": exp,
                    "deviation_pp": dev, "pass": good}
        print(f"  [{tag}] 已结算率 {rate:.2f}% vs 单子基线 {exp}% "
              f"(偏离 {dev:.2f}pp,容差 {G5_TOLERANCE_PP}pp)  [{'PASS' if good else '🔴 FAIL'}]")
    out["pass"] = ok
    if not ok:
        die("两窗已结算率偏离基线 >1pp —— 口径已漂移,这正是 V2 要防的那件事(单子 §9 第 11 项)。")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 4:主判决 + 敏感性网格(§7)—— 同一次批量运行,一次性全表输出
# ─────────────────────────────────────────────────────────────────────────────
def robustness_R3(con) -> dict:
    """母单 R3 抗单点:分别剔除贡献最大的单个比赛组 / 单个钱包,R_V 均须仍 >= +0.5pp。"""
    out = {}
    # 剔最大比赛组(按 |num| 贡献)
    r = con.execute("""
        SELECT sum(num), sum(den) FROM vgrp WHERE race_group <> (
          SELECT race_group FROM vgrp ORDER BY abs(num) DESC LIMIT 1)
    """).fetchone()
    top_g = con.execute("SELECT race_group, num FROM vgrp ORDER BY abs(num) DESC LIMIT 1").fetchone()
    out["drop_top_group"] = {"group": top_g[0], "group_num": top_g[1],
                             "r_v_pp": (r[0] / r[1] * 100) if r[1] else None}
    # 剔最大钱包
    r2 = con.execute(f"""
        WITH pw AS (SELECT proxy_wallet, {sql_num()} num, {sql_den()} den
                    FROM wV WHERE proxy_wallet IN (SELECT proxy_wallet FROM top_w) GROUP BY 1)
        SELECT (SELECT sum(num) FROM pw WHERE proxy_wallet <> t.proxy_wallet)
             / (SELECT sum(den) FROM pw WHERE proxy_wallet <> t.proxy_wallet) * 100,
               t.proxy_wallet, t.num
        FROM (SELECT * FROM pw ORDER BY abs(num) DESC LIMIT 1) t
    """).fetchone()
    out["drop_top_wallet"] = {"wallet": r2[1], "wallet_num": r2[2], "r_v_pp": r2[0]}
    for k, v in out.items():
        p = v["r_v_pp"]
        print(f"  R3 {k:<16}: R_V {p:+.4f}pp  "
              f"[{'PASS' if p is not None and p >= REDLINE_A_PP else 'FAIL'}]")
    return out


def robustness_R4(con) -> dict:
    """母单 R4 跨分层稳:入场价档 × 市场累计成交额档;每格 <30 钱包标注样本不足。"""
    print("  R4 · 按入场价档:")
    price_rows = con.execute(f"""
        SELECT CASE WHEN price < 0.2 THEN '<0.2' WHEN price <= 0.8 THEN '0.2-0.8' ELSE '>0.8' END b,
               {sql_num()} num, {sql_den()} den,
               count(DISTINCT proxy_wallet) nw, count(*) n
        FROM wV WHERE proxy_wallet IN (SELECT proxy_wallet FROM top_w) GROUP BY 1 ORDER BY 1
    """).fetchall()
    print("  R4 · 按市场累计成交额档:")
    vol_rows = con.execute(f"""
        WITH mv AS (SELECT condition_id, {sql_den()} v FROM wV GROUP BY 1)
        SELECT CASE WHEN mv.v < 10000 THEN '<$1万' WHEN mv.v <= 100000 THEN '$1万-10万'
                    ELSE '>$10万' END b,
               {sql_num('w.size', 'w.dir', 'w.win', 'w.price')} num,
               {sql_den('w.size', 'w.price')} den,
               count(DISTINCT w.proxy_wallet) nw, count(*) n
        FROM wV w JOIN mv USING (condition_id)
        WHERE w.proxy_wallet IN (SELECT proxy_wallet FROM top_w) GROUP BY 1 ORDER BY 1
    """).fetchall()
    out = {"price_band": [], "volume_band": []}
    for key, rows in (("price_band", price_rows), ("volume_band", vol_rows)):
        for b, num, den, nw, n in rows:
            pp = (num / den * 100) if den else None
            flag = " ⚠️样本不足(<30钱包),不下结论" if nw < 30 else ""
            out[key].append({"band": b, "r_pp": pp, "n_wallets": nw, "n_trades": n})
            print(f"      {b:<10} R {pp:+8.4f}pp | 钱包 {nw:>6,} | 成交 {n:>9,}{flag}")
    return out


def one_cell(con, led: Ledger, label: str, *, g5_pass: bool,
             settle_gate: int | None = SETTLE_GATE_DAYS,
             min_trades: int = MIN_TRADES, top_frac: float = TOP_FRAC,
             null_p95: float | None, g5_audited: bool = True,
             drop_bots: bool = True, equal_weight: bool = False,
             swap_windows: bool = False, use_dt: bool = False,
             ingest_gate: bool = True, quiet: bool = True) -> dict:
    """跑一个网格格子。窗口/闸门变了就重建,否则复用已物化的 s2_*。"""
    wd, wv = (WIN_V, WIN_D) if swap_windows else (WIN_D, WIN_V)
    build_window(con, "D", wd, led, settle_gate, quiet=quiet, use_dt=use_dt,
                 ingest_gate=ingest_gate)
    build_window(con, "V", wv, led, settle_gate, quiet=quiet, use_dt=use_dt,
                 ingest_gate=ingest_gate)
    wl = apply_wallet_layer(con, led, min_trades, drop_bots, quiet=quiet)
    res = rank_and_verdict(con, top_frac, equal_weight=equal_weight)
    bs = block_bootstrap(con)
    green, detail = judge(res["r_v_pp"], bs["q025_pp"], g5_pass, null_p95)
    row = {"label": label, "settle_gate": settle_gate, "min_trades": min_trades,
           "top_frac": top_frac, "drop_bots": drop_bots, "equal_weight": equal_weight,
           "swap_windows": swap_windows, "use_dt": use_dt, "ingest_gate": ingest_gate,
           "g5_audited": g5_audited,
           **wl, **res, **bs, "green": green, "verdict_detail": detail}
    print(f"  {label:<34} R_V {res['r_v_pp']:+8.4f}pp | 2.5%分位 {bs['q025_pp']:+8.4f}pp"
          f" | 钱包 {wl['wallets_kept']:>6,} | 组 {bs['n_groups']:>6,} | "
          f"{'🟢' if green else '🔴'}{'' if g5_audited else '*未经G5审查'}")
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["selfcheck", "badcheck", "g5", "full"], default="full",
                    help="g5:跑到阶段 3 为止 —— 重测样本构成基线时用,"
                         "机械保证【看不到任何 R_V】(单子 §0.5 修订三第 2 点)")
    ap.add_argument("--seeds", type=int, default=BADCHECK_SEEDS)
    args = ap.parse_args()

    t0 = time.time()
    out: dict = {"prereg": "docs/PREREG_WALLET_SKILL_V2_2026-08-18.md",
                 "run_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "stage": args.stage, "badcheck_seeds": args.seeds}
    con = connect()

    # 数据水位(可复现性:采集器在跑,数字会随时间增长,必须留痕)
    wm = con.execute(f"""SELECT (SELECT count(*) FROM read_parquet('{RAW}')),
                         (SELECT count(*) FROM mkt),
                         (SELECT max(snapshot_at) FROM mkt)""").fetchone()
    out["watermark"] = {"raw_rows": wm[0], "registry_markets": wm[1], "max_snapshot_at": wm[2]}
    save(out)
    print(f"数据水位: 成交 {wm[0]:,} 行 | 注册市场 {wm[1]:,} | 注册表最新快照 {wm[2]}")

    print("\n" + "=" * 78)
    print("阶段 1 · 启动自检(§2 封窗等待期 + §4 符号铁律)")
    print("=" * 78)
    out["selfcheck_seal"] = selfcheck_seal_window()
    out["selfcheck_sign"] = selfcheck_sign(con)
    if args.stage == "selfcheck":
        save(out, "selfcheck.json")
        print(f"\n✅ 阶段 1 通过,耗时 {time.time()-t0:.0f}s")
        return 0

    led = Ledger()
    print("\n" + "=" * 78)
    print("过滤链(§3)· 主口径 —— 逐级出声计数")
    print("=" * 78)
    n_d = build_window(con, "D", WIN_D, led, SETTLE_GATE_DAYS, quiet=False)
    n_v = build_window(con, "V", WIN_V, led, SETTLE_GATE_DAYS, quiet=False)
    wl = apply_wallet_layer(con, led, MIN_TRADES, True, quiet=False)
    out["ledger"] = led.dump()
    out["ledger_notes"] = led.notes
    out["wallet_layer"] = wl
    save(out)

    print("\n" + "=" * 78)
    print("阶段 2 · §8 坏数据自检(P1 打乱身份 / P2 打乱结果)")
    print("=" * 78)
    out["bad_data_checks"] = bad_data_checks(con, seeds=args.seeds)
    null_p95 = out["bad_data_checks"]["perm_null"]["p95"]
    save(out["bad_data_checks"], "bad_data_checks.json")
    save(out)
    if args.stage == "badcheck":
        save(out, "badcheck.json")
        print(f"\n✅ 阶段 1-2 通过,耗时 {time.time()-t0:.0f}s")
        return 0

    print("\n" + "=" * 78)
    print("阶段 3 · §9 第 11 项(两窗已结算率复现)+ G5(缺失与结果不相关)")
    print("=" * 78)
    out["settled_rate_check"] = check_settled_rate(con)
    # 洞 A(2026-08-19 review):敏感性网格里 S5/S6 用的是 3/14 天闸二,样本与主口径
    # 不同。若拿 7 天的 G5 结果去给它们背书,就是「判据跑在一个不会发生的世界里」。
    # 故每个实际用到的闸二取值各跑一次 G5。
    g5_by_gate: dict[int, dict] = {}
    for gd in (SETTLE_GATE_DAYS, 3, 14):
        if gd in g5_by_gate:
            continue
        if gd != SETTLE_GATE_DAYS:
            print(f"\n  —— 附:闸二 0~{gd} 天口径下的 G5(供 S5/S6 那两格使用)——")
        g5_by_gate[gd] = g5_missingness(con, gd)
    out["g5_missingness"] = g5_by_gate[SETTLE_GATE_DAYS]
    out["g5_by_gate"] = {str(k): v["pass"] for k, v in g5_by_gate.items()}
    g5_pass = out["g5_missingness"]["pass"]
    # 出声计数的判定结果由 apply_wallet_layer 出口产出,搬到顶层供结论行读取
    out["expectations"] = wl.get("expectations", {"pass": True, "failures": []})
    save(out)
    if args.stage == "g5":
        # ⛔ 到此为止。重测基线(§3.2 表 / EXPECT_* 常量)只许在这一档下做 ——
        #    「重测的是样本构成,不是红线取值」这句话,靠的不是我记得,是这里返回了。
        save(out, "g5.json")
        print(f"\n✅ 阶段 1-3 通过(未进入判决),耗时 {time.time()-t0:.0f}s")
        return 0

    print("\n" + "=" * 78)
    print("阶段 4 · 主判决 + 敏感性网格(§7,一次性全表输出)")
    print("=" * 78)
    grid_specs = [
        ("主 · gate7 × >=20笔 × 前10%", {}),
        ("S1 · 门槛 10 笔", {"min_trades": 10}),
        ("S2 · 门槛 50 笔", {"min_trades": 50}),
        ("S3 · 不剔除疑似机器人", {"drop_bots": False}),
        ("S4 · 每笔等权(旁证)", {"equal_weight": True}),
        ("S5 · 结算闸 0~3 天", {"settle_gate": 3}),
        ("S6 · 结算闸 0~14 天", {"settle_gate": 14}),
        ("S7 · 闸一关(§0.7 登记的稳健性臂)", {"ingest_gate": False}),
        ("D2 · 窗口对调(诊断)", {"swap_windows": True}),
        ("D3 · 用 dt 切窗(= V1 坐标,诊断)", {"use_dt": True}),
    ]
    grid: list[dict] = []
    out["grid"] = grid
    for label, kw in grid_specs:
        gd = kw.get("settle_gate", SETTLE_GATE_DAYS)
        # 审过的条件:该格的闸二取值跑过 G5,且窗口坐标与闸一口径都没被改动
        audited = (gd in g5_by_gate and not kw.get("use_dt", False)
                   and kw.get("ingest_gate", True))
        cell_g5 = g5_by_gate[gd]["pass"] if gd in g5_by_gate else g5_pass
        grid.append(one_cell(con, led, label, g5_pass=cell_g5, null_p95=null_p95,
                             g5_audited=audited, **kw))
        save(out)      # 每算完一格就落盘 —— 中途被杀,前面的不该跟着一起消失

    # 主判决须在主口径的表上做 R3/R4,故重建主口径
    print("\n主口径 R3/R4(母单红线):")
    build_window(con, "D", WIN_D, led, SETTLE_GATE_DAYS, quiet=True)
    build_window(con, "V", WIN_V, led, SETTLE_GATE_DAYS, quiet=True)
    apply_wallet_layer(con, led, MIN_TRADES, True, quiet=True)
    main_res = rank_and_verdict(con, TOP_FRAC)
    main_bs = block_bootstrap(con)
    out["main"] = {**main_res, **main_bs, "null_p95": null_p95}
    out["main"]["green"], out["main"]["detail"] = judge(
        main_res["r_v_pp"], main_bs["q025_pp"], g5_pass, null_p95)
    out["R3"] = robustness_R3(con)
    out["R4"] = robustness_R4(con)
    save(out)

    # D1 诊断:仅在主判决亮红时用于说明,⛔ 不得改变判决
    if not out["main"]["green"]:
        print("\n  D1 诊断(主判决为红,用于分辨『真没信号』还是『被 10% 稀释』):")
        out["D1"] = one_cell(con, led, "D1 · 前 1%(诊断,不改判决)",
                             g5_pass=g5_pass, null_p95=null_p95, top_frac=0.01)

    print()
    for line in final_verdict_lines(out):
        print(line)

    save(out)
    print(f"\n落盘: {OUT_DIR}/results.json   总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
