#!/usr/bin/env python3
"""
pytest 配置文件
"""

import pytest
import sys
from pathlib import Path

# 添加项目路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "analysis"))
sys.path.insert(0, str(PROJECT_ROOT / "06-tools" / "monitoring"))
sys.path.insert(0, str(PROJECT_ROOT / "08-backtests"))  # 回测 engine 包(相对导入)


# 测试 fixtures
@pytest.fixture
def sample_market_data():
    """示例市场数据"""
    return {
        "id": "12345",
        "question": "Will Bitcoin reach $100k by 2026?",
        "slug": "will-bitcoin-reach-100k-by-2026",
        "outcomePrices": ["0.52", "0.47"],
        "liquidity": 150000,
        "volume": 500000,
        "endDate": "2026-12-31T23:59:59Z"
    }


@pytest.fixture
def sample_pair_cost_opportunity():
    """示例 Pair Cost 套利机会"""
    return {
        "market_id": "12345",
        "slug": "test-market",
        "question": "Test Market",
        "yes_price": 0.52,
        "no_price": 0.47,
        "pair_cost": 0.99,
        "profit_margin": 0.01,
        "profit_pct": 1.0,
        "liquidity": 150000,
        "volume": 500000,
        "end_date": "2026-12-31T23:59:59Z",
        "is_opportunity": True
    }


@pytest.fixture
def sample_whale_data():
    """示例鲸鱼数据"""
    return {
        "wallet": "0x1234567890abcdef1234567890abcdef12345678",
        "pseudonym": "TestWhale",
        "total_volume": 100000,
        "trade_count": 50,
        "large_trades": 10,
        "markets_traded": 5,
        "last_trade": 1700000000
    }


@pytest.fixture
def mock_telegram_response():
    """Mock Telegram API 响应"""
    return {
        "ok": True,
        "result": {
            "message_id": 123,
            "chat": {"id": 1530224854},
            "text": "Test message"
        }
    }


# 配置 pytest
def pytest_configure(config):
    """pytest 配置"""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "requires_api: marks tests that require external API"
    )


# ============================================================================
# 🔴 生产数据目录污染守卫(2026-09-06 立)
# ============================================================================
# 由来:实测发现 `test_backfill_closed.py` 每跑一轮往 `11-collector/data/swept/`
#       写 18 个 parquet —— 根因是 `backfill_once()` 的 `mark = mark or se.write_swept`,
#       判据漏传 `mark=` 就落到真实写入函数上。从 2026-08-06 起累积了 2,377 个垃圾文件
#       (占该目录 54.3%),而 CLAUDE.md 明令「测试必须写 /tmp,生产数据目录不许收测试产物」。
#
# ⭐ 为什么做成 session 级守卫而不是逐个改判据:逐个补 `mark=` 只修今天这 11 处,
#    将来新增判据照样会犯(本项目「照抄结构而不抽象」已犯 3 次)。这条守卫**对所有
#    生产数据目录一视同仁**,任何判据留下新文件都会在 session 结束时被点名。
#
# ⚠️ 它只在**整个 session 结束**时报,不指认具体是哪条判据 —— 定位办法见报错信息。

_COLLECTOR_DATA = PROJECT_ROOT / "11-collector" / "data"
_GUARDED_DIRS = [
    _COLLECTOR_DATA / "swept",
    _COLLECTOR_DATA / "raw",            # ⚠️ dt=YYYY-MM-DD/ 分区,顶层无直接文件
    _COLLECTOR_DATA / "registry",
    _COLLECTOR_DATA / "market_times",
    _COLLECTOR_DATA / "audit",          # ⚠️ 同样是分区目录
    _COLLECTOR_DATA / "state",
    _COLLECTOR_DATA / "truncations",
    PROJECT_ROOT / "07-data",
]


def _snapshot(dirs):
    """{目录: 相对路径集合}。目录不存在记空集(不存在本身不是错)。

    🔴 **必须递归**(2026-09-06 code review 的 HIGH):第一版用 `d.iterdir()` + `is_file()`,
    而 `raw/` 与 `audit/` 是按 `dt=YYYY-MM-DD/` 分区的 —— 顶层**一个直接文件都没有**
    (实测 raw 顶层 344 项 / audit 46 项,直接文件均为 0)⇒ 这两个目录**永远返回空集**,
    守卫对它们完全失明。而 review 用项目真实代码端到端复现:漏传 `write=`(不是 `mark=`)
    会让两个真 parquet 写进 `raw/dt=*/`,**而整轮测试是绿的、守卫一声不吭**。
    ⇒ 这正是本项目「我自己造的检查工具同样会静默坏掉」那个形状,发生在新写的检查工具上。

    用**相对路径**而不是文件名:不同分区下可能出现同名文件,只比名字会漏。
    代价实测:递归全扫 22,379 个文件耗时 0.019s,可忽略。
    """
    snap = {}
    for d in dirs:
        try:
            snap[d] = {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()} if d.is_dir() else set()
        except OSError:
            snap[d] = set()
    return snap


@pytest.fixture(scope="session", autouse=True)
def _no_test_writes_into_production_dirs():
    """🔴 判据不许在生产数据目录里留下文件。

    这条守卫本身要能红 —— 验证办法:临时往任一被守护目录(**含 `raw/dt=*/` 这种分区子目录**)
    塞个文件,整轮跑完必须在这里失败。2026-09-06 立时先看着它红、再修的 11 处调用;
    同日 code review 后又补做了一次:在 `raw/dt=*/` 里造真泄漏,确认能被抓到。

    ⚠️ **前提假设**:测试期间没有真实采集进程在并发写这些目录。
    采集器于 2026-09-05 全部停止;**若将来复工,跑判据前先确认没有真实进程在跑**,
    否则真实写入会被误判成判据泄漏,把人带偏去查错方向。
    """
    before = _snapshot(_GUARDED_DIRS)
    yield
    after = _snapshot(_GUARDED_DIRS)
    leaked = {d: sorted(after[d] - before[d]) for d in _GUARDED_DIRS
              if after[d] - before[d]}
    if leaked:
        detail = "\n".join(
            f"  {d}: 新增 {len(v)} 个,例如 {v[:3]}" for d, v in leaked.items())
        raise AssertionError(
            "🔴 判据往生产数据目录写了文件(违反 CLAUDE.md 的测试隔离铁律):\n"
            f"{detail}\n"
            "  定位办法:`for f in 10-tests/unit/*.py; do` 逐个跑并数目录文件增量。\n"
            "  常见根因:被测函数的写入依赖是 `x = x or 真实写入函数`,判据漏传就落到真目录。"
        )
