#!/usr/bin/env python3
"""判据焊死:规则复核台账到期必须有人复核(2026-08-17)。

## 由来

2026-08-17 用户问「规则都写在 CLAUDE.md 里了,你为什么还犯错」。审计的结论是:
**问句/动作/事实**会触发,**原则复述**不会。当天删掉两条纯劝诫,
并给当天新写的 7 条挂了复核日期 —— 因为它们一次都还没触发过,
现在断言"它们有用"同样是没验过的推断。

⭐ 但"两周后回来复核"这句话本身也是**靠人记得**,而今天刚证明过:
没有强制点的规则会衰减,且衰减得无声无息(三问规则 12 个提交漏了 4 个)。
所以复核日期也做成判据 —— 到期不复核就变红。

## 它不能回答什么

不回答"复核得认不认真"。判据只能逼人到期坐下来看一眼,看完写什么由人负责。
"""
import datetime as dt
import re
import sys
from pathlib import Path

import pytest

CLAUDE_MD = Path(__file__).resolve().parents[2] / "CLAUDE.md"
DUE_RE = re.compile(r"###\s*待复核:(\d{4})-(\d{2})-(\d{2})")


def test_the_ledger_declares_a_due_date():
    """台账必须有到期日 —— 没有到期日的待办等于没有待办。"""
    m = DUE_RE.search(CLAUDE_MD.read_text(encoding="utf-8"))
    assert m, "CLAUDE.md 的规则复核台账里找不到「待复核:YYYY-MM-DD」"


def test_the_review_is_not_overdue():
    """⭐到期就变红:去复核那 7 条,把"删了哪些/留了哪些/依据"写回台账。

    ⚠️ 本条红**不是代码坏了**,是"该坐下来看一眼了"。
    复核完把台账里的日期往后推(或删掉已确认有效/无效的条目),它自然变绿。
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    m = DUE_RE.search(text)
    assert m, "台账没有到期日"
    due = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    today = dt.date.today()
    assert today <= due, (
        f"规则复核已过期 {(today - due).days} 天(到期日 {due})。\n"
        f"该做的事:把台账里那几条逐个量一遍 —— *它有没有真的在写代码那一刻"
        f"改变过我的动作?* 答不出实例的,按 CLAUDE.md 的标准删掉或转成判据。\n"
        f"复核结果写回台账,然后把日期往后推。不许只在对话里说完就算。")


def test_the_ledger_lists_what_each_rule_must_prove():
    """台账里每条都要写明"到期要回答什么" —— 否则复核时无从判断。"""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    seg = text.split("待复核:")[1].split("\n\n**⚠️")[0]
    rows = [l for l in seg.splitlines() if l.strip().startswith("|") and "---" not in l]
    body = [r for r in rows if "到期要回答" not in r]
    assert len(body) >= 5, f"台账里条目太少,像是没在维护:{len(body)}"
    for r in body:
        cells = [c.strip() for c in r.strip("|").split("|")]
        assert len(cells) >= 3 and len(cells[2]) >= 6, f"这条没写清到期要回答什么:{r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
