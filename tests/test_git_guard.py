#!/usr/bin/env python3
"""Behavioural tests for hooks/git-state-guard.py.

Runs the guard as a subprocess exactly the way Claude Code would: PreToolUse
JSON payload on stdin, exit code as the verdict (0 allow, 2 block).

    python3 tests/test_git_guard.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO, "hooks", "git-state-guard.py")

PASS = 0
FAIL = 0


def run_guard(payload, exempt: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("GIT_GUARD_EXEMPT", None)
    if exempt is not None:
        env["GIT_GUARD_EXEMPT"] = exempt
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, GUARD], input=stdin,
                          capture_output=True, text=True, env=env)


def bash(command: str, agent_type: str | None = "dev-backend") -> dict:
    p = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_type is not None:
        p["agent_type"] = agent_type
    return p


def check(name: str, got_exit: int, want_exit: int, note: str = "") -> None:
    global PASS, FAIL
    ok = got_exit == want_exit
    PASS += ok
    FAIL += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name:58} exit={got_exit} (want {want_exit}) {note}")


print("== hook: git-state-guard.py ==")

# --- blocked: destructive working-tree / history mutations by a writer
p = run_guard(bash("git stash"))
check("git stash -> blocked", p.returncode, 2,
      "correction sent" if "Git-state guard" in p.stderr else "NO CORRECTION TEXT")
check("cd && git stash -> blocked",
      run_guard(bash("cd /repo && git stash")).returncode, 2)
check("git -C /p reset --hard -> blocked",
      run_guard(bash("git -C /p reset --hard HEAD~1")).returncode, 2)
check("git checkout -- src/ -> blocked",
      run_guard(bash("git checkout -- src/")).returncode, 2)
check("git restore path -> blocked",
      run_guard(bash("git restore src-tauri/gen/schemas/")).returncode, 2)
check("git clean -fd -> blocked",
      run_guard(bash("git clean -fd")).returncode, 2)
check("git commit -> blocked for writers",
      run_guard(bash('git commit -m "wip"')).returncode, 2)
check("git add -A -> blocked for writers",
      run_guard(bash("git add -A")).returncode, 2)
check("git push -> blocked for writers",
      run_guard(bash("git push origin main")).returncode, 2)
check("git stash pop -> blocked",
      run_guard(bash("git stash pop")).returncode, 2)

# --- allowed: read-only git
check("git status && git diff -> allowed",
      run_guard(bash("git status --short && git diff --stat")).returncode, 0)
check("git log -> allowed",
      run_guard(bash("git log --oneline -5")).returncode, 0)
check("git stash list -> allowed (read-only subcommand)",
      run_guard(bash("git stash list")).returncode, 0)

# --- the real false positive: verb inside a quoted grep pattern
check("'git checkout' inside grep pattern -> allowed",
      run_guard(bash('grep -n "outcome\\|git checkout\\|git pull\\|confirm" src/a.rs')).returncode, 0)

# --- scoping and fail-open
check("exempt agent may commit",
      run_guard(bash("git commit -m x", agent_type="git-commit"),
                exempt="git-commit,deploy").returncode, 0)
check("no agent_type (lead/main session) -> allowed",
      run_guard(bash("git stash", agent_type=None)).returncode, 0)
check("non-Bash tool -> allowed",
      run_guard({"tool_name": "Read", "agent_type": "dev-backend",
                 "tool_input": {"file_path": "/a"}}).returncode, 0)
check("garbage stdin -> fail open",
      run_guard("not json {{{").returncode, 0)
check("missing command -> fail open",
      run_guard({"tool_name": "Bash", "agent_type": "dev-backend",
                 "tool_input": {}}).returncode, 0)

print(f"\ngit-guard: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
