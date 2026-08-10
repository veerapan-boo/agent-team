#!/usr/bin/env python3
"""Behavioural tests for hooks/subagent-return-contract.py.

Runs the hook as a subprocess exactly the way Claude Code would: JSON payload
on stdin, transcript on disk, exit code as the verdict.

    python3 tests/test_hook.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "subagent-return-contract.py")

PASS = 0
FAIL = 0


def transcript(final_message: str, earlier: str | None = None,
               content_as_string: bool = False) -> str:
    """Write a minimal JSONL transcript, return its path."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        if earlier is not None:
            fh.write(json.dumps({"type": "assistant", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": earlier}]}}) + "\n")
        content = (final_message if content_as_string
                   else [{"type": "text", "text": final_message}])
        fh.write(json.dumps({"type": "assistant", "message": {
            "role": "assistant", "content": content}}) + "\n")
    return path


def run_hook(stdin_text: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, HOOK], input=stdin_text,
                          capture_output=True, text=True)


def check(name: str, got_exit: int, want_exit: int, note: str = "") -> None:
    global PASS, FAIL
    ok = got_exit == want_exit
    PASS += ok
    FAIL += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name:55} exit={got_exit} (want {want_exit}) {note}")


def fenced(n: int) -> str:
    return "Done.\n```\n" + "\n".join(f"line {i}" for i in range(n)) + "\n```\n"


print("== hook: subagent-return-contract.py ==")

p = run_hook(json.dumps({"transcript_path": transcript(fenced(60))}))
check("60-line fenced block -> blocked", p.returncode, 2,
      "correction sent" if "Return-contract violation" in p.stderr else "NO CORRECTION TEXT")

p = run_hook(json.dumps({"transcript_path": transcript(fenced(40))}))
check("exactly 40 lines -> blocked (>= boundary)", p.returncode, 2)

p = run_hook(json.dumps({"transcript_path": transcript(fenced(39))}))
check("39-line fenced block -> allowed", p.returncode, 0)

p = run_hook(json.dumps({"transcript_path": transcript(
    "All done.\nFiles: a.rs\nVerified: build OK")}))
check("conclusion-only report -> allowed", p.returncode, 0)

p = run_hook(json.dumps({"transcript_path": transcript(fenced(60)),
                         "stop_hook_active": True}))
check("stop_hook_active -> never re-blocked", p.returncode, 0)

p = run_hook("not json {{{")
check("garbage stdin -> fail open", p.returncode, 0)

p = run_hook(json.dumps({}))
check("missing transcript_path -> fail open", p.returncode, 0)

p = run_hook(json.dumps({"transcript_path": "/nonexistent/nowhere.jsonl"}))
check("nonexistent transcript -> fail open", p.returncode, 0)

p = run_hook(json.dumps({"transcript_path": transcript(
    "Done.\n```\n" + "\n".join("x" for _ in range(60)))}))
check("unclosed 60-line fence -> blocked", p.returncode, 2)

p = run_hook(json.dumps({"transcript_path": transcript(
    "Short final report. Files: a.rs", earlier=fenced(80))}))
check("big block in EARLIER message -> allowed", p.returncode, 0)

p = run_hook(json.dumps({"transcript_path": transcript(fenced(60),
                                                       content_as_string=True)}))
check("content-as-string big block -> blocked", p.returncode, 2)

fd, empty = tempfile.mkstemp(suffix=".jsonl")
os.close(fd)
p = run_hook(json.dumps({"transcript_path": empty}))
check("empty transcript -> fail open", p.returncode, 0)

print(f"\nhook: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
