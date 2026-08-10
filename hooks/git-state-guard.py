#!/usr/bin/env python3
"""PreToolUse hook -- stop concurrent agents from destroying each other's work
through shared git state.

Why this exists
---------------
Agents fanned out in one project share **one git working tree**. The index, the
stash stack, and HEAD are effectively a single shared file that every agent
edits. One agent running `git stash`, `git restore` or `git reset` sweeps up a
sibling's uncommitted work mid-run -- observed in a real session: a deploy agent
ran `git restore <paths>` while a writer was mid-task. Nothing errors, nothing
is reported, and the orchestrator never knows.

The fan-out rule "never let two agents edit the same file" covers files but is
blind to git state. This hook closes that gap the way the return contract is
closed: prose governs judgement, hooks govern behaviour.

Wiring (Claude Code, .claude/settings.json)
-------------------------------------------
    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Bash",
            "hooks": [
              {
                "type": "command",
                "command": "GIT_GUARD_EXEMPT=git-commit,deploy python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/git-state-guard.py\""
              }
            ]
          }
        ]
      }
    }

`GIT_GUARD_EXEMPT` is a comma-separated list of agent types allowed to mutate
git state -- your designated commit/release roles. The orchestrator must still
spawn those roles **alone**, never inside a wave; the guard enforces the
writers, serialization discipline covers the rest.

Contract
--------
stdin  : JSON payload with `tool_name`, `tool_input.command`, `agent_type`
exit 0 : allow the command
exit 2 : block it; stderr is fed back to the agent as a correction

Design choices, all deliberately conservative:
  * only fires for Bash calls made by a subagent (`agent_type` present) that is
    not exempt -- the lead/main session always passes
  * verbs are matched at **command position** (start of line, after && ; | etc.)
    with quoted segments stripped first, so a grep *pattern* containing
    "git checkout" does not trip it -- that false positive really happened
  * read-only git (status, diff, log, show, blame, `stash list`/`show`) passes
  * every unexpected condition FAILS OPEN -- a broken guard must never wedge
    the workflow it is guarding
  * this guards against accident, not adversaries: `bash -c "git stash"` inside
    quotes would slip through the quote-stripping. Accept that; the goal is to
    stop the reflex, and the discipline text covers the rest.
"""

from __future__ import annotations

import json
import os
import re
import sys

# Verbs that mutate the shared working tree, index, stash stack, or history.
BLOCKED_VERBS = {
    "stash", "reset", "checkout", "restore", "clean", "rebase", "merge",
    "switch", "cherry-pick", "revert", "commit", "add", "rm", "mv",
    "push", "pull", "tag",
}

# Read-only subcommands of otherwise-blocked verbs.
READONLY_SUB = {("stash", "list"), ("stash", "show")}

# strip '...' and "..." segments so quoted text (grep patterns, commit-message
# examples) cannot look like a command
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

# `git` at a command position: start of string/line or after a separator,
# optionally wrapped in command/sudo/env, then global flags, then the verb.
GIT_AT_CMD = re.compile(
    r"(?:^|[;&|`(\n]|\$\()\s*"
    r"(?:command\s+|sudo\s+|env\s+(?:\w+=\S*\s+)*)?"
    r"git\s+"
    r"((?:-[cC]\s+\S+\s+|--[\w-]+(?:=\S+)?\s+)*)"
    r"([\w-]+)"
    r"(?:\s+([\w-]+))?"
)

CORRECTION = (
    "Git-state guard: `git {verb}` mutates state shared with concurrently running agents.\n"
    "\n"
    "The working tree, index, stash stack, and HEAD are ONE shared file for every agent in\n"
    "this project. A stash/reset/restore here can silently destroy a sibling agent's\n"
    "uncommitted work -- this has actually happened on this team.\n"
    "\n"
    "Do not retry the command. Instead:\n"
    "  - finish what compiles inside your own file ownership,\n"
    "  - report `BLOCKED:` with the exact git command you wanted and why,\n"
    "  - let the lead route it to the designated git role, which runs alone.\n"
    "\n"
    "If you already ran any state-changing git command earlier in this task, declare it on\n"
    "your report's `Git:` line. An undeclared mutation is treated as an incident.\n"
)


def fail_open(note: str | None = None) -> None:
    """Exit 0 no matter what went wrong. A guard must not become the outage."""
    if note:
        sys.stderr.write(note + "\n")
    sys.exit(0)


def blocked_verb(command: str) -> str | None:
    """Return the offending verb if the command mutates shared git state."""
    stripped = QUOTED.sub(" ", command)
    for match in GIT_AT_CMD.finditer(stripped):
        verb, sub = match.group(2), match.group(3)
        if verb in BLOCKED_VERBS and (verb, sub) not in READONLY_SUB:
            return verb
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        fail_open()

    if payload.get("tool_name") != "Bash":
        fail_open()

    # No agent_type means the lead / main session -- never guarded.
    agent_type = payload.get("agent_type")
    if not agent_type:
        fail_open()

    exempt = {a.strip() for a in os.environ.get("GIT_GUARD_EXEMPT", "").split(",") if a.strip()}
    if agent_type in exempt:
        fail_open()

    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command:
        fail_open()

    verb = blocked_verb(command)
    if verb:
        sys.stderr.write(CORRECTION.format(verb=verb))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
