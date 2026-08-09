#!/usr/bin/env python3
"""SubagentStop hook -- make the conclusion-only return contract enforceable.

Why this exists
---------------
The lead agent's context window is the one thing that has to survive an entire
goal. A specialist that ends its turn by pasting a large diff, file dump, or log
block pins that noise in the lead's context for every subsequent turn.

Every agent definition already *asks* for conclusion-only reports. Measurement
showed that asking is not enough: a discipline rule written as prose moved its
target metric by one percentage point across two sessions. A hook that inspects
the final message and rejects it is a constraint rather than a request, and
constraints hold.

    prose rules govern judgement -- hooks govern behaviour

Wiring (Claude Code, .claude/settings.json)
-------------------------------------------
    {
      "hooks": {
        "SubagentStop": [
          {
            "matcher": "^(writer-one|writer-two|writer-three)$",
            "hooks": [
              {
                "type": "command",
                "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/subagent-return-contract.py\""
              }
            ]
          }
        ]
      }
    }

Wire it for **code-writing specialists only**. Read-only agents -- explorers,
architects, reviewers -- have output formats whose long fenced blocks are the
deliverable, and blocking those is a false positive.

Contract
--------
stdin  : JSON payload with `transcript_path` and `stop_hook_active`
exit 0 : allow the subagent to stop
exit 2 : block the stop; stderr is fed back to the agent as a correction

Design choices, all deliberately conservative:
  * only an oversized *fenced* block trips it -- inline code and short snippets
    are always fine
  * `stop_hook_active` short-circuits, so a blocked agent is corrected once and
    can never be trapped in a loop
  * every unexpected condition FAILS OPEN -- a broken guard must never wedge the
    workflow it is guarding
"""

from __future__ import annotations

import json
import sys

# A fenced block this long inside a "conclusion" is a dump, not an illustration.
BIG_BLOCK_LINES = 40

CORRECTION = (
    "Return-contract violation: your final message contains a {n}-line fenced code block.\n"
    "\n"
    "You are a specialist reporting to the orchestrator. Its context window is the one "
    "thing that survives the whole session, and a pasted diff, file dump, or log block "
    "pins that noise there for every remaining turn of the goal.\n"
    "\n"
    "Re-send your final message as conclusion-only:\n"
    "  - one line on what changed and why\n"
    "  - a `Files:` line listing the paths you touched\n"
    "  - the verification result (build / lint / tests), pass or fail\n"
    "  - any blocker, or `PARTIAL:` with what remains\n"
    "\n"
    "Cite `path:line` instead of pasting. The lead reads the file itself if it needs to.\n"
)


def fail_open(note: str | None = None) -> None:
    """Exit 0 no matter what went wrong. A guard must not become the outage."""
    if note:
        sys.stderr.write(note + "\n")
    sys.exit(0)


def last_assistant_text(transcript_path: str) -> str | None:
    """Return the text of the final assistant message in a JSONL transcript."""
    latest = None
    with open(transcript_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            message = record.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                latest = content
            elif isinstance(content, list):
                parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if parts:
                    latest = "\n".join(parts)
    return latest


def largest_fenced_block(text: str) -> int:
    """Line count of the longest ``` fenced block, counting an unclosed fence."""
    inside = False
    current = 0
    biggest = 0
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if inside:
                biggest = max(biggest, current)
            inside = not inside
            current = 0
            continue
        if inside:
            current += 1
    return max(biggest, current if inside else 0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        fail_open()

    # Never re-block a stop we already forced: bounds the correction to one pass.
    if payload.get("stop_hook_active"):
        fail_open()

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        fail_open()

    try:
        text = last_assistant_text(transcript_path)
    except OSError:
        fail_open()

    if not text:
        fail_open()

    biggest = largest_fenced_block(text)
    if biggest >= BIG_BLOCK_LINES:
        sys.stderr.write(CORRECTION.format(n=biggest))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
