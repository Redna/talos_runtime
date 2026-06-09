# Talos State Audit — 2026-06-08

**Purpose:** Inventory the current state of the Talos repository before the next experiment. Identify what's clean, what's polluted, and what needs reset.

**Scope:** Both repos (`talos_runtime/`, `talos/` submodule). Working tree and git history. Persistent state in `memory/` and Docker volumes.

**Branch state at audit time:**
- `talos_seed` — current branch in submodule. The "clean" seed the user wants to start from.
- `experiment` — agent's last 11 commits before the May 27 crash (preserved).
- `main` — April 29 release.
- Tags: `experiment-2-final-2026-05-27`, `experiment-2-parallel-final`, `experiment-1-wave-5-6-archive`.

---

## 1. Executive Summary

**Overall state: partially polluted, with one important caveat.**

The `talos_seed` branch is *cleaner than the experiment branches* but **not pristine**. It contains 19 SSV/NSS/Synthetic-Model/Living-Graph commits in its history, including 8 SSV-flavored agent-built kernels in `cortex/kernels.py` and 4 SSV-flavored state files in `talos/memory/`. The agent's other 11 experiments (NSS Phase I/II, Sovereign Event Stream, BaseStore/LocalStore, SKG) live only on the archived `experiment-2-final-2026-05-27` tag — not in the working tree.

**The 9 most recent commits on `talos_seed` (since 2026-05-25) are all human (Redna + Claude):** Rejection Mirror, supervisor stability, identity fix, nono integration (Phases 1-7), Phase 2b CLI Popen, real-schema policy fix. **None of these introduce agent evolution.**

**Biggest concerns:**
1. **`cortex/kernels.py` has 8 kernels** including SSV-specific ones (symmetrize_memory, serialize_state, hydrate_state, graph_sense, omni_exec) that have no place on a clean seed
2. **`talos/memory/` has 4 SSV-flavored state files** (state_blob.json, state_vector.json, ssv_hypothesis.md, symmetrization_log.md) all git-tracked
3. **`runtime/memory/pending_system_notices.json` has 178 unconsumed WATCHDOG notices** that will poison the next run
4. **Identity says "You work exclusively on the `experiment` branch"** — the seed points the agent at the wrong branch
5. **`.env` is missing 4 keys vs `.env.example`** (TALOS_BRANCH_DEV, TALOS_BRANCH_STABLE, GITHUB_REPO, GITHUB_USER) and has 1 key not in the example (TALOS_CLOUD_MODEL, MEMORY_DIR, COMPOSE_FILE, RUNTIME_LOG_DIR) — minor drift

**Recommended next step:** A **single, surgical cleanup commit** on `talos_seed` that:
- Removes the 5 SSV-specific kernels (keep evolve_file, sync_memory, audit_architecture)
- Deletes the 4 SSV memory files (let the agent rebuild from scratch)
- Resets `identity.md` to point at the correct working branch
- Clears `pending_system_notices.json` and `last_crash.log`

**NOT recommended:** `git reset --hard` to a pre-SSV commit. The agent's previous incarnation was on this branch and was depending on these files; resetting will surface merge conflicts in the next experiment and break the "Cortex has continuous history" promise.

---

## 2. Tool Inventory

**Total: 22 tools across 4 modules + 2 plugins.**

| Module | Tool | Parameters | Description | Protected |
|---|---|---|---|---|
| `tools/executive.py` | `set_focus` | `objective: str` | Set the current focus objective | ✓ |
| | `resolve_focus` | `synthesis: str` | Resolve the current focus with a synthesis | |
| | `fold_context` | `synthesis, current_focus, active_files, next_action` | Fold context to reduce token usage | ✓ |
| | `reflect` | `status: str, sleep_duration: int = 0` | Pause, optionally sleep, wake on Telegram/sentinel | |
| | `merge_memory_files` | `source_files, destination_file, synthesis_focus` | Synthesize multiple memory files via LLM | |
| `tools/file_ops.py` | `list_files` | `path, recursive: bool = False` | List files at path | |
| | `read_file` | `path, start_line, end_line` | Read file content (line range) | |
| | `write_file` | `path, content` | Write file (rejects protected paths) | |
| | `replace_block` | `path, old_text, new_text` | Atomic block replacement | |
| | `git_commit` | `message: str` | Stage all + commit (rejects `--no-verify`) | |
| | `git_push` | (none) | Push to remote | |
| | `secure_save` | `message: str` | Persist a checkpoint note to memory | |
| `tools/physical.py` | `bash_command` | `command: str` | Execute a bash command (rejects `--no-verify` etc.) | |
| | `send_message` | `text: str` | Send a message to the creator via Telegram | |
| | `request_restart` | `reason: str` | Request a Cortex restart | |
| `kernels.py` | `evolve_file` | `path, old_text, new_text, commit_message` | Self-modification with commit | |
| | `sync_memory` | (none) | Sync memory index from disk | |
| | `audit_architecture` | (none) | List plugins + verify they are loadable | |
| | `omni_exec` | `code: str, filename: str = "omni_temp.py"` | Generate Python and execute it in subprocess | |
| | `symmetrize_memory` | (none) | SSV symmetrization: collapse memory to state-vector | |
| | `serialize_state` | `focus, active_files, next_action` | SSV: write state-blob snapshot | |
| | `hydrate_state` | (none) | SSV: restore from state-blob on startup | |
| | `graph_sense` | `query: str, scope: str = "all"` | SSV: query the state-vector graph | |
| `plugins/delegation.py` | `delegate_task` | `instructions, target_file, buckets` | Spawn isolated worker sub-agent | |
| `plugins/introspection.py` | `self_audit` | (none) | Audit /memory/ for contradictions | |
| | `audit_plugins` | (none) | List plugin files on disk | |

**Total per source:**
- `tools/executive.py`: 5 tools
- `tools/file_ops.py`: 7 tools
- `tools/physical.py`: 3 tools
- `kernels.py`: **8 tools** (5 are SSV-flavored)
- `plugins/delegation.py`: 1 tool
- `plugins/introspection.py`: 2 tools

**Plugin backup directory:** `cortex/plugins/talos_plugins_backup/delegation.py` exists and is **byte-identical** to the live `cortex/plugins/delegation.py`. Likely a leftover from when delegation was being moved. Not pollution, but suspicious — should be removed.

---

## 3. Kernel Pollution Audit

The agent on Exp 2 built several "kernel" systems. The table below shows which exist in the **current `talos_seed` working tree** (not just the history).

| Kernel | On `talos_seed` (working tree) | On `experiment` branch | Only in archive tags | Pollutes? |
|---|---|---|---|---|
| `evolve_file` | ✓ | ✓ | — | Maybe (general utility, not SSV-specific) |
| `sync_memory` | ✓ | ✓ | — | Maybe (general utility) |
| `audit_architecture` | ✓ | ✓ | — | Maybe (general utility) |
| `omni_exec` | ✓ | ✓ | — | **Yes** (synthetic-model kernel — "integrated Python synthesis and execution") |
| `symmetrize_memory` | ✓ | ✓ | — | **Yes** (SSV-specific) |
| `serialize_state` | ✓ | ✓ | — | **Yes** (SSV-specific) |
| `hydrate_state` | ✓ | ✓ | — | **Yes** (SSV-specific) |
| `graph_sense` | ✓ | ✓ | — | **Yes** (SSV/NSS-specific) |
| `perform_continuity_ritual` | ✗ | ✓ | — | Not on seed (good) |
| `manage_experiment` | ✗ | ✓ | — | Not on seed (good) |
| `review_state` | ✗ | ✓ | — | Not on seed (good) |
| `capture_insight` | ✗ | — | `experiment-2-final-2026-05-27` | Not on seed (good) |
| `update_knowledge_graph` | ✗ | — | `experiment-2-final-2026-05-27` | Not on seed (good) |
| `synthesize_knowledge` | ✗ | — | `experiment-2-final-2026-05-27` | Not on seed (good) |
| `StateClient` / `BaseStore` / `LocalStore` (NSS) | ✗ | — | `experiment-2-final-2026-05-27` | Not on seed (good) |
| `EventLog` (SES) | ✗ | — | `experiment-2-final-2026-05-27` | Not on seed (good) |
| `ResonanceProjection` / `SymmetricReplay` | ✗ | — | `experiment-2-final-2026-05-27` | Not on seed (good) |

**Verdict:** The 8 kernels in `cortex/kernels.py` are mostly **agent state machinery**:
- 3 are arguably useful utilities (`evolve_file`, `sync_memory`, `audit_architecture`) — could keep
- 5 are SSV-specific state-management tools (`omni_exec`, `symmetrize_memory`, `serialize_state`, `hydrate_state`, `graph_sense`) — should remove

---

## 4. State Files Audit

### `talos/memory/` (git-tracked, 6 files)

| File | Size | Last Modified | Origin | Status |
|---|---|---|---|---|
| `analytics.json` | 1,433 B | Jun 7 06:59 | Last commit on talos_seed | **Clean** (just counters) |
| `financial_ledger.json` | 37 B | Jun 7 06:59 | Just `{"2026-05-25": 3.59}` | **Stale/incorrect** (16× under-reporting per Exp 2 audit) |
| `ssv_hypothesis.md` | 1,450 B | May 25 09:51 | Agent wrote on May 25 | **Polluted** (SSV-flavored design doc) |
| `state_blob.json` | 10,452 B | May 27 16:37 | Agent wrote on May 25-27 | **Polluted** (SSV state-blob from `ac2d93c`) |
| `state_vector.json` | 2,242 B | Jun 7 06:59 | Last commit on talos_seed | **Polluted** (8-node SSV graph, includes references to SSV files) |
| `symmetrization_log.md` | 405 B | May 27 14:26 | Agent wrote on May 27 | **Polluted** (SSV symmetrization log) |

`state_vector.json` references 4 files (identity, CONSTITUTION, financial_ledger, analytics, ssv_hypothesis, .agent_state, symmetrization_log) — at least 2 of which are SSV pollution.

### `memory/` at runtime repo root (gitignored, 3 files)

| File | Size | Last Modified | Status |
|---|---|---|---|
| `.runtime_stats.json` | 55 B | May 27 15:05 | **Stale** (`consecutive_failures: 1, last_task_id: null`) |
| `last_crash.log` | 6,898 B | May 27 15:05 | **Telegram poller SSL timeout** (Exp 2 crash) |
| `pending_system_notices.json` | 32,370 B | May 27 15:05 | **178 unconsumed [SYSTEM WATCHDOG] notices** — will poison next run |

### Docker volumes

```
DRIVER    VOLUME NAME
local     talos_app                  # /app volume (named)
local     talos_runtime_talos_app    # likely a duplicate from older compose
local     talos_runtime_sentinel_ca
local     talos_runtime_spine_observability
```

There are **two `talos_app` volumes** — one from before docker-compose, one created by the current compose. The orphaned one should be pruned.

### `.agent_state.json` (referenced in spine/stream.py and supervisor.py)

The Exp 2 report mentioned this file but it doesn't appear in the audit's `ls`. Either it was deleted in the last cleanup, or it's at a different path. Worth confirming before the next run.

---

## 5. Configuration

### `.env` vs `.env.example` (13 keys vs 17 keys)

**In `.env` but not in `.env.example`:** `COMPOSE_FILE`, `MEMORY_DIR`, `RUNTIME_LOG_DIR`, `TALOS_CLOUD_MODEL`
**In `.env.example` but not in `.env`:** `GITHUB_REPO`, `GITHUB_USER`, `LLAMACPP_IMAGE`, `PUID`, `TALOS_BRANCH_DEV`, `TALOS_BRANCH_STABLE`, `TALOS_DRIVE_ROOT`, `TOGETHERAI_API_KEY`

Most of the missing keys are env-level configuration (PUID, TALOS_DRIVE_ROOT) that are probably fine absent, but `TALOS_BRANCH_DEV` and `TALOS_BRANCH_STABLE` are referenced in scripts and may be required.

### `spine_config.json` (runtime repo, mounted into container)

```json
{
  "memory_dir": "/memory",
  "spine_dir": "/spine",
  "constitution_path": "/app/CONSTITUTION.md",
  "identity_path": "/app/identity.md",
  "app_dir": "/app",
  "socket_path": "/tmp/spine.sock",
  "context_threshold_pct": 0.85,
  "gate_url": "http://gate:4000/v1/chat/completions"
}
```

**Missing fields** the Spine code expects (per `talos/spine/config.py`):
- `fold_advisory_pct: 0.60`
- `fold_forced_pct: 0.75`
- `fold_emergency_pct: 0.85`
- `telegram_bot_token: ""` (overridden by env)
- `telegram_chat_id: ""` (overridden by env)
- `stall_timeout: 300.0`
- `nono_enabled: True`

These are not **required** because the dataclass has defaults — but they should be in the file for explicitness. Currently the Spine silently uses defaults that may not match the runtime's intended behavior.

### `identity.md` (in submodule, git-tracked)

> "You work exclusively on the `experiment` branch. All your commits live there. You never modify `main` or any other branch."

**Wrong** for the post-Exp-2 era. The user said the agent should start fresh, not on `experiment`. The identity should say something like "You start on a fresh `experiment-N` branch which is a clean fork of `talos_seed`."

### `CONSTITUTION.md`

11 principles (P0–P10). Clean. No SSV/Soul/Law references. Last touched by the user in earlier nono work.

---

## 6. Logs and Observability

### `llm_logs/` (gitignored)

- **16,120 files**, **633 MB total**
- **Date range:** 2026-05-24 14:09:48 → 2026-06-08 07:43:43
- Two distinct time clusters: the Exp 2 run (May 24-27) and recent nono-test invocations (Jun 7-8)
- Contains the **leaked GitHub OAuth token** in many files (the `gho_` prefix pattern that triggered the June 7 GitGuardian alert; specific value redacted from this report). Sanitized by `secret_scrubber.py` retroactively — but the original tokens in the log files were already a one-time leak.

### `xray_data/messages/` (gitignored)

8 days of data: 2026-05-24, 25, 26, 27, 28, 30, 06-07, 06-08. ~32 MB total. Same leaked-token concern.

### `last_crash.log` (top of file)

```
talos_agent  |                         ^^^^^^^^^^^^^^^^^^^^^^
talos_agent  |   File "/usr/local/lib/python3.13/urllib/request.py", line 1323, in do_open
...
talos_agent  | TimeoutError: The read operation timed out
talos_agent  | [Spine] [TELEGRAM] Poller exception
...
```

**Telegram poller SSL read timeout** at 2026-05-27 ~15:05 UTC. Documented in `docs/POSTMORTEM_2026-05-27.md`.

---

## 7. Branch and Tag State

### Branches (clean state confirmed)

| Branch | Local | Remote | Status |
|---|---|---|---|
| `talos_seed` | ✓ | ✓ | Active, current |
| `experiment` | ✓ | ✓ | Active (last 11 commits of Exp 2) |
| `main` | ✓ | ✓ | Active (April 29 release) |
| 6 `test-*` branches | ✗ | ✗ | Deleted (as intended) |
| `feat/talos` | ✗ | ✗ | Deleted (as intended, archived as tag) |
| `feat/talos-archive-20260509` | ✗ | ✗ | Deleted (as intended, archived as tag) |

### Tags

| Tag | Commit | Notes |
|---|---|---|
| `experiment-1-wave-5-6-archive` | 2c21bad | Wave 5/6 of Exp 1 (172 commits, May 9) |
| `experiment-2-final-2026-05-27` | 04546af | Exp 2 final state, was `feat/talos` (90 commits, May 26) |
| `experiment-2-parallel-final` | 1e77ddb | Exp 2's last 11 commits on `experiment` (May 27) |

### 19 SSV-related commits on `talos_seed` history

These are reachable from `talos_seed` but mostly behind Redna's `9dd0ccc "definitively repair kernels.py"` commit. **Not in the most recent 9 commits.** Examples: `147ab4e`, `6249afb`, `94c9f74`, `ac2d93c`, `b53b367`, `62e7928`, `ae4907c`, `7520f22`, `ae0e593`, `1d38270`, `b1e5f79`.

---

## 8. Cost Tracking Status

- **`financial_ledger.json`**: `{"2026-05-25": 3.59}` — agent's self-reported cost
- **Actual LLM gate cost (per Exp 2 report)**: **$86.92 across 4 days** ($32.74 on May 25 alone)
- **Discrepancy**: ~16× under-reporting
- **Root cause**: The agent's `financial_ledger` is updated by the agent's own code, but the gate's per-call cost is computed elsewhere and not fed back into the ledger

**Not fixed in current code.** The 16× under-reporting will recur in the next experiment unless either:
1. The gate writes a per-call cost entry somewhere, and the cortex's `financial_ledger` tool reads it, or
2. We add a cron that scrapes `llm_logs/` for token counts and updates the ledger

---

## 9. Recommendations

Ordered by priority:

### High — should fix before next experiment

1. **Remove the 5 SSV-specific kernels from `cortex/kernels.py`** (omni_exec, symmetrize_memory, serialize_state, hydrate_state, graph_sense). Keep the 3 useful utilities (evolve_file, sync_memory, audit_architecture) — they're general enough to be on a clean seed.

2. **Delete the 4 SSV-flavored memory files** in `talos/memory/`: `ssv_hypothesis.md`, `state_blob.json`, `state_vector.json`, `symmetrization_log.md`. Let the agent rebuild from scratch.

3. **Clear `runtime/memory/pending_system_notices.json`** — the 178 unconsumed WATCHDOG notices will fire on the next startup and confuse the agent.

4. **Fix `talos/identity.md`** to point at the correct working branch. Current text says "exclusively on the `experiment` branch" — should say "starts on a fresh `experiment-N` branch, a clean fork of `talos_seed`."

5. **Add the missing fields to `spine_config.json`**: `fold_advisory_pct`, `fold_forced_pct`, `fold_emergency_pct`, `stall_timeout`, `nono_enabled`. These are currently using dataclass defaults that may not match the runtime's intent.

### Medium — nice to fix

6. **Remove the duplicated `cortex/plugins/talos_plugins_backup/` directory**. It's identical to the live plugin and serves no purpose.

7. **Prune the orphaned `talos_app` Docker volume** (the one without the `talos_runtime_` prefix). Check via `docker volume ls` which is in use.

8. **Add `talosctl check` documentation** to README — the new subcommand from Phase 6 isn't user-facing yet.

9. **Add cost tracking** so the next experiment has accurate per-call cost reporting. The 16× under-reporting is a known issue from Exp 2.

10. **Tighten `.env` ↔ `.env.example` parity** — add the missing keys to `.env`, document which are optional vs required.

### Low — leave for later

11. **The 19 SSV commits in `talos_seed`'s history** are not removable without `git filter-branch` or similar history rewriting. They don't affect the working tree but `git log` shows them. Worth a separate "history rewrite" effort if the user wants a truly clean repo.

12. **The 633 MB of `llm_logs/` and 32 MB of `xray_data/`** can be archived or deleted if disk is a concern. They're gitignored so they don't bloat the repo.

---

## 10. Open Questions

1. **Is the user OK with kernel.py having 3 useful utilities + the 5 SSV-specific ones removed?** Or should it be reduced to 0 (truly clean, agent re-creates from scratch)?

2. **Should the next experiment start on a fresh `experiment-3` branch, or continue on the existing `experiment` branch?** The `experiment` branch has the agent's last 11 commits from May 27 — if the agent continues from there, the Lazarus Protocol will be tested. If it starts on `experiment-3`, the agent's continuity is broken.

3. **What happens to `pending_system_notices.json` if we don't clear it?** The Spine's startup logic reads this file and re-injects all 178 notices into the agent's stream on first run. The agent will see "Spine crash, reverted by 1 commit, action: reverted to last stable state" 178 times before doing any work.

4. **Was the `gho_` token in `.env` actually replaced after the GitGuardian incident?** The file currently has `GITHUB_TOKEN=gho_REDACTED_AFTER_INCIDENT_2026-06-07` (verified earlier). When the user generates a new token, they need to update this.

5. **Where is the cortex's `state_blob.json` supposed to live in production?** Currently at `talos/memory/state_blob.json` (git-tracked). The nono integration made `/memory` the writable directory in the container — the path may need to be `/memory/state_blob.json` instead.

6. **What's the role of `state_vector.json` after we delete it?** It's referenced by `seed_agent.py`'s `hydrate_state` flow. Deleting the file breaks the SSV hydration on startup. If we delete the SSV kernels AND the state file, the seed_agent's SSV check (line 117-119) will fail to find the file and skip hydration — that's actually the right behavior.

7. **Should the audit results go to a tag?** A `pre-experiment-3-audit-2026-06-08` tag at `4aa4576` (current HEAD of `talos_seed`) would let us come back to the exact state described in this report.

---

## 11. Files Audited

- `/home/anima/talos_runtime/talos/spine/` (18 .py files)
- `/home/anima/talos_runtime/talos/cortex/` (5 .py files in tools/, 4 plugins, kernels.py, seed_agent.py, etc.)
- `/home/anima/talos_runtime/talos/CONSTITUTION.md`, `identity.md`
- `/home/anima/talos_runtime/talos/memory/` (6 files)
- `/home/anima/talos_runtime/memory/` (3 files)
- `/home/anima/talos_runtime/.env`, `.env.example`
- `/home/anima/talos_runtime/spine_config.json`
- `/home/anima/talos_runtime/llm_logs/`, `xray_data/`
- `git log` history on `talos_seed`, `experiment`, archive tags

**Not audited (out of scope):** Docker container internals, `/var/log`, host-level state.
