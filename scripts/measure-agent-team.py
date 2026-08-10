#!/usr/bin/env python3
"""Measure a Claude Code agent team from its session transcripts.

Reproduces every number in this repository's README. No instrumentation is
required -- Claude Code already writes one JSONL transcript per subagent run.

    ~/.claude/projects/<project-slug>/<session-id>.jsonl
    ~/.claude/projects/<project-slug>/<session-id>/subagents/
            agent-<id>.jsonl        one per subagent run
            agent-<id>.meta.json    agentType, description

Usage
-----
    # list the projects and sessions this tool can see
    python3 measure-agent-team.py --list

    # measure every subagent of every session in a project
    python3 measure-agent-team.py --project my-project-slug

    # measure one session
    python3 measure-agent-team.py --project my-project-slug --session <session-id>

    # before/after comparison: split at the moment you changed something
    python3 measure-agent-team.py --project my-project-slug \
        --split-at 2026-01-15T09:00:00Z --labels "baseline,with rules"

    # per-agent detail instead of the summary table
    python3 measure-agent-team.py --project my-project-slug --detail

Measurement notes (these matter -- getting them wrong changes the conclusions)
-----------------------------------------------------------------------------
* output_tokens are de-duplicated by message.id, keeping the maximum. Streaming
  writes the same assistant message several times with growing usage; summing
  them naively inflates token counts several-fold.
* turns are de-duplicated by message.id too. A transcript writes one line per
  CONTENT BLOCK (thinking on one line, tool_use on the next, same id), so
  counting assistant lines inflates turn counts roughly 2x. One distinct id =
  one API response; maxTurns counts tool-use turns, so this figure over-counts
  it by about one (the final report message carries no tool call).
* A re-read counts as wasteful only within a single agent AND only when no edit
  to that path happened in between. Counting duplicates across agents produces a
  ~70% "waste" figure that is not waste -- different agents legitimately need the
  same file.
* Inter-message gaps longer than --idle-gap seconds are excluded from the
  generate-time / tool-time split, so a human reading a report is not counted as
  model latency.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import sys

DEFAULT_ROOT = os.path.expanduser("~/.claude/projects")
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


# --------------------------------------------------------------------------- io


def parse_ts(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # a bare --split-at value (no Z, no offset) means UTC
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def read_jsonl(path: str) -> list[dict]:
    out = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def find_subagent_dirs(root: str, project: str | None, session: str | None) -> list[str]:
    dirs = []
    pattern = os.path.join(root, project or "*", session or "*", "subagents")
    for path in sorted(glob.glob(pattern)):
        if os.path.isdir(path):
            dirs.append(path)
    return dirs


# ---------------------------------------------------------------------- extract


def load_agent(meta_path: str, idle_gap: float) -> dict | None:
    """Return one agent's metrics, or None if the transcript is unusable."""
    agent_id = os.path.basename(meta_path)[: -len(".meta.json")]
    jsonl_path = os.path.join(os.path.dirname(meta_path), agent_id + ".jsonl")
    if not os.path.exists(jsonl_path):
        return None

    try:
        meta = json.load(open(meta_path))
    except (ValueError, OSError):
        meta = {}

    events = read_jsonl(jsonl_path)
    stamped = [e for e in events if e.get("timestamp")]
    if not stamped:
        return None

    out_by_id: dict[str, int] = {}
    out_anon = 0
    cache_read = 0
    cache_write = 0
    turn_ids: set[str] = set()
    anon_turns = 0
    tools: collections.Counter = collections.Counter()
    reads = 0
    stale_reads = 0
    partial_reads = 0
    files_touched: set[str] = set()
    fresh: set[str] = set()  # paths read and not edited since

    for event in events:
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        usage = message.get("usage") or {}

        msg_id = message.get("id")
        tokens = usage.get("output_tokens", 0) or 0
        if msg_id:
            turn_ids.add(msg_id)
            out_by_id[msg_id] = max(out_by_id.get(msg_id, 0), tokens)
        else:
            anon_turns += 1
            out_anon += tokens

        cache_read += usage.get("cache_read_input_tokens", 0) or 0
        cache_write += usage.get("cache_creation_input_tokens", 0) or 0

        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            tools[name] += 1
            params = block.get("input") or {}
            path = params.get("file_path") or params.get("path")
            if name == "Read":
                reads += 1
                if params.get("offset") is not None or params.get("limit") is not None:
                    partial_reads += 1
                if path:
                    files_touched.add(path)
                    if path in fresh:
                        stale_reads += 1
                    fresh.add(path)
            elif name in EDIT_TOOLS and path:
                files_touched.add(path)
                fresh.discard(path)

    # first assistant turn's input == the startup context this agent was handed
    startup = 0
    for event in events:
        if event.get("type") == "assistant":
            usage = (event.get("message") or {}).get("usage") or {}
            startup = (
                (usage.get("input_tokens", 0) or 0)
                + (usage.get("cache_read_input_tokens", 0) or 0)
                + (usage.get("cache_creation_input_tokens", 0) or 0)
            )
            break

    # generate-time vs tool-time, from adjacent message pairs
    gen_s = tool_s = 0.0
    timeline = [e for e in stamped if e.get("type") in ("assistant", "user")]
    for prev, cur in zip(timeline, timeline[1:]):
        delta = (parse_ts(cur["timestamp"]) - parse_ts(prev["timestamp"])).total_seconds()
        if delta < 0 or delta > idle_gap:
            continue
        if prev["type"] == "user" and cur["type"] == "assistant":
            gen_s += delta
        elif prev["type"] == "assistant" and cur["type"] == "user":
            tool_s += delta

    times = sorted(parse_ts(e["timestamp"]) for e in stamped)
    return {
        "id": agent_id,
        "type": meta.get("agentType") or "?",
        "desc": meta.get("description") or "",
        "start": times[0],
        "end": times[-1],
        "duration": (times[-1] - times[0]).total_seconds(),
        "turns": len(turn_ids) + anon_turns,
        "out": sum(out_by_id.values()) + out_anon,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "startup": startup,
        "tools": tools,
        "tool_calls": sum(tools.values()),
        "reads": reads,
        "stale_reads": stale_reads,
        "partial_reads": partial_reads,
        "files": len(files_touched),
        "gen_s": gen_s,
        "tool_s": tool_s,
    }


# ---------------------------------------------------------------------- report


def q(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[idx]


def summarise(agents: list[dict], label: str, cap: int) -> None:
    n = len(agents)
    if not n:
        print(f"\n===== {label}: no agents =====")
        return

    durations = [a["duration"] for a in agents]
    outs = [a["out"] for a in agents]
    calls = [a["tool_calls"] for a in agents]
    turns = [a["turns"] for a in agents]
    wall = (max(a["end"] for a in agents) - min(a["start"] for a in agents)).total_seconds()
    busy = sum(durations)
    reads = sum(a["reads"] for a in agents)
    stale = sum(a["stale_reads"] for a in agents)
    partial = sum(a["partial_reads"] for a in agents)
    gen = sum(a["gen_s"] for a in agents)
    tool = sum(a["tool_s"] for a in agents)
    over_cap = sum(1 for t in turns if t > cap)
    slow = sum(1 for d in durations if d > 1200)

    print(f"\n===== {label} =====")
    print(f"  agents                     {n}")
    print(f"  wall clock                 {wall / 3600:.2f} h")
    print(f"  sum of agent runtimes      {busy / 3600:.2f} h")
    print(f"  parallel factor            {busy / wall:.2f}x   (<1.0 = mostly one agent at a time)")
    print(f"  wall clock per agent       {wall / 60 / n:.1f} min")
    print(f"  duration  median / max     {q(durations, .5) / 60:.1f} / {max(durations) / 60:.1f} min")
    print(f"  agents over 20 min         {slow}  ({100 * slow / n:.0f}%)")
    print(f"  output tokens med / max    {q(outs, .5):,.0f} / {max(outs):,}")
    print(f"  output tokens total        {sum(outs):,}")
    print(f"  tool calls med / max       {q(calls, .5):.0f} / {max(calls)}")
    print(f"  turns med / p90 / max      {q(turns, .5):.0f} / {q(turns, .9):.0f} / {max(turns)}")
    print(f"  cache read per agent       {sum(a['cache_read'] for a in agents) / n / 1e6:.2f} M")
    print(f"  startup context median     {q([a['startup'] for a in agents], .5):,.0f} tok")
    print(f"  Read calls per agent       {reads / n:.1f}")
    if reads:
        print(f"  stale re-reads             {100 * stale / reads:.0f}%   (same agent, no edit between)")
        print(f"  partial reads              {100 * partial / reads:.0f}%   (used offset/limit)")
    print(f"  files touched per agent    {q([a['files'] for a in agents], .5):.0f} (median)")
    if gen + tool:
        print(f"  time generating tokens     {100 * gen / (gen + tool):.0f}%   ({gen / 3600:.2f} h)")
        print(f"  time running tools         {100 * tool / (gen + tool):.0f}%   ({tool / 3600:.2f} h)")
    if gen:
        print(f"  generation rate            {sum(outs) / gen:.0f} output tok/s")
    print(f"  would exceed maxTurns={cap:<4}  {over_cap}/{n}  ({100 * over_cap / n:.0f}%) -- killed with NO report")

    mix = collections.Counter()
    for a in agents:
        mix.update(a["tools"])
    if mix:
        print("  tool mix                   " + ", ".join(f"{k}={v}" for k, v in mix.most_common(8)))

    by_type = collections.Counter(a["type"] for a in agents)
    print("  agent types                " + ", ".join(f"{k}={v}" for k, v in by_type.most_common()))


def declared_tools(agents_dir: str) -> dict[str, set[str]]:
    """Parse `tools:` out of each agent definition's YAML frontmatter."""
    out: dict[str, set[str]] = {}
    for path in sorted(glob.glob(os.path.join(agents_dir, "*.md"))):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        front = text.split("---", 2)[1] if text.count("---") >= 2 else ""
        name = os.path.basename(path)[:-3]
        m = re.search(r"^tools:[ \t]*(\S.*)$", front, re.M)
        if m:
            # collapse Agent(a, b, c) so its comma list is not split
            raw = re.sub(r"Agent\([^)]*\)", "Agent", m.group(1))
            out[name] = {t.strip() for t in raw.split(",") if t.strip()}
            continue
        # YAML block-list form:  tools:\n  - Read\n  - Edit
        m = re.search(r"^tools:[ \t]*\n((?:[ \t]+-[ \t]*\S.*\n?)+)", front, re.M)
        if m:
            out[name] = {ln.strip().lstrip("-").strip()
                         for ln in m.group(1).splitlines() if ln.strip()}
    return out


def by_agent(agents: list[dict], agents_dir: str) -> None:
    """Per-role economics: where the tokens go, what each run costs, tool mix."""
    roles: dict[str, dict] = {}
    for a in agents:
        r = roles.setdefault(a["type"], {"n": 0, "out": 0, "dur": 0.0, "calls": 0,
                                         "tools": collections.Counter()})
        r["n"] += 1
        r["out"] += a["out"]
        r["dur"] += a["duration"]
        r["calls"] += a["tool_calls"]
        r["tools"].update(a["tools"])

    total = sum(r["out"] for r in roles.values()) or 1
    ranked = sorted(roles.items(), key=lambda kv: -kv[1]["out"])

    print("\n===== where the tokens go =====")
    print(f"  {'role':22} {'runs':>5} {'tokens':>12} {'share':>7} {'cumulative':>11}")
    cum = 0
    for name, r in ranked:
        cum += r["out"]
        print(f"  {name[:22]:22} {r['n']:5} {r['out']:12,} {100 * r['out'] / total:6.1f}% "
              f"{100 * cum / total:10.1f}%")
    print(f"  {'':22} {sum(r['n'] for r in roles.values()):5} {total:12,}")

    print("\n===== cost per run =====")
    print(f"  {'role':22} {'tok/run':>10} {'min/run':>9} {'calls/run':>10}")
    for name, r in sorted(roles.items(), key=lambda kv: -kv[1]["out"] / max(kv[1]["n"], 1)):
        print(f"  {name[:22]:22} {r['out'] // r['n']:10,} {r['dur'] / 60 / r['n']:9.1f} "
              f"{r['calls'] // r['n']:10}")
    # Compare only roles with enough runs to mean something; a single one-off
    # spawn produces a meaningless ratio.
    solid = {k: v for k, v in roles.items() if v["n"] >= 3 and v["out"]}
    if len(solid) >= 2:
        cheapest = min(solid.items(), key=lambda kv: kv[1]["out"] / kv[1]["n"])
        dearest = max(solid.items(), key=lambda kv: kv[1]["out"] / kv[1]["n"])
        ratio = (dearest[1]["out"] / dearest[1]["n"]) / (cheapest[1]["out"] / cheapest[1]["n"])
        print(f"\n  {dearest[0]} costs {ratio:.1f}x a {cheapest[0]} per run "
              f"(roles with 3+ runs) -- route the cheap role first.")

    print("\n===== tool mix per role =====")
    for name, r in ranked:
        s = sum(r["tools"].values())
        if not s:
            continue
        mix = "  ".join(f"{k}={100 * v // s}%" for k, v in r["tools"].most_common(5))
        print(f"  {name[:22]:22} {mix}")

    decl = declared_tools(agents_dir)
    if not decl:
        print(f"\n  (no agent definitions found under {agents_dir} -- "
              f"pass --agents-dir to audit tool allocation)")
        return

    print("\n===== declared vs actually used =====")
    print(f"  {'agent':22} {'never used':28} {'used but not declared'}")
    findings = 0
    for name, tools in sorted(decl.items()):
        r = roles.get(name)
        if not r:
            continue
        used = {k for k in r["tools"] if k}
        # Skill-style always-available tools are not worth flagging
        unused = tools - used - {"Skill"}
        extra = used - tools
        if not unused and not extra:
            continue
        findings += 1
        print(f"  {name[:22]:22} {','.join(sorted(unused)) or '-':28} "
              f"{','.join(sorted(extra)) or '-'}")
    if not findings:
        print("  every declared tool is used and nothing undeclared was called.")
    else:
        print("\n  A read-only reviewer holding an unused edit tool forfeits the only hard,")
        print("  tool-level guarantee available. A tool used but not declared is a typo the")
        print("  model made, or a tool it needed and did not have. Both are defects.")
        print("  Caveat: this compares today's definitions against the whole transcript")
        print("  history. A definition edited after old runs flags those runs falsely --")
        print("  check the definition's change history before acting on a finding.")


def detail(agents: list[dict]) -> None:
    print(f"\n{'start':8} {'dur':>7} {'turns':>6} {'tools':>6} {'out_tok':>9}  {'type':16} description")
    for a in sorted(agents, key=lambda x: x["start"]):
        print(
            f"{a['start'].strftime('%H:%M:%S')} {a['duration'] / 60:6.1f}m {a['turns']:6} "
            f"{a['tool_calls']:6} {a['out']:9,}  {a['type'][:16]:16} {a['desc'][:52]}"
        )


def list_projects(root: str) -> None:
    if not os.path.isdir(root):
        print(f"No transcript root at {root}", file=sys.stderr)
        return
    print(f"{root}\n")
    for project in sorted(os.listdir(root)):
        pdir = os.path.join(root, project)
        if not os.path.isdir(pdir):
            continue
        sessions = []
        for session in sorted(os.listdir(pdir)):
            sdir = os.path.join(pdir, session, "subagents")
            if os.path.isdir(sdir):
                count = len(glob.glob(os.path.join(sdir, "*.meta.json")))
                if count:
                    sessions.append((session, count))
        if sessions:
            print(f"  {project}")
            for session, count in sessions:
                print(f"      {session}   {count} subagent runs")


# ------------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="transcript root (default: ~/.claude/projects)")
    ap.add_argument("--project", help="project slug (directory name under the root)")
    ap.add_argument("--session", help="session id; default is every session in the project")
    ap.add_argument("--split-at", help="ISO timestamp; produces a before/after comparison")
    ap.add_argument("--labels", default="before,after", help="comma-separated labels for --split-at")
    ap.add_argument("--cap", type=int, default=50, help="hypothetical maxTurns to evaluate (default: 50)")
    ap.add_argument("--idle-gap", type=float, default=600.0, help="ignore message gaps longer than this (seconds)")
    ap.add_argument("--detail", action="store_true", help="print one line per agent")
    ap.add_argument("--by-agent", action="store_true",
                    help="per-role economics: token share, cost per run, tool mix")
    ap.add_argument("--agents-dir", default=".claude/agents",
                    help="with --by-agent: compare declared tools against observed usage")
    ap.add_argument("--list", action="store_true", help="list visible projects and sessions, then exit")
    args = ap.parse_args()

    if args.list:
        list_projects(args.root)
        return 0

    if not args.project:
        ap.error("--project is required (use --list to see what is available)")

    dirs = find_subagent_dirs(args.root, args.project, args.session)
    if not dirs:
        print(f"No subagent transcripts found under {args.root}/{args.project}", file=sys.stderr)
        print("Try --list to see available projects and sessions.", file=sys.stderr)
        return 1

    agents = []
    for d in dirs:
        for meta in sorted(glob.glob(os.path.join(d, "*.meta.json"))):
            record = load_agent(meta, args.idle_gap)
            if record:
                agents.append(record)

    if not agents:
        print("Found subagent directories but no usable transcripts.", file=sys.stderr)
        return 1

    print(f"Read {len(agents)} subagent runs from {len(dirs)} session(s).")

    if args.split_at:
        cut = parse_ts(args.split_at)
        labels = [s.strip() for s in args.labels.split(",")]
        while len(labels) < 2:
            labels.append(f"group{len(labels) + 1}")
        before = [a for a in agents if a["start"] < cut]
        after = [a for a in agents if a["start"] >= cut]
        summarise(before, labels[0], args.cap)
        summarise(after, labels[1], args.cap)
        if args.detail:
            print(f"\n--- {labels[0]} ---")
            detail(before)
            print(f"\n--- {labels[1]} ---")
            detail(after)
    else:
        summarise(agents, args.project, args.cap)
        if args.detail:
            detail(agents)

    if args.by_agent:
        by_agent(agents, args.agents_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
