#!/usr/bin/env python3
"""
PostToolUse hook: Python static review gate.

Triggered after Edit or Write on any .py file.
Reads the Claude Code hook JSON from stdin (field: tool_input.file_path).

Checks performed (fast, no network):
1. py_compile  — syntax errors
2. Pattern scan — bare `except:` / `except Exception:` with pass/continue/only-log
3. Broad exception handlers missing TimeoutError in network/IO files
4. Known-dangerous patterns: requests without timeout=, open() without context manager

Exit codes:
  0  — all checks passed (output = original stdin JSON, required by hook protocol)
  0  — non-.py file or parse error (pass-through, never block non-Python work)

Outputs warnings to stderr (Claude Code surfaces stderr as inline annotations).
Never exits non-zero: we want advisory warnings, not hard blocks, to avoid
breaking the edit flow when the reviewer is uncertain.
"""

import json
import re
import sys
import py_compile
import tempfile
import os
from pathlib import Path

MAX_STDIN = 2 * 1024 * 1024  # 2 MB

# Files whose paths contain these substrings get extra network/IO checks
IO_PATTERNS = [
    "monitor", "notif", "telegram", "sync", "fetch", "http",
    "request", "socket", "api", "clob", "polymarket",
]

# Patterns that indicate a broad exception block that might swallow TimeoutError
BROAD_EXCEPT_RE = re.compile(
    r'except\s+(?:Exception|BaseException)\s*(?:as\s+\w+)?\s*:',
)
BARE_EXCEPT_RE = re.compile(r'except\s*:')

# requests.get/post/put/delete/patch without timeout= argument
REQUESTS_NO_TIMEOUT_RE = re.compile(
    r'requests\.(get|post|put|delete|patch|head)\s*\([^)]*\)',
)

# open() not in a `with` statement
OPEN_WITHOUT_WITH_RE = re.compile(
    r'(?<![.\'"\w])open\s*\(',
)
WITH_OPEN_RE = re.compile(r'\bwith\b.*\bopen\s*\(')


def is_io_file(path_str: str) -> bool:
    lower = path_str.lower()
    return any(p in lower for p in IO_PATTERNS)


def scan_source(src: str, file_path: str) -> list[str]:
    warnings = []
    lines = src.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Bare except
        if BARE_EXCEPT_RE.search(line):
            warnings.append(
                f"  Line {i}: bare `except:` — catches SystemExit/KeyboardInterrupt too. "
                f"Use `except Exception as e:` at minimum."
            )

        # Broad except that swallows everything — flag in IO files
        if is_io_file(file_path) and BROAD_EXCEPT_RE.search(line):
            # Look ahead: if the next non-empty line is pass/continue/log only,
            # that's where TimeoutError gets swallowed.
            next_lines = [l.strip() for l in lines[i:i+4] if l.strip()]
            body_preview = next_lines[0] if next_lines else ""
            if not any(kw in body_preview for kw in ("raise", "return", "sys.exit")):
                warnings.append(
                    f"  Line {i}: broad `except Exception` in network/IO file. "
                    f"Ensure `TimeoutError` (a subclass of OSError since Py3.3) "
                    f"is not silently swallowed — body preview: `{body_preview[:60]}`"
                )

    # requests without timeout
    if is_io_file(file_path):
        for m in REQUESTS_NO_TIMEOUT_RE.finditer(src):
            snippet = m.group(0)
            if "timeout" not in snippet:
                lineno = src[:m.start()].count("\n") + 1
                warnings.append(
                    f"  Line {lineno}: `requests.{m.group(1)}(...)` has no `timeout=` "
                    f"argument — will hang indefinitely on network stall."
                )

    return warnings


def main():
    raw = sys.stdin.buffer.read(MAX_STDIN)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.stdout.buffer.write(raw)
        sys.exit(0)

    file_path = (data.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".py"):
        sys.stdout.buffer.write(raw)
        sys.exit(0)

    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        sys.stdout.buffer.write(raw)
        sys.exit(0)

    issues = []

    # 1. Syntax check
    try:
        py_compile.compile(abs_path, doraise=True)
    except py_compile.PyCompileError as exc:
        issues.append(f"  SYNTAX ERROR: {exc}")

    # 2. Pattern scan
    try:
        src = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        issues.extend(scan_source(src, abs_path))
    except OSError:
        pass

    if issues:
        label = Path(abs_path).name
        io_flag = " [network/IO file]" if is_io_file(abs_path) else ""
        print(
            f"[hook:py-review] {label}{io_flag} — {len(issues)} issue(s) found:\n"
            + "\n".join(issues)
            + "\n\nConsider running: /python-review  or  subagent python-reviewer",
            file=sys.stderr,
        )

    # Always pass through — advisory only
    sys.stdout.buffer.write(raw)
    sys.exit(0)


if __name__ == "__main__":
    main()
