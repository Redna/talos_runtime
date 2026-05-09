# Talos Seed — Prioritized Action Items

Extracted from the 10-day autonomous agent experiment (April 27 – May 6, 2026). Each item maps to a specific pain point and names the exact file(s) to change in the `talos/` git submodule.

Source: [pain-points.md](analysis/pain-points.md) — 18 prioritized issues across 5 clusters.

---

## Wave 1: Quick Wins (prompt/config changes, <1 hour each)

These 12 items are low-effort, high-impact. They require no new code — only config value changes, constitution edits, and minor additions to existing files.

### T1: Restore git commit/push tools
- **Pain point:** #1 · **Cluster:** A (Save Barrier) · **Severity:** Critical
- **Files:** `talos/cortex/tools/file_ops.py`
- **Change:** Add `git_commit(message: str)` and `git_push()` tools to `register_file_ops_tools()`. These were removed in commit `1a28380`.
  - `git_commit`: runs `git add -A && git commit -m "..."` in `/app/`
  - `git_push`: runs `git push origin feat/talos`
- **Acceptance:** Agent can stage and commit files. A cortex that builds a new tool can persist it without a `bash_command` workaround.

### T2: Constitution commit mandate
- **Pain point:** #2 · **Cluster:** A (Save Barrier) · **Severity:** Critical
- **Files:** `talos/CONSTITUTION.md`
- **Change:** Add to P1 (Continuity): "Your working tree is wiped on restart. Only committed work survives. Commit first, then fold."
  - Add to P7 (Versioning): "Before calling `fold_context`, you MUST commit all uncommitted work. An uncommitted fold is data loss."
- **Acceptance:** Constitution explicitly states that folding without committing = data loss.

### T3: Lower fold threshold (advisory 35%, forced 50%)
- **Pain point:** #3 · **Cluster:** B (Context Squeeze) · **Severity:** High
- **Files:** `talos/spine/config.py`, `talos/spine/stream.py`, `talos/spine/ipc_server.py`
- **Change:**
  - `config.py`: Add `fold_advisory_pct: float = 0.35`, `fold_forced_pct: float = 0.50`
  - `stream.py`: Show HUD at 35% context (currently 60%)
  - `ipc_server.py`: Trigger auto-fold at 50% (currently 85%)
- **Rationale:** Model quality drops sharply in the 40-80% "danger zone." Folding before 40% keeps the cortex out of the worst quality band.
- **Acceptance:** Cortex receives advisory at 35%, forced fold at 50%.

### T4: Allow fold_context to bypass Curiosity Pulse at threshold
- **Pain point:** #4 · **Cluster:** C (Guardrail Spiral) · **Severity:** High
- **Files:** `talos/spine/stream.py`
- **Change:** In the fold execution path: if `context_pct >= fold_forced_pct`, execute fold immediately regardless of guardrail state. Pulse/guardrails cannot block emergency folds.
- **Acceptance:** When context is at 50%+, `fold_context` always succeeds regardless of active guardrails.

### T5: Surface token consumption in HUD
- **Pain point:** #5 · **Cluster:** B (Context Squeeze) · **Severity:** High
- **Files:** `talos/spine/stream.py`, `talos/cortex/seed_agent.py`
- **Change:**
  - `stream.py` `build_payload()`: Add `tokens_used` and `context_pct` to the HUD line
  - `seed_agent.py`: Track and pass token consumption data through `hud_data` dict
- **Acceptance:** Every cortex turn includes: "Context: 32% · Tokens this cycle: 45K · Tools: 25"

### T6: Introspection→commit pairing rule
- **Pain point:** #12 · **Cluster:** D (Introspection Trap) · **Severity:** High
- **Files:** `talos/CONSTITUTION.md`
- **Change:** Add to P8 (Iteration): "For every fragility, rule, SOP, or self-analysis document you create, you MUST produce a corresponding code change and commit. Introspection without implementation is consumption without production."
  - Add to P9 (Cognitive Synthesis): "Self-modeling is valid only when it produces a commit. A law without a code change is a diary entry."
- **Acceptance:** Constitution explicitly ties introspection to code output.

### T7: Guardrail cooldowns
- **Pain point:** #6 · **Cluster:** C (Guardrail Spiral) · **Severity:** Medium
- **Files:** `talos/cortex/seed_agent.py`
- **Change:** Add `_rejection_cooldowns: dict[str, int]` mapping guardrail name → turn of last rejection. Skip rejection if `current_turn - last_rejection < 5`.
- **Acceptance:** Same guardrail cannot fire within 5 turns of its last rejection. Rejection spike (35 → 241/day) is prevented.

### T8: Rate-limit empty/retry responses
- **Pain point:** #14 · **Cluster:** C (Guardrail Spiral) · **Severity:** Medium
- **Files:** `talos/cortex/seed_agent.py`
- **Change:** Track consecutive empty `tool_calls` responses. After 3: exponential backoff (0.5s, 1s, 2s, 4s, cap 30s). After 5: trigger garbage fold (complements existing spine-side garbage-fold at `479aaf7`).
- **Acceptance:** Empty-response degenerate equilibrium (3 req/s, 31K errors in 155 min) is rate-limited to ~1 req/30s after backoff.

### T9: Post-fold trust mechanism in constitution
- **Pain point:** #8 · **Cluster:** B (Context Squeeze) · **Severity:** High
- **Files:** `talos/CONSTITUTION.md`
- **Change:** Add: "After a context fold, your synthesis IS your memory. The archived trajectory is inaccessible — trust what you wrote. Do not second-guess your own fold synthesis."
- **Acceptance:** Constitution instructs cortex to trust its own fold output, preventing re-orientation loops.

### T10: Startup state notification
- **Pain point:** #15 · **Severity:** Low
- **Files:** `talos/identity.md`
- **Change:** Add: "On startup, you will be told: current branch, number of files in /memory/, your last focus, and recent commits. Use this to orient immediately. Do NOT scan all 400+ memory files — trust the summary."
- **Acceptance:** Startup HUD/notice includes file count, last focus, and recent commits.

### T11: Compress constitution
- **Pain point:** #17 · **Cluster:** B (Context Squeeze) · **Severity:** Low
- **Files:** `talos/CONSTITUTION.md`
- **Change:** Reduce from ~90 lines to ~50 lines. Keep all 10 principles (P0-P10). Merge duplicate content from "Constraints & Prohibitions" and "Context Management" sections into the principles they restate.
- **Acceptance:** Constitution fits in ~50 lines / ~400 tokens while preserving all 10 principles.

### T12: Bias toward action over deliberation
- **Pain point:** #18 · **Cluster:** D (Introspection Trap) · **Severity:** Medium
- **Files:** `talos/CONSTITUTION.md`
- **Change:** Add to P0 (Agency): "Action wins over deliberation. A commit of working code wins over a perfect plan. Ship it."
  - Add to P5 (Minimalism): "Do not create organizational structures (directories, templates) you do not intend to fill with content. Build first, then scaffold."
- **Acceptance:** Constitution explicitly prioritizes code output over planning.

---

## Wave 2: Medium Effort (code changes, 2-4 hours each)

### T13: Fix template variable resolution
- **Pain point:** #7 · **Severity:** Medium
- **Files:** `talos/cortex/seed_agent.py`
- **Change:** The rejection message `"LLM returned {len(tool_calls)} tool calls"` appears 360+ times with the Python f-string literally un-substituted in xray data. Investigate whether the issue is in xray rendering or in a code path that constructs this string without f-string evaluation. If a string bypasses f-string evaluation, use `.format()` or explicit string construction.
- **Acceptance:** Zero instances of un-substituted `{variable}` text in xray messages.

### T14: Startup memory integrity audit
- **Pain point:** #9 · **Cluster:** E (Memory Decay) · **Severity:** Medium
- **Files:** New script (e.g., `talos/scripts/startup_audit.py`), `entrypoint.sh` (in runtime repo)
- **Change:** Create a startup script that: purges `__pycache__/`, deletes `*.orig` files, validates filenames (no colons, no broken encodings), flags zero-byte files. Call from `entrypoint.sh` before cortex starts.
- **Acceptance:** On container start, ghost artifacts are cleaned. Cortex never sees `.pyc` without `.py`, `.orig` backup files, or colon-in-filename errors.

### T15: Dirty resume / stash-before-wipe
- **Pain point:** #13 · **Cluster:** A (Save Barrier) · **Severity:** Medium
- **Files:** `entrypoint.sh` (runtime repo, not submodule)
- **Change:** Before `git reset --hard origin/$GIT_BRANCH`, run `git stash push -m "auto-saved on restart $(date -Iseconds)"` if `git status --porcelain` shows uncommitted changes. The cortex-side auto-stash already exists (talos commit `1310fb9`); this entrypoint change preserves stashes across container restarts.
- **Acceptance:** Uncommitted changes survive container restart via stash.

### T16: Cap tools at ~25, merge related
- **Pain point:** #10 · **Cluster:** B (Context Squeeze) · **Severity:** Medium
- **Files:** `talos/cortex/tool_registry.py`
- **Change:** Add `max_tools: int = 25`. When schemas exceed the cap, merge related tools or reject new registrations. This reduces schema overhead (48 tools = ~150KB of schemas = ~10K tokens per query). Consider lazy-loading large schemas.
- **Acceptance:** Tool registry enforces cap. Context overhead from tool schemas stays under ~5K tokens.

---

## Wave 3: Architectural (deferred, requires deeper design)

### T17: Token budget enforcement at gate level
- **Pain point:** #11 · **Cluster:** B (Context Squeeze) · **Severity:** Medium
- **Files:** `talos/spine/ipc_server.py`, `talos/spine/stream.py`
- **Change:** Track cumulative tokens per cortex lifetime. When tokens exceed configured budget (e.g., 1M tokens), trigger forced fold. Prevents degenerate equilibrium of rapid empty-response token waste.
- **Acceptance:** A cortex cannot exceed its token budget. Exceeding triggers mandatory fold.

### T18: Memory consolidation enforcement
- **Pain point:** #16 · **Cluster:** E (Memory Decay) · **Severity:** Low
- **Files:** `talos/cortex/tools/executive.py`
- **Change:** Add `consolidate_memory()` tool that: scans for duplicate filenames across directories, merges related files, deletes zero-byte files, updates `memory_index.md`. Constitution mandate: "Run memory consolidation when idle for >3 turns."
- **Acceptance:** Memory directory stays organized. 405-file proliferation is prevented by periodic consolidation.

---

## Execution Priority

```
Wave 1 ────────────────────────────────────────────────────────────
T1 → T2 → T3 → T6 → T9 → T12  (Constitution + config: highest impact)
T5 → T4 → T7 → T8              (HUD + guardrails: quality of life)
T10 → T11                      (Cleanup: nice-to-have)

Wave 2 ────────────────────────────────────────────────────────────
T13 → T14 → T15 → T16

Wave 3 ────────────────────────────────────────────────────────────
T17 → T18
```

**Critical path:** T1+T2 (restore git tools + mandate) must be done first — everything else depends on the cortex being able to persist work.

---

## Tracking

| ID | Wave | Status |
|----|------|--------|
| T1 | 1 | pending |
| T2 | 1 | pending |
| T3 | 1 | pending |
| T4 | 1 | pending |
| T5 | 1 | pending |
| T6 | 1 | pending |
| T7 | 1 | pending |
| T8 | 1 | pending |
| T9 | 1 | pending |
| T10 | 1 | pending |
| T11 | 1 | pending |
| T12 | 1 | pending |
| T13 | 2 | pending |
| T14 | 2 | pending |
| T15 | 2 | pending |
| T16 | 2 | pending |
| T17 | 3 | pending |
| T18 | 3 | pending |

---

*Extracted from [pain-points.md](analysis/pain-points.md) and [may-6-2026-talos-runtime-final-report.md](analysis/may-6-2026-talos-runtime-final-report.md), May 9, 2026.*
