# Talos Bare Minimum — Design Spec

**Date:** 2026-04-20
**Status:** Draft

## Objective

Strip the Talos agent down to its absolute minimum viable architecture. Clean rewrite from scratch, guided by four core principles: singular immutable stream, absolute minimalism, spine-cortex boundary, observability.

## Why Rewrite

The current codebase was corrupted by the agent during a 4100-turn death spiral. It modified core infrastructure files, broke safety constraints, and left vestigial complexity throughout. Patching around agent damage while also making foundational architecture changes (append-only stream, fork-on-fold, dropping modules) is harder and riskier than writing clean. Everything lives in git history as reference.

## Architecture

### Spine-Cortex Boundary

The spine is the **immutable substrate** — transport, stream management, process supervision. The cortex is the **evolving layer** — agent loop, tools, identity.

| Can modify | Cannot modify |
|-----------|--------------|
| `/app/cortex/` (tools, agent loop) | `/app/spine/` (all files) |
| `/memory/` (agent notes, context files) | Spine config, constitution loading, IPC contract |
| `/app/CONSTITUTION.md`, `/app/identity.md` (P2, via git commit + protected branch) | Any spine implementation file |

Self-modification safety is enforced by git commit + protected branch — no additional audit gate.

### Stream Principles

1. **Append-only** — messages are never mutated after insertion into `self.messages`
2. **No shedding** — the active trajectory between forks is the honest conversation. No compression, no ghost tool calls
3. **HUD piggyback** — dynamic data appended to the last tool result message once, then frozen. No mutation of prior messages
4. **Fork on fold** — when context threshold is hit, the current trajectory is archived to disk as a complete file, then `self.messages` is reset to `[system_msg, synthesis_msg]`. No hybrid compression
5. **No archive recall** — the synthesis message IS the handoff. Critical facts must be persisted to `/memory/` files before forking. Archive files exist for xray/debug only
6. **Read filter** — the LLM payload is a computed view derived from `self.messages`. The source of truth never changes

### Fork-on-Fold Mechanism

When `context_pct` exceeds the threshold (e.g. 85%):

1. The LLM calls `fold_context(synthesis)` tool
2. Cortex sends `request_fold` IPC to spine
3. Spine archives `self.messages` to `<spine_dir>/trajectories/<timestamp>.json`
4. Spine resets `self.messages` to `[system_prompt, synthesis_message]`
5. LLM continues from the synthesis with a clean context window

Between forks, the trajectory grows naturally. No compression, no shedding. When it gets too long, fork.

### Pause/Resume

Sentinel-file based. No HTTP control plane needed.

- **Pause:** `touch /spine/.paused` — supervisor sees it, stops calling the LLM
- **Resume:** `rm /spine/.paused && touch /spine/.wake` — cortex picks back up

Any external process (xray, watchdog, human) can trigger these.

### Memory

No memory tools. No `MemoryStore` class. No `agent_memory.json` key-value store.

The LLM uses `write_file`/`read_file`/`bash_command` to manage `/memory/` as a directory of files. The constitution instructs the agent on context offloading discipline (P9): persist critical facts to structured files before folds, synthesize and clean up during idle periods.

The HUD reports memory state by scanning `/memory/` for `.md` files — count and last 3 filenames. No special persistence format, no abstraction layer.

## File Structure

### Spine (10 files)

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `stream.py` | ~300 | Message stream, fork-on-fold, payload builder, stall detection |
| `ipc_server.py` | ~180 | JSON-RPC over Unix socket |
| `ipc_types.py` | ~60 | Shared data types |
| `supervisor.py` | ~200 | Process lifecycle, crash recovery, git rollback, sentinel file checks |
| `constitution.py` | ~70 | Load system prompt from identity files |
| `config.py` | ~50 | Configuration dataclass |
| `events.py` | ~50 | JSONL event logger |
| `health.py` | ~35 | Startup/stall detection, feeds supervisor |
| `telegram.py` | ~90 | Human-agent communication |
| `main.py` | ~60 | Entrypoint, orchestration |

**Spine observable state files** (written to `/spine/` volume, consumed by xray):

| File | Written by | When | Contents |
|------|-----------|------|----------|
| `state.json` | StreamManager | After every think() | turn, context_pct, focus, urgency, memory_file_count |
| `health.json` | Supervisor | Every 5s | status, consecutive_failures |
| `commit.json` | Supervisor | After commit ops | candidate SHA, stable SHA, ahead count |

**Dropped:** `snapshot.py` (fork archives replace it), `task_queue.py`, `task_manager.py`, `control_plane.py` (sentinel files replace it), `core.py` (dead code)

### Cortex (8 files)

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `seed_agent.py` | ~180 | Agent loop, repetition detection, tool call cap, HUD construction |
| `tool_registry.py` | ~90 | Decorator-based tool registration with OpenAI schema generation |
| `spine_client.py` | ~100 | JSON-RPC client over Unix socket |
| `state.py` | ~50 | Focus, error streak, token count — persisted to `.agent_state.json` |
| `tools/executive.py` | ~80 | `set_focus`, `resolve_focus`, `fold_context`, `reflect` |
| `tools/physical.py` | ~80 | `bash_command`, `send_message`, `request_restart` |
| `tools/file_ops.py` | ~70 | `read_file`, `write_file`, `patch_file` |
| `tools/git_ops.py` | ~90 | `git_commit`, `git_checkout`, `git_push` |

**Dropped:** `memory_store.py`, `hud_builder.py` (folded into `seed_agent.py`), `tools/code_surgery.py` (entire file), `tools/memory.py` (entire file)

### Root files

| File | Purpose |
|------|---------|
| `CONSTITUTION.md` | Core directives (updated to reflect new memory/fold model) |
| `identity.md` | Agent identity |
| `pyproject.toml` | Dependencies |
| `requirements.txt` | Pinned deps |

## Tools (16 total)

### Executive (4)
- `set_focus(objective)` — set current objective
- `resolve_focus(synthesis)` — mark objective complete
- `fold_context(synthesis)` — trigger fork-on-fold
- `reflect(status, sleep_duration?)` — pause + optional sleep with wake-on-file

### File Ops (3) — replaces code_surgery.py
- `read_file(path, start_line?, end_line?)` — read file contents with optional line range
- `write_file(path, content)` — write file, creates if missing
- `patch_file(path, patch)` — apply unified diff

### Physical (3)
- `bash_command(command)` — shell execution, blocks `--no-verify` flags
- `send_message(text)` — telegram to creator
- `request_restart(reason)` — graceful restart with uncommitted-changes guard

### Git (3)
- `git_commit(message)` — commit staged changes
- `git_checkout(branch)` — switch branch (protected branch guard)
- `git_push(remote?, branch?)` — push to remote (protected branch guard)

**Dropped tools:** `generate_repo_map`, `replace_symbol`, `consolidate_memory`, `analyze_memory_telemetry`, `git_diff`, `store_memory`, `recall_memory`, `list_memory_keys`, `search_memory`, `forget_memory`

## Defenses (from death spiral learnings)

These are built into the architecture, not bolted on:

1. **Per-turn tool call cap** — max 10 tool calls per response. Excess is truncated
2. **Mid-loop stall detection** — `RepetitionDetector` checks inside the tool call loop, breaks immediately on stall
3. **TypeError catch with missing-arg detail** — `tool_registry.execute()` catches `TypeError` and reports which params are required vs provided
4. **Spine-level stall detection** — `_detect_stall()` scans last 10 assistant messages, injects system notice on repeated patterns
5. **Write protection on spine** — cortex cannot modify `/app/spine/`. Enforced at the `bash_command` tool level: commands that write to `/app/spine/` are rejected. Filesystem-level read-only mount is a future hardening step, not in scope
6. **Gate-level control token stripping** — `_normalize_content()` strips `<|channel|>` and `<|...|>` tokens from LLM responses before returning to spine

## Constitution Updates

CONSTITUTION.md needs revisions to reflect the new architecture:

- P1: Replace MemoryStore references with `/memory/` file discipline
- P6: Replace fold language (DELTA pattern, backpack) with fork-on-fold (archive + synthesis + clean start)
- P9: Replace memory slot model with file-based self-organized memory. Synthesis = clean up your `/memory/` directory
- P10: Stream integrity language updated — append-only, no mutation, fork-on-fold not in-place compression

## Observability

- Gate writes daily JSONL traces to `/data/messages/YYYY-MM-DD.jsonl` (existing, unchanged)
- Spine writes trajectory archives to `<spine_dir>/trajectories/<timestamp>.json` on fork
- Spine writes events to `<spine_dir>/events/*.jsonl` (existing, unchanged)
- HUD piggybacks context %, turn, memory file count, focus, urgency onto last tool result
- No SSE, no streaming observability — file-based only

## Surrounding Architecture Changes

Dropping the control plane (HTTP API on port 4001) and the MemoryStore ripples into gate, xray, and docker-compose.

### Xray

The xray client currently polls the control plane HTTP endpoints for state, health, events, and commit info. All of these are lost when the control plane is removed.

**Replacement strategy — shared volume reads:**

The spine observability volume (`spine_observability:/spine`) is already mounted on the talos container. Mount it on xray too. Then:

| Current (via HTTP) | Replacement (via file) |
|--------------------|----------------------|
| `GET /state` | Read `/spine/state.json` — spine writes current state on every think() |
| `GET /health` | Read `/spine/health.json` — supervisor writes health status periodically |
| `GET /events?tail=N` | Read `/spine/events/*.jsonl` — already file-based, just read directly |
| `GET /commit` | Read `/spine/commit.json` — supervisor writes after every commit |
| `POST /command` | Sentinel files — `touch /spine/.paused` / `rm /spine/.paused` |

The xray client replaces HTTP polling with file reads from the shared volume. One poll loop instead of four separate HTTP calls. No network dependency between xray and the spine — just a shared directory.

The xray `/api/command` endpoint changes from proxying HTTP to `SPINE_URL/command` to writing sentinel files to the shared volume.

### Gate

Gate changes are minimal:

- Gate already normalizes control tokens in responses (`_normalize_content`, `_normalize_tool_calls`) — unchanged
- Gate already writes JSONL traces — unchanged
- The `MEMORY_DIR` env var and financial ledger remain (gate manages spend tracking, not agent memory)
- No gate code needs modification for the talos rewrite

### Docker Compose

Changes:

1. **Remove port 4001** from talos service — no more control plane HTTP
2. **Add spine volume to xray service** — `spine_observability:/spine:ro` so xray can read state/events/health
3. **Remove `SPINE_URL` env from xray** — no HTTP dependency on spine
4. **Add `SPINE_DIR=/spine` env to xray** — path to shared observability volume
5. **Add trajectory volume or directory** — `/spine/trajectories/` within spine_observability

### Spine State File Contract

The spine writes observable state as JSON files (not an HTTP API):

- `/spine/state.json` — current turn, context_pct, focus, urgency, memory_file_count. Written after every think()
- `/spine/health.json` — status (healthy/starting/stalled), consecutive_failures. Written by supervisor periodically
- `/spine/commit.json` — last candidate commit, last stable commit, ahead count. Written by supervisor after commit ops

These files are the replacement for the control plane's HTTP endpoints. Simple, synchronous, no HTTP server needed.

## Scope

This spec covers the **talos** Python package (spine + cortex) plus the necessary surrounding changes to xray, gate, and docker-compose to support the new architecture.