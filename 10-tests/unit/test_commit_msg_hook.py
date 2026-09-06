#!/usr/bin/env python3
"""判据焊死:三问钩子 —— 把「靠我记得」换成「机器挡住」(2026-08-17)。

## 由来(实测,非假想)

CLAUDE.md 明文要求「每一步动手前先写三问,进提交信息」。实测最近 12 个提交:
**8 个做了、4 个没做**,而且**今天最后两个提交都漏了** —— 前三个提交老老实实写,
会话拉长之后就悄悄不写了,没有任何东西提醒我。

这条规则本身是有效的(第 2 问「不做会怎样」当天砍掉了一件本来要做的工作),
问题是**它没有强制点**。没有强制点的规则会衰减,而且衰减得无声无息 ——
这与本项目反复发作的静默失败是同一形状,只是被静默掉的是"我有没有照规矩做"。

## 为什么钩子放在 `deploy/githooks/` 而不是只装在 `.git/hooks/`

`.git/` 不进版本库。只改本机 = 换台机器/重新 clone 就静默复原
(同 2026-08-05「配置两份副本」那次教训:仓库那份才是权威)。
故仓库存一份权威副本,本判据焊住"本机装的那份与仓库一致"。

## 本判据**不能**回答什么

- 不回答「写下的三问是不是认真答的」。钩子只能查有没有写,查不了写得对不对。
  防敷衍靠的是 code review 和提交信息本身会被人读,不是靠钩子。
- 不回答「`--no-verify` 会不会被滥用」。逃生门是 git 标准做法,故意留着 ——
  纯格式化/重构确实不需要三问。滥用与否只能靠人自己看提交历史。
"""
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO_HOOK = ROOT / "deploy" / "githooks" / "commit-msg"
LOCAL_HOOK = ROOT / ".git" / "hooks" / "commit-msg"


def _run_hook(msg: str, staged_py: bool) -> subprocess.CompletedProcess:
    """在一个**临时仓库**里跑钩子 —— 判据绝不碰真仓库的 git 状态。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
        name = "x.py" if staged_py else "x.md"
        (d / name).write_text("# hi\n")
        subprocess.run(["git", "add", name], cwd=d, check=True)
        msg_file = d / "MSG"
        msg_file.write_text(msg, encoding="utf-8")
        return subprocess.run([str(REPO_HOOK), str(msg_file)], cwd=d,
                              capture_output=True, text=True)


# ---------- 判据组 A:两头都焊死 ----------

def test_it_blocks_a_python_commit_without_the_three_questions():
    """⭐真异常必拦:动了 .py 而没写三问 → 非 0 退出。"""
    r = _run_hook("fix: 随手改点东西\n\n没有三问\n", staged_py=True)
    assert r.returncode != 0, "动了 .py 却没三问,钩子放行了"
    assert "三问" in r.stderr


def test_it_lets_a_python_commit_with_the_three_questions_through():
    """稳态静默:写了三问就放行,不许添堵。"""
    msg = ("fix: 某某\n\n1. 服务哪条需求:不变量 A1\n"
           "2. 不做会怎样:会继续丢市场\n3. 怎么验证:判据先红后绿\n")
    r = _run_hook(msg, staged_py=True)
    assert r.returncode == 0, f"写了三问却被拦:{r.stderr}"


def test_a_docs_only_commit_is_not_bothered():
    """纯文档/配置改动不强制 —— 对它们要求三问是形式主义,而形式主义会催生敷衍。"""
    r = _run_hook("docs: 改个错别字\n", staged_py=False)
    assert r.returncode == 0, f"纯文档提交被拦了:{r.stderr}"


def test_merge_and_revert_are_exempt():
    """merge / revert / fixup 的信息由 git 生成,不该被要求三问。"""
    for head in ("Merge branch 'x'", "Revert \"abc\"", "fixup! abc"):
        r = _run_hook(head + "\n", staged_py=True)
        assert r.returncode == 0, f"{head} 被拦了"


def test_any_one_of_the_three_questions_counts():
    """三问的措辞不必逐字一致 —— 只要认得出在答哪一问就行。

    要求逐字会逼人复制粘贴模板,那正是形式主义的开始。
    """
    for phrase in ("服务哪条需求", "不做会怎样", "怎么验证"):
        r = _run_hook(f"fix: x\n\n{phrase}:某某\n", staged_py=True)
        assert r.returncode == 0, f"含「{phrase}」却被拦"


# ---------- 判据组 B:两份副本必须一致(2026-08-05 的教训) ----------

def test_repo_and_local_hook_are_identical():
    """⭐仓库那份是权威;只改本机 = 重新 clone 就静默复原。

    ⚠️ 本条在别人刚 clone、还没装钩子时会红 —— 那是**对的**:
    它在说"你这台机器上钩子没装",而不是"代码坏了"。
    """
    assert REPO_HOOK.exists(), "仓库里没有权威副本"
    assert LOCAL_HOOK.exists(), (
        f"本机没装钩子 ⇒ 三问规则在这台机器上没有强制点。\n"
        f"装:cp {REPO_HOOK.relative_to(ROOT)} .git/hooks/commit-msg "
        f"&& chmod +x .git/hooks/commit-msg")
    assert REPO_HOOK.read_text() == LOCAL_HOOK.read_text(), (
        "本机钩子与仓库不一致 —— 改了本机没同步回仓库(或反之)")


def test_both_copies_are_executable():
    """不可执行的钩子 = git 静默跳过 = 和没装一模一样,而且看不出来。"""
    for p in (REPO_HOOK, LOCAL_HOOK):
        if p.exists():
            assert os.stat(p).st_mode & stat.S_IXUSR, f"{p} 没有可执行位,git 会静默跳过它"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
