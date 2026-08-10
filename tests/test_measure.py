#!/usr/bin/env python3
"""Unit tests for scripts/measure-agent-team.py against synthetic transcripts
with hand-computed expected values.

    python3 tests/test_measure.py

The synthetic agent reproduces the verified on-disk shape of real transcripts:
one API message is split across several JSONL lines, one content block per
line, all sharing message.id, with usage growing across the writes. (Verified
on a real 157-run session: 5,292 tool_use blocks, none duplicated; 9,686
assistant lines vs 4,477 distinct message ids.)

  msg m1: line 1 = thinking block (usage 100), line 2 = Read a.rs (usage 500)
  msg m2: Read a.rs again          -> 1 stale read
  msg m3: Edit a.rs
  msg m4: Read a.rs offset=1       -> partial, NOT stale (edited in between)
  msg m5: final text

Expected:
  output tokens  = 500 + 200 + 300 + 400 + 50 = 1450   (dedup keeps max of m1)
  tool calls     = 4
  reads          = 3, stale = 1, partial = 1
  files touched  = 1
  turns          = 5 distinct messages (6 assistant lines on disk)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "measure-agent-team.py")

spec = importlib.util.spec_from_file_location("measure", SCRIPT)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)

PASS = 0
FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    ok = got == want
    PASS += ok
    FAIL += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name:55} got={got!r} want={want!r}")


def ev(ts: str, mid: str, out_tok: int, blocks: list) -> str:
    return json.dumps({
        "type": "assistant", "timestamp": ts,
        "message": {"id": mid, "role": "assistant",
                    "usage": {"output_tokens": out_tok,
                              "cache_read_input_tokens": 1000,
                              "cache_creation_input_tokens": 0,
                              "input_tokens": 10},
                    "content": blocks}})


def user(ts: str) -> str:
    return json.dumps({"type": "user", "timestamp": ts,
                       "message": {"role": "user", "content": []}})


def tool(name: str, **params) -> dict:
    return {"type": "tool_use", "name": name, "input": params}


tmpdir = tempfile.mkdtemp()
sub = os.path.join(tmpdir, "proj", "sess", "subagents")
os.makedirs(sub)

lines = [
    ev("2026-08-10T10:00:00Z", "m1", 100, [{"type": "thinking"}]),
    ev("2026-08-10T10:00:05Z", "m1", 500, [tool("Read", file_path="/a.rs")]),
    user("2026-08-10T10:00:10Z"),
    ev("2026-08-10T10:00:20Z", "m2", 200, [tool("Read", file_path="/a.rs")]),
    user("2026-08-10T10:00:25Z"),
    ev("2026-08-10T10:00:35Z", "m3", 300, [tool("Edit", file_path="/a.rs")]),
    user("2026-08-10T10:00:40Z"),
    ev("2026-08-10T10:00:50Z", "m4", 400, [tool("Read", file_path="/a.rs", offset=1, limit=10)]),
    user("2026-08-10T10:00:55Z"),
    ev("2026-08-10T10:01:00Z", "m5", 50, [{"type": "text", "text": "done"}]),
]
with open(os.path.join(sub, "agent-x1.jsonl"), "w") as fh:
    fh.write("\n".join(lines) + "\n")
with open(os.path.join(sub, "agent-x1.meta.json"), "w") as fh:
    json.dump({"agentType": "writer", "description": "synthetic"}, fh)

print("== measure-agent-team.py: load_agent on a synthetic transcript ==")
a = measure.load_agent(os.path.join(sub, "agent-x1.meta.json"), idle_gap=600)

check("output tokens dedup by message.id (max wins)", a["out"], 1450)
check("stale re-reads (same agent, no edit between)", a["stale_reads"], 1)
check("partial reads (offset/limit)", a["partial_reads"], 1)
check("files touched", a["files"], 1)
check("tool calls counted once per block", a["tool_calls"], 4)
check("turns == distinct API messages (5), not JSONL lines (6)", a["turns"], 5)

print("\n== percentile helper q() ==")
check("median of [1..4] (nearest-rank upper)", measure.q([1, 2, 3, 4], .5), 3)
check("p90 of 10 items", measure.q(list(range(1, 11)), .9), 10)
check("empty list", measure.q([], .5), 0.0)

print("\n== --split-at with a NAIVE timestamp (no Z, no offset -> UTC) ==")
p = subprocess.run(
    [sys.executable, SCRIPT, "--root", tmpdir, "--project", "proj",
     "--split-at", "2026-08-10T10:00:30"],
    capture_output=True, text=True)
naive_ok = p.returncode == 0
print(f"  {'PASS' if naive_ok else 'FAIL'}  naive --split-at exits cleanly              exit={p.returncode}")
if not naive_ok:
    tail = (p.stderr.strip().splitlines() or ["<no stderr>"])[-1]
    print(f"         crash: {tail}")
PASS += naive_ok
FAIL += not naive_ok

print("\n== declared_tools frontmatter parsing ==")
agents_dir = os.path.join(tmpdir, "agents")
os.makedirs(agents_dir)
with open(os.path.join(agents_dir, "writer.md"), "w") as fh:
    fh.write("---\nname: writer\ntools: Read, Edit, Bash\n---\nbody\n")
with open(os.path.join(agents_dir, "lead.md"), "w") as fh:
    fh.write("---\nname: lead\ntools: Agent(a, b, c), Read\n---\nbody\n")
with open(os.path.join(agents_dir, "yaml-list.md"), "w") as fh:
    fh.write("---\nname: yaml-list\ntools:\n  - Read\n  - Edit\n---\nbody\n")
decl = measure.declared_tools(agents_dir)
check("inline list parsed", decl.get("writer"), {"Read", "Edit", "Bash"})
check("Agent(...) collapsed, comma list survives", decl.get("lead"), {"Agent", "Read"})
check("YAML block-list form parsed", decl.get("yaml-list"), {"Read", "Edit"})

print(f"\nmeasure: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
