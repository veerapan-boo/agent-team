# Shared discipline block

Paste this into your **project instruction file** — the one your runtime injects into every
agent's context automatically (in Claude Code: `CLAUDE.md` at the project root).

Keep it here and **only** here. Repeating it inside each agent definition makes every spawn
pay for it twice; measured saving from de-duplicating was ~300–560 tokens per spawn.

Verify the injection assumption for your runtime before relying on it. In Claude Code an
agent definition can carry a flag that *omits* project instructions, and at least one
built-in agent sets it — so check rather than assume.

Replace the `<…>` placeholders with your project's real commands.

---

```markdown
## Speed & Context Budget -- Read Every Session

These rules exist because a measured session spent **80% of its wall clock generating
tokens and only 20% running tools**. Latency is roughly `output_tokens / 110`, so every
rule below is about producing fewer tokens, not about running faster commands.

### File size limits (hard)

| File type | Hard cap | Start planning the split | Why |
|---|---:|---:|---|
| Implementation source | **800 lines** | 600 | A full read costs ~10k tokens; a 2,000-line file costs ~28k |
| Test-only file | **1000 lines** | 800 | Read less often, splits poorly |
| Documentation | **500 lines** | 400 | Docs get read whole |

When a file crosses the cap, convert it to a module directory along a **seam that already
exists** — state vs. rendering vs. parsing vs. IO. Never split by line count alone. If no
seam exists, the file has a design problem: say so instead of cutting it arbitrarily.

Smaller is not automatically better. Target **one concern per file** (~200-600 lines in
practice). Splitting below that adds a tool call — 520-756 output tokens, 5-7 seconds — for
every agent that needs the whole concept, and saves no input tokens.

### Reading discipline

1. **Search before reading** any file over 400 lines, then read with `offset`/`limit`.
   Read a file whole only when you genuinely need all of it.
2. **Never re-read an unchanged file.** The earlier tool result is still valid.
3. **A legitimate re-read is still a partial read.** If you edited a file and must see it
   again, re-read only the region you care about. Treat "do I already have this file?" as a
   hard gate before every read.
4. **Never read a file back to verify an edit.** The edit tool fails loudly if it did not
   apply.

### Writing discipline

5. **Edit, never rewrite.** Whole-file writes are for new files only.
6. **Truncate command output**: `<build> 2>&1 | tail -40`. A full build log stays in
   context for every later turn.
7. **Never write an identifier you have not read this session** -- function names, struct
   fields, config keys, CLI flags. If you did not read it from source, search for it. Cite
   `file:line` in your report for anything non-obvious.

### Working budget and mandatory reporting

8. **Soft budget: ~40 tool calls per delegated task.** At 40, stop expanding scope. Finish
   what compiles, then report.
9. **A hard turn limit aborts you with no report -- never rely on one.** If you approach
   your soft budget you must: leave the tree **building**, write down what is done and what
   is not, and report it explicitly as `PARTIAL:` naming the remaining work.
   **Reporting partial work is a success.** Silently stopping, or reporting "done" on work
   you did not verify, is the failure this rule exists to prevent.
10. **One module or one screen per delegated task.** A brief spanning two screens plus
    config plus docs is three briefs.
11. **These rules live here, not in the agent files.** This file is injected into every
    subagent's context automatically; an agent definition that repeats this section makes
    each spawn pay for it twice. Agent files link here, they do not restate it.

### Definition of Done (every code-writing agent)

Run these **inside your own agent**, before reporting -- not in a later verification pass:

```bash
<build command>            # zero warnings
<lint command --all>       # zero warnings
<test command>
<check that frozen or generated paths are still clean>
```

Rework agents -- a second agent spawned only to fix the first one's mistakes -- consumed
15% of all agent runs in the measured baseline. Almost all of it would have been caught by
the commands above.
```

---

## Where this block does *not* belong

- Not in each agent definition (that is the duplication this file exists to prevent).
- Not in a `docs/` page nobody loads — it has to be in the file the runtime injects.
- Not split across several files. One block, one location, linked from everywhere else.
