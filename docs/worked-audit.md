# Worked example — auditing a mature agent team

The checklist in [README §10](../README.md#10-audit-checklist) is easy to read and easy to
skim past. This is what it looks like applied to a real team, with the real result.

The team audited here is **good**. It was designed carefully, it has quality hooks other teams
do not have, and it out-performs the team our performance numbers come from on most structural
measures. It still scored **8 of 13** — and the three findings that mattered were not
individually visible. That is the point of writing this down.

All identifying detail has been removed. Agent names below are generic roles.

---

## The team under audit

- 13 agents: one lead, six domain writers, five read-only gates, one documentation writer.
- Writers split by **architectural boundary** (engine / API / UI / bot / data / infra), which
  is the split §8 recommends.
- Three quality hooks wired at the runtime level.
- A written file-ownership table covering every ambiguous path, including "name twins" —
  same-named files in different services that must never be cross-edited.
- A lead with **no** edit or write tools at all.

If you only read the architecture, this team looks finished.

---

## Result

| # | Checklist line | Result |
|---|---|---|
| 1 | Reasoning effort set per role | ❌ not set on any agent |
| 2 | Actual model verified, including env aliases | ⚠️ an env alias silently upgrades the "cheap scout" to a frontier model |
| 3 | Read-only reviewers genuinely lack edit tools | ✅ all five |
| 4 | Lead has no edit or write tools | ✅ |
| 5 | Turn caps follow one design, discipline written down | ✅ exemplary |
| 6 | Soft budget + partial-report protocol in every writer | ❌ zero of seven |
| 7 | Verification follows one design end to end | ❌ half of each |
| 8 | Every mandatory identifier resolves | ❌ a named verify helper does not exist |
| 9 | Flow diagrams agree with prose | ✅ |
| 10 | File-ownership table covers ambiguous paths | ✅ best example seen |
| 11 | Shared rules in one place | ✅ plus a glob-scoped rules directory |
| 12 | No source file over the size cap | ❌ largest is 2,954 lines |
| 13 | Every documented hook exists and is wired | ❌ one of four documented hooks was never created |
| — | Re-run the measurement script | ❌ no transcripts retained |

---

## The finding that a checklist alone would have missed

Three of those failures are individually arguable. Line 5 — the turn-cap discipline — is one
of the best-written pieces of agent documentation in either team studied. It explains why the
cap exists, how to scope around it, and quantifies what a violation costs.

Read together, though:

```
writers capped at ~50 turns          defensible -- paired with a real pre-split discipline
verification centralised at a gate   defensible -- one build per turn instead of N
no partial-report protocol anywhere  defensible -- the cap is meant to be avoided
the named verify helper is missing   not defensible, and invisible from the file
                       ↓
One mis-scoped brief and: the writer is killed mid-run, produces no report,
and the code it already wrote to disk has never been checked by anything.
```

The cap discipline is enforced by prose. The team's own configuration proves prose is not
enough — that is exactly why they built a hook to enforce their report format. The same
reasoning was never applied to the scoping rule the cap depends on.

**None of the four lines above is individually alarming. The failure lives entirely in the
seams**, which is why README §10 now ends with three interaction questions rather than a
tick-box.

---

## The cheapest finding, and why it hides

Four writer definitions instruct their agents to invoke a **verify** helper before declaring
any non-trivial change done. The helper does not exist anywhere on the machine.

Nothing errors. Agent definitions are prose; prose does not resolve references. The agent
reaches that instruction, has nothing to invoke, and continues. Verification silently never
happens — in every run, forever.

Combined with line 7 (six of seven writers carry no build or test command of their own), the
team's actual verification coverage before the end-of-turn gate is **zero**, while every
document describes it as covered.

Finding it took one directory listing.

---

## What the team did better than the measured baseline

Worth stating plainly, because an audit that only lists failures is a bad audit.

| | This team | The team our numbers come from |
|---|---|---|
| Quality hooks wired | 3 | 0 at time of measurement |
| Report format enforced by a hook | ✅ | ❌ (prose only, and it did not hold) |
| File-ownership table | ✅ detailed | ❌ none |
| Read-only gates with tools actually withheld | 5 | 2 |
| Lead is genuinely read-only | ✅ | ✅ |
| Pre-split scoping by file count | ✅ | ❌ |
| Warm handoff between split parts | ✅ | ❌ |
| Codemod-instead-of-agent-per-file rule | ✅ | ❌ |
| Glob-scoped domain rules | ✅ 15 files | ❌ one flat file |
| Documented anti-thrash rule (same failure twice → re-plan) | ✅ | ❌ |

Several rules in the README exist because this team wrote them first.

---

## How to run this on your own team

Roughly 30 minutes, no instrumentation.

1. **Frontmatter sweep.** Parse every agent definition and print name, model, effort, turn
   cap, and tools as one table. Lines 1, 2, 3, 4, 5.
2. **Grep for the protocol keywords** — your partial-report marker, your build command, your
   report-format phrase — one row per agent. Lines 6, 7. Expect surprises: a first pass here
   under-counted because two teams phrase the same contract differently. **Grep for the
   concept, not for your favourite wording**, then read the hits.
3. **Resolve every mandatory identifier.** Extract each skill, script, hook, agent name, and
   build target named as required, and assert it exists. Line 8, and the cheapest minute you
   will spend.
4. **Diff docs against configuration.** For each hook, agent, or mechanism your team file
   documents, confirm it in the configuration file, not in the prose. Lines 9, 13.
5. **Sort sources by line count.** Line 12.
6. **Run the measurement script.** Line 14 — and if there are no transcripts, that is itself
   the finding.
7. **Then ask the three interaction questions** from README §10. This is the step that found
   everything that mattered here.

Steps 1–6 tell you which boxes are unticked. Step 7 tells you which unticked boxes are
actually dangerous — and, occasionally, that a set of ticked ones are.
