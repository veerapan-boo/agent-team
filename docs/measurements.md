# Measurement methodology

Everything in the [README](../README.md) is derived from session transcripts that Claude Code
writes by default. Nothing needs to be instrumented, wrapped, or enabled in advance — the
data for your own team is already on disk.

This document exists so the numbers can be audited and reproduced rather than trusted.

---

## 1. Where the data lives

```
~/.claude/projects/<project-slug>/
├── <session-id>.jsonl                    the lead / main-loop transcript
└── <session-id>/
    ├── subagents/
    │   ├── agent-<id>.jsonl              one file per subagent run
    │   └── agent-<id>.meta.json          { agentType, description, ... }
    └── tool-results/                     large tool outputs, spilled to disk
```

The project slug is the absolute project path with separators replaced by hyphens, so it
normally begins with `-`. That matters when passing it on a command line: use
`--project=<slug>`, not `--project <slug>`, or the shell's argument parser reads the leading
hyphen as a flag.

Each line of a `.jsonl` file is one event. The fields used here:

| Field | Meaning |
|---|---|
| `type` | `assistant`, `user`, `system`, … |
| `timestamp` | ISO-8601, UTC (`…Z`) |
| `isSidechain` | true for subagent events appearing in the lead's file |
| `message.id` | assistant message identifier — **the de-duplication key** |
| `message.usage.output_tokens` | tokens generated for that message |
| `message.usage.input_tokens` | uncached input tokens |
| `message.usage.cache_read_input_tokens` | context replayed from cache |
| `message.usage.cache_creation_input_tokens` | context written to cache |
| `message.content[].type` | `text`, `thinking`, `tool_use` |
| `message.content[].name` | tool name, when `type == "tool_use"` |
| `message.content[].input` | tool arguments — `file_path`, `offset`, `limit`, … |

---

## 2. The three corrections that change the answer

Naive versions of these three calculations produce numbers that are wrong by enough to
support the opposite conclusion. Each was hit and corrected during this analysis.

### 2.1 De-duplicate output tokens by `message.id`

Streaming writes the same assistant message to the transcript several times, with `usage`
growing on each write. Summing every `output_tokens` field inflates the total several-fold.

```python
by_id = {}
for event in assistant_events:
    mid = event["message"].get("id")
    tokens = event["message"]["usage"].get("output_tokens", 0)
    if mid:
        by_id[mid] = max(by_id.get(mid, 0), tokens)   # keep the maximum, not the sum
total = sum(by_id.values())
```

### 2.2 Count a wasted re-read only within one agent, with no edit in between

Counting every duplicate `Read` of the same path across a whole session gives ~70% "waste".
That figure is meaningless: separate agents each start with an empty context and must
legitimately read the same files.

The honest definition — *the same agent reads a path it already read, and did not edit it in
between* — gives **~15–16%**, and that is the number a discipline rule can actually target.

```python
fresh = set()          # paths this agent has read and not modified since
for call in tool_calls:
    path = call.input.get("file_path")
    if call.name == "Read" and path:
        if path in fresh:
            stale_reads += 1
        fresh.add(path)
    elif call.name in {"Edit", "Write"} and path:
        fresh.discard(path)      # a later read is now legitimate
```

The gap between 70% and 15% is the difference between "agents are wasteful" and "agents are
mostly fine, and the fix is elsewhere". Get this one wrong and you optimise the wrong thing.

### 2.3 Exclude human idle time from the generate/tool split

Attribute each adjacent message pair by direction:

- `user` → `assistant` = the model was generating
- `assistant` → `user` = a tool was running

Then drop any gap longer than ~10 minutes. Without that filter, a human reading a report
between phases is recorded as model latency; in the measured session two such pauses were
10.8 and 14.6 minutes.

---

## 3. Derived quantities

| Quantity | Definition |
|---|---|
| **Agent duration** | last timestamp − first timestamp in that agent's transcript |
| **Wall clock** | max(end) − min(start) across the agents in the group |
| **Sum of runtimes** | Σ agent duration |
| **Parallel factor** | sum of runtimes ÷ wall clock. `< 1.0` means idle gaps dominate — mostly one agent at a time. `> 1.0` means genuine overlap |
| **Wall clock per agent** | wall clock ÷ agent count — the honest "how long does one delegated task cost me" figure, because it includes the lead's own thinking between spawns |
| **Startup context** | `input + cache_read + cache_creation` on the agent's **first** assistant message — what it was handed before doing anything |
| **Turns** | count of assistant messages. This is what a `maxTurns` cap counts |
| **Output tokens per tool call** | Σ output tokens ÷ Σ tool calls — the marginal price of one more round trip |
| **Rework agent** | an agent whose task description matches `fix\|repair\|correct\|redo\|re-?run\|resolve\|follow.?up` — a proxy, see §5 |

---

## 4. Reproducing the README numbers

```bash
# 1. find your project slug
python3 scripts/measure-agent-team.py --list

# 2. whole-project summary
python3 scripts/measure-agent-team.py --project=-my-project-slug

# 3. before/after around a change you made
python3 scripts/measure-agent-team.py --project=-my-project-slug \
    --split-at 2026-01-15T09:00:00Z \
    --labels "baseline,with rules"

# 4. evaluate a turn cap you are considering, before you set it
python3 scripts/measure-agent-team.py --project=-my-project-slug --cap 50

# 5. per-agent detail
python3 scripts/measure-agent-team.py --project=-my-project-slug --detail
```

Option 4 is the one to run before changing any `maxTurns` value. It reports what fraction of
your **historical** agents would have been killed by the cap you are about to set.

---

## 5. Known limits of this methodology

State these alongside any number you quote from it.

- **Rework detection is a keyword proxy.** It matches the task description, so it catches
  "Fix wrong API names" and misses a rework task phrased as "Update the contract doc". It
  therefore **under**-counts. Treat 15% as a floor, not a measurement.
- **Turn counts are not deterministic.** In one team a complex single-file task ran 25 turns
  while a six-file task ran 49. Any prediction from turn counts is a distribution, never a
  point estimate — which is exactly why §6 of the README argues for scoping by *file count*.
- **Thinking tokens are not directly readable.** Transcripts persist thinking blocks with the
  text stripped and only a signature retained. The share of output that is extended thinking
  must be inferred by subtracting visible content length from `output_tokens`; that inference
  gave ~60–81% per agent in the measured session.
- **A live session keeps growing.** Numbers taken while a session is still running are a
  snapshot. Re-running the script later changes the totals and, in this case, did not change
  any conclusion.
- **One codebase, one task type.** The performance numbers come from a single Rust terminal
  application ported from a desktop app. The *mechanisms* generalise; the exact percentages
  should be re-measured on your own work before being quoted as yours.
- **Cross-team comparison is structural only.** The second team discussed in the README
  contributed design patterns; its session transcripts were not available, so none of its
  runtime behaviour is measured here.

---

## 6. Raw snapshot

Taken from one session, 76 subagent runs, split at the moment the discipline rules were
applied.

```
===== Era A: no rules =====
  agents                     47
  wall clock                 14.53 h
  sum of agent runtimes      7.65 h
  parallel factor            0.53x
  wall clock per agent       18.6 min
  duration  median / max     4.0 / 43.9 min
  agents over 20 min         7  (15%)
  output tokens med / max    17,387 / 177,085
  output tokens total        1,707,042
  tool calls med / max       39 / 174
  turns med / p90 / max      68 / 204 / 307
  cache read per agent       12.34 M
  startup context median     27,361 tok
  Read calls per agent       18.2
  stale re-reads             16%
  partial reads              49%
  distinct files read/agent  8 (median)
  time generating tokens     80%   (4.54 h)
  time running tools         20%   (1.12 h)
  generation rate            104 output tok/s
  would exceed maxTurns=50   30/47  (64%)

===== Era B: with rules =====
  agents                     29
  wall clock                 2.84 h
  sum of agent runtimes      2.35 h
  parallel factor            0.83x
  wall clock per agent       5.9 min
  duration  median / max     4.4 / 11.5 min
  agents over 20 min         0  (0%)
  output tokens med / max    11,850 / 42,039
  output tokens total        455,245
  tool calls med / max       33 / 59
  turns med / p90 / max      56 / 97 / 118
  cache read per agent       3.86 M
  startup context median     37,337 tok
  Read calls per agent       9.4
  stale re-reads             16%
  partial reads              53%
  distinct files read/agent  5 (median)
  time generating tokens     67%   (1.12 h)
  time running tools         33%   (0.55 h)
  generation rate            113 output tok/s
  would exceed maxTurns=50   19/29  (66%)
```

Turn distribution across both eras, used for the turn-cap analysis:

```
population   n     median   p90    max    >50 turns   >100   >200
ALL          76      60     142    307      64.5%     25.0%   6.6%
writers      54      67     157    307      61.1%     25.9%   9.3%
read-only    21      58     113    166      76.2%     23.8%   0.0%
```
