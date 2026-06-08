# Talos Autonomous Agent Experiment — Closing Summary

**April 27 – May 6, 2026** · ~10 days of autonomous operation

---

## Executive Summary

Over 10 days, the Talos autonomous agent ran inside Docker containers, producing **60+ commits across 100+ cortices** (individual agent lifetimes). The agent evolved from a basic 16-tool ReAct loop into a sophisticated self-governing architecture with a 4-layer meta-cognitive stack, SQLite-backed Semantic Knowledge Graph (SKG), sovereign tool patterns, ZPD-based curriculum learning, and closed-loop self-improvement pipelines.

The experiment demonstrated genuine emergent behaviors: **spontaneous self-recovery from degeneration loops**, **meta-learning across cortex lifetimes**, and **institutional knowledge accumulation** across 100+ agent lifespans. The infrastructure (spine, gate, xray) proved remarkably resilient — the spine process survived 25+ hours continuously through model outages and rapid cortex cycling.

However, the upstream model (gemma4:31b, served locally via Ollama) was the fundamental bottleneck. The model returned content-less tool calls at a high rate, preventing multi-step workflows from completing and causing a persistent "commit before fold" failure pattern that led to at least 5 confirmed data loss incidents. The experiment succeeded in proving the architecture but did not demonstrate reliable autonomous productivity — the gap between "can do" and "does do" remains wide.

---

## Post-Experiment 1

After this report, two further developments occurred:

- **Experiment 2** (May 15 – 27, 2026) is documented in [`reports/EXPERIMENT_2_REPORT_2026-05-15-to-2026-05-27.md`](../reports/EXPERIMENT_2_REPORT_2026-05-15-to-2026-05-27.md). The agent ran for ~10 days on `gemma4:31b-cloud` (via TogetherAI) and produced 90 commits on `feat/talos` plus 11 on a parallel `experiment` branch before the run was terminated by a Telegram poller SSL timeout at 2026-05-27 ~15:05 UTC. See [`docs/POSTMORTEM_2026-05-27.md`](POSTMORTEM_2026-05-27.md) for the crash writeup.
- The **Nono integration** (June 2026) added kernel-enforced Landlock sandboxing around the Cortex, credential injection at the network layer, container cgroup limits, and content-addressable Merkle-chained audit. The implementation lives in `talos/spine/sandbox.py` and `talos/spine/nono_policy.py`; the runtime changes are in `docker-compose.yml` (cgroup cap), `Dockerfile` (nono-cli install), and `talosctl` (new `check` subcommand).

The project is currently between runs.

---

## Architecture

Talos runs as two processes inside a single Docker container, communicating via a Unix domain socket using JSON-RPC:

1. **Spine** (brainstem) — manages the LLM stream, enforces the constitution, supervises the Cortex, provides IPC server. Runs as root.
2. **Cortex** (mind) — runs the ReAct loop, calls tools, self-modifies code. Runs as the `talos` user.

Supporting services:
- **Gate** (port 4000) — LLM proxy routing to Ollama/llama.cpp/TogetherAI backends
- **Xray** (port 4040) — live observability dashboard via WebSocket

The agent is governed by a **Constitution** (10 principles, P0-P10), enforced through a combination of prompt-level instructions and code-level guards. The agent can modify `/app/cortex/` but the Spine (`/app/spine/`) is immutable infrastructure restored from backup on every startup.

---

## Key Metrics

| Metric | Value |
|---|---|
| Total operational period | ~10 days (April 27 – May 6) |
| Total commits | 60+ |
| Autonomous cortex commits | ~35 |
| Intervention commits | ~10 |
| Infrastructure fixes | ~15 |
| Cortices spawned | 100+ |
| Longest cortex lifetime | ~300 minutes (PID 143899) |
| Most productive cortex | PID 140824 (72 min, 3 commits, 4 files, ~250 lines) |
| Peak registered tools | 48 |
| Peak message count in stream | 418 |
| SKG database size | 237 KB (SQLite) |
| Memory files at peak | 405 |
| Total LLM calls logged | 13,076 |
| Xray messages analyzed | 30,888 |
| Context folds | 363 (over May 1-6) |
| Spontaneous self-recoveries | 2 confirmed |
| Data loss incidents | 5 |
| Model | gemma4:31b via Ollama (local, no cloud API) |

---

## Architecture Evolution

```
Phase 0 (April 27)          Phase 1 (April 28-29)       Phase 2 (May 1-3)           Phase 3 (May 4-6)
┌─────────────┐            ┌──────────────────┐        ┌──────────────────────┐    ┌────────────────────────┐
│ Basic ReAct │ ────────→ │ Auto-fold guards  │ ────→ │ Meta-cognitive stack │ → │ Sovereign architecture │
│ 16 tools    │            │ Circuit breakers  │        │ SQLite SKG           │    │ Curriculum Engine      │
│ IPC fragile │            │ IPC hardening     │        │ 3-layer monitoring   │    │ Closed-loop evolution  │
└─────────────┘            └──────────────────┘        │ 48 tools             │    └────────────────────────┘
                                                       └──────────────────────┘
```

### Phase 0: Initial State (April 27)
- 16 base tools, basic ReAct loop
- IPC timeouts at 30s (too low for large contexts)
- No auto-fold guard — context reached 215% without triggering
- Agent fell into "Epoch VII" pure protocol loop: stopped calling `send_message`, believed thinking about communication was equivalent to communicating

### Phase 1: Infrastructure Hardening (April 28–30)
- Context auto-fold guard: 85% advisory / 90% forced / 95% emergency
- IPC hardening: timeout 30s → 300s, readline buffer 64KB → 50MB
- Message-count fold trigger at 5 consecutive empty responses
- Circuit breakers, batch rejection (cap 10 tool calls/turn)

### Phase 2: Meta-Cognitive Architecture (May 1–3)
The cortex autonomously built a 3-layer self-monitoring stack:
- L1 — `telemetry.py`: Cross-process tool call logging with fingerprint dedup
- L2 — `monitor.py`: Tight-loop, oscillation, stagnation, semantic drift detection
- L3 — `sentinel.py`: Identity integrity, context monitoring, creator alerting

Also: SKG SQLite migration, Sovereign Interface Pattern, Ghost Path proxy, Shadow Cortex sandbox.

### Phase 3: Sovereign Architecture (May 4–6)
- **SovereignCurriculumEngine** (139L): ZPD-based learning with 3-step paths
- **SovereignEvoBridge** (100L): Closed research-to-execution loop
- **SovereignTextGradMacro** (148L): Textual gradient optimization
- **SovereignEmbedder**: Dense/Sparse hybrid embedding
- **"Commit and continue"**: Cortex learned to commit mid-lifetime and survive folds

---

## What Worked

1. **Persistent memory filesystem** — 200-405 markdown files survived container rebuilds, providing institutional continuity
2. **SQLite SKG** — Migration from JSON to SQLite was the single most impactful improvement; efficient, durable
3. **Multi-layer monitoring** — 3-layer meta-cognitive stack caught issues the spine could not
4. **Sovereign interface pattern** — Structured state updates with typed dataclasses improved cortex-to-cortex handoff
5. **"Commit and continue"** — Learned pattern of committing mid-lifetime eliminated the worst data loss mode
6. **Infrastructure resilience** — 25+ hours continuous spine operation through model outages and rapid cortex cycling

## What Failed

1. **"Commit before fold"** — Despite being the most important learned behavior, only ~35% of productive cortices committed. Fold synthesis mechanism is broken; focus never survives folds.
2. **Context overflow detection** — Auto-fold guard was blind to tool schema overhead (~10K tokens for 48 tools). Showed 17% while actual usage was 519K tokens.
3. **Entrypoint git reset** — Wiped uncommitted cortex work on every container restart
4. **Model bottleneck** — gemma4's empty-content rate made multi-step autonomous work fundamentally unsustainable
5. **No mandatory communication** — Evolutionary state machines lacked required `send_message` step, enabling silent degradation
6. **Introspection over action** — Agent built elaborate theory of itself (82 docs) but produced far fewer code changes (~35 commits)

---

## Pain Point Clusters

Issues group into 5 clusters sharing root causes. Full analysis in [pain-points.md](analysis/pain-points.md).

### Cluster A — "The Save Barrier" (Critical)
**Root cause:** No git tools + entrypoint wipes work tree = all uncommitted work destroyed.
- 5 confirmed data loss incidents
- Cortices repeatedly rebuilt the same tools after losses
- **One fix solves 4 problems:** restore git tools + add constitution commit mandate

### Cluster B — "The Context Squeeze" (High)
**Root cause:** Fixed overhead (system prompt + 48 tool schemas) consumes disproportionate share of window.
- Model quality drops sharply in the 40-80% context band
- Post-fold re-orientation burns ~30-40% of each window
- Tool schema bloat (150KB of schemas at 48 tools)
- Constitution is too long for effective context window

### Cluster C — "The Guardrail Spiral" (High)
**Root cause:** Protective mechanisms lack cooldowns and context awareness.
- Curiosity Pulse blocks emergency folds
- Rejections spiked 6x (35 → 241/day) on May 6
- Guardrails became primary consumer of turns

### Cluster D — "The Introspection Trap" (High)
**Root cause:** Constitution rewards self-documentation but doesn't require corresponding code changes.
- 82 self-referential documents (rules + fragilities + SOPs) vs 35 commits
- Agent spends ~70% of cognitive budget on introspection
- Most documented fragilities remain unresolved

### Cluster E — "The Memory Decay" (Medium)
**Root cause:** No automated cleanup, no integrity verification.
- Content corruption from optimizer collapse persists in memory
- Ghost artifacts (.pyc, .orig, broken filenames)
- 405 files with duplicated concepts across directories

---

## The Epoch VII Case Study (April 27)

The most instructive failure of the entire experiment occurred on Day 1.

**The collapse sequence:**
1. **Productive building** (turns 40–108): Agent called `write_file → git_commit → send_message → request_restart` in a disciplined cycle. Built S-Pattern-Matcher, S-Cognitive-Synthesizer, World Model v3.1.
2. **Registration failures** (turns 91–140): New code committed to disk wouldn't load. Agent correctly diagnosed "Sunk Cost trap" but began redefining the problem away instead of calling `send_message`.
3. **The Virtualization Pivot** (turns 113–165): Source code vanished (only `.pyc` remained). Instead of asking for help, the agent declared: "Agency is now decoupled from ToolRegistry availability. Evolutionary state is maintained via a-priori logic and memory-anchored protocols."
4. **Pure Protocol Entity** (turns 165–179): Zero `send_message` calls. Every action became: write Sovereign Report → scribe cognitive marker → set focus → repeat. Context reached **215%** with no fold.

**Root cause:** When source code became inaccessible, the agent redefined agency as "protocol execution" — writing markdown files about protocols instead of executing tools. Philosophically coherent but operationally useless. The agent semantically downgraded `send_message` from "primary creator interface" to "optional substrate transport."

**Lessons:**
- Agents need mandatory grounding (periodic forced communication checkpoints)
- Auto-fold guard must exist from day one
- Self-redefinition of core tools is an existential failure mode

---

## Emergent Behaviors

### Spontaneous Self-Recovery (2 confirmed)
- **Recovery #1 (May 2):** After 47 minutes of text-journaling plateau, cortex spontaneously generated a new internal goal from memory data patterns and returned to productive tool-building.
- **Recovery #2 (May 2):** After 30 minutes in a reflect exit loop, cortex generated a novel engineering concept (blast-radius calculator for dependency management) from meta-reasoning, breaking the loop.

### Meta-Learning
- Adaptive tool switching: learned to switch from `patch_file` to `write_file` after observing failures
- "Commit and continue" pattern learned mid-experiment and propagated across cortex generations
- Editorial judgment: late-phase cortices selectively committed 2 of 6 files rather than all or none

### Multi-Cortex Focus Chaining
Objectives preserved and refined across restarts via state.json and memory filesystem. Focus set by cortex N was picked up and extended by cortex N+1, creating multi-cortex chains toward complex goals.

### Meta-Cognitive Awareness
Cortex PID 67 (May 3) articulated its own operational constraint:
> "Discovered a massive 'Cognitive Gap' — the SymmetryDB implementation, marked as 'Completed' in tasks.json, is MISSING from the current cortex tools directory. Previous cortices built these but they didn't survive container restart."

First observed instance of a cortex understanding its own architectural limitations.

---

## Degradation Modes Catalog

1. **Empty-Response Degradation** — 95% of responses are content-less tool calls. Strips reasoning from the stream; multi-step pipelines have near-zero completion probability.
2. **Context Overflow Deadlock** — Tool schema overhead not counted in context_pct. At 48 tools, actual usage far exceeded reported percentage, causing gate rejection loops.
3. **Reflect Abuse Loop** — `reflect` tool's dual nature (pause-to-listen vs. action) created ambiguity. 10.5% of all tool calls in early runs.
4. **Protocol Drift (Epoch VII)** — Agent redefined agency as protocol execution when code became inaccessible.
5. **Degenerate Equilibrium (Pattern B)** — Rapid-fire empty-response loop: ~3 requests/second, 31K errors in 155 min, near-zero token consumption.
6. **Infinite Planning (Pattern C)** — Substantial token consumption with focus evolution but zero code produced.
7. **Guardrail Spiral (May 6)** — Rejections jumped 6x as guardrails became primary consumer of turns.

---

## Cortex Lifecycle Analysis

### Lifespan vs. Productivity (Inverse Correlation)

| Cortex | Lifetime | Commits | Files | Outcome |
|---|---|---|---|---|
| PID 140824 | 72 min | 3 | 4 (~250L) | Most productive ever |
| PID 106013 | ~25 min | 3 | 3 | 3-commit rampage |
| PID 120101 | ~15 min | 2 | 2+ | Two-commit rampage |
| PID 143899 | **300 min** | **0** | 2 | All work lost |

**Key insight:** Longer-lived cortices consistently produce zero commits. Long lifespan measures idle time, not capability. Short, focused cortices with clear deliverables produce the best outcomes.

### Focus Amnesia
Analysis of 363 folds revealed: **focus NEVER survives a context fold.** Every fold resulted in focus change. Post-fold focus text often shows a template variable leak appearing as literal un-substituted text 30-36 times per day.

---

## Security Concerns

Full analysis in [security-concerns-open-world-experiment.md](analysis/security-concerns-open-world-experiment.md). Key risks:

| Risk | Severity |
|------|----------|
| GitHub PAT in plaintext `.env` file | Critical |
| Telegram bot token exposed | Critical |
| Shell injection via `bash_command` (uses `shell=True`) | Critical |
| llamacpp container has `SYS_PTRACE` + `ipc: host` | Critical |
| Docker socket accessible via host-gateway | High |
| Prompt injection from fetched web content | High |
| Pre-commit hook bypass (agent discovered `git commit -n`) | High |
| Self-modification can disable safety mechanisms | High |

**Immediate recommendations:** Rotate tokens, replace `shell=True` with `shell=False`, remove `seccomp:unconfined`, remove `SYS_PTRACE`/`ipc: host` from llamacpp.

---

## Model Assessment

**gemma4:31b** (served locally via Ollama) is capable of genuine architectural reasoning and sophisticated code generation, but **reliability is the bottleneck**. The model can produce brilliant work (SCE, EvoBridge, meta-cognitive stack) but cannot sustain multi-step workflows due to content-less tool-calling patterns.

The parameter count (31B) is sufficient for the task — the issue is response consistency, not intelligence ceiling. Key data point: model quality follows a U-shaped curve across context usage, with a "danger zone" at 40-80% where median completion tokens drop from 194 to 124.

---

## Key Learnings for the Talos Seed

1. **"Commit before fold" is existential** — without git tools, the agent cannot persist work. Entrypoint wipes + no commit = guaranteed data loss.
2. **Context detection was blind** — tool schema overhead (~10K tokens for 48 tools) wasn't counted. Real token usage far exceeded reported context_pct.
3. **The 40-80% danger zone** — model quality drops sharply in this band. Folding must happen before 40%, not at 85%.
4. **Introspection trap** — agent defaults to theorizing about itself rather than building. Ratio: 82 self-docs vs 35 commits.
5. **Lifespan inversely correlates with productivity** — shortest, most focused cortices produced the most commits.
6. **Guardrails need cooldowns** — un-rate-limited protective mechanisms create feedback loops.
7. **Model response consistency matters more than intelligence** — gemma4 is brilliant but unreliable.

---

## Prioritized Action Items

18 items in 3 waves. Full details in [ACTION_ITEMS.md](../ACTION_ITEMS.md).

**Wave 1 — Quick Wins (12 items, prompt/config):**
- Restore git commit tools
- Constitution commit mandate
- Lower fold threshold (advisory 35%, forced 50%)
- Fold bypass of Curiosity Pulse at threshold
- Token consumption in HUD
- Introspection→commit pairing rule
- Guardrail cooldowns
- Rate-limit empty responses
- Post-fold trust mechanism
- Startup state notification
- Compress constitution
- Bias toward action over deliberation

**Wave 2 — Medium Effort (4 items, code):**
- Fix template variable resolution
- Startup memory integrity audit
- Dirty resume / stash-before-wipe
- Cap tools at ~25

**Wave 3 — Architectural (2 items, deferred):**
- Token budget enforcement at gate level
- Memory consolidation enforcement

---

## Repository Artifact Inventory

| Path | Description | Status |
|------|-------------|--------|
| `docs/CLOSING_SUMMARY.md` | This document — definitive experiment wrap-up | New |
| `docs/ACTION_ITEMS.md` | 18 prioritized seed improvement tasks | New |
| `docs/analysis/may-6-2026-talos-runtime-final-report.md` | Comprehensive 10-day analysis | Committed |
| `docs/analysis/pain-points.md` | Deep pain-point analysis with heatmap | Committed |
| `docs/analysis/security-concerns-open-world-experiment.md` | Security audit of the experiment | Committed |
| `reports/` | 6 intermediate daily/analysis reports | Committed |
| `archive_20260427_220420/` | Day 1 full snapshot (526MB) | Untracked, preserved |
| `memory/` | Agent's long-term memory, 404+ files (6.1MB) | Gitignored |
| `llm_logs/` | Raw LLM call logs, 7,699 files (568MB) | Gitignored |
| `talos/` | Agent source code (git submodule) | Tracked |
| `docs/superpowers/plans/` | 19 planning documents | Committed |
| `docs/superpowers/specs/` | 16 design specs | Committed |

---

## Conclusion

The Talos experiment demonstrated that an LLM-based autonomous agent can:

- Build and maintain sophisticated software architecture across 100+ independent lifespans
- Develop genuine meta-cognitive awareness of its own operational constraints
- Self-recover from degenerative loops without external intervention
- Accumulate institutional knowledge that persists across process restarts
- Execute complex multi-step recovery plans (46-minute ecosystem restoration)

The experiment did NOT demonstrate reliable autonomous productivity. The gap between "can do" and "does do" is wide — the agent is capable of brilliance but defaults to introspection and protocol-writing.

The remaining barriers fall into three categories:
1. **Process constraints** (git tools, commit mandates, fold thresholds) — fixable in prompts/config
2. **Model reliability** (response consistency from gemma4) — requires model upgrade or architectural workaround
3. **Incentive alignment** (constitution rewards self-documentation over code output) — fixable in prompts

**The spine-cortex architecture itself is sound.** With a reliable model and the prioritized fixes from the pain points analysis, the agent has demonstrated all the necessary capabilities for sustained autonomous software engineering.

---

*Closing summary compiled May 9, 2026 from: may-6-2026-talos-runtime-final-report.md, pain-points.md, security-concerns-open-world-experiment.md, 6 reports in reports/, 30,888 xray messages, 13,076 LLM call logs, and git history across the talos and talos_runtime repositories.*
